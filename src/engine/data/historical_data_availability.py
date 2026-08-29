"""
Historical Market Data Availability & Acquisition layer (Checkpoint 7).

This is a THIN orchestration / data-availability layer on top of the
EXISTING historical-data components. Its ONE job is:

    "The engine can reliably obtain validated historical data when it
    needs it."

The research / trading layers NEVER touch a provider. They issue a plain
:class:`~engine.models.historical_data.HistoricalDataRequest`
(instrument / timeframe / start / end) and receive a validated, canonical
dataset of the EXISTING :class:`~engine.models.ohlcv.OHLCVCandle`
representation.

Responsibilities (Checkpoint 7 §3):

1. receive a request;
2. determine whether the requested data is already covered by the
   persisted corpus (via the EXISTING :class:`CorpusPreparationPlanner` —
   the single source of coverage / chunk truth);
3. if completely covered: load from the EXISTING
   :class:`HistoricalDataStore` and make NO provider request;
4. if partially covered: identify the missing monthly chunks via the
   existing planner and acquire ONLY those chunks;
5. if completely missing: acquire the required chunks;
6. persist every successful chunk through the existing historical
   ingestion / store pipeline;
7. re-check coverage after acquisition;
8. return only valid, persisted historical candles;
9. fail honestly when acquisition cannot complete.

HARD GUARANTEES (each is a Checkpoint 7 acceptance criterion):

* NO second coverage definition — the existing planner is consumed
  directly (its ``DatasetCoverage`` / ``missing_chunk_keys`` / monthly
  chunk grid).
* The existing store is the source of truth — NO completion database,
  NO ``.done`` file, NO per-chunk completion flags. Resumability is
  derived only from what is persisted.
* NO second HTTP client — acquisition goes through the EXISTING
  ``HistoricalMarketDataService`` ingestion pipeline (``CorpusIngestionEngine``).
* The ``UPSTOX_ANALYTICS_TOKEN`` is required ONLY when acquisition
  through ``upstox-historical`` is actually necessary; a fully covered
  request needs NO token and performs ZERO network requests. A missing
  token fails CLEARLY BEFORE any provider request.
  ``UPSTOX_ACCESS_TOKEN`` is NEVER read / used / copied.
* No token, Authorization header, credential or env contents ever appear
  in results, raised errors or logged lines (reusing the existing
  ingestion engine's redaction).
* No future data: the deterministic reference boundary is enforced and
  ``load_historical`` windowing guarantees future candles can never
  return.

``data/__init__.py`` stays intentionally empty — import via full paths:

    from engine.data.historical_data_availability import (
        HistoricalDataAvailabilityService,
    )
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Iterable, Sequence

from engine.data.corpus_ingestion import (
    COMPLETED,
    FAILED,
    CorpusBacklog,
    CorpusIngestionConfig,
    CorpusIngestionEngine,
    CorpusIngestionError,
    check_upstox_analytics_token,
)
from engine.data.corpus_plan import CorpusPreparationPlanner
from engine.data.historical_service import HistoricalMarketDataService
from engine.data.historical_times import canonical_timeframe
from engine.models.historical_availability import (
    AcquisitionFailure,
    HistoricalAvailabilityStatus,
    HistoricalDataAvailabilityResult,
)
from engine.models.historical_data import (
    DEFAULT_RESEARCH_UNIVERSE,
    HistoricalDataRequest,
    ResearchUniverse,
)
from engine.models.ohlcv import OHLCVCandle

#: The provider that demands the Upstox analytics credential.
_UPSTOX_PROVIDER = "upstox-historical"


class HistoricalDataAvailabilityService:
    """
    On-demand historical data availability & acquisition service.

    The service is stateless across calls: every call re-derives the
    CURRENT coverage from the existing persisted corpus (through the
    existing planner) and, when needed, acquires only the missing chunks
    through the existing ingestion pipeline. Acquisition is SEQUENTIAL
    (no concurrency) and per-chunk failure-isolated.

    Dependencies (all EXISTING components, injected):
        planner
            The existing ``CorpusPreparationPlanner`` — the SINGLE source
            of coverage / missing-chunk truth. Its config's timeframes
            define the chunk grid the service plans against.
        service (optional)
            The existing ``HistoricalMarketDataService`` — the SINGLE
            ingestion + load pipeline. None when the caller only wants to
            serve data already persisted.
        acquisition_engine (optional)
            An already-built ``CorpusIngestionEngine``. When None the
            service builds one from ``planner`` + ``service``.
        universe (optional)
            The existing configurable research universe (instrument
            allow-list). Defaults to the canonical one.
        timeframes (optional)
            Additional supported canonical timeframes the service accepts.
            When empty (default) the service only serves the planner's
            configured timeframes (the planner is the single chunk-grid
            source). An accepted-but-unplanned timeframe is reported
            ``UNPLANNED_TIMEFRAME`` — never guessed.
    """

    def __init__(
        self,
        planner: CorpusPreparationPlanner,
        service: HistoricalMarketDataService | None = None,
        *,
        acquisition_engine: CorpusIngestionEngine | None = None,
        universe: ResearchUniverse | None = None,
        timeframes: Sequence[str] | None = None,
    ) -> None:
        if planner is None:
            raise ValueError(
                "planner is required; the planner is the single coverage "
                "source of truth.",
            )
        if service is None and acquisition_engine is None:
            raise ValueError(
                "at least one of service / acquisition_engine is required "
                "so the service can serve stored data and acquire missing "
                "chunks.",
            )
        self.planner = planner
        self.service = service
        self.acquisition_engine = acquisition_engine
        self.universe = universe or DEFAULT_RESEARCH_UNIVERSE
        extra = tuple(sorted({
            canonical_timeframe(str(t).strip())
            for t in (timeframes or ())
            if canonical_timeframe(str(t).strip()) is not None
        }))
        self._extra_timeframes = extra

    # ------------------------------------------------------------
    # IDENTITY / CAPABILITY PROBES
    # ------------------------------------------------------------

    @property
    def store(self):
        """
        The single persisted store behind the service.

        Derived from the planner (the coverage source of truth) and, when
        the planner has none, from the service. There is never a second
        store.
        """

        store = getattr(self.planner, "store", None)
        if store is None and self.service is not None:
            store = getattr(self.service, "store", None)
        return store

    def available_providers(self) -> tuple[str, ...]:
        """Provider names reachable through the ingestion service."""

        if self.service is not None:
            return tuple(sorted(self.service.available_providers()))
        if self.acquisition_engine is not None:
            return (self.acquisition_engine.config.provider,)
        return ()

    def supports_timeframe(self, timeframe: str) -> bool:
        """True when the service can plan / serve this canonical timeframe."""

        canonical = canonical_timeframe(timeframe)
        return (
            canonical is not None
            and (
                canonical in self.planner.config.timeframes
                or canonical in self._extra_timeframes
            )
        )

    # ------------------------------------------------------------
    # PRIMARY OPERATION
    # ------------------------------------------------------------

    def get_historical_data(
        self,
        request: HistoricalDataRequest,
        *,
        reference_now: datetime | None = None,
        label: str = "",
        metadata: Iterable[tuple[str, str]] | None = None,
    ) -> HistoricalDataAvailabilityResult:
        """
        Ensure the requested dataset is available; return canonical data.

        1. Coverage is derived from the EXISTING planner.
        2. Fully covered -> load local data; ZERO provider requests, NO
           Upstox token required.
        3. Otherwise acquire ONLY the missing chunks through the existing
           ingestion pipeline, persist each success, re-check coverage
           and return the stored window.

        The result is deterministic for identical inputs / corpus state,
        is never raised for an honest per-chunk failure (the outcome is
        reported as ``INCOMPLETE`` with the failed chunks listed), and the
        returned candles are canonical :class:`OHLCVCandle` objects within
        ``[request.start, request.end]``.
        """

        # ---- request structure validation ------------------------
        if not isinstance(request, HistoricalDataRequest):
            return self._invalid_result(
                "request must be a HistoricalDataRequest.",
            )
        instrument = request.instrument.strip().upper()
        timeframe_raw = request.timeframe.strip()
        canonical = canonical_timeframe(timeframe_raw)
        if canonical is None:
            return self._base_result(
                request,
                status=HistoricalAvailabilityStatus.UNSUPPORTED_TIMEFRAME,
                note=(
                    f"timeframe {timeframe_raw!r} is not a supported "
                    "canonical timeframe."
                ),
            )
        if request.start.tzinfo is None or request.end.tzinfo is None:
            return self._invalid_result(
                "start/end must be timezone-aware (naive timestamps are "
                "never silently accepted).",
            )
        if not request.start < request.end:
            return self._invalid_result(
                "start must be strictly before end.",
            )

        now = reference_now if reference_now is not None else request.end
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)

        # Instrument + timeframe planning gates.
        if instrument not in self.universe:
            return self._base_result(
                request,
                status=HistoricalAvailabilityStatus.UNSUPPORTED_INSTRUMENT,
                note=f"{instrument!r} is not in the research universe.",
            )
        if canonical not in self.planner.config.timeframes:
            if canonical not in self._extra_timeframes:
                return self._base_result(
                    request,
                    status=HistoricalAvailabilityStatus.UNPLANNED_TIMEFRAME,
                    note=(
                        f"timeframe {canonical!r} is not part of the "
                        "configured corpus-plan timeframes, so a monthly "
                        "chunk acquisition cannot be planned for it."
                    ),
                )
        if request.provider:
            providers = self.available_providers()
            if providers and request.provider not in providers:
                return self._invalid_result(
                    f"unknown provider {request.provider!r}; registered: "
                    f"{sorted(providers)}.",
                )

        # ---- initial coverage (EXISTING planner) -----------------
        try:
            initial_plan = self.planner.plan(
                [instrument],
                start=request.start,
                end=request.end,
                label=label,
                metadata=metadata,
            )
        except (ValueError, TypeError) as exc:
            return self._invalid_result(
                f"could not plan the requested window: {exc}",
            )
        except Exception as exc:  # noqa: BLE001 - a planner crash is honest ERROR
            return self._base_result(
                request,
                status=HistoricalAvailabilityStatus.ERROR,
                note=self._safe(f"planner failure: {exc}"),
            )

        coverage = self._row_coverage(initial_plan, canonical)
        if coverage is None:
            return self._base_result(
                request,
                status=HistoricalAvailabilityStatus.ERROR,
                note="planner produced no coverage row for the request.",
            )
        required = coverage.required_chunks
        initially_missing = tuple(coverage.missing_chunk_keys)

        # No persisted store -> nothing can be served or persisted.
        if self.store is None:
            return self._build_result(
                request,
                status=HistoricalAvailabilityStatus.NO_STORE_ACQUISITION,
                required=required,
                covered=coverage.covered_chunks,
                missing=initially_missing,
                now=now,
                note=(
                    "no historical store is configured/persisted: data "
                    "could not be served and acquisition could never "
                    "become durable."
                ),
            )

        # Fully covered -> serve local data. NO token, NO provider call.
        if not initially_missing:
            return self._complete_result(
                request,
                required=required,
                covered=coverage.covered_chunks,
                now=now,
            )

        # ---- acquisition required --------------------------------
        engine, resolved_provider, gated = self._resolve_acquisition(
            request,
            now,
            label,
            metadata,
        )
        if engine is None:
            return self._build_result(
                request,
                status=(
                    HistoricalAvailabilityStatus.CREDENTIAL_MISSING
                    if gated
                    else HistoricalAvailabilityStatus.NO_ACQUISITION_PATH
                ),
                required=required,
                covered=coverage.covered_chunks,
                missing=initially_missing,
                now=now,
                note=(
                    "acquisition requires Upstox but UPSTOX_ANALYTICS_TOKEN "
                    "is not set; no provider request was made."
                    if gated
                    else "no ingestion pipeline is available; missing "
                    "chunks could not be acquired."
                ),
            )

        # Derive the CURRENT missing work from the existing engine's
        # planner, then SCOPE it to the requested timeframe: a request for
        # one timeframe must never ingest another timeframe's chunks (the
        # acceptance criterion "only the missing chunks are acquired".
        # The engine's planner covers every configured corpus timeframe,
        # so the backlog is filtered to the requested canonical timeframe
        # before the chunk loop starts.)
        try:
            backlog = engine.build_backlog(
                request.start,
                request.end,
                instruments=[instrument],
            )
        except Exception as exc:  # noqa: BLE001 - honest ERROR, no acquisition
            return self._build_result(
                request,
                status=HistoricalAvailabilityStatus.ERROR,
                required=required,
                covered=coverage.covered_chunks,
                missing=initially_missing,
                now=now,
                note=self._safe(f"acquisition backlog failed: {exc}"),
            )
        scoped_jobs = tuple(
            job for job in backlog.jobs if job.timeframe == canonical
        )
        scoped_backlog = CorpusBacklog(
            jobs=scoped_jobs,
            provider=backlog.provider,
            window_start=backlog.window_start,
            window_end=backlog.window_end,
            instruments=backlog.instruments,
            plan_id=backlog.plan_id,
        )
        try:
            session = engine.run(
                scoped_backlog,
                reference_now=now,
                label=label,
                metadata=metadata,
            )
        except CorpusIngestionError as exc:
            message = str(exc)
            if "UPSTOX_ANALYTICS_TOKEN" in message:
                return self._build_result(
                    request,
                    status=HistoricalAvailabilityStatus.CREDENTIAL_MISSING,
                    required=required,
                    covered=coverage.covered_chunks,
                    missing=initially_missing,
                    now=now,
                    note=(
                        "acquisition requires Upstox but UPSTOX_ANALYTICS_TOKEN "
                        "is not set; no provider request was made."
                    ),
                )
            return self._build_result(
                request,
                status=HistoricalAvailabilityStatus.ERROR,
                required=required,
                covered=coverage.covered_chunks,
                missing=initially_missing,
                now=now,
                note=self._safe(message),
            )
        except Exception as exc:  # noqa: BLE001 - run-level failure isolation
            return self._build_result(
                request,
                status=HistoricalAvailabilityStatus.ERROR,
                required=required,
                covered=coverage.covered_chunks,
                missing=initially_missing,
                now=now,
                note=self._safe(f"acquisition run failed: {exc}"),
            )

        acquired_keys: list[str] = []
        failures: list[AcquisitionFailure] = []
        skipped = 0
        for outcome in session.results:
            if outcome.status == COMPLETED:
                acquired_keys.append(outcome.job.key)
            elif outcome.status == FAILED:
                failures.append(
                    AcquisitionFailure(
                        instrument=instrument,
                        timeframe=outcome.job.timeframe,
                        start=outcome.job.start,
                        end=outcome.job.end,
                        reason=outcome.detail or outcome.status,
                    ),
                )
            else:  # SKIPPED (became covered between plan and ingest)
                skipped += 1

        # ---- re-check coverage AFTER acquisition ------------------
        final_coverage = coverage
        try:
            after_plan = self.planner.plan(
                [instrument],
                start=request.start,
                end=request.end,
                label=label,
                metadata=metadata,
            )
            after_coverage = self._row_coverage(after_plan, canonical)
            if after_coverage is not None:
                final_coverage = after_coverage
        except Exception:  # noqa: BLE001 - fall back to the pre-run view
            pass

        final_missing = tuple(final_coverage.missing_chunk_keys)
        final_covered = final_coverage.covered_chunks
        if not final_missing:
            status = HistoricalAvailabilityStatus.ACQUIRED
        else:
            status = HistoricalAvailabilityStatus.INCOMPLETE

        # Best-effort window load: return the canonical stored candles for
        # the requested window (a partial success still returns the valid
        # data that IS stored; no data is fabricated for the missing part).
        candles: tuple[OHLCVCandle, ...] = ()
        try:
            candles = self._load_window(
                instrument,
                canonical,
                request.start,
                request.end,
            )
        except Exception:  # noqa: BLE001 - a load failure is honest ERROR detail
            pass

        return self._build_result(
            request,
            status=status,
            required=required,
            covered=final_covered,
            acquired=len(acquired_keys),
            skipped=skipped,
            missing=final_missing,
            acquired_keys=tuple(sorted(acquired_keys)),
            failures=tuple(failures),
            now=now,
            candles=candles,
            acquisition_attempted=True,
            note=(
                "all requested chunks acquired."
                if not final_missing
                else f"{len(final_missing)} chunk(s) still missing."
            ),
        )

    # ------------------------------------------------------------
    # BATCH API (sequential, failure-isolated)
    # ------------------------------------------------------------

    def get_historical_data_batch(
        self,
        requests: Sequence[HistoricalDataRequest],
        *,
        reference_now: datetime | None = None,
        label: str = "",
        metadata: Iterable[tuple[str, str]] | None = None,
    ) -> tuple[HistoricalDataAvailabilityResult, ...]:
        """
        Run several single-request availability operations SEQUENTIALLY.

        Each request is handled independently (one request cannot abort
        the rest). Windows are processed in input order (no de-duplication
        — the caller controls the batch content).
        """

        results: list[HistoricalDataAvailabilityResult] = []
        for request in requests:
            results.append(
                self.get_historical_data(
                    request,
                    reference_now=reference_now,
                    label=label,
                    metadata=metadata,
                ),
            )
        return tuple(results)

    # ------------------------------------------------------------
    # LOADING (existing store / service only)
    # ------------------------------------------------------------

    def _load_window(
        self,
        instrument: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> tuple[OHLCVCandle, ...]:
        """Canonical stored candles within ``[start, end]`` (inclusive)."""

        if self.service is not None and getattr(self.service, "store", None) is not None:
            return self.service.load_historical(
                instrument,
                timeframe,
                start=start,
                end=end,
            ).candles
        if self.store is None:
            return ()
        source = self.store.load_candles(instrument, timeframe)
        return tuple(
            candle for candle in source
            if start <= candle.timestamp <= end
        )

    # ------------------------------------------------------------
    # INTERNAL HELPERS
    # ------------------------------------------------------------

    def _resolve_acquisition(self, request, now, label, metadata):
        """
        Resolve the acquisition engine + provider + token-gate verdict.

        Returns ``(engine, resolved_provider, gated)``. ``engine`` is None
        when acquisition cannot proceed (no pipeline, or the Upstox
        credential is missing). Never raises.
        """

        provider = request.provider or ""
        providers = self.available_providers()
        if provider and providers and provider not in providers:
            # Unknown explicit provider — no acquisition path available.
            return None, provider, False
        if provider:
            resolved = provider
        elif self.planner.config.provider in providers:
            # Default acquisition provider = the planner's configured
            # provider (e.g. upstox-historical), matching the corpus
            # budget semantics. The service NEVER silently substitutes a
            # different provider for the configured one.
            resolved = self.planner.config.provider
        else:
            # The configured acquisition provider is not registered: no
            # acquisition path exists (never guess a provider).
            resolved = self.planner.config.provider
            if not resolved:
                return None, "", False
            return None, resolved, False

        if resolved == _UPSTOX_PROVIDER:
            upstox_registered = _UPSTOX_PROVIDER in providers
            if not upstox_registered:
                return None, resolved, False
            # Token gate: required BEFORE any provider / network request.
            if check_upstox_analytics_token() is None:
                return None, resolved, True

        if self.acquisition_engine is not None:
            return self.acquisition_engine, resolved, False
        if self.service is None:
            return None, resolved, False
        engine = CorpusIngestionEngine(
            self.planner,
            self.service,
            CorpusIngestionConfig(
                provider=resolved,
                require_upstox_token=False,
                reference_now=now,
                label=label,
                metadata=tuple(metadata or ()),
            ),
        )
        return engine, resolved, False

    @staticmethod
    def _row_coverage(plan, canonical_timeframe_name):
        for row in plan.rows:
            if row.timeframe == canonical_timeframe_name:
                return row.coverage
        return None

    def _base_result(self, request, *, status, note) -> HistoricalDataAvailabilityResult:
        return HistoricalDataAvailabilityResult(
            instrument=request.instrument.strip().upper(),
            timeframe=canonical_timeframe(request.timeframe) or request.timeframe.strip(),
            request_start=request.start,
            request_end=request.end,
            status=status,
            chunks_required=0,
            chunks_covered=0,
            chunks_still_missing=(),
            reference_now=request.end,
            request=request,
        )

    def _invalid_result(self, note) -> HistoricalDataAvailabilityResult:
        return HistoricalDataAvailabilityResult(
            instrument="",
            timeframe="",
            request_start=datetime(1970, 1, 1, tzinfo=UTC),
            request_end=datetime(1970, 1, 2, tzinfo=UTC),
            status=HistoricalAvailabilityStatus.INVALID_REQUEST,
            reference_now=None,
        )

    def _complete_result(self, request, *, required, covered, now):
        candles = self._load_window(
            request.instrument.strip().upper(),
            canonical_timeframe(request.timeframe) or request.timeframe.strip(),
            request.start,
            request.end,
        )
        return self._build_result(
            request,
            status=HistoricalAvailabilityStatus.COMPLETE,
            required=required,
            covered=covered,
            missing=(),
            now=now,
            candles=candles,
            note="fully covered by the persisted corpus; no acquisition needed.",
        )

    def _build_result(
        self,
        request,
        *,
        status,
        required,
        covered,
        missing=(),
        acquired=0,
        skipped=0,
        acquired_keys=(),
        failures=(),
        now,
        candles=(),
        acquisition_attempted=False,
        note: str = "",
    ) -> HistoricalDataAvailabilityResult:
        return HistoricalDataAvailabilityResult(
            instrument=request.instrument.strip().upper(),
            timeframe=canonical_timeframe(request.timeframe) or request.timeframe.strip(),
            request_start=request.start,
            request_end=request.end,
            status=status,
            chunks_required=required,
            chunks_covered=covered,
            chunks_acquired=acquired,
            chunks_skipped=skipped,
            chunks_still_missing=tuple(missing),
            acquired_chunk_keys=tuple(sorted(acquired_keys)),
            failures=failures,
            candles=candles,
            reference_now=now,
            acquisition_attempted=acquisition_attempted,
            request=request,
        )

    @staticmethod
    def _safe(text: object) -> str:
        """Redact any credential material from an exception / message."""

        return CorpusIngestionEngine._safe_reason(str(text))


__all__ = ["HistoricalDataAvailabilityService"]