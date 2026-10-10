# Generated for the persisted prediction records.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("matches", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Prediction",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "prediction_type",
                    models.CharField(
                        choices=[("score", "Score prediction"), ("outcome", "Outcome prediction")],
                        max_length=10,
                    ),
                ),
                ("toss_decision", models.CharField(blank=True, max_length=8)),
                ("season", models.PositiveIntegerField(blank=True, null=True)),
                ("current_score", models.PositiveSmallIntegerField(blank=True, null=True)),
                (
                    "overs_completed",
                    models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True),
                ),
                ("wickets", models.PositiveSmallIntegerField(blank=True, null=True)),
                (
                    "current_run_rate",
                    models.DecimalField(blank=True, decimal_places=2, max_digits=7, null=True),
                ),
                ("last_5_overs_runs", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("recent_wickets", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("target_score", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("predicted_score", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("prediction_low", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("prediction_high", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("outcome_probabilities", models.JSONField(blank=True, default=dict)),
                ("input_values", models.JSONField(blank=True, default=dict)),
                ("model_version", models.CharField(blank=True, max_length=80)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "batting_team",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="score_predictions",
                        to="matches.team",
                    ),
                ),
                (
                    "bowling_team",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="opponent_predictions",
                        to="matches.team",
                    ),
                ),
                (
                    "venue",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="predictions",
                        to="matches.venue",
                    ),
                ),
                (
                    "toss_winner",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="toss_predictions",
                        to="matches.team",
                    ),
                ),
                (
                    "outcome_winner",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="predicted_match_wins",
                        to="matches.team",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ipl_predictions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(
                        fields=["user", "prediction_type", "created_at"],
                        name="pred_user_type_date_idx",
                    ),
                    models.Index(fields=["batting_team", "venue"], name="pred_team_venue_idx"),
                ],
            },
        ),
    ]
