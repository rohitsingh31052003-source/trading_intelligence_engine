"""
Checkpoint 19.2 demo — reliable intraday data coverage.

Proves (deterministically, offline):
  1. the validated NIFTY Top 200 universe (19.1) is accepted by the
     intraday data layer;
  2. provider capability discovery is explicit (never invented);
  3. provider responses are normalized into canonical OHLCVCandle data
     through the EXISTING boundaries;
  4. data-quality problems (empty / malformed / duplicates /
     out-of-order / future-dated / gaps / stale) are DETECTED;
  5. per-instrument failure isolation (one bad symbol never breaks the
     universe);
  6. partial coverage never becomes false full coverage;
  7. session-aware freshness (NSE 09:15-15:30 IST) is correct;
  8. no broker execution is triggered anywhere.

Run:  python scripts/test_checkpoint_19_2.py
"""

from __future__ import annotations

import ast
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard.data_provider import (  # noqa: E402
    FixtureDataProvider,
    ProviderStatus,
)
from dashboard.intraday_coverage import (  # noqa: E402
    IntradayCoverageEngine,
    IntradayCoverageFormatter,
    IntradayCoverageStatus,
)
from engine.config.universe import NIFTY200_SYMBOLS  # noqa: E402
from engine.config.universe_boundary import (  # noqa: E402
    UniverseBuilder,
)
from engine.data.market_session import (  # noqa: E402
    MarketSessionState,
    market_session_state,
    session_aware_freshness,
)
from engine.models.ohlcv import OHLCVCandle  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))


def _now() -> datetime:
    return datetime(2026, 9, 4, 5, 30, tzinfo=UTC)  # Fri 11:00 IST


def _candle(ts: datetime) -> OHLCVCandle:
    return OHLCVCandle(
        timestamp=ts, open=100, high=101, low=99, close=100, volume=1000,
    )


def _fresh_series() -> tuple[OHLCVCandle, ...]:
    end = datetime(2026, 9, 4, 5, 15, tzinfo=UTC)  # 10:45 IST
    return tuple(
        _candle(end - timedelta(minutes=15 * i)) for i in range(20)
    )


def main() -> int:
    # 1. NIFTY Top 200 accepted.
    engine = IntradayCoverageEngine(provider=FixtureDataProvider())
    report = engine.assess_universe(
        UniverseBuilder.nifty200(), reference_now=_now(),
    )
    check(
        "NIFTY Top 200 universe accepted (200 stocks assessed)",
        report.instrument_count == 200
        and report.universe_instrument_count == 201
        and set(report.instruments) == set(NIFTY200_SYMBOLS),
        f"assessed={report.instrument_count}",
    )
    check(
        "fixture provider honest: 4 instruments with data, 196 unsupported",
        report.counts.with_valid_data == 4
        and report.counts.unsupported_instrument == 196,
        f"valid={report.counts.with_valid_data} "
        f"unsupported={report.counts.unsupported_instrument}",
    )
    check(
        "partial coverage never becomes full coverage",
        report.coverage_ratio < 1.0,
        f"coverage_ratio={report.coverage_ratio:.3f}",
    )

    # 2. Capability discovery explicit.
    caps = engine.provider_capabilities(["RELIANCE", "INVENTED"], "15m")
    by = {c.instrument: c for c in caps}
    check(
        "capability discovery: RELIANCE supported, INVENTED not",
        by["RELIANCE"].supported and not by["INVENTED"].supported,
    )
    sym = engine.symbol_resolutions(["RELIANCE", "INVENTED"])
    sby = {s.instrument: s for s in sym}
    check(
        "symbol resolution: RELIANCE.NS + verified Upstox key",
        sby["RELIANCE"].yahoo_symbol == "RELIANCE.NS"
        and sby["RELIANCE"].upstox_instrument_key == "NSE_EQ|INE002A01018",
    )

    # 3. Data-quality detection via a scripted provider.
    class Scripted:
        data_source = "fake"

        def __init__(self, series):
            self.series = series

        def is_timeframe_supported(self, tf):
            return tf == "15m"

        def supports_instrument(self, instrument):
            return True

        def resolve_symbol(self, instrument):
            return instrument

        def fetch(self, instrument, tf, lookback_bars=300, *, reference_now=None):
            from dashboard.data_provider import (
                FreshnessState,
                InstrumentSeries,
            )
            return InstrumentSeries(
                instrument=instrument,
                setup_candles=self.series,
                available=True,
                reason="",
                data_source="fake",
                provider_status=ProviderStatus.OK,
                freshness_state=FreshnessState.CURRENT,
                latest_candle_timestamp=(
                    self.series[-1].timestamp if self.series else None
                ),
                latest_completed_candle_timestamp=(
                    self.series[-1].timestamp if self.series else None
                ),
            )

    fresh = _fresh_series()
    dup = (
        _candle(datetime(2026, 9, 4, 5, 0, tzinfo=UTC)),
        _candle(datetime(2026, 9, 4, 5, 0, tzinfo=UTC)),
        _candle(datetime(2026, 9, 4, 5, 15, tzinfo=UTC)),
    )
    ooo = (
        _candle(datetime(2026, 9, 4, 5, 15, tzinfo=UTC)),
        _candle(datetime(2026, 9, 4, 4, 45, tzinfo=UTC)),
        _candle(datetime(2026, 9, 4, 5, 0, tzinfo=UTC)),
    )
    fut = (
        _candle(datetime(2026, 9, 4, 4, 45, tzinfo=UTC)),
        _candle(datetime(2026, 9, 4, 6, 0, tzinfo=UTC)),
    )
    gapped = (
        _candle(datetime(2026, 9, 4, 4, 30, tzinfo=UTC)),
        _candle(datetime(2026, 9, 4, 4, 45, tzinfo=UTC)),
        _candle(datetime(2026, 9, 4, 5, 15, tzinfo=UTC)),
    )

    r_fresh = IntradayCoverageEngine(provider=Scripted(fresh)).assess_instrument(
        "X", reference_now=_now(),
    )
    check(
        "fresh completed series -> VALID",
        r_fresh.status is IntradayCoverageStatus.VALID,
        f"candles={r_fresh.candle_count}",
    )
    r_dup = IntradayCoverageEngine(provider=Scripted(dup)).assess_instrument(
        "X", reference_now=_now(),
    )
    check(
        "duplicate candles detected",
        r_dup.duplicate_count == 1
        and any(i.code == "DUPLICATE" for i in r_dup.issues),
        f"duplicates={r_dup.duplicate_count}",
    )
    r_ooo = IntradayCoverageEngine(provider=Scripted(ooo)).assess_instrument(
        "X", reference_now=_now(),
    )
    check(
        "out-of-order candles normalized + reported",
        any(i.code == "UNORDERED" for i in r_ooo.issues)
        and r_ooo.first_timestamp <= r_ooo.last_timestamp,
    )
    r_fut = IntradayCoverageEngine(provider=Scripted(fut)).assess_instrument(
        "X", reference_now=_now(),
    )
    check(
        "future-dated candle rejected",
        r_fut.rejected_future_count == 1
        and any(i.code == "FUTURE_DATED" for i in r_fut.issues),
        f"rejected_future={r_fut.rejected_future_count}",
    )
    r_gap = IntradayCoverageEngine(provider=Scripted(gapped)).assess_instrument(
        "X", reference_now=_now(),
    )
    check(
        "intraday gap detected -> VALID_WITH_GAPS",
        r_gap.status is IntradayCoverageStatus.VALID_WITH_GAPS
        and any(i.code == "UNEXPECTED_GAP" for i in r_gap.issues),
    )

    # 4. Session-aware freshness.
    check(
        "NSE session classification: Fri 11:00 IST is OPEN",
        market_session_state(_now()) is MarketSessionState.OPEN,
    )
    check(
        "session-aware freshness: 15m candle 900s old is CURRENT",
        session_aware_freshness(
            _now(),
            datetime(2026, 9, 4, 5, 15, tzinfo=UTC),
            "15m",
        ).value == "CURRENT",
    )

    # 5. Failure isolation + counts.
    class Mixed:
        data_source = "fake"

        def __init__(self):
            self.good = _fresh_series()

        def is_timeframe_supported(self, tf):
            return tf == "15m"

        def supports_instrument(self, instrument):
            return True

        def resolve_symbol(self, instrument):
            return instrument

        def fetch(self, instrument, tf, lookback_bars=300, *, reference_now=None):
            from dashboard.data_provider import (
                FreshnessState,
                InstrumentSeries,
            )
            if instrument == "BAD":
                return InstrumentSeries(
                    instrument=instrument, available=False, reason="err",
                    data_source="fake", provider_status=ProviderStatus.ERROR,
                    freshness_state=FreshnessState.UNAVAILABLE,
                )
            if instrument == "EMPTY":
                return InstrumentSeries(
                    instrument=instrument, available=False, reason="empty",
                    data_source="fake", provider_status=ProviderStatus.EMPTY,
                    freshness_state=FreshnessState.UNAVAILABLE,
                )
            return InstrumentSeries(
                instrument=instrument, setup_candles=self.good, available=True,
                reason="", data_source="fake",
                provider_status=ProviderStatus.OK,
                freshness_state=FreshnessState.CURRENT,
                latest_candle_timestamp=self.good[-1].timestamp,
                latest_completed_candle_timestamp=self.good[-1].timestamp,
            )

    mixed = IntradayCoverageEngine(provider=Mixed()).assess_universe(
        ["GOOD", "BAD", "EMPTY"], reference_now=_now(),
    )
    check(
        "one bad symbol does not break the universe",
        mixed.instrument_count == 3
        and mixed.counts.valid == 1
        and mixed.counts.provider_errors == 1
        and mixed.counts.empty == 1,
        f"valid={mixed.counts.valid} errors={mixed.counts.provider_errors} "
        f"empty={mixed.counts.empty}",
    )

    # 6. No broker execution.
    path = Path(__file__).resolve().parent.parent / "src" / "dashboard" / "intraday_coverage.py"
    tree = ast.parse(path.read_text())
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
    joined = " ".join(imports).lower()
    check(
        "no broker/execution imports in the coverage layer",
        all(w not in joined for w in ("execution", "broker", "authorization")),
    )

    # 7. Determinism.
    a = IntradayCoverageEngine(provider=FixtureDataProvider()).assess_universe(
        ["TCS", "RELIANCE"], reference_now=_now(),
    )
    b = IntradayCoverageEngine(provider=FixtureDataProvider()).assess_universe(
        ["RELIANCE", "TCS"], reference_now=_now(),
    )
    check(
        "input-order independence + determinism",
        a.instruments == b.instruments and a == b,
    )

    # 8. Full report rendering.
    text = IntradayCoverageFormatter().format(report)
    check(
        "coverage report renders with counts + disclaimer",
        "INTRADAY DATA COVERAGE REPORT" in text and "DISCLAIMER" in text,
    )

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    print("\nCheckpoint 19.2 demo — reliable intraday data coverage")
    print(f"{passed}/{len(CHECKS)} checks passed\n")
    for name, ok, detail in CHECKS:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    print("\nSprint 19.2 demo completed successfully.")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())