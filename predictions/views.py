"""Login-protected HTML and JSON prediction views.

All model inference is delegated to :mod:`predictions.ml_model`; this module
contains request validation, persistence, and presentation only.  In
particular, a request never invokes ``train_*`` or fits a preprocessor.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from decimal import Decimal
from functools import wraps
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.core.serializers.json import DjangoJSONEncoder
from django.db import DatabaseError, transaction
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.safestring import SafeString
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from matches.models import Team, Venue

from .forms import OutcomePredictionForm, ScorePredictionForm
from .ml_model import (
    ModelNotTrainedError,
    ModelPredictionError,
    model_status,
    predict_outcome,
    predict_score,
)
from .models import Prediction


logger = logging.getLogger(__name__)


def _json_safe(value: Any) -> Any:
    """Make model-service output safe for Django's JSON encoder."""

    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (Team, Venue)):
        return value.name
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except (TypeError, ValueError):
            pass
    if hasattr(value, "item"):
        try:
            return _json_safe(value.item())
        except (TypeError, ValueError):
            pass
    if isinstance(value, float):
        return value if value == value and value not in (float("inf"), float("-inf")) else None
    return value


def _json_response(data: Any, *, status: int = 200) -> JsonResponse:
    """Return a JSON response that also tolerates Decimal/date-like values."""

    return JsonResponse(_json_safe(data), status=status, encoder=DjangoJSONEncoder)


class _TemplateSequence(SafeString):
    """Keep an iterable usable when a shared template assigns it via firstof."""

    def __new__(cls, values):
        value = super().__new__(cls, "")
        value._values = values
        return value

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __bool__(self):
        return bool(self._values)

    def __getitem__(self, key):
        return self._values[key]


class _TemplateForm(SafeString):
    """Preserve form field access through the shared firstof aliases."""

    def __new__(cls, form):
        value = super().__new__(cls, "")
        value._form = form
        return value

    def __getattr__(self, name: str) -> Any:
        return getattr(self._form, name)

    def __iter__(self):
        return iter(self._form)

    def __len__(self):
        return len(self._form)

    def __bool__(self):
        return True

    def __getitem__(self, key):
        return self._form[key]


# ---------------------------------------------------------------------------
# Shared helpers


def _api_login_required(view):
    """Return JSON 401s for API callers instead of an HTML login redirect."""

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return JsonResponse(
                {
                    "ok": False,
                    "error": "Authentication is required to use the prediction API.",
                    "code": "authentication_required",
                },
                status=401,
            )
        return view(request, *args, **kwargs)

    return wrapped


def _json_value(value: Any) -> Any:
    if isinstance(value, Team):
        return value.name
    if isinstance(value, Venue):
        return value.name
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "pk"):
        return value.pk
    return value


def _form_values(form) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, value in form.cleaned_data.items():
        if isinstance(value, Decimal):
            values[key] = float(value)
        elif isinstance(value, (Team, Venue)):
            values[key] = value.name
        else:
            values[key] = value
    return values


def _probability_map(values: Any) -> dict[str, float]:
    """Return only finite numeric probabilities for persistence/API output."""

    if not isinstance(values, dict):
        return {}
    result: dict[str, float] = {}
    for label, probability in values.items():
        try:
            number = float(probability)
        except (TypeError, ValueError):
            continue
        if number == number and number not in (float("inf"), float("-inf")) and number >= 0:
            result[str(label)] = number
    return result


def _prediction_payload(prediction: Prediction, result: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": prediction.pk,
        "prediction_type": prediction.prediction_type,
        "batting_team": prediction.batting_team.name if prediction.batting_team_id else None,
        "bowling_team": prediction.bowling_team.name if prediction.bowling_team_id else None,
        "venue": prediction.venue.name if prediction.venue_id else None,
        "toss_winner": prediction.toss_winner.name if prediction.toss_winner_id else None,
        "toss_decision": prediction.toss_decision,
        "season": prediction.season,
        "input_values": _json_safe(prediction.input_values or {}),
        "model_version": prediction.model_version,
        "created_at": prediction.created_at.isoformat() if prediction.created_at else None,
    }
    if prediction.prediction_type == Prediction.Type.SCORE:
        payload.update(
            {
                "prediction": prediction.predicted_score,
                "predicted_score": prediction.predicted_score,
                "prediction_low": prediction.prediction_low,
                "prediction_high": prediction.prediction_high,
            }
        )
    else:
        payload["outcome_winner"] = prediction.outcome_winner.name if prediction.outcome_winner_id else None
        # Do not advertise a probability map unless one was actually stored.
        probabilities = _probability_map(prediction.outcome_probabilities)
        if probabilities:
            payload["probabilities"] = probabilities
            payload["outcome_probabilities"] = probabilities
    if result:
        payload["result"] = _json_safe(result)
    return payload


def _find_team(value: Any) -> Team | None:
    if value is None or value == "":
        return None
    if isinstance(value, Team):
        return value
    name = str(value)
    team = Team.objects.filter(name__iexact=name).first()
    if team:
        return team
    # Custom/local artifacts may use numeric team IDs as classifier labels.
    try:
        return Team.objects.filter(pk=int(name)).first()
    except (TypeError, ValueError):
        return None


def _bounded_prediction_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        numeric = float(value)
        if numeric != numeric or numeric in (float("inf"), float("-inf")):
            return None
        number = int(round(numeric))
    except (TypeError, ValueError, OverflowError):
        return None
    return min(max(number, 0), 32767)


def _save_prediction(user, form, result: dict[str, Any], prediction_type: str) -> Prediction:
    data = form.cleaned_data
    values = _form_values(form)
    model_version = str(result.get("model_version") or "")[:80]
    values["model_version"] = model_version
    common = {
        "user": user,
        "prediction_type": prediction_type,
        "batting_team": data.get("batting_team"),
        "bowling_team": data.get("bowling_team"),
        "venue": data.get("venue"),
        "toss_winner": data.get("toss_winner"),
        "toss_decision": data.get("toss_decision", ""),
        "season": data.get("season"),
        "input_values": values,
        "model_version": model_version,
    }
    if prediction_type == Prediction.Type.SCORE:
        point = result.get(
            "predicted_score",
            result.get("prediction", result.get("score", result.get("predicted_runs"))),
        )
        low = result.get("prediction_low", result.get("range_low"))
        high = result.get("prediction_high", result.get("range_high"))
        bounded_point = _bounded_prediction_int(point)
        bounded_low = _bounded_prediction_int(low)
        bounded_high = _bounded_prediction_int(high)
        if bounded_low is not None and bounded_high is not None and bounded_low > bounded_high:
            bounded_low, bounded_high = bounded_high, bounded_low
        if bounded_point is not None:
            if bounded_low is not None:
                bounded_low = min(bounded_low, bounded_point)
            if bounded_high is not None:
                bounded_high = max(bounded_high, bounded_point)
        common.update(
            {
                "current_score": data.get("current_score"),
                "overs_completed": data.get("overs_completed"),
                "wickets": data.get("wickets"),
                "current_run_rate": data.get("current_run_rate"),
                "last_5_overs_runs": data.get("last_5_overs_runs"),
                "recent_wickets": data.get("recent_wickets"),
                "target_score": data.get("target_score"),
                "predicted_score": bounded_point,
                "prediction_low": bounded_low,
                "prediction_high": bounded_high,
            }
        )
    else:
        probabilities = result.get("probabilities", result.get("outcome_probabilities", {}))
        if not result.get("has_probabilities", bool(probabilities)):
            probabilities = {}
        winner = result.get("predicted_winner", result.get("winner", result.get("prediction")))
        common.update(
            {
                "outcome_winner": _find_team(winner),
                "outcome_probabilities": _probability_map(probabilities),
            }
        )
    with transaction.atomic():
        return Prediction.objects.create(**common)


def _base_context(form) -> dict[str, Any]:
    try:
        teams = Team.objects.order_by("name")
        venues = Venue.objects.order_by("name")
        # Force the inexpensive catalogue queries here so a missing/unmigrated
        # database can be represented by empty choices rather than a 500.
        list(teams[:1])
        list(venues[:1])
    except DatabaseError:
        teams = Team.objects.none()
        venues = Venue.objects.none()
        for field_name in ("batting_team", "bowling_team", "toss_winner"):
            if field_name in form.fields:
                form.fields[field_name].queryset = Team.objects.none()
        if "venue" in form.fields:
            form.fields["venue"].queryset = Venue.objects.none()
    return {
        "form": _TemplateForm(form),
        "form_object": form,
        "teams": teams,
        "venues": venues,
        "current_year": timezone.now().year,
    }


def _form_error_response(
    form,
    message: str = "Please correct the highlighted fields.",
    status: int = 400,
) -> JsonResponse:
    return JsonResponse(
        {
            "ok": False,
            "error": message,
            "message": message,
            "code": "validation_error",
            "errors": form.errors.as_json(),
        },
        status=status,
    )


def _model_error_response(exc: Exception, api: bool = False) -> JsonResponse:
    if isinstance(exc, ModelNotTrainedError):
        return JsonResponse(
            {
                "ok": False,
                "error": str(exc),
                "message": str(exc),
                "code": "model_not_trained",
            },
            status=503,
        )
    return JsonResponse(
        {
            "ok": False,
            "error": str(exc) or "The model could not make a prediction.",
            "message": str(exc) or "The model could not make a prediction.",
            "code": "prediction_error",
        },
        status=422,
    )


def _data_unavailable_response() -> JsonResponse:
    return _json_response(
        {
            "ok": False,
            "error": "Team, venue, or prediction data is temporarily unavailable. Import the IPL dataset and apply migrations first.",
            "code": "data_unavailable",
        },
        status=503,
    )


def _normalise_api_payload(value: dict[str, Any]) -> dict[str, Any]:
    """Accept the public field names and common client aliases."""

    payload = dict(value)
    aliases = {
        "team_a": "batting_team",
        "batting": "batting_team",
        "team_b": "bowling_team",
        "bowling": "bowling_team",
        "opponent": "bowling_team",
        "ground": "venue",
        "stadium": "venue",
        "toss": "toss_winner",
    }
    for source, target in aliases.items():
        if target not in payload and source in payload:
            payload[target] = payload[source]
    if "batting_team" not in payload and "team1" in payload:
        payload["batting_team"] = payload["team1"]
    if "bowling_team" not in payload and "team2" in payload:
        payload["bowling_team"] = payload["team2"]
    return payload


def _request_payload(request) -> tuple[dict[str, Any] | None, str | None]:
    content_type = (request.content_type or "").split(";", 1)[0].strip().lower()
    if content_type == "application/json":
        if not request.body:
            return {}, None
        try:
            value = json.loads(request.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None, "Request body must contain valid JSON."
        if not isinstance(value, dict):
            return None, "Request JSON must be an object."
        # Accept both a flat payload and {"input": {...}} for small clients.
        if "input" in value and isinstance(value["input"], dict):
            nested = dict(value["input"])
            nested.update({key: item for key, item in value.items() if key != "input"})
            return _normalise_api_payload(nested), None
        return _normalise_api_payload(value), None
    if request.POST:
        return _normalise_api_payload(request.POST.dict()), None
    return {}, None


# ---------------------------------------------------------------------------
# HTML forms and result pages


@login_required
@ensure_csrf_cookie
@require_http_methods(["GET", "POST"])
def score_prediction(request):
    initial = {"season": timezone.now().year}
    form = ScorePredictionForm(
        request.POST if request.method == "POST" else None,
        initial=initial,
    )
    context = _base_context(form)
    context.update({"page_title": "Score prediction", "prediction_type": "score"})
    if request.method == "POST" and form.is_valid():
        try:
            result = predict_score(form.cleaned_data)
            prediction = _save_prediction(request.user, form, result, Prediction.Type.SCORE)
        except ModelNotTrainedError as exc:
            form.add_error(None, str(exc))
            context.update({"form": _TemplateForm(form), "model_error": str(exc)})
            return render(request, "prediction.html", context, status=503)
        except ModelPredictionError as exc:
            form.add_error(None, str(exc))
            context.update({"form": _TemplateForm(form), "model_error": str(exc)})
            return render(request, "prediction.html", context, status=422)
        except Exception:
            logger.exception("Unexpected score prediction failure")
            form.add_error(None, "The prediction could not be completed. Please try again.")
            context.update({"form": _TemplateForm(form)})
            return render(request, "prediction.html", context, status=500)
        context.update(
            {
                "prediction": prediction,
                "prediction_record": prediction,
                "result": result,
                "score_range": {
                    "low": result.get("prediction_low"),
                    "high": result.get("prediction_high"),
                    "method": result.get("range_method"),
                },
                "page_title": "Score prediction result",
            }
        )
        return render(request, "prediction_result.html", context)
    return render(request, "prediction.html", context)


@login_required
@ensure_csrf_cookie
@require_http_methods(["GET", "POST"])
def outcome_prediction(request):
    initial = {"season": timezone.now().year}
    form = OutcomePredictionForm(
        request.POST if request.method == "POST" else None,
        initial=initial,
    )
    context = _base_context(form)
    context.update({"page_title": "Outcome prediction", "prediction_type": "outcome"})
    if request.method == "POST" and form.is_valid():
        try:
            result = predict_outcome(form.cleaned_data)
            prediction = _save_prediction(request.user, form, result, Prediction.Type.OUTCOME)
        except ModelNotTrainedError as exc:
            form.add_error(None, str(exc))
            context.update({"form": _TemplateForm(form), "model_error": str(exc)})
            return render(request, "outcome_prediction.html", context, status=503)
        except ModelPredictionError as exc:
            form.add_error(None, str(exc))
            context.update({"form": _TemplateForm(form), "model_error": str(exc)})
            return render(request, "outcome_prediction.html", context, status=422)
        except Exception:
            logger.exception("Unexpected outcome prediction failure")
            form.add_error(None, "The prediction could not be completed. Please try again.")
            context.update({"form": _TemplateForm(form)})
            return render(request, "outcome_prediction.html", context, status=500)
        context.update(
            {
                "prediction": prediction,
                "prediction_record": prediction,
                "result": result,
                "page_title": "Outcome prediction result",
            }
        )
        return render(request, "prediction_result.html", context)
    return render(request, "outcome_prediction.html", context)


@login_required
@require_GET
def prediction_result(request, pk: int):
    prediction = get_object_or_404(Prediction, pk=pk, user=request.user)
    result = {
        "predicted_score": prediction.predicted_score,
        "prediction_low": prediction.prediction_low,
        "prediction_high": prediction.prediction_high,
        "predicted_winner": prediction.outcome_winner.name if prediction.outcome_winner_id else None,
        "probabilities": prediction.outcome_probabilities or {},
    }
    context = {
        "prediction": prediction,
        "prediction_record": prediction,
        "result": result,
        "page_title": "Prediction result",
    }
    return render(request, "prediction_result.html", context)


# ---------------------------------------------------------------------------
# Searchable, filterable, paginated history


@login_required
@require_GET
def prediction_history(request):
    queryset = Prediction.objects.filter(user=request.user).select_related(
        "batting_team", "bowling_team", "venue", "toss_winner", "outcome_winner"
    )
    query = request.GET.get("q", "").strip()
    prediction_type = request.GET.get("type", request.GET.get("prediction_type", "")).strip().lower()
    team_filter = request.GET.get("team", "").strip()
    date_from = request.GET.get("from", "").strip()
    date_to = request.GET.get("to", "").strip()

    if query:
        text_filter = (
            Q(batting_team__name__icontains=query)
            | Q(bowling_team__name__icontains=query)
            | Q(venue__name__icontains=query)
            | Q(outcome_winner__name__icontains=query)
        )
        if query.isdigit():
            text_filter |= Q(predicted_score=int(query))
        queryset = queryset.filter(text_filter)
    if prediction_type in {Prediction.Type.SCORE, Prediction.Type.OUTCOME}:
        queryset = queryset.filter(prediction_type=prediction_type)
    if team_filter:
        if team_filter.isdigit():
            queryset = queryset.filter(
                Q(batting_team_id=int(team_filter)) | Q(bowling_team_id=int(team_filter))
            )
        else:
            queryset = queryset.filter(
                Q(batting_team__name__icontains=team_filter)
                | Q(bowling_team__name__icontains=team_filter)
            )
    try:
        if date_from:
            queryset = queryset.filter(created_at__date__gte=date.fromisoformat(date_from))
        if date_to:
            queryset = queryset.filter(created_at__date__lte=date.fromisoformat(date_to))
    except ValueError:
        # Invalid date filters are ignored rather than breaking the history
        # page; the form can display the valid query parameters unchanged.
        pass

    paginator = Paginator(queryset, 20)
    page_obj = paginator.get_page(request.GET.get("page"))
    querystring = request.GET.copy()
    querystring.pop("page", None)
    context = {
        "page_obj": page_obj,
        "paginator": paginator,
        "predictions": _TemplateSequence(page_obj.object_list),
        "prediction_records": page_obj.object_list,
        "q": query,
        "prediction_type": prediction_type,
        "team_filter": team_filter,
        "date_from": date_from,
        "date_to": date_to,
        "history_querystring": querystring.urlencode(),
        "page_title": "Prediction history",
        "prediction_types": Prediction.Type.choices,
        "teams": Team.objects.all(),
    }
    return render(request, "prediction_history.html", context)


@login_required
@require_POST
def delete_prediction(request, pk: int):
    prediction = get_object_or_404(Prediction, pk=pk, user=request.user)
    prediction.delete()
    wants_json = request.path.startswith("/api/") or "application/json" in (
        request.headers.get("Accept", "")
    )
    if wants_json:
        return _json_response({"ok": True, "deleted": True, "id": pk})
    messages.success(request, "Prediction deleted.")
    return redirect("prediction_history")


# ---------------------------------------------------------------------------
# JSON APIs.  CSRF protection is intentionally left enabled: clients should
# send the standard X-CSRFToken header obtained from the login/form page.


@_api_login_required
@require_POST
def score_prediction_api(request):
    payload, payload_error = _request_payload(request)
    if payload_error:
        return JsonResponse({"ok": False, "error": payload_error, "code": "invalid_json"}, status=400)
    form = ScorePredictionForm(payload or {})
    try:
        form_valid = form.is_valid()
    except DatabaseError:
        return _data_unavailable_response()
    if not form_valid:
        return _form_error_response(form)
    try:
        result = predict_score(form.cleaned_data)
        prediction = _save_prediction(request.user, form, result, Prediction.Type.SCORE)
    except DatabaseError:
        return _data_unavailable_response()
    except (ModelNotTrainedError, ModelPredictionError) as exc:
        return _model_error_response(exc, api=True)
    except Exception:
        logger.exception("Unexpected score API prediction failure")
        return JsonResponse(
            {"ok": False, "error": "The prediction could not be completed.", "code": "prediction_error"},
            status=500,
        )
    serialized_prediction = _prediction_payload(prediction, result)
    response_data = {
        "ok": True,
        "data": {
            "prediction": serialized_prediction,
            "prediction_id": prediction.pk,
            "result": result,
        },
        "prediction": serialized_prediction,
        "prediction_id": prediction.pk,
        "result": result,
        "predicted_score": prediction.predicted_score,
        "prediction_low": prediction.prediction_low,
        "prediction_high": prediction.prediction_high,
    }
    return _json_response(response_data, status=201)


@_api_login_required
@require_POST
def outcome_prediction_api(request):
    payload, payload_error = _request_payload(request)
    if payload_error:
        return JsonResponse({"ok": False, "error": payload_error, "code": "invalid_json"}, status=400)
    form = OutcomePredictionForm(payload or {})
    try:
        form_valid = form.is_valid()
    except DatabaseError:
        return _data_unavailable_response()
    if not form_valid:
        return _form_error_response(form)
    try:
        result = predict_outcome(form.cleaned_data)
        prediction = _save_prediction(request.user, form, result, Prediction.Type.OUTCOME)
    except DatabaseError:
        return _data_unavailable_response()
    except (ModelNotTrainedError, ModelPredictionError) as exc:
        return _model_error_response(exc, api=True)
    except Exception:
        logger.exception("Unexpected outcome API prediction failure")
        return JsonResponse(
            {"ok": False, "error": "The prediction could not be completed.", "code": "prediction_error"},
            status=500,
        )
    response_result = _json_safe(dict(result))
    # Avoid even an empty probability field when the classifier has no
    # predict_proba implementation.
    if not result.get("has_probabilities"):
        response_result.pop("probabilities", None)
        response_result.pop("outcome_probabilities", None)
    serialized_prediction = _prediction_payload(prediction, response_result)
    response_data = {
        "ok": True,
        "data": {
            "prediction": serialized_prediction,
            "prediction_id": prediction.pk,
            "result": response_result,
        },
        "prediction": serialized_prediction,
        "prediction_id": prediction.pk,
        "result": response_result,
        "predicted_winner": prediction.outcome_winner.name if prediction.outcome_winner_id else None,
        "winner": prediction.outcome_winner.name if prediction.outcome_winner_id else None,
    }
    if prediction.outcome_probabilities:
        response_data["outcome_probabilities"] = _probability_map(prediction.outcome_probabilities)
    return _json_response(response_data, status=201)


@_api_login_required
@require_GET
def prediction_history_api(request):
    # Reuse the HTML history's filtering rules without rendering a template.
    queryset = Prediction.objects.filter(user=request.user).select_related(
        "batting_team", "bowling_team", "venue", "toss_winner", "outcome_winner"
    )
    query = request.GET.get("q", "").strip()
    prediction_type = request.GET.get("type", request.GET.get("prediction_type", "")).strip().lower()
    team_filter = request.GET.get("team", "").strip()
    if query:
        text_filter = (
            Q(batting_team__name__icontains=query)
            | Q(bowling_team__name__icontains=query)
            | Q(venue__name__icontains=query)
            | Q(outcome_winner__name__icontains=query)
        )
        if query.isdigit():
            text_filter |= Q(predicted_score=int(query))
        queryset = queryset.filter(text_filter)
    if prediction_type in {Prediction.Type.SCORE, Prediction.Type.OUTCOME}:
        queryset = queryset.filter(prediction_type=prediction_type)
    if team_filter:
        if team_filter.isdigit():
            queryset = queryset.filter(
                Q(batting_team_id=int(team_filter)) | Q(bowling_team_id=int(team_filter))
            )
        else:
            queryset = queryset.filter(
                Q(batting_team__name__icontains=team_filter)
                | Q(bowling_team__name__icontains=team_filter)
            )
    date_from = request.GET.get("from", "").strip()
    date_to = request.GET.get("to", "").strip()
    try:
        if date_from:
            queryset = queryset.filter(created_at__date__gte=date.fromisoformat(date_from))
    except ValueError:
        pass
    try:
        if date_to:
            queryset = queryset.filter(created_at__date__lte=date.fromisoformat(date_to))
    except ValueError:
        pass
    paginator = Paginator(queryset, 20)
    page_obj = paginator.get_page(request.GET.get("page"))
    return _json_response(
        {
            "ok": True,
            "data": {
                "count": paginator.count,
                "page": page_obj.number,
                "num_pages": paginator.num_pages,
                "has_next": page_obj.has_next(),
                "has_previous": page_obj.has_previous(),
                "results": [_prediction_payload(item) for item in page_obj.object_list],
            },
            "count": paginator.count,
            "page": page_obj.number,
            "num_pages": paginator.num_pages,
            "has_next": page_obj.has_next(),
            "has_previous": page_obj.has_previous(),
            "results": [_prediction_payload(item) for item in page_obj.object_list],
        }
    )


@_api_login_required
@require_GET
def prediction_detail_api(request, pk: int):
    try:
        prediction = get_object_or_404(Prediction, pk=pk, user=request.user)
    except Http404:
        return _json_response(
            {"ok": False, "error": "Prediction not found.", "code": "not_found"},
            status=404,
        )
    payload = _prediction_payload(prediction)
    return _json_response({"ok": True, "data": {"prediction": payload}, "prediction": payload})


@_api_login_required
@require_POST
def delete_prediction_api(request, pk: int):
    try:
        prediction = get_object_or_404(Prediction, pk=pk, user=request.user)
    except Http404:
        return _json_response(
            {"ok": False, "error": "Prediction not found.", "code": "not_found"},
            status=404,
        )
    prediction.delete()
    return _json_response({"ok": True, "deleted": True, "id": pk})


@_api_login_required
@require_GET
def model_status_api(request):
    kind = request.GET.get("kind", "score")
    if kind not in {"score", "outcome"}:
        return _json_response(
            {"ok": False, "error": "kind must be score or outcome.", "code": "invalid_kind"},
            status=400,
        )
    status = model_status(kind)
    status["ok"] = bool(status.get("trained"))
    return _json_response(status)


# Conventional aliases used by different parts of the project.
score_prediction_view = score_prediction
outcome_prediction_view = outcome_prediction
prediction_history_view = prediction_history
