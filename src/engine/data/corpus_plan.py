"""
Corpus-preparation planner (Checkpoint 3B — Historical Corpus Preparation).

The planner computes the DETERMINISTIC INGESTION PLAN for a historical
research corpus BEFORE any market data is fetched:

    universe x requested timeframes x monthly window chunks -> one row
    per (instrument, timeframe) -> current stored coverage -> exact list
    of missing chunk requests the operator still has to issue.

It is PURE PLANNING — ORCHESTRATION ONLY:

* It NEVER calls a provider, NEVER fetches market data and never issues
  an HTTP request (planning is network-free by construction).
* It reuses the Phase 6B monthly-chunk helper (:func:`_upstox_monthly_chunks`
  from ``engine.data.historical_provider``), the Phase 6B timeframe
  utilities, the existing :class:`ResearchUniverse` and the existing
  Phase 6B :class:`HistoricalDataStore` ``list_datasets`` coverage
  surface. NO provider logic is duplicated.
* Execution of the plan remains an explicit operator action through the
  EXISTING ``ingest_historical_data.py`` CLI / ``HistoricalMarketDataService.ingest``
  — a planned chunk request is issued exactly like a manually-ingested
  one. The planner itself never ingests.

POINT-IN-TIME / NO-LOOK-AHEAD: the planner accepts NO candle /
future-candle argument and never inspects a provider; coverage is
measured only from what is already persisted in the Phase 6B store. It
implements NO trading / scoring / prediction / decision / evidence
logic, and leaves the authoritative decision engine, trade geometry,
trade planning, paper trading and the live Yahoo completed-candle
boundary untouched.

``data/__init__.py`` stays intentionally empty — import via full paths:

    from engine.data.corpus_plan import CorpusPreparationPlanner
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Iterable, Sequence

from engine.config.corpus_plan_config import (
    CorpusPlanConfig,
    validate_plan_window,
)
from engine.data.historical_provider import _upstox_monthly_chunks
from engine.data.historical_store import (
    HistoricalDataStore,
    StoredDatasetInfo,
)
from engine.models.corpus_plan import (
    CorpusPreparationPlan,
    CorpusPreparationRow,
    DatasetCoverage,
    DatasetCoverageStatus,
)
from engine.models.historical_data import ResearchUniverse

DEFAULT_RESEARCH_UNIVERSE = ResearchUniverse()


def _chunk_key(
    timeframe: str,
    chunk_start: datetime,
    chunk_end: datetime,
) -> str:
    """Deterministic key for one monthly window chunk."""

    return (
        f"{timeframe}:{chunk_start.isoformat()}:{chunk_end.isoformat()}"
    )


def monthly_chunks_for_window(
    start: datetime,
    end: datetime,
) -> tuple[tuple[datetime, datetime], ...]:
    """
    The monthly calendar chunks covering ``[start, end)``.

    Reuses the Phase 6B helper; returns a deterministic tuple of
    half-open ``(chunk_start, chunk_end)`` pairs (chunk boundaries are
    calendar-month ticks, both timezone-aware UTC, ``chunk_end`` never
    exceeds ``end``).
    """

    return tuple(_upstox_monthly_chunks(start, end))


class CorpusPreparationPlanner:
    """
    Builds the deterministic corpus-preparation plan.

    Stateless across calls: identical inputs always produce identical
    plans (the plan id is derived from the canonical identity — no
    wall-clock, no randomness). The planner has no provider dependency
    for PLANNING; an optional provider may be supplied so each row's
    ``provider_supported`` capability is recorded honestly (the plan
    still names every chunk request; unsupported rows are excluded from
    the request accounting and reported explicitly).
    """

    def __init__(
        self,
        config: CorpusPlanConfig | None = None,
        *,
        store: HistoricalDataStore | None = None,
        universe: ResearchUniverse | None = None,
        provider=None,
        instruments: Sequence[str] | None = None,
    ) -> None:
        self.config = config or CorpusPlanConfig()
        self.store = store
        self.universe = universe or DEFAULT_RESEARCH_UNIVERSE
        self.provider = provider
        self._instruments_override = tuple(instruments) if instruments else None

    # ------------------------------------------------------------
    # UNIVERSE RESOLUTION
    # ------------------------------------------------------------

    def _resolve_instruments(
        self,
        instruments: Sequence[str] | None,
    ) -> tuple[str, ...]:
        if instruments is not None:
            normalized = tuple(
                sorted({str(i).strip().upper() for i in instruments if str(i).strip()}),
            )
            if not normalized:
                raise ValueError("instruments must not be empty.")
            return normalized
        if self._instruments_override:
            return tuple(
                sorted(str(i).strip().upper() for i in self._instruments_override),
            )
        return tuple(sorted(self.universe))

    # ------------------------------------------------------------
    # COVERAGE
    # ------------------------------------------------------------

    def _stored_overview(self) -> dict[tuple[str, str], StoredDatasetInfo]:
        if self.store is None:
            return {}
        try:
            return {
                (info.instrument, info.timeframe): info
                for info in self.store.list_datasets()
            }
        except Exception:  # noqa: BLE001 - a store failure never aborts planning
            return {}

    def _coverage_for(
        self,
        instrument: str,
        timeframe: str,
        stored: StoredDatasetInfo | None,
        chunks: tuple[tuple[datetime, datetime], ...],
        stored_candles: tuple | None,
    ) -> DatasetCoverage:
        required_chunks = len(chunks)
        chunk_keys = {
            _chunk_key(timeframe, c_start, c_end): (c_start, c_end)
            for c_start, c_end in chunks
        }
        if self.store is None:
            # Without a store, no chunk can be confirmed covered; every
            # required chunk is reported as missing/unknown planning data.
            return DatasetCoverage(
                instrument=instrument,
                timeframe=timeframe,
                status=DatasetCoverageStatus.UNAVAILABLE,
                required_chunks=required_chunks,
                missing_chunk_keys=tuple(sorted(chunk_keys)),
                note="no historical store configured; coverage unavailable.",
            )
        if stored is None or stored.candle_count == 0:
            if stored is None:
                return DatasetCoverage(
                    instrument=instrument,
                    timeframe=timeframe,
                    status=DatasetCoverageStatus.MISSING,
                    required_chunks=required_chunks,
                    missing_chunk_keys=tuple(sorted(chunk_keys)),
                    note="dataset not stored; every planned chunk is missing.",
                )
            return DatasetCoverage(
                instrument=instrument,
                timeframe=timeframe,
                status=DatasetCoverageStatus.EMPTY,
                required_chunks=required_chunks,
                missing_chunk_keys=tuple(sorted(chunk_keys)),
                note="dataset stored but empty; every planned chunk is missing.",
            )
        first_ts = datetime.fromisoformat(stored.first_timestamp)
        last_ts = datetime.fromisoformat(stored.last_timestamp)
        covered: list[str] = []
        for key, (c_start, c_end) in sorted(chunk_keys.items()):
            # A chunk is covered when the stored series intersects it
            # (inclusive of the chunk start instant / the requested end).
            if first_ts < c_end and last_ts >= c_start:
                covered.append(key)
        missing = tuple(sorted(set(chunk_keys) - set(covered)))
        if len(missing) == 0:
            status = DatasetCoverageStatus.COMPLETE
            note = "full planned window is covered by the stored dataset."
        else:
            status = DatasetCoverageStatus.PARTIAL
            note = (
                f"{len(covered)}/{required_chunks} planned chunks covered; "
                f"{len(missing)} chunk(s) still missing."
            )
        return DatasetCoverage(
            instrument=instrument,
            timeframe=timeframe,
            status=status,
            stored_count=stored.candle_count,
            stored_first=stored.first_timestamp,
            stored_last=stored.last_timestamp,
            required_chunks=required_chunks,
            covered_chunks=len(covered),
            missing_chunk_keys=missing,
            note=note,
        )

    # ------------------------------------------------------------
    # PLAN
    # ------------------------------------------------------------

    def plan(
        self,
        instruments: Sequence[str] | None = None,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        label: str = "",
        metadata: Iterable[tuple[str, str]] | None = None,
    ) -> CorpusPreparationPlan:
        """
        Build the deterministic corpus-preparation plan.

        ``start``/``end`` override the configured window (both must be
        present, timezone-aware and ordered when supplied). The plan
        never reads candles — coverage is computed only from the
        existing Phase 6B store's persisted datasets. No provider call.
        """

        cfg = self.config
        plan_start = start if start is not None else cfg.start
        plan_end = end if end is not None else cfg.end
        if plan_start is None or plan_end is None:
            raise ValueError(
                "a plan window is required: pass start/end or set them on "
                "the config.",
            )
        plan_start, plan_end = validate_plan_window(plan_start, plan_end)
        resolved = self._resolve_instruments(instruments)

        # Same chunk boundary grid for every (instrument, timeframe):
        # the monthly calendar chunks of the planned window — identical
        # to what the Phase 6B Upstox provider would request.
        window_chunks = monthly_chunks_for_window(plan_start, plan_end)
        stored = self._stored_overview()

        rows: list[CorpusPreparationRow] = []
        for timeframe in cfg.timeframes:
            for instrument in resolved:
                supported = self._provider_supports(instrument, timeframe)
                info = stored.get((instrument, timeframe))
                coverage = self._coverage_for(
                    instrument,
                    timeframe,
                    info,
                    window_chunks,
                    None,
                )
                rows.append(
                    CorpusPreparationRow(
                        instrument=instrument,
                        timeframe=timeframe,
                        provider_supported=supported,
                        coverage=coverage,
                    ),
                )

        identity = (
            repr(resolved)
            + "|"
            + repr(cfg.timeframes)
            + "|"
            + plan_start.isoformat()
            + "|"
            + plan_end.isoformat()
            + "|"
            + cfg.provider
            + "|"
            + str(label)
            + "|"
            + repr(tuple(sorted((str(k), str(v)) for k, v in (metadata or ()))))
        )
        plan_id = "prep-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        return CorpusPreparationPlan(
            plan_id=plan_id,
            instruments=resolved,
            timeframes=cfg.timeframes,
            start=plan_start,
            end=plan_end,
            provider=cfg.provider,
            rows=tuple(rows),
            label=label,
            metadata=tuple((str(k), str(v)) for k, v in (metadata or ())),
        )

    def _provider_supports(self, instrument: str, timeframe: str) -> bool:
        if self.provider is None:
            return True
        try:
            supported = self.provider.supports(instrument, timeframe)
        except Exception:  # noqa: BLE001 - capability probe never raises
            return False
        return bool(supported)

    # ------------------------------------------------------------
    # COVERAGE COMPARISON (plan vs store, chunk-request accounting)
    # ------------------------------------------------------------

    def coverage_summary(
        self,
        plan: CorpusPreparationPlan,
    ) -> dict[str, int]:
        """
        A deterministic coverage/request summary for a plan.

        Keys (values are count sums over the plan rows):

        ``datasets`` — planned (instrument, timeframe) rows.
        ``datasets_complete`` / ``datasets_partial`` / ``datasets_missing``
            / ``datasets_empty`` / ``datasets_unavailable`` — per-status
            dataset counts.
        ``requests_required`` — every monthly chunk request (provider-
            supported rows only).
        ``requests_covered`` — chunk requests already satisfied.
        ``requests_missing`` — chunk requests outstanding (>= 0).
        ``rows_unsupported`` — rows the selected provider cannot serve.
        """

        return {
            "datasets": plan.dataset_count,
            "datasets_complete": plan.complete_count,
            "datasets_partial": plan.partial_count,
            "datasets_missing": plan.missing_count,
            "datasets_empty": plan.empty_count,
            "datasets_unavailable": plan.unavailable_count,
            "requests_required": plan.required_request_count,
            "requests_covered": plan.covered_request_count,
            "requests_missing": plan.missing_request_count,
            "rows_unsupported": plan.unsupported_count,
        }

    def plan_to_jsonable(self, plan: CorpusPreparationPlan) -> dict:
        """Deterministic JSON-serializable projection of a plan."""

        rows = []
        for row in plan.rows:
            coverage = None
            if row.coverage is not None:
                coverage = {
                    "status": row.coverage.status.value,
                    "stored_count": row.coverage.stored_count,
                    "stored_first": row.coverage.stored_first,
                    "stored_last": row.coverage.stored_last,
                    "required_chunks": row.coverage.required_chunks,
                    "covered_chunks": row.coverage.covered_chunks,
                    "missing_chunk_keys": list(
                        row.coverage.missing_chunk_keys,
                    ),
                    "note": row.coverage.note,
                }
            rows.append(
                {
                    "instrument": row.instrument,
                    "timeframe": row.timeframe,
                    "provider_supported": row.provider_supported,
                    "coverage": coverage,
                },
            )
        return {
            "plan_id": plan.plan_id,
            "instruments": list(plan.instruments),
            "timeframes": list(plan.timeframes),
            "start": plan.start.isoformat(),
            "end": plan.end.isoformat(),
            "provider": plan.provider,
            "label": plan.label,
            "metadata": [list(pair) for pair in plan.metadata],
            "rows": rows,
            "coverage_summary": self.coverage_summary(plan),
        }


__all__ = [
    "CorpusPreparationPlanner",
    "monthly_chunks_for_window",
]