"""Player discovery, detail and seasonal ranking views."""

from __future__ import annotations

from collections import OrderedDict
from decimal import Decimal
import json

from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, render

from matches.models import Match, Team
from players.forms import PlayerFilterForm, PlayerRankingFilterForm
from players.models import Player, PlayerSeasonPerformance


PLAYERS_PER_PAGE = 24
RANKINGS_PER_PAGE = 25


def _career_rows(player_ids):
    rows = (
        PlayerSeasonPerformance.objects.filter(player_id__in=player_ids)
        .values("player_id")
        .annotate(
            appearances=Sum("appearances"),
            batting_innings=Sum("batting_innings"),
            runs=Sum("runs"),
            balls=Sum("balls"),
            fours=Sum("fours"),
            sixes=Sum("sixes"),
            dismissals=Sum("dismissals"),
            bowling_innings=Sum("bowling_innings"),
            bowling_balls=Sum("bowling_balls"),
            wickets=Sum("wickets"),
            runs_conceded=Sum("runs_conceded"),
            catches=Sum("catches"),
        )
    )
    return {row["player_id"]: row for row in rows}


def _career_stats(values):
    runs = values.get("runs") or 0
    balls = values.get("balls") or 0
    dismissals = values.get("dismissals") or 0
    wickets = values.get("wickets") or 0
    conceded = values.get("runs_conceded") or 0
    bowling_balls = values.get("bowling_balls") or 0
    return {
        "appearances": values.get("appearances") or 0,
        "batting_innings": values.get("batting_innings") or 0,
        "runs": runs,
        "balls": balls,
        "fours": values.get("fours") or 0,
        "sixes": values.get("sixes") or 0,
        "dismissals": dismissals,
        "average": round(runs / dismissals, 2) if dismissals else float(runs),
        "strike_rate": round(runs * 100 / balls, 2) if balls else 0.0,
        "bowling_innings": values.get("bowling_innings") or 0,
        "bowling_balls": bowling_balls,
        "overs": round(bowling_balls / 6, 2),
        "wickets": wickets,
        "runs_conceded": conceded,
        "bowling_average": round(conceded / wickets, 2) if wickets else 0.0,
        "economy": round(conceded * 6 / bowling_balls, 2) if bowling_balls else 0.0,
        "catches": values.get("catches") or 0,
    }


def _attach_career_stats(players):
    player_list = list(players)
    rows = _career_rows([player.id for player in player_list])
    for player in player_list:
        stats = _career_stats(rows.get(player.id, {}))
        player.career_stats = stats
        player.career_runs = stats["runs"]
        player.career_wickets = stats["wickets"]
        player.career_average = stats["average"]
        player.career_strike_rate = stats["strike_rate"]
        player.career_bowling_average = stats["bowling_average"]
        player.career_economy = stats["economy"]
    return player_list


def player_list(request):
    """Search players and filter by historical team or inferred role."""

    data = request.GET.copy()
    if "q" not in data and "search" in data:
        data["q"] = data["search"]
    form = PlayerFilterForm(data) if data else PlayerFilterForm()
    filters = form.cleaned_data if form.is_bound and form.is_valid() else {}
    players = Player.objects.select_related("current_team")

    query = (filters.get("q") or "").strip()
    if query:
        players = players.filter(
            Q(name__icontains=query)
            | Q(current_team__name__icontains=query)
            | Q(affiliations__team__name__icontains=query)
        ).distinct()
    if filters.get("team"):
        players = players.filter(
            Q(affiliations__team=filters["team"])
            | Q(current_team=filters["team"])
        ).distinct()
    if filters.get("role"):
        players = players.filter(role=filters["role"])

    paginator = Paginator(players.order_by("name"), PLAYERS_PER_PAGE)
    page_obj = paginator.get_page(request.GET.get("page"))
    visible_players = _attach_career_stats(page_obj.object_list)
    has_filters = any(data.get(key) for key in ("q", "search", "team", "role"))
    context = {
        "players": visible_players,
        "player_list": visible_players,
        "page_obj": page_obj,
        "paginator": paginator,
        "is_paginated": page_obj.has_other_pages(),
        "filter_form": form,
        "search_form": form,
        "teams": Team.objects.filter(player_affiliations__isnull=False)
        .distinct()
        .order_by("name"),
        "role_choices": Player.Role.choices,
        "search_query": filters.get("q", ""),
        "search": filters.get("q", ""),
        "selected_team": filters.get("team"),
        "selected_role": filters.get("role"),
        "has_filters": has_filters,
        "total_count": paginator.count,
        "available_count": Player.objects.count(),
        "has_players": Player.objects.exists(),
        "empty_state_title": "No matching players" if has_filters else "No players imported",
        "empty_state_message": (
            "Try a different name, team or role."
            if has_filters
            else "Run python manage.py import_ipl_data to build player statistics."
        ),
        "title": "No matching players" if has_filters else "No players imported",
        "message": (
            "Try a different name, team or role."
            if has_filters
            else "Run python manage.py import_ipl_data to build player statistics."
        ),
        "action_url": request.path if has_filters else "",
        "action_label": "Clear filters",
        "empty_state": {
            "title": "No matching players" if has_filters else "No players imported",
            "message": (
                "Try a different name, team or role."
                if has_filters
                else "Run python manage.py import_ipl_data to build player statistics."
            ),
        },
        "command_hint": "python manage.py import_ipl_data --replace",
    }
    return render(request, "players/players.html", context)


def _seasonal_chart(rows):
    seasons: OrderedDict[int, dict] = OrderedDict()
    for row in rows:
        values = seasons.setdefault(
            row.season,
            {
                "season": row.season,
                "appearances": 0,
                "batting_innings": 0,
                "runs": 0,
                "balls": 0,
                "fours": 0,
                "sixes": 0,
                "dismissals": 0,
                "highest_score": 0,
                "bowling_innings": 0,
                "bowling_balls": 0,
                "wickets": 0,
                "runs_conceded": 0,
                "catches": 0,
            },
        )
        for field in (
            "appearances",
            "batting_innings",
            "runs",
            "balls",
            "fours",
            "sixes",
            "dismissals",
            "bowling_innings",
            "bowling_balls",
            "wickets",
            "runs_conceded",
            "catches",
        ):
            values[field] += getattr(row, field)
        values["highest_score"] = max(values["highest_score"], row.highest_score)
    for values in seasons.values():
        values["average"] = (
            round(values["runs"] / values["dismissals"], 2)
            if values["dismissals"]
            else float(values["runs"])
        )
        values["strike_rate"] = (
            round(values["runs"] * 100 / values["balls"], 2) if values["balls"] else 0.0
        )
        values["bowling_average"] = (
            round(values["runs_conceded"] / values["wickets"], 2)
            if values["wickets"]
            else 0.0
        )
        values["economy"] = (
            round(values["runs_conceded"] * 6 / values["bowling_balls"], 2)
            if values["bowling_balls"]
            else 0.0
        )
    chart_values = list(seasons.values())
    labels = [str(item["season"]) for item in chart_values]
    return chart_values, {
        "labels": labels,
        "datasets": [
            {
                "label": "Runs",
                "data": [item["runs"] for item in chart_values],
                "yAxisID": "runs",
            },
            {
                "label": "Wickets",
                "data": [item["wickets"] for item in chart_values],
                "yAxisID": "wickets",
            },
            {
                "label": "Batting average",
                "data": [item["average"] for item in chart_values],
                "yAxisID": "rates",
            },
            {
                "label": "Economy",
                "data": [item["economy"] for item in chart_values],
                "yAxisID": "rates",
            },
        ],
    }


def player_detail(request, pk):
    """Player career totals, recent matches and season-by-season chart data."""

    player = get_object_or_404(
        Player.objects.select_related("current_team").prefetch_related("affiliations__team"), pk=pk
    )
    seasonal_rows = list(
        PlayerSeasonPerformance.objects.filter(player=player)
        .select_related("team")
        .order_by("season", "team__name")
    )
    seasonal_data, chart_data = _seasonal_chart(seasonal_rows)
    performance_count = player.performances.count()
    recent_performances = list(
        player.performances.select_related("match", "match__venue", "match__winner")
        .order_by("-match__date")[:25]
    )
    career_stats = _career_stats(
        {
            "appearances": sum(row.appearances for row in seasonal_rows),
            "batting_innings": sum(row.batting_innings for row in seasonal_rows),
            "runs": sum(row.runs for row in seasonal_rows),
            "balls": sum(row.balls for row in seasonal_rows),
            "fours": sum(row.fours for row in seasonal_rows),
            "sixes": sum(row.sixes for row in seasonal_rows),
            "dismissals": sum(row.dismissals for row in seasonal_rows),
            "bowling_innings": sum(row.bowling_innings for row in seasonal_rows),
            "bowling_balls": sum(row.bowling_balls for row in seasonal_rows),
            "wickets": sum(row.wickets for row in seasonal_rows),
            "runs_conceded": sum(row.runs_conceded for row in seasonal_rows),
            "catches": sum(row.catches for row in seasonal_rows),
        }
    )
    seasons = list(
        PlayerSeasonPerformance.objects.filter(player=player)
        .order_by("-season")
        .values_list("season", flat=True)
        .distinct()
    )
    player.role_label = player.get_role_display()
    player.total_seasons = len(seasonal_data)
    context = {
        "player": player,
        "seasonal_performances": seasonal_rows,
        "seasonal_performance": seasonal_rows,
        "seasonal_stats": seasonal_rows,
        "seasonal_data": seasonal_data,
        "season_data": seasonal_data,
        "season_chart_data": chart_data,
        "chart_data": chart_data,
        "chart_data_json": json.dumps(chart_data, separators=(",", ":")),
        "chart_labels": chart_data["labels"],
        "chart_datasets": chart_data["datasets"],
        "career_stats": career_stats,
        "career": career_stats,
        "batting_stats": career_stats,
        "bowling_stats": career_stats,
        "total_matches": career_stats["appearances"],
        "total_runs": career_stats["runs"],
        "total_wickets": career_stats["wickets"],
        "recent_performances": recent_performances,
        "performances": recent_performances,
        "batting_performances": [row for row in recent_performances if row.balls or row.runs],
        "bowling_performances": [
            row for row in recent_performances if row.bowling_balls or row.wickets
        ],
        "affiliations": player.affiliations.all(),
        "seasons": seasons,
        "performance_count": performance_count,
        "player_of_match_awards": player.player_of_match_awards.count(),
        "has_seasonal_data": bool(seasonal_data),
        "has_performances": bool(recent_performances),
        "empty_state_title": "No player statistics",
        "empty_state_message": "No delivery-derived statistics are available for this player.",
        "title": "No player statistics",
        "message": "No delivery-derived statistics are available for this player.",
        "empty_state": {
            "title": "No player statistics",
            "message": "No delivery-derived statistics are available for this player.",
        },
    }
    return render(request, "players/player_detail.html", context)


def _ranking_value(values, metric):
    runs = values.get("runs") or 0
    dismissals = values.get("dismissals") or 0
    wickets = values.get("wickets") or 0
    conceded = values.get("runs_conceded") or 0
    bowling_balls = values.get("bowling_balls") or 0
    if metric == "wickets":
        return wickets
    if metric == "batting_average":
        return round(runs / dismissals, 2) if dismissals else float(runs)
    if metric == "strike_rate":
        balls = values.get("balls") or 0
        return round(runs * 100 / balls, 2) if balls else 0.0
    if metric == "bowling_average":
        return round(conceded / wickets, 2) if wickets else 0.0
    if metric == "economy":
        return round(conceded * 6 / bowling_balls, 2) if bowling_balls else 0.0
    return runs


def player_rankings(request):
    """Rank aggregated player/team season rows for a selected statistic."""

    seasons = list(
        PlayerSeasonPerformance.objects.order_by("-season")
        .values_list("season", flat=True)
        .distinct()
    )
    if not seasons:
        seasons = list(
            Match.objects.order_by("-season").values_list("season", flat=True).distinct()
        )
    latest_season = seasons[0] if seasons else None
    data = request.GET.copy()
    user_has_filters = any(
        data.get(key) for key in ("q", "search", "team", "role", "season", "metric", "stat")
    )
    if "q" not in data and "search" in data:
        data["q"] = data["search"]
    if "metric" not in data and "stat" in data:
        data["metric"] = data["stat"]
    if latest_season is not None and not data.get("season"):
        data["season"] = str(latest_season)
    form = PlayerRankingFilterForm(data if data else None, seasons=seasons)
    filters = form.cleaned_data if form.is_bound and form.is_valid() else {}
    season = filters.get("season") or latest_season
    metric = filters.get("metric") or "runs"
    label = dict(PlayerRankingFilterForm.METRIC_CHOICES).get(metric, metric)

    ranked_values = []
    if season:
        stats = PlayerSeasonPerformance.objects.filter(season=season)
        query = (filters.get("q") or "").strip()
        if query:
            stats = stats.filter(
                Q(player__name__icontains=query)
                | Q(player__current_team__name__icontains=query)
                | Q(player__affiliations__team__name__icontains=query)
            ).distinct()
        if filters.get("team"):
            stats = stats.filter(team=filters["team"]).filter(
                Q(player__affiliations__team=filters["team"])
                | Q(player__current_team=filters["team"])
            ).distinct()
        if filters.get("role"):
            stats = stats.filter(player__role=filters["role"])
        if metric == "batting_average":
            stats = stats.filter(dismissals__gt=0)
        elif metric == "strike_rate":
            stats = stats.filter(balls__gt=0)
        elif metric == "bowling_average":
            stats = stats.filter(wickets__gt=0)
        elif metric == "economy":
            stats = stats.filter(bowling_balls__gt=0)
        rows = stats.values("player_id").annotate(
            runs=Sum("runs"),
            balls=Sum("balls"),
            dismissals=Sum("dismissals"),
            wickets=Sum("wickets"),
            bowling_balls=Sum("bowling_balls"),
            runs_conceded=Sum("runs_conceded"),
        )
        for values in rows:
            value = _ranking_value(values, metric)
            ranked_values.append(
                {
                    "player_id": values["player_id"],
                    "metric_value": float(Decimal(str(value))),
                    "season": season,
                    "runs": values["runs"] or 0,
                    "balls": values["balls"] or 0,
                    "dismissals": values["dismissals"] or 0,
                    "wickets": values["wickets"] or 0,
                    "bowling_balls": values["bowling_balls"] or 0,
                    "runs_conceded": values["runs_conceded"] or 0,
                }
            )
    lower_is_better = metric in {"bowling_average", "economy"}
    ranked_values.sort(
        key=lambda row: (
            row["metric_value"] if lower_is_better else -row["metric_value"],
            row["player_id"],
        )
    )
    player_map = Player.objects.select_related("current_team").in_bulk(
        [row["player_id"] for row in ranked_values]
    )
    for index, row in enumerate(ranked_values, start=1):
        row["rank"] = index
        row["player"] = player_map.get(row["player_id"])
    paginator = Paginator(ranked_values, RANKINGS_PER_PAGE)
    page_obj = paginator.get_page(request.GET.get("page"))
    visible_rankings = list(page_obj.object_list)
    has_filters = user_has_filters
    context = {
        "rankings": visible_rankings,
        "ranking": visible_rankings,
        "ranking_rows": visible_rankings,
        "ranked_players": visible_rankings,
        "players": visible_rankings,
        "top_players": visible_rankings,
        "page_obj": page_obj,
        "paginator": paginator,
        "is_paginated": page_obj.has_other_pages(),
        "filter_form": form,
        "search_form": form,
        "seasons": seasons,
        "teams": Team.objects.filter(player_affiliations__isnull=False)
        .distinct()
        .order_by("name"),
        "selected_season": season,
        "selected_team": filters.get("team"),
        "selected_role": filters.get("role"),
        "search_query": filters.get("q", ""),
        "search": filters.get("q", ""),
        "metric": metric,
        "metric_label": label,
        "stat": metric,
        "has_filters": has_filters,
        "has_rankings": bool(ranked_values),
        "total_count": len(ranked_values),
        "empty_state_title": "No rankings available",
        "empty_state_message": (
            "Try another season or clear the player filters."
            if has_filters
            else "Player rankings appear after IPL delivery data is imported."
        ),
        "title": "No rankings available",
        "message": (
            "Try another season or clear the player filters."
            if has_filters
            else "Player rankings appear after IPL delivery data is imported."
        ),
        "action_url": request.path if has_filters else "",
        "action_label": "Clear filters",
        "empty_state": {
            "title": "No rankings available",
            "message": (
                "Try another season or clear the player filters."
                if has_filters
                else "Player rankings appear after IPL delivery data is imported."
            ),
        },
        "command_hint": "python manage.py import_ipl_data --replace",
    }
    return render(request, "players/rankings.html", context)


# Explicit aliases for integrations that use the ``*_view`` naming convention.
player_list_view = player_list
player_detail_view = player_detail
player_rankings_view = player_rankings
