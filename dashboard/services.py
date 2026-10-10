"""Database-backed KPI and chart aggregation for the main dashboard.

The public chart payload is deliberately assembled in this module so the HTTP
views stay small and the same calculations can be cached.  All collections use
stable, JSON-serialisable values and return empty collections when the database
has no data yet.
"""

import json
from collections import Counter
from hashlib import sha256

from django.conf import settings
from django.core.cache import cache
from django.db import DatabaseError
from django.db.models import Avg, Count, Max, Q, Sum
from django.utils import timezone

from matches.models import Match, MatchInnings, Team, Venue
from players.models import Player, PlayerPerformance, PlayerSeasonPerformance

# Names used by the browser charts.  Keeping these in one place prevents a
# chart and its API field from silently drifting apart.
MATCHES_BY_SEASON = "matches_by_season"
TEAM_WINS = "team_wins"
RUNS_BY_TEAM = "runs_by_team"
AVERAGE_SCORE_BY_TEAM = "average_score_by_team"
TOP_BATTERS = "top_batters"
TOP_BOWLERS = "top_bowlers"
WIN_PERCENTAGES = "win_percentages"
TOSS_RESULTS = "toss_results"
MATCHES_BY_VENUE = "matches_by_venue"
BATTING_FIRST_WINS = "batting_first_wins"
CHASING_WINS = "chasing_wins"
BOUNDARIES_BY_TEAM = "boundaries_by_team"
WICKETS_BY_TEAM = "wickets_by_team"
TEAM_RADAR = "team_radar"
TEAM_COMPARISON = "team_comparison"
PLAYER_OUTPUT = "player_output"


def _number(value, digits=2):
    """Convert database numeric values (including Decimal) to JSON numbers."""
    if value is None:
        return 0
    return round(float(value), digits)


def _percentage(part, whole):
    return _number((part or 0) * 100 / whole, 2) if whole else 0.0


def _cache_timeout():
    try:
        return max(0, int(getattr(settings, "DATA_ANALYTICS_CACHE_SECONDS", 300)))
    except (TypeError, ValueError):
        return 300


def _database_signature(season):
    """Return a cheap signature that expires cached results after ingestion."""
    try:
        matches = Match.objects.filter(season=season) if season else Match.objects.all()
        signature = matches.aggregate(count=Count("id"), latest=Max("updated_at"))
        related = {
            "innings": MatchInnings.objects.filter(
                **({"match__season": season} if season else {})
            ).count(),
            "performances": PlayerPerformance.objects.filter(
                **({"match__season": season} if season else {})
            ).count(),
            "season_stats": PlayerSeasonPerformance.objects.filter(
                **({"season": season} if season else {})
            ).count(),
        }
        return "|".join(
            [
                str(signature.get("count", 0)),
                str(signature.get("latest") or ""),
                *(f"{name}:{value}" for name, value in related.items()),
            ]
        )
    except DatabaseError:
        # A partially-created development database should still render.  A
        # failed signature is deliberately not cached.
        return ""


def _match_rows(matches):
    return [
        {
            "id": match.id,
            "season": match.season,
            "date": match.date.isoformat(),
            "venue": match.venue.name,
            "city": match.city or match.venue.city,
            "team1": match.team1.name,
            "team2": match.team2.name,
            "winner": match.winner.name if match.winner_id else None,
            "result": match.result_summary,
            "result_type": match.result_type,
        }
        for match in matches.select_related("venue", "team1", "team2", "winner")[:8]
    ]


def _top_batters(season_stats, season=None):
    if season_stats.exists():
        rows = season_stats.values("player_id", "player__name").annotate(
            matches=Count("id", distinct=True),
            team_name=Max("team__name"),
            team_color=Max("team__primary_color"),
            total_runs=Sum("runs"),
            balls=Sum("balls"),
            fours=Sum("fours"),
            sixes=Sum("sixes"),
            dismissals=Sum("dismissals"),
            highest_score=Max("highest_score"),
        ).order_by("-total_runs", "player__name")[:10]
        rows = list(rows)
        score_performances = PlayerPerformance.objects.all()
        if season:
            score_performances = score_performances.filter(match__season=season)
        highest_by_player = {
            row["player_id"]: row["highest_match_runs"] or 0
            for row in score_performances.values("player_id").annotate(
                highest_match_runs=Max("runs")
            )
        }

        result = []
        for row in rows:
            runs = row["total_runs"] or 0
            balls = row["balls"] or 0
            dismissals = row["dismissals"] or 0
            result.append(
                {
                    "player_id": row["player_id"],
                    "player": row["player__name"],
                    "team": row["team_name"],
                    "color": row["team_color"] or "#6C63FF",
                    "matches": row["matches"],
                    "runs": runs,
                    "average": _number(runs / dismissals if dismissals else runs),
                    "strike_rate": _number(runs * 100 / balls if balls else 0),
                    "fours": row["fours"] or 0,
                    "sixes": row["sixes"] or 0,
                    "highest_score": max(
                        row["highest_score"] or 0,
                        highest_by_player.get(row["player_id"], 0),
                    ),
                }
            )
        return result

    # PlayerPerformance is the normalized source of truth.  This fallback also
    # makes the dashboard useful before the optional seasonal rollup is built.
    performances = PlayerPerformance.objects.all()
    if season:
        performances = performances.filter(match__season=season)
    rows = performances.values("player_id", "player__name").annotate(
        matches=Count("id", distinct=True),
        total_runs=Sum("runs"),
        balls=Sum("balls"),
        fours=Sum("fours"),
        sixes=Sum("sixes"),
        highest_score=Max("highest_score"),
        highest_match_runs=Max("runs"),
    ).order_by("-total_runs", "player__name")[:10]
    return [
        {
            "player_id": row["player_id"],
            "player": row["player__name"],
            "team": None,
            "color": "#6C63FF",
            "matches": row["matches"],
            "runs": row["total_runs"] or 0,
            # Dismissals are not stored on the ball-level performance model.
            "average": _number(row["total_runs"] or 0),
            "strike_rate": _number(
                (row["total_runs"] or 0) * 100 / (row["balls"] or 0)
                if row["balls"]
                else 0
            ),
            "fours": row["fours"] or 0,
            "sixes": row["sixes"] or 0,
            "highest_score": max(
                row["highest_score"] or 0,
                row["highest_match_runs"] or 0,
            ),
        }
        for row in rows
    ]


def _top_bowlers(season_stats, season=None):
    if season_stats.exists():
        rows = season_stats.values("player_id", "player__name").annotate(
            matches=Count("id", distinct=True),
            team_name=Max("team__name"),
            team_color=Max("team__primary_color"),
            wickets=Sum("wickets"),
            bowling_balls=Sum("bowling_balls"),
            runs_conceded=Sum("runs_conceded"),
        ).order_by("-wickets", "runs_conceded", "player__name")[:10]

        result = []
        for row in rows:
            wickets = row["wickets"] or 0
            balls = row["bowling_balls"] or 0
            conceded = row["runs_conceded"] or 0
            result.append(
                {
                    "player_id": row["player_id"],
                    "player": row["player__name"],
                    "team": row["team_name"],
                    "color": row["team_color"] or "#6C63FF",
                    "matches": row["matches"],
                    "wickets": wickets,
                    "average": _number(conceded / wickets if wickets else 0),
                    "economy": _number(conceded * 6 / balls if balls else 0),
                    "runs_conceded": conceded,
                }
            )
        return result

    performances = PlayerPerformance.objects.all()
    if season:
        performances = performances.filter(match__season=season)
    rows = performances.values("player_id", "player__name").annotate(
        matches=Count("id", distinct=True),
        wickets=Sum("wickets"),
        bowling_balls=Sum("bowling_balls"),
        runs_conceded=Sum("runs_conceded"),
    ).order_by("-wickets", "runs_conceded", "player__name")[:10]
    return [
        {
            "player_id": row["player_id"],
            "player": row["player__name"],
            "team": None,
            "color": "#6C63FF",
            "matches": row["matches"],
            "wickets": row["wickets"] or 0,
            "average": _number(
                (row["runs_conceded"] or 0) / (row["wickets"] or 0)
                if row["wickets"]
                else 0
            ),
            "economy": _number(
                (row["runs_conceded"] or 0) * 6 / (row["bowling_balls"] or 0)
                if row["bowling_balls"]
                else 0
            ),
            "runs_conceded": row["runs_conceded"] or 0,
        }
        for row in rows
    ]


def _player_output(season=None, limit=40):
    """Return players who contributed both runs and wickets in the selection.

    Building this from one aggregate query is more reliable than intersecting
    independent top-batter and top-bowler lists, which frequently contain no
    common player when specialist batters and specialist bowlers dominate.
    """

    performances = PlayerPerformance.objects.filter(runs__gt=0, wickets__gt=0)
    if season:
        performances = performances.filter(match__season=season)
    rows = performances.values(
        "player_id",
        "player__name",
        "player__current_team__name",
    ).annotate(
        runs=Sum("runs"),
        wickets=Sum("wickets"),
        matches=Count("id", distinct=True),
    ).order_by("-runs", "player__name")[:limit]
    return {
        "points": [
            {
                "player_id": row["player_id"],
                "label": row["player__name"],
                "team": row["player__current_team__name"],
                "matches": row["matches"],
                "x": row["runs"] or 0,
                "y": row["wickets"] or 0,
            }
            for row in rows
        ]
    }


def _team_radar(team_metrics):
    labels = [
        "Wins",
        "Win rate",
        "Runs",
        "Average score",
        "Wickets",
        "Boundaries",
    ]
    if not team_metrics:
        return {"labels": labels, "datasets": []}

    keys = [
        "wins",
        "win_percentage",
        "runs",
        "average_score",
        "wickets",
        "boundaries",
    ]
    maxima = {
        key: max((float(team.get(key, 0)) for team in team_metrics), default=0)
        for key in keys
    }
    datasets = []
    for team in team_metrics:
        datasets.append(
            {
                "team_id": team["team_id"],
                "label": team["team"],
                "color": team.get("color") or "#6C63FF",
                "data": [
                    _number(float(team.get(key, 0)) / maxima[key] * 100)
                    if maxima[key]
                    else 0
                    for key in keys
                ],
            }
        )
    return {"labels": labels, "datasets": datasets}


def build_dashboard_stats(season=None):
    """Build the complete public dashboard payload from normalized models."""
    matches = Match.objects.all()
    if season:
        matches = matches.filter(season=season)

    innings = MatchInnings.objects.all()
    season_stats = PlayerSeasonPerformance.objects.all()
    if season:
        innings = innings.filter(match__season=season)
        season_stats = season_stats.filter(season=season)

    match_aggregate = matches.aggregate(
        count=Count("id"),
        completed=Count("id", filter=Q(winner__isnull=False)),
        latest_date=Max("date"),
        latest_season=Max("season"),
    )
    innings_aggregate = innings.aggregate(
        total_runs=Sum("runs"),
        total_wickets=Sum("wickets"),
        average_score=Avg("runs"),
    )
    if season:
        performance_aggregate = PlayerPerformance.objects.filter(
            match__season=season
        ).aggregate(
            highest_score=Max("highest_score"),
            highest_match_runs=Max("runs"),
        )
    else:
        performance_aggregate = PlayerPerformance.objects.aggregate(
            highest_score=Max("highest_score"),
            highest_match_runs=Max("runs"),
        )

    total_runs = innings_aggregate["total_runs"] or 0
    total_wickets = innings_aggregate["total_wickets"] or 0
    boundaries = season_stats.aggregate(
        fours=Sum("fours"), sixes=Sum("sixes")
    )
    if not season_stats.exists():
        boundary_performances = PlayerPerformance.objects.all()
        if season:
            boundary_performances = boundary_performances.filter(
                match__season=season
            )
        boundaries = boundary_performances.aggregate(
            fours=Sum("fours"), sixes=Sum("sixes")
        )
    total_boundaries = (boundaries["fours"] or 0) + (boundaries["sixes"] or 0)

    kpis = {
        "total_matches": match_aggregate["count"] or 0,
        "completed_matches": match_aggregate["completed"] or 0,
        "total_teams": Team.objects.count(),
        "total_players": Player.objects.count(),
        "total_venues": Venue.objects.filter(matches__in=matches).distinct().count()
        if matches.exists()
        else 0,
        "total_seasons": matches.values("season").distinct().count(),
        "latest_season": match_aggregate["latest_season"],
        "latest_match_date": match_aggregate["latest_date"].isoformat()
        if match_aggregate["latest_date"]
        else None,
        "total_runs": total_runs,
        "total_wickets": total_wickets,
        "average_team_score": _number(innings_aggregate["average_score"]),
        "average_score": _number(innings_aggregate["average_score"]),
        "highest_score": max(
            performance_aggregate["highest_score"] or 0,
            performance_aggregate["highest_match_runs"] or 0,
        ),
        "total_boundaries": total_boundaries,
        "total_fours": boundaries["fours"] or 0,
        "total_sixes": boundaries["sixes"] or 0,
    }

    matches_by_season = [
        {
            "season": row["season"],
            "matches": row["matches"],
            "runs": row["runs"] or 0,
            "average_score": _number(row["average_score"]),
        }
        for row in matches.values("season")
        .annotate(
            matches=Count("id", distinct=True),
            runs=Sum("innings__runs"),
            average_score=Avg("innings__runs"),
        )
        .order_by("season")
    ]

    team_wins = [
        {
            "team_id": row["winner_id"],
            "team": row["winner__name"],
            "color": row["winner__primary_color"],
            "wins": row["wins"],
        }
        for row in matches.filter(winner__isnull=False)
        .values("winner_id", "winner__name", "winner__primary_color")
        .annotate(wins=Count("id"))
        .order_by("-wins", "winner__name")
    ]

    team_runs = {
        row["team_id"]: row
        for row in innings.values("team_id", "team__name", "team__primary_color")
        .annotate(total_runs=Sum("runs"), average_score=Avg("runs"), innings=Count("id"))
        .order_by("team__name")
    }
    runs_by_team = [
        {
            "team_id": row["team_id"],
            "team": row["team__name"],
            "color": row["team__primary_color"],
            "runs": row["total_runs"] or 0,
            "innings": row["innings"],
        }
        for row in sorted(team_runs.values(), key=lambda item: (-(item["total_runs"] or 0), item["team__name"]))
    ]
    average_score_by_team = [
        {
            "team_id": row["team_id"],
            "team": row["team__name"],
            "color": row["team__primary_color"],
            "runs": row["total_runs"] or 0,
            "innings": row["innings"],
            "average_score": _number(row["average_score"]),
        }
        for row in sorted(team_runs.values(), key=lambda item: (-(item["average_score"] or 0), item["team__name"]))
    ]

    played = Counter()
    for first, second in matches.values_list("team1_id", "team2_id").iterator():
        played[first] += 1
        played[second] += 1
    wins_by_team = {row["team_id"]: row["wins"] for row in team_wins}
    team_names = {
        row.id: {"name": row.name, "color": row.primary_color}
        for row in Team.objects.filter(id__in=played)
    }
    win_percentages = [
        {
            "team_id": team_id,
            "team": team_names.get(team_id, {}).get("name", f"Team {team_id}"),
            "color": team_names.get(team_id, {}).get("color", "#6C63FF"),
            "matches": matches_played,
            "wins": wins_by_team.get(team_id, 0),
            "win_percentage": _percentage(wins_by_team.get(team_id, 0), matches_played),
        }
        for team_id, matches_played in played.items()
    ]
    win_percentages.sort(key=lambda row: (-row["win_percentage"], row["team"]))

    toss_decisions = {
        row["toss_decision"] or "unknown": row["matches"]
        for row in matches.exclude(toss_decision="")
        .values("toss_decision")
        .annotate(matches=Count("id"))
    }
    toss_results = (
        [
            {
                "decision": "bat",
                "label": "Won toss and chose to bat",
                "matches": toss_decisions.get("bat", 0),
            },
            {
                "decision": "field",
                "label": "Won toss and chose to field",
                "matches": toss_decisions.get("field", 0),
            },
        ]
        if matches.exists()
        else []
    )
    toss_outcome_counter = Counter()
    for row in matches.filter(winner__isnull=False).values(
        "toss_winner_id", "winner_id", "toss_decision"
    ):
        if not row["toss_winner_id"] or not row["toss_decision"]:
            toss_outcome_counter["no_toss_data"] += 1
        elif row["winner_id"] == row["toss_winner_id"]:
            key = "bat" if row["toss_decision"] == Match.TossDecision.BAT else "field"
            toss_outcome_counter[key] += 1
        else:
            toss_outcome_counter["lost_toss"] += 1
    toss_outcomes = (
        [
            {
                "outcome": "bat",
                "label": "Toss winner chose to bat and won",
                "matches": toss_outcome_counter["bat"],
            },
            {
                "outcome": "field",
                "label": "Toss winner chose to field and won",
                "matches": toss_outcome_counter["field"],
            },
            {
                "outcome": "lost_toss",
                "label": "Toss winner lost the match",
                "matches": toss_outcome_counter["lost_toss"],
            },
            {
                "outcome": "no_toss_data",
                "label": "Toss data unavailable",
                "matches": toss_outcome_counter["no_toss_data"],
            },
        ]
        if matches.exists()
        else []
    )

    venue_rows = {
        row["venue_id"]: row
        for row in matches.values("venue_id", "venue__name", "venue__city")
        .annotate(matches=Count("id", distinct=True), wins=Count("id", filter=Q(winner__isnull=False)))
    }
    venue_scores = {
        row["match__venue_id"]: row
        for row in innings.values("match__venue_id")
        .annotate(average_score=Avg("runs"), runs=Sum("runs"))
    }
    matches_by_venue = [
        {
            "venue_id": row["venue_id"],
            "venue": row["venue__name"],
            "city": row["venue__city"],
            "matches": row["matches"],
            "completed": row["wins"],
            "average_score": _number(venue_scores.get(row["venue_id"], {}).get("average_score")),
            "runs": venue_scores.get(row["venue_id"], {}).get("runs") or 0,
        }
        for row in sorted(venue_rows.values(), key=lambda item: (-item["matches"], item["venue__name"]))
    ]

    batting_first_counter = Counter()
    chasing_counter = Counter()
    team_names_full = {
        row.id: {"name": row.name, "color": row.primary_color}
        for row in Team.objects.all()
    }
    for row in innings.filter(innings=1, match__winner__isnull=False).values(
        "team_id", "match__winner_id"
    ):
        if row["team_id"] == row["match__winner_id"]:
            batting_first_counter[row["team_id"]] += 1
        else:
            chasing_counter[row["team_id"]] += 1
    batting_first_wins = [
        {
            "team_id": team_id,
            "team": team_names_full.get(team_id, {}).get("name", f"Team {team_id}"),
            "color": team_names_full.get(team_id, {}).get("color", "#6C63FF"),
            "wins": count,
        }
        for team_id, count in batting_first_counter.items()
    ]
    chasing_wins = [
        {
            "team_id": team_id,
            "team": team_names_full.get(team_id, {}).get("name", f"Team {team_id}"),
            "color": team_names_full.get(team_id, {}).get("color", "#6C63FF"),
            "wins": count,
        }
        for team_id, count in chasing_counter.items()
    ]
    batting_first_wins.sort(key=lambda row: (-row["wins"], row["team"]))
    chasing_wins.sort(key=lambda row: (-row["wins"], row["team"]))

    if season_stats.exists():
        boundary_source = season_stats.values(
            "team_id", "team__name", "team__primary_color"
        ).annotate(
            total_fours=Sum("fours"),
            total_sixes=Sum("sixes"),
            boundary_players=Count("player_id", distinct=True),
        )
        boundary_rows = {
            row["team_id"]: {
                "team_id": row["team_id"],
                "team__name": row["team__name"],
                "team__primary_color": row["team__primary_color"],
                "fours": row["total_fours"] or 0,
                "sixes": row["total_sixes"] or 0,
                "boundary_players": row["boundary_players"],
            }
            for row in boundary_source
            if row["team_id"] is not None
        }
    else:
        # Seasonal rollups are optional.  Keep the chart useful for a fresh
        # import that has only normalized per-match performance records.
        boundary_source = PlayerPerformance.objects.all()
        if season:
            boundary_source = boundary_source.filter(match__season=season)
        boundary_source = boundary_source.values(
            "player__current_team_id",
            "player__current_team__name",
            "player__current_team__primary_color",
        ).annotate(
            total_fours=Sum("fours"),
            total_sixes=Sum("sixes"),
            boundary_players=Count("player_id", distinct=True),
        )
        boundary_rows = {
            row["player__current_team_id"]: {
                "team_id": row["player__current_team_id"],
                "team__name": row["player__current_team__name"],
                "team__primary_color": row["player__current_team__primary_color"],
                "fours": row["total_fours"] or 0,
                "sixes": row["total_sixes"] or 0,
                "boundary_players": row["boundary_players"],
            }
            for row in boundary_source
            if row["player__current_team_id"] is not None
        }
    boundaries_by_team = [
        {
            "team_id": row["team_id"],
            "team": row["team__name"],
            "color": row["team__primary_color"],
            "fours": row["fours"] or 0,
            "sixes": row["sixes"] or 0,
            "total": (row["fours"] or 0) + (row["sixes"] or 0),
        }
        for row in sorted(
            boundary_rows.values(),
            key=lambda item: (-((item["fours"] or 0) + (item["sixes"] or 0)), item["team__name"]),
        )
    ]
    if season_stats.exists():
        wicket_source = season_stats.values(
            "team_id", "team__name", "team__primary_color"
        ).annotate(
            total_wickets=Sum("wickets"),
            bowlers=Count("player_id", filter=Q(wickets__gt=0), distinct=True),
        )
        wicket_rows = {
            row["team_id"]: {
                "team_id": row["team_id"],
                "team__name": row["team__name"],
                "team__primary_color": row["team__primary_color"],
                "wickets": row["total_wickets"] or 0,
                "bowlers": row["bowlers"],
            }
            for row in wicket_source
            if row["team_id"] is not None
        }
    else:
        wicket_source = PlayerPerformance.objects.all()
        if season:
            wicket_source = wicket_source.filter(match__season=season)
        wicket_source = wicket_source.values(
            "player__current_team_id",
            "player__current_team__name",
            "player__current_team__primary_color",
        ).annotate(
            total_wickets=Sum("wickets"),
            bowlers=Count("player_id", filter=Q(wickets__gt=0), distinct=True),
        )
        wicket_rows = {
            row["player__current_team_id"]: {
                "team_id": row["player__current_team_id"],
                "team__name": row["player__current_team__name"],
                "team__primary_color": row["player__current_team__primary_color"],
                "wickets": row["total_wickets"] or 0,
                "bowlers": row["bowlers"],
            }
            for row in wicket_source
            if row["player__current_team_id"] is not None
        }
    wickets_by_team = [
        {
            "team_id": row["team_id"],
            "team": row["team__name"],
            "color": row["team__primary_color"],
            "wickets": row["wickets"] or 0,
            "bowlers": row["bowlers"],
        }
        for row in sorted(
            wicket_rows.values(), key=lambda item: (-(item["wickets"] or 0), item["team__name"])
        )
    ]

    # A five-team radar is readable on the dashboard and still compares real
    # values from the same filtered dataset as the other charts.
    ranking_ids = [row["team_id"] for row in win_percentages[:5]]
    team_metrics = []
    for team_id in ranking_ids:
        wins = wins_by_team.get(team_id, 0)
        played_count = played.get(team_id, 0)
        scoring = team_runs.get(team_id, {})
        boundary = boundary_rows.get(team_id, {})
        wickets = wicket_rows.get(team_id, {})
        team_metrics.append(
            {
                "team_id": team_id,
                "team": team_names_full.get(team_id, {}).get("name", f"Team {team_id}"),
                "color": team_names_full.get(team_id, {}).get("color", "#6C63FF"),
                "matches": played_count,
                "wins": wins,
                "win_percentage": _percentage(wins, played_count),
                "runs": scoring.get("total_runs") or 0,
                "innings": scoring.get("innings") or 0,
                "average_score": _number(scoring.get("average_score")),
                "wickets": wickets.get("wickets") or 0,
                "wickets_per_match": _number(
                    (wickets.get("wickets") or 0) / played_count if played_count else 0
                ),
                "fours": boundary.get("fours") or 0,
                "sixes": boundary.get("sixes") or 0,
                "boundaries": (boundary.get("fours") or 0) + (boundary.get("sixes") or 0),
            }
        )
    team_radar = _team_radar(team_metrics)

    charts = {
        MATCHES_BY_SEASON: matches_by_season,
        TEAM_WINS: team_wins,
        RUNS_BY_TEAM: runs_by_team,
        AVERAGE_SCORE_BY_TEAM: average_score_by_team,
        TOP_BATTERS: _top_batters(season_stats, season=season),
        TOP_BOWLERS: _top_bowlers(season_stats, season=season),
        WIN_PERCENTAGES: win_percentages,
        TOSS_RESULTS: toss_results,
        "toss_outcomes": toss_outcomes,
        MATCHES_BY_VENUE: matches_by_venue,
        BATTING_FIRST_WINS: batting_first_wins,
        CHASING_WINS: chasing_wins,
        BOUNDARIES_BY_TEAM: boundaries_by_team,
        WICKETS_BY_TEAM: wickets_by_team,
        TEAM_RADAR: team_radar,
        TEAM_COMPARISON: team_metrics,
        PLAYER_OUTPUT: _player_output(season=season),
        # Readable aliases for chart consumers that use alternate naming.
        "matches_per_season": matches_by_season,
        "wins_by_team": team_wins,
        "runs_and_average_scores": {
            "runs": runs_by_team,
            "average_scores": average_score_by_team,
        },
        "win_percentage_by_team": win_percentages,
        "toss_decisions": toss_results,
        "venue_performance": matches_by_venue,
        "batting_first_vs_chasing": {
            "batting_first": batting_first_wins,
            "chasing": chasing_wins,
        },
        "boundary_stats": boundaries_by_team,
        "wicket_stats": wickets_by_team,
    }
    payload = {
        "kpis": kpis,
        "filters": {"season": season, "available_seasons": list(
            Match.objects.values_list("season", flat=True).distinct().order_by("season")
        )},
        "charts": charts,
        "recent_matches": _match_rows(matches),
        "empty": not bool(match_aggregate["count"]),
        "generated_at": timezone.now().isoformat(),
    }

    # Convenient aliases for consumers that do not use the charts envelope.
    payload.update(kpis)
    payload.update(
        {
            "season_matches": matches_by_season,
            "runs_average_score": {
                "runs": runs_by_team,
                "average_scores": average_score_by_team,
            },
            "toss_result": toss_results,
            "venues": matches_by_venue,
            "boundaries": boundaries_by_team,
            "wickets": wickets_by_team,
        }
    )
    return payload


def get_dashboard_stats(season=None):
    """Return cached dashboard JSON data with a short, ingestion-aware TTL."""
    signature = _database_signature(season)
    cache_key = "dashboard:stats:v3"
    cache_material = json.dumps(
        {"season": season, "signature": signature}, sort_keys=True, default=str
    )
    cache_key = f"{cache_key}:{sha256(cache_material.encode()).hexdigest()}"

    timeout = _cache_timeout()
    if timeout and signature:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    payload = build_dashboard_stats(season=season)
    if timeout and signature:
        cache.set(cache_key, payload, timeout)
    return payload


# Stable aliases for callers that use generic service names.
get_stats = get_dashboard_stats
build_stats = build_dashboard_stats
calculate_dashboard_stats = build_dashboard_stats
get_dashboard_statistics = get_dashboard_stats
get_dashboard_data = get_dashboard_stats
