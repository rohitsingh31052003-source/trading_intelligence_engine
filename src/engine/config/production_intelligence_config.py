"""
Configuration for the PRODUCTION INTEGRATION + FINAL HARDENING layer
(Sprint 12E).

:class:`ProductionIntelligenceConfig` is intentionally MINIMAL. It
carries NO strategy / optimization / scoring parameters. The production
integration status semantics are a FIXED mirror of the reused Sprint 12B
:class:`~engine.models.decision_intelligence_integration.IntegrationStatus`
and are intentionally NOT configurable, so the established authority
contract cannot be weakened. The
:data:`~engine.models.strategy_intelligence.EVIDENCE_TO_STRATEGY`,
:data:`~engine.models.decision_intelligence.ASSESSMENT_TO_CONTEXT` and
Sprint 12B integration-status mappings are all left untouched.

Sprint 12E is the FINAL planned sprint. This config exists only so the
production bundle carries an explicit, deterministic label / metadata
identity (matching every prior sprint's configuration discipline) and so
the production artifact is auditable.

Design rules:

* Frozen + slots (matches the rest of the config layer).
* No magic numbers; the production layer has no thresholds.
* Validated construction; invalid configuration raises explicitly.
* ``snapshot()`` returns a sorted, auditable (name, str(value)) sequence.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ProductionIntelligenceConfig:
    """
    Configuration for the Sprint 12E production integration engine.

    Attributes:

    label
        Optional descriptive label identifying the production run. Used
        as the default label when the caller does not supply one.

    metadata
        Optional descriptive metadata (sorted key/value pairs). Used as
        the default metadata when the caller does not supply any.
    """

    label: str = ""
    metadata: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.label, str):
            raise TypeError(
                f"ProductionIntelligenceConfig.label must be str, got "
                f"{type(self.label).__name__}.",
            )
        if not isinstance(self.metadata, tuple):
            raise TypeError(
                "ProductionIntelligenceConfig.metadata must be a tuple of "
                "(str, str) pairs.",
            )
        for pair in self.metadata:
            if (
                not isinstance(pair, tuple)
                or len(pair) != 2
                or not isinstance(pair[0], str)
                or not isinstance(pair[1], str)
            ):
                raise ValueError(
                    "ProductionIntelligenceConfig.metadata must be a tuple "
                    "of (str, str) pairs.",
                )

    def snapshot(self) -> tuple[tuple[str, str], ...]:
        """Return a sorted, auditable (name, str(value)) sequence."""

        items: list[tuple[str, str]] = [
            ("label", self.label),
        ]
        for key, value in self.metadata:
            items.append((f"metadata.{key}", value))
        return tuple(sorted(items))


__all__ = ["ProductionIntelligenceConfig"]
