"""
Experiment Query, Filtering & Analysis Layer (Sprint 11L).

This package sits ABOVE the experiment registry / persistence layer
(Sprint 11K). It makes persisted experiment results discoverable,
filterable, sortable, groupable and summarizable WITHOUT rerunning
the underlying trading pipeline.

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
       ↑
    registry / persistence
       ↑
    query / filtering / analysis

The query layer implements NO trading, validation, performance,
pipeline, research, comparison or persistence logic. Every value is
read from the persisted ``ExperimentResult`` produced by the
existing engines. This is the sixth orchestration package (after
pipeline, reporting, research, experiment and registry) and
therefore re-exports its public API for convenience.

Evidence safety is preserved from Sprint 11J: descriptive
"best"/"leader" results are computed ONLY among experiments with
SUFFICIENT evidence and are ``None`` otherwise. The layer never
turns descriptive historical results into predictive claims.
"""

from engine.models.query import (
    ExperimentAnalysisSummary,
    ExperimentFilter,
    ExperimentGroup,
    ExperimentGroupDimension,
    ExperimentGrouping,
    ExperimentQueryError,
    ExperimentQueryRow,
    ExperimentSortKey,
    MetricLeader,
    SortOrder,
)
from engine.query.query import ExperimentQueryEngine

__all__ = [
    "ExperimentAnalysisSummary",
    "ExperimentFilter",
    "ExperimentGroup",
    "ExperimentGroupDimension",
    "ExperimentGrouping",
    "ExperimentQueryEngine",
    "ExperimentQueryError",
    "ExperimentQueryRow",
    "ExperimentSortKey",
    "MetricLeader",
    "SortOrder",
]
