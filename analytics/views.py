"""HTML views for the advanced analytics workspace."""

from django.db import DatabaseError
from django.shortcuts import render
from django.urls import reverse

from . import services
from .forms import (
    HeadToHeadForm,
    PlayerComparisonForm,
    PlayerTrendForm,
    SeasonFilterForm,
    TeamComparisonForm,
    VenueAnalysisForm,
)


def _clean_filter_values(cleaned):
    return {
        "season": services._season_int(cleaned.get("season")),
        "team": cleaned.get("team"),
        "venue": cleaned.get("venue"),
    }


def _base_context(request, form, title, description, *, analysis=None, api_name=None):
    return {
        "form": form,
        "title": title,
        "description": description,
        "analysis": analysis,
        "has_analysis": analysis is not None,
        "api_url": reverse(api_name) if api_name else "",
    }


def _page_context(request, form, title, description, api_name):
    return _base_context(
        request, form, title, description, analysis=None, api_name=api_name
    )


def analytics_dashboard(request):
    form = SeasonFilterForm(request.GET or None)
    filters = {"season": None, "team": None, "venue": None}
    if request.GET and form.is_valid():
        filters = _clean_filter_values(form.cleaned_data)
    parameters = {
        "season": filters["season"].pk if hasattr(filters["season"], "pk") else filters["season"],
        "team": filters["team"].pk if filters["team"] else None,
        "venue": filters["venue"].pk if filters["venue"] else None,
    }
    try:
        analysis = services.cached_analytics(
            "overview",
            parameters,
            lambda: services.analytics_overview(**filters),
        )
    except DatabaseError:
        analysis = None
    context = _base_context(
        request,
        form,
        "Advanced analytics",
        "Filter the full cricket dataset by season, team, and venue.",
        analysis=analysis,
        api_name="analytics-data",
    )
    context["overview"] = analysis
    return render(request, "analytics/analytics.html", context)


def team_comparison(request):
    form = TeamComparisonForm(request.GET or None)
    context = _page_context(
        request,
        form,
        "Team comparison",
        "Compare form, scoring, wickets, tosses, and direct head-to-head results.",
        "team-comparison",
    )
    if request.GET and form.is_valid():
        data = form.cleaned_data
        team_a, team_b = data["team_a"], data["team_b"]
        season = services._season_int(data.get("season"))
        venue = data.get("venue")
        parameters = {
            "team_a": team_a.pk,
            "team_b": team_b.pk,
            "season": season,
            "venue": venue.pk if venue else None,
        }
        try:
            context["analysis"] = services.cached_analytics(
                "team-comparison",
                parameters,
                lambda: services.head_to_head(
                    team_a, team_b, season=season, venue=venue
                ),
            )
            context["has_analysis"] = True
        except DatabaseError:
            context["has_analysis"] = False
    return render(request, "analytics/analysis.html", context)


# Conventional ``*_view`` names are retained for callers that import views
# directly instead of resolving a URL.
analytics_dashboard_view = analytics_dashboard
team_comparison_view = team_comparison


def player_comparison(request):
    form = PlayerComparisonForm(request.GET or None)
    context = _page_context(
        request,
        form,
        "Player comparison",
        "Compare batting, bowling, economy, strike rate, and season-by-season output.",
        "player-comparison",
    )
    if request.GET and form.is_valid():
        data = form.cleaned_data
        player_a, player_b = data["player_a"], data["player_b"]
        season = services._season_int(data.get("season"))
        team, venue = data.get("team"), data.get("venue")
        parameters = {
            "player_a": player_a.pk,
            "player_b": player_b.pk,
            "season": season,
            "team": team.pk if team else None,
            "venue": venue.pk if venue else None,
        }
        try:
            context["analysis"] = services.cached_analytics(
                "player-comparison",
                parameters,
                lambda: services.compare_players(
                    player_a,
                    player_b,
                    season=season,
                    team=team,
                    venue=venue,
                ),
            )
            context["has_analysis"] = True
        except DatabaseError:
            context["has_analysis"] = False
    return render(request, "analytics/analysis.html", context)


def venue_analysis(request):
    form = VenueAnalysisForm(request.GET or None)
    context = _page_context(
        request,
        form,
        "Venue analysis",
        "Examine scoring, toss choices, wicket patterns, and team records by ground.",
        "venue-stats",
    )
    if request.GET and form.is_valid():
        data = form.cleaned_data
        venue = data["venue"]
        season = services._season_int(data.get("season"))
        team = data.get("team")
        parameters = {
            "venue": venue.pk,
            "season": season,
            "team": team.pk if team else None,
        }
        try:
            context["analysis"] = services.cached_analytics(
                "venue-analysis",
                parameters,
                lambda: services.venue_analysis(
                    venue=venue, season=season, team=team
                ),
            )
            context["has_analysis"] = True
        except DatabaseError:
            context["has_analysis"] = False
    return render(request, "analytics/analysis.html", context)


def player_trend(request):
    form = PlayerTrendForm(request.GET or None)
    context = _page_context(
        request,
        form,
        "Player trend",
        "Track batting average, strike rate, wickets, and economy season by season.",
        "player-stats",
    )
    if request.GET and form.is_valid():
        data = form.cleaned_data
        player = data["player"]
        team, venue = data.get("team"), data.get("venue")
        start = services._season_int(data.get("start_season"))
        end = services._season_int(data.get("end_season"))
        parameters = {
            "player": player.pk,
            "team": team.pk if team else None,
            "venue": venue.pk if venue else None,
            "start": start,
            "end": end,
        }
        try:
            context["analysis"] = services.cached_analytics(
                "player-trend",
                parameters,
                lambda: services.player_trend(
                    player,
                    start_season=start,
                    end_season=end,
                    team=team,
                    venue=venue,
                ),
            )
            context["has_analysis"] = True
        except DatabaseError:
            context["has_analysis"] = False
    return render(request, "analytics/analysis.html", context)


def head_to_head(request):
    form = HeadToHeadForm(request.GET or None)
    context = _page_context(
        request,
        form,
        "Head to head",
        "Review every meeting, win split, margin, toss record, and season trend.",
        "team-comparison",
    )
    if request.GET and form.is_valid():
        data = form.cleaned_data
        team_a, team_b = data["team_a"], data["team_b"]
        season = services._season_int(data.get("season"))
        venue = data.get("venue")
        parameters = {
            "team_a": team_a.pk,
            "team_b": team_b.pk,
            "season": season,
            "venue": venue.pk if venue else None,
        }
        try:
            context["analysis"] = services.cached_analytics(
                "head-to-head",
                parameters,
                lambda: services.head_to_head(
                    team_a, team_b, season=season, venue=venue
                ),
            )
            context["has_analysis"] = True
        except DatabaseError:
            context["has_analysis"] = False
    return render(request, "analytics/analysis.html", context)


analytics_dashboard_view = analytics_dashboard
team_comparison_view = team_comparison
player_comparison_view = player_comparison
venue_analysis_view = venue_analysis
player_trend_view = player_trend
head_to_head_view = head_to_head
