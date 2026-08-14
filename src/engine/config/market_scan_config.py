"""
Configuration for the multi-timeframe market scanner (Sprint 11U).

All timeframe roles, alignment rules, market-level ranking priorities
and eligibility caps live here; no magic numbers are embedded in the
engine. The defaults are deliberately conservative and deterministic.
They are NOT calibrated to any market; they express interpretable,
rule-based scan / ranking criteria.

The scanner layer is DESCRIPTIVE. It identifies the strongest available
technical trade opportunities across instruments and timeframes at one
point in time. It is NOT a probability of success, NOT a profitability
prediction, and NOT a trading recommendation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from engine.models.market_context import MarketTrendState
from engine.models.market_scan import MTFAlignment


class ScanRankingPriority(Enum):
    """
    The deterministic priority dimensions used to rank eligible
    opportunities across instruments / timeframes strongest-first.

    This ranking is DISTINCT from Sprint 11T's per-evaluation-context
    ranking: 11T ranks opportunities within ONE evaluation context;
    11U ranks opportunities ACROSS instruments and timeframes.

    The enum members are listed in the EXACT order the engine applies
    them. Ties at each priority are broken by the next priority, and
    finally by a fully deterministic, direction-symmetric tie-breaker so
    identical inputs always produce identical rankings and no two
    eligible opportunities share a rank.

    A SHORT opportunity never ranks above a LONG opportunity (or vice
    versa) merely because of its direction: direction only influences
    the ranking through the underlying evidence / alignment / scores.
    """

    ELIGIBILITY = "ELIGIBILITY"
    MTF_ALIGNMENT = "MTF_ALIGNMENT"
    OPPORTUNITY_STATUS = "OPPORTUNITY_STATUS"
    DECISION_CLASSIFICATION = "DECISION_CLASSIFICATION"
    DECISION_SCORE = "DECISION_SCORE"
    GEOMETRY_COMPLETENESS = "GEOMETRY_COMPLETENESS"
    RISK_REWARD = "RISK_REWARD"
    CONFLUENCE = "CONFLUENCE"
    CONFLICT_FREE = "CONFLICT_FREE"
    DETERMINISTIC_TIEBREAK = "DETERMINISTIC_TIEBREAK"


#: Canonical market-level ranking order, expressed as a tuple so it can
#: be surfaced verbatim by the report layer (the implementation MUST make
#: it obvious why instrument A ranks above instrument B).
SCAN_RANKING_PRIORITY_ORDER: tuple[ScanRankingPriority, ...] = (
    ScanRankingPriority.ELIGIBILITY,
    ScanRankingPriority.MTF_ALIGNMENT,
    ScanRankingPriority.OPPORTUNITY_STATUS,
    ScanRankingPriority.DECISION_CLASSIFICATION,
    ScanRankingPriority.DECISION_SCORE,
    ScanRankingPriority.GEOMETRY_COMPLETENESS,
    ScanRankingPriority.RISK_REWARD,
    ScanRankingPriority.CONFLUENCE,
    ScanRankingPriority.CONFLICT_FREE,
    ScanRankingPriority.DETERMINISTIC_TIEBREAK,
)


# Higher-timeframe context state -> directional disposition used by the
# alignment logic. RANGE / NEUTRAL are deliberately NON-directional so a
# neutral / ranging higher context is never silently interpreted as
# bullish or bearish. UNKNOWN means insufficient context (the alignment
# is UNKNOWN, never fabricated).
_DIRECTIONAL_CONTEXT = {
    MarketTrendState.BULLISH: "BULLISH",
    MarketTrendState.BEARISH: "BEARISH",
    MarketTrendState.RANGE: None,
    MarketTrendState.NEUTRAL: None,
    MarketTrendState.UNKNOWN: None,
}  # noqa: F841 (re-exported for callers/tests that inspect the policy)


# Strength ordering of MTF alignments for the market-level ranking
# (ALIGNED strongest; UNKNOWN weakest). CONFLICTING is weaker than
# NEUTRAL so a conflicting opportunity never outranks a neutral one.
_ALIGNMENT_STRENGTH = {
    MTFAlignment.ALIGNED: 3,
    MTFAlignment.NEUTRAL: 2,
    MTFAlignment.CONFLICTING: 1,
    MTFAlignment.UNKNOWN: 0}


@dataclass(frozen=True, slots=True)
class TimeframeRoleConfig:
    """
    Configuration for one timeframe role in the scan.

    .. note::
        Provided for completeness / explicit role labelling. The
        :class:`MarketScanner` derives the roles from
        :class:`MarketScanConfig` (context + setup timeframe pair); this
        dataclass documents the role contract.

    Attributes:

    role
        The :class:`TimeframeRole` (CONTEXT_TIMEFRAME / SETUP_TIMEFRAME).

    timeframe
        Canonical timeframe label (e.g. ``"1D"`` / ``"15M"``).

    require_completed_candle
        Whether only a COMPLETED (closed) candle of this timeframe may
        be used at the evaluation time (the context timeframe ALWAYS
        requires a completed candle so no in-progress higher-timeframe
        candle leaks into the scan). ``True`` for context timeframes by
        default; the setup timeframe reads its latest closed candle.
    """

    role: "TimeframeRole"
    timeframe: str
    require_completed_candle: bool = True


# Forward reference resolved at runtime via the import below.
from engine.models.market_scan import TimeframeRole  # noqa: E402


@dataclass(frozen=True, slots=True)
class MarketScanConfig:
    """
    Configuration for ``MarketScanner``.

    Attributes:

    context_timeframe
        The higher / context timeframe label (e.g. ``"1D"``).

    setup_timeframe
        The lower / execution timeframe label (e.g. ``"15M"``).

    min_history
        Minimum number of candles that must exist on the setup timeframe
        before an instrument is evaluated. Instruments with fewer
        candles are INCOMPLETE — insufficient data is never a bullish /
        bearish conclusion.

    require_context_timeframe
        When ``True`` (default), an instrument whose context timeframe
        carries no completed candle before the evaluation time is
        INCOMPLETE. Missing higher-timeframe context is never fabricated.

    require_completed_context_candle
        When ``True`` (default), the higher-timeframe context at
        evaluation time ``T`` uses ONLY the latest higher-timeframe
        candle that CLOSED strictly before ``T``. An in-progress
        higher-timeframe candle is NEVER used — this is the core
        look-ahead protection for multi-timeframe scans.

    require_opportunity_for_eligibility
        When ``True`` (default), an instrument is only eligible for
        market-level ranking when it produced an eligible Sprint 11T
        opportunity on the setup timeframe. When ``False``, a
        directional candidate / decision may still rank (relaxed).

    max_surfaced_opportunities
        Maximum number of opportunities surfaced (best + alternatives).
        ``None`` means no cap. The best always counts as 1 surfaced.

    label
        Human-readable scan label (part of the deterministic scan id).

    metadata
        Arbitrary deterministic string metadata (part of the scan id).

    Validation:

    * ``min_history`` must be non-negative.
    * ``max_surfaced_opportunities`` must be >= 1 when set.
    * ``context_timeframe`` and ``setup_timeframe`` must differ.
    """

    context_timeframe: str = "1D"
    setup_timeframe: str = "15M"
    min_history: int = 10
    require_context_timeframe: bool = True
    require_completed_context_candle: bool = True
    require_opportunity_for_eligibility: bool = True
    max_surfaced_opportunities: int | None = None
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.min_history < 0:
            raise ValueError("min_history must be non-negative.")
        if (
            self.max_surfaced_opportunities is not None
            and self.max_surfaced_opportunities < 1
        ):
            raise ValueError(
                "max_surfaced_opportunities must be >= 1 when set.",
            )
        if self.context_timeframe == self.setup_timeframe:
            raise ValueError(
                "context_timeframe and setup_timeframe must differ.",
            )


__all__ = [
    "MarketScanConfig",
    "SCAN_RANKING_PRIORITY_ORDER",
    "ScanRankingPriority",
    "TimeframeRoleConfig",
]
