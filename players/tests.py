"""Focused tests for player filters, rankings and chart payloads."""

from unittest.mock import patch

from django.test import RequestFactory, TestCase

from matches.models import Team
from players.models import Player, PlayerSeasonPerformance
from players.views import player_detail, player_rankings


class PlayerViewTests(TestCase):
    def setUp(self):
        self.team_a = Team.objects.create(name="Alpha")
        self.team_b = Team.objects.create(name="Beta")
        self.player_a = Player.objects.create(name="Alpha Batter", current_team=self.team_a)
        self.player_b = Player.objects.create(name="Beta Batter", current_team=self.team_b)
        PlayerSeasonPerformance.objects.create(
            player=self.player_a,
            team=self.team_a,
            season=2024,
            appearances=2,
            batting_innings=2,
            runs=100,
            balls=80,
            dismissals=1,
            highest_score=70,
        )
        PlayerSeasonPerformance.objects.create(
            player=self.player_b,
            team=self.team_b,
            season=2024,
            appearances=1,
            batting_innings=1,
            runs=40,
            balls=30,
            dismissals=1,
            highest_score=25,
        )
        self.factory = RequestFactory()

    def test_rankings_support_season_metric_and_search(self):
        request = self.factory.get(
            "/players/rankings/", {"season": "2024", "metric": "runs", "q": "Alpha"}
        )
        with patch("players.views.render") as render:
            player_rankings(request)
            context = render.call_args.args[2]
        self.assertEqual(context["total_count"], 1)
        self.assertEqual(context["rankings"][0]["player"], self.player_a)
        self.assertEqual(context["rankings"][0]["rank"], 1)
        self.assertEqual(context["metric_label"], "Most runs")

    def test_rankings_put_low_economy_first(self):
        # Add a bowler with a genuinely qualified economy rate; players with no
        # bowling balls are intentionally excluded from this metric.
        bowler = Player.objects.create(name="Bowler", current_team=self.team_a)
        PlayerSeasonPerformance.objects.create(
            player=bowler,
            team=self.team_a,
            season=2024,
            bowling_innings=1,
            bowling_balls=12,
            wickets=2,
            runs_conceded=8,
        )
        request = self.factory.get("/players/rankings/", {"season": "2024", "metric": "economy"})
        with patch("players.views.render") as render:
            player_rankings(request)
            context = render.call_args.args[2]
        self.assertEqual(context["rankings"][0]["player"], bowler)
        self.assertEqual(context["rankings"][0]["metric_value"], 4.0)

    def test_detail_exposes_empty_state_and_json_safe_chart_keys(self):
        request = self.factory.get(f"/players/{self.player_a.pk}/")
        with patch("players.views.render") as render:
            player_detail(request, self.player_a.pk)
            context = render.call_args.args[2]
        self.assertIn("chart_data", context)
        self.assertIn("datasets", context["chart_data"])
        self.assertTrue(context["has_seasonal_data"])
        self.assertEqual(context["player"], self.player_a)
