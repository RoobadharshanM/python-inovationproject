"""Database models for teams, venues, matches and innings."""
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

from . import branding


class Team(models.Model):
    name = models.CharField(max_length=120, unique=True)
    short_name = models.CharField(max_length=20, blank=True)
    primary_color = models.CharField(max_length=7, default="#6C63FF")
    logo = models.ImageField(upload_to="team-logos/", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def brand(self) -> "branding.TeamBrand":
        """Colour and monogram metadata used to render the generated crest."""

        return branding.brand_for(self.name)

    @property
    def crest_colors(self) -> dict[str, str]:
        """Return the colour set that keeps the crest readable.

        The stored ``primary_color`` wins so a club can rebrand itself from the
        admin, and the remaining stops are filled in from the brand table.
        """

        brand = self.brand
        return {
            "primary": self.primary_color or brand.primary_color,
            "secondary": brand.secondary_color,
            "accent": brand.accent_color,
            "text": branding.text_colour_for(self.primary_color or brand.primary_color),
        }

    @property
    def monogram(self) -> str:
        """Short uppercase code shown inside the generated crest."""

        return branding.monogram_for(self.short_name or self.brand.short_name, self.name)

    @property
    def crest_label(self) -> str:
        """Accessible label describing the crest, e.g. ``"MI crest"``."""

        return f"{self.monogram} crest"


class Venue(models.Model):
    name = models.CharField(max_length=180, unique=True)
    city = models.CharField(max_length=100, blank=True, db_index=True)
    short_name = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Match(models.Model):
    class MatchType(models.TextChoices):
        LEAGUE = "league", "League match"
        PLAYOFF = "playoff", "Playoff"
        FINAL = "final", "Final"

    class TossDecision(models.TextChoices):
        BAT = "bat", "Bat first"
        FIELD = "field", "Field first"

    class ResultType(models.TextChoices):
        NORMAL = "normal", "Normal result"
        SUPER_OVER = "super_over", "Super over"
        TIE = "tie", "Tie"
        NO_RESULT = "no_result", "No result"
        RAIN = "rain", "Rain affected"

    class DismissalMethod(models.TextChoices):
        RUNS = "runs", "By runs"
        WICKETS = "wickets", "By wickets"
        SUPER_OVER = "super_over", "Super over"
        TIE = "tie", "Tie"
        NO_RESULT = "no_result", "No result"
        RAIN = "rain", "Rain affected"

    external_id = models.PositiveBigIntegerField(
        unique=True, null=True, blank=True, db_index=True
    )
    season = models.PositiveIntegerField(db_index=True)
    date = models.DateField(db_index=True)
    city = models.CharField(max_length=100, blank=True)
    venue = models.ForeignKey(
        Venue, on_delete=models.PROTECT, related_name="matches"
    )
    team1 = models.ForeignKey(
        Team, on_delete=models.PROTECT, related_name="matches_as_team1"
    )
    team2 = models.ForeignKey(
        Team, on_delete=models.PROTECT, related_name="matches_as_team2"
    )
    toss_winner = models.ForeignKey(
        Team,
        on_delete=models.PROTECT,
        related_name="toss_wins",
        null=True,
        blank=True,
    )
    toss_decision = models.CharField(
        max_length=8, choices=TossDecision.choices, blank=True
    )
    winner = models.ForeignKey(
        Team,
        on_delete=models.PROTECT,
        related_name="match_wins",
        null=True,
        blank=True,
    )
    result_type = models.CharField(
        max_length=16, choices=ResultType.choices, default=ResultType.NORMAL
    )
    result = models.CharField(max_length=220, blank=True)
    dismissal_method = models.CharField(
        max_length=12, choices=DismissalMethod.choices, blank=True
    )
    player_of_match = models.ForeignKey(
        "players.Player",
        on_delete=models.SET_NULL,
        related_name="player_of_match_awards",
        null=True,
        blank=True,
    )
    match_type = models.CharField(
        max_length=10, choices=MatchType.choices, default=MatchType.LEAGUE
    )
    target_runs = models.PositiveSmallIntegerField(null=True, blank=True)
    target_overs = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    win_by_runs = models.PositiveSmallIntegerField(null=True, blank=True)
    win_by_wickets = models.PositiveSmallIntegerField(null=True, blank=True)
    dl_applied = models.BooleanField(default=False)
    umpires = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "match"
        verbose_name_plural = "matches"
        ordering = ["-date", "-external_id"]
        indexes = [
            models.Index(fields=["season", "date"], name="match_season_date_idx"),
            models.Index(fields=["team1", "season"], name="match_team1_season_idx"),
            models.Index(fields=["team2", "season"], name="match_team2_season_idx"),
            models.Index(fields=["winner", "season"], name="match_winner_season_idx"),
            models.Index(fields=["venue", "season"], name="match_venue_season_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~Q(team1=models.F("team2")),
                name="different_teams_in_match",
            )
        ]

    def __str__(self):
        return f"{self.team1} vs {self.team2} - {self.date:%d %b %Y}"

    @property
    def teams(self):
        return (self.team1, self.team2)

    @property
    def result_summary(self):
        if self.result:
            return self.result
        if self.winner_id:
            return f"{self.winner} won"
        return "No result"

    @property
    def team_score(self):
        return self.innings.filter(innings=1).first()


class MatchInnings(models.Model):
    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="innings")
    team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="innings")
    innings = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1)], default=1
    )
    runs = models.PositiveIntegerField(default=0)
    wickets = models.PositiveSmallIntegerField(default=0)
    balls = models.PositiveIntegerField(default=0)
    extras = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "match innings"
        verbose_name_plural = "match innings"
        ordering = ["match", "innings"]
        constraints = [
            models.UniqueConstraint(
                fields=["match", "innings"], name="unique_innings_per_match"
            ),
            models.CheckConstraint(
                condition=Q(innings__in=[1, 2]), name="innings_is_one_or_two"
            ),
        ]

    def __str__(self):
        return f"{self.match_id}: {self.team} innings {self.innings}"

    @property
    def overs_display(self):
        return f"{self.balls // 6}.{self.balls % 6}"
