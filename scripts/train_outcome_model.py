#!/usr/bin/env python3
"""Train the match-winner classifier from normalized Django database data.

The shared trainer uses the recorded winner only as the target and reads
prediction-time-safe PlayerPerformance/Player aggregates from the imported
database. Run from the
project root after importing matches, innings, players, and outcomes and
applying migrations::

    python scripts/train_outcome_model.py

Probabilities are published only when the fitted classifier exposes
``predict_proba``; this script never invents confidence values.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--test-size",
        type=float,
        default=0.2,
        help="Fraction of the latest rows reserved for evaluation when a temporal split is feasible (default: 0.2).",
    )
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Artifact directory (default: Django settings.MODEL_DIRECTORY, normally data/models).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 0 < args.test_size < 1:
        raise SystemExit("--test-size must be greater than 0 and less than 1")

    try:
        import django

        django.setup()
        from predictions.training import TrainingDataError, format_training_report, train_outcome_model
    except Exception as exc:
        print(f"Could not initialize Django: {exc}", file=sys.stderr)
        return 2

    try:
        metadata = train_outcome_model(
            test_size=args.test_size,
            random_state=args.random_state,
            output_dir=args.output_dir,
        )
    except TrainingDataError as exc:
        print(f"Outcome training stopped: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Outcome training failed: {exc}", file=sys.stderr)
        return 1

    print("Outcome model training complete")
    print(format_training_report(metadata))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
