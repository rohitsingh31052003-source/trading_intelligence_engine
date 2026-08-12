"""
Evaluation reporting layer (Sprint 11G + 11J + 11L + 11M).

This package sits ABOVE the historical evaluation pipeline
(Sprint 11F). It turns a ``PipelineResult`` into a structured,
reusable, immutable ``EvaluationReport`` suitable for comparing
historical runs and eventually supporting strategy research. It
also hosts the experiment comparison / report formatters
(Sprint 11J), the experiment query / grouping / analysis
formatters (Sprint 11L), the experiment suite / suite comparison
formatters (Sprint 11M) and the selection report formatter
(Sprint 11N).

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
       ↑
    suite / batch orchestration
       ↑
    selection & promotion
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
from engine.reporting.query import (
    ExperimentAnalysisFormatter,
    ExperimentGroupingFormatter,
    ExperimentQueryFormatter,
)
from engine.reporting.selection import (
    SelectionReportFormatter,
)
from engine.reporting.suite import (
    SuiteComparisonFormatter,
    SuiteReportFormatter,
)

__all__ = [
    "EvaluationReport",
    "EvaluationReportEngine",
    "ExperimentAnalysisFormatter",
    "ExperimentComparisonFormatter",
    "ExperimentGroupingFormatter",
    "ExperimentQueryFormatter",
    "ExperimentReportFormatter",
    "PipelineStatistics",
    "SelectionReportFormatter",
    "SignalStatistics",
    "SuiteComparisonFormatter",
    "SuiteReportFormatter",
    "TradeStatistics",
]
