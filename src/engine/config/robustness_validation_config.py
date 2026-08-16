"""
Configuration for the robustness, failure-mode & edge-case hardening
validation layer (Sprint 12D).

The config is intentionally MINIMAL. Sprint 12D is a VALIDATION /
HARDENING layer, NOT a new decision / scoring / predictive layer. It
has NO strategy parameters, NO optimization parameters, NO probability
thresholds and NO trading logic. It only carries:

* an accounting-reconciliation tolerance (used when reconciling R
  aggregates independently recomputed from raw outcomes against the
  Sprint 11X reported statistics),
* the 11Y evidence sample-size boundaries that 12D exercises at the
  edge (so the engine knows where to place a deliberately-insufficient
  cohort vs. a sufficient one when building boundary scenarios), and
* descriptive label / metadata used to identify a validation run.

The validation layer REUSES the existing Sprint 11X / 11Y / 11Z / 12A
/ 12B engines verbatim; it performs INDEPENDENT CROSS-CHECKS of their
already-computed outputs (it never duplicates the trading / outcome /
analytics / evidence / decision logic). The only numerics it
introduces are a tiny floating-point tolerance and the 11Y sample-size
boundaries (mirrored from the 11Y config defaults) so deterministic
floating-point rounding noise never produces a spurious accounting
failure and so boundary scenarios land precisely on the 11Y hard
evidence gate.

NO PREDICTIVE CLAIMS:

The validation layer does NOT perform statistical hypothesis tests and
does NOT use terms such as "statistically significant". Validation
results are DESCRIPTIVE: they report whether the existing architecture
remains internally consistent, deterministic, leak-free and
accounting-consistent under the supplied boundary / adversarial /
empty / malformed-representable conditions. They do NOT predict future
market behavior and do NOT guarantee profitability.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RobustnessValidationConfig:
    """
    Minimal configuration for the robustness validation engine.

    Attributes:

    accounting_tolerance
        Absolute floating-point tolerance used when reconciling R
        aggregates (total / average / median / gross positive /
        gross negative / profit factor) independently recomputed from
        the raw outcomes against the Sprint 11X reported statistics.
        Defaults to ``1e-9``. Must be non-negative. This is a numeric
        tolerance ONLY; it never weakens a count / status / evidence
        comparison (those are compared by exact equality).

    evidence_min_sample_total
        Mirrors the Sprint 11Y ``EvidenceConfig.min_sample_total``
        default (30). 12D uses this ONLY to classify whether a
        boundary cohort is below / at / above the 11Y hard evidence
        gate when reporting descriptive boundary-scenario context. It
        NEVER overrides the real 11Y classification (the real engine
        always classifies against its own config). Must be >= 1.

    evidence_strong_min_sample
        Mirrors the Sprint 11Y ``EvidenceConfig.strong_min_sample``
        default (50). Used ONLY for descriptive boundary context.
        Must be >= ``evidence_min_sample_total``.

    lookup_max_dimensions
        Mirrors the Sprint 11Z ``StrategyIntelligenceConfig.
        lookup_max_dimensions`` default (2). 12D uses this ONLY to
        report descriptive lookup-dimension-cap context; it never
        changes the real 11Z lookup behaviour. Must be in ``[1, 2]``.

    label
        Optional descriptive label identifying the validation run.

    metadata
        Optional descriptive metadata (sorted key/value pairs) carried
        for traceability.
    """

    accounting_tolerance: float = 1e-9
    evidence_min_sample_total: int = 30
    evidence_strong_min_sample: int = 50
    lookup_max_dimensions: int = 2
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.accounting_tolerance < 0:
            raise ValueError("accounting_tolerance must be non-negative.")
        if self.evidence_min_sample_total < 1:
            raise ValueError("evidence_min_sample_total must be >= 1.")
        if self.evidence_strong_min_sample < self.evidence_min_sample_total:
            raise ValueError(
                "evidence_strong_min_sample must be >= "
                "evidence_min_sample_total.",
            )
        if self.lookup_max_dimensions not in (1, 2):
            raise ValueError("lookup_max_dimensions must be 1 or 2.")

    def snapshot(self) -> tuple[tuple[str, str], ...]:
        """A sorted, auditable snapshot of the config values."""

        pairs = [
            ("accounting_tolerance", str(self.accounting_tolerance)),
            (
                "evidence_min_sample_total",
                str(self.evidence_min_sample_total),
            ),
            (
                "evidence_strong_min_sample",
                str(self.evidence_strong_min_sample),
            ),
            ("lookup_max_dimensions", str(self.lookup_max_dimensions)),
        ]
        return tuple(sorted(pairs))


__all__ = ["RobustnessValidationConfig"]
