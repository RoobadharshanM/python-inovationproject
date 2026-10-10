"""Contract tests for the analytics page and its JSON renderer."""
from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.urls import reverse


class AnalyticsScriptContractTests(SimpleTestCase):
    def test_error_state_is_preserved_after_loading_finishes(self):
        script = (
            settings.STATICFILES_DIRS[0] / "js" / "analytics.js"
        ).read_text(encoding="utf-8")
        self.assertIn("setLoading(false, failed ? 'error' : finalState)", script)
        self.assertIn("errorPanel.hidden = value || finalState !== 'error'", script)


class AnalyticsApiTests(TestCase):
    def test_analytics_api_returns_renderable_chart_collections(self):
        response = self.client.get(reverse("analytics-data"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        data = payload["data"]
        self.assertIn("overview", data)
        self.assertIn("season_stats", data)
        self.assertIn("team_stats", data)
        self.assertIn("player_stats", data)
        self.assertIn("venue_stats", data)
        self.assertIn("player_output", data["overview"])
