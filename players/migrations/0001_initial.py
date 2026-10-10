# Generated manually for the initial IPL domain schema.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("matches", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Player",
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
                ("name", models.CharField(max_length=140, unique=True)),
                (
                    "role",
                    models.CharField(
                        choices=[
                            ("batter", "Batter"),
                            ("bowler", "Bowler"),
                            ("all_rounder", "All-rounder"),
                            ("wicketkeeper", "Wicketkeeper-batter"),
                            ("unknown", "Unknown"),
                        ],
                        default="unknown",
                        max_length=16,
                    ),
                ),
                ("nationality", models.CharField(blank=True, max_length=80)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "current_team",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="current_players",
                        to="matches.team",
                    ),
                ),
            ],
            options={
                "ordering": ["name"],
                "indexes": [models.Index(fields=["name"], name="player_name_idx")],
            },
        ),
        migrations.CreateModel(
            name="PlayerTeam",
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
                ("first_season", models.PositiveIntegerField(blank=True, null=True)),
                ("last_season", models.PositiveIntegerField(blank=True, null=True)),
                ("appearances", models.PositiveIntegerField(default=0)),
                (
                    "player",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="affiliations",
                        to="players.player",
                    ),
                ),
                (
                    "team",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="player_affiliations",
                        to="matches.team",
                    ),
                ),
            ],
            options={
                "ordering": ["-last_season", "team__name"],
                "indexes": [
                    models.Index(
                        fields=["team", "last_season"],
                        name="player_team_season_idx",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("player", "team"),
                        name="unique_player_team_affiliation",
                    )
                ],
            },
        ),
    ]
