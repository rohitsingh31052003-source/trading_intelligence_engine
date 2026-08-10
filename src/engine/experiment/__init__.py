"""
Reproducible Research Experiment Framework (Sprint 11J).

This package sits ABOVE the research / robustness layer
(Sprint 11H/11I). It makes complete research runs reproducible,
identifiable, comparable and reportable by orchestrating the
existing pipeline, reporting and research layers.

Dependency direction:

    models
       ↑
    intelligence engines
       ↑
    pipeline / orchestration
       ↑
    reporting / aggregation
       ↑
    research / robustness
       ↑
    experiment framework

The experiment framework implements NO trading, validation,
performance, pipeline or research logic. Every engine is reused
as-is. This is the fourth orchestration package (after
pipeline, reporting and research) and therefore re-exports its
public API for convenience.
"""

from engine.experiment.comparison import ExperimentComparisonEngine
from engine.experiment.config import (
    EvaluationConfig,
    ExperimentConfig,
)
from engine.experiment.reproducibility import (
    build_reproducibility_metadata,
    dataset_content_hash,
)
from engine.experiment.runner import ExperimentRunner
from engine.models.experiment import (
    DatasetSpec,
    ExperimentComparison,
    ExperimentComparisonRow,
    ExperimentEvidenceStatus,
    ExperimentResult,
    ExperimentSummary,
    ReproducibilityMetadata,
)

__all__ = [
    "DatasetSpec",
    "EvaluationConfig",
    "ExperimentComparison",
    "ExperimentComparisonEngine",
    "ExperimentComparisonRow",
    "ExperimentConfig",
    "ExperimentEvidenceStatus",
    "ExperimentResult",
    "ExperimentRunner",
    "ExperimentSummary",
    "ReproducibilityMetadata",
    "build_reproducibility_metadata",
    "dataset_content_hash",
]
