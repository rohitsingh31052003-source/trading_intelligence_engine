"""
Domain models for the historical research corpus (Product Phase 6C).

These models describe the RESEARCH-READY HISTORICAL CORPUS built ON TOP
of the Product Phase 6B historical data foundation
(:mod:`engine.models.historical_data`). The corpus answers:

    "What did the market look like at a historical evaluation time T,
    using ONLY information that would actually have been available at
    that point?"

Product Phase 6C is RESEARCH PREPARATION ONLY:

* It is NOT a trading strategy, NOT a prediction engine, NOT a decision
  engine, NOT a scoring layer, NOT an evidence / outcome engine. Those
  belong to the FUTURE Product Phase 6D setup/outcome research.
* It never calls the decision engine, never generates trade candidates,
  never creates paper trades and never computes historical "evidence"
  (win rate, average R, profit factor, expected return).
* The EXISTING live path and the Product Phase 6B ingestion path remain
  authoritative and are untouched by these models.

DESIGN PRINCIPLE — point-in-time correctness (NON-NEGOTIABLE):

For every historical evaluation point ``T`` the usable data satisfies
``candle.timestamp <= T`` on the setup timeframe and ``candle.timestamp
< T`` (strictly completed) on the higher / context timeframe. No model
carries a hidden future-candle parameter; an evaluation point whose
required data is missing is reported EXPLICITLY (never fabricated).

DESIGN PRINCIPLE — no fabricated data:

Missing / insufficient / invalid historical data is reported via
:class:`CorpusPointStatus` and :class:`CorpusBuildIssue` — never
padded, repaired or synthesized.

Design rules (match the rest of the model layer):

* Frozen + slots dataclasses.
* Optional fields use ``None`` so "unobserved" / "unavailable" is never
  silently a real value.
* No business logic lives here; the models are data carriers with
  ``__post_init__`` structural validation only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from engine.models.historical_data import HistoricalDataIssue
from engine.models.market_context import MarketContext
from engine.models.market_scan import MTFAlignment
from engine.models.ohlcv import OHLCVCandle


# ============================================================
# STATUS VOCABULARY
# ============================================================


class CorpusPointStatus(Enum):
    """
    Explicit status of ONE historical evaluation point.

    VALID
        The point carries usable setup-timeframe data up to ``T`` (and
        the required context-timeframe data up to the latest completed
        context candle strictly before ``T``) and satisfies the minimum
        history requirements.

    INSUFFICIENT_HISTORY
        Usable data exists at ``T`` but the available history is shorter
        than the configured minimum history requirements. The point is
        skipped, never padded.

    MISSING_DATA
        No usable setup (or required context) candle exists at or before
        ``T`` (empty / absent dataset). The point is skipped, never
        fabricated.

    DATA_GAP
        The series contains UNEXPECTED data gaps within the evaluated
        window and the corpus is configured to skip gapped evaluation
        windows (``skip_gapped_points``). The point is skipped honestly.

    INVALID
        The evaluation point request itself is invalid (unknown
        instrument, unsupported timeframe, naive evaluation timestamp,
        reversed window, ...). No state is produced from invalid input.
    """

    VALID = "VALID"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    MISSING_DATA = "MISSING_DATA"
    DATA_GAP = "DATA_GAP"
    INVALID = "INVALID"

    @property
    def is_usable(self) -> bool:
        """Only VALID points carry a usable historical market state."""

        return self is CorpusPointStatus.VALID


# ============================================================
# DATA-QUALITY SUMMARY
# ============================================================


@dataclass(frozen=True, slots=True)
class CorpusDataQuality:
    """
    Data-quality summary for ONE (instrument, timeframe) corpus series.

    Every field is OBSERVED from the already-validated stored series:
    counts are counts; ``gaps`` are the reused Phase 6B
    :class:`HistoricalGap` records (missing candles are reported, never
    fabricated); ``issues`` are the reused Phase 6B
    :class:`HistoricalDataIssue` records carried from validation /
    provenance. No quality metric is invented here.

    Attributes:

    source_count
        Number of candles in the underlying stored series BEFORE any
        window filtering.

    window_count
        Number of candles inside the requested data window
        (``[start, end]`` when supplied).

    first_timestamp / last_timestamp
        First / last candle timestamps in the requested window
        (``None`` when the window is empty).

    unexpected_gap_count / closure_gap_count
        Number of UNEXPECTED gaps vs plausible market-closure gaps
        within the window (reused Phase 6B gap classification).

    invalid_records
        Rejected records carried from ingestion provenance (0 when the
        provenance is unavailable).

    gaps / issues
        The full reused gap / issue records for audit.
    """

    source_count: int
    window_count: int
    first_timestamp: datetime | None
    last_timestamp: datetime | None
    unexpected_gap_count: int
    closure_gap_count: int
    invalid_records: int
    gaps: tuple = ()
    issues: tuple[HistoricalDataIssue, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "source_count",
            "window_count",
            "unexpected_gap_count",
            "closure_gap_count",
            "invalid_records",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative.")
        if self.window_count > self.source_count:
            raise ValueError("window_count cannot exceed source_count.")

    @property
    def has_unexpected_gaps(self) -> bool:
        return self.unexpected_gap_count > 0


# ============================================================
# HISTORICAL SLICE (one instrument, one timeframe, boundary T)
# ============================================================


@dataclass(frozen=True, slots=True)
class CorpusTimeframeSlice:
    """
    A deterministic point-in-time historical slice for ONE instrument /
    timeframe at an evaluation boundary ``T``.

    LOOK-AHEAD PROTECTION (structural): ``candles`` contains ONLY
    candles whose timestamp satisfies the boundary rule carried by
    ``boundary_inclusive`` (setup timeframe: ``<= T``; context
    timeframe: ``< T`` — a higher-timeframe candle whose close extends
    to or beyond ``T`` is NEVER treated as completed information at
    ``T``).

    Attributes:

    instrument / timeframe
        Canonical identity.

    candles
        Chronologically ordered candles available at the boundary
        (a PREFIX of the stored window — the slice is built as
        "historical data up to T, then evaluate", never "evaluate the
        full future dataset, then slice").

    evaluation_time
        The applied evaluation boundary.

    boundary_inclusive
        ``True`` when the boundary rule is ``timestamp <= T`` (setup
        timeframe); ``False`` when it is ``timestamp < T`` (context
        timeframe).

    first_timestamp / last_timestamp
        First / last candle timestamps in the slice (``None`` when
        empty).

    count
        Number of candles in the slice.

    source_count
        Number of candles in the underlying stored series BEFORE any
        window / evaluation filtering (audit only).

    quality
        The reused :class:`CorpusDataQuality` summary for the full
        window this slice was drawn from (audit / reporting only — it
        never influences the slice content).
    """

    instrument: str
    timeframe: str
    candles: tuple[OHLCVCandle, ...]
    evaluation_time: datetime
    boundary_inclusive: bool
    first_timestamp: datetime | None
    last_timestamp: datetime | None
    count: int
    source_count: int
    quality: CorpusDataQuality | None = None

    def __post_init__(self) -> None:
        if self.evaluation_time.tzinfo is None:
            raise ValueError("evaluation_time must be timezone-aware.")
        if self.count != len(self.candles):
            raise ValueError("count must equal len(candles).")
        if self.count > self.source_count:
            raise ValueError("count cannot exceed source_count.")

    @property
    def is_empty(self) -> bool:
        return not self.candles


# ============================================================
# HISTORICAL MARKET STATE (the Phase 6D research API payload)
# ============================================================


@dataclass(frozen=True, slots=True)
class HistoricalMarketState:
    """
    The reconstructed market state at ONE historical evaluation time.

    This is the payload Phase 6D consumes. It bundles the point-in-time
    context + setup slices with the market-structure / context
    information the EXISTING architecture already computes
    (:class:`MarketContext` from Sprint 11P, computed on the historical
    prefix ONLY) and the reused multi-timeframe alignment classification
    (Sprint 11U). NOTHING is recomputed from future data; every derived
    field is a function of candles available at ``evaluation_time``.

    Attributes:

    instrument / evaluation_time
        Identity + the preserved evaluation timestamp.

    setup_timeframe / context_timeframe
        Canonical timeframe labels.

    setup_slice
        The setup-timeframe slice (``timestamp <= T``).

    context_slice
        The context-timeframe slice (``timestamp < T``, completed
        higher-timeframe candles only), or ``None`` when no context
        timeframe is configured / available.

    setup_context / context_context
        The reused :class:`MarketContext` computed on the setup /
        context slices respectively (``None`` when the corresponding
        slice is missing or too short to evaluate). These reuse the
        existing swing / structure / trend / range / support-resistance
        intelligence — Phase 6C invents NO new indicator.

    mtf_alignment
        The reused Sprint 11U :class:`MTFAlignment` between the context
        trend and the setup trend (``UNKNOWN`` when either context is
        unavailable). Range / neutral context is NEVER silently
        interpreted as bullish / bearish.

    latest_usable_setup_timestamp / latest_usable_context_timestamp
        The timestamps of the latest candles actually usable at
        ``evaluation_time`` (``None`` when the slice is empty). These
        make the point-in-time boundary explicit and auditable.

    structure_unavailable_reasons
        Explicit reasons for every piece of structure / context
        information the existing architecture could NOT provide at this
        point (empty when everything requested was available). An
        unavailable field is reported, never fabricated.
    """

    instrument: str
    evaluation_time: datetime
    setup_timeframe: str
    context_timeframe: str
    setup_slice: CorpusTimeframeSlice
    context_slice: CorpusTimeframeSlice | None
    setup_context: MarketContext | None = None
    context_context: MarketContext | None = None
    mtf_alignment: MTFAlignment = MTFAlignment.UNKNOWN
    latest_usable_setup_timestamp: datetime | None = None
    latest_usable_context_timestamp: datetime | None = None
    structure_unavailable_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.evaluation_time.tzinfo is None:
            raise ValueError("evaluation_time must be timezone-aware.")

    @property
    def has_structure(self) -> bool:
        return self.setup_context is not None

    @property
    def has_context_timeframe(self) -> bool:
        return self.context_slice is not None and not self.context_slice.is_empty


# ============================================================
# EVALUATION POINT
# ============================================================


@dataclass(frozen=True, slots=True)
class CorpusEvaluationPoint:
    """
    ONE historical evaluation point in the corpus.

    A point ALWAYS carries its status + explicit reason. Only a
    :class:`CorpusPointStatus.VALID` point carries a ``state``; a
    skipped point carries ``state=None`` plus the specific skip reason
    (never fabricated, never silently dropped).

    Attributes:

    instrument / evaluation_time / setup_timeframe / context_timeframe
        Identity + the preserved evaluation timestamp.

    status
        :class:`CorpusPointStatus`.

    state
        The reconstructed :class:`HistoricalMarketState` when VALID,
        else ``None``.

    history_count
        Number of usable setup-timeframe candles at the boundary
        (0 for skipped points without usable data).

    reason
        Human-readable explanation (empty on VALID).
    """

    instrument: str
    evaluation_time: datetime
    setup_timeframe: str
    context_timeframe: str
    status: CorpusPointStatus
    state: HistoricalMarketState | None = None
    history_count: int = 0
    reason: str = ""

    def __post_init__(self) -> None:
        if self.evaluation_time.tzinfo is None:
            raise ValueError("evaluation_time must be timezone-aware.")
        if self.history_count < 0:
            raise ValueError("history_count must be non-negative.")
        if self.status is CorpusPointStatus.VALID and self.state is None:
            raise ValueError("a VALID point must carry a state.")
        if self.status is not CorpusPointStatus.VALID and self.state is not None:
            raise ValueError("a non-VALID point must not carry a state.")

    @property
    def is_valid(self) -> bool:
        return self.status is CorpusPointStatus.VALID


# ============================================================
# BUILD ISSUE + DATASET + REPORT
# ============================================================


@dataclass(frozen=True, slots=True)
class CorpusBuildIssue:
    """
    One auditable corpus-build issue (missing dataset, storage failure,
    malformed stored payload, ...). Explicit — never silently dropped.
    """

    instrument: str
    timeframe: str
    reason: str
    error: str = ""


@dataclass(frozen=True, slots=True)
class CorpusInstrumentDataset:
    """
    The loaded corpus series for ONE instrument (setup + context).

    Attributes:

    instrument
        Canonical instrument name.

    setup_timeframe / context_timeframe
        Canonical timeframe labels.

    setup_candles / context_candles
        The full stored window candles (chronological). Point-in-time
        slicing happens per evaluation point; these are the SOURCE
        series the slices are drawn from.

    setup_quality / context_quality
        Reused :class:`CorpusDataQuality` summaries (``None`` for the
        context series when no context timeframe is configured).

    available
        Whether the setup series was loaded successfully.

    issues
        Explicit load issues (empty on success).
    """

    instrument: str
    setup_timeframe: str
    context_timeframe: str
    setup_candles: tuple[OHLCVCandle, ...]
    context_candles: tuple[OHLCVCandle, ...]
    setup_quality: CorpusDataQuality
    context_quality: CorpusDataQuality | None = None
    available: bool = True
    issues: tuple[CorpusBuildIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class ResearchCorpusReport:
    """
    Data-quality + coverage report for a built research corpus.

    Every requirement of the corpus data-quality reporting contract is
    surfaced explicitly: requested vs loaded instruments, requested vs
    available timeframes, per-instrument coverage (date range, candle
    counts, gaps, invalid records), evaluation accounting (valid /
    insufficient / missing / gapped / invalid / future-rejected),
    provider/source identity, storage status and the ingestion
    configuration version. Failures are explicit; unusable historical
    data is never silently dropped.
    """

    requested_instruments: tuple[str, ...]
    loaded_instruments: tuple[str, ...]
    missing_instruments: tuple[str, ...]
    requested_timeframes: tuple[str, ...]
    available_timeframes: tuple[str, ...]
    per_instrument_quality: tuple[tuple[str, CorpusDataQuality], ...]
    evaluation_count: int
    valid_count: int
    insufficient_history_count: int
    missing_data_count: int
    data_gap_count: int
    invalid_count: int
    rejected_future_records: int
    provider: str
    storage_status: str
    ingestion_version: str
    issues: tuple[CorpusBuildIssue, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "evaluation_count",
            "valid_count",
            "insufficient_history_count",
            "missing_data_count",
            "data_gap_count",
            "invalid_count",
            "rejected_future_records",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative.")
        if (
            self.evaluation_count
            != self.valid_count
            + self.insufficient_history_count
            + self.missing_data_count
            + self.data_gap_count
            + self.invalid_count
        ):
            raise ValueError(
                "evaluation_count must equal the sum of the status counts.",
            )


# ============================================================
# THE RESEARCH CORPUS
# ============================================================


@dataclass(frozen=True, slots=True)
class HistoricalResearchCorpus:
    """
    The research-ready historical corpus (Product Phase 6C).

    Bundles the built per-instrument datasets, the generated evaluation
    points (VALID + explicitly skipped), the data-quality report and the
    corpus identity. The corpus is consumed by Phase 6D through
    ``engine.data.research_corpus.HistoricalResearchCorpusEngine`` —
    Phase 6D never sees providers, normalization, storage or symbol
    resolution.

    LOOK-AHEAD PROTECTION: a VALID evaluation point's state is a
    function of candles available at its ``evaluation_time`` ONLY (setup
    ``<= T``, context strictly completed ``< T``). Future candles are
    structurally excluded.
    """

    corpus_id: str
    instruments: tuple[str, ...]
    setup_timeframe: str
    context_timeframe: str
    datasets: tuple[CorpusInstrumentDataset, ...]
    evaluation_points: tuple[CorpusEvaluationPoint, ...]
    report: ResearchCorpusReport
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.evaluation_points

    @property
    def valid_points(self) -> tuple[CorpusEvaluationPoint, ...]:
        return tuple(p for p in self.evaluation_points if p.is_valid)


__all__ = [
    "CorpusBuildIssue",
    "CorpusDataQuality",
    "CorpusEvaluationPoint",
    "CorpusInstrumentDataset",
    "CorpusPointStatus",
    "CorpusTimeframeSlice",
    "HistoricalMarketState",
    "HistoricalResearchCorpus",
    "ResearchCorpusReport",
]
