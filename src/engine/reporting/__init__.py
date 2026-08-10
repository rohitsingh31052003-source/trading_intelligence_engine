"""
Evaluation reporting layer (Sprint 11G).

This package sits ABOVE the historical evaluation pipeline
(Sprint 11F). It turns a ``PipelineResult`` into a structured,
reusable, immutable ``EvaluationReport`` suitable for comparing
historical runs and eventually supporting strategy research.

Dependency direction:

    models
       ↑
    intelligence engines
       ↑
    pipeline / orchestration
       ↑
    reporting / aggregation
"""

from engine.models.evaluation import (
    EvaluationReport,
    PipelineStatistics,
    SignalStatistics,
    TradeStatistics,
)
from engine.reporting.comparison import (
    ExperimentComparisonFormatter,
)
from engine.reporting.evaluation import EvaluationReportEngine
from engine.reporting.experiment import (
    ExperimentReportFormatter,
)

__all__ = [
    "EvaluationReport",
    "EvaluationReportEngine",
    "ExperimentComparisonFormatter",
    "ExperimentReportFormatter",
    "PipelineStatistics",
    "SignalStatistics",
    "TradeStatistics",
]
