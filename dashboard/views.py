"""Public dashboard, account views, and the dashboard statistics API."""

from functools import wraps

from django.contrib import messages
from django.contrib.auth import login, update_session_auth_hash
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, SetPasswordForm
from django.db import DatabaseError
from django.http import JsonResponse
from django.shortcuts import redirect, render, resolve_url
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET

from matches.models import Match, Team, Venue
from players.models import Player

from .forms import ProfileForm, RegistrationForm
from .services import get_dashboard_stats


def _database_counts():
    try:
        return {
            "matches": Match.objects.count(),
            "teams": Team.objects.count(),
            "players": Player.objects.count(),
            "venues": Venue.objects.count(),
            "seasons": Match.objects.values("season").distinct().count(),
        }
    except DatabaseError:
        return {"matches": 0, "teams": 0, "players": 0, "venues": 0, "seasons": 0}


@require_GET
def health(request):
    """Lightweight readiness endpoint for local launchers and monitoring."""

    return JsonResponse({"status": "ok", "service": "ipl-analytics"})


def home(request):
    return render(request, "dashboard/home.html", {"counts": _database_counts()})


def about(request):
    return render(request, "dashboard/about.html", {"counts": _database_counts()})


@login_required
def dashboard(request):
    counts = _database_counts()
    return render(
        request,
        "dashboard/dashboard.html",
        {
            "counts": counts,
            "dashboard_api_url": reverse("dashboard-stats"),
            "has_data": bool(counts["matches"]),
        },
    )


def register(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    if request.method == "POST":
        form = RegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, "Welcome! Your account is ready.")
            return redirect("dashboard")
    else:
        form = RegistrationForm()
    return render(request, "dashboard/register.html", {"form": form})


def user_login(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    form = AuthenticationForm(
        request,
        data=request.POST if request.method == "POST" else None,
    )
    if request.method == "POST" and form.is_valid():
        login(request, form.get_user())
        destination = request.POST.get("next") or request.GET.get("next")
        if destination and url_has_allowed_host_and_scheme(
            destination,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return redirect(destination)
        return redirect(resolve_url("dashboard"))
    return render(
        request,
        "dashboard/login.html",
        {
            "form": form,
            "next": request.POST.get("next") or request.GET.get("next", ""),
        },
    )


def logout(request):
    if request.method != "POST":
        return JsonResponse(
            {
                "ok": False,
                "data": None,
                "error": "Method not allowed. Use POST.",
                "code": "method_not_allowed",
            },
            status=405,
            json_dumps_params={"indent": 2},
        )
    auth_logout(request)
    messages.success(request, "You have been signed out.")
    return redirect("home")


@login_required
def profile(request):
    """Update profile details and/or the password from the same page.

    The page contains two independent forms.  Older versions bound every
    POST to the profile form, which made a password-only submission fail with
    unrelated required-field errors.  Detect the fields that were submitted
    so either form can be used independently (and both may be submitted by a
    client that wants to update them together).
    """
    form = ProfileForm(instance=request.user)
    password_form = None
    saved = False

    if request.method == "POST":
        profile_fields = {"first_name", "last_name", "email"}
        has_profile_fields = any(field in request.POST for field in profile_fields)
        has_password_fields = any(
            field in request.POST for field in ("new_password1", "new_password2")
        )

        if has_profile_fields:
            form = ProfileForm(request.POST, instance=request.user)
        if has_password_fields:
            password_form = SetPasswordForm(request.user, request.POST)

        profile_valid = not has_profile_fields or form.is_valid()
        password_valid = password_form is None or password_form.is_valid()
        if (has_profile_fields or has_password_fields) and profile_valid and password_valid:
            user = form.save() if has_profile_fields else request.user
            if password_form is not None:
                user = password_form.save()
                update_session_auth_hash(request, user)
            messages.success(request, "Your profile has been updated.")
            saved = True

    if saved:
        return redirect("profile")
    return render(
        request,
        "dashboard/profile.html",
        {"form": form, "password_form": password_form},
    )


def json_require_GET(view):
    """Use Django's ``require_GET`` while returning JSON for 405 responses."""
    guarded_view = require_GET(view)

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        response = guarded_view(request, *args, **kwargs)
        if response.status_code == 405:
            json_response = JsonResponse(
                {
                    "ok": False,
                    "data": None,
                    "error": "Method not allowed. Use GET.",
                    "code": "method_not_allowed",
                },
                status=405,
                json_dumps_params={"indent": 2},
            )
            json_response["Allow"] = "GET"
            return json_response
        return response

    return wrapped


def _dashboard_error(message, *, code="invalid_query", status=400):
    return JsonResponse(
        {"ok": False, "data": None, "error": message, "code": code},
        status=status,
        json_dumps_params={"indent": 2},
    )


@json_require_GET
def dashboard_stats_api(request):
    season_raw = request.GET.get("season")
    season = None
    if season_raw not in (None, "", "all", "null"):
        try:
            season = int(season_raw)
        except (TypeError, ValueError):
            return _dashboard_error("The season must be a positive integer.")
        if season <= 0:
            return _dashboard_error("The season must be a positive integer.")
    try:
        data = get_dashboard_stats(season=season)
    except DatabaseError:
        return _dashboard_error(
            "Dashboard data is temporarily unavailable.", code="database_error", status=503
        )
    response_data = {"ok": True, "data": data}
    response_data.update(data)
    return JsonResponse(response_data, json_dumps_params={"indent": 2})


def _wants_json(request):
    return request.path.startswith("/api/") or "application/json" in request.headers.get(
        "Accept", ""
    )


def error_404(request, exception):
    message = "The page or API resource you requested was not found."
    if _wants_json(request):
        return JsonResponse(
            {"ok": False, "data": None, "error": message, "code": "not_found"},
            status=404,
            json_dumps_params={"indent": 2},
        )
    return render(
        request,
        "dashboard/error.html",
        {"status_code": 404, "headline": "Page not found", "message": message},
        status=404,
    )


def error_500(request):
    message = "The server could not complete this request. Please try again shortly."
    if _wants_json(request):
        return JsonResponse(
            {"ok": False, "data": None, "error": message, "code": "server_error"},
            status=500,
            json_dumps_params={"indent": 2},
        )
    return render(
        request,
        "dashboard/error.html",
        {"status_code": 500, "headline": "Something went wrong", "message": message},
        status=500,
    )


# Direct-import aliases used by a few integrations.
dashboard_view = dashboard
login_view = user_login
logout_view = logout
profile_view = profile
registration_view = register
register_view = register
index = home
home_view = home
about_view = about
dashboard_stats = dashboard_stats_api
