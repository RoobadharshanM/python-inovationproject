# Match models are split from teams/venues to close the player FK dependency.

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("matches", "0001_initial"),
        ("players", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Match",
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
                    "external_id",
                    models.PositiveBigIntegerField(
                        blank=True, db_index=True, null=True, unique=True
                    ),
                ),
                ("season", models.PositiveIntegerField(db_index=True)),
                ("date", models.DateField(db_index=True)),
                ("city", models.CharField(blank=True, max_length=100)),
                (
                    "toss_decision",
                    models.CharField(
                        blank=True,
                        choices=[("bat", "Bat first"), ("field", "Field first")],
                        max_length=8,
                    ),
                ),
                (
                    "result_type",
                    models.CharField(
                        choices=[
                            ("normal", "Normal result"),
                            ("super_over", "Super over"),
                            ("tie", "Tie"),
                            ("no_result", "No result"),
                            ("rain", "Rain affected"),
                        ],
                        default="normal",
                        max_length=16,
                    ),
                ),
                ("result", models.CharField(blank=True, max_length=220)),
                (
                    "dismissal_method",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("runs", "By runs"),
                            ("wickets", "By wickets"),
                            ("super_over", "Super over"),
                            ("tie", "Tie"),
                            ("no_result", "No result"),
                            ("rain", "Rain affected"),
                        ],
                        max_length=12,
                    ),
                ),
                (
                    "match_type",
                    models.CharField(
                        choices=[
                            ("league", "League match"),
                            ("playoff", "Playoff"),
                            ("final", "Final"),
                        ],
                        default="league",
                        max_length=10,
                    ),
                ),
                ("target_runs", models.PositiveSmallIntegerField(blank=True, null=True)),
                (
                    "target_overs",
                    models.DecimalField(
                        blank=True, decimal_places=2, max_digits=5, null=True
                    ),
                ),
                ("win_by_runs", models.PositiveSmallIntegerField(blank=True, null=True)),
                (
                    "win_by_wickets",
                    models.PositiveSmallIntegerField(blank=True, null=True),
                ),
                ("dl_applied", models.BooleanField(default=False)),
                ("umpires", models.JSONField(blank=True, default=list)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "player_of_match",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="player_of_match_awards",
                        to="players.player",
                    ),
                ),
                (
                    "team1",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="matches_as_team1",
                        to="matches.team",
                    ),
                ),
                (
                    "team2",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="matches_as_team2",
                        to="matches.team",
                    ),
                ),
                (
                    "toss_winner",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="toss_wins",
                        to="matches.team",
                    ),
                ),
                (
                    "venue",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="matches",
                        to="matches.venue",
                    ),
                ),
                (
                    "winner",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="match_wins",
                        to="matches.team",
                    ),
                ),
            ],
            options={
                "ordering": ["-date", "-external_id"],
                "indexes": [
                    models.Index(
                        fields=["season", "date"], name="match_season_date_idx"
                    ),
                    models.Index(
                        fields=["team1", "season"], name="match_team1_season_idx"
                    ),
                    models.Index(
                        fields=["team2", "season"], name="match_team2_season_idx"
                    ),
                    models.Index(
                        fields=["winner", "season"], name="match_winner_season_idx"
                    ),
                    models.Index(
                        fields=["venue", "season"], name="match_venue_season_idx"
                    ),
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("team1", models.F("team2")), _negated=True),
                        name="different_teams_in_match",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="MatchInnings",
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
                    "innings",
                    models.PositiveSmallIntegerField(
                        default=1,
                        validators=[django.core.validators.MinValueValidator(1)],
                    ),
                ),
                ("runs", models.PositiveIntegerField(default=0)),
                ("wickets", models.PositiveSmallIntegerField(default=0)),
                ("balls", models.PositiveIntegerField(default=0)),
                ("extras", models.PositiveIntegerField(default=0)),
                (
                    "match",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="innings",
                        to="matches.match",
                    ),
                ),
                (
                    "team",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="innings",
                        to="matches.team",
                    ),
                ),
            ],
            options={
                "ordering": ["match", "innings"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("match", "innings"),
                        name="unique_innings_per_match",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("innings__in", [1, 2])),
                        name="innings_is_one_or_two",
                    ),
                ],
            },
        ),
    ]
