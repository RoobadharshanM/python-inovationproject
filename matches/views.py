"""Match list and scorecard views."""

from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, render

from matches.forms import MatchFilterForm
from matches.models import Match, MatchInnings, Team, Venue


MATCHS_PER_PAGE = 20


def match_list(request):
    """List matches with composable GET filters and safe pagination."""

    seasons = list(
        Match.objects.order_by("-season")
        .values_list("season", flat=True)
        .distinct()
    )
    data = request.GET.copy()
    if "q" not in data and "search" in data:
        data["q"] = data["search"]
    form = MatchFilterForm(data, seasons=seasons) if data else MatchFilterForm(seasons=seasons)
    filters = form.cleaned_data if form.is_bound and form.is_valid() else {}

    matches = Match.objects.select_related("venue", "team1", "team2", "winner", "player_of_match")
    query = (filters.get("q") or "").strip()
    if query:
        search = Q(team1__name__icontains=query) | Q(team2__name__icontains=query)
        search |= Q(venue__name__icontains=query) | Q(city__icontains=query)
        search |= Q(winner__name__icontains=query) | Q(result__icontains=query)
        if query.isdigit():
            search |= Q(external_id=int(query))
        matches = matches.filter(search)
    if filters.get("season"):
        matches = matches.filter(season=filters["season"])
    if filters.get("team"):
        matches = matches.filter(
            Q(team1=filters["team"]) | Q(team2=filters["team"])
        ).distinct()
    if filters.get("venue"):
        matches = matches.filter(venue=filters["venue"])
    if filters.get("winner"):
        matches = matches.filter(winner=filters["winner"])

    paginator = Paginator(matches.order_by("-date", "-id"), MATCHS_PER_PAGE)
    page_obj = paginator.get_page(request.GET.get("page"))
    match_list = list(page_obj.object_list)
    has_filters = any(
        data.get(key) for key in ("q", "search", "season", "team", "venue", "winner")
    )
    total_count = paginator.count
    context = {
        "matches": match_list,
        "matches_list": match_list,
        "match_list": match_list,
        "match_count": total_count,
        "page_obj": page_obj,
        "paginator": paginator,
        "is_paginated": page_obj.has_other_pages(),
        "filter_form": form,
        "search_form": form,
        "seasons": seasons,
        "teams": Team.objects.order_by("name"),
        "venues": Venue.objects.order_by("name"),
        "winners": Team.objects.filter(match_wins__isnull=False).distinct().order_by("name"),
        "search_query": filters.get("q", ""),
        "search": filters.get("q", ""),
        "selected_season": filters.get("season"),
        "selected_team": filters.get("team"),
        "selected_venue": filters.get("venue"),
        "selected_winner": filters.get("winner"),
        "has_filters": has_filters,
        "total_count": total_count,
        "available_count": Match.objects.count(),
        "has_matches": Match.objects.exists(),
        "empty_state_title": "No matching matches" if has_filters else "No matches imported",
        "empty_state_message": (
            "Try clearing or changing the filters."
            if has_filters
            else "Run python manage.py import_ipl_data to load the match archive."
        ),
        "title": "No matching matches" if has_filters else "No matches imported",
        "message": (
            "Try clearing or changing the filters."
            if has_filters
            else "Run python manage.py import_ipl_data to load the match archive."
        ),
        "action_url": request.path if has_filters else "",
        "action_label": "Clear filters",
        "empty_state": {
            "title": "No matching matches" if has_filters else "No matches imported",
            "message": (
                "Try clearing or changing the filters."
                if has_filters
                else "Run python manage.py import_ipl_data to load the match archive."
            ),
        },
        "command_hint": "python manage.py import_ipl_data --replace",
    }
    return render(request, "matches/matches.html", context)


def match_detail(request, pk):
    """Display match metadata, aggregated scorecards and player performances."""

    match = get_object_or_404(
        Match.objects.select_related("venue", "team1", "team2", "toss_winner", "winner", "player_of_match"),
        pk=pk,
    )
    innings = list(
        MatchInnings.objects.filter(match=match)
        .select_related("team")
        .order_by("innings")
    )
    performances = list(
        match.player_performances.select_related("player", "player__current_team")
        .order_by("-runs", "-wickets", "player__name")
    )
    scorecards = {
        item.innings: {
            "team": item.team,
            "runs": item.runs,
            "wickets": item.wickets,
            "balls": item.balls,
            "overs": item.overs_display,
            "extras": item.extras,
        }
        for item in innings
    }
    batting_performances = [item for item in performances if item.balls or item.runs]
    bowling_performances = [item for item in performances if item.bowling_balls or item.wickets]
    context = {
        "match": match,
        "innings": innings,
        "innings_list": innings,
        "scorecards": scorecards,
        "performances": performances,
        "player_performances": performances,
        "batting_performances": batting_performances,
        "bowling_performances": bowling_performances,
        "player_of_match": match.player_of_match,
        "teams": match.teams,
        "venue": match.venue,
        "has_scorecards": bool(innings),
        "has_performances": bool(performances),
        "empty_state_title": "No scorecard imported",
        "empty_state_message": "Delivery data is not available for this match.",
        "title": "No scorecard imported",
        "message": "Delivery data is not available for this match.",
    }
    return render(request, "matches/match_detail.html", context)


# Explicit view aliases for callers that use the ``*_view`` naming convention.
match_list_view = match_list
match_detail_view = match_detail
