"""
Configuration for the controlled decision intelligence INTEGRATION
boundary (Sprint 12B).

The config is intentionally MINIMAL. The integration boundary has NO
strategy / optimization / scoring parameters: it does NOT adjust the
existing decision score, does NOT re-rank, does NOT re-classify, does
NOT introduce weights, probabilities, predictive models, or BUY / SELL
logic. The :class:`~engine.models.decision_intelligence_integration.IntegrationStatus`
assignment is a FIXED, deterministic function of (a) whether an existing
decision is present, (b) whether a decision intelligence context is
present, and (c) the reused Sprint 12A
:class:`~engine.models.decision_intelligence.DecisionContextStatus` /
lookup match status. It is NOT configurable and is NOT overridden by
anything in this config.

``strict``
    When ``True`` (the default), the engine raises a
    :class:`ValueError` if a non-``None`` ``existing_decision`` and a
    non-``None`` ``decision_intelligence`` carry INCONSISTENT
    opportunity profiles (the 12A context profile disagrees with the
    integration profile on a non-empty dimension). This is an
    AUDIT GUARD: it prevents silently integrating a decision-intelligence
    context computed for a DIFFERENT opportunity than the existing
    decision. When ``False``, the inconsistency is recorded in the
    rationale / limitations instead of raising (useful for exploratory
    analysis). The guard NEVER modifies the existing decision; it only
    decides whether to reject the integration loudly or softly.

NO STATISTICAL CLAIMS:

The integration layer does NOT perform statistical hypothesis tests and
does NOT use terms such as "statistically significant". It is an
INFORMATION / CONTEXT / AUDIT layer presented to the existing decision
process without altering it.

The integration layer is DESCRIPTIVE. It is NOT a trading signal, NOT a
prediction, NOT a probability of success, NOT a profitability guarantee,
NOT a buy / sell recommendation, and NOT a statistical-significance
claim.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DecisionIntelligenceIntegrationConfig:
    """
    Minimal configuration for the decision intelligence integration
    engine.

    Attributes:

    strict
        When ``True`` (default), raise on an inconsistent opportunity
        profile between the existing decision and the decision
        intelligence context. When ``False``, record the inconsistency
        in the rationale / limitations instead. The guard never modifies
        the existing decision.

    label
        Optional descriptive label identifying the integration run.

    metadata
        Optional descriptive metadata (sorted key/value pairs) carried
        for traceability.
    """

    strict: bool = True
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = ()

    def snapshot(self) -> tuple[tuple[str, str], ...]:
        """A sorted, auditable snapshot of the config values."""

        pairs = [
            ("strict", str(self.strict)),
        ]
        return tuple(sorted(pairs))


__all__ = ["DecisionIntelligenceIntegrationConfig"]
