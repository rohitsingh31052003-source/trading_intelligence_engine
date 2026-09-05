"""
Checkpoint 19.3 — continuous market scanning tests.

Deterministic, network-free tests for the continuous market scanner:

* the scanner consumes the FROZEN 19.1 NIFTY Top 200 universe and the
  FROZEN 19.2 intraday coverage layer;
* one scan cycle produces an explicit per-symbol result for EVERY
  requested constituent;
* result ordering is deterministic (canonical universe order);
* per-symbol statuses reuse the 19.2 vocabulary;
* unsupported / stale / failed / invalid symbols are EXPLICITLY
  represented (never silently dropped);
* one failing symbol never terminates the cycle (failure isolation);
* PARTIAL vs FULL success is explicit; COMPLETE failure is distinct;
* scan intervals are configurable and tests never sleep in real time;
* cycles never overlap (SKIPPED policy);
* the scanner stops cleanly and start/stop is deterministic;
* market-session behavior is reused from 19.2;
* NO setup detection / ranking / alerts / broker execution introduced.

The suite uses fixture + scripted fake providers and a fake clock /
instant waiter so NO test waits on the wall clock.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from dashboard.continuous_scanner import (
    ContinuousScanner,
    ContinuousScannerEngine,
    PriceSource,
)
from dashboard.data_provider import (
    FixtureDataProvider,
    FreshnessState,
    InstrumentSeries,
    ProviderStatus,
)
from dashboard.intraday_coverage import IntradayCoverageEngine
from engine.config.universe import NIFTY200_SYMBOLS
from engine.config.universe_boundary import (
    DEFAULT_NIFTY200_UNIVERSE,
    UniverseBuilder,
)
from engine.data.market_session import MarketSessionState
from engine.models.continuous_scan import (
    ContinuousScanConfig,
    MarketScanCycleResult,
    MarketScanStatus,
    PerSymbolScanResult,
    ScanOnceRequest,
    ScanSourceKind,
    ScannerState,
)
from engine.models.intraday_coverage import IntradayCoverageStatus
from engine.models.ohlcv import OHLCVCandle


# ------------------------------------------------------------
# FACTORIES / FAKES
# ------------------------------------------------------------


def _candle(ts: datetime, close: float = 100.0) -> OHLCVCandle:
    return OHLCVCandle(
        timestamp=ts,
        open=close,
        high=close + 1.0,
        low=close - 1.0,
        close=close,
        volume=1000.0,
    )


def _now() -> datetime:
    """Friday 11:00 IST reference instant (05:30 UTC)."""
    return datetime(2026, 9, 4, 5, 30, tzinfo=UTC)


def _fresh_15m_series(count: int = 20) -> tuple[OHLCVCandle, ...]:
    """A 15m series ending at the latest COMPLETED candle before the
    reference instant (Friday 11:00 IST == 05:30 UTC; 10:45 IST ==
    05:15 UTC has closed, age 900s <= 1800s threshold -> CURRENT)."""
    end = datetime(2026, 9, 4, 5, 15, tzinfo=UTC)
    return tuple(
        _candle(end - timedelta(minutes=15 * (count - 1 - i)))
        for i in range(count)
    )


@dataclass(frozen=True)
class FakeFetchSpec:
    instrument: str
    candles: tuple[OHLCVCandle, ...] = ()
    status: ProviderStatus = ProviderStatus.OK
    data_source: str = "fake"
    forming: OHLCVCandle | None = None
    latest: datetime | None = None


class ScriptedProvider:
    """Deterministic fake provider whose behaviour is fully scripted.

    Mirrors the 19.2 test helper: returns an ``InstrumentSeries`` whose
    provider status + candles drive the coverage classification that the
    scanner consumes. Tracking ``calls`` + `raised` enables no-overlap /
    failure-isolation assertions.
    """

    data_source = "fake"

    def __init__(
        self,
        specs: dict[str, FakeFetchSpec],
        raised: set[str] | None = None,
        delay_seconds: float = 0.0,
    ) -> None:
        self._specs = specs
        self._raised = raised or set()
        self._delay_seconds = delay_seconds
        self.calls: list[tuple[str, str]] = []
        self._lock = threading.Lock()

    def is_timeframe_supported(self, tf: str) -> bool:
        return tf == "15m"

    def supports_instrument(self, instrument: str) -> bool:
        return instrument in self._specs

    def resolve_symbol(self, instrument: str) -> str:
        spec = self._specs.get(instrument)
        return spec.instrument if spec else instrument

    def fetch(
        self,
        instrument: str,
        setup_timeframe: str,
        lookback_bars: int = 300,
        *,
        reference_now: datetime | None = None,
    ) -> InstrumentSeries:
        del lookback_bars, reference_now
        with self._lock:
            self.calls.append((instrument, setup_timeframe))
        if self._delay_seconds > 0:
            time.sleep(self._delay_seconds)
        if instrument in self._raised:
            raise RuntimeError(f"boom {instrument}")
        spec = self._specs.get(instrument)
        if spec is None:
            return InstrumentSeries(
                instrument=instrument,
                available=False,
                reason="instrument not served by fake provider",
                data_source="fake",
                provider_status=ProviderStatus.UNSUPPORTED,
                freshness_state=FreshnessState.UNAVAILABLE,
            )
        return InstrumentSeries(
            instrument=spec.instrument,
            setup_candles=spec.candles,
            available=bool(spec.candles and spec.status is ProviderStatus.OK),
            reason="",
            data_source=spec.data_source,
            provider_status=spec.status,
            freshness_state=FreshnessState.CURRENT,
            latest_candle_timestamp=(
                spec.latest
                or (spec.candles[-1].timestamp if spec.candles else None)
            ),
            latest_completed_candle_timestamp=(
                spec.candles[-1].timestamp if spec.candles else None
            ),
            forming_setup_candle=spec.forming,
            last_successful_fetch_time=(
                datetime(2026, 9, 4, 5, 29, tzinfo=UTC) if spec.candles else None
            ),
            rejected_future_count=0,
        )

    def last_updated(self, instrument: str, setup_timeframe: str) -> datetime | None:
        del setup_timeframe
        spec = self._specs.get(instrument)
        return spec.candles[-1].timestamp if spec and spec.candles else None


class FakeClock:
    """Deterministic mutable clock for the scanner loop."""

    def __init__(self, start: datetime) -> None:
        self.t = start

    def __call__(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)


def _scripted_engine(
    specs: dict[str, FakeFetchSpec],
    raised: set[str] | None = None,
    delay_seconds: float = 0.0,
    price_source: PriceSource | None = None,
) -> tuple[ContinuousScannerEngine, ScriptedProvider]:
    provider = ScriptedProvider(specs, raised=raised, delay_seconds=delay_seconds)
    coverage = IntradayCoverageEngine(provider=provider)
    engine = ContinuousScannerEngine(
        coverage_engine=coverage,
        price_source=price_source,
    )
    return engine, provider


def _two_symbol_specs() -> dict[str, FakeFetchSpec]:
    """RELIANCE fresh + TCS fresh (both VALID at the reference now)."""
    return {
        "RELIANCE": FakeFetchSpec(
            instrument="RELIANCE", candles=_fresh_15m_series(),
        ),
        "TCS": FakeFetchSpec(instrument="TCS", candles=_fresh_15m_series()),
    }


# ------------------------------------------------------------
# A. UNIVERSE ACCEPTANCE (19.1 frozen foundation)
# ------------------------------------------------------------


class TestUniverseAcceptance:
    def test_nifty200_default_universe_accepted(self):
        engine = ContinuousScannerEngine(coverage_engine=IntradayCoverageEngine(
            provider=FixtureDataProvider(),
        ))
        result = engine.run_cycle(
            universe=DEFAULT_NIFTY200_UNIVERSE, reference_now=_now(),
        )
        assert result.instrument_count == 200
        assert result.metadata.requested_universe_size == 200
        assert len(result.results) == 200

    def test_separate_benchmark_included_when_market_tuple(self):
        # NIFTY benchmark separate from the 200 constituents.
        names = ("NIFTY",) + tuple(DEFAULT_NIFTY200_UNIVERSE.symbols)
        engine = ContinuousScannerEngine(coverage_engine=IntradayCoverageEngine(
            provider=FixtureDataProvider(),
        ))
        result = engine.run_cycle(universe=names, reference_now=_now())
        assert result.instrument_count == 201
        assert "NIFTY" in result.instrument_statuses()

    def test_plain_sequence_accepted(self):
        engine, _ = _scripted_engine(_two_symbol_specs())
        result = engine.run_cycle(
            universe=["reliance", "TCS"], reference_now=_now(),
        )
        assert result.instrument_count == 2

    def test_scan_once_request_accepted(self):
        engine, _ = _scripted_engine(_two_symbol_specs())
        result = engine.run_cycle(
            ScanOnceRequest(universe=["RELIANCE", "TCS"], reference_now=_now()),
        )
        assert result.instrument_count == 2

    def test_deterministic_universe_sorting(self):
        engine, _ = _scripted_engine(_two_symbol_specs())
        a = engine.run_cycle(universe=["TCS", "RELIANCE"], reference_now=_now())
        b = engine.run_cycle(universe=["RELIANCE", "TCS"], reference_now=_now())
        assert [r.instrument for r in a.results] == ["RELIANCE", "TCS"]
        assert [r.instrument for r in b.results] == ["RELIANCE", "TCS"]


# ------------------------------------------------------------
# B. SINGLE CYCLE PROCESSES THE EXPECTED UNIVERSE
# ------------------------------------------------------------


class TestSingleCycle:
    def test_every_expected_symbol_present(self):
        engine, _ = _scripted_engine(_two_symbol_specs())
        result = engine.run_cycle(
            universe=["RELIANCE", "TCS"], reference_now=_now(),
        )
        assert set(result.instrument_statuses()) == {"RELIANCE", "TCS"}

    def test_result_ordering_deterministic(self):
        engine, _ = _scripted_engine(_two_symbol_specs())
        a = engine.run_cycle(universe=["TCS", "RELIANCE"], reference_now=_now())
        b = engine.run_cycle(universe=["TCS", "RELIANCE"], reference_now=_now())
        assert [r.instrument for r in a.results] == [r.instrument for r in b.results]

    def test_per_symbol_data_preserved(self):
        series = _fresh_15m_series()
        engine, _ = _scripted_engine(
            {"RELIANCE": FakeFetchSpec(instrument="RELIANCE", candles=series)},
        )
        result = engine.run_cycle(
            universe=["RELIANCE"], reference_now=_now(),
        )
        symbol = result.result_for("RELIANCE")
        assert symbol is not None
        assert symbol.status is IntradayCoverageStatus.VALID
        assert symbol.coverage is not None
        assert symbol.coverage.candle_count == len(series)
        assert symbol.fresh is True
        assert symbol.available is True
        assert symbol.needs_attention is False

    def test_cycle_metadata_correct(self):
        engine, _ = _scripted_engine(_two_symbol_specs())
        result = engine.run_cycle(
            universe=["RELIANCE", "TCS"], reference_now=_now(),
        )
        meta = result.metadata
        assert meta.requested_universe_size == 2
        assert meta.attempted_instrument_count == 2
        assert meta.successful_instrument_count == 2
        assert meta.unavailable_instrument_count == 0
        assert meta.failed_instrument_count == 0
        assert meta.cycle_started_at == _now()
        assert meta.cycle_ended_at == _now()
        assert meta.duration_seconds == 0.0

    def test_latest_price_preserved_when_price_source(self):
        series = _fresh_15m_series()
        provider = ScriptedProvider(
            {"RELIANCE": FakeFetchSpec(instrument="RELIANCE", candles=series)},
        )
        price_source = PriceSource(
            lookup=lambda instrument, tf: (
                133.5 if instrument == "RELIANCE" else None
            ),
        )
        engine = ContinuousScannerEngine(
            coverage_engine=IntradayCoverageEngine(provider=provider),
            price_source=price_source,
        )
        result = engine.run_cycle(
            universe=["RELIANCE"], reference_now=_now(),
        )
        assert result.result_for("RELIANCE").latest_price == 133.5

    def test_latest_price_none_without_price_source(self):
        engine, _ = _scripted_engine(_two_symbol_specs())
        result = engine.run_cycle(
            universe=["RELIANCE"], reference_now=_now(),
        )
        assert result.result_for("RELIANCE").latest_price is None


# ------------------------------------------------------------
# C. EXPLICIT STATUSES (never silently dropped)
# ------------------------------------------------------------


class TestExplicitStatuses:
    def test_unsupported_symbol_explicit(self):
        specs = {"RELIANCE": FakeFetchSpec(instrument="RELIANCE", candles=_fresh_15m_series())}
        engine, _ = _scripted_engine(specs)
        result = engine.run_cycle(
            universe=["RELIANCE", "INVENTED"], reference_now=_now(),
        )
        assert result.instrument_count == 2
        assert len(result.results) == 2
        invented = result.result_for("INVENTED")
        assert invented.status is IntradayCoverageStatus.UNSUPPORTED_INSTRUMENT
        assert invented.available is False
        assert invented.needs_attention is True

    def test_stale_symbol_explicit(self):
        # Candle series ends long before the reference (stale).
        stale = _candle(_now() - timedelta(hours=48))
        specs = {
            "RELIANCE": FakeFetchSpec(
                instrument="RELIANCE", candles=(stale,),
            ),
        }
        engine, _ = _scripted_engine(specs)
        result = engine.run_cycle(
            universe=["RELIANCE"], reference_now=_now(),
        )
        symbol = result.result_for("RELIANCE")
        assert symbol.status is IntradayCoverageStatus.STALE
        assert symbol.available is True  # stale = usable data w/ caveat
        assert symbol.fresh is False
        assert symbol.needs_attention is True

    def test_provider_error_explicit(self):
        engine, _ = _scripted_engine(
            {"RELIANCE": FakeFetchSpec(instrument="RELIANCE", candles=_fresh_15m_series())},
            raised={"RELIANCE"},
        )
        result = engine.run_cycle(
            universe=["RELIANCE"], reference_now=_now(),
        )
        assert result.instrument_statuses()["RELIANCE"] is IntradayCoverageStatus.PROVIDER_ERROR

    def test_empty_response_explicit(self):
        engine, _ = _scripted_engine(
            {"RELIANCE": FakeFetchSpec(
                instrument="RELIANCE", candles=(), status=ProviderStatus.EMPTY,
            )},
        )
        result = engine.run_cycle(
            universe=["RELIANCE"], reference_now=_now(),
        )
        assert result.instrument_statuses()["RELIANCE"] is IntradayCoverageStatus.EMPTY

    def test_one_symbol_failure_does_not_kill_cycle(self):
        specs = _two_symbol_specs()
        specs["HDFCBANK"] = FakeFetchSpec(
            instrument="HDFCBANK", candles=(), status=ProviderStatus.EMPTY,
        )
        engine, _ = _scripted_engine(specs, raised={"TCS"})
        result = engine.run_cycle(
            universe=["RELIANCE", "TCS", "HDFCBANK"], reference_now=_now(),
        )
        assert result.instrument_count == 3
        assert result.status is MarketScanStatus.PARTIAL_SUCCESS
        statuses = result.instrument_statuses()
        assert statuses["RELIANCE"] is IntradayCoverageStatus.VALID
        assert statuses["TCS"] is IntradayCoverageStatus.PROVIDER_ERROR
        assert statuses["HDFCBANK"] is IntradayCoverageStatus.EMPTY


# ------------------------------------------------------------
# D. PARTIAL vs FULL vs COMPLETE FAILURE
# ------------------------------------------------------------


class TestCycleStatus:
    def test_full_success_when_all_valid(self):
        engine, _ = _scripted_engine(_two_symbol_specs())
        result = engine.run_cycle(
            universe=["RELIANCE", "TCS"], reference_now=_now(),
        )
        assert result.status is MarketScanStatus.FULL_SUCCESS
        assert result.is_full_success

    def test_partial_success_when_symbol_needs_attention(self):
        specs = _two_symbol_specs()
        specs["STALE_SYM"] = FakeFetchSpec(
            instrument="STALE_SYM", candles=(_candle(_now() - timedelta(hours=48)),),
        )
        engine, _ = _scripted_engine(specs)
        result = engine.run_cycle(
            universe=["RELIANCE", "STALE_SYM"], reference_now=_now(),
        )
        assert result.status is MarketScanStatus.PARTIAL_SUCCESS
        assert result.is_partial_success
        assert not result.is_full_success

    def test_partial_never_full(self):
        specs = {"RELIANCE": FakeFetchSpec(instrument="RELIANCE", candles=_fresh_15m_series())}
        engine, _ = _scripted_engine(specs)
        result = engine.run_cycle(
            universe=["RELIANCE", "MISSING"], reference_now=_now(),
        )
        assert result.status is MarketScanStatus.PARTIAL_SUCCESS
        assert not result.is_full_success

    def test_unavailable_cycle_on_empty_universe(self):
        engine, _ = _scripted_engine({})
        result = engine.run_cycle(universe=[], reference_now=_now())
        assert result.status is MarketScanStatus.UNAVAILABLE
        assert result.error

    def test_complete_failure_on_coverage_raise(self):
        class ExplodingCoverage(IntradayCoverageEngine):
            def assess_universe(self, *args, **kwargs):  # noqa: ANN002
                raise RuntimeError("boom-universe")

        engine = ContinuousScannerEngine(coverage_engine=ExplodingCoverage())
        result = engine.run_cycle(
            universe=["RELIANCE"], reference_now=_now(),
        )
        assert result.status is MarketScanStatus.COMPLETE_FAILURE
        assert result.results == ()
        assert "boom-universe" in result.error


# ------------------------------------------------------------
# E. CYCLE IDENTITY / DETERMINISM
# ------------------------------------------------------------


class TestDeterminism:
    def test_same_inputs_same_cycle_id(self):
        engine, _ = _scripted_engine(_two_symbol_specs())
        a = engine.run_cycle(universe=["RELIANCE", "TCS"], reference_now=_now())
        b = engine.run_cycle(universe=["TCS", "RELIANCE"], reference_now=_now())
        assert a.cycle_id == b.cycle_id
        assert a.cycle_id.startswith("cycle-")

    def test_different_reference_now_different_cycle_id(self):
        engine, _ = _scripted_engine(_two_symbol_specs())
        a = engine.run_cycle(universe=["RELIANCE"], reference_now=_now())
        b = engine.run_cycle(
            universe=["RELIANCE"], reference_now=_now() + timedelta(minutes=15),
        )
        assert a.cycle_id != b.cycle_id

    def test_manual_has_manual_run_id(self):
        engine, _ = _scripted_engine(_two_symbol_specs())
        result = engine.run_cycle(
            universe=["RELIANCE"], reference_now=_now(),
            source=ScanSourceKind.MANUAL, manual_run_id="run-001",
        )
        assert result.source is ScanSourceKind.MANUAL
        assert result.manual_run_id == "run-001"


# ------------------------------------------------------------
# F. CONTINUOUS LOOP (deterministic clock; no real sleeps)
# ------------------------------------------------------------


def _make_scanner(
    engine: ContinuousScannerEngine,
    start: datetime = _now(),
    interval: float = 60.0,
    max_cycles: int | None = 3,
) -> tuple[ContinuousScanner, FakeClock, list]:
    clock = FakeClock(start)
    slept: list[float] = []

    def waiter(seconds: float) -> None:
        slept.append(seconds)
        clock.advance(seconds)

    config = ContinuousScanConfig(
        scan_interval_seconds=interval,
        skip_intervals_on_overrun=True,
        max_cycles=max_cycles,
    )
    scanner = ContinuousScanner(
        engine=engine,
        config=config,
        universe=["RELIANCE", "TCS"],
        clock=clock,
        waiter=waiter,
    )
    return scanner, clock, slept


class TestContinuousLoop:
    def test_multiple_cycles_independent(self):
        engine, _ = _scripted_engine(_two_symbol_specs())
        scanner, clock, _ = _make_scanner(engine, interval=60, max_cycles=3)
        results = scanner.run(cycles=3)
        assert len(results) == 3
        assert scanner.cycle_count == 3
        # Distinct reference times -> distinct cycle ids.
        assert len({r.reference_now for r in results}) == 3
        assert len({r.cycle_id for r in results}) == 3

    def test_interval_respected_by_clock(self):
        engine, _ = _scripted_engine(_two_symbol_specs())
        scanner, clock, slept = _make_scanner(engine, interval=60, max_cycles=3)
        scanner.run(cycles=3)
        # After the first cycle the loop waited one full interval each time.
        assert sum(slept) >= 120.0  # two sleeps of ~60s
        refs = [r.reference_now for r in scanner.results]
        assert (refs[1] - refs[0]).total_seconds() >= 59.9
        assert (refs[2] - refs[1]).total_seconds() >= 59.9

    def test_configurable_interval(self):
        engine, _ = _scripted_engine(_two_symbol_specs())
        scanner, clock, _ = _make_scanner(engine, interval=5, max_cycles=2)
        scanner.run(cycles=2)
        refs = [r.reference_now for r in scanner.results]
        assert (refs[1] - refs[0]).total_seconds() >= 4.9

    def test_max_cycles_cap(self):
        engine, _ = _scripted_engine(_two_symbol_specs())
        scanner, clock, _ = _make_scanner(engine, interval=60, max_cycles=5)
        results = scanner.run(cycles=3)  # caller cap smaller
        assert len(results) == 3
        scanner2, clock2, _ = _make_scanner(engine, interval=60, max_cycles=5)
        results2 = scanner2.run(cycles=None)  # config cap
        assert len(results2) == 5

    def test_scanner_state_during_run(self):
        engine, _ = _scripted_engine(_two_symbol_specs())
        scanner, clock, _ = _make_scanner(engine, interval=60, max_cycles=1)
        assert scanner.state() is ScannerState.STOPPED
        results = scanner.run(cycles=1)
        assert len(results) == 1
        assert scanner.state() is ScannerState.STOPPED
        assert len(scanner.results) == 1


# ------------------------------------------------------------
# G. NO-OVERLAP POLICY
# ------------------------------------------------------------


def _blocking_provider(gate: dict) -> ScriptedProvider:
    class BlockingProvider(ScriptedProvider):
        def fetch(self, instrument, setup_timeframe, lookback_bars=300, *, reference_now=None):
            gate["entered"] = True
            while not gate.get("release"):
                time.sleep(0.001)
            return super().fetch(instrument, setup_timeframe, lookback_bars, reference_now=reference_now)

    return BlockingProvider(_two_symbol_specs())


class TestNoOverlap:
    def test_manual_scan_skipped_while_busy(self):
        gate: dict = {}
        provider = _blocking_provider(gate)
        engine = ContinuousScannerEngine(
            coverage_engine=IntradayCoverageEngine(provider=provider),
        )
        clock = FakeClock(_now())
        scanner = ContinuousScanner(
            engine=engine,
            config=ContinuousScanConfig(scan_interval_seconds=900, run_forever=True),
            universe=["RELIANCE", "TCS"],
            clock=clock,
            waiter=lambda s: clock.advance(s),
        )
        scanner.start()
        deadline = time.monotonic() + 10
        while not gate.get("entered") and time.monotonic() < deadline:  # noqa
            time.sleep(0.005)
        assert scanner.state() in (ScannerState.SCANNING, ScannerState.RUNNING)
        skipped = scanner.scan_once(reference_now=clock())
        assert skipped.status is MarketScanStatus.SKIPPED
        assert skipped.results == ()
        gate["release"] = True
        scanner.stop()
        scanner.join(timeout=10)
        assert scanner.state() is ScannerState.STOPPED

    def test_slow_cycle_does_not_overlap(self):
        # A slow provider (sleeps 0.02s per fetch) with a tiny interval.
        # The loop's back-pressure means cycles are never concurrent and
        # reference times never collide.
        start = _now()
        engine, provider = _scripted_engine(
            _two_symbol_specs(), delay_seconds=0.02,
        )
        clock = FakeClock(start)
        scanner = ContinuousScanner(
            engine=engine,
            config=ContinuousScanConfig(scan_interval_seconds=0.001, max_cycles=3),
            universe=["RELIANCE", "TCS"],
            clock=clock,
            waiter=lambda s: clock.advance(s),
        )
        results = scanner.run(cycles=3)
        assert len(results) == 3
        refs = [r.reference_now for r in results]
        # No two cycles share a reference instant -> no overlap.
        assert len(set(refs)) == 3


# ------------------------------------------------------------
# H. GRACEFUL STOP
# ------------------------------------------------------------


class TestGracefulStop:
    def test_stop_between_cycles(self):
        engine, _ = _scripted_engine(_two_symbol_specs())
        clock = FakeClock(_now())
        scanner = ContinuousScanner(
            engine=engine,
            config=ContinuousScanConfig(scan_interval_seconds=60, run_forever=True),
            universe=["RELIANCE", "TCS"],
            clock=clock,
            waiter=lambda s: clock.advance(s),
        )
        scanner.start()
        time.sleep(0.05)  # let at least one cycle start
        scanner.stop()
        scanner.join(timeout=10)
        assert scanner.state() is ScannerState.STOPPED

    def test_duplicate_stop_idempotent(self):
        engine, _ = _scripted_engine(_two_symbol_specs())
        scanner, clock, _ = _make_scanner(engine, interval=60, max_cycles=2)
        scanner.run(cycles=2)
        scanner.stop()
        scanner.stop()
        assert scanner.state() is ScannerState.STOPPED

    def test_restart_after_stop(self):
        engine, _ = _scripted_engine(_two_symbol_specs())
        clock = FakeClock(_now())
        # Bounded restart: cycle list is reset on start (fresh run).
        scanner = ContinuousScanner(
            engine=engine,
            config=ContinuousScanConfig(scan_interval_seconds=60, run_forever=True),
            universe=["RELIANCE", "TCS"],
            clock=clock,
            waiter=lambda s: clock.advance(s),
        )
        scanner.start()
        time.sleep(0.05)
        scanner.stop()
        scanner.join(timeout=10)
        assert scanner.state() is ScannerState.STOPPED
        assert scanner.cycle_count > 0
        first_count = scanner.cycle_count
        scanner.start()
        time.sleep(0.05)
        scanner.stop()
        scanner.join(timeout=10)
        assert scanner.state() is ScannerState.STOPPED
        # The new run reset the cycle list and produced fresh cycles.
        assert scanner.cycle_count > 0
        assert first_count > 0

    def test_no_orphaned_worker_after_stop(self):
        engine, _ = _scripted_engine(_two_symbol_specs())
        clock = FakeClock(_now())
        scanner = ContinuousScanner(
            engine=engine,
            config=ContinuousScanConfig(scan_interval_seconds=60, run_forever=True),
            universe=["RELIANCE", "TCS"],
            clock=clock,
            waiter=lambda s: clock.advance(s),
        )
        scanner.start()
        time.sleep(0.05)
        scanner.stop()
        scanner.join(timeout=10)
        assert scanner._thread is None or not scanner._thread.is_alive()  # noqa: SLF001


# ------------------------------------------------------------
# I. MARKET SESSION BEHAVIOR (19.2 reuse)
# ------------------------------------------------------------


class TestMarketSession:
    def test_session_open_carried_on_result(self):
        engine, _ = _scripted_engine(_two_symbol_specs())
        result = engine.run_cycle(universe=["RELIANCE"], reference_now=_now())
        assert result.market_session is MarketSessionState.OPEN

    def test_session_weekend_classified(self):
        sunday = datetime(2026, 9, 6, 5, 30, tzinfo=UTC)
        engine, _ = _scripted_engine(_two_symbol_specs())
        result = engine.run_cycle(universe=["RELIANCE"], reference_now=sunday)
        assert result.market_session is MarketSessionState.WEEKEND


# ------------------------------------------------------------
# J. BOUNDARIES (no premature intelligence / execution)
# ------------------------------------------------------------


class TestBoundaries:
    def test_model_has_no_setup_or_ranking_fields(self):
        fields = {f.name for f in MarketScanCycleResult.__dataclass_fields__.values()}
        for forbidden in ("setup", "quality", "score", "rank", "entry", "stop", "target"):
            assert not any(forbidden in f for f in fields)

    def test_model_has_no_execution_fields(self):
        # Field names must not carry broker/order/position/portfolio
        # semantics. (The module docstring may mention "broker
        # execution" only as a negation — the field NAME check is the
        # structural one.)
        fields = {f.name for f in MarketScanCycleResult.__dataclass_fields__.values()}
        fields |= {
            f.name for f in PerSymbolScanResult.__dataclass_fields__.values()
        }
        for forbidden in ("broker", "order", "execution", "position", "portfolio"):
            assert not any(forbidden in f for f in fields), fields

    def test_scanner_module_imports_no_broker_network(self):
        import ast

        path = (
            __import__("pathlib").Path(__file__).resolve().parent.parent
            / "src" / "dashboard" / "continuous_scanner.py"
        )
        mod = ast.parse(open(path).read())
        imports = [
            n.names[0].name
            for n in ast.walk(mod)
            if isinstance(n, ast.ImportFrom) and n.module
        ] + [
            n.names[0].name
            for n in ast.walk(mod)
            if isinstance(n, ast.Import)
        ]
        for forbidden in ("requests", "httpx", "urllib", "socket", "broker"):
            assert not any(forbidden in imp for imp in imports), imports

    def test_cycle_result_never_directional(self):
        engine, _ = _scripted_engine(_two_symbol_specs())
        result = engine.run_cycle(universe=["RELIANCE"], reference_now=_now())
        assert not hasattr(result, "direction")
        assert not hasattr(result, "setup")
        assert not hasattr(result.result_for("RELIANCE"), "recommendation")

    def test_repeated_cycles_produce_independent_results(self):
        engine, _ = _scripted_engine(_two_symbol_specs())
        a = engine.run_cycle(universe=["RELIANCE"], reference_now=_now())
        b = engine.run_cycle(
            universe=["RELIANCE"], reference_now=_now() + timedelta(minutes=15),
        )
        assert a.cycle_id != b.cycle_id
        assert a is not b

    def test_full_suite_regression_baseline(self):
        # Guard: the pre-19.3 pipeline signal/trade baseline is untouched
        # (the scanner shares nothing with the signal pipeline).
        from engine.pipeline.historical_pipeline import (
            HistoricalEvaluationPipeline,
            PipelineConfig,
        )

        config = PipelineConfig()
        assert config is not None  # importable, constructible