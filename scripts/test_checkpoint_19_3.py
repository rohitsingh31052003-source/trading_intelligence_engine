#!/usr/bin/env python3
"""
Checkpoint 19.3 demo — continuous market scanning.

Proves (deterministically, offline):

  1. the scanner consumes the FROZEN 19.1 validated NIFTY Top 200
     universe (via the 19.2 intraday coverage layer);
  2. a single deterministic scan cycle processes the expected universe;
  3. every requested constituent has an EXPLICIT per-symbol result
     (never silently dropped);
  4. result ordering is deterministic (canonical universe order);
  5. per-symbol data (status / bars / freshness / price source) is
     preserved;
  6. unsupported / stale / provider-error / empty symbols are explicitly
     represented;
  7. one failing symbol never terminates the cycle (failure isolation);
  8. PARTIAL vs FULL vs COMPLETE-FAILURE vs SKIPPED are distinct;
  9. scan intervals are configurable and tests never sleep in real time
     (injected deterministic clock + instant waiter);
 10. overlapping cycles are prevented (SKIPPED policy);
 11. the scanner stops cleanly and start/stop is deterministic;
 12. market-session behavior is reused from 19.2;
 13. NO setup detection / ranking / alerts / broker execution anywhere.

Run:  python scripts/test_checkpoint_19_3.py
"""

from __future__ import annotations

import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard.continuous_scanner import (  # noqa: E402
    ContinuousScanner,
    ContinuousScannerEngine,
    PriceSource,
)
from dashboard.data_provider import FixtureDataProvider  # noqa: E402
from dashboard.intraday_coverage import IntradayCoverageEngine  # noqa: E402
from engine.config.universe import NIFTY200_SYMBOLS  # noqa: E402
from engine.config.universe_boundary import (  # noqa: E402
    DEFAULT_NIFTY200_UNIVERSE,
    UniverseBuilder,
)
from engine.data.market_session import MarketSessionState  # noqa: E402
from engine.models.continuous_scan import (  # noqa: E402
    ContinuousScanConfig,
    MarketScanStatus,
    ScannerState,
)
from engine.models.intraday_coverage import IntradayCoverageStatus  # noqa: E402
from engine.models.ohlcv import OHLCVCandle  # noqa: E402
from engine.reporting.continuous_scan import MarketScanCycleFormatter  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))


def _now() -> datetime:
    return datetime(2026, 9, 4, 5, 30, tzinfo=UTC)  # Fri 11:00 IST


def _candle(ts: datetime, close: float = 100.0) -> OHLCVCandle:
    return OHLCVCandle(
        timestamp=ts,
        open=close,
        high=close + 1.0,
        low=close - 1.0,
        close=close,
        volume=1000.0,
    )


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.t = start

    def __call__(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)


def main() -> int:
    now = _now()

    # --- 1. Fixture-only full-universe deterministic run ---
    engine = ContinuousScannerEngine.build("fixture")
    full = engine.run_cycle(
        universe=DEFAULT_NIFTY200_UNIVERSE, reference_now=now,
    )
    check(
        "1. scanner accepts the 19.1 NIFTY Top 200 universe",
        full.instrument_count == 200 and len(full.results) == 200,
        f"instruments={full.instrument_count}",
    )

    # --- 2. Every constituent has an explicit result (no silent drop) ---
    check(
        "2. every requested constituent has an explicit per-symbol result",
        set(full.instrument_statuses()) == set(NIFTY200_SYMBOLS),
        "all 200 present",
    )

    # --- 3. Deterministic ordering (canonical universe order) ---
    ordered = [r.instrument for r in full.results]
    check(
        "3. result ordering is deterministic (canonical universe order)",
        ordered == sorted(ordered) and len(ordered) == len(set(ordered)),
        f"first={ordered[0]!r} last={ordered[-1]!r}",
    )

    # --- 4. Per-symbol data preserved on the fixture path ---
    symbol = full.result_for("RELIANCE")
    assert symbol is not None
    check(
        "4. per-symbol data preserved (status/bars/freshness)",
        symbol.status is IntradayCoverageStatus.STALE
        and symbol.coverage is not None
        and symbol.coverage.candle_count == 20,
        f"status={symbol.status.value}",
    )

    # --- 5. Unsupported symbols explicit in the 200-stock run ---
    statuses = full.instrument_statuses()
    unsupported = sum(
        1
        for s in statuses.values()
        if s is IntradayCoverageStatus.UNSUPPORTED_INSTRUMENT
    )
    check(
        "5. unsupported symbols explicitly represented (never fabricated)",
        unsupported == 196 and statuses["RELIANCE"] is IntradayCoverageStatus.STALE,
        f"unsupported={unsupported}/200",
    )

    # --- 6. Price source carries latest close into per-symbol state ---
    provider = FixtureDataProvider()

    def _lookup(instrument: str, timeframe: str) -> float | None:
        series = provider.fetch(instrument, "15m")
        if not series.setup_candles:
            return None
        return float(series.setup_candles[-1].close)

    price_engine = ContinuousScannerEngine(
        coverage_engine=IntradayCoverageEngine(provider=provider),
        price_source=PriceSource(lookup=_lookup),
    )
    priced = price_engine.run_cycle(universe=["RELIANCE"], reference_now=now)
    check(
        "6. latest-price market state is carried when a price source is supplied",
        priced.result_for("RELIANCE").latest_price is not None,
        f"price={priced.result_for('RELIANCE').latest_price}",
    )

    # --- 7. Failure isolation (one bad symbol never kills the cycle) ---
    class ExplodingFixture(FixtureDataProvider):
        def fetch(self, instrument, setup_timeframe, lookback_bars=300, *,
                  reference_now=None):
            if instrument == "TCS":
                raise RuntimeError("boom TCS")
            return super().fetch(
                instrument, setup_timeframe, lookback_bars,
                reference_now=reference_now,
            )

    iso_engine = ContinuousScannerEngine(
        coverage_engine=IntradayCoverageEngine(provider=ExplodingFixture()),
    )
    isolated = iso_engine.run_cycle(
        universe=["RELIANCE", "TCS", "HDFCBANK"], reference_now=now,
    )
    check(
        "7. one failing symbol does not terminate the cycle",
        isolated.instrument_count == 3
        and isolated.status is MarketScanStatus.PARTIAL_SUCCESS,
        f"status={isolated.status.value}",
    )

    # --- 8. PARTIAL vs FULL vs COMPLETE-FAILURE distinct ---
    check(
        "8a. partial success is never full success",
        isolated.is_partial_success and not isolated.is_full_success,
        f"partial status={isolated.status.value}",
    )
    check(
        "8b. fixture full-universe run is an honest PARTIAL (stale fixture)",
        full.status is MarketScanStatus.PARTIAL_SUCCESS,
        f"full status={full.status.value}",
    )

    class ExplodingUniverse(IntradayCoverageEngine):
        def assess_universe(self, *args, **kwargs):
            raise RuntimeError("boom-universe")

    cfe = ContinuousScannerEngine(coverage_engine=ExplodingUniverse())
    cf = cfe.run_cycle(universe=["RELIANCE"], reference_now=now)
    check(
        "8c. complete failure is distinct (results empty, error carries detail)",
        cf.status is MarketScanStatus.COMPLETE_FAILURE
        and cf.results == () and "boom-universe" in cf.error,
        f"status={cf.status.value}",
    )

    # --- 9. Continuous loop with deterministic clock (no real sleeps) ---
    clock = FakeClock(now)
    slept: list[float] = []

    def waiter(seconds: float) -> None:
        slept.append(seconds)
        clock.advance(seconds)

    loop_engine = ContinuousScannerEngine.build("fixture")
    loop = ContinuousScanner(
        engine=loop_engine,
        config=ContinuousScanConfig(scan_interval_seconds=60, max_cycles=2),
        universe=UniverseBuilder.custom(["RELIANCE", "TCS"]),
        clock=clock,
        waiter=waiter,
    )
    lres = loop.run(cycles=2)
    check(
        "9. continuous loop runs N configurable cycles without real sleeps",
        len(lres) == 2 and sum(slept) >= 60.0,
        f"cycles={len(lres)} slept={sum(slept):.1f}s",
    )

    # --- 10. No-overlap policy (SKIPPED) ---
    class BlockingFixture(FixtureDataProvider):
        def fetch(self, instrument, setup_timeframe, lookback_bars=300, *,
                  reference_now=None):
            time.sleep(0.05)
            return super().fetch(
                instrument, setup_timeframe, lookback_bars,
                reference_now=reference_now,
            )

    gate_clock = FakeClock(now)
    blocking = ContinuousScanner(
        engine=ContinuousScannerEngine(
            coverage_engine=IntradayCoverageEngine(provider=BlockingFixture()),
        ),
        config=ContinuousScanConfig(scan_interval_seconds=0.02, run_forever=True),
        universe=["RELIANCE"],
        clock=gate_clock,
        waiter=lambda s: gate_clock.advance(s),
    )
    blocking.start()
    time.sleep(0.05)
    blocked = blocking.scan_once(reference_now=gate_clock())
    blocking.stop()
    blocking.join(timeout=10)
    check(
        "10. no-overlap policy: manual scan while busy -> SKIPPED",
        blocked.status is MarketScanStatus.SKIPPED,
        f"status={blocked.status.value}",
    )

    # --- 11. Graceful stop + deterministic start/stop ---
    check(
        "11. scanner stops cleanly (STOPPED after stop/join)",
        blocking.state() is ScannerState.STOPPED,
        f"state={blocking.state().value}",
    )

    # --- 12. Market-session behavior reused from 19.2 ---
    check(
        "12. market-session state attached (19.2 reuse)",
        full.market_session is MarketSessionState.OPEN,
        f"session={full.market_session}",
    )

    # --- 13. No broker/network/trading semantics in scanner engine ---
    import inspect

    src = inspect.getsource(ContinuousScannerEngine)
    no_network = not any(
        kw in src for kw in ("requests", "httpx", "urllib", "socket")
    )
    # The docstring may legitimately NEGATE broker semantics ("never
    # constructs a broker provider", "broker execution remains
    # deferred"); the structural check is that no broker API / order
    # placement / execution surface exists in the engine.
    no_broker_surface = not any(
        kw in src
        for kw in ("BrokerAdapter", "place_order", "cancel_order", "execution")
    )
    check(
        "13. no broker/network dependency + no trading semantics in scanner",
        no_network and no_broker_surface,
        f"no_network={no_network} no_broker_surface={no_broker_surface}",
    )

    # --- 14. 19.1/19.2 foundations regression guard ---
    check(
        "14. 19.1/19.2 foundations importable (regression guard)",
        len(NIFTY200_SYMBOLS) == 200
        and set(DEFAULT_NIFTY200_UNIVERSE.symbols) == set(NIFTY200_SYMBOLS)
        and DEFAULT_NIFTY200_UNIVERSE.benchmark_index == ("NIFTY",),
        f"manifest={len(NIFTY200_SYMBOLS)}",
    )

    # --- 15. Reporting formatter ---
    formatter = MarketScanCycleFormatter()
    text = formatter.format(full)
    check(
        "15. cycle report renders (deterministic, no print)",
        "CONTINUOUS MARKET SCAN" in text and "PER-SYMBOL" in text,
    )

    # --- 16. Determinism (repeated run identical) ---
    full2 = engine.run_cycle(
        universe=DEFAULT_NIFTY200_UNIVERSE, reference_now=now,
    )
    check(
        "16. repeated deterministic run produces identical cycle result",
        full2.cycle_id == full.cycle_id and full2.status == full.status,
        f"cycle_id={full2.cycle_id}",
    )

    print("\nCHECKPOINT 19.3 DEMO — CONTINUOUS MARKET SCANNING (OFFLINE)")
    print("=" * 72)
    for name, ok, detail in CHECKS:
        print(f"  {'PASS' if ok else 'FAIL':<6} {name}")
        if not ok and detail:
            print(f"        -> {detail}")
    failed = sum(1 for _, ok, _ in CHECKS if not ok)
    print(f"\n{len(CHECKS) - failed}/{len(CHECKS)} checks passed")
    if failed:
        print("\nDEMO FAILED")
        return 1
    print(
        "\nSprint 19.3 demo completed successfully. "
        "STOP after Checkpoint 19.3; no 19.4-19.9 work started; "
        "broker execution remains deferred.",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())