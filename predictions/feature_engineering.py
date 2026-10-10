"""Feature definitions shared by the offline training jobs and online predictions.

The normalized database contains completed innings and player performances,
and no persisted delivery-snapshot table is assumed. The score trainer uses
actual pre-final checkpoints when the imported delivery archive is available,
or a leakage-safe start-of-innings state otherwise; in both cases the final
innings total is only the regression target. The request-time feature schema
is shared with the lazy model loader.

Keeping the feature names in one small, dependency-light module is important:
the training scripts and the request-time loader must always agree on the
schema.  Scikit-learn is imported only by :func:`make_preprocessor`, so
importing this module while Django starts is inexpensive.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


# Score features describe the state available at prediction time. The offline
# trainer uses actual pre-final checkpoints when available and a leakage-safe
# start-of-innings fallback otherwise; derived fields come from validated values.
SCORE_CATEGORICAL_FEATURES = [
    "batting_team",
    "bowling_team",
    "venue",
    "toss_winner",
    "toss_decision",
    "season",
    "match_type",
]
SCORE_NUMERIC_FEATURES = [
    "current_score",
    "overs_completed",
    "wickets",
    "current_run_rate",
    "last_5_overs_runs",
    "recent_wickets",
    "target_runs_available",
    "balls_remaining",
    "wickets_remaining",
    "is_second_innings",
    "batting_team_matches_before",
    "batting_team_avg_runs_before",
    "batting_team_avg_wickets_before",
    "batting_team_avg_run_rate_before",
    "bowling_team_avg_runs_allowed_before",
    "bowling_team_avg_wickets_before",
    "venue_avg_runs_before",
    "venue_matches_before",
    "head_to_head_matches_before",
    "head_to_head_avg_runs_before",
    "batting_roster_players_before",
    "batting_roster_avg_runs_before",
    "batting_roster_avg_strike_rate_before",
    "bowling_roster_players_before",
    "bowling_roster_avg_wickets_before",
    "bowling_roster_avg_economy_before",
]
SCORE_FEATURE_NAMES = SCORE_CATEGORICAL_FEATURES + SCORE_NUMERIC_FEATURES

OUTCOME_CATEGORICAL_FEATURES = [
    "team1",
    "team2",
    "venue",
    "toss_winner",
    "toss_decision",
    "season",
    "match_type",
]
OUTCOME_NUMERIC_FEATURES = [
    "team1_matches_before",
    "team2_matches_before",
    "team1_win_rate_before",
    "team2_win_rate_before",
    "team1_avg_runs_before",
    "team2_avg_runs_before",
    "team1_avg_wickets_before",
    "team2_avg_wickets_before",
    "venue_avg_runs_before",
    "venue_matches_before",
    "head_to_head_matches_before",
    "head_to_head_team1_wins_before",
    "head_to_head_team1_win_rate_before",
    "team1_roster_avg_runs_before",
    "team1_roster_avg_strike_rate_before",
    "team2_roster_avg_runs_before",
    "team2_roster_avg_strike_rate_before",
]
OUTCOME_FEATURE_NAMES = OUTCOME_CATEGORICAL_FEATURES + OUTCOME_NUMERIC_FEATURES

# Backward-compatible name used by callers that validate request-time fields.
LIVE_SCORE_NUMERIC_FEATURES = SCORE_NUMERIC_FEATURES

_TEAM_ALIASES = {
    "team_a": "batting_team",
    "team_b": "bowling_team",
    "batting": "batting_team",
    "bowling": "bowling_team",
    "team1": "team1",
    "team2": "team2",
}
_VENUE_ALIASES = {"ground": "venue", "stadium": "venue"}
_TARGET_ALIASES = {
    "target": "target_runs_available",
    "target_runs": "target_runs_available",
    "target_score_available": "target_runs_available",
}


def _name(value: Any) -> Any:
    """Return a stable string label for a Django model or a scalar value."""

    if value is None:
        return None
    if hasattr(value, "name"):
        return str(value.name)
    if hasattr(value, "label"):
        return str(value.label)
    return str(value)


def _number(value: Any) -> float | int | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    # Keep integral numeric values integral where possible, which makes
    # metadata and debug output easier to read without changing model input.
    return int(number) if number.is_integer() else number


def _mapping(value: Any) -> Mapping[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return value
    # Django QueryDict and similar objects support dict-like access but are not
    # always Mapping instances.
    try:
        return dict(value)
    except (TypeError, ValueError):
        return {}


def normalise_feature_input(values: Any) -> dict[str, Any]:
    """Normalize a form/API payload without performing any training work.

    Model instances are accepted so views can pass cleaned Django objects.  The
    function also accepts the common ``team1``/``team2`` spelling for outcome
    requests.  Unknown keys are ignored; the artifact's feature list remains
    the final authority on which fields are used.
    """

    source = _mapping(values)
    result: dict[str, Any] = {}
    for key, value in source.items():
        result[str(key)] = _name(value) if key in {
            "batting_team",
            "bowling_team",
            "team1",
            "team2",
            "venue",
            "toss_winner",
        } else value

    for alias, canonical in _TEAM_ALIASES.items():
        if canonical not in result and alias in result:
            result[canonical] = result[alias]
    for alias, canonical in _VENUE_ALIASES.items():
        if canonical not in result and alias in result:
            result[canonical] = result[alias]
    for alias, canonical in _TARGET_ALIASES.items():
        if canonical not in result and alias in result:
            result[canonical] = result[alias]
    if "target_runs_available" not in result and result.get("target_score") not in (None, ""):
        result["target_runs_available"] = result["target_score"]

    # Derived match-state values are computed from validated form inputs rather
    # than trusted from an API client.
    overs = _number(result.get("overs_completed"))
    wickets = _number(result.get("wickets"))
    current_score = _number(result.get("current_score"))
    if overs is not None:
        result["balls_remaining"] = max(0.0, 120.0 - overs * 6.0)
        if overs == 0 and current_score == 0:
            result.setdefault("current_run_rate", 0.0)
            result.setdefault("last_5_overs_runs", 0.0)
            result.setdefault("recent_wickets", 0.0)
        if current_score is not None and overs > 0 and result.get("current_run_rate") in (None, ""):
            result["current_run_rate"] = current_score * 6.0 / overs
    if wickets is not None:
        result["wickets_remaining"] = max(0.0, 10.0 - wickets)
    if result.get("target_runs_available") not in (None, ""):
        result["is_second_innings"] = 1
    elif "is_second_innings" not in result:
        result["is_second_innings"] = 0

    # Outcome forms use batting_team/bowling_team, while the outcome artifact
    # uses team1/team2.  Preserve the order supplied by the form.
    if "team1" not in result and "batting_team" in result:
        result["team1"] = result["batting_team"]
    if "team2" not in result and "bowling_team" in result:
        result["team2"] = result["bowling_team"]

    for key in (
        "is_first_innings",
        "target_runs_available",
        "batting_team_matches_before",
        "batting_team_avg_runs_before",
        "batting_team_avg_wickets_before",
        "batting_team_avg_run_rate_before",
        "bowling_team_avg_runs_allowed_before",
        "bowling_team_avg_wickets_before",
        "venue_avg_runs_before",
        "venue_matches_before",
        "head_to_head_matches_before",
        "head_to_head_avg_runs_before",
        "batting_roster_players_before",
        "batting_roster_avg_runs_before",
        "batting_roster_avg_strike_rate_before",
        "bowling_roster_players_before",
        "bowling_roster_avg_wickets_before",
        "bowling_roster_avg_economy_before",
        "team1_matches_before",
        "team2_matches_before",
        "team1_win_rate_before",
        "team2_win_rate_before",
        "team1_avg_runs_before",
        "team2_avg_runs_before",
        "team1_avg_wickets_before",
        "team2_avg_wickets_before",
        "head_to_head_team1_wins_before",
        "head_to_head_team1_win_rate_before",
        "team1_roster_avg_runs_before",
        "team1_roster_avg_strike_rate_before",
        "team2_roster_avg_runs_before",
        "team2_roster_avg_strike_rate_before",
    ) + tuple(LIVE_SCORE_NUMERIC_FEATURES):
        if key in result:
            result[key] = _number(result[key])

    if "season" in result and result["season"] not in (None, ""):
        result["season"] = str(result["season"])
    if "toss_decision" in result:
        toss_decision = str(result["toss_decision"]).strip().lower()
        result["toss_decision"] = toss_decision or "unknown"
    if "toss_winner" in result and result["toss_winner"] == "":
        result["toss_winner"] = "unknown"
    return result


def feature_row(values: Any, feature_names: list[str] | tuple[str, ...]) -> dict[str, Any]:
    """Return exactly the columns expected by a saved preprocessor."""

    normalized = normalise_feature_input(values)
    return {name: normalized.get(name) for name in feature_names}


def make_preprocessor(
    feature_names: list[str] | tuple[str, ...],
    categorical_features: list[str] | tuple[str, ...],
):
    """Build the dense, leakage-safe preprocessor used by both model types."""

    # Imports are deliberately local: a Django web process should be able to
    # import the app and show a useful "model not trained" page without loading
    # scikit-learn until it actually loads an artifact.
    from sklearn.compose import ColumnTransformer
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler

    names = list(feature_names)
    categorical = [name for name in categorical_features if name in names]
    numeric = [name for name in names if name not in categorical]

    numeric_transformer = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True)),
            ("scaler", StandardScaler()),
        ]
    )

    # ``sparse_output`` is the spelling in current supported scikit-learn;
    # retain a fallback for environments that still expose ``sparse``.
    try:
        encoder = OneHotEncoder(handle_unknown="ignore", sparse_output=False)
    except TypeError:  # pragma: no cover - compatibility with older sklearn
        encoder = OneHotEncoder(handle_unknown="ignore", sparse=False)
    categorical_transformer = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="most_frequent")),
            ("onehot", encoder),
        ]
    )

    return ColumnTransformer(
        transformers=[
            ("numeric", numeric_transformer, numeric),
            ("categorical", categorical_transformer, categorical),
        ],
        remainder="drop",
        sparse_threshold=0,
    )


def training_feature_names(kind: str) -> list[str]:
    """Return the canonical feature order for ``score`` or ``outcome``."""

    if kind == "score":
        return list(SCORE_FEATURE_NAMES)
    if kind == "outcome":
        return list(OUTCOME_FEATURE_NAMES)
    raise ValueError(f"Unknown prediction kind: {kind}")


def training_categorical_features(kind: str) -> list[str]:
    if kind == "score":
        return list(SCORE_CATEGORICAL_FEATURES)
    if kind == "outcome":
        return list(OUTCOME_CATEGORICAL_FEATURES)
    raise ValueError(f"Unknown prediction kind: {kind}")
