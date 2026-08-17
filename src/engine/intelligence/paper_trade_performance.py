"""
Paper-trade performance analytics engine (Product Phase 5).

This is a DOWNSTREAM DESCRIPTIVE analytics layer. It consumes
ALREADY-COMPUTED Product Phase 5 :class:`~engine.models.paper_trade.PaperTrade`
records and aggregates them into descriptive performance statistics. It
NEVER re-evaluates trades, NEVER re-runs the pipeline, NEVER re-reads
candles, NEVER uses future information, NEVER introduces ML / predictive
models / parameter optimization / live trading.

It is NOT a new intelligence / scoring layer. It performs NO market
analysis, NO decision logic, NO prediction, NO probability. The existing
Sprint 11S decision classification on each paper trade is AUTHORITATIVE
and is never renamed / upgraded / downgraded by aggregation. A
paper-trade RESULT is a SEPARATE concern from the system DECISION:
aggregating trades never rewrites the original decisions.

ACCOUNTING DISCIPLINE (mirrors Sprint 11X exactly so the two layers are
semantically consistent):

* Win / loss rates use the resolved target-vs-stop denominator
  ``TARGET_HIT + STOP_HIT``. ``BOTH_TOUCHED`` (ambiguous),
  ``MANUAL_CLOSE``, ``EXPIRED``, ``CANCELLED``, ``NO_GEOMETRY`` and any
  unresolved state are EXCLUDED. ``None`` when the denominator is zero.
* R-multiple aggregates (total / average / median / gross positive /
  gross negative / profit factor) are computed ONLY over CLOSED trades
  with a valid ``realized_r`` (TARGET_HIT / STOP_HIT / EXPIRED /
  MANUAL_CLOSE). ``BOTH_TOUCHED`` (``realized_r`` is ``None``) and
  ``NO_GEOMETRY`` are excluded.
* Profit factor is ``gross_positive_R / abs(gross_negative_R)``; ``None``
  when ``gross_negative_r`` is zero (never fabricated).
* P&L aggregates use ``Decimal`` (never float) over CLOSED trades with a
  valid ``realized_pnl``.

DETERMINISM (no wall-clock, no randomness, no unordered iteration):

Identical paper-trade collections always produce identical analytics.
The ``analytics_id`` hashes the SORTED paper-trade identities so a
shuffled input yields the same id. Group ordering is deterministic
(INSTRUMENT lexicographic; DIRECTION LONG<SHORT; DECISION
PREFERRED<QUALIFIED<WATCH<REJECTED; SETUP_TYPE canonical; TIMEFRAME
lexicographic; the ``""`` unavailable sentinel sorts LAST in every
dimension).

NO-LOOK-AHEAD (HARD REQUIREMENT): the analytics layer consumes
already-computed paper trades only and adds NO future information. It
takes NO candle / future-market-data argument. It NEVER calls the
Sprint 11W ``OutcomeEvaluator.evaluate`` and NEVER runs the
``HistoricalEvaluationPipeline.evaluate`` (regression-tested by patching
both to raise).

This is analytics + reporting, NOT a new orchestration package.
``intelligence/__init__.py`` stays intentionally empty; import via
``from engine.intelligence.paper_trade_performance import
PaperTradePerformanceEngine``.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Iterable, Sequence

from engine.models.paper_trade import (
    PaperExitReason,
    PaperTrade,
    PaperTradeStatus,
)
from engine.models.paper_trade_performance import (
    PaperTradeGroupDimension,
    PaperTradePerformanceAnalytics,
    PaperTradePerformanceBreakdown,
    PaperTradePerformanceGroup,
    PaperTradePerformanceStatistics,
)


# ============================================================
# ENGINE
# ============================================================


class PaperTradePerformanceEngine:
    """
    Aggregate already-computed paper trades into descriptive statistics.

    The engine is PURE, DETERMINISTIC and STATELESS across calls. The
    input paper trades are NEVER mutated. It performs NO market analysis,
    NO decision logic, NO prediction. The existing decision on each trade
    is AUTHORITATIVE and is never modified by aggregation.
    """

    def analyze(
        self,
        trades: Iterable[PaperTrade],
        *,
        label: str = "",
        metadata: Sequence[tuple[str, str]] | None = None,
    ) -> PaperTradePerformanceAnalytics:
        """Aggregate a collection of paper trades."""

        trades_t = tuple(trades)
        overall = _compute_statistics(trades_t)

        breakdowns: list[PaperTradePerformanceBreakdown] = []
        for dim in (
            PaperTradeGroupDimension.INSTRUMENT,
            PaperTradeGroupDimension.DIRECTION,
            PaperTradeGroupDimension.DECISION,
            PaperTradeGroupDimension.SETUP_TYPE,
            PaperTradeGroupDimension.TIMEFRAME,
        ):
            breakdowns.append(_compute_breakdown(trades_t, dim))

        meta = tuple(metadata) if metadata else ()
        analytics_id = _analytics_id(trades_t, label, meta)
        rationale = _rationale(overall, len(trades_t))

        return PaperTradePerformanceAnalytics(
            analytics_id=analytics_id,
            overall=overall,
            breakdowns=tuple(breakdowns),
            trade_count=len(trades_t),
            label=label,
            metadata=meta,
            rationale=rationale,
        )


# ============================================================
# STATISTICS COMPUTATION
# ============================================================


def _compute_statistics(trades: Sequence[PaperTrade]) -> PaperTradePerformanceStatistics:
    total = len(trades)
    waiting = sum(1 for t in trades if t.status is PaperTradeStatus.WAITING_FOR_ENTRY)
    open_ = sum(1 for t in trades if t.status is PaperTradeStatus.OPEN)
    closed = sum(1 for t in trades if t.status is PaperTradeStatus.CLOSED)
    cancelled = sum(1 for t in trades if t.status is PaperTradeStatus.CANCELLED)
    invalidated = sum(1 for t in trades if t.status is PaperTradeStatus.INVALIDATED)

    wins = sum(
        1 for t in trades if t.exit_reason is PaperExitReason.TARGET_HIT
    )
    losses = sum(
        1 for t in trades if t.exit_reason is PaperExitReason.STOP_HIT
    )
    ambiguous = sum(
        1 for t in trades if t.exit_reason is PaperExitReason.BOTH_TOUCHED
    )
    expired = sum(
        1 for t in trades if t.exit_reason is PaperExitReason.EXPIRED
    )
    manual_close = sum(
        1 for t in trades if t.exit_reason is PaperExitReason.MANUAL_CLOSE
    )

    # Win/loss rates: TARGET_HIT + STOP_HIT denominator.
    denom = wins + losses
    win_rate = (wins / denom) if denom > 0 else None
    loss_rate = (losses / denom) if denom > 0 else None

    # R aggregates: only CLOSED trades with a valid realized_r.
    rs = [float(t.realized_r) for t in trades if t.realized_r is not None]
    valid_r_count = len(rs)
    total_r = sum(rs) if rs else None
    average_r = (sum(rs) / len(rs)) if rs else None
    median_r = _median(rs) if rs else None
    gross_pos_r = sum(r for r in rs if r > 0) if rs else None
    gross_neg_r = sum(abs(r) for r in rs if r < 0) if rs else None
    if gross_pos_r is None and gross_neg_r is None:
        profit_factor = None
    elif gross_neg_r is None or gross_neg_r == 0:
        profit_factor = None
    else:
        gross_pos = gross_pos_r if gross_pos_r is not None else 0.0
        profit_factor = gross_pos / gross_neg_r

    # P&L aggregates (Decimal): CLOSED trades with a valid realized_pnl.
    pnls = [t.realized_pnl for t in trades if t.realized_pnl is not None]
    valid_pnl_count = len(pnls)
    total_pnl = sum(pnls) if pnls else None
    average_pnl = (sum(pnls) / Decimal(len(pnls))) if pnls else None

    return PaperTradePerformanceStatistics(
        total=total,
        waiting=waiting,
        open=open_,
        closed=closed,
        cancelled=cancelled,
        invalidated=invalidated,
        wins=wins,
        losses=losses,
        ambiguous=ambiguous,
        expired=expired,
        manual_close=manual_close,
        win_rate=win_rate,
        loss_rate=loss_rate,
        total_realized_r=total_r,
        average_realized_r=average_r,
        median_realized_r=median_r,
        gross_positive_r=gross_pos_r,
        gross_negative_r=gross_neg_r,
        profit_factor=profit_factor,
        valid_r_count=valid_r_count,
        total_realized_pnl=total_pnl,
        average_realized_pnl=average_pnl,
        valid_pnl_count=valid_pnl_count,
    )


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return float(s[mid])
    return (float(s[mid - 1]) + float(s[mid])) / 2.0


# ============================================================
# BREAKDOWNS
# ============================================================


_DIRECTION_ORDER = ("LONG", "SHORT")
_DECISION_ORDER = ("PREFERRED", "QUALIFIED", "WATCH", "REJECTED")
# SETUP_TYPE canonical order mirrors the 11X setup-type ordering.
_SETUP_TYPE_ORDER = (
    "TREND_CONTINUATION",
    "BREAKOUT",
    "STRUCTURE_CONTINUATION",
    "RANGE_REJECTION",
    "SETUP_CANDIDATE",
)


def _dimension_key(trade: PaperTrade, dim: PaperTradeGroupDimension) -> str:
    if dim is PaperTradeGroupDimension.INSTRUMENT:
        return trade.instrument or ""
    if dim is PaperTradeGroupDimension.DIRECTION:
        return trade.direction or ""
    if dim is PaperTradeGroupDimension.DECISION:
        return trade.existing_decision or ""
    if dim is PaperTradeGroupDimension.SETUP_TYPE:
        return trade.setup_type or ""
    if dim is PaperTradeGroupDimension.TIMEFRAME:
        return trade.timeframe or ""
    return ""


def _ordered_keys(keys: set[str], dim: PaperTradeGroupDimension) -> list[str]:
    canonical: tuple[str, ...]
    if dim is PaperTradeGroupDimension.DIRECTION:
        canonical = _DIRECTION_ORDER
    elif dim is PaperTradeGroupDimension.DECISION:
        canonical = _DECISION_ORDER
    elif dim is PaperTradeGroupDimension.SETUP_TYPE:
        canonical = _SETUP_TYPE_ORDER
    else:
        # INSTRUMENT / TIMEFRAME: lexicographic, unavailable sentinel last.
        canonical = ()
    ordered = [k for k in canonical if k in keys]
    rest = sorted(k for k in keys if k not in canonical and k != "")
    ordered.extend(rest)
    if "" in keys:
        ordered.append("")
    return ordered


def _compute_breakdown(
    trades: Sequence[PaperTrade], dim: PaperTradeGroupDimension,
) -> PaperTradePerformanceBreakdown:
    buckets: dict[str, list[PaperTrade]] = {}
    for t in trades:
        k = _dimension_key(t, dim)
        buckets.setdefault(k, []).append(t)
    ordered_keys = _ordered_keys(set(buckets.keys()), dim)
    groups = tuple(
        PaperTradePerformanceGroup(
            dimension=dim,
            key=k,
            statistics=_compute_statistics(buckets[k]),
        )
        for k in ordered_keys
    )
    return PaperTradePerformanceBreakdown(dimension=dim, groups=groups)


# ============================================================
# ID + RATIONALE
# ============================================================


def _analytics_id(
    trades: Sequence[PaperTrade],
    label: str,
    metadata: tuple[tuple[str, str], ...],
) -> str:
    ids = sorted(t.paper_trade_id for t in trades)
    canonical = json.dumps(
        {
            "trade_ids": ids,
            "label": label,
            "metadata": [[k, v] for k, v in metadata],
        },
        sort_keys=True,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"ptperf-{digest[:16]}"


def _rationale(stats: PaperTradePerformanceStatistics, total: int) -> str:
    if total == 0:
        return (
            "No paper trades aggregated. Performance analytics are "
            "descriptive and require at least one paper-trade record."
        )
    parts = [
        f"Aggregated {total} paper trade(s): "
        f"{stats.closed} closed, {stats.open} open, {stats.waiting} waiting, "
        f"{stats.cancelled} cancelled, {stats.invalidated} invalidated.",
    ]
    if stats.win_rate is not None:
        parts.append(
            f"Win rate {stats.win_rate:.1%} over "
            f"{stats.wins + stats.losses} determinate target/stop trades."
        )
    else:
        parts.append("No determinate target/stop trades to compute a win rate.")
    if stats.valid_r_count > 0:
        parts.append(
            f"Average realized R {stats.average_realized_r:.4f} over "
            f"{stats.valid_r_count} valid-R trade(s)."
        )
    parts.append(
        "Paper-trading performance is DESCRIPTIVE observational "
        "validation; it does NOT predict future performance and does NOT "
        "constitute financial advice."
    )
    return " ".join(parts)


__all__ = ["PaperTradePerformanceEngine"]
