"""Read-only JSON APIs for advanced cricket analytics."""

from functools import wraps

from django.db import DatabaseError
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from matches.models import Team, Venue
from players.models import Player

from . import services


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


def _error(message, *, code="invalid_filter", status=400):
    return JsonResponse(
        {"ok": False, "data": None, "error": message, "code": code},
        status=status,
        json_dumps_params={"indent": 2},
    )


def _success(data, *, status=200):
    # Keep the historical flattened fields for existing dashboard clients and
    # also expose the documented ``data`` envelope.  This makes the endpoint
    # useful to small API clients without breaking the Chart.js frontend.
    response_data = {"ok": True, "data": data}
    response_data.update(data)
    return JsonResponse(response_data, status=status, json_dumps_params={"indent": 2})


def _positive_id(request, *names):
    """Read a positive ID, accepting a few conventional query aliases."""
    name = names[0]
    raw = None
    for candidate in names:
        if candidate in request.GET:
            name = candidate
            raw = request.GET.get(candidate)
            break
    if raw in (None, "", "all", "null"):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"'{name}' must be a positive integer ID.")
    if value <= 0:
        raise ValueError(f"'{name}' must be a positive integer ID.")
    return value


def _season(request):
    raw = request.GET.get("season")
    if raw in (None, "", "all", "null"):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError("'season' must be a positive integer.")
    if value <= 0:
        raise ValueError("'season' must be a positive integer.")
    return value


def _season_parameter(request, name):
    """Parse an optional positive season/range parameter consistently."""
    raw = request.GET.get(name)
    if raw in (None, "", "all", "null"):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"'{name}' must be a positive integer.")
    if value <= 0:
        raise ValueError(f"'{name}' must be a positive integer.")
    return value


def _lookup(model, value, label):
    if value is None:
        return None
    instance = model.objects.filter(pk=value).first()
    if instance is None:
        raise ValueError(f"The selected {label} does not exist.")
    return instance


def _common_filters(request):
    season = _season(request)
    team = _lookup(Team, _positive_id(request, "team", "team_id"), "team")
    venue = _lookup(Venue, _positive_id(request, "venue", "venue_id"), "venue")
    return season, team, venue


def _parameters(season, team=None, venue=None, **extra):
    values = {
        "season": season,
        "team": team.pk if team else None,
        "venue": venue.pk if venue else None,
    }
    values.update(extra)
    return values


@json_require_GET
def team_stats_api(request):
    try:
        season, team, venue = _common_filters(request)
        parameters = _parameters(season, team, venue)
        data = services.cached_analytics(
            "team-stats",
            parameters,
            lambda: services.team_statistics(
                team=team, season=season, venue=venue
            ),
        )
    except ValueError as exc:
        return _error(str(exc))
    except DatabaseError:
        return _error("Analytics data is temporarily unavailable.", code="database_error", status=503)
    return _success(data)


@json_require_GET
def player_stats_api(request):
    try:
        season, team, venue = _common_filters(request)
        player = _lookup(Player, _positive_id(request, "player", "player_id"), "player")
        parameters = _parameters(
            season, team, venue, player=player.pk if player else None
        )
        data = services.cached_analytics(
            "player-stats",
            parameters,
            lambda: services.player_statistics(
                player=player, season=season, team=team, venue=venue
            ),
        )
    except ValueError as exc:
        return _error(str(exc))
    except DatabaseError:
        return _error("Analytics data is temporarily unavailable.", code="database_error", status=503)
    return _success(data)


@json_require_GET
def season_stats_api(request):
    try:
        season, team, venue = _common_filters(request)
        parameters = _parameters(season, team, venue)
        data = services.cached_analytics(
            "season-stats",
            parameters,
            lambda: services.season_statistics(
                season=season, team=team, venue=venue
            ),
        )
    except ValueError as exc:
        return _error(str(exc))
    except DatabaseError:
        return _error("Analytics data is temporarily unavailable.", code="database_error", status=503)
    return _success(data)


@json_require_GET
def venue_stats_api(request):
    try:
        season, team, venue = _common_filters(request)
        parameters = _parameters(season, team, venue)
        data = services.cached_analytics(
            "venue-stats",
            parameters,
            lambda: services.venue_statistics(
                venue=venue, season=season, team=team
            ),
        )
    except ValueError as exc:
        return _error(str(exc))
    except DatabaseError:
        return _error("Analytics data is temporarily unavailable.", code="database_error", status=503)
    return _success(data)


def _empty_comparison(label, first_id, second_id):
    return {
        "filters": {f"{label}_a_id": first_id, f"{label}_b_id": second_id},
        f"{label}_a": None,
        f"{label}_b": None,
        "summary": {"meetings": 0},
        "matches": [],
        "empty": True,
    }


@json_require_GET
def team_comparison_api(request):
    try:
        first_id = _positive_id(request, "team_a", "team1", "first_team")
        second_id = _positive_id(request, "team_b", "team2", "second_team")
        season = _season(request)
        venue = _lookup(Venue, _positive_id(request, "venue", "venue_id"), "venue")
        parameters = {
            "team_a": first_id,
            "team_b": second_id,
            "season": season,
            "venue": venue.pk if venue else None,
        }
        if first_id is None or second_id is None:
            return _success(_empty_comparison("team", first_id, second_id))
        if first_id == second_id:
            raise ValueError("'team_a' and 'team_b' must be different teams.")
        team_a = _lookup(Team, first_id, "first team")
        team_b = _lookup(Team, second_id, "second team")
        data = services.cached_analytics(
            "team-comparison-api",
            parameters,
            lambda: services.head_to_head(
                team_a, team_b, season=season, venue=venue
            ),
        )
    except ValueError as exc:
        return _error(str(exc))
    except DatabaseError:
        return _error("Analytics data is temporarily unavailable.", code="database_error", status=503)
    return _success(data)


def _empty_player_comparison(first_id, second_id):
    return {
        "filters": {
            "player_a_id": first_id,
            "player_b_id": second_id,
        },
        "player_a": None,
        "player_b": None,
        "leaders": {},
        "empty": True,
    }


@json_require_GET
def player_comparison_api(request):
    try:
        first_id = _positive_id(request, "player_a", "player1", "first_player")
        second_id = _positive_id(request, "player_b", "player2", "second_player")
        season = _season(request)
        team = _lookup(Team, _positive_id(request, "team", "team_id"), "team")
        venue = _lookup(Venue, _positive_id(request, "venue", "venue_id"), "venue")
        parameters = {
            "player_a": first_id,
            "player_b": second_id,
            "season": season,
            "team": team.pk if team else None,
            "venue": venue.pk if venue else None,
        }
        if first_id is None or second_id is None:
            return _success(_empty_player_comparison(first_id, second_id))
        if first_id == second_id:
            raise ValueError("'player_a' and 'player_b' must be different players.")
        player_a = _lookup(Player, first_id, "first player")
        player_b = _lookup(Player, second_id, "second player")
        data = services.cached_analytics(
            "player-comparison-api",
            parameters,
            lambda: services.compare_players(
                player_a,
                player_b,
                season=season,
                team=team,
                venue=venue,
            ),
        )
    except ValueError as exc:
        return _error(str(exc))
    except DatabaseError:
        return _error("Analytics data is temporarily unavailable.", code="database_error", status=503)
    return _success(data)


@json_require_GET
def player_trend_api(request):
    """Return a player's season-by-season batting and bowling trend."""
    try:
        player_id = _positive_id(request, "player", "player_id")
        if player_id is None:
            raise ValueError("'player' is required for a trend analysis.")
        player = _lookup(Player, player_id, "player")
        team = _lookup(Team, _positive_id(request, "team", "team_id"), "team")
        venue = _lookup(Venue, _positive_id(request, "venue", "venue_id"), "venue")
        start_season = _season_parameter(request, "start_season")
        end_season = _season_parameter(request, "end_season")
        if start_season and end_season and start_season > end_season:
            raise ValueError("'start_season' must not be after 'end_season'.")
        parameters = {
            "player": player.pk,
            "team": team.pk if team else None,
            "venue": venue.pk if venue else None,
            "start_season": start_season,
            "end_season": end_season,
        }
        data = services.cached_analytics(
            "player-trend-api",
            parameters,
            lambda: services.player_trend(
                player,
                start_season=start_season,
                end_season=end_season,
                team=team,
                venue=venue,
            ),
        )
    except ValueError as exc:
        return _error(str(exc))
    except DatabaseError:
        return _error(
            "Analytics data is temporarily unavailable.",
            code="database_error",
            status=503,
        )
    return _success(data)


@json_require_GET
def analytics_data_api(request):
    try:
        season, team, venue = _common_filters(request)
        team_a = _lookup(
            Team, _positive_id(request, "team_a", "team1", "first_team"), "first team"
        )
        team_b = _lookup(
            Team, _positive_id(request, "team_b", "team2", "second_team"), "second team"
        )
        player = _lookup(Player, _positive_id(request, "player", "player_id"), "player")
        player_a = _lookup(
            Player, _positive_id(request, "player_a", "player1", "first_player"), "first player"
        )
        player_b = _lookup(
            Player, _positive_id(request, "player_b", "player2", "second_player"), "second player"
        )
        start_season = _season_parameter(request, "start_season")
        end_season = _season_parameter(request, "end_season")
        if start_season and end_season and start_season > end_season:
            raise ValueError("'start_season' must not be after 'end_season'.")
        if team_a and team_b and team_a.pk == team_b.pk:
            raise ValueError("'team_a' and 'team_b' must be different teams.")
        if player_a and player_b and player_a.pk == player_b.pk:
            raise ValueError("'player_a' and 'player_b' must be different players.")
        parameters = _parameters(
            season,
            team,
            venue,
            team_a=team_a.pk if team_a else None,
            team_b=team_b.pk if team_b else None,
            player=player.pk if player else None,
            player_a=player_a.pk if player_a else None,
            player_b=player_b.pk if player_b else None,
            start_season=start_season,
            end_season=end_season,
        )
        data = services.cached_analytics(
            "analytics-data",
            parameters,
            lambda: services.analytics_data(
                season=season,
                team=team,
                venue=venue,
                team_a=team_a,
                team_b=team_b,
                player=player,
                player_a=player_a,
                player_b=player_b,
                start_season=start_season,
                end_season=end_season,
            ),
        )
    except ValueError as exc:
        return _error(str(exc))
    except DatabaseError:
        return _error("Analytics data is temporarily unavailable.", code="database_error", status=503)
    return _success(data)


# Public function aliases retained for callers that use the shorter endpoint
# names rather than the historical ``*_api`` suffixes.
team_stats = team_stats_api
player_stats = player_stats_api
season_stats = season_stats_api
venue_stats = venue_stats_api
team_comparison = team_comparison_api
player_comparison = player_comparison_api
analytics_data = analytics_data_api
player_trend = player_trend_api
head_to_head_api = team_comparison_api
