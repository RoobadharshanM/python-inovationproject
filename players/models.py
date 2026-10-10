"""Player, affiliation, match and season performance models."""
import hashlib
import re
from decimal import Decimal

from django.db import models
from django.db.models import Q

from matches.models import Match, Team

# Name particles that carry no meaning in a monogram.  Without this, "AB de
# Villiers" would collapse to "ADV" instead of the familiar "AB".
NAME_PARTICLES = frozenset(
    {"al", "bin", "da", "das", "de", "del", "der", "dos", "du", "el", "la", "le", "van", "von"}
)

# Stable palette used for players who have no current team, so a given player
# always keeps the same colour between page loads.
AVATAR_PALETTE = (
    "#4D8DFF", "#8257FF", "#22D3EE", "#34D399",
    "#FB923C", "#FB7185", "#60A5FA", "#C084FC",
)


def player_initials(name: str, limit: int = 3) -> str:
    """Return a readable monogram for a cricket player's name.

    IPL scorecards store most players under an abbreviated form, so a naive
    "first two characters" rule produces broken labels such as ``"S "`` for
    ``"S Dhawan"`` (note the trailing space) and ``"VJ"`` for
    ``"VVS Laxman"``.  This helper understands the abbreviation convention:

    ============================  =======
    Name                          Monogram
    ============================  =======
    ``S Dhawan``                  ``SD``
    ``R Ashwin``                  ``RA``
    ``AB de Villiers``            ``AB``
    ``VVS Laxman``                ``VVS``
    ``Harbhajan Singh``           ``HS``
    ``Sachin Baby``               ``SB``
    ============================  =======
    """

    # Defensive: strip trailing qualifiers such as "(sub)" or "(2)" that older
    # imports left in the database.  Without this, "A Mukund (sub)" would
    # render the nonsense monogram "A(".
    text = re.sub(r"\s*\([^()]*\)\s*$", "", str(name or "")).strip()

    tokens = [
        cleaned
        for cleaned in (token.strip(" ., '-") for token in text.split())
        if cleaned
    ]
    if not tokens:
        return "?"

    # A single word: "Sachin" -> "S", "VVS" -> "VVS".
    if len(tokens) == 1:
        word = tokens[0]
        return word[:limit] if word.isupper() and len(word) > 1 else word[0].upper()

    first = tokens[0]
    # Scorecards abbreviate with runs of up to five initials, for example
    # "AB", "VVS", "SMSM", "DPMD" and "WPUJC".  A single letter is handled
    # separately below because it needs the surname to complete the monogram.
    leading_initials = first.isupper() and 2 <= len(first) <= 5

    if leading_initials:
        # Already an abbreviation ("AB", "VVS", "SMSM") - use it as-is.
        return first[:limit]

    if first.isupper() and len(first) == 1:
        # One leading initial: pair it with the final name, skipping particles
        # so "R Ashwin" -> "RA" and "I de Villiers" -> "IV".
        significant = [token for token in tokens[1:] if token.lower() not in NAME_PARTICLES]
        if significant:
            return f"{first[0]}{significant[-1][0]}".upper()[:limit]
        return first

    # Full names: first and last initial, e.g. "Harbhajan Singh" -> "HS".
    significant = [token for token in tokens if token.lower() not in NAME_PARTICLES]
    return ("".join(token[0] for token in significant) or tokens[0])[:limit].upper()


class Player(models.Model):
    class Role(models.TextChoices):
        BATTER = "batter", "Batter"
        BOWLER = "bowler", "Bowler"
        ALL_ROUNDER = "all_rounder", "All-rounder"
        WICKETKEEPER = "wicketkeeper", "Wicketkeeper-batter"
        UNKNOWN = "unknown", "Unknown"

    name = models.CharField(max_length=140, unique=True)
    current_team = models.ForeignKey(
        Team,
        on_delete=models.SET_NULL,
        related_name="current_players",
        null=True,
        blank=True,
    )
    role = models.CharField(
        max_length=16, choices=Role.choices, default=Role.UNKNOWN
    )
    nationality = models.CharField(max_length=80, blank=True)
    image = models.ImageField(
        upload_to="player-photos/",
        blank=True,
        null=True,
        help_text="Optional portrait. Leave blank to use the generated initials avatar.",
    )
    photo_url = models.URLField(
        blank=True,
        help_text="Optional remote portrait URL, used when no file is uploaded.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["name"], name="player_name_idx")]

    def __str__(self):
        return self.name

    @property
    def initials(self) -> str:
        """Uppercase monogram used by the generated avatar."""

        return player_initials(self.name)

    @property
    def portrait(self) -> str:
        """Best available portrait URL, or an empty string for the fallback.

        An uploaded file always wins over a remote URL so a club can override a
        stale link without editing the database again.
        """

        if self.image:
            try:
                return self.image.url
            except ValueError:  # pragma: no cover - unsaved/missing file
                pass
        return self.photo_url or ""

    @property
    def has_portrait(self) -> bool:
        return bool(self.portrait)

    @property
    def avatar_color(self) -> str:
        """Accent colour for the generated avatar.

        Uses the current team's primary colour when known, otherwise a stable
        colour derived from the name so the same player always looks the same.
        """

        if self.current_team_id and self.current_team.primary_color:
            return self.current_team.primary_color
        digest = hashlib.sha256(self.name.encode("utf-8")).digest()
        return AVATAR_PALETTE[digest[0] % len(AVATAR_PALETTE)]

    @property
    def avatar_text_color(self) -> str:
        """Readable text colour for :attr:`avatar_color`."""

        from matches.branding import text_colour_for

        return text_colour_for(self.avatar_color)

    @property
    def display_role(self) -> str:
        return self.get_role_display()

    @property
    def batting_average(self):
        """Career batting average across all team/season rows.

        Averaging each season's average gives a misleading result, especially
        for players who changed teams.  Aggregate numerator and denominator
        first so the property remains correct when affiliations split a season.
        """
        aggregate = self.season_stats.aggregate(
            runs=models.Sum("runs"), dismissals=models.Sum("dismissals")
        )
        runs = aggregate["runs"] or 0
        dismissals = aggregate["dismissals"] or 0
        if not dismissals:
            return Decimal("0.00")
        return (Decimal(runs) / Decimal(dismissals)).quantize(Decimal("0.01"))

    @property
    def strike_rate(self):
        aggregate = self.season_stats.aggregate(runs=models.Sum("runs"), balls=models.Sum("balls"))
        balls = aggregate["balls"] or 0
        return round((aggregate["runs"] or 0) * 100 / balls, 2) if balls else 0.0

    @property
    def bowling_average(self):
        aggregate = self.season_stats.aggregate(
            wickets=models.Sum("wickets"), conceded=models.Sum("runs_conceded")
        )
        wickets = aggregate["wickets"] or 0
        return round(aggregate["conceded"] / wickets, 2) if wickets else 0.0

    @property
    def economy(self):
        aggregate = self.season_stats.aggregate(
            balls=models.Sum("bowling_balls"), conceded=models.Sum("runs_conceded")
        )
        balls = aggregate["balls"] or 0
        return round((aggregate["conceded"] or 0) * 6 / balls, 2) if balls else 0.0

    @property
    def total_runs(self):
        return self.season_stats.aggregate(total=models.Sum("runs"))["total"] or 0

    @property
    def total_wickets(self):
        return self.season_stats.aggregate(total=models.Sum("wickets"))["total"] or 0


class PlayerTeam(models.Model):
    """Historical team affiliations, used for team filters and trend analysis."""

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="affiliations")
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="player_affiliations")
    first_season = models.PositiveIntegerField(null=True, blank=True)
    last_season = models.PositiveIntegerField(null=True, blank=True)
    appearances = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-last_season", "team__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["player", "team"], name="unique_player_team_affiliation"
            )
        ]
        indexes = [models.Index(fields=["team", "last_season"], name="player_team_season_idx")]

    def __str__(self):
        return f"{self.player} — {self.team}"


class PlayerPerformance(models.Model):
    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="performances")
    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="player_performances")
    season = models.PositiveIntegerField(db_index=True)
    runs = models.PositiveSmallIntegerField(default=0)
    balls = models.PositiveSmallIntegerField(default=0)
    fours = models.PositiveSmallIntegerField(default=0)
    sixes = models.PositiveSmallIntegerField(default=0)
    wickets = models.PositiveSmallIntegerField(default=0)
    bowling_balls = models.PositiveSmallIntegerField(default=0)
    runs_conceded = models.PositiveSmallIntegerField(default=0)
    catches = models.PositiveSmallIntegerField(default=0)
    highest_score = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["-match__date", "player__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["player", "match"], name="unique_player_match_performance"
            )
        ]
        indexes = [
            models.Index(fields=["player", "season", "runs"], name="perf_player_season_runs"),
            models.Index(fields=["player", "season", "wickets"], name="perf_player_season_wkt"),
        ]

    def __str__(self):
        return f"{self.player}: {self.runs} runs, {self.wickets} wickets ({self.match_id})"

    @property
    def strike_rate(self):
        return round(self.runs * 100 / self.balls, 2) if self.balls else 0.0

    @property
    def overs(self):
        return Decimal(self.bowling_balls) / Decimal("6")


class PlayerSeasonPerformance(models.Model):
    """Pre-aggregated seasonal statistics for fast rankings and charts."""

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="season_stats")
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="season_stats")
    season = models.PositiveIntegerField()
    appearances = models.PositiveIntegerField(default=0)
    batting_innings = models.PositiveIntegerField(default=0)
    runs = models.PositiveIntegerField(default=0)
    balls = models.PositiveIntegerField(default=0)
    fours = models.PositiveIntegerField(default=0)
    sixes = models.PositiveIntegerField(default=0)
    dismissals = models.PositiveIntegerField(default=0)
    highest_score = models.PositiveIntegerField(default=0)
    bowling_innings = models.PositiveIntegerField(default=0)
    bowling_balls = models.PositiveIntegerField(default=0)
    wickets = models.PositiveSmallIntegerField(default=0)
    runs_conceded = models.PositiveSmallIntegerField(default=0)
    catches = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["-season", "player__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["player", "team", "season"],
                name="unique_player_team_season_stats",
            )
        ]
        indexes = [
            models.Index(fields=["season", "runs"], name="season_stats_runs_idx"),
            models.Index(fields=["season", "wickets"], name="season_stats_wickets_idx"),
        ]

    def __str__(self):
        return f"{self.player} ({self.team}, {self.season})"

    @property
    def average(self):
        return round(self.runs / self.dismissals, 2) if self.dismissals else float(self.runs)

    @property
    def strike_rate(self):
        return round(self.runs * 100 / self.balls, 2) if self.balls else 0.0

    @property
    def economy(self):
        return round(self.runs_conceded * 6 / self.bowling_balls, 2) if self.bowling_balls else 0.0
