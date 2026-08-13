"""
Setup assessment report formatter (Sprint 11Q).

``SetupAssessmentFormatter`` renders a descriptive ``SetupAssessment``
(or a sequence of them) as a plain-text report. It is stateless and
deterministic: identical inputs always produce identical text.

The report is DESCRIPTIVE. It never claims directional prediction,
profitability, or a trading recommendation. Every report ends with an
explicit warning that setup classifications are descriptive technical
evidence, not predictions or guarantees of profitability.
"""

from __future__ import annotations

from typing import Iterable

from engine.models.setup_confluence import (
    EvidenceAlignment,
    SetupAssessment,
    SetupClassification,
)

_WARNING = (
    "WARNING: Setup classifications are descriptive technical "
    "evidence, not predictions or guarantees of profitability."
)


class SetupAssessmentFormatter:
    """
    Render a descriptive setup assessment as plain text.
    """

    def format(self, assessment: SetupAssessment) -> str:
        """Render a single ``SetupAssessment``."""

        return self._render_point(assessment, include_warning=True)

    def format_sequence(
        self,
        assessments: Iterable[SetupAssessment],
    ) -> str:
        """Render a sequence of ``SetupAssessment`` snapshots.

        The descriptive warning is emitted exactly once at the end of
        the sequence rather than per-point.
        """

        lines: list[str] = []
        for a in assessments:
            lines.append(self._render_point(a, include_warning=False))
            lines.append("")
        lines.append(_WARNING)
        return "\n".join(lines).rstrip() + "\n"

    # ------------------------------------------------------------
    # INTERNALS
    # ------------------------------------------------------------

    def _render_point(
        self,
        a: SetupAssessment,
        include_warning: bool = True,
    ) -> str:
        ev = a.evidence

        supporting = (
            ", ".join(i.source.name.lower() for i in ev.supporting)
            or "none"
        )
        conflicting = (
            ", ".join(i.source.name.lower() for i in ev.conflicting)
            or "none"
        )

        lines = [
            "Setup Assessment",
            "----------------",
            f"index          : {a.index}",
            f"timestamp      : {a.timestamp}",
            f"Direction      : {a.direction.name}",
            f"Classification : {a.classification.name}",
            f"Confluence     : {a.confluence_score}",
            f"Supporting      : {supporting}",
            f"Conflicting     : {conflicting}",
            f"Candle Evidence : {a.candle_evidence}",
            f"Trend           : {a.trend_evidence}",
            f"Structure       : {a.structure_evidence}",
            f"Location        : {a.location_evidence}",
            f"Regime          : {a.regime_evidence}",
        ]

        lines.append("")
        lines.append("Evidence detail:")
        for item in ev.all:
            lines.append(
                f"  - {item.source.name:9s} "
                f"dir={item.direction.name:8s} "
                f"align={item.alignment.name:11s} "
                f"label={item.label}"
            )

        lines.append("")
        lines.append(f"Reason          : {a.reason}")
        if include_warning:
            lines.append(_WARNING)
        return "\n".join(lines)


__all__ = ["SetupAssessmentFormatter"]
