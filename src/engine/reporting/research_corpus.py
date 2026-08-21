"""
Research corpus report formatter (Product Phase 6C).

Stateless, deterministic, returns ``str`` (no ``print()``). Renders the
corpus data-quality / coverage report and per-evaluation-point detail
with DESCRIPTIVE language only. The corpus is research preparation; it
is NOT a trading strategy, NOT a prediction and NOT a decision.

Following the 11O-12E / Product-Phase reporting convention this
formatter is imported via full path (NOT re-exported from
``reporting/__init__.py``).
"""

from __future__ import annotations

from engine.models.research_corpus import (
    CorpusEvaluationPoint,
    HistoricalResearchCorpus,
)


_DISCLAIMER = (
    "The historical research corpus is DESCRIPTIVE research "
    "preparation only. It is not a trading strategy, not a prediction, "
    "not a probability claim and not a decision or recommendation. "
    "Historical data quality is reported honestly; no missing or "
    "unavailable data is fabricated."
)


class ResearchCorpusFormatter:
    """Format corpus build reports + evaluation points for audit."""

    def format(self, corpus: HistoricalResearchCorpus) -> str:
        report = corpus.report
        lines: list[str] = []
        lines.append("=" * 72)
        lines.append("HISTORICAL RESEARCH CORPUS REPORT")
        lines.append("=" * 72)
        lines.append(f"Corpus ID: {corpus.corpus_id}")
        if corpus.label:
            lines.append(f"Label: {corpus.label}")
        if corpus.metadata:
            for key, value in corpus.metadata:
                lines.append(f"Metadata: {key} = {value}")
        lines.append("")
        lines.append("-- Configuration ---------------------------------------------")
        lines.append(f"Setup timeframe: {corpus.setup_timeframe}")
        lines.append(
            "Context timeframe: "
            + (corpus.context_timeframe or "(disabled)")
        )
        lines.append("")
        lines.append("-- Coverage --------------------------------------------------")
        lines.append(
            f"Requested instruments: {', '.join(report.requested_instruments) or '(none)'}"
        )
        lines.append(
            f"Loaded instruments: {', '.join(report.loaded_instruments) or '(none)'}"
        )
        lines.append(
            f"Missing instruments: {', '.join(report.missing_instruments) or '(none)'}"
        )
        lines.append(
            f"Requested timeframes: {', '.join(report.requested_timeframes) or '(none)'}"
        )
        lines.append(
            f"Available timeframes: {', '.join(report.available_timeframes) or '(none)'}"
        )
        for name, quality in report.per_instrument_quality:
            lines.append(
                f"  {name}: candles={quality.window_count} "
                f"(source={quality.source_count}) "
                f"first={self._ts(quality.first_timestamp)} "
                f"last={self._ts(quality.last_timestamp)} "
                f"unexpected_gaps={quality.unexpected_gap_count} "
                f"closure_gaps={quality.closure_gap_count} "
                f"invalid_records={quality.invalid_records}"
            )
        lines.append("")
        lines.append("-- Evaluation Points -----------------------------------------")
        lines.append(f"Evaluation points: {report.evaluation_count}")
        lines.append(f"  VALID: {report.valid_count}")
        lines.append(f"  INSUFFICIENT_HISTORY: {report.insufficient_history_count}")
        lines.append(f"  MISSING_DATA: {report.missing_data_count}")
        lines.append(f"  DATA_GAP: {report.data_gap_count}")
        lines.append(f"  INVALID: {report.invalid_count}")
        lines.append(
            f"Rejected future records (beyond window end): "
            f"{report.rejected_future_records}"
        )
        lines.append("")
        lines.append("-- Source / Storage ------------------------------------------")
        lines.append(f"Provider/source: {report.provider or 'unavailable'}")
        lines.append(f"Storage status: {report.storage_status}")
        lines.append(f"Ingestion version: {report.ingestion_version}")
        lines.append("")
        lines.append("-- Issues ----------------------------------------------------")
        if report.issues:
            for issue in report.issues:
                lines.append(
                    f"  [{issue.error or 'ISSUE'}] {issue.instrument}/"
                    f"{issue.timeframe}: {issue.reason}"
                )
        else:
            lines.append("  (none)")
        lines.append("")
        lines.append("-- Rationale -------------------------------------------------")
        lines.append(
            "The corpus reconstructs the market state at each historical "
            "evaluation time using ONLY information available at that "
            "time (setup candles with timestamp <= T; completed context "
            "candles strictly before T). Skipped points carry explicit "
            "statuses and are never fabricated."
        )
        lines.append("")
        lines.append(f"DISCLAIMER: {_DISCLAIMER}")
        return "\n".join(lines)

    def format_point(self, point: CorpusEvaluationPoint) -> str:
        """Format ONE evaluation point (boundary + state) for audit."""

        lines: list[str] = []
        lines.append("=" * 72)
        lines.append("HISTORICAL EVALUATION POINT")
        lines.append("=" * 72)
        lines.append(f"Instrument: {point.instrument}")
        lines.append(f"Evaluation time T: {point.evaluation_time.isoformat()}")
        lines.append(f"Setup timeframe: {point.setup_timeframe}")
        lines.append(
            "Context timeframe: " + (point.context_timeframe or "(disabled)")
        )
        lines.append(f"Status: {point.status.name}")
        if point.reason:
            lines.append(f"Reason: {point.reason}")
        lines.append(f"Usable setup history: {point.history_count}")
        state = point.state
        if state is not None:
            lines.append("")
            lines.append("-- Point-in-time boundary ------------------------------------")
            lines.append(
                "Latest usable setup candle (<= T): "
                + self._ts(state.latest_usable_setup_timestamp)
            )
            lines.append(
                "Latest completed context candle (< T): "
                + self._ts(state.latest_usable_context_timestamp)
            )
            lines.append("Future candles (> T): excluded (never inspected)")
            lines.append("")
            lines.append("-- Structure / context (reused engine outputs) ---------------")
            setup_context = state.setup_context
            if setup_context is not None:
                lines.append(
                    f"Setup trend: {setup_context.trend.state.name} "
                    f"(range={setup_context.range.state.name}, "
                    f"confirmed_swings={setup_context.confirmed_swings})"
                )
                lines.append(
                    "Setup recent structure: "
                    + ", ".join(
                        s.structure.name for s in setup_context.recent_structure
                    )
                    if setup_context.recent_structure
                    else "Setup recent structure: (none)"
                )
                sr = setup_context.support_resistance
                lines.append(
                    "Setup support/resistance: "
                    + self._price(sr.support)
                    + " / "
                    + self._price(sr.resistance)
                    + f" (location={sr.location.name})"
                )
            context_context = state.context_context
            if context_context is not None:
                lines.append(
                    f"Context trend: {context_context.trend.state.name} "
                    f"(range={context_context.range.state.name})"
                )
            lines.append(f"MTF alignment: {state.mtf_alignment.name}")
            if state.structure_unavailable_reasons:
                lines.append("Unavailable structure information:")
                for reason in state.structure_unavailable_reasons:
                    lines.append(f"  - {reason}")
        lines.append("")
        lines.append(f"DISCLAIMER: {_DISCLAIMER}")
        return "\n".join(lines)

    @staticmethod
    def _ts(value) -> str:
        return value.isoformat() if value is not None else "unavailable"

    @staticmethod
    def _price(value) -> str:
        return f"{value:.2f}" if value is not None else "unavailable"


__all__ = ["ResearchCorpusFormatter"]
