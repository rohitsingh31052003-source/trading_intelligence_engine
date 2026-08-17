"""
Domain models for paper-trade performance analytics (Product Phase 5).

These models describe AGGREGATE, DESCRIPTIVE paper-trading performance
statistics computed from a collection of Product Phase 5
:class:`~engine.models.paper_trade.PaperTrade` records. The analytics
layer is DOWNSTREAM ONLY: it consumes already-computed paper trades and
aggregates them. It NEVER re-evaluates trades, NEVER re-runs the
pipeline, NEVER uses future information, NEVER introduces machine
learning, predictive models, parameter optimization, or live trading.

These models are DELIBERATELY DISTINCT from the Sprint 11X
``HistoricalPerformance*`` models (which aggregate Sprint 11W historical
opportunity outcomes). Product Phase 5 aggregates PAPER TRADES —
human-created validation records of what the existing opportunities
would have done. The names (``PaperTradePerformance*``) reflect this
distinction. The accounting discipline (BOTH_TOUCHED excluded from
win/loss + R; NO_GEOMETRY excluded; profit factor ``None`` when no valid
negative R) mirrors Sprint 11X exactly so the two layers are
semantically consistent.

A :class:`PaperTradePerformanceAnalytics` result is NOT a trading
signal, NOT a prediction, NOT a probability of success, NOT a
profitability guarantee, NOT a statistical-significance claim, and NOT a
trading recommendation. It is a DESCRIPTIVE measurement of how
previously-recorded paper trades resolved.

DESIGN PRINCIPLE — no fabricated values:

Unavailable metrics remain ``None``. Counts remain ``0``. The distinction
between "observed zero" and "not available" is preserved:

* Win / loss rates use the resolved target-vs-stop denominator
  ``TARGET_HIT + STOP_HIT``. ``BOTH_TOUCHED`` (ambiguous),
  ``MANUAL_CLOSE``, ``EXPIRED``, ``CANCELLED``, ``NO_GEOMETRY`` and any
  unresolved state are EXCLUDED from the win/loss denominator. When the
  denominator is zero, the rate is ``None``.
* R-multiple aggregates (total / average / median / gross positive /
  gross negative / profit factor) are computed ONLY over CLOSED trades
  with a valid ``realized_r`` (TARGET_HIT / STOP_HIT / EXPIRED /
  MANUAL_CLOSE). ``BOTH_TOUCHED`` (ambiguous, ``realized_r`` is ``None``)
  and ``NO_GEOMETRY`` are excluded.
* Profit factor is ``gross_positive_R / abs(gross_negative_R)``. When
  there is no valid negative R, the profit factor is ``None``.
* P&L aggregates use ``Decimal`` (never float) and are computed ONLY
  over CLOSED trades with a valid ``realized_pnl``.

DESIGN PRINCIPLE — deterministic:

The same collection of paper trades always produces identical analytics.
Group ordering is deterministic (see
:class:`PaperTradePerformanceBreakdown`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


class PaperTradeGroupDimension(Enum):
    """
    A dimension along which paper-trade performance may be grouped.

    Each dimension corresponds to metadata already present on the paper
    trade (itself a projection of the existing Sprint 11R/11S/11T
    fields). No metadata is invented; when a value is unavailable it
    groups under the empty sentinel ``""`` (displayed as ``"unavailable"``).
    """

    INSTRUMENT = "INSTRUMENT"
    DIRECTION = "DIRECTION"
    DECISION = "DECISION"
    SETUP_TYPE = "SETUP_TYPE"
    TIMEFRAME = "TIMEFRAME"


@dataclass(frozen=True, slots=True)
class PaperTradePerformanceStatistics:
    """
    DESCRIPTIVE aggregate statistics for a set of paper trades.

    All counts are ``int`` (``0`` is a legitimate observed-zero count).
    All rate / ratio / R-multiple metrics are ``float | None`` (``None``
    when not computable from the available trades — NEVER fabricated).
    All P&L aggregates are ``Decimal | None`` (monetary precision
    preserved; NEVER float).

    Attributes:

    total
        Total number of paper trades in the set.

    waiting / open / closed / cancelled / invalidated
        Counts by :class:`PaperTradeStatus`.

    wins / losses
        Counts of determinate favorable (TARGET_HIT) / adverse
        (STOP_HIT) CLOSED trades. ``MANUAL_CLOSE`` / ``EXPIRED`` /
        ``BOTH_TOUCHED`` are NOT counted as wins or losses.

    ambiguous
        Count of CLOSED trades with ``BOTH_TOUCHED`` exit reason
        (ambiguous; never win/loss).

    expired
        Count of CLOSED trades resolved as ``EXPIRED``.

    manual_close
        Count of CLOSED trades resolved via ``MANUAL_CLOSE``.

    win_rate / loss_rate
        ``wins / (wins + losses)`` and ``losses / (wins + losses)``.
        ``None`` when the denominator is zero. BOTH_TOUCHED / EXPIRED /
        MANUAL_CLOSE / NO_GEOMETRY / unresolved states are excluded.

    total_realized_r / average_realized_r / median_realized_r
        R-multiple aggregates over CLOSED trades with a valid
        ``realized_r``. ``None`` when no valid R observations.

    gross_positive_r / gross_negative_r / profit_factor
        Sum of positive R, sum of negative R (abs), and
        ``gross_positive_r / gross_negative_r``. ``profit_factor`` is
        ``None`` when ``gross_negative_r`` is zero (never fabricated).

    valid_r_count
        Number of CLOSED trades contributing a valid ``realized_r``.

    total_realized_pnl / average_realized_pnl
        P&L aggregates (``Decimal``) over CLOSED trades with a valid
        ``realized_pnl``. ``None`` when no valid P&L observations.

    valid_pnl_count
        Number of CLOSED trades contributing a valid ``realized_pnl``.
    """

    total: int = 0
    waiting: int = 0
    open: int = 0
    closed: int = 0
    cancelled: int = 0
    invalidated: int = 0
    wins: int = 0
    losses: int = 0
    ambiguous: int = 0
    expired: int = 0
    manual_close: int = 0

    win_rate: float | None = None
    loss_rate: float | None = None

    total_realized_r: float | None = None
    average_realized_r: float | None = None
    median_realized_r: float | None = None
    gross_positive_r: float | None = None
    gross_negative_r: float | None = None
    profit_factor: float | None = None
    valid_r_count: int = 0

    total_realized_pnl: Decimal | None = None
    average_realized_pnl: Decimal | None = None
    valid_pnl_count: int = 0


@dataclass(frozen=True, slots=True)
class PaperTradePerformanceGroup:
    """A single group's statistics along a grouping dimension."""

    dimension: PaperTradeGroupDimension
    key: str
    statistics: PaperTradePerformanceStatistics


@dataclass(frozen=True, slots=True)
class PaperTradePerformanceBreakdown:
    """A breakdown of paper-trade performance along one dimension."""

    dimension: PaperTradeGroupDimension
    groups: tuple[PaperTradePerformanceGroup, ...]


@dataclass(frozen=True, slots=True)
class PaperTradePerformanceAnalytics:
    """
    Aggregate paper-trade performance analytics.

    Attributes:

    analytics_id
        Deterministic identity (``"ptperf-" + sha256[:16]`` of the
        sorted paper-trade identities). Shuffled-equivalent paper trades
        produce the same id.

    overall
        :class:`PaperTradePerformanceStatistics` over the full set.

    breakdowns
        Tuple of :class:`PaperTradePerformanceBreakdown` (one per
        supported dimension).

    trade_count
        Number of paper trades aggregated.

    label / metadata
        Optional identity / metadata.

    rationale
        Descriptive summary.

    is_empty
        Whether no paper trades were aggregated.
    """

    analytics_id: str
    overall: PaperTradePerformanceStatistics
    breakdowns: tuple[PaperTradePerformanceBreakdown, ...]
    trade_count: int
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    rationale: str = ""

    @property
    def is_empty(self) -> bool:
        return self.trade_count == 0


__all__ = [
    "PaperTradeGroupDimension",
    "PaperTradePerformanceAnalytics",
    "PaperTradePerformanceBreakdown",
    "PaperTradePerformanceGroup",
    "PaperTradePerformanceStatistics",
]
