"""Focused tests for normalisation, importing and match view filters."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.db.models import Sum
from django.test import SimpleTestCase, TestCase

from run import MINIMUM_IMPORTER_VERSION
from matches import importer
from matches.branding import brand_for
from matches.importer import (
    DataValidationError,
    IPLDataImporter,
    discover_ipl_data_files,
    normalize_player_name,
    normalize_team_name,
    normalize_venue_name,
    parse_match_date,
)
from matches.models import Match, MatchInnings, Team
from players.models import Player, PlayerPerformance, PlayerSeasonPerformance, PlayerTeam


MATCH_HEADER = (
    "id,season,city,date,team1,team2,toss_winner,toss_decision,result,dl_applied,"
    "winner,win_by_runs,win_by_wickets,player_of_match,venue,umpire1,umpire2,umpire3"
)
DELIVERY_HEADER = (
    "match_id,inning,batting_team,bowling_team,over,ball,batsman,non_striker,bowler,"
    "is_super_over,wide_runs,bye_runs,legbye_runs,noball_runs,penalty_runs,batsman_runs,"
    "extra_runs,total_runs,player_dismissed,dismissal_kind,fielder"
)


class ImporterUtilityTests(SimpleTestCase):
    def test_normalisation_and_date_parsing(self):
        self.assertEqual(normalize_team_name("  Delhi Daredevils "), "Delhi Capitals")
        self.assertEqual(normalize_venue_name("MA Chidambaram Stadium, Chepauk"), "M. A. Chidambaram Stadium, Chennai")
        self.assertEqual(parse_match_date("05/04/2017").isoformat(), "2017-04-05")

    def test_discovery_accepts_project_root_and_dataset_directory(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            data_dir = root / "DataSets"
            data_dir.mkdir()
            (data_dir / "matches.csv").write_text(MATCH_HEADER + "\n", encoding="utf-8")
            (data_dir / "deliveries.csv").write_text(DELIVERY_HEADER + "\n", encoding="utf-8")
            match_file, delivery_file = discover_ipl_data_files(root)
            self.assertEqual(match_file.name, "matches.csv")
            self.assertEqual(delivery_file.name, "deliveries.csv")

    def test_invalid_match_is_reported_before_database_write(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "matches.csv").write_text(
                MATCH_HEADER + "\n1,2024,City,not-a-date,A,B,,,,0,,,,Ground,,,\n",
                encoding="utf-8",
            )
            (root / "deliveries.csv").write_text(DELIVERY_HEADER + "\n", encoding="utf-8")
            with self.assertRaises(DataValidationError):
                IPLDataImporter(root).run()

    def test_player_names_lose_substitute_suffixes_and_gain_corrections(self):
        self.assertEqual(normalize_player_name("AB de Villiers (sub)"), "AB de Villiers")
        self.assertEqual(normalize_player_name("Harmeet Singh (2)"), "Harmeet Singh")
        self.assertEqual(normalize_player_name("  MS   Dhoni "), "MS Dhoni")
        # The export misspells V. V. S. Lakshman.
        self.assertEqual(normalize_player_name("VVS Laxman"), "VVS Lakshman")
        # Names are already canonical, so they are returned untouched.
        self.assertEqual(normalize_player_name("S Dhawan"), "S Dhawan")
        # The suffix stripper must converge rather than loop.
        for noisy in ("X (sub) (sub)", "Y (2) (sub)", "Q (  sub  )"):
            once = normalize_player_name(noisy)
            self.assertEqual(normalize_player_name(once), once)
        self.assertEqual(normalize_player_name(None), "")


class ImporterDatabaseTests(TestCase):
    """Import tests must never publish a fingerprint for the real dataset.

    ``_write_import_manifest`` writes to the project's ``data/processed``
    directory.  Left unpatched, importing a two-row fixture here overwrites the
    real manifest with temporary paths, so the launcher then thinks the genuine
    dataset is stale and re-imports all 756 matches on every start.
    """

    def setUp(self):
        super().setUp()
        # Capture the genuine location before patching it out.
        self.real_manifest_path = importer.import_manifest_path()
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.published_manifest_path = Path(temporary.name) / "ipl_import_manifest.json"
        patcher = patch(
            "matches.importer.import_manifest_path",
            return_value=self.published_manifest_path,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write_fixture(self, directory):
        directory = Path(directory)
        (directory / "matches.csv").write_text(
            MATCH_HEADER
            + "\n1,2024,City,2024-04-01,Alpha,beta,Alpha,bat,Alpha won by 2 runs,0,Alpha,2,0,Hero,Test Ground,Umpire One,Umpire Two,\n",
            encoding="utf-8",
        )
        rows = [
            "1,1,Alpha,Beta,1,1,Hero,Partner,Bowler,0,0,0,0,0,0,4,0,4,,,",
            "1,2,Beta,Alpha,1,1,Rival,Partner,Bowler,0,0,0,0,0,0,0,1,1,Hero,bowled,Fielder",
        ]
        # The second row is an exact duplicate of the first and must not inflate
        # the score.  It exercises deterministic duplicate handling.
        rows.append(rows[0])
        (directory / "deliveries.csv").write_text(
            DELIVERY_HEADER + "\n" + "\n".join(rows) + "\n", encoding="utf-8"
        )

    def test_import_builds_scorecards_performances_and_rollups(self):
        with TemporaryDirectory() as directory:
            self._write_fixture(directory)
            summary = IPLDataImporter(directory).run(replace=True)
            self.assertEqual(summary.matches_created, 1)
            self.assertEqual(summary.duplicate_delivery_rows, 1)
            self.assertEqual(Match.objects.count(), 1)
            self.assertEqual(MatchInnings.objects.count(), 2)
            self.assertEqual(Team.objects.count(), 2)
            self.assertEqual(Player.objects.count(), 5)
            self.assertEqual(PlayerPerformance.objects.filter(runs=4).count(), 1)
            hero = PlayerPerformance.objects.get(player__name="Hero")
            bowler = PlayerPerformance.objects.get(player__name="Bowler")
            self.assertEqual(hero.wickets, 0)
            self.assertEqual(hero.highest_score, 4)
            self.assertEqual(bowler.wickets, 1)
            self.assertEqual(bowler.bowling_balls, 2)
            self.assertEqual(
                PlayerSeasonPerformance.objects.get(player=hero.player).dismissals, 1
            )
            self.assertEqual(PlayerSeasonPerformance.objects.filter(runs=4).count(), 1)
            self.assertEqual(PlayerTeam.objects.count(), 5)
            self.assertEqual(Match.objects.get().winner.name, "Alpha")
            self.assertEqual(Match.objects.get().match_type, Match.MatchType.FINAL)
            self.assertEqual(MatchInnings.objects.get(innings=1).runs, 4)

    def test_external_id_collision_with_different_match_is_rejected(self):
        with TemporaryDirectory() as directory:
            self._write_fixture(directory)
            IPLDataImporter(directory).run(replace=True)
            match_path = Path(directory) / "matches.csv"
            source = match_path.read_text(encoding="utf-8").replace(
                "Alpha,beta,Alpha,bat,Alpha won by 2 runs,0,Alpha",
                "Alpha,Gamma,Alpha,bat,Alpha won by 2 runs,0,Alpha",
            )
            match_path.write_text(source, encoding="utf-8")

            with self.assertRaises(DataValidationError):
                IPLDataImporter(directory).run(replace=False)

            self.assertEqual(Match.objects.get().team2.name, "beta")

    def test_substitute_rows_are_folded_into_one_player(self):
        """A "(sub)" duplicate must not split one player's career in two.

        The reconciliation step is exercised directly: a full re-import
        rebuilds match rows from the delivery source, which would overwrite the
        synthetic stray row and hide the merge behaviour under test.
        """

        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_fixture(directory)
            IPLDataImporter(root).run(replace=True)

            hero = Player.objects.get(name="Hero")
            match = Match.objects.get()
            hero_row = PlayerPerformance.objects.get(player=hero, match=match)
            hero_wickets = hero_row.wickets

            # Same match as the canonical player, so merging collides with the
            # unique (player, match) constraint.
            stray = Player.objects.create(name="Hero (sub)")
            PlayerPerformance.objects.create(
                player=stray, match=match, season=match.season, wickets=2, catches=1
            )
            PlayerSeasonPerformance.objects.create(
                player=stray,
                team=Team.objects.get(name="Alpha"),
                season=match.season,
                appearances=1,
                wickets=2,
            )
            self.assertTrue(Player.objects.filter(name="Hero (sub)").exists())

            importer._reconcile_legacy_player_duplicates()

            suffixed = Player.objects.filter(name__regex=r"\((sub|\d+)\)\s*$")
            self.assertEqual(suffixed.count(), 0)
            self.assertEqual(Player.objects.filter(name="Hero").count(), 1)
            # Exactly one row survives for the player/match pair.
            rows = PlayerPerformance.objects.filter(player=hero, match=match)
            self.assertEqual(rows.count(), 1)
            # The stray row's figures are combined, not discarded.
            self.assertEqual(rows.get().wickets, hero_wickets + 2)
            self.assertEqual(rows.get().catches, 1)
            # The stale season rollup is dropped; the importer rebuilds it.
            stale = PlayerSeasonPerformance.objects.filter(wickets=2)
            self.assertFalse(
                stale.filter(player__name__regex=r"\((sub|\d+)\)").exists()
            )

    def test_team_branding_is_backfilled_from_the_brand_table(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_fixture(directory)
            IPLDataImporter(root).run(replace=True)

            chennai = Team.objects.create(name="Chennai Super Kings")
            IPLDataImporter(root).run(replace=False)
            chennai.refresh_from_db()

            self.assertEqual(chennai.short_name, "CSK")
            self.assertEqual(chennai.primary_color, brand_for("Chennai Super Kings").primary_color)
            self.assertNotEqual(chennai.primary_color, "#6C63FF")

    def test_manual_colour_survives_a_reimport(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_fixture(directory)
            IPLDataImporter(root).run(replace=True)
            chennai = Team.objects.create(
                name="Chennai Super Kings", primary_color="#ABCDEF"
            )

            IPLDataImporter(root).run(replace=False)
            chennai.refresh_from_db()

            # A club that picked its own colour must not be reset to the
            # placeholder on the next import.
            self.assertEqual(chennai.primary_color, "#ABCDEF")

    def test_import_does_not_touch_the_real_manifest_path(self):
        """A fixture import must publish its fingerprint only to the test path."""

        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_fixture(directory)
            IPLDataImporter(root).run(replace=True)

            self.assertTrue(self.published_manifest_path.is_file())
            payload = json.loads(self.published_manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "complete")
            self.assertEqual(payload["importer_version"], MINIMUM_IMPORTER_VERSION)
            self.assertNotEqual(self.published_manifest_path, self.real_manifest_path)

    def test_repeated_import_is_idempotent_and_preserves_unrelated_rows(self):
        with TemporaryDirectory() as directory:
            self._write_fixture(directory)
            IPLDataImporter(directory).run(replace=True)
            manual_player = Player.objects.create(name="Manual Player")
            manual_team = Team.objects.get(name="Alpha")
            manual_affiliation = PlayerTeam.objects.create(
                player=manual_player,
                team=manual_team,
                first_season=2023,
                last_season=2023,
                appearances=7,
            )
            manual_season = PlayerSeasonPerformance.objects.create(
                player=manual_player,
                team=manual_team,
                season=2023,
                appearances=7,
                runs=99,
            )

            summary = IPLDataImporter(directory).run(replace=False)

            self.assertEqual(summary.matches_created, 0)
            self.assertEqual(summary.matches_updated, 1)
            self.assertEqual(Match.objects.count(), 1)
            self.assertEqual(
                PlayerTeam.objects.get(pk=manual_affiliation.pk).appearances,
                7,
            )
            self.assertEqual(
                PlayerSeasonPerformance.objects.get(pk=manual_season.pk).runs,
                99,
            )


class MatchFormTests(SimpleTestCase):
    def test_season_validation(self):
        from matches.forms import MatchFilterForm

        form = MatchFilterForm({"season": "not-a-year"}, seasons=[2024])
        self.assertFalse(form.is_valid())
        self.assertIn("season", form.errors)
