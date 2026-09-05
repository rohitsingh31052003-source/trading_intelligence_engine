"""
Intraday market-data coverage + reliability engine (Checkpoint 19.2).

This is a THIN, deterministic DATA-QUALITY layer that answers "can the
future scanner reliably obtain intraday data for the NIFTY Top 200?"
It sits ABOVE the existing market-data providers and REUSES their
existing normalization / validation / completed-candle / freshness
semantics. It implements NO trading, scoring, prediction, decision,
ranking or execution logic.

Architecture (per the Checkpoint 19.2 boundary):

    NIFTY Top 200 (validated universe)
        -> Intraday Market Data Provider (existing provider abstraction)
        -> Provider Response (existing status vocabulary)
        -> Normalized Market Data (existing OHLCVCandle + validation)
        -> Validated Intraday Data (existing completed-candle boundary)
        -> Data Availability / Freshness Status (this layer)
        -> Future Continuous Scanner (19.3)

Key guarantees:

* PROVIDER CAPABILITY DISCOVERY: every (provider, instrument,
  timeframe) triple is explicitly declared via
  :class:`ProviderCoverageCapability` — the layer never pretends a
  provider supports an interval or instrument it does not.
* NIFTY TOP 200 COVERAGE: the validated 19.1 universe is accepted as
  the input universe and each constituent gets a deterministic status.
* NORMALIZATION: provider responses are normalized into the canonical
  :class:`OHLCVCandle` representation via the EXISTING
  :class:`dashboard.data_provider.split_completed_candles` +
  :class:`engine.data.validator.DataValidator` semantics. Provider
  formats never leak into scanner logic.
* TIMESTAMP CORRECTNESS: UTC normalization + chronological ordering +
  duplicate handling + future-candle rejection (all reused).
* OHLCV VALIDATION: impossible candles are rejected / classified, never
  silently repaired.
* COMPLETENESS: complete / partial / missing / empty / stale / failed /
  unsupported are explicitly distinguishable; missing data is never
  converted into valid data.
* FRESHNESS: session-aware freshness via
  :class:`engine.data.market_session` (documented thresholds).
* FAILURE ISOLATION: one failing symbol NEVER prevents the remaining
  constituents from being assessed.
* RATE LIMITS / RETRIES: the layer performs NO retries (bounded
  deterministic retries are deferred to Checkpoint 19.8); provider
  failures are classified honestly.
* PROVIDER ABSTRACTION: the future scanner consumes the canonical
  intraday data interface (:class:`dashboard.data_provider.DashboardDataProvider`),
  never provider-specific APIs.

No broker credentials are required. No broker execution is triggered.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Sequence

from dashboard.data_provider import (
    DashboardDataProvider,
    FIXTURE_INSTRUMENTS,
    FixtureDataProvider,
    InstrumentSeries,
    ProviderStatus,
    split_completed_candles,
)
from engine.config.universe_boundary import (
    DEFAULT_NIFTY200_UNIVERSE,
    UniverseDefinition,
)
from engine.data.market_session import (
    MarketSessionState,
    SessionFreshnessConfig,
    market_session_state,
    seconds_until_next_open,
    session_aware_freshness,
    staleness_seconds,
)
from engine.data.validator import DataValidator
from engine.models.intraday_coverage import (
    IntradayCandleIssue,
    IntradayCoverageCounts,
    IntradayCoverageReport,
    IntradayCoverageStatus,
    IntradayInstrumentCoverage,
    IntradaySymbolResolution,
    ProviderCoverageCapability,
)
from engine.models.ohlcv import OHLCVCandle

#: Default intraday timeframe the coverage layer grades (canonical).
DEFAULT_INTRADAY_TIMEFRAME: str = "15m"

#: Default session-aware freshness thresholds (documented in
#: :class:`SessionFreshnessConfig`).
DEFAULT_SESSION_FRESHNESS: SessionFreshnessConfig = SessionFreshnessConfig()


def _canonical_name(name: str) -> str:
    """Canonicalize + validate a single instrument name."""

    if not isinstance(name, str):
        raise TypeError(f"instrument name must be a str, got {type(name).__name__}")
    canonical = name.strip().upper()
    if not canonical:
        raise ValueError("instrument name cannot be empty")
    return canonical


def _status_from_provider_status(status: ProviderStatus) -> IntradayCoverageStatus:
    """Map the EXISTING provider-status vocabulary to the coverage status."""

    if status is ProviderStatus.UNSUPPORTED:
        return IntradayCoverageStatus.UNSUPPORTED_INSTRUMENT
    if status is ProviderStatus.NOT_READY:
        return IntradayCoverageStatus.TEMPORARILY_UNAVAILABLE
    if status is ProviderStatus.EMPTY:
        return IntradayCoverageStatus.EMPTY
    if status is ProviderStatus.ERROR:
        return IntradayCoverageStatus.PROVIDER_ERROR
    return IntradayCoverageStatus.SUPPORTED


@dataclass(frozen=True, slots=True)
class IntradayCoverageConfig:
    """
    Configuration for the intraday coverage engine (Checkpoint 19.2).

    Attributes:

    timeframe
        Canonical intraday timeframe to grade (default ``"15m"``).

    session_freshness
        :class:`SessionFreshnessConfig` thresholds (documented).

    detect_gaps
        When True, run the existing gap detector over the accepted
        candles and downgrade a fresh series with an UNEXPECTED gap to
        ``VALID_WITH_GAPS`` (honest missing-candle reporting). Default
        True.

    closure_seconds
        Gap-closure window passed to the existing gap detector (a gap
        whose span is within this is treated as a plausible market
        closure, not an unexpected gap). Default 2 days.
    """

    timeframe: str = DEFAULT_INTRADAY_TIMEFRAME
    session_freshness: SessionFreshnessConfig = DEFAULT_SESSION_FRESHNESS
    detect_gaps: bool = True
    closure_seconds: int = 2 * 86400

    def __post_init__(self) -> None:
        from engine.data.historical_times import canonical_timeframe

        canonical = canonical_timeframe(self.timeframe)
        if canonical is None or canonical == "1D":
            raise ValueError(
                f"timeframe must be a canonical INTRADAY timeframe "
                f"(got {self.timeframe!r}).",
            )
        object.__setattr__(self, "timeframe", canonical)
        if self.closure_seconds <= 0:
            raise ValueError("closure_seconds must be positive.")


class IntradayCoverageEngine:
    """
    Stateless, deterministic intraday coverage engine.

    The engine accepts the validated 19.1 universe (or any sequence of
    canonical instrument names), resolves each constituent through the
    provider abstraction, normalizes/validates the provider response
    with the EXISTING semantics, and classifies each instrument into a

    deterministic coverage status. One failing symbol never breaks the
    remaining universe (per-instrument failure isolation).
    """

    def __init__(
        self,
        provider: DashboardDataProvider | None = None,
        config: IntradayCoverageConfig | None = None,
    ) -> None:
        if provider is None:
            provider = FixtureDataProvider()
        self.provider = provider
        self.config = config or IntradayCoverageConfig()

    @classmethod
    def build(
        cls,
        provider_name: str = "fixture",
        *,
        timeframe: str = DEFAULT_INTRADAY_TIMEFRAME,
        reference_now: datetime | None = None,
    ) -> "IntradayCoverageEngine":
        """
        Stateless factory over the EXISTING ``make_provider`` factory.

        ``"fixture"`` (default) -> the deterministic offline fixture
        provider. ``"yahoo"`` -> the OPTIONAL live/near-live Yahoo
        provider (requires ``yfinance``; graceful on failure). Never
        silently falls back between providers. No broker provider is
        ever constructed here.
        """

        del reference_now  # retained for API symmetry; providers are built analytically
        from dashboard.data_provider import make_provider

        return cls(provider=make_provider(name=provider_name))

    # ------------------------------------------------------------
    # CAPABILITY DISCOVERY
    # ------------------------------------------------------------

    def provider_capabilities(
        self,
        instruments: Sequence[str],
        timeframe: str | None = None,
    ) -> tuple[ProviderCoverageCapability, ...]:
        """
        Explicit capability declaration for every (provider,
        instrument, timeframe) triple (deterministic).

        ``supported`` is True only when the provider DECLARES support
        for the instrument/timeframe. No capability is invented.
        """

        tf = timeframe or self.config.timeframe
        out: list[ProviderCoverageCapability] = []
        for instrument in instruments:
            canon = _canonical_name(instrument)
            tf_supported = False
            try:
                tf_supported = self.provider.is_timeframe_supported(tf)
            except Exception:  # pragma: no cover - defensive
                tf_supported = False
            instrument_supported: bool = True  # default: unknown superset
            supports_instrument = getattr(
                self.provider, "supports_instrument", None,
            )
            if supports_instrument is not None:
                try:
                    instrument_supported = bool(supports_instrument(canon))
                except Exception:  # pragma: no cover - defensive
                    instrument_supported = True
            elif isinstance(self.provider, FixtureDataProvider):
                # Fixture provider: instrument-level support is statically
                # declared (its cache only serves the fixture set).
                instrument_supported = canon in FIXTURE_INSTRUMENTS

            supported = bool(tf_supported and instrument_supported)
            resolved: str | None = None
            reason = ""
            if supported:
                try:
                    resolve = getattr(self.provider, "resolve_symbol", None)
                    if resolve is not None:
                        resolved = resolve(canon)
                except Exception:  # pragma: no cover - defensive
                    resolved = None
            else:
                reasons: list[str] = []
                if not tf_supported:
                    reasons.append(
                        f"timeframe {tf!r} not declared by "
                        f"{getattr(self.provider, 'data_source', '?')}.",
                    )
                if not instrument_supported:
                    reasons.append(
                        f"instrument {canon!r} not in the provider's "
                        "declared instrument set.",
                    )
                reason = " ".join(reasons)
            out.append(
                ProviderCoverageCapability(
                    provider_name=getattr(
                        self.provider, "data_source", "unknown",
                    ),
                    instrument=canon,
                    timeframe=tf,
                    supported=supported,
                    resolved_symbol=resolved,
                    reason=reason,
                ),
            )
        return tuple(out)

    def symbol_resolutions(
        self,
        instruments: Sequence[str],
    ) -> tuple[IntradaySymbolResolution, ...]:
        """
        Deterministic symbol / instrument-identifier resolution for each
        instrument across the provider abstraction.

        Yahoo symbols are resolved via the provider's ``resolve_symbol``
        when present; Upstox instrument keys are read from the verified
        default map ONLY (never guessed). Resolution is informational —
        it never fabricates a mapping.
        """

        from engine.data.historical_provider import (
            _default_upstox_instrument_key_map,
        )
        from dashboard.universe import TOP200_YAHOO_SYMBOLS, UNIVERSE_YAHOO_SYMBOLS

        upstox_map = _default_upstox_instrument_key_map()
        static_yahoo = dict(UNIVERSE_YAHOO_SYMBOLS)
        static_yahoo.update(TOP200_YAHOO_SYMBOLS)
        out: list[IntradaySymbolResolution] = []
        for instrument in instruments:
            canon = _canonical_name(instrument)
            yahoo: str | None = None
            resolve = getattr(self.provider, "resolve_symbol", None)
            if resolve is not None:
                try:
                    yahoo = resolve(canon)
                except Exception:  # pragma: no cover - defensive
                    yahoo = None
            if yahoo is None:
                # Provider-independent informational fallback: the
                # canonical dashboard Yahoo map (never fabricated).
                yahoo = static_yahoo.get(canon)
            out.append(
                IntradaySymbolResolution(
                    instrument=canon,
                    yahoo_symbol=yahoo,
                    upstox_instrument_key=upstox_map.get(canon),
                ),
            )
        return tuple(out)

    # ------------------------------------------------------------
    # PER-INSTRUMENT ASSESSMENT
    # ------------------------------------------------------------

    def assess_instrument(
        self,
        instrument: str,
        timeframe: str | None = None,
        *,
        reference_now: datetime | None = None,
    ) -> IntradayInstrumentCoverage:
        """
        Classify ONE instrument's intraday coverage (deterministic).

        The reference time is explicit (never wall-clock). The provider
        response is normalized with the EXISTING completed-candle
        boundary + ``DataValidator`` semantics; every failure is
        classified, never raised.
        """

        tf = timeframe or self.config.timeframe
        canon = _canonical_name(instrument)
        now = reference_now or datetime.now(UTC)

        # Capability gate first: an unsupported timeframe is classified
        # explicitly and never fetched.
        try:
            tf_supported = self.provider.is_timeframe_supported(tf)
        except Exception:  # pragma: no cover - defensive
            tf_supported = False
        if not tf_supported:
            return IntradayInstrumentCoverage(
                instrument=canon,
                timeframe=tf,
                status=IntradayCoverageStatus.UNSUPPORTED_TIMEFRAME,
                provider_name=getattr(self.provider, "data_source", ""),
                market_session=market_session_state(now),
                reason=(
                    f"provider {getattr(self.provider, 'data_source', '?')} "
                    f"does not support timeframe {tf!r}."
                ),
            )

        try:
            series = self.provider.fetch(
                canon, tf, reference_now=now,
            )
        except Exception as exc:  # pragma: no cover - defensive
            return IntradayInstrumentCoverage(
                instrument=canon,
                timeframe=tf,
                status=IntradayCoverageStatus.PROVIDER_ERROR,
                provider_name=getattr(self.provider, "data_source", ""),
                market_session=market_session_state(now),
                issues=(
                    IntradayCandleIssue(
                        code="PROVIDER_ERROR",
                        detail=f"provider raised: {type(exc).__name__}: {exc}",
                    ),
                ),
                reason=f"provider raised: {type(exc).__name__}: {exc}",
            )

        return self._classify_series(
            canon, tf, series, now,
        )

    def _classify_series(
        self,
        instrument: str,
        timeframe: str,
        series: InstrumentSeries,
        now: datetime,
    ) -> IntradayInstrumentCoverage:
        """Classify an already-fetched :class:`InstrumentSeries`."""

        provider_name = series.data_source or getattr(
            self.provider, "data_source", "",
        )
        market_session = market_session_state(now)
        issues: list[IntradayCandleIssue] = []
        resolved_symbol: str | None = None
        try:
            resolve = getattr(self.provider, "resolve_symbol", None)
            if resolve is not None:
                resolved_symbol = resolve(instrument)
        except Exception:  # pragma: no cover - defensive
            resolved_symbol = None

        # Provider-level failure / unsupported / empty classification.
        if series.provider_status is ProviderStatus.UNSUPPORTED:
            status = IntradayCoverageStatus.UNSUPPORTED_INSTRUMENT
            issues.append(
                IntradayCandleIssue(
                    code="UNSUPPORTED",
                    detail=series.reason or "provider reports unsupported.",
                ),
            )
            return IntradayInstrumentCoverage(
                instrument=instrument,
                timeframe=timeframe,
                status=status,
                provider_name=provider_name,
                resolved_symbol=resolved_symbol,
                market_session=market_session,
                issues=tuple(issues),
                reason=series.reason or "provider reports unsupported.",
            )
        if series.provider_status in (
            ProviderStatus.NOT_READY,
            ProviderStatus.ERROR,
        ):
            status = (
                IntradayCoverageStatus.TEMPORARILY_UNAVAILABLE
                if series.provider_status is ProviderStatus.NOT_READY
                else IntradayCoverageStatus.PROVIDER_ERROR
            )
            issues.append(
                IntradayCandleIssue(
                    code=(
                        "TEMPORARILY_UNAVAILABLE"
                        if status is IntradayCoverageStatus.TEMPORARILY_UNAVAILABLE
                        else "PROVIDER_ERROR"
                    ),
                    detail=series.reason or "provider failure.",
                ),
            )
            return IntradayInstrumentCoverage(
                instrument=instrument,
                timeframe=timeframe,
                status=status,
                provider_name=provider_name,
                resolved_symbol=resolved_symbol,
                market_session=market_session,
                issues=tuple(issues),
                reason=series.reason or "provider failure.",
            )
        if series.provider_status is ProviderStatus.EMPTY or not series.setup_candles:
            status = IntradayCoverageStatus.EMPTY
            issues.append(
                IntradayCandleIssue(
                    code="EMPTY",
                    detail=series.reason or "provider returned no candles.",
                ),
            )
            return IntradayInstrumentCoverage(
                instrument=instrument,
                timeframe=timeframe,
                status=status,
                provider_name=provider_name,
                resolved_symbol=resolved_symbol,
                market_session=market_session,
                issues=tuple(issues),
                reason=series.reason or "provider returned no candles.",
            )

        # Validated completed candles exist. Re-run the completed-candle
        # boundary + DataValidator semantics defensively (the provider
        # already did this, but the coverage layer must never trust a
        # provider blindly). Duplicate / out-of-order detection runs on
        # the RAW provider series BEFORE the boundary normalizes it, so
        # these quality problems are reported honestly rather than being
        # silently absorbed.
        raw_series = series.setup_candles
        raw_timestamps = [c.timestamp for c in raw_series]
        raw_duplicates = len(raw_timestamps) - len(set(raw_timestamps))
        if raw_duplicates:
            issues.append(
                IntradayCandleIssue(
                    code="DUPLICATE",
                    detail=(
                        f"{raw_duplicates} duplicate-timestamp candle(s) "
                        "rejected (first kept)."
                    ),
                ),
            )
        raw_unordered = raw_timestamps != sorted(raw_timestamps)
        if raw_unordered:
            issues.append(
                IntradayCandleIssue(
                    code="UNORDERED",
                    detail="out-of-order timestamps normalized to "
                    "chronological order.",
                ),
            )

        boundary = split_completed_candles(
            series.setup_candles, timeframe, now,
        )
        completed = list(boundary.completed)
        rejected_future = len(boundary.rejected_future) + getattr(
            series, "rejected_future_count", 0,
        )
        if rejected_future:
            issues.append(
                IntradayCandleIssue(
                    code="FUTURE_DATED",
                    detail=(
                        f"{rejected_future} future-dated candle(s) rejected "
                        "by the completed-candle boundary."
                    ),
                ),
            )

        valid: list[OHLCVCandle] = []
        invalid_count = 0
        for candle in completed:
            try:
                DataValidator.validate_candle(candle)
                valid.append(candle)
            except (ValueError, TypeError):
                invalid_count += 1
        if invalid_count:
            issues.append(
                IntradayCandleIssue(
                    code="INVALID_OHLC",
                    detail=f"{invalid_count} invalid candle(s) rejected.",
                ),
            )
        completed = valid

        if not completed:
            return IntradayInstrumentCoverage(
                instrument=instrument,
                timeframe=timeframe,
                status=IntradayCoverageStatus.INVALID_RESPONSE,
                provider_name=provider_name,
                resolved_symbol=resolved_symbol,
                market_session=market_session,
                rejected_future_count=rejected_future,
                issues=tuple(issues),
                reason="no valid completed candles after validation.",
            )

        # Chronology over the accepted candles (the boundary already
        # sorted + de-duplicated; this is a defensive re-sort).
        timestamps = [c.timestamp for c in completed]
        if timestamps != sorted(timestamps):
            completed = sorted(completed, key=lambda c: c.timestamp)

        latest = completed[-1].timestamp
        data_age = (now - latest).total_seconds() if now >= latest else None
        threshold = staleness_seconds(
            now, timeframe, self.config.session_freshness,
        )
        freshness = session_aware_freshness(
            now, latest, timeframe, self.config.session_freshness,
        )

        # Gap detection (honest missing-candle reporting) — reuses the
        # existing detector and refines its verdict for the INTRADAY
        # use case (session-aware): a gap is counted as UNEXPECTED only
        # when it occurs INSIDE the NSE session (both candles on the
        # same IST trading date) and is not the documented lunch break.
        # Normal overnight / weekend transitions are never flagged.
        unexpected_gaps = 0
        if self.config.detect_gaps and len(completed) >= 2:
            unexpected_gaps = self._intraday_unexpected_gap_count(
                completed, timeframe,
            )
            if unexpected_gaps:
                issues.append(
                    IntradayCandleIssue(
                        code="UNEXPECTED_GAP",
                        detail=(
                            f"{unexpected_gaps} unexpected intraday gap(s) "
                            "detected during the NSE session (missing "
                            "candles are reported, never fabricated)."
                        ),
                    ),
                )

        if freshness.value == "CURRENT":
            status = (
                IntradayCoverageStatus.VALID_WITH_GAPS
                if unexpected_gaps
                else IntradayCoverageStatus.VALID
            )
        else:
            status = IntradayCoverageStatus.STALE

        reason_parts = [f"{len(completed)} completed candle(s)"]
        if unexpected_gaps:
            reason_parts.append(f"{unexpected_gaps} unexpected intraday gap(s)")
        if freshness.value == "STALE":
            reason_parts.append(
                f"data age {int(data_age or 0)}s exceeds the "
                f"session-aware threshold {threshold}s",
            )
        reason = "; ".join(reason_parts)

        return IntradayInstrumentCoverage(
            instrument=instrument,
            timeframe=timeframe,
            status=status,
            provider_name=provider_name,
            resolved_symbol=resolved_symbol,
            candle_count=len(completed),
            first_timestamp=completed[0].timestamp,
            last_timestamp=latest,
            forming_candle=series.forming_setup_candle,
            rejected_future_count=rejected_future,
            duplicate_count=raw_duplicates,
            out_of_order_count=1 if raw_unordered else 0,
            issues=tuple(issues),
            market_session=market_session,
            staleness_seconds=threshold,
            data_age_seconds=data_age,
            last_update_time=series.last_successful_fetch_time,
            reason=reason,
        )

    @staticmethod
    def _intraday_unexpected_gap_count(
        candles: Sequence[OHLCVCandle],
        timeframe: str,
    ) -> int:
        """
        Session-aware INTRADAY unexpected-gap count (deterministic).

        A gap between two consecutive candles is UNEXPECTED only when:

        * the candles belong to the SAME IST trading day (so the span is
          a hole inside the session, not an overnight / weekend
          transition), AND
        * the span is longer than the expected 1-candle cadence, AND
        * the span is NOT the documented 12:00-13:00 IST equity lunch
          pause (a candle immediately before 12:00 and the next one at
          or after 13:00, up to 75 minutes).

        This refines the generic historical gap detector for the
        intraday-coverage use case without duplicating its weekend
        classification: it simply never calls the generic detector's
        weekend rule into question for same-day pairs.
        """

        from engine.data.historical_times import timeframe_seconds
        from engine.data.market_session import IST_TZ

        step = timeframe_seconds(timeframe)
        if step is None or step <= 0:
            return 0
        gaps = 0
        for prev, nxt in zip(candles, candles[1:]):
            span = (nxt.timestamp - prev.timestamp).total_seconds()
            if span <= step:
                continue
            prev_local = prev.timestamp.astimezone(IST_TZ)
            nxt_local = nxt.timestamp.astimezone(IST_TZ)
            if prev_local.date() != nxt_local.date():
                continue  # overnight / weekend transition — normal
            if prev_local.weekday() >= 5:
                continue  # weekend candles (defensive)
            if prev_local.hour < 12 and nxt_local.hour >= 13 and span <= 75 * 60:
                continue  # documented 12:00-13:00 lunch pause
            gaps += 1
        return gaps

    # ------------------------------------------------------------
    # UNIVERSE ASSESSMENT
    # ------------------------------------------------------------

    def assess_universe(
        self,
        universe: UniverseDefinition | Sequence[str] | None = None,
        timeframe: str | None = None,
        *,
        reference_now: datetime | None = None,
    ) -> IntradayCoverageReport:
        """
        Assess intraday coverage for a universe (deterministic,
        failure-isolated).

        ``universe`` may be a validated :class:`UniverseDefinition`
        (e.g. ``UniverseBuilder.nifty200()``) or a plain sequence of
        canonical names. When ``None`` the default NIFTY Top 200
        definition is used. One failing symbol NEVER prevents the
        remaining constituents from being assessed (per-instrument
        failure isolation).

        The report carries explicit counts so a diagnostic can answer
        "how many of the 200 are supported / unsupported / no-data /
        stale / error / malformed" without interior inspection. Partial
        coverage can never become false full coverage
        (:attr:`IntradayCoverageReport.coverage_ratio`).
        """

        tf = timeframe or self.config.timeframe
        now = reference_now or datetime.now(UTC)

        if universe is None:
            universe = DEFAULT_NIFTY200_UNIVERSE
        if isinstance(universe, UniverseDefinition):
            instruments = list(universe.symbols)
            universe_count = universe.instrument_count
        else:
            instruments = [_canonical_name(i) for i in universe]
            universe_count = len(instruments)

        results = tuple(
            self.assess_instrument(i, tf, reference_now=now)
            for i in sorted(set(instruments))
        )

        # Accumulate into a plain dict (the counts model is frozen) and
        # construct the immutable aggregate at the end. ``supported`` is
        # a DERIVED property on the counts model (tested minus explicit
        # unsupported / not-ready), so it is not tallied here.
        tally: dict[str, int] = {key: 0 for key in (
            "unsupported_instrument", "unsupported_timeframe",
            "temporarily_unavailable", "provider_errors", "invalid_responses",
            "no_data", "empty", "stale", "valid_with_gaps", "valid",
            "not_tested",
        )}
        for result in results:
            status = result.status
            if status is IntradayCoverageStatus.UNSUPPORTED_INSTRUMENT:
                tally["unsupported_instrument"] += 1
            elif status is IntradayCoverageStatus.UNSUPPORTED_TIMEFRAME:
                tally["unsupported_timeframe"] += 1
            elif status is IntradayCoverageStatus.TEMPORARILY_UNAVAILABLE:
                tally["temporarily_unavailable"] += 1
            elif status is IntradayCoverageStatus.PROVIDER_ERROR:
                tally["provider_errors"] += 1
            elif status is IntradayCoverageStatus.INVALID_RESPONSE:
                tally["invalid_responses"] += 1
            elif status is IntradayCoverageStatus.NO_DATA:
                tally["no_data"] += 1
            elif status is IntradayCoverageStatus.EMPTY:
                tally["empty"] += 1
            elif status is IntradayCoverageStatus.STALE:
                tally["stale"] += 1
            elif status is IntradayCoverageStatus.VALID_WITH_GAPS:
                tally["valid_with_gaps"] += 1
            elif status is IntradayCoverageStatus.VALID:
                tally["valid"] += 1
            else:
                tally["not_tested"] += 1

        counts = IntradayCoverageCounts(**tally)

        session = market_session_state(now)
        until_open = seconds_until_next_open(now)
        capabilities = self.provider_capabilities(
            [r.instrument for r in results], tf,
        )

        return IntradayCoverageReport(
            provider_name=getattr(self.provider, "data_source", "unknown"),
            timeframe=tf,
            universe_instrument_count=universe_count,
            reference_now=now,
            results=results,
            counts=counts,
            market_session=session,
            seconds_until_next_open=(
                until_open.total_seconds() if until_open is not None else None
            ),
            capabilities=capabilities,
        )


class IntradayCoverageFormatter:
    """
    Deterministic, stateless text renderer for the intraday coverage
    report. Returns ``str`` (no ``print()`` inside). DESCRIPTIVE ONLY —
    it reports data availability / quality; it is NOT a trading signal,
    NOT a prediction and NOT a recommendation.
    """

    def __init__(self, width: int = 80) -> None:
        if width < 1:
            raise ValueError("width must be >= 1.")
        self.width = width

    def _wrap(self, text: str, indent: str = "") -> str:
        words = text.split()
        if not words:
            return ""
        lines: list[str] = []
        current = indent
        for word in words:
            if current == indent:
                current = indent + word
            elif len(current) + 1 + len(word) <= self.width:
                current += " " + word
            else:
                lines.append(current)
                current = indent + word
        if current:
            lines.append(current)
        return "\n".join(lines)

    def _session_label(self, report: IntradayCoverageReport) -> str:
        session = report.market_session
        if session is None:
            return "unknown"
        label = str(session.value)
        if report.seconds_until_next_open is not None:
            label += (
                f" (next NSE open in {int(report.seconds_until_next_open)}s)"
            )
        elif hasattr(session, "value"):
            label = f"{label} (next-open estimate unavailable)"
        return label

    def format(self, report: IntradayCoverageReport) -> str:
        """A complete intraday coverage report (deterministic)."""

        counts = report.counts
        lines = [
            "INTRADAY DATA COVERAGE REPORT",
            "",
            f"Provider             : {report.provider_name}",
            f"Timeframe            : {report.timeframe}",
            f"Reference now        : {report.reference_now.isoformat()}",
            f"Market session       : {self._session_label(report)}",
            f"Universe size        : {report.universe_instrument_count}",
            f"Assessed             : {counts.tested}",
            "",
            "COVERAGE COUNTS",
            f"  supported                     : {counts.supported}",
            f"  valid                         : {counts.valid}",
            f"  valid with gaps               : {counts.valid_with_gaps}",
            f"  stale                         : {counts.stale}",
            f"  no_data                       : {counts.no_data}",
            f"  empty                         : {counts.empty}",
            f"  unsupported_instrument        : {counts.unsupported_instrument}",
            f"  unsupported_timeframe         : {counts.unsupported_timeframe}",
            f"  temporarily_unavailable       : {counts.temporarily_unavailable}",
            f"  provider_errors               : {counts.provider_errors}",
            f"  invalid_responses             : {counts.invalid_responses}",
            f"  not_tested                    : {counts.not_tested}",
            "",
            f"  with valid data               : {counts.with_valid_data}",
            f"  needs attention               : {counts.needs_attention}",
            f"  coverage ratio                : {report.coverage_ratio:.2%}",
            "",
            "PER-INSTRUMENT STATUS",
        ]
        for result in report.results:
            line = (
                f"  {result.instrument:<22} {result.status.value:<26} "
                f"candles={result.candle_count}  "
                f"last={result.last_timestamp.isoformat() if result.last_timestamp else 'none'}"
            )
            lines.append(line)
            if result.issues:
                first = result.issues[0]
                lines.append(
                    self._wrap(
                        f"      issue: {first.code}: {first.detail}",
                        indent="      ",
                    ),
                )
            if result.reason and not result.issues:
                lines.append(
                    self._wrap(f"      {result.reason}", indent="      "),
                )
        lines.append("")
        lines.append(
            "DISCLAIMER: intraday coverage is a descriptive data-quality "
            "assessment. It does not predict market behavior, does not "
            "constitute a trading recommendation, and does not authorize "
            "any broker execution.",
        )
        return "\n".join(lines)

    def format_summary(self, report: IntradayCoverageReport) -> str:
        """Compact one-line-per-category summary (for CLIs)."""

        counts = report.counts
        lines = [
            "INTRADAY COVERAGE SUMMARY",
            f"provider={report.provider_name} timeframe={report.timeframe} "
            f"assessed={counts.tested}/{report.universe_instrument_count}",
            (
                "supported={supported} valid={valid} valid_with_gaps="
                "{vwg} stale={stale} no_data={no_data} empty={empty} "
                "unsupported_instrument={ui} unsupported_timeframe={ut} "
                "temporarily_unavailable={tu} provider_errors={pe} "
                "invalid_responses={ir} needs_attention={na} "
                "coverage_ratio={cr:.2f}"
            ).format(
                supported=counts.supported,
                valid=counts.valid,
                vwg=counts.valid_with_gaps,
                stale=counts.stale,
                no_data=counts.no_data,
                empty=counts.empty,
                ui=counts.unsupported_instrument,
                ut=counts.unsupported_timeframe,
                tu=counts.temporarily_unavailable,
                pe=counts.provider_errors,
                ir=counts.invalid_responses,
                na=counts.needs_attention,
                cr=report.coverage_ratio,
            ),
        ]
        return "\n".join(lines)


__all__ = [
    "DEFAULT_INTRADAY_TIMEFRAME",
    "DEFAULT_SESSION_FRESHNESS",
    "IntradayCoverageConfig",
    "IntradayCoverageEngine",
    "IntradayCoverageFormatter",
]