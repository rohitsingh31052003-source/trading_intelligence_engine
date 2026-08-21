"""
Historical setup research report formatter (Product Phase 6D).

Stateless, deterministic, returns ``str`` (no ``print()``). Renders the
research request, corpus interaction, evaluation-point accounting,
detected occurrences, realized outcomes and the aggregated descriptive
evidence with DESCRIPTIVE language only.

Following the 11O-12E / Product-Phase reporting convention this
formatter is imported via full path (NOT re-exported from
``reporting/__init__.py``).
"""

from __future__ import annotations

from engine.models.setup_research import (
    SetupEvidence,
    SetupResearchObservation,
    SetupResearchResult,
)


_DISCLAIMER = (
    "Historical evidence is descriptive and observational. It is not a "
    "prediction, recommendation, or guarantee of future performance. "
    "The existing decision engine remains authoritative; this research "
    "does not modify it and does not constitute a BUY/SELL/ENTER/EXIT/"
    "HOLD recommendation."
)


def _fmt(value: float | None, precision: int) -> str:
    return f"{value:.{precision}f}" if value is not None else "unavailable"


class SetupResearchFormatter:
    """Format historical setup research results for audit."""

    def __init__(self, precision: int = 2, width: int = 72) -> None:
        if precision < 0:
            raise ValueError("precision must be non-negative.")
        if width < 20:
            raise ValueError("width must be >= 20.")
        self.precision = precision
        self.width = width

    # ------------------------------------------------------------
    # PUBLIC API
    # ------------------------------------------------------------

    def format(self, result: SetupResearchResult) -> str:
        request = result.request
        w = self.width
        lines: list[str] = []
        lines.append("=" * w)
        lines.append("PHASE 6D — HISTORICAL SETUP RESEARCH")
        lines.append("=" * w)
        lines.append(f"Research ID: {result.research_id}")
        if result.label:
            lines.append(f"Label: {result.label}")
        if result.metadata:
            for key, value in result.metadata:
                lines.append(f"Metadata: {key} = {value}")
        lines.append(f"Status: {result.status.name}")
        lines.append("")

        lines.append("-- Request ---------------------------------------------------")
        lines.append(f"Instrument: {request.instrument}")
        lines.append(f"Setup timeframe: {request.setup_timeframe}")
        lines.append(
            "Context timeframe: " + (request.context_timeframe or "(disabled)")
        )
        lines.append(
            "Window: "
            + (request.start_time.isoformat() if request.start_time else "(open)")
            + " -> "
            + (request.end_time.isoformat() if request.end_time else "(open)")
        )
        lines.append(f"Forward horizon: {request.forward_horizon} candle(s)")
        lines.append(f"Minimum history: {request.minimum_history} candle(s)")
        if request.has_filters:
            lines.append(
                "Filters: "
                + ", ".join(
                    f"{name}={value}"
                    for name, value in (
                        ("trend", request.trend_filter),
                        ("range", request.range_filter),
                        ("mtf", request.mtf_alignment_filter),
                        ("setup_type", request.setup_type_filter),
                        ("direction", request.direction_filter),
                    )
                    if value
                )
            )
        else:
            lines.append("Filters: (none)")
        lines.append("")

        lines.append("-- Evaluation Points -----------------------------------------")
        lines.append(f"Points examined: {result.points_examined}")
        lines.append(f"Valid points researched: {result.valid_points}")
        if result.skip_counts:
            for name, count in result.skip_counts:
                lines.append(f"Skipped ({name}): {count}")
        else:
            lines.append("Skipped: 0")
        lines.append("")

        lines.append("-- Occurrences & Outcomes ------------------------------------")
        lines.append(f"Historical occurrences: {result.occurrence_count}")
        if result.occurrences_detected != result.occurrence_count:
            lines.append(
                f"Occurrences detected before filters: "
                f"{result.occurrences_detected}"
            )
        lines.append(f"Completed outcomes: {result.completed_outcomes}")
        lines.append(f"Ambiguous outcomes: {result.ambiguous_count}")
        lines.append(f"Unresolved outcomes: {result.unresolved_count}")
        lines.append("")

        if result.evidence is not None:
            lines.extend(self._format_evidence(result.evidence))
            lines.append("")

        if result.grouped_evidence:
            lines.append("-- Evidence By Group -----------------------------------------")
            for evidence in result.grouped_evidence:
                lines.append(
                    f"{evidence.key}: n={evidence.sample_size} "
                    f"win={evidence.win_count} loss={evidence.loss_count} "
                    f"expired={evidence.expired_count} "
                    f"ambiguous={evidence.ambiguous_count} "
                    f"avgR={_fmt(evidence.average_realized_r, self.precision)} "
                    f"medR={_fmt(evidence.median_realized_r, self.precision)} "
                    f"avgMFE={_fmt(evidence.average_mfe, self.precision)} "
                    f"avgMAE={_fmt(evidence.average_mae, self.precision)} "
                    f"strength={evidence.strength.name}"
                )
            lines.append("")

        if result.observations:
            lines.append("-- Observations ----------------------------------------------")
            for observation in result.observations:
                lines.append(self._format_observation_line(observation))
            lines.append("")

        lines.append("-- Rationale ---------------------------------------------------")
        lines.append(result.rationale)
        lines.append("")
        if result.limitations:
            lines.append("-- Limitations -------------------------------------------------")
            for limitation in result.limitations:
                lines.append(f"- {limitation}")
            lines.append("")
        lines.append(f"WARNING: {_DISCLAIMER}")
        lines.append("=" * w)
        return "\n".join(lines)

    # ------------------------------------------------------------
    # INTERNAL
    # ------------------------------------------------------------

    def _format_evidence(self, evidence: SetupEvidence) -> list[str]:
        p = self.precision
        lines = ["-- Evidence --------------------------------------------------"]
        lines.append(f"Evidence status: {evidence.strength.name}")
        lines.append(f"Sample size: {evidence.sample_size}")
        lines.append(f"Occurrence count: {evidence.occurrence_count}")
        lines.append(f"Completed outcomes: {evidence.completed_outcomes}")
        lines.append(f"Ambiguous outcomes: {evidence.ambiguous_count}")
        lines.append(f"Unresolved outcomes: {evidence.unresolved_count}")
        lines.append(f"Geometry unavailable: {evidence.no_geometry_count}")
        lines.append(f"Wins (target hit): {evidence.win_count}")
        lines.append(f"Losses (stop hit): {evidence.loss_count}")
        lines.append(f"Expired (horizon): {evidence.expired_count}")
        lines.append(f"Win rate: {_fmt(evidence.win_rate, p)}")
        lines.append(
            f"Average realized R: {_fmt(evidence.average_realized_r, p)}"
        )
        lines.append(f"Median realized R: {_fmt(evidence.median_realized_r, p)}")
        lines.append(f"Average MFE: {_fmt(evidence.average_mfe, p)}")
        lines.append(f"Average MAE: {_fmt(evidence.average_mae, p)}")
        lines.append(f"Reason: {evidence.reason}")
        return lines

    def _format_observation_line(
        self, observation: SetupResearchObservation,
    ) -> str:
        occurrence = observation.occurrence
        outcome = observation.outcome
        p = self.precision
        geometry = (
            f"entry={_fmt(occurrence.entry, p)} "
            f"stop={_fmt(occurrence.stop, p)} "
            f"target={_fmt(occurrence.target, p)}"
            if occurrence.geometry_available
            else "geometry=unavailable"
        )
        return (
            f"{occurrence.evaluation_time.isoformat()} "
            f"{occurrence.instrument} "
            f"{occurrence.setup_classification}/{occurrence.direction} "
            f"decision={occurrence.decision_classification} "
            f"setup={occurrence.setup_type} "
            f"trend={occurrence.trend_state} "
            f"mtf={occurrence.mtf_alignment} "
            f"{geometry} -> {outcome.outcome_status.name} "
            f"exit={_fmt(outcome.exit_price, p)} "
            f"R={_fmt(outcome.realized_r, p)} "
            f"MFE={_fmt(outcome.mfe, p)} "
            f"MAE={_fmt(outcome.mae, p)} "
            f"bars={outcome.bars_held}"
        )


__all__ = ["SetupResearchFormatter"]
