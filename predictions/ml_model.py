"""Lazy, cached access to offline prediction artifacts.

The web application never trains a model.  It loads the three files published
by ``scripts/train_score_model.py`` or ``scripts/train_outcome_model.py`` only
when a request needs them.  Missing or incomplete artifacts are represented by
:class:`ModelNotTrainedError`; malformed inputs and model failures use
:class:`ModelPredictionError` so views can return useful messages instead of a
500 traceback.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .feature_engineering import (
    OUTCOME_FEATURE_NAMES,
    SCORE_FEATURE_NAMES,
    feature_row,
    normalise_feature_input,
)


class ModelNotTrainedError(RuntimeError):
    """Raised when a requested model artifact has not been published."""


class ModelPredictionError(RuntimeError):
    """Raised when a published artifact cannot process a valid prediction."""


@dataclass(frozen=True)
class _Artifacts:
    kind: str
    model: Any
    preprocessor: Any
    metadata: dict[str, Any]
    model_path: Path
    preprocessor_path: Path | None
    metadata_path: Path | None

    def __iter__(self):
        """Permit ``model, preprocessor, metadata = load_*_model()``."""
        yield self.model
        yield self.preprocessor
        yield self.metadata

    @property
    def estimator(self) -> Any:
        """The fitted estimator, exposed for legacy service callers."""

        return self.model

    @property
    def pipeline(self) -> Any:
        return self.model

    def predict(self, values: Any) -> Any:
        return self.model.predict(_transform(self, values))

    def predict_proba(self, values: Any) -> Any:
        return self.model.predict_proba(_transform(self, values))

    def transform(self, values: Any) -> Any:
        if self.preprocessor is not None:
            return self.preprocessor.transform(values)
        return self.model.transform(values)

    def __getattr__(self, name: str) -> Any:
        # Keep the public loader useful to code that historically expected an
        # estimator rather than the richer artifact bundle.
        try:
            return getattr(self.model, name)
        except AttributeError as exc:
            raise AttributeError(name) from exc


_CACHE: dict[tuple[str, str], tuple[tuple[Any, ...], _Artifacts]] = {}
_CACHE_LOCK = threading.RLock()


def _model_directory(model_directory: str | Path | None = None) -> Path:
    if model_directory is not None:
        return Path(model_directory).expanduser()
    try:
        from django.conf import settings

        configured = getattr(settings, "MODEL_DIRECTORY", None)
        if configured:
            return Path(configured).expanduser()
        base_dir = Path(getattr(settings, "BASE_DIR", Path.cwd()))
        return base_dir / "data" / "models"
    except Exception:
        return Path.cwd() / "data" / "models"


def _paths(kind: str, model_directory: str | Path | None = None) -> tuple[Path, Path | None, Path | None]:
    directory = _model_directory(model_directory)
    if kind not in {"score", "outcome"}:
        raise ValueError(f"Unknown prediction kind: {kind}")

    def first_existing(names: list[str], fallback: str) -> Path:
        for name in names:
            candidate = directory / name
            if candidate.exists():
                return candidate
        return directory / fallback

    # The official jobs publish the first three names.  The alternatives keep
    # the request-time loader compatible with small local/older experiments.
    model_path = first_existing(
        [
            f"{kind}_model.joblib",
            f"{kind}_model.pkl",
            f"{kind}_pipeline.joblib",
            f"{kind}.joblib",
        ],
        f"{kind}_model.joblib",
    )
    preprocessor_path = first_existing(
        [
            f"{kind}_preprocessor.joblib",
            f"{kind}_preprocessor.pkl",
        ],
        f"{kind}_preprocessor.joblib",
    )
    metadata_path = first_existing(
        [f"{kind}_metadata.json"],
        f"{kind}_metadata.json",
    )
    return (
        model_path,
        preprocessor_path if preprocessor_path.exists() else None,
        metadata_path if metadata_path.exists() else None,
    )


def _signature(paths: tuple[Path | None, ...]) -> tuple[Any, ...]:
    signature: list[Any] = []
    for path in paths:
        if path is None:
            signature.append(None)
        else:
            try:
                stat = path.stat()
                signature.append((str(path), stat.st_mtime_ns, stat.st_size))
            except OSError:
                signature.append((str(path), None, None))
    return tuple(signature)


def _read_metadata(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError, TypeError) as exc:
        raise ModelNotTrainedError(
            "The model metadata file is unreadable. Retrain the model from the project root."
        ) from exc
    if not isinstance(value, dict):
        raise ModelNotTrainedError(
            "The model metadata file is invalid. Retrain the model from the project root."
        )
    return value


def _load_artifacts(kind: str, model_directory: str | Path | None = None) -> _Artifacts:
    if kind not in {"score", "outcome"}:
        raise ValueError(f"Unknown prediction kind: {kind}")
    model_path, preprocessor_path, metadata_path = _paths(kind, model_directory)
    if not model_path.exists():
        raise ModelNotTrainedError(
            f"The {kind} model has not been trained. Run scripts/train_{kind}_model.py after importing data."
        )
    signature = _signature((model_path, preprocessor_path, metadata_path))
    cache_key = (str(model_path.parent), kind)
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        if cached and cached[0] == signature:
            return cached[1]

        try:
            import joblib

            model = joblib.load(model_path)
        except FileNotFoundError as exc:
            raise ModelNotTrainedError(
                f"The {kind} model artifact is missing. Run the corresponding training script."
            ) from exc
        except Exception as exc:
            raise ModelNotTrainedError(
                f"The {kind} model artifact could not be loaded. Retrain it from the project root."
            ) from exc

        metadata = _read_metadata(metadata_path)
        preprocessor = None
        # Supporting a bundle makes the loader tolerant of locally produced
        # artifacts, while the official jobs still publish separate files.
        if isinstance(model, Mapping) and ("model" in model or "pipeline" in model or "estimator" in model):
            bundle = model
            preprocessor = bundle.get("preprocessor")
            embedded_metadata = bundle.get("metadata")
            if isinstance(embedded_metadata, dict):
                metadata = {**embedded_metadata, **metadata}
            model = bundle.get("model")
            if model is None:
                model = bundle.get("pipeline")
            if model is None:
                model = bundle.get("estimator")
        if preprocessor is None and preprocessor_path is not None:
            try:
                import joblib

                preprocessor = joblib.load(preprocessor_path)
            except Exception as exc:
                raise ModelNotTrainedError(
                    f"The {kind} preprocessor artifact is missing or unreadable. Retrain the model."
                ) from exc
        if model is None:
            raise ModelNotTrainedError(
                f"The {kind} model artifact does not contain an estimator. Retrain the model."
            )
        # A pipeline can be self-contained.  Otherwise a separate
        # preprocessor is required to turn the request's named feature row
        # into model input.  ``Pipeline`` exposes ``named_steps`` even when
        # its final estimator is a regressor/classifier without ``transform``.
        has_internal_steps = hasattr(model, "named_steps") or hasattr(model, "steps")
        if preprocessor is None and not (hasattr(model, "transform") or has_internal_steps):
            raise ModelNotTrainedError(
                f"The {kind} model has no usable preprocessor. Retrain the model."
            )

        artifacts = _Artifacts(
            kind=kind,
            model=model,
            preprocessor=preprocessor,
            metadata=metadata,
            model_path=model_path,
            preprocessor_path=preprocessor_path,
            metadata_path=metadata_path,
        )
        _CACHE[cache_key] = (signature, artifacts)
        return artifacts


def clear_model_cache(kind: str | None = None) -> None:
    """Clear cached artifacts, primarily for management commands and tests."""

    with _CACHE_LOCK:
        if kind is None:
            _CACHE.clear()
        else:
            for key in list(_CACHE):
                if key[1] == kind:
                    del _CACHE[key]


def _feature_names(artifacts: _Artifacts) -> list[str]:
    metadata_names = artifacts.metadata.get("feature_names")
    if isinstance(metadata_names, (list, tuple)) and metadata_names:
        return [str(name) for name in metadata_names]
    for estimator in (artifacts.preprocessor, artifacts.model):
        names = getattr(estimator, "feature_names_in_", None)
        if names is not None:
            return [str(name) for name in names]
    return list(SCORE_FEATURE_NAMES if artifacts.kind == "score" else OUTCOME_FEATURE_NAMES)


def _matrix(artifacts: _Artifacts, values: Mapping[str, Any] | Any):
    names = _feature_names(artifacts)
    row = feature_row(normalise_feature_input(values), names)
    # ColumnTransformer and Pipeline steps fitted with named columns need a
    # DataFrame at request time.  Keep the array fallback for genuinely
    # positional test fixtures/legacy artifacts.
    try:
        import pandas as pd

        frame = pd.DataFrame([row], columns=names)
        positional = getattr(artifacts.preprocessor, "feature_names_in_", None)
        if positional is None:
            positional = getattr(artifacts.model, "feature_names_in_", None)
        if positional is not None and all(
            isinstance(name, (int, float)) and not isinstance(name, bool)
            for name in positional
        ):
            import numpy as np

            positional_values = [row.get(str(name), row.get(name)) for name in positional]
            return np.asarray([positional_values], dtype=object)
        return frame
    except ImportError:  # pragma: no cover - requirements include pandas
        pass
    except (TypeError, ValueError):
        pass

    import numpy as np

    return np.asarray(
        [[row.get(name, row.get(str(name))) for name in names]],
        dtype=object,
    )


def _transform(artifacts: _Artifacts, values: Mapping[str, Any] | Any):
    matrix = _matrix(artifacts, values)
    try:
        if artifacts.preprocessor is not None:
            matrix = artifacts.preprocessor.transform(matrix)
        return matrix
    except Exception as exc:
        raise ModelPredictionError(
            "The prediction features could not be prepared. Check the teams, venue, and numeric values."
        ) from exc


def _first(value: Any) -> Any:
    """Return the first prediction without treating a string as a sequence."""

    if isinstance(value, (str, bytes)):
        return value
    try:
        return value[0]
    except (IndexError, KeyError, TypeError):
        return value


def _finite_float(value: Any) -> float:
    try:
        if hasattr(value, "size") and getattr(value, "size") == 1:
            value = value.reshape(-1)[0]
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ModelPredictionError("The trained model returned a non-numeric score.") from exc
    if number != number or number in (float("inf"), float("-inf")):
        raise ModelPredictionError("The trained model returned an invalid score.")
    return number


def _score_range(metadata: Mapping[str, Any], point: float) -> tuple[int | None, int | None, str | None]:
    """Build a range from measured hold-out error, never a guessed percentage."""

    error = metadata.get("residual_error")
    if not isinstance(error, Mapping):
        error = metadata.get("residual_error_range")
    if not isinstance(error, Mapping):
        error = metadata.get("score_range")
    if not isinstance(error, Mapping):
        return None, None, None

    # Absolute residual quantiles are centred on the point estimate and
    # guarantee a useful interval even when the hold-out model has a bias.
    absolute = error.get("absolute_error_quantiles")
    if isinstance(absolute, Mapping):
        try:
            spread = float(absolute.get("q90"))
        except (TypeError, ValueError):
            spread = float("nan")
        if spread == spread and spread not in (float("inf"), float("-inf")) and spread >= 0:
            low_value = point - spread
            high_value = point + spread
        else:
            low_value = high_value = float("nan")
    else:
        low_value = high_value = float("nan")

    if low_value != low_value or high_value != high_value:
        low_offset = error.get(
            "range_low_offset",
            error.get("low_offset", error.get("residual_low", error.get("low"))),
        )
        high_offset = error.get(
            "range_high_offset",
            error.get("high_offset", error.get("residual_high", error.get("high"))),
        )
        try:
            low_offset = float(low_offset)
            high_offset = float(high_offset)
        except (TypeError, ValueError):
            return None, None, None
        if not all(value == value and value not in (float("inf"), float("-inf")) for value in (low_offset, high_offset)):
            return None, None, None
        if low_offset > high_offset:
            low_offset, high_offset = high_offset, low_offset
        low_value, high_value = point + low_offset, point + high_offset

    # Always include the point estimate.  This also handles older artifacts
    # whose signed residual quantiles happened to be both positive.
    low_value = min(low_value, point)
    high_value = max(high_value, point)
    low = min(max(0, int(round(low_value))), 32767)
    high = min(max(low, int(round(high_value))), 32767)
    score_range_metadata = metadata.get("score_range", {})
    method = str(
        error.get("method")
        or (score_range_metadata.get("method") if isinstance(score_range_metadata, Mapping) else "")
        or "empirical_absolute_residual_error"
    )
    return low, high, method


def _plain_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_value(item) for item in value]
    if hasattr(value, "item"):
        try:
            return _plain_value(value.item())
        except (TypeError, ValueError):
            pass
    return value


def _model_metrics(metadata: Mapping[str, Any]) -> dict[str, Any]:
    metrics = metadata.get("metrics", {})
    return _plain_value(metrics) if isinstance(metrics, Mapping) else {}


def predict_score(values: Mapping[str, Any] | Any) -> dict[str, Any]:
    """Predict an innings score and, when available, its empirical range."""

    artifacts = _load_artifacts("score")
    matrix = _transform(artifacts, values)
    try:
        raw = artifacts.model.predict(matrix)
        point = _finite_float(_first(raw))
    except ModelPredictionError:
        raise
    except Exception as exc:
        raise ModelPredictionError("The trained score model could not make a prediction.") from exc

    point = min(max(0.0, point), 32767.0)
    normalized_values = normalise_feature_input(values)
    current_score = normalized_values.get("current_score")
    if isinstance(current_score, (int, float)):
        point = min(max(point, float(current_score)), 32767.0)
    low, high, method = _score_range(artifacts.metadata, point)
    if isinstance(current_score, (int, float)) and low is not None:
        low = max(low, int(round(float(current_score))))
        high = max(high or low, low)
    result = {
        "prediction": int(round(point)),
        "predicted_score": int(round(point)),
        "range_low": low,
        "range_high": high,
        "prediction_low": low,
        "prediction_high": high,
        "range_method": method,
        "model_version": str(artifacts.metadata.get("model_version", "")),
        "metrics": _model_metrics(artifacts.metadata),
    }
    return result


def _class_labels(model: Any, fallback: list[str] | None = None) -> list[str]:
    labels = getattr(model, "classes_", None)
    if labels is None:
        labels = fallback or []
    result: list[str] = []
    for label in labels:
        if hasattr(label, "item"):
            try:
                label = label.item()
            except (TypeError, ValueError):
                pass
        result.append(str(label))
    return result


def predict_outcome(values: Mapping[str, Any] | Any) -> dict[str, Any]:
    """Predict a winner; include probabilities only when the classifier emits them."""

    artifacts = _load_artifacts("outcome")
    matrix = _transform(artifacts, values)
    try:
        raw = artifacts.model.predict(matrix)
        prediction = _first(raw)
        if hasattr(prediction, "item"):
            prediction = prediction.item()
        prediction = str(prediction)
    except Exception as exc:
        raise ModelPredictionError("The trained outcome model could not make a prediction.") from exc

    probabilities: dict[str, float] = {}
    probability_available = False
    # Never convert a decision score into a made-up probability.  Only a real
    # classifier predict_proba result is persisted or returned.
    if hasattr(artifacts.model, "predict_proba"):
        try:
            raw_probabilities = artifacts.model.predict_proba(matrix)
            # sklearn returns a 2-D ``(samples, classes)`` array, while small
            # compatible artifacts sometimes return one already-selected row.
            if hasattr(raw_probabilities, "ndim") and raw_probabilities.ndim > 1:
                values_array = _first(raw_probabilities)
            elif (
                isinstance(raw_probabilities, (list, tuple))
                and raw_probabilities
                and isinstance(raw_probabilities[0], (list, tuple))
            ):
                values_array = raw_probabilities[0]
            else:
                values_array = raw_probabilities
            labels = _class_labels(artifacts.model, artifacts.metadata.get("classes", []))
            if hasattr(values_array, "__len__") and len(values_array) == len(labels):
                for label, probability in zip(labels, values_array):
                    probability = _finite_float(probability)
                    if probability < 0 or probability > 1:
                        raise ModelPredictionError("The classifier returned an invalid probability.")
                    probabilities[label] = probability
                probability_available = bool(probabilities)
        except ModelPredictionError:
            raise
        except Exception as exc:
            raise ModelPredictionError(
                "The classifier probabilities could not be read; no probabilities were returned."
            ) from exc

    result = {
        "prediction": prediction,
        "predicted_winner": prediction,
        "winner": prediction,
        "has_probabilities": probability_available,
        "model_version": str(artifacts.metadata.get("model_version", "")),
        "metrics": _model_metrics(artifacts.metadata),
        "probabilities_calibrated": bool(
            artifacts.metadata.get("probabilities", {}).get("calibrated", False)
            if isinstance(artifacts.metadata.get("probabilities", {}), Mapping)
            else False
        ),
    }
    # Do not expose probability keys at all for a classifier without a
    # predict_proba implementation.  An empty map can be mistaken for a
    # fabricated all-zero result by API clients.
    if probability_available:
        result["probabilities"] = probabilities
        result["outcome_probabilities"] = probabilities
    return result


def model_status(kind: str, model_directory: str | Path | None = None) -> dict[str, Any]:
    """Return a non-throwing status object suitable for a template/API."""

    if kind not in {"score", "outcome"}:
        raise ValueError(f"Unknown prediction kind: {kind}")
    try:
        artifacts = _load_artifacts(kind, model_directory=model_directory)
    except ModelNotTrainedError as exc:
        return {"kind": kind, "trained": False, "message": str(exc)}
    except Exception:
        return {
            "kind": kind,
            "trained": False,
            "message": "The model artifact could not be loaded. Retrain it from the project root.",
        }
    return {
        "kind": kind,
        "trained": True,
        "model_version": str(artifacts.metadata.get("model_version", "")),
        "metrics": _model_metrics(artifacts.metadata),
        "model_path": str(artifacts.model_path),
        "preprocessor_path": str(artifacts.preprocessor_path) if artifacts.preprocessor_path else None,
        "metadata_path": str(artifacts.metadata_path) if artifacts.metadata_path else None,
    }


# Public loader aliases.  They return the immutable artifact bundle so callers
# can inspect the estimator, preprocessor, and metadata without triggering a
# second disk load.
def load_score_model(model_directory: str | Path | None = None) -> _Artifacts:
    return _load_artifacts("score", model_directory=model_directory)


def load_outcome_model(model_directory: str | Path | None = None) -> _Artifacts:
    return _load_artifacts("outcome", model_directory=model_directory)


get_score_model = load_score_model
get_outcome_model = load_outcome_model


def load_model(kind: str, model_directory: str | Path | None = None) -> _Artifacts:
    """Load either artifact kind through the same lazy/cached path."""

    return _load_artifacts(kind, model_directory=model_directory)


get_model = load_model


def is_model_trained(kind: str) -> bool:
    return bool(model_status(kind).get("trained"))


# Compatibility aliases used by older notebooks/tests.
predict_score_model = predict_score
predict_match_outcome = predict_outcome

