"""CSV discovery, validation, normalisation and database import for IPL data.

The :class:`IPLDataImporter` is intentionally independent of Django management
commands.  It can be used by jobs, notebooks and tests as well as by
``manage.py import_ipl_data``.

Only canonical, validated rows are written.  Preparation happens before the
transaction starts and the complete write is atomic, so malformed input never
leaves partially imported data behind.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
from django.conf import settings
from django.db import transaction

from matches.branding import brand_for
from matches.models import Match, MatchInnings, Team, Venue
from players.models import (
    Player,
    PlayerPerformance,
    PlayerSeasonPerformance,
    PlayerTeam,
)


class DataImportError(Exception):
    """Base exception for expected importer failures."""


class DataDiscoveryError(DataImportError):
    """Raised when the two source CSV files cannot be located."""


@dataclass(frozen=True)
class ImportIssue:
    """A source-level validation issue suitable for command output."""

    message: str
    row: int | str | None = None
    field: str | None = None
    source: str | None = None

    def __str__(self) -> str:
        location = []
        if self.source:
            location.append(str(self.source))
        if self.row is not None:
            location.append(f"row {self.row}")
        if self.field:
            location.append(self.field)
        prefix = " / ".join(location)
        return f"{prefix}: {self.message}" if prefix else self.message

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class DataValidationError(DataImportError):
    """Raised when a source file cannot be safely normalised."""

    def __init__(self, message: str, issues: Iterable[ImportIssue] = ()):
        self.issues = tuple(issues)
        detail = message
        if self.issues:
            examples = "; ".join(str(issue) for issue in self.issues[:5])
            suffix = "" if len(self.issues) <= 5 else f" (+{len(self.issues) - 5} more)"
            detail = f"{message}: {examples}{suffix}"
        super().__init__(detail)


@dataclass
class ImportSummary:
    """Counters and non-fatal warnings produced by one import run."""

    matches_seen: int = 0
    matches_created: int = 0
    matches_updated: int = 0
    delivery_rows: int = 0
    duplicate_match_rows: int = 0
    duplicate_delivery_rows: int = 0
    teams_imported: int = 0
    venues_imported: int = 0
    players_imported: int = 0
    innings_imported: int = 0
    performances_imported: int = 0
    season_performances_imported: int = 0
    affiliations_imported: int = 0
    matches_with_deliveries: int = 0
    warnings: list[str] = field(default_factory=list)
    source_files: dict[str, str] = field(default_factory=dict)
    replaced: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PreparedData:
    matches: pd.DataFrame
    deliveries: pd.DataFrame


MATCH_FILENAMES = (
    "matches.csv",
    "match.csv",
    "ipl_matches.csv",
    "ipl matches.csv",
    "matches_data.csv",
    "matches data.csv",
)
DELIVERY_FILENAMES = (
    "deliveries.csv",
    "delivery.csv",
    "ipl_deliveries.csv",
    "ipl deliveries.csv",
    "deliveries_data.csv",
    "deliveries data.csv",
)

# Header matching is punctuation/case insensitive, e.g. ``team_1`` matches
# ``Team 1``.  The first spelling is the internal canonical name.
MATCH_COLUMN_ALIASES = {
    "external_id": ("id", "match_id", "matchid", "match_no", "match_number", "game_id"),
    "season": ("season", "season_year", "year"),
    "city": ("city", "location"),
    "date": ("date", "match_date", "matchdate", "played_on"),
    "team1": ("team1", "team_1", "team_a", "home_team", "first_team"),
    "team2": ("team2", "team_2", "team_b", "away_team", "second_team"),
    "toss_winner": ("toss_winner", "tosswinning_team", "won_toss"),
    "toss_decision": ("toss_decision", "decision"),
    "result": ("result", "outcome", "result_description"),
    "result_type": ("result_type", "result_status", "status"),
    "dl_applied": (
        "dl_applied",
        "duckworth_lewis",
        "duckworth_lewis_applied",
        "dls_applied",
    ),
    "winner": ("winner", "winning_team"),
    "win_by_runs": ("win_by_runs", "winner_runs", "margin_runs"),
    "win_by_wickets": ("win_by_wickets", "winner_wickets", "margin_wickets"),
    "player_of_match": (
        "player_of_match",
        "player_of_the_match",
        "pom",
        "best_player",
    ),
    "venue": ("venue", "ground", "stadium"),
    "umpire1": ("umpire1", "umpire_1", "umpire_one", "first_umpire"),
    "umpire2": ("umpire2", "umpire_2", "umpire_two", "second_umpire"),
    "umpire3": ("umpire3", "umpire_3", "umpire_three", "third_umpire"),
    "umpires": ("umpires", "umpire"),
    "match_type": ("match_type", "stage", "match_stage"),
    "target_runs": ("target_runs", "target", "target_score"),
    "target_overs": ("target_overs", "overs_target"),
}

DELIVERY_COLUMN_ALIASES = {
    "match_id": ("match_id", "id", "match", "match_no", "match_number", "game_id"),
    "inning": ("inning", "innings", "innings_no", "inning_number"),
    "batting_team": ("batting_team", "battingteam", "team_batting"),
    "bowling_team": ("bowling_team", "bowlingteam", "team_bowling"),
    "over": ("over", "over_no", "over_number"),
    "ball": ("ball", "ball_no", "ball_number", "delivery"),
    "batsman": ("batsman", "striker", "batter", "batsman_name"),
    "non_striker": ("non_striker", "nonstriker", "non_striker_name"),
    "bowler": ("bowler", "bowler_name"),
    "is_super_over": ("is_super_over", "super_over", "is_superover"),
    "wide_runs": ("wide_runs", "wides", "wide"),
    "bye_runs": ("bye_runs", "byes", "bye"),
    "legbye_runs": ("legbye_runs", "leg_bye_runs", "legbyes"),
    "noball_runs": ("noball_runs", "no_ball_runs", "noballs", "no_ball"),
    "penalty_runs": ("penalty_runs", "penalty"),
    "batsman_runs": ("batsman_runs", "batter_runs", "striker_runs", "runs_off_bat"),
    "extra_runs": ("extra_runs", "extras"),
    "total_runs": ("total_runs", "team_runs", "runs_total"),
    "player_dismissed": ("player_dismissed", "dismissed_player", "batsman_out"),
    "dismissal_kind": ("dismissal_kind", "dismissal_type", "wicket_type"),
    "fielder": ("fielder", "fielding_player", "fielder_name"),
}

NULL_TEXT = {
    "",
    "-",
    "--",
    "na",
    "n/a",
    "nan",
    "none",
    "null",
    "not available",
    "not applicable",
}

TEAM_ALIASES = {
    "delhi daredevils": "Delhi Capitals",
    "delhi capitals": "Delhi Capitals",
    "kings xi punjab": "Punjab Kings",
    "punjab kings": "Punjab Kings",
    "rising pune supergiants": "Rising Pune Supergiant",
    "rising pune supergiant": "Rising Pune Supergiant",
    "sunrisers hyderabad": "Sunrisers Hyderabad",
    "royal challengers bangalore": "Royal Challengers Bangalore",
    "royal challengers bengaluru": "Royal Challengers Bangalore",
    "mumbai indians": "Mumbai Indians",
    "chennai super kings": "Chennai Super Kings",
    "kolkata knight riders": "Kolkata Knight Riders",
    "rajasthan royals": "Rajasthan Royals",
    "rcb": "Royal Challengers Bangalore",
    "mi": "Mumbai Indians",
    "csk": "Chennai Super Kings",
    "kkr": "Kolkata Knight Riders",
    "srh": "Sunrisers Hyderabad",
    "dc": "Delhi Capitals",
    "rr": "Rajasthan Royals",
    "pbks": "Punjab Kings",
    "gt": "Gujarat Titans",
    "lsg": "Lucknow Super Giants",
}

TEAM_SHORT_NAMES = {
    "Chennai Super Kings": "CSK",
    "Deccan Chargers": "DEC",
    "Delhi Capitals": "DC",
    "Gujarat Lions": "GL",
    "Gujarat Titans": "GT",
    "Kochi Tuskers Kerala": "KTK",
    "Kolkata Knight Riders": "KKR",
    "Lucknow Super Giants": "LSG",
    "Mumbai Indians": "MI",
    "Pune Warriors": "PW",
    "Punjab Kings": "PBKS",
    "Rajasthan Royals": "RR",
    "Rising Pune Supergiant": "RPS",
    "Royal Challengers Bangalore": "RCB",
    "Sunrisers Hyderabad": "SRH",
}

# Genuine spelling corrections found in the upstream Kaggle export.  Keys are
# compared case- and punctuation-insensitively by :func:`normalize_player_name`.
#
# Only verified misspellings belong here.  The abbreviated forms used by IPL
# scorecards ("S Dhawan", "VVS Laxman", "KMDN Kulasekara", "SMSM Senanayake")
# are the official listings for those cricketers and are deliberately left
# untouched - rewriting them would replace correct data with invented names.
PLAYER_NAME_ALIASES = {
    # The Indian batter is V. V. S. *Lakshman*; the export drops the "h".
    "vvs laxman": "VVS Lakshman",
}

VENUE_ALIASES = {
    "aca vdca stadium": "Dr. Y.S. Rajasekhara Reddy ACA-VDCA Cricket Stadium",
    "dr dy patil sports academy": "Dr. D. Y. Patil Sports Academy",
    "dr d y patil sports academy": "Dr. D. Y. Patil Sports Academy",
    "is bindra stadium": "Punjab Cricket Association IS Bindra Stadium, Mohali",
    "punjab cricket association stadium mohali": "Punjab Cricket Association IS Bindra Stadium, Mohali",
    "rajiv gandhi intl cricket stadium": "Rajiv Gandhi International Stadium, Uppal",
    "feroz shah kotla ground": "Feroz Shah Kotla",
    "m chinnaswamy stadium": "M. Chinnaswamy Stadium",
    "m chinnaswamy stadium bengaluru": "M. Chinnaswamy Stadium",
    "ma chidambaram stadium chepauk": "M. A. Chidambaram Stadium, Chennai",
    "ma chidambaram stadium": "M. A. Chidambaram Stadium, Chennai",
    "dr ys rajasekhara reddy aca vdca cricket stadium": (
        "Dr. Y.S. Rajasekhara Reddy ACA-VDCA Cricket Stadium"
    ),
    "hpca stadium": "Himachal Pradesh Cricket Association Stadium",
}

CITY_ALIASES = {
    "bangalore": "Bengaluru",
    "bangalore urban": "Bengaluru",
    "bengaluru": "Bengaluru",
    "chennai": "Chennai",
    "delhi": "Delhi",
    "hyderabad": "Hyderabad",
    "jaipur": "Jaipur",
    "kolkata": "Kolkata",
    "mumbai": "Mumbai",
    "nagpur": "Nagpur",
    "pune": "Pune",
    "raipur": "Raipur",
    "sharjah": "Sharjah",
    "visakhapatnam": "Visakhapatnam",
}

NON_WICKET_DISMISSALS = {
    "retiredhurt",
    "retiredill",
    "obstructingthefield",
    "obstructingfield",
}

# Dismissals that count toward a team's wicket total but are not credited to
# the bowler.  Run-outs and retirements must not inflate bowling statistics.
NON_BOWLER_WICKETS = {
    "runout",
    "retiredout",
    "retiredhurt",
    "retiredill",
    "obstructingthefield",
    "obstructingfield",
    "timedout",
}

_MISSING = object()

DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%b-%Y",
    "%d %b %Y",
    "%d-%B-%Y",
    "%d %B %Y",
    "%Y%m%d",
)


def _column_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKC", str(value)).casefold())


def _identity_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def clean_text(value: Any) -> str:
    """Return compact, Unicode-normalised text without CSV null markers."""

    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = "".join(character for character in text if character.isprintable())
    text = re.sub(r"\s+", " ", text).strip()
    return "" if text.casefold() in NULL_TEXT else text


def normalize_team_name(value: Any) -> str:
    """Normalise known franchise renames and spacing in team names."""

    text = clean_text(value)
    if not text:
        return ""
    identity = _identity_key(text)
    for alias, canonical in TEAM_ALIASES.items():
        if _identity_key(alias) == identity:
            return canonical
    return text


def normalize_venue_name(value: Any) -> str:
    """Normalise venue spelling while preserving historically distinct grounds."""

    text = clean_text(value)
    if not text:
        return ""
    text = re.sub(r"\s*,\s*", ", ", text)
    identity = _identity_key(text)
    for alias, canonical in VENUE_ALIASES.items():
        if _identity_key(alias) == identity:
            return canonical
    return text


def normalize_city_name(value: Any) -> str:
    text = clean_text(value)
    return CITY_ALIASES.get(text.casefold(), text)


# The Kaggle deliveries export labels substitutes as "<name> (sub)" and, when a
# franchise has two players with the same name, as "<name> (2)".  Those suffixes
# are not part of a player's name: left in place they create duplicate player
# rows whose career statistics are silently split.  The importer resolves them
# to the canonical name so one player keeps one record.
PLAYER_SUFFIX_PATTERN = re.compile(r"\s*\(\s*(?:sub|\d+)\s*\)\s*$", re.IGNORECASE)


def normalize_player_name(value: Any) -> str:
    """Return the canonical player name.

    Strips substitute/disambiguating suffixes, collapses whitespace and applies
    :data:`PLAYER_NAME_ALIASES` for genuine spelling corrections.
    """

    text = clean_text(value)
    if not text:
        return ""
    previous = None
    while previous != text:
        previous = text
        text = PLAYER_SUFFIX_PATTERN.sub("", text).strip()
    identity = _identity_key(text)
    for alias, canonical in PLAYER_NAME_ALIASES.items():
        if _identity_key(alias) == identity:
            return canonical
    return text


def parse_match_date(value: Any) -> date:
    """Parse common Kaggle date values, including day-first IPL exports."""

    if value is None or (not isinstance(value, str) and pd.isna(value)):
        raise ValueError("date is blank")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = clean_text(value)
    if not text:
        raise ValueError("date is blank")

    # Excel's 1900 date system is occasionally used by notebook exports.
    try:
        serial = float(text)
    except (TypeError, ValueError):
        serial = None
    if serial is not None and math.isfinite(serial) and 20_000 <= serial <= 80_000:
        offset = pd.to_timedelta(serial, unit="D").to_pytimedelta()
        return (datetime(1899, 12, 30) + offset).date()

    for date_format in DATE_FORMATS:
        try:
            return datetime.strptime(text, date_format).date()
        except ValueError:
            continue
    try:
        parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)
        if pd.notna(parsed):
            return pd.Timestamp(parsed).date()
    except (TypeError, ValueError, OverflowError):
        pass
    raise ValueError(f"unrecognised date {text!r}")


def _parse_integer(
    value: Any,
    field: str,
    *,
    row: int | str,
    source: str,
    minimum: int = 0,
    maximum: int | None = None,
    blank: int | None | object = _MISSING,
) -> int | None:
    text = clean_text(value)
    if not text and blank is not _MISSING:
        return blank  # type: ignore[return-value]
    if not text:
        raise DataValidationError(
            "A required numeric value is blank.",
            [ImportIssue("required value is blank", row, field, source)],
        )
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        raise DataValidationError(
            "A numeric value is invalid.",
            [ImportIssue(f"{text!r} is not an integer", row, field, source)],
        ) from None
    if not number.is_finite() or number != number.to_integral_value():
        raise DataValidationError(
            "A numeric value is invalid.",
            [ImportIssue(f"{text!r} is not an integer", row, field, source)],
        )
    integer = int(number)
    if integer < minimum or (maximum is not None and integer > maximum):
        bound = f"between {minimum} and {maximum}" if maximum is not None else f">= {minimum}"
        raise DataValidationError(
            "A numeric value is outside the allowed range.",
            [ImportIssue(f"{integer} must be {bound}", row, field, source)],
        )
    return integer


def _nullable_number(value, converter):
    """Convert pandas' NaN placeholders back to model-friendly ``None``."""

    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return converter(value)


def _parse_decimal(
    value: Any,
    field: str,
    *,
    row: int | str,
    source: str,
    minimum: Decimal = Decimal("0"),
    maximum: Decimal | None = None,
) -> Decimal | None:
    text = clean_text(value)
    if not text:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        raise DataValidationError(
            "A decimal value is invalid.",
            [ImportIssue(f"{text!r} is not a number", row, field, source)],
        ) from None
    if not number.is_finite() or number < minimum or (maximum is not None and number > maximum):
        bound = f"between {minimum} and {maximum}" if maximum is not None else f">= {minimum}"
        raise DataValidationError(
            "A decimal value is outside the allowed range.",
            [ImportIssue(f"{text!r} must be {bound}", row, field, source)],
        )
    return number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _parse_bool(value: Any) -> bool:
    text = clean_text(value).casefold()
    if text in {"1", "true", "yes", "y", "t"}:
        return True
    if text in {"", "0", "false", "no", "n", "f"}:
        return False
    number = Decimal(text)
    if number not in {Decimal("0"), Decimal("1")}:
        raise ValueError(f"invalid boolean value {text!r}")
    return bool(number)


def _candidate_directories(data_dir: str | Path | None) -> list[Path]:
    if data_dir is not None:
        root = Path(data_dir).expanduser()
        candidates = [root, root / "DataSets", root / "data" / "raw", root.parent / "DataSets"]
    else:
        configured = Path(settings.RAW_DATA_DIRECTORY)
        candidates = [configured, Path(settings.BASE_DIR) / "DataSets"]
    result: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        if resolved not in seen:
            seen.add(resolved)
            result.append(resolved)
    return result


def _find_file(directory: Path, names: tuple[str, ...]) -> Path | None:
    if not directory.is_dir():
        return None
    by_lower_name = {entry.name.casefold(): entry for entry in directory.iterdir() if entry.is_file()}
    for name in names:
        match = by_lower_name.get(name.casefold())
        if match is not None:
            return match
    return None


def discover_ipl_data_files(data_dir: str | Path | None = None) -> tuple[Path, Path]:
    """Discover ``matches.csv`` and ``deliveries.csv`` in likely data roots.

    ``data_dir`` may point at the CSV directory, the project root containing
    ``DataSets/``, or a raw-data directory.  When omitted, Django's
    ``RAW_DATA_DIRECTORY`` is preferred.
    """

    searched: list[str] = []
    partial_errors: list[str] = []
    for directory in _candidate_directories(data_dir):
        searched.append(str(directory))
        match_file = _find_file(directory, MATCH_FILENAMES)
        delivery_file = _find_file(directory, DELIVERY_FILENAMES)
        if match_file and delivery_file:
            return match_file.resolve(), delivery_file.resolve()
        missing = []
        if not match_file:
            missing.append("matches.csv")
        if not delivery_file:
            missing.append("deliveries.csv")
        if missing:
            partial_errors.append(f"{directory}: missing {', '.join(missing)}")
    detail = "; ".join(partial_errors) if partial_errors else "no candidate directories found"
    raise DataDiscoveryError(
        "Could not find the IPL matches and deliveries CSV files. "
        f"Searched: {', '.join(searched)}. {detail}"
    )


# Backwards-friendly short name for callers that do not need the IPL prefix.
discover_data_files = discover_ipl_data_files


def _read_csv(path: Path) -> pd.DataFrame:
    try:
        frame = pd.read_csv(
            path,
            dtype=str,
            keep_default_na=False,
            na_filter=False,
            encoding="utf-8-sig",
            low_memory=False,
        )
    except (OSError, UnicodeError, ValueError, pd.errors.ParserError) as exc:
        raise DataImportError(f"Unable to read {path}: {exc}") from exc
    if frame.empty:
        raise DataValidationError(
            "The CSV file is empty.",
            [ImportIssue("no data rows", source=str(path))],
        )
    return frame


def _map_columns(
    frame: pd.DataFrame,
    aliases: dict[str, tuple[str, ...]],
    *,
    required: Iterable[str],
    source: str,
) -> pd.DataFrame:
    available: dict[str, str] = {}
    for original in frame.columns:
        key = _column_key(original)
        if key in available and available[key] != original:
            raise DataValidationError(
                "The CSV contains ambiguous duplicate headers.",
                [
                    ImportIssue(
                        f"{available[key]!r} and {original!r} are equivalent",
                        field=str(original),
                        source=source,
                    )
                ],
            )
        available[key] = original

    rename: dict[str, str] = {}
    for canonical, variants in aliases.items():
        for variant in variants:
            original = available.get(_column_key(variant))
            if original is not None:
                rename[original] = canonical
                break

    missing = [column for column in required if column not in rename.values()]
    if missing:
        raise DataValidationError(
            "The CSV is missing required columns.",
            [
                ImportIssue(
                    "accepted aliases: " + ", ".join(aliases[column]),
                    field=column,
                    source=source,
                )
                for column in missing
            ],
        )
    return frame.rename(columns=rename)


def _cell(row: dict[str, Any], column: str) -> str:
    return clean_text(row.get(column, ""))


def _classify_match_type(value: Any) -> str:
    text = _identity_key(value)
    if "final" in text:
        return Match.MatchType.FINAL
    if any(word in text for word in ("playoff", "qualifier", "elimination", "challenger")):
        return Match.MatchType.PLAYOFF
    return Match.MatchType.LEAGUE


def _classify_result(raw_result: str, result_type: str) -> str:
    explicit = _identity_key(result_type)
    type_map = {
        _identity_key(value): value for value, _ in Match.ResultType.choices
    }
    if explicit in type_map:
        return type_map[explicit]
    text = _identity_key(raw_result)
    if "superover" in text or "superrover" in text:
        return Match.ResultType.SUPER_OVER
    if "tie" in text or "tied" in text:
        return Match.ResultType.TIE
    if "rain" in text or "abandon" in text or "washout" in text:
        return Match.ResultType.RAIN
    if "noresult" in text or "no result" in text or "withoutresult" in text:
        return Match.ResultType.NO_RESULT
    return Match.ResultType.NORMAL


def _result_margins(raw_result: str) -> tuple[int | None, int | None]:
    runs_match = re.search(r"(?:won\s+by|by)\s+(\d+)\s+runs?", raw_result, re.I)
    wickets_match = re.search(r"(?:won\s+by|by)\s+(\d+)\s+wickets?", raw_result, re.I)
    runs = int(runs_match.group(1)) if runs_match else None
    wickets = int(wickets_match.group(1)) if wickets_match else None
    return runs, wickets


def _winner_from_result(raw_result: str, teams: tuple[str, str]) -> str:
    result_key = _identity_key(raw_result)
    for team in sorted(teams, key=len, reverse=True):
        if _identity_key(team) in result_key:
            return team
    return ""


def _truncate_text(
    value: str,
    maximum: int,
    *,
    field: str,
    row: int,
    source: str,
    summary: ImportSummary,
) -> str:
    if len(value) <= maximum:
        return value
    summary.warnings.append(
        f"{source} row {row}: {field} was shortened from {len(value)} to {maximum} characters."
    )
    return value[: maximum - 1].rstrip() + "…"


def _prepare_matches(
    raw: pd.DataFrame,
    *,
    source: str,
    summary: ImportSummary,
) -> pd.DataFrame:
    required = ("external_id", "season", "date", "team1", "team2", "venue")
    frame = _map_columns(raw, MATCH_COLUMN_ALIASES, required=required, source=source)
    records: list[dict[str, Any]] = []
    seen: dict[int, tuple[Any, ...]] = {}

    for index, row in frame.iterrows():
        source_row = int(index) + 2
        values = row.to_dict()
        try:
            external_id = _parse_integer(
                _cell(values, "external_id"),
                "external_id",
                row=source_row,
                source=source,
                minimum=1,
                maximum=9_223_372_036_854_775_807,
            )
            season = _parse_integer(
                _cell(values, "season"), "season", row=source_row, source=source, minimum=1
            )
            try:
                match_date = parse_match_date(_cell(values, "date"))
            except ValueError as exc:
                raise DataValidationError(
                    "A match date is invalid.",
                    [ImportIssue(str(exc), source_row, "date", source)],
                ) from None

            team1 = normalize_team_name(_cell(values, "team1"))
            team2 = normalize_team_name(_cell(values, "team2"))
            if not team1 or not team2:
                raise DataValidationError(
                    "A team name is blank.",
                    [ImportIssue("both teams are required", source_row, "team1/team2", source)],
                )
            if _identity_key(team1) == _identity_key(team2):
                raise DataValidationError(
                    "A match has the same team twice.",
                    [ImportIssue(team1, source_row, "team2", source)],
                )
            if len(team1) > 120 or len(team2) > 120:
                raise DataValidationError(
                    "A team name is too long for the database model.",
                    [ImportIssue("maximum length is 120", source_row, "team1/team2", source)],
                )

            venue_name = normalize_venue_name(_cell(values, "venue"))
            if not venue_name:
                raise DataValidationError(
                    "A venue is blank.",
                    [ImportIssue("venue is required", source_row, "venue", source)],
                )
            venue_name = _truncate_text(
                venue_name,
                180,
                field="venue",
                row=source_row,
                source=source,
                summary=summary,
            )
            city = normalize_city_name(_cell(values, "city"))
            city = _truncate_text(
                city,
                100,
                field="city",
                row=source_row,
                source=source,
                summary=summary,
            )

            toss_winner = normalize_team_name(_cell(values, "toss_winner"))
            toss_key = _identity_key(toss_winner)
            if toss_key == _identity_key(team1):
                toss_winner = team1
            elif toss_key == _identity_key(team2):
                toss_winner = team2
            elif toss_winner:
                raise DataValidationError(
                    "The toss winner did not play in the match.",
                    [ImportIssue(toss_winner, source_row, "toss_winner", source)],
                )

            decision_key = _identity_key(_cell(values, "toss_decision"))
            if decision_key in {"bat", "batfirst", "battingfirst", "choosebat"}:
                toss_decision = Match.TossDecision.BAT
            elif decision_key in {
                "field",
                "fieldfirst",
                "fieldingfirst",
                "choosefield",
                "bowl",
                "bowlingfirst",
            }:
                toss_decision = Match.TossDecision.FIELD
            else:
                toss_decision = ""

            raw_result = _cell(values, "result")
            explicit_result_type = _cell(values, "result_type")
            result_type = _classify_result(raw_result, explicit_result_type)
            winner = normalize_team_name(_cell(values, "winner"))
            winner_key = _identity_key(winner)
            if winner_key == _identity_key(team1):
                winner = team1
            elif winner_key == _identity_key(team2):
                winner = team2
            elif not winner:
                winner = _winner_from_result(raw_result, (team1, team2))
            elif winner:
                raise DataValidationError(
                    "The winner did not play in the match.",
                    [ImportIssue(winner, source_row, "winner", source)],
                )

            run_margin = _parse_integer(
                _cell(values, "win_by_runs"),
                "win_by_runs",
                row=source_row,
                source=source,
                maximum=32_767,
                blank=None,
            )
            wicket_margin = _parse_integer(
                _cell(values, "win_by_wickets"),
                "win_by_wickets",
                row=source_row,
                source=source,
                maximum=32_767,
                blank=None,
            )
            result_runs, result_wickets = _result_margins(raw_result)
            run_margin = run_margin if run_margin is not None else result_runs
            wicket_margin = wicket_margin if wicket_margin is not None else result_wickets
            if run_margin and wicket_margin:
                raise DataValidationError(
                    "A match has both run and wicket margins.",
                    [ImportIssue("the margins are mutually exclusive", source_row, "result", source)],
                )
            if (run_margin or wicket_margin) and not winner:
                winner = _winner_from_result(raw_result, (team1, team2))
            if (run_margin or wicket_margin) and not winner:
                raise DataValidationError(
                    "A winning margin was supplied without a winner.",
                    [ImportIssue("winner is required", source_row, "winner", source)],
                )
            # The canonical IPL CSV records the side involved in a tied
            # super-over as ``winner``; preserve that source value because the
            # shared model explicitly allows it for result analysis.
            # Zero is the Kaggle convention for the unused margin column.
            if run_margin == 0:
                run_margin = None
            if wicket_margin == 0:
                wicket_margin = None

            if result_type == Match.ResultType.SUPER_OVER:
                dismissal_method = Match.DismissalMethod.SUPER_OVER
            elif result_type == Match.ResultType.TIE:
                dismissal_method = Match.DismissalMethod.TIE
            elif result_type == Match.ResultType.RAIN:
                dismissal_method = Match.DismissalMethod.RAIN
            elif result_type == Match.ResultType.NO_RESULT:
                dismissal_method = Match.DismissalMethod.NO_RESULT
            elif run_margin:
                dismissal_method = Match.DismissalMethod.RUNS
            elif wicket_margin:
                dismissal_method = Match.DismissalMethod.WICKETS
            else:
                dismissal_method = ""

            if _identity_key(raw_result) in {
                value.value for value in Match.ResultType
            } | {"runs", "wickets", "normal"}:
                result_text = {
                    Match.ResultType.TIE: "Tie",
                    Match.ResultType.NO_RESULT: "No result",
                    Match.ResultType.RAIN: "Rain affected",
                    Match.ResultType.SUPER_OVER: "Super over",
                }.get(result_type, "")
            else:
                result_text = _truncate_text(
                    raw_result,
                    220,
                    field="result",
                    row=source_row,
                    source=source,
                    summary=summary,
                )

            player_of_match = clean_text(_cell(values, "player_of_match"))
            if len(player_of_match) > 140:
                raise DataValidationError(
                    "A player name is too long for the database model.",
                    [ImportIssue("maximum length is 140", source_row, "player_of_match", source)],
                )

            umpires: list[str] = []
            combined_umpires = _cell(values, "umpires")
            if combined_umpires:
                umpires.extend(re.split(r"\s*[;|]\s*|\s*,\s*", combined_umpires))
            for column in ("umpire1", "umpire2", "umpire3"):
                umpire = _cell(values, column)
                if umpire:
                    umpires.append(umpire)
            umpires = list(dict.fromkeys(filter(None, (clean_text(item) for item in umpires))))

            target_runs = _parse_integer(
                _cell(values, "target_runs"),
                "target_runs",
                row=source_row,
                source=source,
                minimum=1,
                maximum=32_767,
                blank=None,
            )
            target_overs = _parse_decimal(
                _cell(values, "target_overs"),
                "target_overs",
                row=source_row,
                source=source,
                maximum=Decimal("999.99"),
            )

            record = {
                "external_id": external_id,
                "season": season,
                "date": match_date,
                "city": city,
                "venue_name": venue_name,
                "team1_name": team1,
                "team2_name": team2,
                "toss_winner_name": toss_winner,
                "toss_decision": toss_decision,
                "winner_name": winner,
                "result_type": result_type,
                "result": result_text,
                "dismissal_method": dismissal_method,
                "player_of_match": player_of_match,
                "match_type": _classify_match_type(_cell(values, "match_type")),
                "target_runs": target_runs,
                "target_overs": target_overs,
                "win_by_runs": run_margin,
                "win_by_wickets": wicket_margin,
                "dl_applied": _parse_bool(_cell(values, "dl_applied")),
                "umpires": umpires,
            }
        except DataValidationError:
            raise
        except (TypeError, ValueError, InvalidOperation) as exc:
            raise DataValidationError(
                "A match row is invalid.",
                [ImportIssue(str(exc), source_row, source=source)],
            ) from exc

        signature = tuple(record[key] for key in record if key != "external_id")
        if external_id in seen:
            if seen[external_id] == signature:
                summary.duplicate_match_rows += 1
                continue
            raise DataValidationError(
                "The matches CSV contains conflicting duplicate match IDs.",
                [ImportIssue(str(external_id), source_row, "external_id", source)],
            )
        seen[external_id] = signature
        records.append(record)

    summary.matches_seen = len(records)
    return pd.DataFrame.from_records(records)


def _integer_series(
    frame: pd.DataFrame,
    column: str,
    *,
    source: str,
    default: int = 0,
    minimum: int = 0,
    maximum: int | None = None,
) -> pd.Series:
    raw = frame[column].map(clean_text)
    numeric = pd.to_numeric(raw.replace("", pd.NA), errors="coerce")
    invalid = raw.ne("") & numeric.isna()
    finite = numeric.notna() & numeric.map(
        lambda value: bool(pd.notna(value) and math.isfinite(float(value)))
    )
    fractional = numeric.notna() & numeric.map(
        lambda value: bool(
            pd.notna(value)
            and math.isfinite(float(value))
            and float(value) != math.floor(float(value))
        )
    )
    bad = invalid | ~finite | fractional
    if numeric.notna().any():
        negative = numeric.notna() & (numeric < minimum)
        too_large = numeric.notna() & numeric.map(
            lambda value: maximum is not None and pd.notna(value) and float(value) > maximum
        )
        bad = bad | negative | too_large
    if bad.any():
        first_index = int(bad[bad].index[0])
        issues = [
            ImportIssue(
                str(frame.at[first_index, column]),
                int(first_index) + 2,
                column,
                source,
            )
        ]
        raise DataValidationError(
            f"Delivery column {column!r} contains an invalid numeric value.",
            issues,
        )
    return numeric.fillna(default).astype("int64")


def _prepare_deliveries(
    raw: pd.DataFrame,
    matches: pd.DataFrame,
    *,
    source: str,
    summary: ImportSummary,
) -> pd.DataFrame:
    required = (
        "match_id",
        "inning",
        "batting_team",
        "bowling_team",
        "over",
        "ball",
        "batsman",
        "batsman_runs",
    )
    had_total_runs = any(
        _column_key(column) in DELIVERY_COLUMN_ALIASES["total_runs"]
        for column in raw.columns
    )
    had_extra_runs = any(
        _column_key(column) in DELIVERY_COLUMN_ALIASES["extra_runs"]
        for column in raw.columns
    )
    frame = _map_columns(raw, DELIVERY_COLUMN_ALIASES, required=required, source=source)
    frame = frame.copy()
    frame["external_id"] = _integer_series(
        frame, "match_id", source=source, minimum=1, maximum=9_223_372_036_854_775_807
    )
    for column in (
        "inning",
        "wide_runs",
        "bye_runs",
        "legbye_runs",
        "noball_runs",
        "penalty_runs",
        "batsman_runs",
        "total_runs",
        "extra_runs",
        "is_super_over",
    ):
        if column not in frame:
            frame[column] = "0"
        frame[column] = _integer_series(
            frame,
            column,
            source=source,
            maximum=32_767 if column in {"inning", "is_super_over"} else 1_000_000,
        )

    for column in ("over", "ball"):
        if column not in frame:
            frame[column] = "0"
        frame[column] = _integer_series(
            frame, column, source=source, minimum=1, maximum=1_000
        )
    for column in ("non_striker", "bowler", "player_dismissed", "dismissal_kind", "fielder"):
        if column not in frame:
            frame[column] = ""
        frame[column] = frame[column].map(clean_text)

    # Player names are case-insensitive identities in the scorecard.  Pick the
    # first spelling for each folded name so a source that changes casing does
    # not create duplicate (player, match) performance rows later.
    canonical_player_names: dict[str, str] = {}
    for column in ("batsman", "non_striker", "bowler", "player_dismissed", "fielder"):
        for name in frame[column].unique().tolist():
            if name:
                canonical_player_names.setdefault(name.casefold(), name)
    for column in ("batsman", "non_striker", "bowler", "player_dismissed", "fielder"):
        frame[column] = frame[column].map(
            lambda name: canonical_player_names.get(name.casefold(), name) if name else name
        )

    long_player_names = sorted(
        {
            name
            for column in ("batsman", "non_striker", "bowler", "player_dismissed", "fielder")
            for name in frame[column].unique().tolist()
            if name and len(name) > 140
        }
    )
    if long_player_names:
        raise DataValidationError(
            "A player name is too long for the database model.",
            [ImportIssue(long_player_names[0], field="player", source=source)],
        )

    for column in ("batting_team", "bowling_team", "batsman"):
        missing = frame[column].eq("")
        if missing.any():
            first_index = int(missing[missing].index[0])
            raise DataValidationError(
                f"Delivery column {column!r} contains blank required values.",
                [ImportIssue("value is required", int(first_index) + 2, column, source)],
            )
    frame["batting_team"] = frame["batting_team"].map(normalize_team_name)
    frame["bowling_team"] = frame["bowling_team"].map(normalize_team_name)

    bad_super_over = ~frame["is_super_over"].isin([0, 1])
    if bad_super_over.any():
        first_index = int(bad_super_over[bad_super_over].index[0])
        raise DataValidationError(
            "is_super_over must be 0 or 1.",
            [ImportIssue(str(frame.at[first_index, "is_super_over"]), int(first_index) + 2, "is_super_over", source)],
        )
    superover_innings = frame["inning"].ge(3)
    bad_innings = frame["inning"].lt(1) | (superover_innings & frame["inning"].gt(10))
    if bad_innings.any():
        first_index = int(bad_innings[bad_innings].index[0])
        raise DataValidationError(
            "Delivery innings must be 1/2, with repeated super overs after that.",
            [ImportIssue(str(frame.at[first_index, "inning"]), int(first_index) + 2, "inning", source)],
        )
    if superover_innings.any():
        # A few canonical Kaggle releases mark super-over innings with a zero
        # ``is_super_over`` flag.  The innings number is authoritative here.
        summary.warnings.append(
            f"{int(superover_innings.sum())} super-over delivery row(s) were retained for player stats and excluded from regulation scorecards."
        )
    frame["source_inning"] = frame["inning"]

    match_ids = set(matches["external_id"].tolist())
    orphan = ~frame["external_id"].isin(match_ids)
    if orphan.any():
        examples = frame.loc[orphan, "external_id"].head(5).astype(int).tolist()
        first_index = int(orphan[orphan].index[0])
        raise DataValidationError(
            "The deliveries CSV references match IDs absent from matches.csv.",
            [
                ImportIssue(
                    "unknown match IDs include " + ", ".join(map(str, examples)),
                    int(first_index) + 2,
                    "match_id",
                    source,
                )
            ],
        )

    match_team_names = {
        int(row.external_id): {
            _identity_key(row.team1_name): row.team1_name,
            _identity_key(row.team2_name): row.team2_name,
        }
        for row in matches.itertuples(index=False)
    }
    for index, delivery in frame.iterrows():
        match_id = int(delivery["external_id"])
        canonical_names = match_team_names[match_id]
        batting_name = canonical_names.get(_identity_key(delivery["batting_team"]))
        bowling_name = canonical_names.get(_identity_key(delivery["bowling_team"]))
        if batting_name is None or bowling_name is None:
            raise DataValidationError(
                "A delivery team did not play in its referenced match.",
                [
                    ImportIssue(
                        f"{delivery['batting_team']} vs {delivery['bowling_team']}",
                        int(index) + 2,
                        "batting_team/bowling_team",
                        source,
                    )
                ],
            )
        frame.at[index, "batting_team"] = batting_name
        frame.at[index, "bowling_team"] = bowling_name
        if batting_name == bowling_name:
            raise DataValidationError(
                "A delivery has the same batting and bowling team.",
                [
                    ImportIssue(
                        batting_name,
                        int(index) + 2,
                        "batting_team",
                        source,
                    )
                ],
            )

    if superover_innings.any():
        regulation_innings = (
            frame.loc[~superover_innings, ["external_id", "inning", "batting_team"]]
            .drop_duplicates(["external_id", "batting_team"])
            .set_index(["external_id", "batting_team"])["inning"]
            .to_dict()
        )
        for index in frame.index[superover_innings]:
            key = (int(frame.at[index, "external_id"]), frame.at[index, "batting_team"])
            regulation_inning = regulation_innings.get(key)
            if regulation_inning not in {1, 2}:
                raise DataValidationError(
                    "A super-over batting team has no regulation innings.",
                    [
                        ImportIssue(
                            f"match {key[0]}, team {key[1]}",
                            int(index) + 2,
                            "inning",
                            source,
                        )
                    ],
                )
            frame.at[index, "inning"] = regulation_inning

    # The explicit extra and total columns, when present, must agree.  If total
    # is absent it is derived from batter plus extras.
    if had_total_runs and had_extra_runs:
        inconsistent = frame["total_runs"] != frame["batsman_runs"] + frame["extra_runs"]
        if inconsistent.any():
            first_index = int(inconsistent[inconsistent].index[0])
            raise DataValidationError(
                "Delivery total_runs does not equal batsman_runs plus extra_runs.",
                [ImportIssue("inconsistent totals", int(first_index) + 2, "total_runs", source)],
            )
    elif not had_total_runs:
        frame["total_runs"] = frame["batsman_runs"] + frame["extra_runs"]
    invalid_total = frame["total_runs"] < frame["batsman_runs"]
    if invalid_total.any():
        first_index = int(invalid_total[invalid_total].index[0])
        raise DataValidationError(
            "A delivery has fewer total runs than batter runs.",
            [ImportIssue("total_runs is smaller than batsman_runs", int(first_index) + 2, "total_runs", source)],
        )
    frame["extras"] = frame["total_runs"] - frame["batsman_runs"]

    # First remove byte-for-byte duplicate source rows.  A repeated delivery
    # coordinate with different statistics is corruption, not a harmless replay.
    duplicate_columns = [
        column
        for column in (
            "external_id",
            "inning",
            "source_inning",
            "batting_team",
            "bowling_team",
            "over",
            "ball",
            "is_super_over",
            "batsman",
            "non_striker",
            "bowler",
            "batsman_runs",
            "total_runs",
            "player_dismissed",
            "dismissal_kind",
            "fielder",
        )
    ]
    duplicate_rows = frame.duplicated(subset=duplicate_columns, keep="first")
    summary.duplicate_delivery_rows += int(duplicate_rows.sum())
    frame = frame.loc[~duplicate_rows].copy()
    coordinate_duplicates = frame.duplicated(
        subset=("external_id", "source_inning", "over", "ball", "is_super_over"), keep="last"
    )
    if coordinate_duplicates.any():
        # The canonical Kaggle export contains a small number of corrected
        # duplicate coordinates (the replacement row is appended later).  Keep
        # the final source value deterministically instead of double-counting.
        summary.duplicate_delivery_rows += int(coordinate_duplicates.sum())
        frame = frame.loc[~coordinate_duplicates].copy()
    if frame.empty:
        raise DataValidationError(
            "The deliveries CSV contains no usable delivery rows.",
            [ImportIssue("all rows were empty or duplicates", source=source)],
        )

    frame["boundary_four"] = frame["batsman_runs"].eq(4)
    frame["boundary_six"] = frame["batsman_runs"].eq(6)
    frame["legal_ball"] = frame["wide_runs"].eq(0) & frame["noball_runs"].eq(0)
    # A batter faces a no-ball, but not a wide or a delivery completed entirely
    # as byes/leg-byes. Keep this separate from ``legal_ball`` because no-balls
    # do not consume an over.
    frame["faced_ball"] = frame["wide_runs"].eq(0) & (
        frame["batsman_runs"].gt(0)
        | (frame["bye_runs"].eq(0) & frame["legbye_runs"].eq(0))
    )
    dismissal_key = frame["dismissal_kind"].map(_identity_key)
    frame["valid_wicket"] = frame["player_dismissed"].ne("") & ~dismissal_key.isin(
        NON_WICKET_DISMISSALS
    )
    frame["bowler_wicket"] = (
        frame["valid_wicket"]
        & frame["bowler"].ne("")
        & ~dismissal_key.isin(NON_BOWLER_WICKETS)
    )
    frame["caught"] = dismissal_key.str.startswith("caught") & frame["fielder"].ne("")
    frame["bowling_runs"] = (
        frame["total_runs"] - frame["bye_runs"] - frame["legbye_runs"] - frame["penalty_runs"]
    )
    invalid_conceded = frame["bowling_runs"] < 0
    if invalid_conceded.any():
        first_index = int(invalid_conceded[invalid_conceded].index[0])
        raise DataValidationError(
            "A delivery has negative bowler runs conceded.",
            [ImportIssue("check bye, leg-bye and penalty runs", int(first_index) + 2, "bowling_runs", source)],
        )

    summary.delivery_rows = len(frame)
    summary.matches_with_deliveries = int(frame["external_id"].nunique())
    return frame


def _aggregate_innings(deliveries: pd.DataFrame) -> pd.DataFrame:
    # Super-over runs are not part of the regulation team score.  The shared
    # MatchInnings model intentionally has only innings 1/2, so keep those
    # deliveries in player aggregates but omit them from scorecards.
    regulation = deliveries[
        (deliveries["source_inning"] <= 2) & (deliveries["is_super_over"] == 0)
    ]
    if regulation.empty:
        return pd.DataFrame(
            columns=["external_id", "inning", "batting_team", "runs", "wickets", "balls", "extras"]
        )
    grouped = regulation.groupby(
        ["external_id", "inning", "batting_team"], as_index=False, sort=False
    ).agg(
        runs=("total_runs", "sum"),
        wickets=("valid_wicket", "sum"),
        balls=("legal_ball", "sum"),
        extras=("extras", "sum"),
    )
    sides = regulation.groupby(["external_id", "inning"])["batting_team"].nunique()
    if (sides > 1).any():
        key = sides[sides > 1].index[0]
        raise DataValidationError(
            "Two batting teams share one innings number.",
            [ImportIssue(f"match {int(key[0])}, innings {int(key[1])}")],
        )
    grouped["wickets"] = grouped["wickets"].astype("int64")
    grouped["balls"] = grouped["balls"].astype("int64")
    grouped["extras"] = grouped["extras"].astype("int64")
    return grouped


def _empty_performance_columns() -> list[str]:
    return [
        "runs",
        "balls",
        "fours",
        "sixes",
        "wickets",
        "bowling_balls",
        "runs_conceded",
        "catches",
        "dismissals",
        "highest_score",
    ]


def _aggregate_player_performances(
    deliveries: pd.DataFrame,
    matches: pd.DataFrame,
) -> pd.DataFrame:
    group_keys = ["external_id", "batsman"]
    # First total each individual innings. This makes highest_score an actual
    # innings score rather than the best single delivery.
    batting_innings = deliveries[deliveries["batsman"].ne("")].groupby(
        ["external_id", "inning", "batsman"], as_index=False, sort=False
    ).agg(
        innings_runs=("batsman_runs", "sum"),
        innings_balls=("faced_ball", "sum"),
        innings_fours=("boundary_four", "sum"),
        innings_sixes=("boundary_six", "sum"),
        batting_team_name=("batting_team", "first"),
        batting_rows=("batsman", "size"),
    )
    batting = batting_innings.groupby(group_keys, as_index=False, sort=False).agg(
        runs=("innings_runs", "sum"),
        balls=("innings_balls", "sum"),
        fours=("innings_fours", "sum"),
        sixes=("innings_sixes", "sum"),
        highest_score=("innings_runs", "max"),
        batting_team_name=("batting_team_name", "first"),
        batting_rows=("batting_rows", "sum"),
    )

    # A substitute/non-striker can appear in a match without being the striker
    # on a delivery. Preserve that participation as a batting appearance.
    non_striker_presence = (
        deliveries[deliveries["non_striker"].ne("")]
        .groupby(["external_id", "non_striker"], as_index=False, sort=False)
        .size()
        .rename(columns={"non_striker": "batsman", "size": "non_striker_rows"})
    )
    batting_presence = pd.concat(
        [
            batting[["external_id", "batsman"]].assign(batting_present=1),
            non_striker_presence[["external_id", "batsman"]].assign(batting_present=1),
        ],
        ignore_index=True,
    ).groupby(["external_id", "batsman"], as_index=False, sort=False).agg(
        batting_appeared=("batting_present", "max")
    )

    # Wickets are credited to the bowler on the delivery, while dismissals are
    # credited to the dismissed batter. Keeping these as separate aggregates is
    # essential for correct bowling and batting-average calculations.
    bowling = deliveries[deliveries["bowler"].ne("")].groupby(
        ["external_id", "bowler"], as_index=False, sort=False
    ).agg(
        bowling_balls=("legal_ball", "sum"),
        runs_conceded=("bowling_runs", "sum"),
        bowling_team_name=("bowling_team", "first"),
        bowling_appeared=("bowler", "size"),
    ).rename(columns={"bowler": "batsman"})
    bowling_wickets = deliveries[deliveries["bowler_wicket"]].groupby(
        ["external_id", "bowler"], as_index=False, sort=False
    ).agg(wickets=("bowler_wicket", "sum")).rename(columns={"bowler": "batsman"})
    bowling = bowling.merge(
        bowling_wickets, on=["external_id", "batsman"], how="left", validate="one_to_one"
    )
    if "wickets" not in bowling:
        bowling["wickets"] = 0
    bowling["wickets"] = bowling["wickets"].fillna(0).astype("int64")

    dismissed = deliveries[deliveries["valid_wicket"]].groupby(
        ["external_id", "player_dismissed"], as_index=False, sort=False
    ).agg(dismissals=("valid_wicket", "sum")).rename(
        columns={"player_dismissed": "batsman"}
    )

    catches = deliveries[deliveries["caught"]].groupby(
        ["external_id", "fielder"], as_index=False, sort=False
    ).agg(catches=("caught", "sum")).rename(columns={"fielder": "batsman"})

    participant_frames: list[pd.DataFrame] = []
    participant_specs = (
        ("batsman", "batting_team", 0),
        ("non_striker", "batting_team", 0),
        ("bowler", "bowling_team", 1),
        ("player_dismissed", "batting_team", 2),
        ("fielder", "bowling_team", 2),
    )
    for player_column, team_column, priority in participant_specs:
        subset = deliveries[
            deliveries[player_column].ne("")
            & (deliveries["valid_wicket"] if player_column == "player_dismissed" else True)
        ][["external_id", player_column, team_column]].drop_duplicates()
        subset = subset.rename(
            columns={player_column: "batsman", team_column: "team_name"}
        )
        subset["priority"] = priority
        participant_frames.append(subset)

    participants = pd.concat(participant_frames, ignore_index=True)
    if participants.empty:
        return pd.DataFrame(
            columns=[
                "external_id",
                "batsman",
                "team_name",
                "season",
                "batting_appeared",
                "bowling_appeared",
                *_empty_performance_columns(),
            ]
        )
    participants = participants.sort_values(
        ["external_id", "batsman", "priority", "team_name"]
    ).drop_duplicates(["external_id", "batsman"], keep="first")
    participants = participants.rename(columns={"batsman": "player_name"}).drop(
        columns="priority"
    )

    performance = participants
    for part in (batting_presence, batting, bowling, dismissed, catches):
        part = part.rename(columns={"batsman": "player_name"})
        performance = performance.merge(
            part, on=("external_id", "player_name"), how="left", validate="one_to_one"
        )
    for column in _empty_performance_columns():
        if column not in performance:
            performance[column] = 0
        performance[column] = performance[column].fillna(0).astype("int64")
    for column in ("batting_appeared", "bowling_appeared"):
        performance[column] = performance[column].fillna(0).astype("int64")
    for column in ("batting_team_name", "bowling_team_name"):
        if column not in performance:
            performance[column] = ""
        else:
            performance[column] = performance[column].fillna("")

    primary_team = performance["batting_team_name"].replace("", pd.NA).combine_first(
        performance["bowling_team_name"].replace("", pd.NA)
    )
    performance["team_name"] = primary_team.fillna(performance["team_name"])
    season_lookup = matches[["external_id", "season"]].rename(
        columns={"external_id": "external_id"}
    )
    performance = performance.merge(
        season_lookup, on="external_id", how="left", validate="many_to_one"
    )
    return performance.drop(
        columns=("batting_team_name", "bowling_team_name"), errors="ignore"
    )


def _aggregate_season_performances(performances: pd.DataFrame) -> pd.DataFrame:
    if performances.empty:
        return pd.DataFrame(
            columns=[
                "player_name",
                "team_name",
                "season",
                "appearances",
                "batting_innings",
                "runs",
                "balls",
                "fours",
                "sixes",
                "dismissals",
                "highest_score",
                "bowling_innings",
                "bowling_balls",
                "wickets",
                "runs_conceded",
                "catches",
            ]
        )
    grouped = performances.copy()
    grouped["batting_innings"] = grouped["batting_appeared"].gt(0).astype("int64")
    grouped["bowling_innings"] = grouped["bowling_appeared"].gt(0).astype("int64")
    grouped = grouped.groupby(
        ["player_name", "team_name", "season"], as_index=False, sort=False
    ).agg(
        appearances=("external_id", "nunique"),
        batting_innings=("batting_innings", "sum"),
        runs=("runs", "sum"),
        balls=("balls", "sum"),
        fours=("fours", "sum"),
        sixes=("sixes", "sum"),
        dismissals=("dismissals", "sum"),
        highest_score=("highest_score", "max"),
        bowling_innings=("bowling_innings", "sum"),
        bowling_balls=("bowling_balls", "sum"),
        wickets=("wickets", "sum"),
        runs_conceded=("runs_conceded", "sum"),
        catches=("catches", "sum"),
    )
    return grouped


def _chunks(values: list[Any], size: int = 900) -> Iterable[list[Any]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _team_cache() -> dict[str, Team]:
    return {team.name.casefold(): team for team in Team.objects.order_by("pk")}


def _team_brand_defaults(name: str) -> dict[str, str]:
    """Return the short code and primary colour to seed a team row with."""

    brand = brand_for(name)
    return {
        "short_name": TEAM_SHORT_NAMES.get(name, brand.short_name),
        "primary_color": brand.primary_color,
    }


def _get_or_create_team(cache: dict[str, Team], name: str) -> Team:
    key = name.casefold()
    team = cache.get(key)
    if team is not None:
        return team

    # Upgrade a previously imported historical alias when it is unambiguous.
    historical_aliases = [alias for alias, canonical in TEAM_ALIASES.items() if canonical == name]
    if historical_aliases:
        alias_names = [TEAM_ALIASES.get(alias, alias) for alias in historical_aliases]
        alias_names.extend(
            alias.title() for alias in historical_aliases if alias not in alias_names
        )
        team = Team.objects.filter(name__in=alias_names).order_by("pk").first()
        if team is not None and team.name != name:
            team.name = name
            defaults = _team_brand_defaults(name)
            team.short_name = team.short_name or defaults["short_name"]
            team.save(update_fields=["name", "short_name"])
    if team is None:
        team = Team.objects.filter(name__iexact=name).order_by("pk").first()
    if team is None:
        team = Team.objects.create(name=name, **_team_brand_defaults(name))

    # Backfill branding for rows imported before the brand table existed.  The
    # guard keeps the placeholder colour once a club has chosen a real one.
    defaults = _team_brand_defaults(team.name)
    updates: list[str] = []
    if not team.short_name and defaults["short_name"]:
        team.short_name = defaults["short_name"]
        updates.append("short_name")
    if team.primary_color == "#6C63FF" and defaults["primary_color"] != "#6C63FF":
        team.primary_color = defaults["primary_color"]
        updates.append("primary_color")
    if updates:
        team.save(update_fields=updates)

    cache[key] = team
    return team


def _venue_cache() -> dict[str, Venue]:
    return {venue.name.casefold(): venue for venue in Venue.objects.order_by("pk")}


def _get_or_create_venue(cache: dict[str, Venue], name: str, city: str) -> Venue:
    key = name.casefold()
    venue = cache.get(key)
    if venue is None:
        venue = Venue.objects.filter(name__iexact=name).order_by("pk").first()
    if venue is None:
        alias_keys = [alias for alias, canonical in VENUE_ALIASES.items() if canonical == name]
        alias_names = [VENUE_ALIASES.get(alias, alias) for alias in alias_keys]
        alias_names.extend(alias.title() for alias in alias_keys)
        if alias_names:
            venue = Venue.objects.filter(name__in=alias_names).order_by("pk").first()
    if venue is None:
        venue = Venue.objects.create(name=name, city=city)
        cache[key] = venue
    elif venue.name != name:
        venue.name = name
        if city:
            venue.city = city
        venue.save(update_fields=["name", "city"])
    elif city and not venue.city:
        venue.city = city
        venue.save(update_fields=["city"])
    return venue


def _player_cache() -> dict[str, Player]:
    cache: dict[str, Player] = {}
    for player in Player.objects.order_by("pk"):
        cache.setdefault(player.name.casefold(), player)
    return cache


def _get_or_create_player(cache: dict[str, Player], name: str) -> Player:
    if not name:
        raise DataValidationError("A player name is blank.")
    name = normalize_player_name(name)
    key = name.casefold()
    player = cache.get(key)
    if player is None:
        player = Player.objects.filter(name__iexact=name).order_by("pk").first()
        if player is None:
            player = Player.objects.create(name=name)
        cache[key] = player
    return player


# Match-level columns that add up when two spellings of the same player are
# merged for one match.  ``highest_score`` is a maximum rather than a sum.
_SUMMED_PERFORMANCE_FIELDS = (
    "runs",
    "balls",
    "fours",
    "sixes",
    "wickets",
    "bowling_balls",
    "runs_conceded",
    "catches",
)


def _backfill_team_branding() -> int:
    """Seed ``short_name`` and ``primary_color`` for every known franchise.

    Run as a standalone pass because :func:`_get_or_create_team` returns early
    on a cache hit, which would otherwise skip rows imported before the brand
    table existed.  A team's colour is only overwritten while it still holds the
    old placeholder, so a club can pick its own colour in the admin.
    """

    updated = 0
    for team in Team.objects.all():
        defaults = _team_brand_defaults(team.name)
        changes: list[str] = []
        if not team.short_name and defaults["short_name"]:
            team.short_name = defaults["short_name"]
            changes.append("short_name")
        if team.primary_color == "#6C63FF" and defaults["primary_color"] != "#6C63FF":
            team.primary_color = defaults["primary_color"]
            changes.append("primary_color")
        if changes:
            team.save(update_fields=changes)
            updated += 1
    return updated


def _reconcile_legacy_player_duplicates() -> int:
    """Fold rows whose stored name is no longer canonical into one player.

    Two cases are handled:

    * Substitute labels - earlier versions stored ``"AB de Villiers (sub)"``
      alongside ``"AB de Villiers"``, splitting one career across two records.
    * Corrected spellings - an alias fix (``"VVS Laxman"`` ->
      ``"VVS Lakshman"``) would otherwise create a second row instead of
      renaming the existing one.

    Every match-level performance is reassigned to the surviving player.  Season
    rollups are *not* rebuilt here: the caller re-derives them from the full
    delivery source later in the same import, which also restores the dismissal
    counts that are not stored on the match-level rows.

    Returns the number of duplicate rows that were removed.
    """

    removed = 0
    candidates = [
        player
        for player in Player.objects.order_by("pk")
        if normalize_player_name(player.name) != player.name
    ]
    for duplicate in candidates:
        canonical_name = normalize_player_name(duplicate.name)
        if not canonical_name:
            continue
        canonical = (
            Player.objects.filter(name__iexact=canonical_name)
            .exclude(pk=duplicate.pk)
            .order_by("pk")
            .first()
        )
        if canonical is None:
            # Only the suffixed spelling exists (a player who never appeared
            # under the main team sheet) - simply clean the name.
            if duplicate.name != canonical_name:
                duplicate.name = canonical_name
                duplicate.save(update_fields=["name"])
            continue

        # Reassign every match-level row onto the canonical player.
        #
        # ``unique_player_match_performance`` forbids two rows for the same
        # player and match, so a naive ``.update(player=canonical)`` raises
        # IntegrityError whenever both spellings recorded that match.  When a
        # collision occurs the two rows are combined instead of dropping data.
        for stray in PlayerPerformance.objects.filter(player=duplicate):
            existing = PlayerPerformance.objects.filter(
                player=canonical, match=stray.match
            ).first()
            if existing is None:
                stray.player = canonical
                stray.save(update_fields=["player"])
                continue
            for field in _SUMMED_PERFORMANCE_FIELDS:
                setattr(existing, field, getattr(existing, field) + getattr(stray, field))
            existing.highest_score = max(existing.highest_score, stray.highest_score)
            existing.save(
                update_fields=[*_SUMMED_PERFORMANCE_FIELDS, "highest_score"]
            )
            stray.delete()

        PlayerSeasonPerformance.objects.filter(player=duplicate).delete()

        # Merge historical affiliations, keeping the widest season range.
        for affiliation in PlayerTeam.objects.filter(player=duplicate):
            existing = PlayerTeam.objects.filter(
                player=canonical, team=affiliation.team
            ).first()
            if existing is None:
                affiliation.player = canonical
                affiliation.save(update_fields=["player"])
                continue
            seasons = [
                value
                for value in (existing.first_season, affiliation.first_season)
                if value is not None
            ]
            last_seasons = [
                value
                for value in (existing.last_season, affiliation.last_season)
                if value is not None
            ]
            existing.first_season = min(seasons) if seasons else None
            existing.last_season = max(last_seasons) if last_seasons else None
            existing.appearances += affiliation.appearances
            existing.save(
                update_fields=["first_season", "last_season", "appearances"]
            )
            affiliation.delete()

        # Prefer richer metadata when the canonical row is missing it.
        updates: list[str] = []
        if not canonical.current_team_id and duplicate.current_team_id:
            canonical.current_team_id = duplicate.current_team_id
            updates.append("current_team")
        if canonical.role == Player.Role.UNKNOWN and duplicate.role != Player.Role.UNKNOWN:
            canonical.role = duplicate.role
            updates.append("role")
        if not canonical.nationality and duplicate.nationality:
            canonical.nationality = duplicate.nationality
            updates.append("nationality")
        if updates:
            canonical.save(update_fields=updates)

        duplicate.delete()
        removed += 1
    return removed


def _delivery_player_names(deliveries: pd.DataFrame, matches: pd.DataFrame) -> set[str]:
    names: set[str] = set()
    for column in ("batsman", "non_striker", "bowler", "player_dismissed", "fielder"):
        names.update(
            normalize_player_name(value)
            for value in deliveries[column].unique().tolist()
            if value
        )
    names.update(
        normalize_player_name(value)
        for value in matches["player_of_match"].unique().tolist()
        if value
    )
    names.discard("")
    return names


def _rebuild_player_aggregates(
    *,
    source_team_by_performance: dict[tuple[int, int], int],
    source_dismissals: set[tuple[int, int]],
    source_batting_appearances: set[tuple[int, int]],
    source_bowling_appearances: set[tuple[int, int]],
) -> tuple[int, int]:
    season_teams: dict[tuple[int, int], set[int]] = {}
    match_seasons = dict(Match.objects.values_list("id", "season"))
    for (player_id, match_id), team_id in source_team_by_performance.items():
        season_teams.setdefault((player_id, match_seasons[match_id]), set()).add(team_id)
    season_hint = {
        key: next(iter(team_ids))
        for key, team_ids in season_teams.items()
        if len(team_ids) == 1
    }

    aggregates: dict[tuple[int, int, int], dict[str, Any]] = {}
    latest_team: dict[int, tuple[date, int, int]] = {}
    player_roles: dict[int, set[str]] = {}
    current_teams = dict(Player.objects.values_list("id", "current_team_id"))

    queryset = PlayerPerformance.objects.select_related("player", "match").only(
        "player_id",
        "player__name",
        "player__role",
        "player__current_team_id",
        "match_id",
        "match__date",
        "match__season",
        "season",
        "runs",
        "balls",
        "fours",
        "sixes",
        "wickets",
        "bowling_balls",
        "runs_conceded",
        "catches",
        "highest_score",
    )
    for performance in queryset.iterator(chunk_size=2_000):
        key = (performance.player_id, performance.match_id)
        team_id = source_team_by_performance.get(key)
        if team_id is None:
            team_id = season_hint.get((performance.player_id, performance.season))
        if team_id is None:
            team_id = current_teams.get(performance.player_id)
        if team_id is None:
            continue
        aggregate_key = (performance.player_id, team_id, performance.season)
        aggregate = aggregates.setdefault(
            aggregate_key,
            {
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
        aggregate["appearances"] += 1
        aggregate["runs"] += performance.runs
        aggregate["balls"] += performance.balls
        aggregate["fours"] += performance.fours
        aggregate["sixes"] += performance.sixes
        aggregate["highest_score"] = max(aggregate["highest_score"], performance.highest_score)
        aggregate["bowling_balls"] += performance.bowling_balls
        aggregate["wickets"] += performance.wickets
        aggregate["runs_conceded"] += performance.runs_conceded
        aggregate["catches"] += performance.catches
        if key in source_batting_appearances or performance.balls > 0:
            aggregate["batting_innings"] += 1
            player_roles.setdefault(performance.player_id, set()).add("bat")
        if key in source_bowling_appearances or performance.bowling_balls > 0 or performance.wickets > 0:
            aggregate["bowling_innings"] += 1
            player_roles.setdefault(performance.player_id, set()).add("bowl")
        if key in source_dismissals:
            aggregate["dismissals"] += 1

        date_value = performance.match.date
        previous = latest_team.get(performance.player_id)
        candidate = (date_value, performance.match.season, team_id)
        if previous is None or candidate[:2] >= previous[:2]:
            latest_team[performance.player_id] = candidate

    season_rows = [
        PlayerSeasonPerformance(
            player_id=player_id,
            team_id=team_id,
            season=season,
            **values,
        )
        for (player_id, team_id, season), values in aggregates.items()
    ]
    if season_rows:
        PlayerSeasonPerformance.objects.bulk_create(
            season_rows,
            update_conflicts=True,
            update_fields=[
                "appearances",
                "batting_innings",
                "runs",
                "balls",
                "fours",
                "sixes",
                "dismissals",
                "highest_score",
                "bowling_innings",
                "bowling_balls",
                "wickets",
                "runs_conceded",
                "catches",
            ],
            unique_fields=["player", "team", "season"],
            batch_size=2_000,
        )

    affiliation_values: dict[tuple[int, int], dict[str, Any]] = {}
    for row in season_rows:
        key = (row.player_id, row.team_id)
        affiliation = affiliation_values.setdefault(
            key,
            {
                "first_season": row.season,
                "last_season": row.season,
                "appearances": 0,
            },
        )
        affiliation["first_season"] = min(affiliation["first_season"], row.season)
        affiliation["last_season"] = max(affiliation["last_season"], row.season)
        affiliation["appearances"] += row.appearances
    affiliations = [
        PlayerTeam(player_id=player_id, team_id=team_id, **values)
        for (player_id, team_id), values in affiliation_values.items()
    ]
    if affiliations:
        PlayerTeam.objects.bulk_create(
            affiliations,
            update_conflicts=True,
            update_fields=["first_season", "last_season", "appearances"],
            unique_fields=["player", "team"],
            batch_size=2_000,
        )

    for player in Player.objects.filter(id__in=latest_team):
        inferred = player_roles.get(player.id, set())
        role = (
            Player.Role.ALL_ROUNDER
            if inferred == {"bat", "bowl"}
            else Player.Role.BATTER
            if inferred == {"bat"}
            else Player.Role.BOWLER
            if inferred == {"bowl"}
            else Player.Role.UNKNOWN
        )
        player.current_team_id = latest_team[player.id][2]
        if player.role == Player.Role.UNKNOWN and role != Player.Role.UNKNOWN:
            player.role = role
        player.save(update_fields=["current_team", "role"])
    return len(season_rows), len(affiliations)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def import_manifest_path() -> Path:
    """Return the location the import fingerprint is published to.

    Kept as a function (rather than a module constant) so the test suite can
    patch it.  Importing a small fixture would otherwise overwrite the real
    fingerprint with temporary paths, which made the launcher believe the real
    dataset was stale and re-import all 756 matches on every start.
    """

    return Path(settings.BASE_DIR) / "data" / "processed" / "ipl_import_manifest.json"


def _write_import_manifest(
    match_path: Path,
    delivery_path: Path,
    summary: ImportSummary,
) -> None:
    """Publish a non-authoritative source fingerprint after DB commit."""

    directory = import_manifest_path().parent
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "ipl_import_manifest.json"
    temporary = path.with_suffix(".tmp")
    payload = {
        "schema_version": 1,
        # Bumped to 4: player names are now normalised (substitute suffixes are
        # folded into the canonical record) and team branding is backfilled.
        "importer_version": 4,
        "status": "complete",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "matches_sha256": _sha256_file(match_path),
        "deliveries_sha256": _sha256_file(delivery_path),
        "matches_imported": summary.matches_imported,
        "performances_imported": summary.performances_imported,
        "season_rows_imported": summary.season_performances_imported,
        "source_files": summary.source_files,
    }
    with temporary.open("w", encoding="utf-8") as destination:
        json.dump(payload, destination, indent=2, sort_keys=True)
        destination.write("\n")
    temporary.replace(path)


def _infer_missing_match_stages(matches: pd.DataFrame) -> pd.DataFrame:
    """Infer final/playoff stages for canonical files without a stage column."""

    frame = matches.copy()
    for season in sorted(frame["season"].dropna().unique()):
        season_mask = frame["season"].eq(season)
        season_rows = frame.loc[season_mask].sort_values(["date", "external_id"])
        # If this season already has an explicit stage, trust the source.
        if season_rows["match_type"].astype(str).isin(
            [Match.MatchType.FINAL, Match.MatchType.PLAYOFF]
        ).any():
            continue
        count = len(season_rows)
        if count == 0:
            continue
        playoff_count = 3 if int(season) in {2008, 2009} else 4
        playoff_count = min(playoff_count, count)
        stage_indices = season_rows.index[-playoff_count:]
        frame.loc[stage_indices, "match_type"] = Match.MatchType.PLAYOFF
        frame.loc[stage_indices[-1], "match_type"] = Match.MatchType.FINAL
    return frame


class IPLDataImporter:
    """Discover, validate and atomically import canonical IPL CSV data."""

    def __init__(self, data_dir: str | Path | None = None):
        self.data_dir = data_dir

    def run(self, *, replace: bool = False) -> ImportSummary:
        match_path, delivery_path = discover_ipl_data_files(self.data_dir)
        summary = ImportSummary(
            source_files={"matches": str(match_path), "deliveries": str(delivery_path)},
            replaced=replace,
        )
        raw_matches = _read_csv(match_path)
        raw_deliveries = _read_csv(delivery_path)
        matches = _prepare_matches(raw_matches, source=str(match_path), summary=summary)
        matches = _infer_missing_match_stages(matches)
        deliveries = _prepare_deliveries(
            raw_deliveries, matches, source=str(delivery_path), summary=summary
        )
        innings = _aggregate_innings(deliveries)
        performances = _aggregate_player_performances(deliveries, matches)

        with transaction.atomic():
            if replace:
                Match.objects.all().delete()
                Player.objects.all().delete()
                Team.objects.all().delete()
                Venue.objects.all().delete()

            teams = _team_cache()
            venues = _venue_cache()
            players = _player_cache()
            # Brand every franchise, then fold legacy "(sub)" rows and
            # corrected spellings into their canonical player.  Both run before
            # the caches are used so every performance below lands on the single
            # surviving record.
            _backfill_team_branding()
            _reconcile_legacy_player_duplicates()
            teams = _team_cache()
            players = _player_cache()
            player_names = _delivery_player_names(deliveries, matches)
            for name in sorted(player_names):
                _get_or_create_player(players, name)
            summary.players_imported = len(player_names)

            match_by_external: dict[int, Match] = {}
            for values in matches.to_dict("records"):
                team1 = _get_or_create_team(teams, values["team1_name"])
                team2 = _get_or_create_team(teams, values["team2_name"])
                venue = _get_or_create_venue(
                    venues, values["venue_name"], values["city"]
                )
                toss_winner = (
                    _get_or_create_team(teams, values["toss_winner_name"])
                    if values["toss_winner_name"]
                    else None
                )
                winner = (
                    _get_or_create_team(teams, values["winner_name"])
                    if values["winner_name"]
                    else None
                )
                player_of_match = (
                    _get_or_create_player(players, values["player_of_match"])
                    if values["player_of_match"]
                    else None
                )
                existing_match = Match.objects.filter(
                    external_id=values["external_id"]
                ).first()
                if existing_match is not None and not replace:
                    existing_identity = (
                        existing_match.season,
                        existing_match.date,
                        existing_match.team1_id,
                        existing_match.team2_id,
                        existing_match.venue_id,
                    )
                    incoming_identity = (
                        values["season"],
                        values["date"],
                        team1.id,
                        team2.id,
                        venue.id,
                    )
                    if existing_identity != incoming_identity:
                        raise DataValidationError(
                            "An external match ID collides with a different existing match.",
                            [
                                ImportIssue(
                                    f"match id {values['external_id']} already belongs to another date/team/venue",
                                    field="external_id",
                                    source="matches.csv",
                                )
                            ],
                        )
                defaults = {
                    "season": values["season"],
                    "date": values["date"],
                    "city": values["city"],
                    "venue": venue,
                    "team1": team1,
                    "team2": team2,
                    "toss_winner": toss_winner,
                    "toss_decision": values["toss_decision"],
                    "winner": winner,
                    "result_type": values["result_type"],
                    "result": values["result"],
                    "dismissal_method": values["dismissal_method"],
                    "player_of_match": player_of_match,
                    "match_type": values["match_type"],
                    "target_runs": _nullable_number(values["target_runs"], int),
                    "target_overs": _nullable_number(values["target_overs"], lambda item: item),
                    "win_by_runs": _nullable_number(values["win_by_runs"], int),
                    "win_by_wickets": _nullable_number(values["win_by_wickets"], int),
                    "dl_applied": values["dl_applied"],
                    "umpires": values["umpires"],
                }
                match, created = Match.objects.update_or_create(
                    external_id=values["external_id"], defaults=defaults
                )
                match_by_external[values["external_id"]] = match
                if created:
                    summary.matches_created += 1
                else:
                    summary.matches_updated += 1
            summary.matches_imported = len(match_by_external)
            summary.teams_imported = len(teams)
            summary.venues_imported = len(venues)
            summary.innings_imported = len(innings)
            summary.performances_imported = len(performances)

            imported_match_ids = list(match_by_external.values())
            imported_ids = [match.id for match in imported_match_ids]
            for chunk in _chunks(imported_ids):
                MatchInnings.objects.filter(match_id__in=chunk).delete()
                PlayerPerformance.objects.filter(match_id__in=chunk).delete()

            MatchInnings.objects.bulk_create(
                (
                    MatchInnings(
                        match_id=match_by_external[int(values["external_id"])].id,
                        team=_get_or_create_team(teams, str(values["batting_team"])),
                        innings=int(values["inning"]),
                        runs=int(values["runs"]),
                        wickets=int(values["wickets"]),
                        balls=int(values["balls"]),
                        extras=int(values["extras"]),
                    )
                    for values in innings.to_dict("records")
                ),
                batch_size=2_000,
            )

            source_team_by_performance: dict[tuple[int, int], int] = {}
            source_dismissals: set[tuple[int, int]] = set()
            source_batting_appearances: set[tuple[int, int]] = set()
            source_bowling_appearances: set[tuple[int, int]] = set()
            performance_objects: list[PlayerPerformance] = []
            for values in performances.to_dict("records"):
                player = _get_or_create_player(players, str(values["player_name"]))
                match = match_by_external[int(values["external_id"])]
                team = _get_or_create_team(teams, str(values["team_name"]))
                performance_objects.append(
                    PlayerPerformance(
                        player=player,
                        match=match,
                        season=int(values["season"]),
                        runs=int(values["runs"]),
                        balls=int(values["balls"]),
                        fours=int(values["fours"]),
                        sixes=int(values["sixes"]),
                        wickets=int(values["wickets"]),
                        bowling_balls=int(values["bowling_balls"]),
                        runs_conceded=int(values["runs_conceded"]),
                        catches=int(values["catches"]),
                        highest_score=int(values["highest_score"]),
                    )
                )
                key = (player.id, match.id)
                source_team_by_performance[key] = team.id
                if int(values["dismissals"]):
                    source_dismissals.add(key)
                if int(values["batting_appeared"]):
                    source_batting_appearances.add(key)
                if int(values["bowling_appeared"]):
                    source_bowling_appearances.add(key)
            PlayerPerformance.objects.bulk_create(performance_objects, batch_size=2_000)

            season_count, affiliation_count = _rebuild_player_aggregates(
                source_team_by_performance=source_team_by_performance,
                source_dismissals=source_dismissals,
                source_batting_appearances=source_batting_appearances,
                source_bowling_appearances=source_bowling_appearances,
            )
            # Persisted season rows are rebuilt from all performance rows so a
            # repeated import remains consistent.
            summary.season_performances_imported = season_count
            summary.affiliations_imported = affiliation_count
            summary.teams_imported = Team.objects.count()
            summary.venues_imported = Venue.objects.count()

        missing_delivery_matches = summary.matches_seen - summary.matches_with_deliveries
        if missing_delivery_matches:
            summary.warnings.append(
                f"{missing_delivery_matches} match(es) have no delivery rows; no scorecard was created for them."
            )
        try:
            _write_import_manifest(match_path, delivery_path, summary)
        except OSError as exc:
            summary.warnings.append(
                f"The import completed, but its source manifest could not be written: {exc}"
            )
        return summary

    # Descriptive aliases are convenient for non-command callers.
    import_data = run
    import_matches = run


def import_ipl_data(
    data_dir: str | Path | None = None, *, replace: bool = False
) -> ImportSummary:
    """Convenience wrapper for scripts that do not need an importer instance."""

    return IPLDataImporter(data_dir=data_dir).run(replace=replace)


# Naming aliases used by a few notebook/script integrations.
IPLMatchesImporter = IPLDataImporter
IPLImporter = IPLDataImporter
