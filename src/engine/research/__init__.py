"""
Research / robustness layer (Sprint 11H).

This package sits ABOVE the evaluation reporting layer
(Sprint 11G). It answers research questions about an existing
strategy: regime performance, performance segmentation,
parameter sensitivity, out-of-sample evaluation and
data-leakage auditing.

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
"""

from engine.models.research import (
    ConfidenceBucket,
    LeakageCheckResult,
    MarketRegime,
    OutOfSampleReport,
    ParameterResult,
    ParameterSensitivityReport,
    RegimeStatistics,
    ResearchReport,
    RiskRewardBucket,
    SegmentStatistics,
    SegmentedPerformance,
    SegmentationDimension,
)
from engine.research.leakage import (
    LeakageAuditConfig,
    LeakageAuditEngine,
)
from engine.research.out_of_sample import (
    OutOfSampleConfig,
    OutOfSampleEngine,
)
from engine.research.regime import (
    MarketRegimeEngine,
    RegimeConfig,
)
from engine.research.research import (
    ResearchConfig,
    ResearchEngine,
)
from engine.research.segmentation import (
    PerformanceSegmentationEngine,
    SegmentationConfig,
)
from engine.research.sensitivity import (
    ParameterSensitivityEngine,
    SensitivityConfig,
)

__all__ = [
    "ConfidenceBucket",
    "LeakageAuditConfig",
    "LeakageAuditEngine",
    "LeakageCheckResult",
    "MarketRegime",
    "MarketRegimeEngine",
    "OutOfSampleConfig",
    "OutOfSampleEngine",
    "OutOfSampleReport",
    "ParameterResult",
    "ParameterSensitivityEngine",
    "ParameterSensitivityReport",
    "PerformanceSegmentationEngine",
    "RegimeConfig",
    "RegimeStatistics",
    "ResearchConfig",
    "ResearchEngine",
    "ResearchReport",
    "RiskRewardBucket",
    "SegmentStatistics",
    "SegmentationConfig",
    "SegmentationDimension",
    "SegmentedPerformance",
    "SensitivityConfig",
]
