"""
Historical research corpus engine (Product Phase 6C).

This module builds the RESEARCH-READY HISTORICAL CORPUS on top of the
Product Phase 6B historical data foundation
(:class:`HistoricalMarketDataService` + :class:`HistoricalDataStore`).
It answers, for every historical evaluation time ``T``:

    "What did the market look like at ``T``, using ONLY information that
    would actually have been available at that point?"

Product Phase 6C is RESEARCH PREPARATION ONLY:

* It is NOT a trading strategy, NOT a prediction engine, NOT a decision
  engine, NOT an evidence / outcome engine. Phase 6D consumes the
  corpus; Phase 6C never calls the decision engine, never generates
  trade candidates, never creates paper trades and never computes
  historical performance evidence.

POINT-IN-TIME CORRECTNESS (NON-NEGOTIABLE, structural):

* Setup timeframe: only candles with ``timestamp <= T`` are usable.
* Context (higher) timeframe: only candles with ``timestamp < T`` are
  usable — a higher-timeframe candle whose close extends to or beyond
  ``T`` is NEVER treated as completed information at ``T``.
* Structure / context information is computed as
  ``historical data up to T -> compute features -> state at T``
  (the reused Sprint 11P :class:`MarketContextEngine` is fed ONLY the
  historical prefix), never "compute features on the full future
  dataset, then slice".
* The public API accepts NO ``future`` / ``future_candles`` / ``lookahead``
  parameter.

REUSE, DO NOT RE-INVENT: the corpus reuses the Phase 6B storage /
validation / gap infrastructure and the existing Sprint 11P market
context + Sprint 11U MTF alignment intelligence VERBATIM. It introduces
NO competing candle / data representation and NO second storage
mechanism (the corpus itself is an in-memory research view over the
existing store; a minimal corpus-metadata persistence layer is provided
separately in :mod:`engine.data.research_corpus_store`).

``data/__init__.py`` stays intentionally empty — import via full paths:

    from engine.data.research_corpus import HistoricalResearchCorpusEngine
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Iterable, Sequence

from engine.config.research_corpus_config import ResearchCorpusConfig
from engine.data.historical_gaps import detect_gaps
from engine.data.historical_serialization import HISTORICAL_SCHEMA_VERSION
from engine.data.historical_service import HistoricalMarketDataService
from engine.data.historical_store import HistoricalDataStoreError
from engine.data.historical_times import canonical_timeframe
from engine.intelligence.market_context_engine import MarketContextEngine
from engine.intelligence.mtf_alignment import MTFAlignmentEngine
from engine.models.historical_availability import HistoricalAvailabilityStatus
from engine.models.historical_data import (
    GapKind,
    HistoricalDataError,
    HistoricalDatasetSlice,
    HistoricalDataRequest,
)
from engine.models.market_context import MarketContext
from engine.models.market_scan import MTFAlignment
from engine.models.ohlcv import OHLCVCandle
from engine.models.research_corpus import (
    CorpusBuildIssue,
    CorpusDataQuality,
    CorpusEvaluationPoint,
    CorpusInstrumentDataset,
    CorpusPointStatus,
    CorpusTimeframeSlice,
    HistoricalMarketState,
    HistoricalResearchCorpus,
    ResearchCorpusReport,
)


#: Corpus format version carried onto reports (research metadata, NOT a
#: second storage schema — the Phase 6B candle store remains the single
#: persistence mechanism).
CORPUS_VERSION = "research-corpus-v1"


def _utc(timestamp: datetime) -> datetime | None:
    """Normalize an aware timestamp; ``None`` for naive values."""

    if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
        return None
    return timestamp


def _direction_of(context: MarketContext | None) -> str:
    """Map a reused market-context trend to a directional string."""

    if context is None:
        return ""
    state = context.trend.state.name
    if state == "BULLISH":
        return "LONG"
    if state == "BEARISH":
        return "SHORT"
    return ""


def evaluation_grid(
    candles: Iterable[OHLCVCandle],
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    every: int = 1,
) -> tuple[datetime, ...]:
    """
    The canonical evaluation grid for a candle series.

    The grid IS the existing candle boundary grid: the timestamps of
    the candles inside ``[start, end]``, sampled one every ``every``
    candles. The corpus never invents arbitrary wall-clock evaluation
    timestamps — evaluation points exist exactly where completed setup
    candles exist. Deterministic; the input is never mutated.
    """

    if every < 1:
        raise ValueError("every must be >= 1.")
    timestamps: list[datetime] = []
    seen: set[datetime] = set()
    for candle in candles:
        ts = candle.timestamp
        if ts in seen:
            continue
        if start is not None and ts < start:
            continue
        if end is not None and ts > end:
            continue
        seen.add(ts)
        timestamps.append(ts)
    timestamps.sort()
    return tuple(timestamps[::every])


class HistoricalResearchCorpusEngine:
    """
    Build and query the historical research corpus.

    ORCHESTRATION ONLY: loads Phase 6B stored datasets, slices them
    point-in-time, and projects the reused Sprint 11P / 11U intelligence
    onto the historical prefixes. Implements NO trading / scoring /
    prediction / decision / evidence logic. Stateless across calls.
    """

    def __init__(
        self,
        service: HistoricalMarketDataService,
        config: ResearchCorpusConfig | None = None,
        *,
        context_engine: MarketContextEngine | None = None,
        alignment_engine: MTFAlignmentEngine | None = None,
        availability_service: object | None = None,
    ) -> None:
        self.service = service
        self.config = config or ResearchCorpusConfig()
        self._context_engine = context_engine or MarketContextEngine()
        self._alignment_engine = alignment_engine or MTFAlignmentEngine()
        self._availability_service = availability_service

    # ------------------------------------------------------------
    # PUBLIC API — SAMPLING
    # ------------------------------------------------------------

    def evaluation_points_for(
        self,
        instrument: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        every: int | None = None,
    ) -> tuple[datetime, ...]:
        """
        The canonical evaluation grid for one instrument's setup series.

        Returns an empty tuple when the stored dataset is unavailable
        (missing data is reported, never fabricated).
        """

        slice_ = self._load_slice(instrument, self.config.setup_timeframe)
        if slice_ is None:
            return ()
        return evaluation_grid(
            slice_.candles,
            start=start if start is not None else self.config.start,
            end=end if end is not None else self.config.end,
            every=every if every is not None else self.config.sample_every,
        )

    # ------------------------------------------------------------
    # PUBLIC API — RESEARCH STATE (the Phase 6D entry point)
    # ------------------------------------------------------------

    def evaluation_point(
        self,
        instrument: str,
        evaluation_time: datetime,
    ) -> CorpusEvaluationPoint:
        """
        Reconstruct the historical evaluation point for one instrument
        at one evaluation time.

        ALWAYS returns an explicit :class:`CorpusEvaluationPoint`: a
        VALID point carries the reconstructed
        :class:`HistoricalMarketState`; a skipped point carries the
        specific status + reason (never fabricated, never silently
        dropped). Only data available at ``evaluation_time`` is used.
        """

        cfg = self.config
        instrument = str(instrument).strip().upper()
        boundary = _utc(evaluation_time)
        if boundary is None:
            raise ValueError("evaluation_time must be timezone-aware.")
        setup_slice = self._load_slice(instrument, cfg.setup_timeframe)
        context_slice = (
            self._load_slice(instrument, cfg.context_timeframe)
            if cfg.has_context_timeframe
            else None
        )
        setup_quality = self._quality(
            setup_slice, cfg.setup_timeframe, instrument,
        )
        context_quality = (
            self._quality(context_slice, cfg.context_timeframe, instrument)
            if cfg.has_context_timeframe
            else None
        )
        return self._evaluate_point(
            instrument=instrument,
            evaluation_time=boundary,
            setup_slice=setup_slice,
            context_slice=context_slice,
            setup_quality=setup_quality,
            context_quality=context_quality,
        )

    def get_state(
        self,
        instrument: str,
        evaluation_time: datetime,
    ) -> HistoricalMarketState | None:
        """
        The historical market state at ``evaluation_time``, or ``None``.

        Convenience wrapper over :meth:`evaluation_point`: returns the
        reconstructed state for a VALID point and ``None`` for a skipped
        / invalid point (the explicit skip reason is available via
        :meth:`evaluation_point`). Phase 6D uses this as the research
        dataset API — it never sees providers, normalization, storage
        or symbol resolution.
        """

        return self.evaluation_point(instrument, evaluation_time).state

    # ------------------------------------------------------------
    # PUBLIC API — CORPUS BUILD
    # ------------------------------------------------------------

    def build(
        self,
        instruments: Sequence[str] | None = None,
        *,
        label: str = "",
        metadata: Iterable[tuple[str, str]] | None = None,
    ) -> HistoricalResearchCorpus:
        """
        Build the research corpus over the configured universe.

        Loads every requested instrument's setup + context datasets from
        the Phase 6B store, generates the deterministic evaluation grid
        (the union of setup candle boundaries across all loaded setup
        series, sampled every ``config.sample_every``), evaluates every
        (instrument, evaluation time) point point-in-time, and assembles
        the data-quality report. Failures are explicit; no unusable
        historical data is silently dropped.

        When ``config.auto_acquire`` is ``True`` and an availability
        service has been configured, any missing historical data is
        automatically acquired through the existing
        :class:`HistoricalDataAvailabilityService` pipeline before
        corpus construction proceeds. When ``False`` (the default),
        building a corpus behaves exactly as before: no provider calls,
        no network requests, no persistence changes.
        """

        cfg = self.config
        requested = self._resolve_instruments(instruments)

        if cfg.auto_acquire and self._availability_service is not None:
            self._auto_acquire_missing(requested, label, metadata)
        requested_timeframes = (
            (cfg.setup_timeframe, cfg.context_timeframe)
            if cfg.has_context_timeframe
            else (cfg.setup_timeframe,)
        )

        datasets: list[CorpusInstrumentDataset] = []
        build_issues: list[CorpusBuildIssue] = []
        loaded_setup: set[str] = set()
        loaded_context: set[str] = set()
        providers: set[str] = set()
        ingestion_versions: set[str] = set()
        rejected_future_records = 0
        grids: list[datetime] = []

        for instrument in requested:
            dataset, rejected_future = self._load_dataset(instrument, build_issues)
            datasets.append(dataset)
            rejected_future_records += rejected_future
            provider, version = self._provenance_info(
                instrument, cfg.setup_timeframe,
            )
            if provider:
                providers.add(provider)
            if version:
                ingestion_versions.add(version)
            if dataset.available:
                loaded_setup.add(instrument)
                grids.extend(
                    evaluation_grid(
                        dataset.setup_candles,
                        start=cfg.start,
                        end=cfg.end,
                    ),
                )
            if cfg.has_context_timeframe and dataset.context_quality is not None:
                context_ok = not any(
                    i.timeframe == cfg.context_timeframe for i in dataset.issues
                )
                if context_ok:
                    loaded_context.add(instrument)

        grid = tuple(sorted(set(grids)))[:: cfg.sample_every]

        points: list[CorpusEvaluationPoint] = []
        for dataset in datasets:
            for boundary in grid:
                points.append(
                    self._evaluate_point(
                        instrument=dataset.instrument,
                        evaluation_time=boundary,
                        setup_slice=self._slice_from(dataset, setup=True),
                        context_slice=self._slice_from(dataset, setup=False),
                        setup_quality=dataset.setup_quality,
                        context_quality=dataset.context_quality,
                    ),
                )

        counts = {status: 0 for status in CorpusPointStatus}
        for point in points:
            counts[point.status] += 1

        available_timeframes = tuple(
            timeframe
            for timeframe, loaded in (
                (cfg.setup_timeframe, loaded_setup),
                (cfg.context_timeframe, loaded_context),
            )
            if timeframe and loaded
        )
        storage_status = self._storage_status(len(loaded_setup), len(requested))
        report = ResearchCorpusReport(
            requested_instruments=requested,
            loaded_instruments=tuple(sorted(loaded_setup)),
            missing_instruments=tuple(
                sorted(set(requested) - loaded_setup),
            ),
            requested_timeframes=requested_timeframes,
            available_timeframes=available_timeframes,
            per_instrument_quality=tuple(
                (d.instrument, d.setup_quality) for d in datasets if d.available
            ),
            evaluation_count=len(points),
            valid_count=counts[CorpusPointStatus.VALID],
            insufficient_history_count=counts[
                CorpusPointStatus.INSUFFICIENT_HISTORY
            ],
            missing_data_count=counts[CorpusPointStatus.MISSING_DATA],
            data_gap_count=counts[CorpusPointStatus.DATA_GAP],
            invalid_count=counts[CorpusPointStatus.INVALID],
            rejected_future_records=rejected_future_records,
            provider="+".join(sorted(providers)),
            storage_status=storage_status,
            ingestion_version=(
                "+".join(sorted(ingestion_versions))
                if ingestion_versions
                else str(HISTORICAL_SCHEMA_VERSION)
            ),
            issues=tuple(build_issues),
        )
        corpus_id = self._corpus_id(
            requested=requested,
            grid=grid,
            label=label,
            metadata=metadata,
        )
        return HistoricalResearchCorpus(
            corpus_id=corpus_id,
            instruments=requested,
            setup_timeframe=cfg.setup_timeframe,
            context_timeframe=cfg.context_timeframe,
            datasets=tuple(datasets),
            evaluation_points=tuple(points),
            report=report,
            label=label,
            metadata=tuple(sorted((str(k), str(v)) for k, v in (metadata or ()))),
        )

    # ------------------------------------------------------------
    # INTERNAL — INSTRUMENT RESOLUTION + LOADING
    # ------------------------------------------------------------

    def _auto_acquire_missing(
        self,
        instruments: tuple[str, ...],
        label: str,
        metadata: Iterable[tuple[str, str]] | None,
    ) -> None:
        """
        Automatically acquire missing historical data through the existing
        :class:`HistoricalDataAvailabilityService` pipeline.

        For each (instrument, timeframe) pair, a
        :class:`HistoricalDataRequest` is constructed and delegated to
        the availability service. The service determines coverage through
        the existing :class:`CorpusPreparationPlanner`, acquires only
        missing chunks through the existing ingestion pipeline, and
        persists them into the existing :class:`HistoricalDataStore`.

        Acquisition failures are reported via the existing status/error
        model (:class:`HistoricalAvailabilityStatus`,
        :class:`AcquisitionFailure`) — they do NOT halt the corpus build.
        The corpus build proceeds with whatever data is available after
        acquisition, exactly as it would without auto-acquire.
        """

        cfg = self.config
        if self._availability_service is None:
            return
        start = cfg.start
        end = cfg.end
        if start is None or end is None:
            return
        timeframes = (
            (cfg.setup_timeframe, cfg.context_timeframe)
            if cfg.has_context_timeframe
            else (cfg.setup_timeframe,)
        )
        for instrument in instruments:
            for timeframe in timeframes:
                request = HistoricalDataRequest(
                    instrument=instrument,
                    timeframe=timeframe,
                    start=start,
                    end=end,
                )
                try:
                    self._availability_service.get_historical_data(
                        request,
                        reference_now=end,
                        label=label,
                        metadata=metadata,
                    )
                except Exception:
                    pass

    def _resolve_instruments(
        self,
        instruments: Sequence[str] | None,
    ) -> tuple[str, ...]:
        if instruments is None:
            return tuple(self.service.universe.instruments)
        normalized = sorted(
            {str(i).strip().upper() for i in instruments if str(i).strip()},
        )
        return tuple(normalized)

    def _load_slice(
        self,
        instrument: str,
        timeframe: str,
    ) -> HistoricalDatasetSlice | None:
        """Load the windowed stored slice; ``None`` when unavailable."""

        return self._load_series(instrument, timeframe)[0]

    def _load_dataset(
        self,
        instrument: str,
        issues: list[CorpusBuildIssue],
    ) -> tuple[CorpusInstrumentDataset, int]:
        """Load one instrument's setup + context series.

        Returns the dataset and the number of stored records excluded
        because they extend beyond the requested window end (the corpus
        "rejected future records" accounting — records future of the
        corpus data window are reported, never silently used).
        """

        cfg = self.config
        setup_slice, setup_beyond = self._load_series(
            instrument, cfg.setup_timeframe,
        )
        setup_quality = self._quality(setup_slice, cfg.setup_timeframe, instrument)
        context_slice: HistoricalDatasetSlice | None = None
        context_quality: CorpusDataQuality | None = None
        context_beyond = 0
        if cfg.has_context_timeframe:
            context_slice, context_beyond = self._load_series(
                instrument, cfg.context_timeframe,
            )
            context_quality = self._quality(
                context_slice, cfg.context_timeframe, instrument,
            )
        dataset_issues: list[CorpusBuildIssue] = []
        available = setup_slice is not None
        if not available:
            dataset_issues.append(
                CorpusBuildIssue(
                    instrument=instrument,
                    timeframe=cfg.setup_timeframe,
                    reason=(
                        f"stored dataset {instrument}/{cfg.setup_timeframe} "
                        "is unavailable (missing or corrupted); reported "
                        "explicitly, never fabricated."
                    ),
                    error=HistoricalDataError.MISSING_INSTRUMENT.name,
                ),
            )
        if (
            cfg.has_context_timeframe
            and context_slice is None
        ):
            dataset_issues.append(
                CorpusBuildIssue(
                    instrument=instrument,
                    timeframe=cfg.context_timeframe,
                    reason=(
                        f"stored context dataset {instrument}/"
                        f"{cfg.context_timeframe} is unavailable; context "
                        "information will be unavailable for this instrument."
                    ),
                    error=HistoricalDataError.MISSING_TIMEFRAME.name,
                ),
            )
        issues.extend(dataset_issues)
        dataset = CorpusInstrumentDataset(
            instrument=instrument,
            setup_timeframe=cfg.setup_timeframe,
            context_timeframe=cfg.context_timeframe,
            setup_candles=setup_slice.candles if setup_slice is not None else (),
            context_candles=(
                context_slice.candles if context_slice is not None else ()
            ),
            setup_quality=setup_quality,
            context_quality=context_quality,
            available=available,
            issues=tuple(dataset_issues),
        )
        return dataset, setup_beyond + context_beyond

    def _load_series(
        self,
        instrument: str,
        timeframe: str,
    ) -> tuple[HistoricalDatasetSlice | None, int]:
        """
        Load the stored series and apply the configured data window.

        Returns ``(windowed_slice, beyond_end_count)``:
        ``windowed_slice`` is ``None`` when the stored dataset is
        unavailable (missing / corrupted / no store configured);
        ``beyond_end_count`` counts stored records strictly after the
        configured window end (the corpus future-record accounting).
        The stored series is never mutated.
        """

        cfg = self.config
        try:
            full = self.service.load_historical(instrument, timeframe)
        except HistoricalDataStoreError:
            return None, 0
        windowed = [
            c
            for c in full.candles
            if (cfg.start is None or c.timestamp >= cfg.start)
            and (cfg.end is None or c.timestamp <= cfg.end)
        ]
        beyond = (
            sum(1 for c in full.candles if c.timestamp > cfg.end)
            if cfg.end is not None
            else 0
        )
        return (
            HistoricalDatasetSlice(
                instrument=instrument,
                timeframe=canonical_timeframe(timeframe) or timeframe,
                candles=tuple(windowed),
                first_timestamp=windowed[0].timestamp if windowed else None,
                last_timestamp=windowed[-1].timestamp if windowed else None,
                count=len(windowed),
                source_count=full.source_count,
            ),
            beyond,
        )

    def _slice_from(
        self,
        dataset: CorpusInstrumentDataset,
        *,
        setup: bool,
    ) -> HistoricalDatasetSlice | None:
        if setup:
            if not dataset.available:
                return None
            return HistoricalDatasetSlice(
                instrument=dataset.instrument,
                timeframe=dataset.setup_timeframe,
                candles=dataset.setup_candles,
                first_timestamp=dataset.setup_quality.first_timestamp,
                last_timestamp=dataset.setup_quality.last_timestamp,
                count=dataset.setup_quality.window_count,
                source_count=dataset.setup_quality.source_count,
            )
        if dataset.context_quality is None:
            return None
        return HistoricalDatasetSlice(
            instrument=dataset.instrument,
            timeframe=dataset.context_timeframe,
            candles=dataset.context_candles,
            first_timestamp=dataset.context_quality.first_timestamp,
            last_timestamp=dataset.context_quality.last_timestamp,
            count=dataset.context_quality.window_count,
            source_count=dataset.context_quality.source_count,
        )

    # ------------------------------------------------------------
    # INTERNAL — DATA QUALITY + PROVENANCE
    # ------------------------------------------------------------

    def _quality(
        self,
        slice_: HistoricalDatasetSlice | None,
        timeframe: str,
        instrument: str,
    ) -> CorpusDataQuality:
        """Observed data-quality summary (reused Phase 6B gap/validation)."""

        invalid_records = self._provenance_rejected(instrument, timeframe)
        if slice_ is None:
            return CorpusDataQuality(
                source_count=0,
                window_count=0,
                first_timestamp=None,
                last_timestamp=None,
                unexpected_gap_count=0,
                closure_gap_count=0,
                invalid_records=invalid_records,
                gaps=(),
                issues=(),
            )
        gaps = detect_gaps(slice_.candles, timeframe)
        unexpected = sum(1 for g in gaps if g.kind is GapKind.UNEXPECTED_GAP)
        return CorpusDataQuality(
            source_count=slice_.source_count,
            window_count=slice_.count,
            first_timestamp=slice_.first_timestamp,
            last_timestamp=slice_.last_timestamp,
            unexpected_gap_count=unexpected,
            closure_gap_count=len(gaps) - unexpected,
            invalid_records=invalid_records,
            gaps=gaps,
            # Per-record validation issues happened at INGESTION time
            # (Phase 6B); the corpus reads the already-validated stored
            # series, so there are legitimately no new issues here.
            issues=(),
        )

    def _provenance_lines(
        self,
        instrument: str,
        timeframe: str,
    ) -> tuple[str, ...]:
        store = self.service.store
        if store is None:
            return ()
        try:
            return store.load_provenance(instrument, timeframe)
        except HistoricalDataStoreError:
            return ()

    def _provenance_rejected(self, instrument: str, timeframe: str) -> int:
        total = 0
        for line in self._provenance_lines(instrument, timeframe):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                total += int(payload.get("records_rejected") or 0)
            except (TypeError, ValueError):
                continue
        return total

    def _provenance_info(
        self,
        instrument: str,
        timeframe: str,
    ) -> tuple[str, str]:
        provider = ""
        version = ""
        for line in self._provenance_lines(instrument, timeframe):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            provider = str(payload.get("provider") or provider)
            version = str(payload.get("schema_version") or version)
        return provider, version

    def _storage_status(self, loaded: int, requested: int) -> str:
        if self.service.store is None:
            return "unavailable"
        if requested == 0:
            return "unavailable"
        if loaded == requested:
            return "persisted"
        if loaded == 0:
            return "unavailable"
        return "partial"

    # ------------------------------------------------------------
    # INTERNAL — POINT EVALUATION (point-in-time)
    # ------------------------------------------------------------

    def _evaluate_point(
        self,
        *,
        instrument: str,
        evaluation_time: datetime,
        setup_slice: HistoricalDatasetSlice | None,
        context_slice: HistoricalDatasetSlice | None,
        setup_quality: CorpusDataQuality,
        context_quality: CorpusDataQuality | None,
    ) -> CorpusEvaluationPoint:
        cfg = self.config
        base = {
            "instrument": instrument,
            "evaluation_time": evaluation_time,
            "setup_timeframe": cfg.setup_timeframe,
            "context_timeframe": cfg.context_timeframe,
        }

        if setup_slice is None:
            return CorpusEvaluationPoint(
                status=CorpusPointStatus.MISSING_DATA,
                reason="no stored setup dataset for this instrument.",
                **base,
            )

        # Setup boundary: usable = candles with timestamp <= T (a PREFIX
        # of the stored window — "historical data up to T", never the
        # full future dataset sliced afterwards).
        setup_prefix = tuple(
            c for c in setup_slice.candles if c.timestamp <= evaluation_time
        )
        if not setup_prefix:
            return CorpusEvaluationPoint(
                status=CorpusPointStatus.MISSING_DATA,
                reason="no usable setup candle exists at or before the "
                "evaluation time.",
                **base,
            )
        if len(setup_prefix) < cfg.min_setup_history:
            return CorpusEvaluationPoint(
                status=CorpusPointStatus.INSUFFICIENT_HISTORY,
                history_count=len(setup_prefix),
                reason=(
                    f"setup history {len(setup_prefix)} < required "
                    f"{cfg.min_setup_history}; point skipped, never padded."
                ),
                **base,
            )

        # Context boundary: usable = candles with timestamp < T (strictly
        # completed higher-timeframe candles only; an in-progress HTF
        # candle is NEVER treated as completed information at T).
        context_prefix: tuple[OHLCVCandle, ...] = ()
        if cfg.has_context_timeframe:
            context_prefix = tuple(
                c
                for c in (context_slice.candles if context_slice else ())
                if c.timestamp < evaluation_time
            )
            if not context_prefix:
                return CorpusEvaluationPoint(
                    status=CorpusPointStatus.MISSING_DATA,
                    reason=(
                        "no completed context candle exists strictly before "
                        "the evaluation time."
                    ),
                    **base,
                )
            if len(context_prefix) < cfg.min_context_history:
                return CorpusEvaluationPoint(
                    status=CorpusPointStatus.INSUFFICIENT_HISTORY,
                    history_count=len(setup_prefix),
                    reason=(
                        f"context history {len(context_prefix)} < required "
                        f"{cfg.min_context_history}; point skipped, never "
                        "padded."
                    ),
                    **base,
                )

        if cfg.skip_gapped_points:
            for prefix, timeframe in (
                (setup_prefix, cfg.setup_timeframe),
                (context_prefix, cfg.context_timeframe),
            ):
                if not prefix:
                    continue
                if any(
                    g.kind is GapKind.UNEXPECTED_GAP
                    for g in detect_gaps(prefix, timeframe)
                ):
                    return CorpusEvaluationPoint(
                        status=CorpusPointStatus.DATA_GAP,
                        history_count=len(setup_prefix),
                        reason=(
                            f"unexpected data gap in the evaluated {timeframe} "
                            "window; point skipped honestly (missing candles "
                            "are never fabricated)."
                        ),
                        **base,
                    )

        state = self._build_state(
            instrument=instrument,
            evaluation_time=evaluation_time,
            setup_prefix=setup_prefix,
            context_prefix=context_prefix,
            setup_slice=setup_slice,
            context_slice=context_slice,
            setup_quality=setup_quality,
            context_quality=context_quality,
        )
        return CorpusEvaluationPoint(
            status=CorpusPointStatus.VALID,
            state=state,
            history_count=len(setup_prefix),
            **base,
        )

    def _build_state(
        self,
        *,
        instrument: str,
        evaluation_time: datetime,
        setup_prefix: tuple[OHLCVCandle, ...],
        context_prefix: tuple[OHLCVCandle, ...],
        setup_slice: HistoricalDatasetSlice,
        context_slice: HistoricalDatasetSlice | None,
        setup_quality: CorpusDataQuality,
        context_quality: CorpusDataQuality | None,
    ) -> HistoricalMarketState:
        cfg = self.config

        setup_tf_slice = CorpusTimeframeSlice(
            instrument=instrument,
            timeframe=cfg.setup_timeframe,
            candles=setup_prefix,
            evaluation_time=evaluation_time,
            boundary_inclusive=True,
            first_timestamp=setup_prefix[0].timestamp,
            last_timestamp=setup_prefix[-1].timestamp,
            count=len(setup_prefix),
            source_count=setup_slice.source_count,
            quality=setup_quality,
        )
        context_tf_slice: CorpusTimeframeSlice | None = None
        if cfg.has_context_timeframe and context_prefix:
            context_tf_slice = CorpusTimeframeSlice(
                instrument=instrument,
                timeframe=cfg.context_timeframe,
                candles=context_prefix,
                evaluation_time=evaluation_time,
                boundary_inclusive=False,
                first_timestamp=context_prefix[0].timestamp,
                last_timestamp=context_prefix[-1].timestamp,
                count=len(context_prefix),
                source_count=(
                    context_slice.source_count if context_slice is not None else 0
                ),
                quality=context_quality,
            )

        # Structure / context information: computed on the historical
        # PREFIX only (historical data up to T -> compute features ->
        # state at T). The reused Sprint 11P engine confirms swings only
        # with right-side candles present, so no future-confirmed
        # structure can leak into the state at T.
        setup_context = self._context_engine.analyze_at(
            setup_prefix, len(setup_prefix) - 1,
        )
        context_context: MarketContext | None = None
        unavailable: list[str] = []
        if cfg.has_context_timeframe:
            if context_prefix:
                context_context = self._context_engine.analyze_at(
                    context_prefix, len(context_prefix) - 1,
                )
            else:
                unavailable.append(
                    "context timeframe configured but no completed context "
                    "candles are available at this point."
                )
        else:
            unavailable.append(
                "context timeframe disabled by configuration; higher-"
                "timeframe context is unavailable."
            )

        alignment = self._alignment_engine.align(
            context_context, _direction_of(setup_context),
        )
        return HistoricalMarketState(
            instrument=instrument,
            evaluation_time=evaluation_time,
            setup_timeframe=cfg.setup_timeframe,
            context_timeframe=cfg.context_timeframe,
            setup_slice=setup_tf_slice,
            context_slice=context_tf_slice,
            setup_context=setup_context,
            context_context=context_context,
            mtf_alignment=alignment,
            latest_usable_setup_timestamp=setup_prefix[-1].timestamp,
            latest_usable_context_timestamp=(
                context_prefix[-1].timestamp if context_prefix else None
            ),
            structure_unavailable_reasons=tuple(unavailable),
        )

    # ------------------------------------------------------------
    # INTERNAL — IDENTITY
    # ------------------------------------------------------------

    def _corpus_id(
        self,
        *,
        requested: tuple[str, ...],
        grid: tuple[datetime, ...],
        label: str,
        metadata: Iterable[tuple[str, str]] | None,
    ) -> str:
        cfg = self.config
        identity = {
            "version": CORPUS_VERSION,
            "instruments": list(requested),
            "setup_timeframe": cfg.setup_timeframe,
            "context_timeframe": cfg.context_timeframe,
            "min_setup_history": cfg.min_setup_history,
            "min_context_history": cfg.min_context_history,
            "sample_every": cfg.sample_every,
            "start": cfg.start.isoformat() if cfg.start else "",
            "end": cfg.end.isoformat() if cfg.end else "",
            "skip_gapped_points": cfg.skip_gapped_points,
            "grid": [ts.isoformat() for ts in grid],
            "label": label,
            "metadata": sorted((str(k), str(v)) for k, v in (metadata or ())),
        }
        text = json.dumps(identity, sort_keys=True, separators=(",", ":"))
        return "corpus-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "CORPUS_VERSION",
    "HistoricalResearchCorpusEngine",
    "evaluation_grid",
]
