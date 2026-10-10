"""Focused unit tests for the prediction service's safety boundaries."""

from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase, override_settings

from predictions.feature_engineering import SCORE_FEATURE_NAMES, normalise_feature_input
from predictions.ml_model import _score_range, clear_model_cache, model_status
from predictions.training import TrainingDataset, _split_indices


class PredictionFeatureTests(SimpleTestCase):
    def test_feature_normalisation_keeps_runtime_values_in_the_artifact_schema(self):
        values = normalise_feature_input(
            {
                "batting_team": "Team A",
                "bowling_team": "Team B",
                "season": 2024,
                "current_score": "42",
                "overs_completed": "6.5",
            }
        )
        self.assertEqual(values["batting_team"], "Team A")
        self.assertEqual(values["season"], "2024")
        self.assertEqual(values["current_score"], 42)
        self.assertEqual(values["overs_completed"], 6.5)
        self.assertTrue({"current_score", "overs_completed"}.issubset(SCORE_FEATURE_NAMES))

    def test_score_range_uses_empirical_absolute_error_metadata(self):
        metadata = {
            "residual_error": {
                "absolute_error_quantiles": {"q90": 12.5},
                "method": "empirical_absolute_residual_error_on_holdout",
            }
        }
        low, high, method = _score_range(metadata, 100.0)
        self.assertEqual((low, high), (88, 112))
        self.assertEqual(method, "empirical_absolute_residual_error_on_holdout")

    def test_temporal_split_is_used_when_enough_rows_exist(self):
        dataset = TrainingDataset(
            "score",
            [{"id": index} for index in range(6)],
            list(range(6)),
            [date(2020, 1, index + 1) for index in range(6)],
            list(range(6)),
        )
        train, test, metadata = _split_indices(dataset, test_size=0.34)
        self.assertEqual(metadata["strategy"], "chronological_holdout")
        self.assertEqual(test, [4, 5])
        self.assertEqual(train, [0, 1, 2, 3])

    def test_missing_artifacts_are_reported_as_not_trained(self):
        with TemporaryDirectory() as directory:
            with override_settings(MODEL_DIRECTORY=Path(directory)):
                clear_model_cache()
                status = model_status("score", model_directory=directory)
        self.assertFalse(status["trained"])
        self.assertIn("train_score_model.py", status["message"])
