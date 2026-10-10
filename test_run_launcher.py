"""Regression tests for the root-level one-command launcher."""
import json
import os
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import SimpleTestCase

import run


class OneCommandLauncherTests(SimpleTestCase):
    def test_project_layout_and_dataset_are_discovered(self):
        self.assertTrue(run.MANAGE_PY.is_file())
        self.assertTrue(run.REQUIREMENTS_FILE.is_file())
        dataset = run.discover_dataset()
        self.assertIsNotNone(dataset)
        self.assertEqual(dataset.matches.name.casefold(), "matches.csv")
        self.assertEqual(dataset.deliveries.name.casefold(), "deliveries.csv")

    def test_virtualenv_path_matches_platform(self):
        expected = Path("Scripts") / "python.exe" if os.name == "nt" else Path("bin") / "python"
        self.assertEqual(run.virtualenv_python(), run.VENV_DIRECTORY / expected)

    def test_server_option_and_url_validation(self):
        self.assertEqual(run.browser_url("127.0.0.1", 8000), "http://127.0.0.1:8000/")
        self.assertEqual(run.browser_url("::", 8000), "http://[::1]:8000/")
        self.assertEqual(run.server_address("::1", 8001), "[::1]:8001")
        run.validate_server_options("localhost", 8000)
        with self.assertRaises(run.LauncherError):
            run.validate_server_options("localhost", 0)
        with self.assertRaises(run.LauncherError):
            run.validate_server_options("bad host", 8000)

    def test_default_command_needs_no_optional_flags(self):
        args = run.build_parser().parse_args([])
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 8000)
        self.assertFalse(args.server)
        self.assertFalse(args.train)
        self.assertFalse(args.no_browser)

    def test_model_artifact_names_match_runtime_conventions(self):
        self.assertIn("score_model.joblib", run.MODEL_ARTIFACTS["score"]["model"])
        self.assertIn("score_preprocessor.joblib", run.MODEL_ARTIFACTS["score"]["preprocessor"])
        self.assertNotIn("score_prediction_model.pkl", run.MODEL_ARTIFACTS["score"]["model"])

    def test_import_manifest_requires_matching_source_hash_and_count(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            matches = root / "matches.csv"
            deliveries = root / "deliveries.csv"
            manifest = root / "manifest.json"
            matches.write_text("id\n1\n", encoding="utf-8")
            deliveries.write_text("id\n1\n", encoding="utf-8")
            payload = {
                "status": "complete",
                "importer_version": run.MINIMUM_IMPORTER_VERSION,
                "matches_sha256": run._file_sha256(matches),
                "deliveries_sha256": run._file_sha256(deliveries),
                "matches_imported": 1,
            }
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            files = run.DatasetFiles(root, matches, deliveries)
            with patch.object(run, "IMPORT_MANIFEST", manifest):
                self.assertTrue(run.import_manifest_matches(files, 1))
                self.assertFalse(run.import_manifest_matches(files, 2))
                deliveries.write_text("id\n1\n2\n", encoding="utf-8")
                self.assertFalse(run.import_manifest_matches(files, 1))

    def test_stale_importer_version_forces_a_reimport(self):
        """An older manifest must not be trusted to describe a clean database."""

        with TemporaryDirectory() as directory:
            root = Path(directory)
            matches = root / "matches.csv"
            deliveries = root / "deliveries.csv"
            manifest = root / "manifest.json"
            matches.write_text("id\n1\n", encoding="utf-8")
            deliveries.write_text("id\n1\n", encoding="utf-8")
            payload = {
                "status": "complete",
                "importer_version": run.MINIMUM_IMPORTER_VERSION - 1,
                "matches_sha256": run._file_sha256(matches),
                "deliveries_sha256": run._file_sha256(deliveries),
                "matches_imported": 1,
            }
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            files = run.DatasetFiles(root, matches, deliveries)
            with patch.object(run, "IMPORT_MANIFEST", manifest):
                self.assertFalse(run.import_manifest_matches(files, 1))

    def test_launcher_importer_version_matches_the_importer(self):
        source = (Path(run.PROJECT_ROOT) / "matches" / "importer.py").read_text(
            encoding="utf-8"
        )
        match = re.search(r'"importer_version":\s*(\d+)', source)
        self.assertIsNotNone(match, "importer.py must publish importer_version")
        self.assertEqual(int(match.group(1)), run.MINIMUM_IMPORTER_VERSION)

    def test_development_server_keeps_the_template_reloader(self):
        """--noreload would freeze template edits until a manual restart.

        Django's cached template loader never re-checks timestamps, so the
        autoreloader is the only thing that picks up a saved .html change.
        """

        for value, expected in (
            ("True", True),
            ("true", True),
            ("1", True),
            ("on", True),
            ("False", False),
            ("0", False),
        ):
            with self.subTest(debug=value):
                with patch.dict(os.environ, {"DJANGO_DEBUG": value}):
                    self.assertEqual(run.debug_enabled(), expected)
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(run.debug_enabled())

    @patch.object(run.webbrowser, "open_new_tab", return_value=True)
    def test_browser_failure_policy_is_non_fatal(self, browser):
        run.open_browser_when_ready("http://127.0.0.1:8000/")
        browser.assert_called_once_with("http://127.0.0.1:8000/")

    @patch.object(run, "subprocess")
    def test_server_rejects_unsafe_port_before_subprocess(self, _subprocess):
        with self.assertRaises(run.LauncherError):
            run.main(["--server", "--port", "70000"])
