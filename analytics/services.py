"""Advanced, database-backed cricket analytics services.

Only normalized models are used here.  Public functions return plain Python
objects containing JSON-safe numbers, lists, and dictionaries, which keeps the
HTTP API small and makes every result straightforward to cache.
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
from players.models import PlayerPerformance, PlayerSeasonPerformance


def _number(value, digits=2):
    if value is None:
        return 0
    return round(float(value), digits)


def _percentage(part, whole):
    return _number((part or 0) * 100 / whole, 2) if whole else 0.0


def _season_int(value):
    """Return a positive season number, or ``None`` for an empty filter."""
    if value in (None, "", "all", "null"):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _match_queryset(season=None, team=None, venue=None):
    season = _season_int(season)
    matches = Match.objects.all()
    if season:
        matches = matches.filter(season=season)
    if team:
        matches = matches.filter(Q(team1=team) | Q(team2=team))
    if venue:
        matches = matches.filter(venue=venue)
    return matches


def _innings_queryset(matches):
    return MatchInnings.objects.filter(match__in=matches)


def _performance_queryset(matches):
    return PlayerPerformance.objects.filter(match__in=matches)


def _serialize_match(match):
    return {
        "id": match.id,
        "external_id": match.external_id,
        "season": match.season,
        "date": match.date.isoformat(),
        "venue_id": match.venue_id,
        "venue": match.venue.name,
        "city": match.city or match.venue.city,
        "team1_id": match.team1_id,
        "team1": match.team1.name,
        "team2_id": match.team2_id,
        "team2": match.team2.name,
        "toss_winner_id": match.toss_winner_id,
        "toss_winner": match.toss_winner.name if match.toss_winner_id else None,
        "toss_decision": match.toss_decision,
        "winner_id": match.winner_id,
        "winner": match.winner.name if match.winner_id else None,
        "result": match.result_summary,
        "result_type": match.result_type,
        "win_by_runs": match.win_by_runs,
        "win_by_wickets": match.win_by_wickets,
    }


def _serialize_team(team):
    return {
        "id": team.pk,
        "name": team.name,
        "short_name": team.short_name,
        "color": team.primary_color,
    }


def _serialize_player(player):
    return {
        "id": player.pk,
        "name": player.name,
        "role": player.get_role_display(),
        "role_key": player.role,
        "nationality": player.nationality,
        "current_team_id": player.current_team_id,
        "current_team": player.current_team.name if player.current_team_id else None,
    }


def _top_players(performances, role="bat", limit=10):
    """Aggregate ball/match player records and enrich batting averages."""
    if role == "bat":
        rows = performances.values(
            "player_id", "player__name", "player__current_team__name"
        ).annotate(
            appearances=Count("id", distinct=True),
            total_runs=Sum("runs"),
            balls=Sum("balls"),
            fours=Sum("fours"),
            sixes=Sum("sixes"),
            highest_score=Max("highest_score"),
            highest_match_runs=Max("runs"),
        ).order_by("-total_runs", "player__name")[:limit]
        rows = list(rows)
        player_ids = [row["player_id"] for row in rows]
        dismissals = {}
        if player_ids:
            rollups = PlayerSeasonPerformance.objects.filter(
                player_id__in=player_ids
            )
            selected_seasons = performances.values_list(
                "match__season", flat=True
            ).distinct()
            rollups = rollups.filter(season__in=selected_seasons)
            dismissals = {
                row["player_id"]: row["dismissals"] or 0
                for row in rollups.values("player_id").annotate(
                    dismissals=Sum("dismissals")
                )
            }
        output = []
        for row in rows:
            runs, balls = row["total_runs"] or 0, row["balls"] or 0
            outs = dismissals.get(row["player_id"], 0)
            output.append(
                {
                    "player_id": row["player_id"],
                    "player": row["player__name"],
                    "team": row["player__current_team__name"],
                    "appearances": row["appearances"],
                    "runs": runs,
                    "average": _number(runs / outs if outs else runs),
                    "strike_rate": _number(runs * 100 / balls if balls else 0),
                    "fours": row["fours"] or 0,
                    "sixes": row["sixes"] or 0,
                    "boundaries": (row["fours"] or 0) + (row["sixes"] or 0),
                    "highest_score": max(
                        row["highest_score"] or 0,
                        row["highest_match_runs"] or 0,
                    ),
                    "average_is_estimate": not bool(outs),
                }
            )
        return output

    rows = performances.values(
        "player_id", "player__name", "player__current_team__name"
    ).annotate(
        appearances=Count("id", distinct=True),
        wickets=Sum("wickets"),
        bowling_balls=Sum("bowling_balls"),
        runs_conceded=Sum("runs_conceded"),
    ).order_by("-wickets", "runs_conceded", "player__name")[:limit]
    return [
        {
            "player_id": row["player_id"],
            "player": row["player__name"],
            "team": row["player__current_team__name"],
            "appearances": row["appearances"],
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


def _player_output_points(performances, limit=40):
    """Return one real batting/bowling output point per qualified player."""

    rows = performances.filter(runs__gt=0, wickets__gt=0).values(
        "player_id", "player__name", "player__current_team__name"
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
                "player": row["player__name"],
                "team": row["player__current_team__name"],
                "matches": row["matches"],
                "x": row["runs"] or 0,
                "y": row["wickets"] or 0,
            }
            for row in rows
        ]
    }


def _team_metric(team, matches):
    team_matches = matches.filter(Q(team1=team) | Q(team2=team))
    played = team_matches.count()
    wins = team_matches.filter(winner=team).count()
    completed = team_matches.exclude(winner__isnull=True).count()
    innings = MatchInnings.objects.filter(
        match__in=team_matches, team=team
    ).aggregate(
        # Keep the aggregate alias distinct from the model field.  Django
        # resolves annotation names while constructing subsequent aggregates;
        # naming the Sum ``runs`` would make ``Avg("runs")`` refer to that
        # aggregate and fail with ``FieldError``.
        total_runs=Sum("runs"),
        average_score=Avg("runs"),
        wickets=Sum("wickets"),
        innings=Count("id"),
    )

    # PlayerPerformance has no team foreign key.  Attribute exact match-level
    # rows through the player's current team first, then use seasonal rollups
    # as a historical fallback.  A direct reverse relation from a performance
    # row to ``season_stats`` does not exist, so avoid that lookup entirely.
    team_performances = PlayerPerformance.objects.filter(match__in=team_matches)
    attributed_performances = team_performances.filter(player__current_team=team)
    if not attributed_performances.exists():
        attributed_player_ids = PlayerSeasonPerformance.objects.filter(
            team=team,
            season__in=team_matches.values_list("season", flat=True).distinct(),
        ).values("player_id")
        if attributed_player_ids.exists():
            attributed_performances = team_performances.filter(
                player_id__in=attributed_player_ids
            )
    player_totals = attributed_performances.aggregate(
        fours=Sum("fours"),
        sixes=Sum("sixes"),
        wickets=Sum("wickets"),
        catches=Sum("catches"),
    )
    top_source = (
        attributed_performances
        if attributed_performances.exists()
        else team_performances
    )
    top_batters = _top_players(top_source, limit=5)
    top_bowlers = _top_players(top_source, role="bowl", limit=5)
    fours = player_totals["fours"] or 0
    sixes = player_totals["sixes"] or 0
    return {
        **_serialize_team(team),
        "played": played,
        "matches": played,
        "wins": wins,
        "losses": completed - wins,
        "no_results": played - completed,
        "win_percentage": _percentage(wins, played),
        "win_rate": _percentage(wins, played),
        "runs": innings["total_runs"] or 0,
        "innings": innings["innings"] or 0,
        "average_score": _number(innings["average_score"]),
        "wickets": innings["wickets"] or 0,
        "wickets_per_innings": _number(
            (innings["wickets"] or 0) / innings["innings"] if innings["innings"] else 0
        ),
        "fours": fours,
        "sixes": sixes,
        "boundaries": fours + sixes,
        "catches": player_totals["catches"] or 0,
        "top_batters": top_batters,
        "top_bowlers": top_bowlers,
    }


def _participant_ids(matches, selected_team=None):
    ids = set()
    for first, second in matches.values_list("team1_id", "team2_id").iterator():
        ids.add(first)
        ids.add(second)
    if selected_team:
        ids.add(selected_team.pk)
    return sorted(ids)


def team_statistics(team=None, season=None, venue=None):
    matches = _match_queryset(season=season, team=team, venue=venue)
    participants = _participant_ids(matches, team)
    teams = Team.objects.filter(id__in=participants)
    return {
        "filters": {
            "season": season,
            "team_id": team.pk if team else None,
            "venue_id": venue.pk if venue else None,
        },
        "count": len(participants),
        "teams": [_team_metric(item, matches) for item in teams],
        "empty": not matches.exists(),
    }


def _aggregate_player_performances(performances):
    totals = performances.aggregate(
        appearances=Count("id"),
        runs=Sum("runs"),
        balls=Sum("balls"),
        fours=Sum("fours"),
        sixes=Sum("sixes"),
        highest_score=Max("highest_score"),
        bowling_balls=Sum("bowling_balls"),
        wickets=Sum("wickets"),
        runs_conceded=Sum("runs_conceded"),
        catches=Sum("catches"),
    )
    return {
        "appearances": totals["appearances"] or 0,
        "batting_innings": totals["appearances"] or 0,
        "runs": totals["runs"] or 0,
        "balls": totals["balls"] or 0,
        "fours": totals["fours"] or 0,
        "sixes": totals["sixes"] or 0,
        "dismissals": None,
        "highest_score": totals["highest_score"] or 0,
        "bowling_innings": totals["appearances"] or 0,
        "bowling_balls": totals["bowling_balls"] or 0,
        "wickets": totals["wickets"] or 0,
        "runs_conceded": totals["runs_conceded"] or 0,
        "catches": totals["catches"] or 0,
    }


def _player_rollup(player, season=None, team=None, venue=None):
    rollups = PlayerSeasonPerformance.objects.filter(player=player)
    if season:
        rollups = rollups.filter(season=season)
    if team:
        rollups = rollups.filter(team=team)
    if venue:
        allowed_players = PlayerPerformance.objects.filter(
            match__venue=venue
        ).filter(
            Q(match__season=season) if season else Q(match__isnull=False)
        ).values("player_id")
        rollups = rollups.filter(player_id__in=allowed_players)

    aggregate = rollups.aggregate(
        appearances=Sum("appearances"),
        batting_innings=Sum("batting_innings"),
        runs=Sum("runs"),
        balls=Sum("balls"),
        fours=Sum("fours"),
        sixes=Sum("sixes"),
        dismissals=Sum("dismissals"),
        highest_score=Max("highest_score"),
        bowling_innings=Sum("bowling_innings"),
        bowling_balls=Sum("bowling_balls"),
        wickets=Sum("wickets"),
        runs_conceded=Sum("runs_conceded"),
        catches=Sum("catches"),
    )
    rollup_teams = sorted(
        {
            row
            for row in rollups.values_list("team__name", flat=True)
            if row
        }
    )
    used_fallback = False

    # Per-match records are the exact source for a venue slice and also serve
    # as a fallback when optional seasonal rollups have not yet been built.
    if venue or not rollups.exists():
        performances = PlayerPerformance.objects.filter(player=player)
        if season:
            performances = performances.filter(match__season=season)
        if team:
            performances = performances.filter(
                Q(match__team1=team) | Q(match__team2=team)
            )
        if venue:
            performances = performances.filter(match__venue=venue)
        performance_aggregate = _aggregate_player_performances(performances)
        if performance_aggregate["appearances"]:
            used_fallback = True
            aggregate = performance_aggregate

    match_score_queryset = PlayerPerformance.objects.filter(player=player)
    if season:
        match_score_queryset = match_score_queryset.filter(match__season=season)
    if team:
        match_score_queryset = match_score_queryset.filter(
            Q(match__team1=team) | Q(match__team2=team)
        )
    if venue:
        match_score_queryset = match_score_queryset.filter(match__venue=venue)
    highest_match_runs = match_score_queryset.aggregate(
        value=Max("runs")
    )["value"] or 0

    runs = aggregate["runs"] or 0
    balls = aggregate["balls"] or 0
    dismissals = aggregate["dismissals"]
    wickets = aggregate["wickets"] or 0
    bowling_balls = aggregate["bowling_balls"] or 0
    conceded = aggregate["runs_conceded"] or 0
    fours = aggregate["fours"] or 0
    sixes = aggregate["sixes"] or 0
    return {
        **_serialize_player(player),
        "teams": rollup_teams,
        "appearances": aggregate["appearances"] or 0,
        "batting_innings": aggregate["batting_innings"] or 0,
        "runs": runs,
        "balls": balls,
        "average": _number(runs / dismissals if dismissals else runs),
        "batting_average": _number(runs / dismissals if dismissals else runs),
        "strike_rate": _number(runs * 100 / balls if balls else 0),
        "fours": fours,
        "sixes": sixes,
        "boundaries": fours + sixes,
        "highest_score": max(
            aggregate["highest_score"] or 0, highest_match_runs
        ),
        "dismissals": dismissals,
        "bowling_innings": aggregate["bowling_innings"] or 0,
        "bowling_balls": bowling_balls,
        "overs": _number(bowling_balls / 6),
        "wickets": wickets,
        "bowling_average": _number(conceded / wickets if wickets else 0),
        "economy": _number(conceded * 6 / bowling_balls if bowling_balls else 0),
        "runs_conceded": conceded,
        "catches": aggregate["catches"] or 0,
        "average_is_estimate": dismissals is None,
        "source": "player_performance" if used_fallback else "player_season_performance",
    }


def _season_player_rows(player, season=None, team=None):
    rows = PlayerSeasonPerformance.objects.filter(player=player)
    if season:
        rows = rows.filter(season=season)
    if team:
        rows = rows.filter(team=team)
    return rows.values("season").annotate(
        appearances=Sum("appearances"),
        batting_innings=Sum("batting_innings"),
        runs=Sum("runs"),
        balls=Sum("balls"),
        fours=Sum("fours"),
        sixes=Sum("sixes"),
        dismissals=Sum("dismissals"),
        highest_score=Max("highest_score"),
        bowling_balls=Sum("bowling_balls"),
        wickets=Sum("wickets"),
        runs_conceded=Sum("runs_conceded"),
    ).order_by("season")


def _player_series(player, season=None, team=None, venue=None):
    rows = list(_season_player_rows(player, season=season, team=team))
    if venue or not rows:
        # Venue filters use per-match records to avoid including the same
        # player's statistics from other grounds in a season.  The same
        # per-match path is also the fallback when optional rollups are absent.
        allowed = PlayerPerformance.objects.filter(player=player)
        if venue:
            allowed = allowed.filter(match__venue=venue)
        if season:
            allowed = allowed.filter(match__season=season)
        if team:
            allowed = allowed.filter(
                Q(match__team1=team) | Q(match__team2=team)
            )
        rows = [
            {
                **row,
                "season": row["match__season"],
            }
            for row in allowed.values("match__season")
            .annotate(
                appearances=Count("id"),
                batting_innings=Count("id"),
                runs=Sum("runs"),
                balls=Sum("balls"),
                fours=Sum("fours"),
                sixes=Sum("sixes"),
                highest_score=Max("highest_score"),
                bowling_balls=Sum("bowling_balls"),
                wickets=Sum("wickets"),
                runs_conceded=Sum("runs_conceded"),
            )
            .order_by("match__season")
        ]
    if rows:
        score_performances = PlayerPerformance.objects.filter(player=player)
        if season:
            score_performances = score_performances.filter(match__season=season)
        if team:
            score_performances = score_performances.filter(
                Q(match__team1=team) | Q(match__team2=team)
            )
        if venue:
            score_performances = score_performances.filter(match__venue=venue)
        highest_by_season = {
            row["match__season"]: row["highest_match_runs"] or 0
            for row in score_performances.values("match__season").annotate(
                highest_match_runs=Max("runs")
            )
        }
        for row in rows:
            row["highest_score"] = max(
                row.get("highest_score") or 0,
                highest_by_season.get(row["season"], 0),
            )
    result = []
    for row in rows:
        runs, balls = row["runs"] or 0, row["balls"] or 0
        outs = row.get("dismissals")
        wickets, bowling_balls = row["wickets"] or 0, row["bowling_balls"] or 0
        conceded = row["runs_conceded"] or 0
        result.append(
            {
                "season": row["season"],
                "appearances": row["appearances"] or 0,
                "runs": runs,
                "average": _number(runs / outs if outs else runs),
                "strike_rate": _number(runs * 100 / balls if balls else 0),
                "fours": row["fours"] or 0,
                "sixes": row["sixes"] or 0,
                "highest_score": row["highest_score"] or 0,
                "wickets": wickets,
                "bowling_average": _number(
                    conceded / wickets if wickets else 0
                ),
                "economy": _number(
                    conceded * 6 / bowling_balls if bowling_balls else 0
                ),
            }
        )
    return result


def player_statistics(player=None, season=None, team=None, venue=None):
    performances = _performance_queryset(
        _match_queryset(season=season, team=team, venue=venue)
    )
    if player:
        performances = performances.filter(player=player)
        data = _player_rollup(player, season=season, team=team, venue=venue)
        data["series"] = _player_series(
            player, season=season, team=team, venue=venue
        )
        players = [data]
    else:
        players = _top_players(performances, limit=25)
        data = None
    return {
        "filters": {
            "season": season,
            "team_id": team.pk if team else None,
            "venue_id": venue.pk if venue else None,
            "player_id": player.pk if player else None,
        },
        "count": len(players),
        "player": data,
        "players": players,
        "empty": not performances.exists(),
    }


def compare_players(player_a, player_b, season=None, team=None, venue=None):
    if player_a.pk == player_b.pk:
        raise ValueError("Choose two different players to compare.")
    first = _player_rollup(player_a, season=season, team=team, venue=venue)
    second = _player_rollup(player_b, season=season, team=team, venue=venue)
    metrics = [
        "runs",
        "average",
        "strike_rate",
        "fours",
        "sixes",
        "highest_score",
        "wickets",
        "bowling_average",
        "economy",
        "catches",
    ]
    lower_is_better = {"bowling_average", "economy"}
    leaders = {}
    for metric in metrics:
        first_value = first.get(metric, 0)
        second_value = second.get(metric, 0)
        if first_value == second_value:
            leader, tie = first, True
        elif metric in lower_is_better:
            leader = first if first_value < second_value else second
            tie = False
        else:
            leader = first if first_value > second_value else second
            tie = False
        leaders[metric] = {
            "player_id": leader["id"],
            "player": leader["name"],
            "tie": tie,
        }
    return {
        "filters": {
            "season": season,
            "team_id": team.pk if team else None,
            "venue_id": venue.pk if venue else None,
            "player_a_id": player_a.pk,
            "player_b_id": player_b.pk,
        },
        "player_a": first,
        "player_b": second,
        "player_a_series": _player_series(
            player_a, season=season, team=team, venue=venue
        ),
        "player_b_series": _player_series(
            player_b, season=season, team=team, venue=venue
        ),
        "leaders": leaders,
        "empty": not (first["appearances"] or second["appearances"]),
    }


def season_statistics(season=None, team=None, venue=None):
    season = _season_int(season)
    base = _match_queryset(season=season, team=team, venue=venue)
    seasons = sorted(
        set(base.values_list("season", flat=True).distinct().order_by("season"))
    )
    if season:
        seasons = [season] if season in seasons else []
    rows = []
    for number in seasons:
        matches = base.filter(season=number)
        innings = _innings_queryset(matches)
        performances = _performance_queryset(matches)
        aggregate = matches.aggregate(
            matches=Count("id"),
            completed=Count("id", filter=Q(winner__isnull=False)),
        )
        score = innings.aggregate(total_runs=Sum("runs"), average_score=Avg("runs"))
        player_totals = performances.aggregate(
            fours=Sum("fours"), sixes=Sum("sixes"), wickets=Sum("wickets")
        )
        fours, sixes = player_totals["fours"] or 0, player_totals["sixes"] or 0
        rows.append(
            {
                "season": number,
                "matches": aggregate["matches"],
                "completed": aggregate["completed"],
                "teams": len(_participant_ids(matches)),
                "total_runs": score["total_runs"] or 0,
                "average_team_score": _number(score["average_score"]),
                "boundaries": fours + sixes,
                "fours": fours,
                "sixes": sixes,
                "wickets": player_totals["wickets"] or 0,
            }
        )
    return {
        "filters": {
            "season": season,
            "team_id": team.pk if team else None,
            "venue_id": venue.pk if venue else None,
        },
        "count": len(rows),
        "seasons": rows,
        "available_seasons": list(
            Match.objects.values_list("season", flat=True).distinct().order_by("season")
        ),
        "empty": not rows,
    }


def _venue_metric(venue, matches):
    matches = matches.filter(venue=venue)
    first_innings = list(
        _innings_queryset(matches)
        .filter(innings=1)
        .values_list("runs", flat=True)
    )
    totals_by_match = list(
        _innings_queryset(matches)
        .values("match_id")
        .annotate(runs=Sum("runs"))
        .values_list("runs", flat=True)
    )
    performances = _performance_queryset(matches)
    player_totals = performances.aggregate(
        fours=Sum("fours"), sixes=Sum("sixes"), wickets=Sum("wickets")
    )
    aggregate = matches.aggregate(
        matches=Count("id"),
        completed=Count("id", filter=Q(winner__isnull=False)),
    )
    toss = Counter(matches.values_list("toss_decision", flat=True))
    batting_first = Counter()
    chasing = Counter()
    first_teams = {
        row["match_id"]: row["team_id"]
        for row in _innings_queryset(matches)
        .filter(innings=1)
        .values("match_id", "team_id")
    }
    for match_id, winner_id in matches.exclude(winner__isnull=True).values_list(
        "id", "winner_id"
    ):
        if first_teams.get(match_id) == winner_id:
            batting_first[winner_id] += 1
        elif first_teams.get(match_id):
            chasing[winner_id] += 1
    fours, sixes = player_totals["fours"] or 0, player_totals["sixes"] or 0
    return {
        "venue_id": venue.pk,
        "venue": venue.name,
        "short_name": venue.short_name,
        "city": venue.city,
        "matches": aggregate["matches"] or 0,
        "completed": aggregate["completed"] or 0,
        "total_runs": sum(totals_by_match),
        "average_first_innings_score": _number(
            sum(first_innings) / len(first_innings) if first_innings else 0
        ),
        "average_match_runs": _number(
            sum(totals_by_match) / len(totals_by_match) if totals_by_match else 0
        ),
        "toss_bat": toss.get("bat", 0),
        "toss_field": toss.get("field", 0),
        "batting_first_wins": sum(batting_first.values()),
        "chasing_wins": sum(chasing.values()),
        "boundaries": fours + sixes,
        "fours": fours,
        "sixes": sixes,
        "wickets": player_totals["wickets"] or 0,
    }


def venue_statistics(venue=None, season=None, team=None):
    matches = _match_queryset(season=season, team=team, venue=venue)
    venue_ids = sorted({row for row in matches.values_list("venue_id", flat=True)})
    if venue:
        venue_ids = [venue.pk]
    venues = Venue.objects.filter(id__in=venue_ids)
    rows = [_venue_metric(item, matches) for item in venues]
    rows.sort(key=lambda row: (-row["matches"], row["venue"]))
    return {
        "filters": {
            "season": season,
            "team_id": team.pk if team else None,
            "venue_id": venue.pk if venue else None,
        },
        "count": len(rows),
        "venues": rows,
        "empty": not matches.exists(),
    }


def venue_analysis(venue=None, season=None, team=None):
    matches = _match_queryset(season=season, team=team, venue=venue)
    performances = _performance_queryset(matches)
    innings = _innings_queryset(matches)
    aggregate = matches.aggregate(
        matches=Count("id"),
        completed=Count("id", filter=Q(winner__isnull=False)),
    )
    first_scores = innings.filter(innings=1).aggregate(avg=Avg("runs"))
    total_scores = list(
        innings.values("match_id").annotate(runs=Sum("runs")).values_list("runs", flat=True)
    )
    data = venue_statistics(venue=venue, season=season, team=team)
    team_ids = _participant_ids(matches)
    season_rows = []
    for year in sorted(matches.values_list("season", flat=True).distinct()):
        year_matches = matches.filter(season=year)
        year_aggregate = year_matches.aggregate(
            matches=Count("id"),
            completed=Count("id", filter=Q(winner__isnull=False)),
        )
        year_scores = _innings_queryset(year_matches).filter(innings=1).aggregate(
            avg=Avg("runs")
        )
        season_rows.append(
            {
                "season": year,
                "matches": year_aggregate["matches"],
                "completed": year_aggregate["completed"],
                "average_first_innings_score": _number(year_scores["avg"]),
            }
        )
    return {
        "filters": {
            "season": season,
            "team_id": team.pk if team else None,
            "venue_id": venue.pk if venue else None,
        },
        "venue": _serialize_match_venue(venue) if venue else None,
        "summary": {
            "matches": aggregate["matches"] or 0,
            "completed": aggregate["completed"] or 0,
            "total_runs": sum(total_scores),
            "average_match_runs": _number(
                sum(total_scores) / len(total_scores) if total_scores else 0
            ),
            "average_first_innings_score": _number(first_scores["avg"]),
        },
        "metrics": data["venues"],
        "seasons": season_rows,
        "teams": [_team_metric(item, matches) for item in Team.objects.filter(id__in=team_ids)],
        "top_batters": _top_players(performances, limit=15),
        "top_bowlers": _top_players(performances, role="bowl", limit=15),
        "player_output": _player_output_points(performances),
        "recent_matches": [
            _serialize_match(match)
            for match in matches.select_related(
                "venue", "team1", "team2", "toss_winner", "winner"
            )[:15]
        ],
        "empty": not matches.exists(),
    }


def _serialize_match_venue(venue):
    return {
        "id": venue.pk,
        "name": venue.name,
        "short_name": venue.short_name,
        "city": venue.city,
    }


def _head_to_head_team(team, opponent, matches):
    team_matches = matches.filter(Q(team1=team) | Q(team2=team))
    wins = team_matches.filter(winner=team)
    losses = team_matches.filter(winner=opponent)
    innings = MatchInnings.objects.filter(match__in=team_matches, team=team).aggregate(
        # Use a separate alias for the sum; otherwise Avg("runs") resolves to
        # the Sum annotation on Django's current query compiler.
        total_runs=Sum("runs"),
        average=Avg("runs"),
        wickets=Sum("wickets"),
    )
    runs_margins = list(
        wins.filter(win_by_runs__gt=0).values_list("win_by_runs", flat=True)
    )
    wicket_margins = list(
        wins.filter(win_by_wickets__gt=0).values_list("win_by_wickets", flat=True)
    )
    return {
        **_serialize_team(team),
        "played": team_matches.count(),
        "wins": wins.count(),
        "losses": losses.count(),
        "no_results": team_matches.filter(winner__isnull=True).count(),
        "win_percentage": _percentage(wins.count(), team_matches.count()),
        "runs": innings["total_runs"] or 0,
        "average_score": _number(innings["average"]),
        "wickets": innings["wickets"] or 0,
        "toss_wins": team_matches.filter(toss_winner=team).count(),
        "average_run_margin": _number(
            sum(runs_margins) / len(runs_margins) if runs_margins else 0
        ),
        "average_wicket_margin": _number(
            sum(wicket_margins) / len(wicket_margins) if wicket_margins else 0
        ),
    }


def head_to_head(team_a, team_b, season=None, venue=None):
    if team_a.pk == team_b.pk:
        raise ValueError("Choose two different teams to compare.")
    matches = _match_queryset(season=season, venue=venue).filter(
        Q(team1=team_a, team2=team_b) | Q(team1=team_b, team2=team_a)
    )
    meeting_count = matches.count()
    a_wins = matches.filter(winner=team_a).count()
    b_wins = matches.filter(winner=team_b).count()
    years = list(
        matches.values("season")
        .annotate(
            matches=Count("id"),
            team_a_wins=Count("id", filter=Q(winner=team_a)),
            team_b_wins=Count("id", filter=Q(winner=team_b)),
            no_results=Count("id", filter=Q(winner__isnull=True)),
        )
        .order_by("season")
    )
    return {
        "filters": {
            "season": season,
            "venue_id": venue.pk if venue else None,
            "team_a_id": team_a.pk,
            "team_b_id": team_b.pk,
        },
        "team_a": _head_to_head_team(team_a, team_b, matches),
        "team_b": _head_to_head_team(team_b, team_a, matches),
        "summary": {
            "meetings": meeting_count,
            "team_a_wins": a_wins,
            "team_b_wins": b_wins,
            "no_results": meeting_count - a_wins - b_wins,
            "team_a_win_percentage": _percentage(a_wins, meeting_count),
            "team_b_win_percentage": _percentage(b_wins, meeting_count),
        },
        "by_season": years,
        "matches": [
            _serialize_match(match)
            for match in matches.select_related(
                "venue", "team1", "team2", "toss_winner", "winner"
            )
        ],
        "empty": meeting_count == 0,
    }


def player_trend(player, start_season=None, end_season=None, team=None, venue=None):
    rows = list(
        _season_player_rows(
            player, season=None, team=team
        )
    )
    if venue:
        # The per-match path below keeps venue trends exact rather than
        # including the player's other matches from the same season.
        rows = []
    if not rows:
        # A database containing only normalized per-match performance records
        # can still provide a useful season trend.
        fallback = PlayerPerformance.objects.filter(player=player)
        if team:
            fallback = fallback.filter(
                Q(match__team1=team) | Q(match__team2=team)
            )
        if venue:
            fallback = fallback.filter(match__venue=venue)
        rows = [
            {
                **row,
                "season": row["match__season"],
                "dismissals": None,
            }
            for row in fallback.values("match__season")
            .annotate(
                appearances=Count("id"),
                batting_innings=Count("id"),
                runs=Sum("runs"),
                balls=Sum("balls"),
                fours=Sum("fours"),
                sixes=Sum("sixes"),
                highest_score=Max("highest_score"),
                bowling_balls=Sum("bowling_balls"),
                wickets=Sum("wickets"),
                runs_conceded=Sum("runs_conceded"),
            )
            .order_by("match__season")
        ]
    if start_season:
        rows = [row for row in rows if row["season"] >= start_season]
    if end_season:
        rows = [row for row in rows if row["season"] <= end_season]

    score_performances = PlayerPerformance.objects.filter(player=player)
    if team:
        score_performances = score_performances.filter(
            Q(match__team1=team) | Q(match__team2=team)
        )
    if venue:
        score_performances = score_performances.filter(match__venue=venue)
    if start_season:
        score_performances = score_performances.filter(
            match__season__gte=start_season
        )
    if end_season:
        score_performances = score_performances.filter(
            match__season__lte=end_season
        )
    highest_by_season = {
        row["match__season"]: row["highest_match_runs"] or 0
        for row in score_performances.values("match__season").annotate(
            highest_match_runs=Max("runs")
        )
    }
    for row in rows:
        row["highest_score"] = max(
            row.get("highest_score") or 0,
            highest_by_season.get(row["season"], 0),
        )

    points = []
    cumulative_runs = cumulative_wickets = 0
    for row in rows:
        runs, outs = row["runs"] or 0, row["dismissals"] or 0
        balls, wickets = row["balls"] or 0, row["wickets"] or 0
        cumulative_runs += runs
        cumulative_wickets += wickets
        points.append(
            {
                "season": row["season"],
                "appearances": row["appearances"] or 0,
                "runs": runs,
                "average": _number(runs / outs if outs else runs),
                "strike_rate": _number(runs * 100 / balls if balls else 0),
                "fours": row["fours"] or 0,
                "sixes": row["sixes"] or 0,
                "boundaries": (row["fours"] or 0) + (row["sixes"] or 0),
                "highest_score": row["highest_score"] or 0,
                "wickets": wickets,
                "bowling_average": _number(
                    (row["runs_conceded"] or 0) / wickets if wickets else 0
                ),
                "economy": _number(
                    (row["runs_conceded"] or 0) * 6 / (row["bowling_balls"] or 0)
                    if row["bowling_balls"]
                    else 0
                ),
                "cumulative_runs": cumulative_runs,
                "cumulative_wickets": cumulative_wickets,
            }
        )
    recent_queryset = PlayerPerformance.objects.filter(player=player)
    if team:
        recent_queryset = recent_queryset.filter(
            Q(match__team1=team) | Q(match__team2=team)
        )
    if venue:
        recent_queryset = recent_queryset.filter(match__venue=venue)
    if start_season:
        recent_queryset = recent_queryset.filter(match__season__gte=start_season)
    if end_season:
        recent_queryset = recent_queryset.filter(match__season__lte=end_season)
    recent = list(
        recent_queryset.select_related("match").order_by("-match__date")[:10]
    )
    return {
        "filters": {
            "player_id": player.pk,
            "team_id": team.pk if team else None,
            "venue_id": venue.pk if venue else None,
            "start_season": start_season,
            "end_season": end_season,
        },
        "player": _serialize_player(player),
        "points": points,
        "recent_matches": [
            {
                "match_id": item.match_id,
                "date": item.match.date.isoformat(),
                "season": item.season,
                "opponent": None,
                "runs": item.runs,
                "balls": item.balls,
                "fours": item.fours,
                "sixes": item.sixes,
                "highest_score": max(item.highest_score, item.runs),
                "wickets": item.wickets,
                "bowling_balls": item.bowling_balls,
                "runs_conceded": item.runs_conceded,
            }
            for item in recent
        ],
        "empty": not points and not recent,
    }


def analytics_overview(season=None, team=None, venue=None):
    season = _season_int(season)
    matches = _match_queryset(season=season, team=team, venue=venue)
    innings = _innings_queryset(matches)
    performances = _performance_queryset(matches)
    aggregate = matches.aggregate(
        matches=Count("id"),
        completed=Count("id", filter=Q(winner__isnull=False)),
    )
    score = innings.aggregate(total_runs=Sum("runs"), average=Avg("runs"))
    player_totals = performances.aggregate(
        fours=Sum("fours"), sixes=Sum("sixes"), wickets=Sum("wickets")
    )
    participants = _participant_ids(matches, team)
    fours, sixes = player_totals["fours"] or 0, player_totals["sixes"] or 0
    return {
        "filters": {
            "season": season,
            "team_id": team.pk if team else None,
            "venue_id": venue.pk if venue else None,
        },
        "summary": {
            "matches": aggregate["matches"] or 0,
            "completed_matches": aggregate["completed"] or 0,
            "completed": aggregate["completed"] or 0,
            "teams": len(participants),
            "total_teams": len(participants),
            "venues": matches.values("venue_id").distinct().count(),
            "total_venues": matches.values("venue_id").distinct().count(),
            "total_runs": score["total_runs"] or 0,
            "average_team_score": _number(score["average"]),
            "boundaries": fours + sixes,
            "wickets": player_totals["wickets"] or 0,
        },
        "standings": [_team_metric(item, matches) for item in Team.objects.filter(id__in=participants)],
        "seasons": season_statistics(season=season, team=team, venue=venue)["seasons"],
        "venues": venue_statistics(venue=venue, season=season, team=team)["venues"],
        "top_batters": _top_players(performances, limit=15),
        "top_bowlers": _top_players(performances, role="bowl", limit=15),
        "player_output": _player_output_points(performances),
        "recent_matches": [
            _serialize_match(match)
            for match in matches.select_related(
                "venue", "team1", "team2", "toss_winner", "winner"
            )[:10]
        ],
        "empty": not matches.exists(),
        "generated_at": timezone.now().isoformat(),
    }


def analytics_data(
    season=None,
    team=None,
    venue=None,
    team_a=None,
    team_b=None,
    player=None,
    player_a=None,
    player_b=None,
    start_season=None,
    end_season=None,
):
    season = _season_int(season)
    start_season = _season_int(start_season)
    end_season = _season_int(end_season)
    data = {
        "filters": {
            "season": season,
            "team_id": team.pk if team else None,
            "venue_id": venue.pk if venue else None,
            "team_a_id": team_a.pk if team_a else None,
            "team_b_id": team_b.pk if team_b else None,
            "player_id": player.pk if player else None,
            "player_a_id": player_a.pk if player_a else None,
            "player_b_id": player_b.pk if player_b else None,
            "start_season": start_season,
            "end_season": end_season,
        },
        "overview": analytics_overview(season=season, team=team, venue=venue),
        "season_stats": season_statistics(season=season, team=team, venue=venue),
        "team_stats": team_statistics(team=team, season=season, venue=venue),
        "player_stats": player_statistics(
            player=player, season=season, team=team, venue=venue
        ),
        "venue_stats": venue_statistics(
            venue=venue, season=season, team=team
        ),
    }
    if team_a and team_b and team_a.pk != team_b.pk:
        data["team_comparison"] = head_to_head(
            team_a, team_b, season=season, venue=venue
        )
    if player_a and player_b and player_a.pk != player_b.pk:
        data["player_comparison"] = compare_players(
            player_a, player_b, season=season, team=team, venue=venue
        )
    if venue:
        data["venue_analysis"] = venue_analysis(
            venue=venue, season=season, team=team
        )
    if player:
        data["player_trend"] = player_trend(
            player,
            start_season=start_season,
            end_season=end_season,
            team=team,
            venue=venue,
        )
    data["generated_at"] = timezone.now().isoformat()
    return data


# Stable service-layer aliases make the public functions convenient to use
# from management commands, tests, and future API versions.
get_team_statistics = team_statistics
get_player_statistics = player_statistics
get_season_statistics = season_statistics
get_venue_statistics = venue_statistics
get_team_comparison = head_to_head
get_player_comparison = compare_players
get_venue_analysis = venue_analysis
get_head_to_head = head_to_head
get_player_trend = player_trend
get_analytics_overview = analytics_overview
get_analytics_data = analytics_data
get_team_stats = team_statistics
get_player_stats = player_statistics
get_season_stats = season_statistics
get_venue_stats = venue_statistics
team_stats = team_statistics
player_stats = player_statistics
season_stats = season_statistics
venue_stats = venue_statistics


def _cache_timeout():
    try:
        return max(0, int(getattr(settings, "DATA_ANALYTICS_CACHE_SECONDS", 300)))
    except (TypeError, ValueError):
        return 300


def _database_signature():
    try:
        values = [
            Match.objects.aggregate(count=Count("id"), latest=Max("updated_at")),
            MatchInnings.objects.aggregate(count=Count("id")),
            PlayerPerformance.objects.aggregate(count=Count("id")),
            PlayerSeasonPerformance.objects.aggregate(count=Count("id")),
        ]
        return json.dumps(values, sort_keys=True, default=str)
    except DatabaseError:
        return ""


def cached_analytics(kind, parameters, builder):
    """Cache an analytics result using filters plus cheap table signatures."""
    signature = _database_signature()
    material = json.dumps(
        {"kind": kind, "parameters": parameters, "signature": signature},
        sort_keys=True,
        default=str,
    )
    key = f"analytics:{kind}:{sha256(material.encode()).hexdigest()}"
    timeout = _cache_timeout()
    if timeout and signature:
        cached = cache.get(key)
        if cached is not None:
            return cached
    result = builder()
    if timeout and signature:
        cache.set(key, result, timeout)
    return result
