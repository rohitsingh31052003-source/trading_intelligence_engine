"""
Reproducibility metadata builder (Sprint 11J).

Builds an explicit, honest ``ReproducibilityMetadata`` record
for an experiment run. Every value that cannot be determined is
represented explicitly (``None`` or ``"UNKNOWN"``) rather than
fabricated.

The experiment ID remains fully deterministic: it is derived
solely from the immutable ``ExperimentConfig`` and is never
contaminated by nondeterministic values such as the current
time.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any, Mapping

from engine.config.swing_config import SwingConfig
from engine.models.experiment import (
    DatasetSpec,
    ReproducibilityMetadata,
)
from engine.models.ohlcv import OHLCVCandle
from engine.pipeline.historical_pipeline import PipelineConfig


_PACKAGE_NAME = "trading-intelligence-engine"


def _code_version() -> str:
    """
    Safely resolve the installed package version, when
    available. Returns ``"UNKNOWN"`` when the package metadata
    cannot be read. Never fabricates a version.
    """

    try:
        return version(_PACKAGE_NAME)
    except PackageNotFoundError:
        return "UNKNOWN"
    except Exception:
        return "UNKNOWN"


def dataset_content_hash(
    candles: list[OHLCVCandle] | tuple[OHLCVCandle, ...],
) -> str:
    """
    Deterministic SHA-256 (hex) of a candle sequence.

    The hash covers the canonical, sorted-key representation of
    each candle's OHLCV fields and timestamp. Identical candle
    sequences always yield the same hash; any change to any
    candle changes the hash.

    This is used both to populate the resolved dataset identity
    and to verify that a custom dataset's declared content hash
    matches the actual data.
    """

    import hashlib
    import json

    payload = [
        {
            "timestamp": _timestamp_string(c.timestamp),
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
            "volume": c.volume,
        }
        for c in candles
    ]

    representation = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
    )

    return hashlib.sha256(representation.encode("utf-8")).hexdigest()


def _timestamp_string(timestamp: Any) -> str:
    """
    Stable string for a candle timestamp.

    Uses ISO-8601 when the timestamp is a datetime; otherwise
    falls back to ``str()``.
    """

    iso = getattr(timestamp, "isoformat", None)

    if callable(iso):
        return iso()

    return str(timestamp)


def _parameter_values(
    pipeline: PipelineConfig,
    strategy_parameters: Mapping[str, str],
    seed: int | None,
) -> dict[str, str]:
    """
    Capture relevant parameter values for quick inspection.

    Only deterministic, human-meaningful values are captured.
    """

    values: dict[str, str] = {}

    swing = pipeline.swing_config
    values["pipeline.min_history"] = str(pipeline.min_history)
    values["pipeline.swing.lookback"] = str(swing.lookback)
    values[
        "pipeline.swing.confirmation_candles"
    ] = str(swing.confirmation_candles)
    values[
        "pipeline.swing.minimum_move_percent"
    ] = str(swing.minimum_move_percent)

    for key, val in strategy_parameters.items():
        values[f"strategy.{key}"] = str(val)

    if seed is not None:
        values["seed"] = str(seed)

    return values


def build_reproducibility_metadata(
    config: Any,
    dataset: DatasetSpec,
    dataset_size: int,
    actual_content_hash: str,
) -> ReproducibilityMetadata:
    """
    Construct a ``ReproducibilityMetadata`` from an experiment
    config and the resolved dataset.

    The configuration representation and hash are read directly
    from the immutable config so they are always consistent
    with the experiment ID.
    """

    representation = config.canonical_representation
    config_hash = config.configuration_hash

    parameter_values = _parameter_values(
        config.pipeline,
        config.strategy_parameters,
        config.seed,
    )

    reproducible = bool(representation) and bool(dataset.name)

    return ReproducibilityMetadata(
        experiment_id=config.experiment_id,
        configuration_hash=config_hash,
        configuration_representation=representation,
        dataset_identity=dataset.name,
        dataset_content_hash=actual_content_hash,
        dataset_size=dataset_size,
        parameter_values=parameter_values,
        code_version=_code_version(),
        random_seed=config.seed,
        reproducible=reproducible,
    )
