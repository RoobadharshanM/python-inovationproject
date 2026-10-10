"""Regression tests for dashboard delivery and the public JSON contract."""
from datetime import date
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from dashboard.services import build_dashboard_stats
from matches.branding import TEAM_BRANDS, brand_for, monogram_for, text_colour_for
from matches.models import Match, Team, Venue
from players.models import Player, PlayerPerformance, player_initials


class DashboardFrontendContractTests(SimpleTestCase):
    def test_hidden_attribute_cannot_be_overridden_by_bootstrap_flex_utilities(self):
        css = (Path(settings.BASE_DIR) / "static" / "css" / "style.css").read_text(
            encoding="utf-8"
        )
        self.assertIn("[hidden] { display: none !important; }", css)

    def test_input_icons_reserve_space_and_hide_after_a_value_is_entered(self):
        static_root = Path(settings.BASE_DIR) / "static"
        css = (static_root / "css" / "style.css").read_text(encoding="utf-8")
        script = (static_root / "js" / "main.js").read_text(encoding="utf-8")
        self.assertIn("padding-left: 3rem !important", css)
        self.assertIn(".input-icon-wrap.has-value > .bi", css)
        self.assertIn("initInputIcons();", script)
        self.assertIn("classList.toggle('has-value'", script)


class PlayerPortraitTests(TestCase):
    def test_initials_handle_the_ipl_abbreviation_convention(self):
        expected = {
            "S Dhawan": "SD",
            "R Ashwin": "RA",
            "AB de Villiers": "AB",
            "VVS Lakshman": "VVS",
            "Harbhajan Singh": "HS",
            "Sachin Baby": "SB",
            "SMSM Senanayake": "SMS",
            "WPUJC Vaas": "WPU",
            "Sachin": "S",
            "": "?",
        }
        for name, monogram in expected.items():
            with self.subTest(name=name):
                self.assertEqual(player_initials(name), monogram)

    def test_initials_never_produce_garbage_from_legacy_suffixes(self):
        # Before the importer fix these rendered as "A(" and "HS(".
        self.assertEqual(player_initials("A Mukund (sub)"), "AM")
        self.assertEqual(player_initials("Harmeet Singh (2)"), "HS")

    def test_portrait_falls_back_to_a_stable_colour(self):
        team = Team.objects.create(name="Test XI", primary_color="#ABCDEF")
        with_team = Player.objects.create(name="With Team", current_team=team)
        self.assertEqual(with_team.avatar_color, "#ABCDEF")
        self.assertFalse(with_team.has_portrait)
        self.assertEqual(with_team.portrait, "")

        orphan = Player.objects.create(name="No Team")
        self.assertEqual(orphan.avatar_color, orphan.avatar_color)
        self.assertRegex(orphan.avatar_color, r"^#[0-9A-Fa-f]{6}$")
        self.assertIn(orphan.avatar_text_color, ("#FFFFFF", "#111827"))


class TeamBrandingTests(TestCase):
    def test_every_known_franchise_has_a_brand_entry(self):
        for name in TEAM_BRANDS:
            brand = brand_for(name)
            self.assertRegex(brand.primary_color, r"^#[0-9A-Fa-f]{6}$")
            self.assertRegex(brand.secondary_color, r"^#[0-9A-Fa-f]{6}$")
            self.assertRegex(brand.accent_color, r"^#[0-9A-Fa-f]{6}$")
            self.assertTrue(brand.short_name)

    def test_light_crest_gets_dark_text_and_dark_crest_gets_light_text(self):
        # Chennai Super Kings' yellow is far too light for white monogram text.
        self.assertEqual(text_colour_for("#F9CD05"), "#111827")
        self.assertEqual(text_colour_for("#3A225D"), "#FFFFFF")
        self.assertEqual(text_colour_for("not-a-colour"), "#FFFFFF")

    def test_monogram_prefers_short_code_and_falls_back_to_initials(self):
        self.assertEqual(monogram_for("MI", "Mumbai Indians"), "MI")
        self.assertEqual(monogram_for("PBKS", "Punjab Kings"), "PBKS")
        self.assertEqual(monogram_for("", "Rising Pune Supergiant"), "RPS")
        self.assertEqual(monogram_for("", "Kochi Tuskers Kerala"), "KTK")
        self.assertEqual(monogram_for("", ""), "?")

    def test_team_crest_colors_prefer_the_stored_primary_colour(self):
        team = Team(name="Mumbai Indians", short_name="MI", primary_color="#123456")
        colours = team.crest_colors
        self.assertEqual(colours["primary"], "#123456")
        self.assertEqual(colours["secondary"], brand_for("Mumbai Indians").secondary_color)
        self.assertEqual(team.monogram, "MI")
        self.assertEqual(team.crest_label, "MI crest")


class DashboardApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        first = Team.objects.create(name="Alpha XI", short_name="AXI")
        second = Team.objects.create(name="Beta XI", short_name="BXI")
        venue = Venue.objects.create(name="Test Ground", city="Testville")
        match = Match.objects.create(
            external_id=90001,
            season=2024,
            date=date(2024, 4, 1),
            city="Testville",
            venue=venue,
            team1=first,
            team2=second,
            toss_winner=first,
            toss_decision=Match.TossDecision.BAT,
            winner=first,
        )
        player = Player.objects.create(
            name="All Round Player",
            current_team=first,
            role=Player.Role.ALL_ROUNDER,
        )
        PlayerPerformance.objects.create(
            player=player,
            match=match,
            season=2024,
            runs=42,
            balls=30,
            fours=4,
            sixes=1,
            wickets=3,
            bowling_balls=18,
            runs_conceded=35,
        )

    def test_dashboard_page_requires_authentication(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_dashboard_api_exposes_all_chart_datasets(self):
        response = self.client.get(reverse("dashboard-stats"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        charts = payload["data"]["charts"]
        for name in (
            "matches_by_season",
            "team_wins",
            "top_batters",
            "top_bowlers",
            "team_radar",
            "player_output",
        ):
            self.assertIn(name, charts)

    def test_player_output_chart_contains_real_runs_and_wickets(self):
        charts = build_dashboard_stats()["charts"]
        points = charts["player_output"]["points"]
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]["x"], 42)
        self.assertEqual(points[0]["y"], 3)
