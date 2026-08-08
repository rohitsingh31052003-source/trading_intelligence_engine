"""
End-to-end historical evaluation pipeline (Sprint 11F).

This package contains the orchestration layer that connects the
existing intelligence engines into a single deterministic,
walk-forward evaluation of a historical candle sequence.

Dependency direction:

    models
       ↑
    intelligence engines
       ↑
    pipeline / orchestration
"""

from engine.pipeline.historical_pipeline import (
    HistoricalEvaluationPipeline,
    PipelineConfig,
)
from engine.pipeline.datasets import (
    flat_dataset,
    minimal_dataset,
    trending_dataset,
)

__all__ = [
    "HistoricalEvaluationPipeline",
    "PipelineConfig",
    "flat_dataset",
    "minimal_dataset",
    "trending_dataset",
]
