"""
Configuration for the historical evidence / validation layer
(Sprint 11Y).

The config carries the explicit, documented thresholds that separate
INSUFFICIENT / WEAK / MODERATE / STRONG evidence. The thresholds are
about EVIDENCE QUALITY (how much reliable historical observation
backs a cohort's metrics), NOT about profitability. They are driven
primarily by SAMPLE SIZE and RESOLVED observation counts.

CRITICAL — sample size is a HARD GATE:

A cohort whose total sample is below ``min_sample_total`` is ALWAYS
classified INSUFFICIENT, regardless of how favourable its observed win
rate or realized R may be. A small sample is never promoted to a
stronger evidence level merely because its observed result is
impressive. This prevents the classic small-sample overfitting trap
(e.g. one historical trade at +2R being treated as stronger evidence
than 100 trades at a modest but consistent result).

NO STATISTICAL CLAIMS:

The evidence layer does NOT perform statistical hypothesis tests and
does NOT use terms such as "statistically significant". The thresholds
are deterministic, configurable observation-count gates plus
descriptive-metric context. Their meaning is documented on every
field below.

The evidence layer is DESCRIPTIVE. It is NOT a prediction, NOT a
probability of success, NOT a profitability guarantee, and NOT a
trading recommendation.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EvidenceConfig:
    """
    Threshold configuration for the historical evidence engine.

    All thresholds are non-negative integers (observation counts) or
    non-negative floats (descriptive-metric context). They are
    documented below. Changing them changes evidence classifications
    deterministically.

    SAMPLE GATES (observation counts; the primary evidence driver):

    min_sample_total
        Minimum total outcomes a cohort must have to be anything other
        than INSUFFICIENT. Below this -> INSUFFICIENT (hard gate,
        regardless of observed win rate). Default 30: a commonly used
        heuristic floor below which descriptive statistics are
        considered unreliable for inference. Must be >= 1.

    min_resolved
        Minimum RESOLVED outcomes (TARGET_HIT + STOP_HIT + BOTH_TOUCHED
        + EXPIRED) for a cohort to reach at least WEAK. Below
        ``min_sample_total`` -> INSUFFICIENT; at/above
        ``min_sample_total`` but below ``min_resolved`` -> WEAK.
        Default 10.

    min_valid_r
        Minimum valid realized-R observations for a cohort to reach at
        least WEAK when its sample is otherwise adequate. Realized R
        is the core quantitative evidence; too few R observations means
        R-based metrics (profit factor, average / median R) are not
        meaningful. Default 10.

    STRONG GATES:

    strong_min_sample
        Minimum total outcomes for STRONG evidence. Default 50: a
        substantially larger sample supporting reliable inference.
        Must be >= ``min_sample_total``.

    strong_min_resolved
        Minimum resolved outcomes for STRONG evidence. Default 30.
        Must be >= ``min_resolved``.

    strong_min_valid_r
        Minimum valid realized-R observations for STRONG evidence.
        Default 30. Must be >= ``min_valid_r``.

    DESCRIPTIVE-METRIC CONTEXT (used in the rationale, never as a
    hard gate that overrides the sample gates):

    favorable_win_rate
        Win rate at/above which the rationale notes the observed result
        is favourable (descriptive context only; does NOT upgrade
        evidence strength on its own). Default 0.55. In [0, 1].

    favorable_profit_factor
        Profit factor at/above which the rationale notes the observed
        result is favourable (descriptive context only). Default 1.3.
        Must be > 0.

    favorable_avg_r
        Average realized R at/above which the rationale notes the
        observed result is favourable (descriptive context only).
        Default 0.1.

    label
        Optional descriptive label identifying the evidence run.

    metadata
        Optional descriptive metadata (sorted key/value pairs) carried
        for traceability.
    """

    min_sample_total: int = 30
    min_resolved: int = 10
    min_valid_r: int = 10

    strong_min_sample: int = 50
    strong_min_resolved: int = 30
    strong_min_valid_r: int = 30

    favorable_win_rate: float = 0.55
    favorable_profit_factor: float = 1.3
    favorable_avg_r: float = 0.1

    label: str = ""
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.min_sample_total < 1:
            raise ValueError("min_sample_total must be >= 1.")
        if self.min_resolved < 0:
            raise ValueError("min_resolved must be non-negative.")
        if self.min_valid_r < 0:
            raise ValueError("min_valid_r must be non-negative.")
        if self.strong_min_sample < self.min_sample_total:
            raise ValueError(
                "strong_min_sample must be >= min_sample_total.",
            )
        if self.strong_min_resolved < self.min_resolved:
            raise ValueError(
                "strong_min_resolved must be >= min_resolved.",
            )
        if self.strong_min_valid_r < self.min_valid_r:
            raise ValueError(
                "strong_min_valid_r must be >= min_valid_r.",
            )
        if not 0.0 <= self.favorable_win_rate <= 1.0:
            raise ValueError("favorable_win_rate must be in [0, 1].")
        if self.favorable_profit_factor <= 0:
            raise ValueError("favorable_profit_factor must be > 0.")
        if self.favorable_avg_r < 0:
            raise ValueError("favorable_avg_r must be non-negative.")

    def snapshot(self) -> tuple[tuple[str, str], ...]:
        """A sorted, auditable snapshot of the threshold values."""

        pairs = [
            ("min_sample_total", str(self.min_sample_total)),
            ("min_resolved", str(self.min_resolved)),
            ("min_valid_r", str(self.min_valid_r)),
            ("strong_min_sample", str(self.strong_min_sample)),
            ("strong_min_resolved", str(self.strong_min_resolved)),
            ("strong_min_valid_r", str(self.strong_min_valid_r)),
            ("favorable_win_rate", str(self.favorable_win_rate)),
            ("favorable_profit_factor", str(self.favorable_profit_factor)),
            ("favorable_avg_r", str(self.favorable_avg_r)),
        ]
        return tuple(sorted(pairs))


__all__ = ["EvidenceConfig"]
