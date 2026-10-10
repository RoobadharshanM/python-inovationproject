# Performance models depend on the now-complete matches schema.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("players", "0001_initial"),
        ("matches", "0002_match_schema"),
    ]

    operations = [
        migrations.CreateModel(
            name="PlayerPerformance",
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
                ("season", models.PositiveIntegerField(db_index=True)),
                ("runs", models.PositiveSmallIntegerField(default=0)),
                ("balls", models.PositiveSmallIntegerField(default=0)),
                ("fours", models.PositiveSmallIntegerField(default=0)),
                ("sixes", models.PositiveSmallIntegerField(default=0)),
                ("wickets", models.PositiveSmallIntegerField(default=0)),
                ("bowling_balls", models.PositiveSmallIntegerField(default=0)),
                ("runs_conceded", models.PositiveSmallIntegerField(default=0)),
                ("catches", models.PositiveSmallIntegerField(default=0)),
                ("highest_score", models.PositiveSmallIntegerField(default=0)),
                (
                    "match",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="player_performances",
                        to="matches.match",
                    ),
                ),
                (
                    "player",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="performances",
                        to="players.player",
                    ),
                ),
            ],
            options={
                "ordering": ["-match__date", "player__name"],
                "indexes": [
                    models.Index(
                        fields=["player", "season", "runs"],
                        name="perf_player_season_runs",
                    ),
                    models.Index(
                        fields=["player", "season", "wickets"],
                        name="perf_player_season_wkt",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("player", "match"),
                        name="unique_player_match_performance",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="PlayerSeasonPerformance",
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
                ("season", models.PositiveIntegerField()),
                ("appearances", models.PositiveIntegerField(default=0)),
                ("batting_innings", models.PositiveIntegerField(default=0)),
                ("runs", models.PositiveIntegerField(default=0)),
                ("balls", models.PositiveIntegerField(default=0)),
                ("fours", models.PositiveIntegerField(default=0)),
                ("sixes", models.PositiveIntegerField(default=0)),
                ("dismissals", models.PositiveIntegerField(default=0)),
                ("highest_score", models.PositiveIntegerField(default=0)),
                ("bowling_innings", models.PositiveIntegerField(default=0)),
                ("bowling_balls", models.PositiveIntegerField(default=0)),
                ("wickets", models.PositiveSmallIntegerField(default=0)),
                ("runs_conceded", models.PositiveSmallIntegerField(default=0)),
                ("catches", models.PositiveSmallIntegerField(default=0)),
                (
                    "player",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="season_stats",
                        to="players.player",
                    ),
                ),
                (
                    "team",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="season_stats",
                        to="matches.team",
                    ),
                ),
            ],
            options={
                "ordering": ["-season", "player__name"],
                "indexes": [
                    models.Index(
                        fields=["season", "runs"], name="season_stats_runs_idx"
                    ),
                    models.Index(
                        fields=["season", "wickets"],
                        name="season_stats_wickets_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("player", "team", "season"),
                        name="unique_player_team_season_stats",
                    )
                ],
            },
        ),
    ]
