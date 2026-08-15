"""
Configuration for the decision intelligence foundation layer
(Sprint 12A).

The config carries the explicit, documented descriptive-context
thresholds used by the decision-intelligence factor wording. The
core :class:`~engine.models.decision_intelligence.DecisionContextStatus`
classification is a FIXED, deterministic mapping from the reused
Sprint 11Z :class:`~engine.models.strategy_intelligence.StrategyAssessmentStatus`
(and the Sprint 11Z lookup match status) — it is NOT configurable and
is NOT overridden by the descriptive thresholds below.

NO STATISTICAL CLAIMS:

The decision intelligence layer does NOT perform statistical
hypothesis tests and does NOT use terms such as "statistically
significant". The descriptive-context thresholds are deterministic,
configurable observation / metric gates used ONLY for the wording of
the :class:`~engine.models.decision_intelligence.DecisionEvidenceFactor`
factors. They NEVER upgrade the decision context status, NEVER
override the reused evidence strength (sample-size hard-gated by
Sprint 11Y), and NEVER replace the existing decision / scoring logic.

The decision intelligence layer is DESCRIPTIVE. It is NOT a trading
signal, NOT a prediction, NOT a probability of success, NOT a
profitability guarantee, NOT a buy / sell recommendation, and NOT a
statistical-significance claim. It is an INFORMATION / CONTEXT layer
presented to the existing decision process without altering it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DecisionIntelligenceConfig:
    """
    Threshold configuration for the decision intelligence engine.

    These thresholds are DESCRIPTIVE-METRIC CONTEXT (used in the
    factor wording, never as a hard gate that overrides the reused
    evidence strength or the decision context status). The core
    status mapping is the fixed
    :data:`engine.models.decision_intelligence.ASSESSMENT_TO_CONTEXT`
    contract.

    DESCRIPTIVE-METRIC CONTEXT (factor wording only):

    favorable_win_rate
        Win rate at/above which a FAVORABLE_HISTORICAL_CHARACTERISTICS
        factor may be emitted (descriptive context only; does NOT
        upgrade the decision context status). Default 0.55. In [0, 1].

    favorable_profit_factor
        Profit factor at/above which a favourable characteristic may
        be emitted (descriptive context only). Default 1.3. Must be > 0.

    favorable_avg_r
        Average realized R at/above which a favourable characteristic
        may be emitted (descriptive context only). Default 0.1. Must
        be non-negative.

    adverse_avg_r
        Average realized R below which an UNFAVORABLE_HISTORICAL_CHARACTERISTICS
        factor may be emitted (descriptive context only). Default 0.0.

    adverse_profit_factor
        Profit factor below which an unfavourable characteristic may
        be emitted (descriptive context only). Default 1.0. Must be > 0
        and <= favorable_profit_factor.

    label
        Optional descriptive label identifying the decision-intelligence
        run.

    metadata
        Optional descriptive metadata (sorted key/value pairs) carried
        for traceability.
    """

    favorable_win_rate: float = 0.55
    favorable_profit_factor: float = 1.3
    favorable_avg_r: float = 0.1
    adverse_avg_r: float = 0.0
    adverse_profit_factor: float = 1.0
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not 0.0 <= self.favorable_win_rate <= 1.0:
            raise ValueError("favorable_win_rate must be in [0, 1].")
        if self.favorable_profit_factor <= 0:
            raise ValueError("favorable_profit_factor must be > 0.")
        if self.favorable_avg_r < 0:
            raise ValueError("favorable_avg_r must be non-negative.")
        if self.adverse_profit_factor <= 0:
            raise ValueError("adverse_profit_factor must be > 0.")
        if self.adverse_profit_factor > self.favorable_profit_factor:
            raise ValueError(
                "adverse_profit_factor must be <= favorable_profit_factor.",
            )

    def snapshot(self) -> tuple[tuple[str, str], ...]:
        """A sorted, auditable snapshot of the threshold values."""

        pairs = [
            ("favorable_win_rate", str(self.favorable_win_rate)),
            ("favorable_profit_factor", str(self.favorable_profit_factor)),
            ("favorable_avg_r", str(self.favorable_avg_r)),
            ("adverse_avg_r", str(self.adverse_avg_r)),
            ("adverse_profit_factor", str(self.adverse_profit_factor)),
        ]
        return tuple(sorted(pairs))


__all__ = ["DecisionIntelligenceConfig"]
