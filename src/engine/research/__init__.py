"""
Research / robustness layer (Sprint 11H, hardened in Sprint 11I).

This package sits ABOVE the evaluation reporting layer
(Sprint 11G). It answers research questions about an existing
strategy: regime performance, performance segmentation,
parameter sensitivity, parameter robustness, walk-forward
parameter selection, out-of-sample evaluation and
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

Sprint 11I hardening (additive, backward-compatible):

* ``WalkForwardParameterEngine`` -- explicit development /
  evaluation separation for parameter selection.
* ``ParameterRobustnessEngine`` -- descriptive best vs robust /
  stable configurations.
* Structured ``LeakageCheck`` / ``LeakageSeverity`` results with
  ``NOT_VERIFIED`` semantics (never falsely report PASS).
* ``DataSufficiencyReport`` -- sample-size awareness across
  trades, regimes, OOS and parameter observations.
"""

from engine.models.research import (
    CandidateResult,
    ConfigurationRobustness,
    ConfidenceBucket,
    DataSufficiencyReport,
    LeakageCheck,
    LeakageCheckResult,
    LeakageSeverity,
    MarketRegime,
    OutOfSampleReport,
    ParameterResult,
    ParameterRobustnessReport,
    ParameterSensitivityReport,
    RegimeStatistics,
    ResearchReport,
    RiskRewardBucket,
    SegmentStatistics,
    SegmentedPerformance,
    SegmentationDimension,
    SelectedConfiguration,
    WalkForwardSelectionReport,
)
from engine.research.leakage import (
    LeakageAuditConfig,
    LeakageAuditContext,
    LeakageAuditEngine,
)
from engine.research.out_of_sample import (
    OutOfSampleConfig,
    OutOfSampleEngine,
    PipelineEvaluator,
)
from engine.research.regime import (
    MarketRegimeEngine,
    RegimeConfig,
)
from engine.research.research import (
    ResearchConfig,
    ResearchEngine,
)
from engine.research.robustness import (
    ParameterRobustnessEngine,
    RobustnessConfig,
    build_sensitivity_for_robustness,
)
from engine.research.segmentation import (
    PerformanceSegmentationEngine,
    SegmentationConfig,
)
from engine.research.sensitivity import (
    ParameterSensitivityEngine,
    SensitivityConfig,
)
from engine.research.walk_forward import (
    WalkForwardConfig,
    WalkForwardEvaluator,
    WalkForwardParameterEngine,
)

__all__ = [
    "CandidateResult",
    "ConfigurationRobustness",
    "ConfidenceBucket",
    "DataSufficiencyReport",
    "LeakageAuditConfig",
    "LeakageAuditContext",
    "LeakageAuditEngine",
    "LeakageCheck",
    "LeakageCheckResult",
    "LeakageSeverity",
    "MarketRegime",
    "MarketRegimeEngine",
    "OutOfSampleConfig",
    "OutOfSampleEngine",
    "OutOfSampleReport",
    "ParameterResult",
    "ParameterRobustnessEngine",
    "ParameterRobustnessReport",
    "ParameterSensitivityEngine",
    "ParameterSensitivityReport",
    "PerformanceSegmentationEngine",
    "PipelineEvaluator",
    "RegimeConfig",
    "RegimeStatistics",
    "ResearchConfig",
    "ResearchEngine",
    "ResearchReport",
    "RiskRewardBucket",
    "RobustnessConfig",
    "SegmentStatistics",
    "SegmentationConfig",
    "SegmentationDimension",
    "SegmentedPerformance",
    "SelectedConfiguration",
    "SensitivityConfig",
    "WalkForwardConfig",
    "WalkForwardEvaluator",
    "WalkForwardParameterEngine",
    "WalkForwardSelectionReport",
    "build_sensitivity_for_robustness",
]
