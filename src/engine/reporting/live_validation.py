"""
Reporting for live paper validation (Product Phase 6F).

Renders the historical-vs-live comparison for one validation cycle or
one observation. DESCRIPTIVE ONLY: the report NEVER claims historical
evidence predicts the future, NEVER computes a predictive probability,
NEVER creates a new trading score and NEVER issues a
BUY/SELL/ENTER/EXIT/HOLD recommendation. The three concerns (current
decision / historical evidence context / actual paper-trade outcome)
are presented as strictly separate columns — never merged.

Following the 11O-12E / Product-Phase reporting convention this module
is imported via its full path (``engine.reporting.live_validation``);
``reporting/__init__.py`` is NOT extended.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from engine.models.live_validation import (
    LIVE_VALIDATION_LIMITATIONS,
    LiveValidationCycleResult,
    LiveValidationObservation,
    LiveValidationStatus,
)


_UNAVAILABLE = "unavailable"


def _fmt_ts(value: datetime | None) -> str:
    return value.isoformat() if value is not None else _UNAVAILABLE


def _fmt_float(value: float | None, precision: int) -> str:
    return f"{value:.{precision}f}" if value is not None else _UNAVAILABLE


def _fmt_decimal(value: Decimal | None) -> str:
    return str(value) if value is not None else _UNAVAILABLE


def _fmt_text(value: str) -> str:
    return value if value else _UNAVAILABLE


class LiveValidationFormatter:
    """
    Stateless, deterministic formatter for live validation artifacts.

    ``format_cycle`` renders the historical-vs-live comparison table +
    per-observation detail for a full cycle; ``format_observation``
    renders one observation. Both return ``str`` (no ``print()``).
    """

    def __init__(self, precision: int = 2, width: int = 72) -> None:
        if precision < 0:
            raise ValueError("precision must be non-negative.")
        if width <= 0:
            raise ValueError("width must be positive.")
        self.precision = precision
        self.width = width

    # ------------------------------------------------------------
    # SINGLE OBSERVATION
    # ------------------------------------------------------------

    def format_observation(self, obs: LiveValidationObservation) -> str:
        """Render one validation observation (descriptive only)."""

        w = self.width
        lines: list[str] = []
        lines.append("=" * w)
        lines.append("LIVE VALIDATION OBSERVATION".center(w))
        lines.append("=" * w)
        lines.append(f"Validation ID:        {obs.validation_id}")
        lines.append(f"Instrument:           {obs.instrument}")
        lines.append(
            f"Timeframes:           {obs.setup_timeframe} (setup) / "
            f"{obs.context_timeframe or _UNAVAILABLE} (context)"
        )
        lines.append(f"Evaluation time:      {_fmt_ts(obs.evaluation_timestamp)}")
        lines.append(f"Provider:             {_fmt_text(obs.provider)}")
        lines.append(f"Provider status:      {_fmt_text(obs.provider_status)}")
        lines.append(f"Freshness:            {_fmt_text(obs.freshness_state)}")
        lines.append("-" * w)
        lines.append("CURRENT STATE (authoritative existing decision — unchanged)")
        lines.append(f"  Decision:           {_fmt_text(obs.decision_classification)}")
        lines.append(f"  Actionability:      {_fmt_text(obs.actionability)}")
        lines.append(f"  Direction:          {_fmt_text(obs.direction)}")
        lines.append(
            f"  Geometry available: {'yes' if obs.geometry_available else 'no'}"
        )
        lines.append("-" * w)
        lines.append("HISTORICAL EVIDENCE (descriptive context — not a prediction)")
        lines.append(f"  Evidence status:    {obs.historical_context_status}")
        lines.append(f"  Evidence strength:  {obs.historical_evidence_strength}")
        lines.append(f"  Sample size:        {obs.historical_sample_size}")
        lines.append(
            f"  Win rate:           {_fmt_float(obs.historical_win_rate, self.precision)}"
        )
        lines.append(
            f"  Average realized R: "
            f"{_fmt_float(obs.historical_average_realized_r, self.precision)}"
        )
        lines.append(
            f"  Profit factor:      "
            f"{_fmt_float(obs.historical_profit_factor, self.precision)}"
        )
        lines.append(
            f"  Research refs:      "
            f"{', '.join(obs.research_ids) if obs.research_ids else _UNAVAILABLE}"
        )
        lines.append("-" * w)
        lines.append("PAPER-TRADE OUTCOME (actual result — never fabricated)")
        lines.append(
            f"  Paper trade:        "
            f"{obs.paper_trade_id if obs.paper_trade_id else 'none'}"
        )
        lines.append(f"  Outcome status:     {_fmt_text(obs.outcome_status)}")
        lines.append(f"  Outcome time:       {_fmt_ts(obs.outcome_timestamp)}")
        lines.append(f"  Realized R:         {_fmt_decimal(obs.realized_r)}")
        lines.append("-" * w)
        lines.append(f"VALIDATION STATUS:    {obs.validation_status.value}")
        lines.append(f"Revision:             {obs.revision}")
        lines.append(f"Recorded at:          {_fmt_ts(obs.recorded_at)}")
        lines.append(f"Updated at:           {_fmt_ts(obs.updated_at)}")
        if obs.warnings:
            lines.append("-" * w)
            lines.append("WARNINGS")
            for warning in obs.warnings:
                lines.append(f"  - {warning}")
        lines.append("-" * w)
        for limitation in obs.limitations:
            lines.append(f"NOTE: {limitation}")
        return "\n".join(lines)

    # ------------------------------------------------------------
    # FULL CYCLE (historical-vs-live comparison)
    # ------------------------------------------------------------

    def format_cycle(self, result: LiveValidationCycleResult) -> str:
        """Render the historical-vs-live comparison for a full cycle."""

        w = self.width
        lines: list[str] = []
        lines.append("=" * w)
        lines.append("LIVE PAPER VALIDATION REPORT".center(w))
        lines.append("=" * w)
        lines.append(f"Cycle ID:             {result.cycle_id}")
        lines.append(f"Status:               {result.status.value}")
        lines.append(
            f"Operations cycle:     {result.operations_cycle_id or _UNAVAILABLE}"
        )
        lines.append(f"Reference time:       {_fmt_ts(result.reference_now)}")
        lines.append(f"Provider:             {_fmt_text(result.provider)}")
        lines.append(f"Instruments scanned:  {result.instruments_scanned}")
        lines.append(f"Observations recorded:{result.observations_recorded:>3}")
        lines.append(f"Observations advanced:{result.observations_updated:>3}")
        lines.append(f"Paper trades created: {result.paper_trades_created}")
        lines.append(f"Duplicates skipped:   {result.duplicates_skipped}")
        lines.append("-" * w)
        lines.append(
            "HISTORICAL-VS-LIVE COMPARISON (descriptive only — historical "
            "evidence does NOT predict future results)"
        )
        lines.append("-" * w)
        if not result.observations:
            lines.append("(no observations)")
        else:
            header = (
                f"{'Instrument':<12} {'Decision':<10} {'Actionability':<22} "
                f"{'Evidence':<20} {'Strength':<13} {'Sample':>6} "
                f"{'Trade':<7} {'Outcome':<18} {'Realized R':>11} "
                f"{'Validation':<20}"
            )
            lines.append(header)
            lines.append("-" * w)
            for obs in result.observations:
                lines.append(
                    f"{obs.instrument:<12} "
                    f"{(obs.decision_classification or '-'):<10} "
                    f"{(obs.actionability or '-'):<22} "
                    f"{obs.historical_context_status:<20} "
                    f"{obs.historical_evidence_strength:<13} "
                    f"{obs.historical_sample_size:>6} "
                    f"{'yes' if obs.paper_trade_id else 'no':<7} "
                    f"{(obs.outcome_status or '-'):<18} "
                    f"{_fmt_decimal(obs.realized_r):>11} "
                    f"{obs.validation_status.value:<20}"
                )
        if result.errors:
            lines.append("-" * w)
            lines.append("ERRORS (failure-isolated; never converted into success)")
            for error in result.errors:
                lines.append(f"  - {error}")
        if result.warnings:
            lines.append("-" * w)
            lines.append("WARNINGS")
            for warning in result.warnings:
                lines.append(f"  - {warning}")
        lines.append("-" * w)
        lines.append("RATIONALE")
        lines.append(f"  {result.rationale}")
        lines.append("-" * w)
        lines.append("LIMITATIONS")
        lines.append(f"  {result.limitations}")
        for limitation in LIVE_VALIDATION_LIMITATIONS:
            lines.append(f"  NOTE: {limitation}")
        return "\n".join(lines)


__all__ = ["LiveValidationFormatter"]
