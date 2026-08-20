"""
Historical Market Data Service (Product Phase 6A).

This is the SERVICE LAYER for the historical-data foundation. It sits
above the provider / validation / gap / persistence layers and exposes
the ingestion workflow used by the CLI and the dashboard:

* ``fetch_historical(request)`` — provider -> normalize (UTC, sort,
  dedupe) -> validate -> gap detection -> provenance.
* ``validate_historical(...)`` — explicit validation only.
* ``store_historical(...)`` — idempotent persistence via the store.
* ``ingest(...)`` — fetch + validate + store combined.
* ``load_historical(...)`` — load a stored dataset with optional
  window / evaluation-time boundary (look-ahead protection).

PROVIDER concerns stay BELOW this layer: the service depends only on
the :class:`HistoricalMarketDataProvider` protocol; providers are
replaceable without touching the service / research layer.

LOOK-AHEAD PROTECTION (NON-NEGOTIABLE):

* Ingestion rejects future-dated candles relative to the caller's
  ``reference_now`` (default: ``datetime.now(UTC)`` injected for
  determinism in tests).
* A request with a future ``end`` is rejected INVALID unless
  ``request.allow_future_end`` is set.
* No outcome evaluator is called during ingestion.
* No historical-evaluation pipeline is called during ingestion.

The service implements NO trading / scoring / prediction / decision /
geometry / evidence logic. It establishes trustworthy historical data
only.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Sequence

from engine.data.historical_gaps import (
    GapDetectionConfig,
    detect_gaps,
)
from engine.data.historical_provider import (
    HistoricalMarketDataProvider,
    HistoricalProviderResponse,
)
from engine.data.historical_store import (
    HistoricalDataStore,
    HistoricalDataStoreError as HistoricalStoreError,
)
from engine.data.historical_times import canonical_timeframe
from engine.data.historical_validation import HistoricalDataValidator
from engine.models.historical_data import (
    DEFAULT_RESEARCH_UNIVERSE,
    HistoricalDataError,
    HistoricalDataIssue,
    HistoricalDataRequest,
    HistoricalDatasetSlice,
    HistoricalFetchResult,
    HistoricalIngestionStatus,
    HistoricalIngestResult,
    HistoricalProvenance,
    HistoricalStoreResult,
    ProviderResponseStatus,
    ResearchUniverse,
)
from engine.models.ohlcv import OHLCVCandle


class HistoricalMarketDataService:
    """
    Service layer over provider + validation + gaps + persistence.

    Stateless across calls; deterministic given the same inputs
    (``reference_now`` is explicit; ``gap_config`` is config).
    """

    def __init__(
        self,
        provider: HistoricalMarketDataProvider | None = None,
        *,
        providers: Sequence[HistoricalMarketDataProvider] | None = None,
        universe: ResearchUniverse | None = None,
        store: HistoricalDataStore | None = None,
        gap_config: GapDetectionConfig | None = None,
    ) -> None:
        # Provider registry (name -> provider). A single provider or a
        # sequence is accepted; the default is a deterministic local one.
        from engine.data.historical_provider import (
            DeterministicLocalHistoricalProvider,
        )

        self.universe = universe or DEFAULT_RESEARCH_UNIVERSE
        self.store = store
        self.gap_config = gap_config
        if providers is None:
            providers = [provider] if provider is not None else [DeterministicLocalHistoricalProvider()]
        self.providers = {p.provider_name: p for p in providers}
        if not self.providers:
            raise ValueError("at least one provider is required.")

    # ------------------------------------------------------------
    # PROVIDER RESOLUTION
    # ------------------------------------------------------------

    def _provider_for(
        self,
        request: HistoricalDataRequest,
    ) -> HistoricalMarketDataProvider:
        if request.provider:
            provider = self.providers.get(request.provider)
            if provider is None:
                raise ValueError(
                    f"Unknown provider {request.provider!r}; registered: "
                    f"{sorted(self.providers)}.",
                )
            return provider
        # Deterministic default: first provider by name (sorted so
        # ordering differences never matter).
        return self.providers[sorted(self.providers)[0]]

    def available_providers(self) -> tuple[str, ...]:
        return tuple(sorted(self.providers))

    # ------------------------------------------------------------
    # VALIDATION HELPERS
    # ------------------------------------------------------------

    def validate_historical(
        self,
        records: Sequence[object],
        *,
        instrument: str,
        timeframe: str,
        reference_now: datetime | None = None,
        allow_future: bool = False,
    ) -> tuple[tuple[OHLCVCandle, ...], tuple[HistoricalDataIssue, ...]]:
        """Explicit validation of raw records (no provider call)."""

        return HistoricalDataValidator.validate(
            records,
            instrument=instrument,
            timeframe=timeframe,
            reference_now=reference_now or datetime.now(UTC),
            allow_future=allow_future,
        )

    # ------------------------------------------------------------
    # FETCH
    # ------------------------------------------------------------

    def fetch_historical(
        self,
        request: HistoricalDataRequest,
        *,
        reference_now: datetime | None = None,
    ) -> HistoricalFetchResult:
        """
        Fetch + normalize + validate + gap-detect + provenance.

        The result is deterministic given the same inputs. Request-level
        validation failures (unsupported instrument / timeframe / future
        end) are reported INVALID with an explicit issue — never raised.
        """

        now = reference_now or datetime.now(UTC)
        instrument = request.instrument.strip().upper()
        issues: list[HistoricalDataIssue] = []

        # Validate the request identity (universe + timeframe) first.
        if instrument not in self.universe:
            issues.append(
                HistoricalDataIssue(
                    error=HistoricalDataError.UNSUPPORTED_INSTRUMENT,
                    reason=(
                        f"{instrument!r} is not in the research universe; "
                        "request rejected."
                    ),
                    instrument=instrument,
                    timeframe=request.timeframe,
                ),
            )
        canonical = canonical_timeframe(request.timeframe)
        if canonical is None:
            canonical = request.timeframe
            issues.append(
                HistoricalDataIssue(
                    error=HistoricalDataError.UNSUPPORTED_TIMEFRAME,
                    reason=(
                        f"timeframe {request.timeframe!r} is not supported; "
                        "request rejected."
                    ),
                    instrument=instrument,
                    timeframe=request.timeframe,
                ),
            )

        provider = self._provider_for(request)

        # Future end rejection (production ingestion never requests the
        # future). Controlled imports must opt in via allow_future_end.
        if not request.allow_future_end and request.end > now:
            issues.append(
                HistoricalDataIssue(
                    error=HistoricalDataError.INVALID_RANGE,
                    reason=(
                        "request end is in the future relative to the "
                        "reference now; the production ingestion path never "
                        "requests future market data."
                    ),
                    instrument=instrument,
                    timeframe=canonical,
                ),
            )

        if issues:
            status = HistoricalIngestionStatus.INVALID
            provenance = HistoricalProvenance(
                provider=provider.provider_name,
                instrument=instrument,
                timeframe=canonical,
                requested_start=request.start,
                requested_end=request.end,
                actual_first_candle=None,
                actual_last_candle=None,
                ingestion_timestamp=now,
                records_received=0,
                records_accepted=0,
                records_rejected=0,
                status=status,
                reason="request rejected: " + issues[0].reason,
            )
            return HistoricalFetchResult(
                request=request,
                status=status,
                candles=(),
                issues=tuple(issues),
                gaps=(),
                provenance=provenance,
            )

        if not provider.is_available():
            provenance = HistoricalProvenance(
                provider=provider.provider_name,
                instrument=instrument,
                timeframe=canonical,
                requested_start=request.start,
                requested_end=request.end,
                actual_first_candle=None,
                actual_last_candle=None,
                ingestion_timestamp=now,
                records_received=0,
                records_accepted=0,
                records_rejected=0,
                status=HistoricalIngestionStatus.ERROR,
                reason="provider is not available.",
            )
            return HistoricalFetchResult(
                request=request,
                status=HistoricalIngestionStatus.ERROR,
                candles=(),
                issues=(
                    HistoricalDataIssue(
                        error=HistoricalDataError.PROVIDER_ERROR,
                        reason="provider is not available.",
                        instrument=instrument,
                        timeframe=canonical,
                    ),
                ),
                gaps=(),
                provenance=provenance,
            )
        if not provider.supports(instrument, canonical):
            provenance = HistoricalProvenance(
                provider=provider.provider_name,
                instrument=instrument,
                timeframe=canonical,
                requested_start=request.start,
                requested_end=request.end,
                actual_first_candle=None,
                actual_last_candle=None,
                ingestion_timestamp=now,
                records_received=0,
                records_accepted=0,
                records_rejected=0,
                status=HistoricalIngestionStatus.INVALID,
                reason="provider does not support the requested instrument / timeframe.",
            )
            return HistoricalFetchResult(
                request=request,
                status=HistoricalIngestionStatus.INVALID,
                candles=(),
                issues=(
                    HistoricalDataIssue(
                        error=HistoricalDataError.UNSUPPORTED_TIMEFRAME,
                        reason=(
                            f"provider {provider.provider_name} does not "
                            f"support {instrument}/{canonical}."
                        ),
                        instrument=instrument,
                        timeframe=canonical,
                    ),
                ),
                gaps=(),
                provenance=provenance,
            )

        try:
            response = provider.fetch(request)
        except Exception as exc:
            provenance = HistoricalProvenance(
                provider=provider.provider_name,
                instrument=instrument,
                timeframe=canonical,
                requested_start=request.start,
                requested_end=request.end,
                actual_first_candle=None,
                actual_last_candle=None,
                ingestion_timestamp=now,
                records_received=0,
                records_accepted=0,
                records_rejected=0,
                status=HistoricalIngestionStatus.ERROR,
                reason=f"provider error: {exc}",
            )
            return HistoricalFetchResult(
                request=request,
                status=HistoricalIngestionStatus.ERROR,
                candles=(),
                issues=(
                    HistoricalDataIssue(
                        error=HistoricalDataError.PROVIDER_ERROR,
                        reason=f"provider error: {exc}",
                        instrument=instrument,
                        timeframe=canonical,
                    ),
                ),
                gaps=(),
                provenance=provenance,
            )

        if response.status is ProviderResponseStatus.UNSUPPORTED:
            provenance = HistoricalProvenance(
                provider=provider.provider_name,
                instrument=instrument,
                timeframe=canonical,
                requested_start=request.start,
                requested_end=request.end,
                actual_first_candle=None,
                actual_last_candle=None,
                ingestion_timestamp=now,
                records_received=0,
                records_accepted=0,
                records_rejected=0,
                status=HistoricalIngestionStatus.ERROR,
                reason=response.reason or "provider unsupported.",
            )
            return HistoricalFetchResult(
                request=request,
                status=HistoricalIngestionStatus.ERROR,
                candles=(),
                issues=(
                    HistoricalDataIssue(
                        error=HistoricalDataError.UNSUPPORTED_TIMEFRAME,
                        reason=response.reason or "provider unsupported.",
                        instrument=instrument,
                        timeframe=canonical,
                    ),
                ),
                gaps=(),
                provenance=provenance,
            )

        raw = list(response.candles)
        received = len(raw)
        if received == 0:
            provenance = HistoricalProvenance(
                provider=provider.provider_name,
                instrument=instrument,
                timeframe=canonical,
                requested_start=request.start,
                requested_end=request.end,
                actual_first_candle=None,
                actual_last_candle=None,
                ingestion_timestamp=now,
                records_received=0,
                records_accepted=0,
                records_rejected=0,
                status=HistoricalIngestionStatus.EMPTY,
                reason=(
                    response.reason
                    if isinstance(response, HistoricalProviderResponse) and response.reason
                    else "provider returned an empty response."
                ),
            )
            empty_issue = HistoricalDataValidator.empty_issue(instrument, canonical)
            return HistoricalFetchResult(
                request=request,
                status=HistoricalIngestionStatus.EMPTY,
                candles=(),
                issues=(empty_issue,),
                gaps=(),
                provenance=provenance,
            )

        accepted, validation_issues = self.validate_historical(
            raw,
            instrument=instrument,
            timeframe=canonical,
            reference_now=now,
            allow_future=request.allow_future_end,
        )
        issues.extend(validation_issues)

        rejected = received - len(accepted)
        if accepted:
            status = (
                HistoricalIngestionStatus.AVAILABLE
                if rejected == 0
                else HistoricalIngestionStatus.PARTIAL
            )
        else:
            status = HistoricalIngestionStatus.INVALID

        gaps = detect_gaps(accepted, canonical, self.gap_config)
        provenance = HistoricalProvenance(
            provider=provider.provider_name,
            instrument=instrument,
            timeframe=canonical,
            requested_start=request.start,
            requested_end=request.end,
            actual_first_candle=accepted[0].timestamp if accepted else None,
            actual_last_candle=accepted[-1].timestamp if accepted else None,
            ingestion_timestamp=now,
            records_received=received,
            records_accepted=len(accepted),
            records_rejected=rejected,
            status=status,
            reason=(
                ""
                if status is HistoricalIngestionStatus.AVAILABLE
                else "partial data: some records rejected by validation."
            ),
        )
        return HistoricalFetchResult(
            request=request,
            status=status,
            candles=accepted,
            issues=tuple(issues),
            gaps=gaps,
            provenance=provenance,
        )

    # ------------------------------------------------------------
    # STORE
    # ------------------------------------------------------------

    def store_historical(
        self,
        fetch: HistoricalFetchResult,
        *,
        overwrite: bool = False,
    ) -> HistoricalStoreResult:
        """
        Persist a fetch result idempotently.

        Returns the store accounting. Empty results still record
        provenance (so the operation is auditable) but write no candles.
        """

        if self.store is None:
            raise HistoricalStoreError(
                "No historical store configured; cannot persist.",
            )
        added, existing, total, path = self.store.store(
            fetch.provenance.instrument,
            fetch.provenance.timeframe,
            fetch.candles,
            fetch.provenance,
            overwrite=overwrite,
        )
        return HistoricalStoreResult(
            instrument=fetch.provenance.instrument,
            timeframe=fetch.provenance.timeframe,
            records_added=added,
            records_existing=existing,
            total_candles=total,
            path=str(path),
        )

    def ingest(
        self,
        request: HistoricalDataRequest,
        *,
        reference_now: datetime | None = None,
        overwrite: bool = False,
    ) -> HistoricalIngestResult:
        """fetch + validate + store combined (the ingestion workflow)."""

        fetch = self.fetch_historical(request, reference_now=reference_now)
        store_result: HistoricalStoreResult | None = None
        # AVAILABLE / PARTIAL / EMPTY operations are persisted (EMPTY writes
        # an empty dataset file + its provenance line, so the operation is
        # auditable). INVALID / ERROR operations are never stored.
        if fetch.status in (
            HistoricalIngestionStatus.AVAILABLE,
            HistoricalIngestionStatus.PARTIAL,
            HistoricalIngestionStatus.EMPTY,
        ):
            store_result = self.store_historical(fetch, overwrite=overwrite)
        return HistoricalIngestResult(fetch=fetch, store=store_result)

    # ------------------------------------------------------------
    # LOAD
    # ------------------------------------------------------------

    def load_historical(
        self,
        instrument: str,
        timeframe: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        evaluation_time: datetime | None = None,
    ) -> HistoricalDatasetSlice:
        """
        Load a stored dataset within an optional window and an optional
        evaluation boundary.

        LOOK-AHEAD PROTECTION: when ``evaluation_time`` is set, only
        candles with ``timestamp <= evaluation_time`` are returned. The
        stored series is never mutated.
        """

        if self.store is None:
            raise HistoricalStoreError(
                "No historical store configured; cannot load.",
            )
        stored = self.store.load_candles(instrument, timeframe)
        boundary = evaluation_time
        filtered: list[OHLCVCandle] = []
        for candle in stored:
            if boundary is not None and candle.timestamp > boundary:
                continue
            if start is not None and candle.timestamp < start:
                continue
            if end is not None and candle.timestamp > end:
                continue
            filtered.append(candle)
        return HistoricalDatasetSlice(
            instrument=instrument,
            timeframe=canonical_timeframe(timeframe) or timeframe,
            candles=tuple(filtered),
            first_timestamp=filtered[0].timestamp if filtered else None,
            last_timestamp=filtered[-1].timestamp if filtered else None,
            count=len(filtered),
            source_count=len(stored),
            evaluation_time=evaluation_time,
        )


__all__ = ["HistoricalMarketDataService", "HistoricalStoreError"]
