"""
Configuration for the evidence-conditioned strategy intelligence
layer (Sprint 11Z).

The config carries the explicit, documented descriptive-context
thresholds used by the strategy interpretation wording. The
core classification (the deterministic
:func:`~engine.models.strategy_intelligence.EVIDENCE_TO_STRATEGY`
mapping from the reused Sprint 11Y :class:`EvidenceStrength` to the
Sprint 11Z :class:`StrategyAssessmentStatus`) is NOT configurable —
it is a fixed reuse contract. The thresholds below ONLY affect the
DESCRIPTIVE wording of the ``interpretation`` text; they NEVER
upgrade an assessment's status and NEVER override the underlying
evidence strength (which is sample-size hard-gated by Sprint 11Y).

NO STATISTICAL CLAIMS:

The strategy layer does NOT perform statistical hypothesis tests and
does NOT use terms such as "statistically significant". The
descriptive-context thresholds are deterministic, configurable
observation / metric gates used ONLY for wording.

The strategy layer is DESCRIPTIVE. It is NOT a prediction, NOT a
probability of success, NOT a profitability guarantee, NOT a buy /
sell recommendation, and NOT a statistical-significance claim.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StrategyIntelligenceConfig:
    """
    Threshold configuration for the strategy intelligence engine.

    These thresholds are DESCRIPTIVE-METRIC CONTEXT (used in the
    interpretation wording, never as a hard gate that overrides the
    reused evidence strength). The core status mapping is the fixed
    :data:`engine.models.strategy_intelligence.EVIDENCE_TO_STRATEGY`
    contract.

    DESCRIPTIVE-METRIC CONTEXT (interpretation wording only):

    favorable_win_rate
        Win rate at/above which the interpretation notes the observed
        result is favourable (descriptive context only; does NOT
        upgrade the assessment status). Default 0.55. In [0, 1].

    favorable_profit_factor
        Profit factor at/above which the interpretation notes the
        observed result is favourable (descriptive context only).
        Default 1.3. Must be > 0.

    favorable_avg_r
        Average realized R at/above which the interpretation notes the
        observed result is favourable (descriptive context only).
        Default 0.1. Must be non-negative.

    label
        Optional descriptive label identifying the strategy run.

    metadata
        Optional descriptive metadata (sorted key/value pairs) carried
        for traceability.

    lookup_max_dimensions
        The maximum number of opportunity-profile dimensions the
        lookup will attempt to match against simultaneously when
        selecting the most specific matching cohort. Default 2
        (mirrors the Sprint 11Y controlled composite-cohort limit so
        the lookup never expands beyond the supported cohort specs).
        Must be in [1, 2].
    """

    favorable_win_rate: float = 0.55
    favorable_profit_factor: float = 1.3
    favorable_avg_r: float = 0.1
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = ()
    lookup_max_dimensions: int = 2

    def __post_init__(self) -> None:
        if not 0.0 <= self.favorable_win_rate <= 1.0:
            raise ValueError("favorable_win_rate must be in [0, 1].")
        if self.favorable_profit_factor <= 0:
            raise ValueError("favorable_profit_factor must be > 0.")
        if self.favorable_avg_r < 0:
            raise ValueError("favorable_avg_r must be non-negative.")
        if not 1 <= self.lookup_max_dimensions <= 2:
            raise ValueError(
                "lookup_max_dimensions must be in [1, 2].",
            )

    def snapshot(self) -> tuple[tuple[str, str], ...]:
        """A sorted, auditable snapshot of the threshold values."""

        pairs = [
            ("favorable_win_rate", str(self.favorable_win_rate)),
            ("favorable_profit_factor", str(self.favorable_profit_factor)),
            ("favorable_avg_r", str(self.favorable_avg_r)),
            ("lookup_max_dimensions", str(self.lookup_max_dimensions)),
        ]
        return tuple(sorted(pairs))


__all__ = ["StrategyIntelligenceConfig"]
