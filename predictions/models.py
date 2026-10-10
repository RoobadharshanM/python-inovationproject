"""Persisted machine-learning score and outcome predictions."""
from django.conf import settings
from django.db import models

from matches.models import Team, Venue


class Prediction(models.Model):
    class Type(models.TextChoices):
        SCORE = "score", "Score prediction"
        OUTCOME = "outcome", "Outcome prediction"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ipl_predictions",
    )
    prediction_type = models.CharField(max_length=10, choices=Type.choices)
    batting_team = models.ForeignKey(
        Team,
        on_delete=models.SET_NULL,
        related_name="score_predictions",
        null=True,
        blank=True,
    )
    bowling_team = models.ForeignKey(
        Team,
        on_delete=models.SET_NULL,
        related_name="opponent_predictions",
        null=True,
        blank=True,
    )
    venue = models.ForeignKey(
        Venue, on_delete=models.SET_NULL, related_name="predictions", null=True, blank=True
    )
    toss_winner = models.ForeignKey(
        Team,
        on_delete=models.SET_NULL,
        related_name="toss_predictions",
        null=True,
        blank=True,
    )
    toss_decision = models.CharField(max_length=8, blank=True)
    season = models.PositiveIntegerField(null=True, blank=True)
    current_score = models.PositiveSmallIntegerField(null=True, blank=True)
    overs_completed = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    wickets = models.PositiveSmallIntegerField(null=True, blank=True)
    current_run_rate = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True
    )
    last_5_overs_runs = models.PositiveSmallIntegerField(null=True, blank=True)
    recent_wickets = models.PositiveSmallIntegerField(null=True, blank=True)
    target_score = models.PositiveSmallIntegerField(null=True, blank=True)
    predicted_score = models.PositiveSmallIntegerField(null=True, blank=True)
    prediction_low = models.PositiveSmallIntegerField(null=True, blank=True)
    prediction_high = models.PositiveSmallIntegerField(null=True, blank=True)
    outcome_winner = models.ForeignKey(
        Team,
        on_delete=models.SET_NULL,
        related_name="predicted_match_wins",
        null=True,
        blank=True,
    )
    outcome_probabilities = models.JSONField(default=dict, blank=True)
    input_values = models.JSONField(default=dict, blank=True)
    model_version = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "prediction_type", "created_at"], name="pred_user_type_date_idx"),
            models.Index(fields=["batting_team", "venue"], name="pred_team_venue_idx"),
        ]

    def __str__(self):
        if self.prediction_type == self.Type.SCORE:
            return f"{self.batting_team} score estimate: {self.predicted_score}"
        return f"{self.outcome_winner} outcome estimate"

    @property
    def teams_summary(self):
        if self.prediction_type == self.Type.OUTCOME:
            return f"{self.batting_team} vs {self.bowling_team}"
        return f"{self.batting_team} vs {self.bowling_team}"
