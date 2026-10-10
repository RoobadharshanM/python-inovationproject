"""Offline, database-driven training jobs for the prediction application.

This module is intentionally separate from request handling.  The web app only
loads artifacts produced here; it never calls a training function.  Both jobs
read normalized Django ORM data, construct features using information available
before the match/innings, evaluate on a chronological holdout when possible,
and atomically publish a model, preprocessor, and JSON metadata file.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from collections import defaultdict
from decimal import Decimal
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .feature_engineering import (
    SCORE_CATEGORICAL_FEATURES,
    SCORE_FEATURE_NAMES,
    OUTCOME_CATEGORICAL_FEATURES,
    OUTCOME_FEATURE_NAMES,
    make_preprocessor,
)


class TrainingDataError(RuntimeError):
    """Raised when the database does not contain enough usable training data."""


@dataclass
class TrainingDataset:
    kind: str
    rows: list[dict[str, Any]]
    targets: list[float | int | str]
    dates: list[date]
    row_ids: list[Any]
    # Optional grouping key used to keep all innings from one match together
    # in a chronological score holdout.  It remains optional for small callers
    # that construct a row-level dataset directly.
    group_ids: list[Any] | None = None


# ---------------------------------------------------------------------------
# Safe numeric helpers


def _mean(values: Iterable[float]) -> float | None:
    values = [float(value) for value in values if value is not None]
    return sum(values) / len(values) if values else None


def _rate(runs: float, balls: float, multiplier: float = 6.0) -> float | None:
    if balls <= 0:
        return None
    return runs / balls * multiplier


def _finite(value: Any) -> float | None:
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _json_safe(value: Any) -> Any:
    """Convert numpy/scalar/path values to strict JSON-compatible values."""

    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Decimal):
        return float(value) if value.is_finite() else None
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "item"):
        try:
            return _json_safe(value.item())
        except (TypeError, ValueError):
            pass
    if hasattr(value, "tolist") and callable(value.tolist):
        try:
            return _json_safe(value.tolist())
        except (TypeError, ValueError):
            pass
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def _metric_dict(values: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _json_safe(value) for key, value in values.items()}


# ---------------------------------------------------------------------------
# Leakage-safe historical feature construction


def _team_label(team: Any) -> str:
    return str(team.name) if team is not None else "unknown"


def _blank_team_stats() -> dict[str, Any]:
    return {
        "matches": 0,
        "innings": 0,
        "runs": 0,
        "wickets": 0,
        "bowling_wickets": 0,
        "balls": 0,
        "runs_allowed": 0,
        "wins": 0,
    }


def _blank_player_stats() -> dict[str, float]:
    return {
        "appearances": 0.0,
        "runs": 0.0,
        "balls": 0.0,
        "wickets": 0.0,
        "bowling_balls": 0.0,
        "runs_conceded": 0.0,
    }


def _average(values: list[float]) -> float | None:
    return _mean(values)


def _team_features(stats: dict[str, Any], role: str) -> dict[str, Any]:
    """Convert cumulative pre-match team statistics into model features."""

    matches = stats.get("matches", 0)
    innings = stats.get("innings", 0)
    if role == "batting":
        runs = stats.get("runs", 0)
        wickets = stats.get("wickets", 0)
        balls = stats.get("balls", 0)
        return {
            "matches": matches,
            "avg_runs": runs / innings if innings else None,
            "avg_wickets": wickets / innings if innings else None,
            "run_rate": _rate(runs, balls),
        }
    runs_allowed = stats.get("runs_allowed", 0)
    wickets = stats.get("bowling_wickets", stats.get("wickets", 0))
    return {
        "matches": matches,
        "avg_runs": runs_allowed / innings if innings else None,
        "avg_wickets": wickets / innings if innings else None,
        "run_rate": _rate(runs_allowed, stats.get("balls", 0)),
    }


def _roster_features(stats: dict[str, dict[str, float]]) -> dict[str, Any]:
    players = list(stats.values())
    if not players:
        return {
            "players": 0,
            "avg_runs": None,
            "avg_strike_rate": None,
            "avg_wickets": None,
            "avg_economy": None,
        }
    appearances = [item.get("appearances", 0) for item in players]
    avg_runs = [
        item.get("runs", 0) / item["appearances"]
        for item in players
        if item.get("appearances", 0) > 0
    ]
    strike_rates = [
        item.get("runs", 0) * 100 / item["balls"]
        for item in players
        if item.get("balls", 0) > 0
    ]
    avg_wickets = [
        item.get("wickets", 0) / item["appearances"]
        for item in players
        if item.get("appearances", 0) > 0
    ]
    economy = [
        item.get("runs_conceded", 0) * 6 / item["bowling_balls"]
        for item in players
        if item.get("bowling_balls", 0) > 0
    ]
    return {
        "players": len(players),
        "appearances": sum(appearances),
        "avg_runs": _average(avg_runs),
        "avg_strike_rate": _average(strike_rates),
        "avg_wickets": _average(avg_wickets),
        "avg_economy": _average(economy),
    }


def _load_player_performances() -> tuple[
    dict[int, list[Any]],
    dict[int, list[tuple[int | None, int | None, int]]],
    dict[int, int | None],
]:
    """Load player history in a way that also works before player migrations.

    Player information is supplementary: a project may have imported teams and
    innings before importing player performances.  In that case the match and
    innings features remain usable rather than causing an opaque training crash.
    """

    try:
        from django.db import OperationalError

        from players.models import Player, PlayerPerformance, PlayerTeam
    except Exception:  # pragma: no cover - only if the players app is absent
        return {}, {}, {}

    by_match: dict[int, list[Any]] = defaultdict(list)
    affiliation_ranges: dict[int, list[tuple[int | None, int | None, int]]] = defaultdict(list)
    player_team: dict[int, int | None] = {}
    try:
        for player in Player.objects.all().only("id", "current_team_id"):
            player_team[player.id] = player.current_team_id
        for affiliation in PlayerTeam.objects.all().only(
            "player_id", "team_id", "first_season", "last_season"
        ):
            # Keep the full inclusive range.  Looking only at the first/last
            # season would incorrectly use a player's current team for seasons
            # in between a transfer.
            affiliation_ranges[affiliation.player_id].append(
                (affiliation.first_season, affiliation.last_season, affiliation.team_id)
            )
        for performance in PlayerPerformance.objects.select_related("match").all():
            by_match[performance.match_id].append(performance)
    except OperationalError:
        # The caller will still receive a useful team/innings-only model.  A
        # warning is included in the eventual metadata by the training job.
        return defaultdict(list), affiliation_ranges, player_team
    except Exception:
        # Do not hide programming errors in a production import, but avoid a
        # partially imported optional data source making the core job unusable.
        return defaultdict(list), affiliation_ranges, player_team

    return by_match, affiliation_ranges, player_team


def _performance_team(
    performance: Any,
    season: int,
    affiliations: dict[int, list[tuple[int | None, int | None, int]]],
    current_teams: dict[int, int | None],
) -> int | None:
    # PlayerTeam ranges are historical and therefore safe for the matching
    # match.  The current-team fallback supports small hand-created fixtures
    # that have no affiliation rows; the imported production dataset does use
    # affiliation ranges.
    for first_season, last_season, team_id in affiliations.get(performance.player_id, []):
        if (first_season is None or season >= first_season) and (
            last_season is None or season <= last_season
        ):
            return team_id
    return current_teams.get(performance.player_id)


def _score_delivery_file() -> Path:
    """Locate the delivery archive used to create prediction-time snapshots."""

    from django.conf import settings

    base_dir = Path(getattr(settings, "BASE_DIR", Path.cwd()))
    raw_dir = Path(getattr(settings, "RAW_DATA_DIRECTORY", base_dir / "DataSets"))
    candidates = [
        raw_dir / "deliveries.csv",
        base_dir / "DataSets" / "deliveries.csv",
        base_dir / "data" / "raw" / "deliveries.csv",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise TrainingDataError(
        "Score training requires deliveries.csv. Import the Kaggle IPL CSV files "
        "before training the live-state score model."
    )


def _build_live_score_dataset() -> TrainingDataset:
    """Build leakage-safe checkpoints from real delivery data and the database.

    The normalized database supplies match context and the authoritative final
    innings total.  The delivery archive supplies only the state available at
    each over checkpoint; the target is never placed in a feature row.
    """

    import pandas as pd
    from django.conf import settings
    from django.db import DatabaseError

    from matches.models import MatchInnings

    source_file = _score_delivery_file()
    try:
        deliveries = pd.read_csv(source_file, encoding="utf-8-sig", low_memory=False)
    except (OSError, UnicodeError, ValueError, pd.errors.ParserError) as exc:
        raise TrainingDataError(f"Could not read {source_file}: {exc}") from exc

    # The supplied Kaggle file uses these names.  Case/punctuation-insensitive
    # aliases make the offline job usable with common exported variants too.
    def column_key(value: Any) -> str:
        return "".join(character for character in str(value).casefold() if character.isalnum())

    aliases = {
        "match_id": ("match_id", "matchid", "id", "match_no", "game_id"),
        "inning": ("inning", "innings", "inning_no", "inning_number"),
        "over": ("over", "over_no", "over_number"),
        "ball": ("ball", "ball_no", "ball_number", "delivery"),
        "wide_runs": ("wide_runs", "wides", "wide"),
        "noball_runs": ("noball_runs", "no_ball_runs", "noballs"),
        "bye_runs": ("bye_runs", "byes", "bye"),
        "legbye_runs": ("legbye_runs", "leg_bye_runs", "legbyes"),
        "batsman_runs": ("batsman_runs", "batter_runs", "striker_runs", "runs_off_bat"),
        "total_runs": ("total_runs", "team_runs", "runs_total"),
        "extra_runs": ("extra_runs", "extras"),
        "player_dismissed": ("player_dismissed", "dismissed_player", "batsman_out"),
        "dismissal_kind": ("dismissal_kind", "dismissal_type", "wicket_type"),
        "is_super_over": ("is_super_over", "superover", "is_superover"),
    }
    available: dict[str, str] = {}
    for column in deliveries.columns:
        available.setdefault(column_key(column), column)
    rename: dict[str, str] = {}
    for canonical, variants in aliases.items():
        for variant in variants:
            source = available.get(column_key(variant))
            if source is not None:
                rename[source] = canonical
                break
    deliveries = deliveries.rename(columns=rename)
    required = {"match_id", "inning", "over", "ball"}
    missing = sorted(required.difference(deliveries.columns))
    if missing:
        raise TrainingDataError(
            "deliveries.csv is missing required live-training columns: "
            + ", ".join(missing)
        )

    deliveries = deliveries.copy()
    for column in ("match_id", "inning", "over", "ball"):
        deliveries[column] = pd.to_numeric(deliveries[column], errors="coerce")
    deliveries = deliveries[
        deliveries["match_id"].notna()
        & deliveries["inning"].notna()
        & deliveries["over"].notna()
        & deliveries["ball"].notna()
    ].copy()
    deliveries["inning"] = deliveries["inning"].astype(int)
    deliveries = deliveries[deliveries["inning"].isin([1, 2])]

    for column, default in (
        ("wide_runs", 0),
        ("noball_runs", 0),
        ("bye_runs", 0),
        ("legbye_runs", 0),
        ("batsman_runs", 0),
        ("extra_runs", 0),
    ):
        if column not in deliveries:
            deliveries[column] = default
        deliveries[column] = pd.to_numeric(deliveries[column], errors="coerce").fillna(default)
    if "total_runs" not in deliveries:
        deliveries["total_runs"] = deliveries["batsman_runs"] + deliveries["extra_runs"]
    deliveries["total_runs"] = pd.to_numeric(deliveries["total_runs"], errors="coerce").fillna(0)
    if "is_super_over" not in deliveries:
        deliveries["is_super_over"] = 0
    deliveries["is_super_over"] = pd.to_numeric(
        deliveries["is_super_over"], errors="coerce"
    ).fillna(0).astype(int)
    deliveries = deliveries[deliveries["is_super_over"] == 0].copy()
    if "player_dismissed" not in deliveries:
        deliveries["player_dismissed"] = ""
    if "dismissal_kind" not in deliveries:
        deliveries["dismissal_kind"] = ""
    deliveries["player_dismissed"] = deliveries["player_dismissed"].fillna("").astype(str)
    deliveries["dismissal_kind"] = deliveries["dismissal_kind"].fillna("").astype(str)
    deliveries["legal_ball"] = (
        deliveries["wide_runs"].eq(0) & deliveries["noball_runs"].eq(0)
    ).astype(int)
    dismissal_key = deliveries["dismissal_kind"].str.casefold()
    non_wickets = {"retired hurt", "retired ill", "obstructing the field"}
    deliveries["valid_wicket"] = (
        deliveries["player_dismissed"].ne("")
        & ~deliveries["player_dismissed"].str.casefold().isin({"nan", "none", "null"})
        & ~dismissal_key.isin(non_wickets)
    ).astype(int)

    # The final score and match context always come from the normalized DB.
    innings_lookup: dict[tuple[int, int], dict[str, Any]] = {}
    try:
        queryset = MatchInnings.objects.select_related(
            "match", "team", "match__team1", "match__team2", "match__venue", "match__toss_winner"
        ).order_by("match__date", "match_id", "innings")
        for inning in queryset:
            match = inning.match
            if match.external_id is None or inning.innings not in (1, 2):
                continue
            if inning.team_id not in (match.team1_id, match.team2_id):
                continue
            other_team = match.team2 if inning.team_id == match.team1_id else match.team1
            innings_lookup[(int(match.external_id), int(inning.innings))] = {
                "match_pk": match.pk,
                "batting_team": inning.team.name,
                "bowling_team": other_team.name,
                "venue": match.venue.name,
                "season": str(match.season),
                "toss_winner": match.toss_winner.name if match.toss_winner_id else "unknown",
                "toss_decision": match.toss_decision or "unknown",
                "match_type": match.match_type or "league",
                "date": match.date,
                "target": match.target_runs if inning.innings == 2 else None,
                "final_score": int(inning.runs),
            }
    except DatabaseError as exc:
        raise TrainingDataError(
            "Could not read MatchInnings rows. Apply migrations and import normalized data first."
        ) from exc

    rows: list[dict[str, Any]] = []
    targets: list[int] = []
    dates: list[date] = []
    row_ids: list[str] = []
    group_ids: list[int] = []
    for (match_id, inning_number), group in deliveries.groupby(["match_id", "inning"], sort=False):
        key = (int(match_id), int(inning_number))
        context = innings_lookup.get(key)
        if context is None:
            continue
        group = group.sort_values(["over", "ball"])
        maximum_over = int(group["over"].max())
        if maximum_over <= 0:
            continue
        # Do not train on the completed innings checkpoint: at that point the
        # cumulative score is already the regression target.  Every retained
        # row is a genuine pre-final prediction-time state.
        for checkpoint in range(0, maximum_over):
            state = group[group["over"] <= checkpoint]
            balls = int(state["legal_ball"].sum())
            current_score = int(state["total_runs"].sum())
            wickets = int(state["valid_wicket"].sum())
            recent_start = max(1, checkpoint - 4)
            recent = state[state["over"] >= recent_start] if checkpoint else state.iloc[0:0]
            run_rate = current_score * 6 / balls if balls else 0.0
            row = {
                "batting_team": context["batting_team"],
                "bowling_team": context["bowling_team"],
                "venue": context["venue"],
                "toss_winner": context["toss_winner"],
                "toss_decision": context["toss_decision"],
                "season": context["season"],
                "match_type": context["match_type"],
                "current_score": current_score,
                "overs_completed": round(balls / 6, 4),
                "wickets": wickets,
                "current_run_rate": round(run_rate, 4),
                "last_5_overs_runs": int(recent["total_runs"].sum()),
                "recent_wickets": int(recent["valid_wicket"].sum()),
                "target_runs_available": context["target"],
                "balls_remaining": max(0, 120 - balls),
                "wickets_remaining": max(0, 10 - wickets),
                "is_second_innings": int(inning_number == 2),
            }
            # Historical aggregate features are part of the shared schema but
            # are not reconstructed from future deliveries here; the fitted
            # preprocessor imputes missing values rather than fabricating them.
            for feature_name in SCORE_FEATURE_NAMES:
                row.setdefault(feature_name, None)
            rows.append(row)
            targets.append(int(context["final_score"]))
            dates.append(context["date"])
            row_ids.append(f"{int(match_id)}:{int(inning_number)}:{checkpoint}")
            group_ids.append(int(context["match_pk"]))

    if not rows:
        raise TrainingDataError(
            "No usable delivery checkpoints matched the imported MatchInnings rows."
        )

    # Keep an inspectable generated table when the normal project settings are
    # available.  It is outside the request path and is safe to regenerate.
    try:
        processed_directory = Path(settings.BASE_DIR) / "data" / "processed"
        processed_directory.mkdir(parents=True, exist_ok=True)
        processed_path = processed_directory / "score_training_snapshots.csv"
        temporary_path = processed_path.with_suffix(".tmp")
        pd.DataFrame(rows).assign(final_score=targets).to_csv(temporary_path, index=False)
        os.replace(temporary_path, processed_path)
    except OSError:
        # Artifact publication must remain possible on read-only deployments.
        pass

    return TrainingDataset("score", rows, targets, dates, row_ids, group_ids)


def _build_datasets() -> tuple[list[TrainingDataset], bool]:
    """Build score and outcome rows from matches, innings, and player data."""

    from matches.models import Match, MatchInnings  # noqa: F401 - explicit data contract

    try:
        matches = list(
            Match.objects.select_related("team1", "team2", "venue", "toss_winner", "winner")
            .prefetch_related("innings")
            .order_by("date", "external_id", "pk")
        )
    except Exception as exc:
        raise TrainingDataError(
            "Could not read Match rows. Apply migrations and import normalized match data first."
        ) from exc

    performances, affiliations, current_teams = _load_player_performances()
    player_warning = not performances

    team_stats: dict[int, dict[str, Any]] = defaultdict(_blank_team_stats)
    venue_stats: dict[int, dict[str, Any]] = defaultdict(
        lambda: {"matches": 0, "runs": 0}
    )
    h2h_stats: dict[tuple[int, int], dict[str, Any]] = defaultdict(
        lambda: {
            "matches": 0,
            "runs": 0,
            "team1_wins": 0,
            "team2_wins": 0,
        }
    )
    player_team_stats: dict[int, dict[int, dict[str, float]]] = defaultdict(dict)

    score_rows: list[dict[str, Any]] = []
    score_targets: list[int] = []
    score_dates: list[date] = []
    score_ids: list[Any] = []
    score_group_ids: list[Any] = []
    outcome_rows: list[dict[str, Any]] = []
    outcome_targets: list[str] = []
    outcome_dates: list[date] = []
    outcome_ids: list[Any] = []

    for match in matches:
        innings = sorted(list(match.innings.all()), key=lambda item: (item.innings, item.pk))
        if not innings:
            continue
        team1_id = match.team1_id
        team2_id = match.team2_id
        team1_name = _team_label(match.team1)
        team2_name = _team_label(match.team2)
        toss_winner = _team_label(match.toss_winner) if match.toss_winner_id else "unknown"
        toss_decision = match.toss_decision or "unknown"
        match_type = match.match_type or "league"
        venue_name = _team_label(match.venue)

        team1_prior = _team_features(team_stats[team1_id], "batting")
        team2_prior = _team_features(team_stats[team2_id], "batting")
        team1_bowling_prior = _team_features(team_stats[team1_id], "bowling")
        team2_bowling_prior = _team_features(team_stats[team2_id], "bowling")
        venue_prior = venue_stats[match.venue_id]
        h2h12 = h2h_stats[(team1_id, team2_id)]
        h2h21 = h2h_stats[(team2_id, team1_id)]
        # Both oriented keys are updated once per historical match, so the
        # feature is a genuine meeting count rather than an innings count.
        h2h_matches = h2h12["matches"]
        h2h_avg = (
            (h2h12["runs"] + h2h21["runs"]) / (2 * h2h_matches)
            if h2h_matches
            else None
        )
        h2h12_wins = h2h12["team1_wins"]
        h2h_win_rate = h2h12_wins / h2h_matches if h2h_matches else None

        team1_roster = _roster_features(player_team_stats[team1_id])
        team2_roster = _roster_features(player_team_stats[team2_id])

        for inning in innings:
            if inning.team_id not in (team1_id, team2_id):
                continue
            batting_id = inning.team_id
            bowling_id = team2_id if batting_id == team1_id else team1_id
            batting_name = team1_name if batting_id == team1_id else team2_name
            bowling_name = team2_name if batting_id == team1_id else team1_name
            batting_prior = _team_features(team_stats[batting_id], "batting")
            bowling_prior = _team_features(team_stats[bowling_id], "bowling")
            oriented_h2h = h2h_stats[(batting_id, bowling_id)]
            oriented_matches = oriented_h2h["matches"]
            oriented_avg = oriented_h2h["runs"] / oriented_matches if oriented_matches else None
            batting_roster = _roster_features(player_team_stats[batting_id])
            bowling_roster = _roster_features(player_team_stats[bowling_id])

            # This is a pre-match feature.  target_runs is only available when
            # the second innings is being predicted, and is never populated
            # for a first-innings row.
            target_runs_available = match.target_runs if inning.innings == 2 else None
            score_rows.append(
                {
                    "batting_team": batting_name,
                    "bowling_team": bowling_name,
                    "venue": venue_name,
                    "toss_winner": toss_winner,
                    "toss_decision": toss_decision,
                    "season": str(match.season),
                    "match_type": match_type,
                    # Aggregate-only training data has no historical delivery
                    # snapshot.  Use the information available immediately
                    # before the innings instead of deriving a "current" state
                    # from the final target.  Runtime requests can provide a
                    # later state; the same feature columns are then populated.
                    "current_score": 0,
                    "overs_completed": 0.0,
                    "wickets": 0,
                    "current_run_rate": None,
                    "last_5_overs_runs": None,
                    "recent_wickets": None,
                    "target_runs_available": target_runs_available,
                    "balls_remaining": 120.0,
                    "wickets_remaining": 10.0,
                    "is_second_innings": int(inning.innings == 2),
                    "batting_team_matches_before": batting_prior["matches"],
                    "batting_team_avg_runs_before": batting_prior["avg_runs"],
                    "batting_team_avg_wickets_before": batting_prior["avg_wickets"],
                    "batting_team_avg_run_rate_before": batting_prior["run_rate"],
                    "bowling_team_avg_runs_allowed_before": bowling_prior["avg_runs"],
                    "bowling_team_avg_wickets_before": bowling_prior["avg_wickets"],
                    "venue_avg_runs_before": (
                        venue_prior["runs"] / venue_prior["matches"]
                        if venue_prior["matches"]
                        else None
                    ),
                    "venue_matches_before": venue_prior["matches"],
                    "head_to_head_matches_before": oriented_matches,
                    "head_to_head_avg_runs_before": oriented_avg,
                    "batting_roster_players_before": batting_roster["players"],
                    "batting_roster_avg_runs_before": batting_roster["avg_runs"],
                    "batting_roster_avg_strike_rate_before": batting_roster["avg_strike_rate"],
                    "bowling_roster_players_before": bowling_roster["players"],
                    "bowling_roster_avg_wickets_before": bowling_roster["avg_wickets"],
                    "bowling_roster_avg_economy_before": bowling_roster["avg_economy"],
                }
            )
            score_targets.append(int(inning.runs))
            score_dates.append(match.date)
            score_ids.append(inning.pk)
            score_group_ids.append(match.pk)

        # Outcome labels are only made after all pre-match features for this
        # match have been captured.  Ties/no-results are excluded rather than
        # assigning an artificial target.
        if match.winner_id in (team1_id, team2_id) and match.result_type not in {
            "tie",
            "no_result",
        }:
            winner_name = _team_label(match.winner)
            t1_wins = team_stats[team1_id].get("wins", 0)
            t2_wins = team_stats[team2_id].get("wins", 0)
            t1_matches = team_stats[team1_id].get("matches", 0)
            t2_matches = team_stats[team2_id].get("matches", 0)
            outcome_rows.append(
                {
                    "team1": team1_name,
                    "team2": team2_name,
                    "venue": venue_name,
                    "toss_winner": toss_winner,
                    "toss_decision": toss_decision,
                    "season": str(match.season),
                    "match_type": match_type,
                    "team1_matches_before": t1_matches,
                    "team2_matches_before": t2_matches,
                    "team1_win_rate_before": t1_wins / t1_matches if t1_matches else None,
                    "team2_win_rate_before": t2_wins / t2_matches if t2_matches else None,
                    "team1_avg_runs_before": team1_prior["avg_runs"],
                    "team2_avg_runs_before": team2_prior["avg_runs"],
                    "team1_avg_wickets_before": team1_prior["avg_wickets"],
                    "team2_avg_wickets_before": team2_prior["avg_wickets"],
                    "venue_avg_runs_before": (
                        venue_prior["runs"] / venue_prior["matches"]
                        if venue_prior["matches"]
                        else None
                    ),
                    "venue_matches_before": venue_prior["matches"],
                    "head_to_head_matches_before": h2h_matches,
                    "head_to_head_team1_wins_before": h2h12_wins,
                    "head_to_head_team1_win_rate_before": h2h_win_rate,
                    "team1_roster_avg_runs_before": team1_roster["avg_runs"],
                    "team1_roster_avg_strike_rate_before": team1_roster["avg_strike_rate"],
                    "team2_roster_avg_runs_before": team2_roster["avg_runs"],
                    "team2_roster_avg_strike_rate_before": team2_roster["avg_strike_rate"],
                }
            )
            outcome_targets.append(winner_name)
            outcome_dates.append(match.date)
            outcome_ids.append(match.pk)

        # Update all historical aggregates only after the current match's rows
        # have been constructed.  This is the key no-leakage boundary.
        for inning in innings:
            if inning.team_id not in (team1_id, team2_id):
                continue
            batting_id = inning.team_id
            bowling_id = team2_id if batting_id == team1_id else team1_id
            batting = team_stats[batting_id]
            bowling = team_stats[bowling_id]
            batting["innings"] += 1
            batting["runs"] += int(inning.runs)
            batting["wickets"] += int(inning.wickets)
            batting["balls"] += int(inning.balls)
            bowling["runs_allowed"] += int(inning.runs)
            bowling["bowling_wickets"] += int(inning.wickets)
            h2h = h2h_stats[(batting_id, bowling_id)]
            h2h["runs"] += int(inning.runs)
        # Count one completed historical meeting for each orientation.  The
        # runs above are accumulated separately for the team that batted.
        h2h_stats[(team1_id, team2_id)]["matches"] += 1
        h2h_stats[(team2_id, team1_id)]["matches"] += 1
        team_stats[team1_id]["matches"] += 1
        team_stats[team2_id]["matches"] += 1
        venue_stats[match.venue_id]["matches"] += 1
        venue_stats[match.venue_id]["runs"] += sum(
            int(inning.runs) for inning in innings if inning.team_id in (team1_id, team2_id)
        )
        if match.winner_id == team1_id:
            team_stats[team1_id]["wins"] += 1
            h2h_stats[(team1_id, team2_id)]["team1_wins"] += 1
            h2h_stats[(team2_id, team1_id)]["team2_wins"] += 1
        elif match.winner_id == team2_id:
            team_stats[team2_id]["wins"] += 1
            h2h_stats[(team1_id, team2_id)]["team2_wins"] += 1
            h2h_stats[(team2_id, team1_id)]["team1_wins"] += 1

        for performance in performances.get(match.pk, []):
            team_id = _performance_team(
                performance,
                int(match.season),
                affiliations,
                current_teams,
            )
            if team_id is None:
                continue
            roster = player_team_stats[team_id].setdefault(
                performance.player_id, _blank_player_stats()
            )
            roster["appearances"] += 1
            roster["runs"] += float(performance.runs or 0)
            roster["balls"] += float(performance.balls or 0)
            roster["wickets"] += float(performance.wickets or 0)
            roster["bowling_balls"] += float(performance.bowling_balls or 0)
            roster["runs_conceded"] += float(performance.runs_conceded or 0)

    datasets = [
        TrainingDataset(
            "score",
            score_rows,
            score_targets,
            score_dates,
            score_ids,
            score_group_ids,
        ),
        TrainingDataset("outcome", outcome_rows, outcome_targets, outcome_dates, outcome_ids),
    ]
    return datasets, player_warning


# ---------------------------------------------------------------------------
# Splitting, model fitting, and metrics


def _ordered_indices(dataset: TrainingDataset) -> list[int]:
    return sorted(
        range(len(dataset.rows)),
        key=lambda index: (dataset.dates[index], str(dataset.row_ids[index])),
    )


def _split_indices(
    dataset: TrainingDataset,
    test_size: float = 0.2,
) -> tuple[list[int], list[int], dict[str, Any]]:
    """Return chronological train/test indices, with a documented tiny-data fallback."""

    try:
        test_size = float(test_size)
    except (TypeError, ValueError) as exc:
        raise ValueError("test_size must be greater than 0 and less than 1.") from exc
    if not 0 < test_size < 1:
        raise ValueError("test_size must be greater than 0 and less than 1.")
    n = len(dataset.rows)
    if n == 0:
        raise TrainingDataError(f"No usable {dataset.kind} rows were found in the database.")
    if n == 1:
        return [0], [], {
            "strategy": "training_only_single_row",
            "test_size": test_size,
            "cutoff_date": dataset.dates[0].isoformat(),
        }

    if dataset.kind == "score":
        group_ids = dataset.group_ids if dataset.group_ids is not None and len(dataset.group_ids) == n else None
        # Keep every checkpoint from a match on the same side of the split. A
        # row-level cut could put 5-over states for a match in training and its
        # final checkpoint in testing, leaking the innings being predicted.
        ordered = _ordered_indices(dataset)
        groups: dict[Any, list[int]] = {}
        for index in ordered:
            if group_ids is not None:
                group = group_ids[index]
            else:
                # Legacy callers sometimes encode ``match:innings`` in the
                # row ID; plain IDs remain a row-level split.
                group = str(dataset.row_ids[index]).split(":", 1)[0]
            groups.setdefault(group, []).append(index)
        ordered_groups = sorted(
            groups,
            key=lambda group: (
                min(dataset.dates[index] for index in groups[group]),
                min(str(dataset.row_ids[index]) for index in groups[group]),
            ),
        )
        if len(ordered_groups) == 1:
            # There is no match-level holdout when all rows belong to one
            # match; retain a genuine final-row evaluation for tiny fixtures.
            test_indices = [ordered[-1]]
            train_indices = ordered[:-1]
            return train_indices, test_indices, {
                "strategy": "chronological_holdout",
                "split_unit": "row",
                "test_fraction": 1 / n,
                "cutoff_date": dataset.dates[train_indices[-1]].isoformat(),
                "test_start_date": dataset.dates[test_indices[0]].isoformat(),
                "test_end_date": dataset.dates[test_indices[-1]].isoformat(),
            }
        group_count = max(1, int(round(len(ordered_groups) * test_size)))
        group_count = min(group_count, len(ordered_groups) - 1)
        test_groups = set(ordered_groups[-group_count:])
        train_indices = [
            index for group, indices in groups.items() if group not in test_groups for index in indices
        ]
        test_indices = [
            index for group, indices in groups.items() if group in test_groups for index in indices
        ]
        return train_indices, test_indices, {
            "strategy": "chronological_holdout",
            "split_unit": "match",
            "split_strategy": "chronological_match_holdout",
            "test_size": test_size,
            "test_fraction": len(test_indices) / n,
            "test_fraction_matches": group_count / len(ordered_groups),
            "test_fraction_rows": len(test_indices) / n,
            "training_matches": len(ordered_groups) - group_count,
            "test_matches": group_count,
            "cutoff_date": dataset.dates[train_indices[-1]].isoformat(),
            "test_start_date": dataset.dates[test_indices[0]].isoformat(),
            "test_end_date": dataset.dates[test_indices[-1]].isoformat(),
        }

    ordered = _ordered_indices(dataset)
    # A temporal split is feasible as soon as there are a few observations.
    if n >= 5:
        test_count = max(1, int(round(n * test_size)))
        test_count = min(test_count, n - 1)
        test_indices = ordered[-test_count:]
        train_indices = ordered[:-test_count]
        return train_indices, test_indices, {
            "strategy": "chronological_holdout",
            "test_size": test_size,
            "test_fraction": test_count / n,
            "cutoff_date": dataset.dates[train_indices[-1]].isoformat(),
            "test_start_date": dataset.dates[test_indices[0]].isoformat(),
            "test_end_date": dataset.dates[test_indices[-1]].isoformat(),
        }

    # For a handful of rows, a random split is the only way to produce a
    # genuine holdout metric.  It is explicitly recorded rather than pretending
    # the result was temporal.
    try:
        import numpy as np
        from sklearn.model_selection import train_test_split

        indices = np.array(ordered)
        labels = np.array(dataset.targets, dtype=object)
        stratify = None
        if dataset.kind == "outcome":
            _, counts = np.unique(labels, return_counts=True)
            if len(counts) > 1 and counts.min() >= 2:
                stratify = labels
        train, test = train_test_split(
            indices,
            test_size=max(1, int(round(n * test_size))),
            random_state=42,
            shuffle=True,
            stratify=stratify,
        )
        return sorted(map(int, train)), sorted(map(int, test)), {
            "strategy": "random_holdout_insufficient_rows_for_temporal_split",
            "test_size": test_size,
            "test_fraction": len(test) / n,
            "random_state": 42,
        }
    except Exception:
        # Last-resort split for unusual label distributions.  It is still a
        # real evaluation, and metadata records the fallback.
        test_indices = ordered[-1:]
        return ordered[:-1], test_indices, {
            "strategy": "last_row_holdout_fallback",
            "test_size": test_size,
            "test_fraction": 1 / n,
        }


def _frame(rows: list[dict[str, Any]], feature_names: list[str]):
    import pandas as pd

    return pd.DataFrame(rows, columns=feature_names)


def _make_score_estimator(random_state: int = 42):
    from sklearn.ensemble import HistGradientBoostingRegressor

    return HistGradientBoostingRegressor(
        learning_rate=0.06,
        max_iter=300,
        max_leaf_nodes=31,
        min_samples_leaf=20,
        l2_regularization=1.0,
        random_state=random_state,
    )


def _new_outcome_estimator(labels: list[str] | list[Any], random_state: int = 42):
    from sklearn.dummy import DummyClassifier
    from sklearn.ensemble import RandomForestClassifier

    unique = {str(label) for label in labels}
    if len(unique) < 2:
        return DummyClassifier(strategy="prior"), False
    # A regularized forest handles the many rare historical franchises without
    # extrapolating unstable linear decision boundaries. Sigmoid calibration
    # is fitted only within the isolated training side in _fit_outcome_estimator.
    estimator = RandomForestClassifier(
        n_estimators=300,
        max_depth=12,
        min_samples_leaf=3,
        max_features="sqrt",
        class_weight="balanced_subsample",
        random_state=random_state,
        n_jobs=1,
    )
    return estimator, True


def _fit_outcome_estimator(X, y, random_state: int = 42):
    """Fit a classifier with leakage-safe calibration inside the training set."""

    estimator, can_calibrate = _new_outcome_estimator(list(y), random_state)
    calibrated = False
    if can_calibrate and len(y) >= 8:
        try:
            from collections import Counter

            from sklearn.calibration import CalibratedClassifierCV
            from sklearn.model_selection import StratifiedKFold

            class_counts = Counter(str(label) for label in y)
            minimum_class_count = min(class_counts.values())
            # Calibration folds only use the already-isolated training side.
            # Stratification prevents a chronological fold from omitting a
            # rare historical franchise; the outer test split remains strictly
            # chronological.
            n_splits = min(5, max(2, minimum_class_count // 2))
            if n_splits >= 2:
                calibrated_model = CalibratedClassifierCV(
                    estimator=estimator,
                    method="sigmoid",
                    cv=StratifiedKFold(
                        n_splits=n_splits,
                        shuffle=True,
                        random_state=random_state,
                    ),
                    n_jobs=1,
                )
                calibrated_model.fit(X, y)
                estimator = calibrated_model
                calibrated = True
        except (ValueError, TypeError, RuntimeError):
            # A small/imbalanced data set can leave a fold with one class. A
            # plain classifier is still valid; calibration is then explicitly
            # marked false rather than being invented.
            calibrated = False
    if not calibrated:
        estimator.fit(X, y)
    return estimator, calibrated


def _regression_metrics(y_true, y_pred) -> dict[str, Any]:
    import numpy as np
    from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

    truth = np.asarray(y_true, dtype=float)
    prediction = np.asarray(y_pred, dtype=float)
    mae = float(mean_absolute_error(truth, prediction))
    mse = float(mean_squared_error(truth, prediction))
    rmse = float(math.sqrt(mse))
    if len(truth) >= 2:
        r2 = float(r2_score(truth, prediction))
    else:
        r2 = None
    return {"mae": mae, "rmse": rmse, "r2": r2, "test_samples": len(truth)}


def _classification_metrics(y_true, y_pred) -> dict[str, Any]:
    import numpy as np
    from sklearn.metrics import (
        accuracy_score,
        precision_recall_fscore_support,
    )

    truth = np.asarray(y_true, dtype=object)
    prediction = np.asarray(y_pred, dtype=object)
    accuracy = float(accuracy_score(truth, prediction))
    macro = precision_recall_fscore_support(
        truth,
        prediction,
        average="macro",
        zero_division=0,
    )
    weighted = precision_recall_fscore_support(
        truth,
        prediction,
        average="weighted",
        zero_division=0,
    )
    return {
        "accuracy": accuracy,
        "precision_macro": float(macro[0]),
        "recall_macro": float(macro[1]),
        "f1_macro": float(macro[2]),
        "precision_weighted": float(weighted[0]),
        "recall_weighted": float(weighted[1]),
        "f1_weighted": float(weighted[2]),
        "test_samples": len(truth),
    }


def _residual_metadata(y_true, y_pred) -> dict[str, Any]:
    import numpy as np

    residuals = np.asarray(y_true, dtype=float) - np.asarray(y_pred, dtype=float)
    absolute = np.abs(residuals)
    q10, q50, q90 = np.quantile(residuals, [0.10, 0.50, 0.90])
    abs_q50, abs_q90 = np.quantile(absolute, [0.50, 0.90])
    return {
        "method": "empirical_absolute_residual_error_on_holdout",
        "interval_coverage": 0.80,
        "residual_quantiles": {
            "q10": float(q10),
            "q50": float(q50),
            "q90": float(q90),
        },
        "absolute_error_quantiles": {
            "q50": float(abs_q50),
            "q90": float(abs_q90),
        },
        # The runtime interval is centred on the point estimate.  These
        # offsets are measured from the hold-out absolute errors, rather than
        # fabricated confidence percentages.
        "range_low_offset": -float(abs_q90),
        "range_high_offset": float(abs_q90),
        "residual_low": float(q10),
        "residual_high": float(q90),
        "sample_count": len(residuals),
    }


def _version(kind: str, dataset: TrainingDataset) -> str:
    latest = max(dataset.dates).isoformat() if dataset.dates else "none"
    digest = hashlib.sha256(
        f"{kind}:{len(dataset.rows)}:{latest}:{','.join(map(str, dataset.row_ids))}".encode()
    ).hexdigest()[:12]
    return f"{kind}-{digest}"


def _atomic_joblib_dump(value: Any, path: Path) -> None:
    import joblib

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    os.close(fd)
    try:
        joblib.dump(value, temporary)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _atomic_json_dump(value: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    os.close(fd)
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(_json_safe(value), handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _publish(
    kind: str,
    model: Any,
    preprocessor: Any,
    metadata: dict[str, Any],
    output_dir: str | os.PathLike[str] | None,
) -> dict[str, Path]:
    if output_dir is not None:
        directory = Path(output_dir).expanduser()
    else:
        from django.conf import settings

        directory = Path(settings.MODEL_DIRECTORY).expanduser()
    paths = {
        "model": directory / f"{kind}_model.joblib",
        "preprocessor": directory / f"{kind}_preprocessor.joblib",
        "metadata": directory / f"{kind}_metadata.json",
    }
    _atomic_joblib_dump(model, paths["model"])
    _atomic_joblib_dump(preprocessor, paths["preprocessor"])
    # Metadata is written last: a request that sees it can assume the two
    # joblib files have already been completely published.
    metadata = dict(metadata)
    metadata["artifacts"] = {key: path.name for key, path in paths.items()}
    _atomic_json_dump(metadata, paths["metadata"])
    return paths


def _run_training(
    kind: str,
    test_size: float,
    random_state: int,
    output_dir: str | os.PathLike[str] | None,
) -> dict[str, Any]:
    if kind not in {"score", "outcome"}:
        raise ValueError(f"Unknown prediction kind: {kind}")
    if not 0 < float(test_size) < 1:
        raise ValueError("test_size must be greater than 0 and less than 1.")
    import numpy as np

    training_data_note = None
    using_live_score = False
    if kind == "score":
        try:
            dataset = _build_live_score_dataset()
            player_warning = False
            using_live_score = True
        except TrainingDataError as exc:
            # A normalized database can still be trained without the optional
            # raw delivery archive. Prefer real checkpoints when available,
            # while keeping DB-only migration/test fixtures usable.
            datasets, player_warning = _build_datasets()
            dataset = next(item for item in datasets if item.kind == kind)
            training_data_note = str(exc)
    else:
        datasets, player_warning = _build_datasets()
        dataset = next(item for item in datasets if item.kind == kind)
    if len(dataset.rows) < 2:
        raise TrainingDataError(
            f"At least two usable {kind} rows are required. "
            "Import MatchInnings data and run the training job again."
        )

    feature_names = list(SCORE_FEATURE_NAMES if kind == "score" else OUTCOME_FEATURE_NAMES)
    categorical = list(
        SCORE_CATEGORICAL_FEATURES if kind == "score" else OUTCOME_CATEGORICAL_FEATURES
    )
    # Sorting is explicit before both the holdout and any calibration split.
    order = _ordered_indices(dataset)
    ordered_rows = [dataset.rows[index] for index in order]
    ordered_targets = [dataset.targets[index] for index in order]
    ordered_dates = [dataset.dates[index] for index in order]
    ordered_ids = [dataset.row_ids[index] for index in order]
    ordered_group_ids = (
        [dataset.group_ids[index] for index in order]
        if dataset.group_ids is not None and len(dataset.group_ids) == len(order)
        else None
    )
    ordered_dataset = TrainingDataset(
        kind,
        ordered_rows,
        ordered_targets,
        ordered_dates,
        ordered_ids,
        ordered_group_ids,
    )
    train_indices, test_indices, split = _split_indices(ordered_dataset, test_size=test_size)

    X = _frame(ordered_rows, feature_names)
    y = np.asarray(ordered_targets, dtype=object)
    if kind == "score":
        y = y.astype(float)
    train_indices = list(train_indices)
    test_indices = list(test_indices)

    # Evaluation model: the preprocessor is fit only on the training side.
    evaluation_preprocessor = make_preprocessor(feature_names, categorical)
    X_train = X.iloc[train_indices]
    X_test = X.iloc[test_indices] if test_indices else X.iloc[[]]
    y_train = y[train_indices]
    y_test = y[test_indices]
    evaluation_preprocessor.fit(X_train)
    transformed_train = evaluation_preprocessor.transform(X_train)

    if kind == "score":
        from sklearn.base import clone

        evaluation_model = clone(_make_score_estimator(random_state))
        evaluation_model.fit(transformed_train, y_train)
        train_predictions = evaluation_model.predict(transformed_train)
        if test_indices:
            test_predictions = evaluation_model.predict(evaluation_preprocessor.transform(X_test))
            metrics = _regression_metrics(y_test, test_predictions)
            residual_source = "chronological_test_set" if split["strategy"] == "chronological_holdout" else "holdout_test_set"
        else:
            metrics = _regression_metrics(y_train, train_predictions)
            test_predictions = train_predictions
            residual_source = "training_set_single_row_fallback"
        residual_y = y_test if test_indices else y_train
        residual_predictions = test_predictions
        # Ensure a finite point estimate even with a degenerate tiny sample.
        final_preprocessor = make_preprocessor(feature_names, categorical)
        final_preprocessor.fit(X)
        final_model = clone(_make_score_estimator(random_state))
        final_model.fit(final_preprocessor.transform(X), y)
        probability_metadata = None
    else:
        evaluation_model, calibrated_for_evaluation = _fit_outcome_estimator(
            transformed_train, list(y_train), random_state=random_state
        )
        train_predictions = evaluation_model.predict(transformed_train)
        if test_indices:
            transformed_test = evaluation_preprocessor.transform(X_test)
            test_predictions = evaluation_model.predict(transformed_test)
            metrics = _classification_metrics(y_test, test_predictions)
            residual_source = "chronological_test_set" if split["strategy"] == "chronological_holdout" else "holdout_test_set"
        else:
            metrics = _classification_metrics(y_train, train_predictions)
            test_predictions = train_predictions
            residual_source = "training_set_single_row_fallback"
        # Outcome probabilities are a property of the fitted classifier.  Do
        # not manufacture them for a classifier without predict_proba.
        supports_probabilities = hasattr(evaluation_model, "predict_proba")
        final_preprocessor = make_preprocessor(feature_names, categorical)
        final_preprocessor.fit(X)
        final_model, calibrated_for_final = _fit_outcome_estimator(
            final_preprocessor.transform(X), list(y), random_state=random_state
        )
        supports_probabilities = bool(
            supports_probabilities and hasattr(final_model, "predict_proba")
        )
        probability_metadata = {
            "available": supports_probabilities,
            "method": "classifier.predict_proba" if supports_probabilities else None,
            "calibrated": bool(calibrated_for_final),
        }
        residual_y = None
        residual_predictions = None

    metadata: dict[str, Any] = {
        "kind": kind,
        "model_version": _version(kind, ordered_dataset),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model_type": type(final_model).__name__,
        "target": "innings_runs" if kind == "score" else "match_winner_team_name",
        "feature_names": feature_names,
        "categorical_features": categorical,
        "numeric_features": [name for name in feature_names if name not in categorical],
        "training_rows": len(ordered_rows),
        "train_rows": len(train_indices),
        "test_rows": len(test_indices),
        "date_range": {
            "start": ordered_dates[0].isoformat() if ordered_dates else None,
            "end": ordered_dates[-1].isoformat() if ordered_dates else None,
        },
        "split": split,
        "metrics": _metric_dict(metrics),
        "evaluation_metrics": _metric_dict(metrics),
        "test_metrics": _metric_dict(metrics),
        "data_source": (
            ["MatchInnings", "Match", "deliveries.csv"]
            if kind == "score" and using_live_score
            else ["MatchInnings", "Match", "PlayerPerformance", "Player"]
        ),
        "player_data_available": not player_warning,
        "training_data_fallback": training_data_note,
        "leakage_policy": (
            "Score rows contain only pre-final delivery state available at their checkpoint; "
            "final innings totals are stored only as regression targets."
            if kind == "score" and using_live_score
            else "Score rows use a start-of-innings database snapshot and historical aggregates; "
            "final innings totals are stored only as regression targets."
            if kind == "score"
            else "Only aggregates from matches strictly before the current match are used; winner/result fields are never features."
        ),
        "prediction_time_scope": (
            "live_innings_state" if kind == "score" and using_live_score
            else "start_of_innings_aggregate_snapshot" if kind == "score"
            else "pre_match"
        ),
        "live_score_features_used": True if kind == "score" else None,
        "score_snapshot_policy": (
            "actual pre-final delivery checkpoints; completed-innings state is excluded and no final-target-derived features are used"
            if kind == "score" and using_live_score
            else "database MatchInnings start-state snapshot; no final-target-derived cumulative features"
            if kind == "score"
            else None
        ),
        "trained_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    # Flat aliases keep the metadata easy to consume from a shell dashboard
    # while the nested structures retain the full evaluation detail.
    if kind == "score":
        metadata.update(
            {
                "mae": metrics.get("mae"),
                "rmse": metrics.get("rmse"),
                "r2": metrics.get("r2"),
            }
        )
    else:
        metadata.update(
            {
                "accuracy": metrics.get("accuracy"),
                "precision": metrics.get("precision_macro"),
                "recall": metrics.get("recall_macro"),
                "f1": metrics.get("f1_macro"),
            }
        )

    if kind == "score":
        if using_live_score:
            metadata["processed_feature_table"] = "data/processed/score_training_snapshots.csv"
        metadata["residual_error"] = _residual_metadata(residual_y, residual_predictions)
        metadata["residual_error_range"] = {
            "low_offset": metadata["residual_error"]["range_low_offset"],
            "high_offset": metadata["residual_error"]["range_high_offset"],
            "basis": residual_source,
            "is_empirical": True,
        }
        metadata["empirical_error_range"] = dict(metadata["residual_error_range"])
        # Friendly aliases for consumers that only need a simple range.
        metadata["score_range"] = {
            "low_offset": metadata["residual_error"]["range_low_offset"],
            "high_offset": metadata["residual_error"]["range_high_offset"],
            "absolute_error_q90": metadata["residual_error"]["absolute_error_quantiles"]["q90"],
            "method": "empirical_absolute_residual_error",
        }
    else:
        metadata["probabilities"] = probability_metadata
        metadata["probability_source"] = (
            "classifier.predict_proba" if probability_metadata and probability_metadata["available"] else None
        )
        metadata["classes"] = [
            str(value) for value in getattr(final_model, "classes_", sorted(set(map(str, y))))
        ]

    paths = _publish(kind, final_model, final_preprocessor, metadata, output_dir)
    metadata["artifacts"] = {key: path.name for key, path in paths.items()}
    metadata["artifact_paths"] = {key: str(value) for key, value in paths.items()}
    return metadata


def train_score_model(
    *,
    test_size: float = 0.2,
    random_state: int = 42,
    output_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Train and publish the innings-score regressor."""

    return _run_training("score", test_size=test_size, random_state=random_state, output_dir=output_dir)


def train_outcome_model(
    *,
    test_size: float = 0.2,
    random_state: int = 42,
    output_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Train and publish the match-winner classifier."""

    return _run_training("outcome", test_size=test_size, random_state=random_state, output_dir=output_dir)


def format_training_report(metadata: dict[str, Any]) -> str:
    """Return a concise, copyable report for either training script."""

    metrics = metadata.get("metrics", {})
    lines = [
        f"Model: {metadata.get('model_version')} ({metadata.get('model_type')})",
        f"Rows: {metadata.get('training_rows')} (train={metadata.get('train_rows')}, test={metadata.get('test_rows')})",
        f"Split: {metadata.get('split', {}).get('split_strategy', metadata.get('split', {}).get('strategy'))}",
    ]
    if metadata.get("kind") == "score":
        lines.extend(
            [
                f"MAE: {metrics.get('mae')}",
                f"RMSE: {metrics.get('rmse')}",
                f"R2: {metrics.get('r2')}",
                "Range: empirical absolute-residual q90 from the holdout "
                f"(±{metadata.get('residual_error', {}).get('absolute_error_quantiles', {}).get('q90')} runs)",
            ]
        )
    else:
        probability = metadata.get("probabilities", {})
        lines.extend(
            [
                f"Accuracy: {metrics.get('accuracy')}",
                f"Precision (macro): {metrics.get('precision_macro')}",
                f"Recall (macro): {metrics.get('recall_macro')}",
                f"F1 (macro): {metrics.get('f1_macro')}",
                f"Probabilities: {'classifier.predict_proba' if probability.get('available') else 'not available'}",
            ]
        )
    lines.append("Artifacts: " + ", ".join(metadata.get("artifacts", {}).values()))
    return "\n".join(lines)
