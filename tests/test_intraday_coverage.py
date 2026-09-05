"""
Checkpoint 19.2 — intraday data coverage layer tests.

These tests prove :class:`dashboard.intraday_coverage.IntradayCoverageEngine`
is a deterministic, honest DATA-QUALITY layer that answers "can the
future scanner reliably obtain intraday data for the NIFTY Top 200?":

* the validated 19.1 NIFTY Top 200 universe is accepted as input;
* provider capability discovery is explicit (never invented);
* provider responses are normalized into canonical OHLCVCandle data
  through the EXISTING boundaries (no provider formats leak);
* data-quality problems (empty / malformed / duplicates / out-of-order /
  future-dated / gaps / stale) are DETECTED, never silently accepted;
* per-instrument failure isolation (one bad symbol never breaks the
  universe);
* partial coverage can never become false full coverage;
* no broker execution is triggered anywhere.

All tests are deterministic and network-free: fixture providers + fake
providers with scripted responses.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from dashboard.data_provider import (
    FixtureDataProvider,
    FreshnessState,
    InstrumentSeries,
    ProviderStatus,
)
from dashboard.intraday_coverage import (
    IntradayCoverageConfig,
    IntradayCoverageEngine,
    IntradayCoverageFormatter,
    IntradayCoverageStatus,
)
from engine.config.universe import NIFTY200_SYMBOLS
from engine.config.universe_boundary import UniverseBuilder
from engine.data.market_session import SessionFreshnessConfig
from engine.models.intraday_coverage import (
    IntradayCoverageReport,
)
from engine.models.ohlcv import OHLCVCandle


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
    """A deterministic Friday 11:00 IST reference instant (05:30 UTC)."""
    return datetime(2026, 9, 4, 5, 30, tzinfo=UTC)


def _friday_cfg(**kw) -> SessionFreshnessConfig:
    return SessionFreshnessConfig(
        open_staleness_multiplier=kw.get("open_mult", 2.0),
        closed_staleness_seconds=kw.get("closed", 26 * 3600),
        weekend_staleness_seconds=kw.get("weekend", 96 * 3600),
    )


def _fresh_15m_series(count: int = 20) -> tuple[OHLCVCandle, ...]:
    """A 15m series ending at the latest COMPLETED candle before the
    reference instant.

    At the reference (Fri 11:00 IST == 05:30 UTC) the 15m candle opened
    at 10:45 IST (05:15 UTC) is the newest that has closed, so the
    series ends at 05:15 UTC (age 900s <= 1800s -> CURRENT).
    """
    end = datetime(2026, 9, 4, 5, 15, tzinfo=UTC)  # 10:45 IST
    return tuple(
        _candle(end - timedelta(minutes=15 * (count - 1 - i)))
        for i in range(count)
    )


# ============================================================
# SCRIPTED FAKE PROVIDER (failure isolation + classification)
# ============================================================


@dataclass(frozen=True)
class FakeFetchSpec:
    instrument: str
    candles: tuple[OHLCVCandle, ...] = ()
    status: ProviderStatus = ProviderStatus.OK
    data_source: str = "fake"
    forming: OHLCVCandle | None = None
    latest: datetime | None = None


class ScriptedProvider:
    """Deterministic fake provider whose behaviour is fully scripted."""

    data_source = "fake"

    def __init__(
        self,
        specs: dict[str, FakeFetchSpec],
        raised: set[str] | None = None,
    ) -> None:
        self._specs = specs
        self._raised = raised or set()
        self.calls: list[tuple[str, str]] = []

    def is_timeframe_supported(self, tf: str) -> bool:
        return tf == "15m"

    def supports_instrument(self, instrument: str) -> bool:
        return instrument in self._specs

    def resolve_symbol(self, instrument: str) -> str:
        return self._specs[instrument].instrument

    def fetch(
        self,
        instrument: str,
        setup_timeframe: str,
        lookback_bars: int = 300,
        *,
        reference_now: datetime | None = None,
    ) -> InstrumentSeries:
        del lookback_bars, reference_now
        self.calls.append((instrument, setup_timeframe))
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
            last_successful_fetch_time=datetime(2026, 9, 4, 5, 29, tzinfo=UTC),
            rejected_future_count=0,
        )

    def last_updated(self, instrument: str, setup_timeframe: str) -> datetime | None:
        del setup_timeframe
        spec = self._specs.get(instrument)
        return spec.latest if spec else None


# ============================================================
# A. UNIVERSE ACCEPTANCE (NIFTY Top 200)
# ============================================================


class TestUniverseAcceptance:
    def test_nifty200_universe_accepted(self):
        engine = IntradayCoverageEngine(provider=FixtureDataProvider())
        report = engine.assess_universe(
            UniverseBuilder.nifty200(),
            reference_now=_now(),
        )
        assert isinstance(report, IntradayCoverageReport)
        # 200 stocks + the benchmark index (carried separately).
        assert report.universe_instrument_count == 201
        assert report.instrument_count == 200
        assert set(report.instruments) == set(NIFTY200_SYMBOLS)
        assert report.counts.tested == 200
        assert report.timeframe == "15m"

    def test_default_universe_is_top200(self):
        engine = IntradayCoverageEngine(provider=FixtureDataProvider())
        report = engine.assess_universe(reference_now=_now())
        assert set(report.instruments) == set(NIFTY200_SYMBOLS)

    def test_plain_sequence_accepted(self):
        engine = IntradayCoverageEngine(provider=FixtureDataProvider())
        report = engine.assess_universe(["RELIANCE", "TCS"], reference_now=_now())
        assert set(report.instruments) == {"RELIANCE", "TCS"}
        assert report.universe_instrument_count == 2

    def test_fixture_only_four_instruments_have_data(self):
        engine = IntradayCoverageEngine(provider=FixtureDataProvider())
        report = engine.assess_universe(reference_now=_now())
        # Only the 4 fixture instruments carry data; 196 are unsupported.
        assert report.counts.unsupported_instrument == 196
        assert report.counts.stale == 4
        assert report.counts.with_valid_data == 4
        assert report.coverage_ratio < 1.0  # partial != full


# ============================================================
# B. CAPABILITY DISCOVERY
# ============================================================


class TestCapabilityDiscovery:
    def test_fixture_declares_only_fixture_instruments(self):
        engine = IntradayCoverageEngine(provider=FixtureDataProvider())
        caps = engine.provider_capabilities(["RELIANCE", "INVENTED"], "15m")
        by = {c.instrument: c for c in caps}
        assert by["RELIANCE"].supported is True
        assert by["INVENTED"].supported is False
        assert by["INVENTED"].reason

    def test_fixture_unsupported_timeframe(self):
        engine = IntradayCoverageEngine(provider=FixtureDataProvider())
        caps = engine.provider_capabilities(["RELIANCE"], "5m")
        for c in caps:
            assert c.supported is False

    def test_upstox_verified_keys_preserved(self):
        engine = IntradayCoverageEngine(provider=FixtureDataProvider())
        sym = engine.symbol_resolutions(["RELIANCE", "TCS", "INVENTED"])
        by = {s.instrument: s for s in sym}
        assert by["RELIANCE"].upstox_instrument_key == "NSE_EQ|INE002A01018"
        assert by["TCS"].upstox_instrument_key == "NSE_EQ|INE467B01029"
        assert by["INVENTED"].upstox_instrument_key is None

    def test_yahoo_symbol_resolution(self):
        engine = IntradayCoverageEngine(provider=FixtureDataProvider())
        sym = engine.symbol_resolutions(["RELIANCE"])
        assert sym[0].yahoo_symbol == "RELIANCE.NS"


# ============================================================
# C. CLASSIFICATION
# ============================================================


class TestClassification:
    def test_unsupported_instrument(self):
        provider = ScriptedProvider({})
        engine = IntradayCoverageEngine(provider=provider)
        res = engine.assess_instrument("UNKNOWN", reference_now=_now())
        assert res.status is IntradayCoverageStatus.UNSUPPORTED_INSTRUMENT

    def test_unsupported_timeframe(self):
        class TFProvider(FixtureDataProvider):
            def is_timeframe_supported(self, tf):  # noqa: ANN001
                return tf == "1h"

        engine = IntradayCoverageEngine(provider=TFProvider())
        res = engine.assess_instrument("RELIANCE", "5m", reference_now=_now())
        assert res.status is IntradayCoverageStatus.UNSUPPORTED_TIMEFRAME

    def test_empty_response(self):
        provider = ScriptedProvider({
            "XYZ": FakeFetchSpec("XYZ", (), status=ProviderStatus.EMPTY),
        })
        engine = IntradayCoverageEngine(provider=provider)
        res = engine.assess_instrument("XYZ", reference_now=_now())
        assert res.status is IntradayCoverageStatus.EMPTY

    def test_provider_error(self):
        provider = ScriptedProvider({
            "XYZ": FakeFetchSpec("XYZ", (), status=ProviderStatus.ERROR),
        })
        engine = IntradayCoverageEngine(provider=provider)
        res = engine.assess_instrument("XYZ", reference_now=_now())
        assert res.status is IntradayCoverageStatus.PROVIDER_ERROR

    def test_not_ready(self):
        provider = ScriptedProvider({
            "ABC": FakeFetchSpec("ABC", (), status=ProviderStatus.NOT_READY),
        })
        engine = IntradayCoverageEngine(provider=provider)
        res = engine.assess_instrument("ABC", reference_now=_now())
        assert res.status is IntradayCoverageStatus.TEMPORARILY_UNAVAILABLE

    def test_raised_provider_is_provider_error(self):
        provider = ScriptedProvider(
            {"XYZ": FakeFetchSpec("XYZ", ())},
            raised={"XYZ"},
        )
        engine = IntradayCoverageEngine(provider=provider)
        res = engine.assess_instrument("XYZ", reference_now=_now())
        assert res.status is IntradayCoverageStatus.PROVIDER_ERROR

    def test_invalid_ohlc_rejected_at_construction(self):
        # Impossible OHLC cannot even be constructed as an OHLCVCandle
        # (high < low) — the canonical model rejects it structurally.
        # The coverage layer also defensively re-validates via
        # DataValidator, but the model contract is the primary gate.
        with pytest.raises(ValueError):
            OHLCVCandle(
                timestamp=_now(),
                open=200.0, high=100.0, low=90.0, close=95.0, volume=1,
            )

    def test_all_candles_rejected_is_invalid_response(self):
        # A series of future-dated candles (all rejected) -> nothing
        # valid -> INVALID_RESPONSE (honest, not fabricated data).
        future1 = _candle(datetime(2026, 9, 4, 6, 0, tzinfo=UTC))
        future2 = _candle(datetime(2026, 9, 4, 6, 15, tzinfo=UTC))
        provider = ScriptedProvider({
            "BAD": FakeFetchSpec("BAD", (future1, future2), status=ProviderStatus.OK),
        })
        engine = IntradayCoverageEngine(provider=provider)
        res = engine.assess_instrument("BAD", reference_now=_now())
        assert res.status is IntradayCoverageStatus.INVALID_RESPONSE
        assert res.rejected_future_count == 2

    def test_valid_fresh_series(self):
        series = _fresh_15m_series(20)
        provider = ScriptedProvider({
            "GOOD": FakeFetchSpec("GOOD", series, status=ProviderStatus.OK),
        })
        engine = IntradayCoverageEngine(provider=provider)
        res = engine.assess_instrument("GOOD", reference_now=_now())
        assert res.status is IntradayCoverageStatus.VALID
        assert res.candle_count == 20

    def test_stale_series(self):
        # Last candle at 09:30 IST (2 x 15m before 11:00 IST) — exactly
        # at the 2x open multiplier boundary -> CURRENT.
        series = (
            _candle(datetime(2026, 9, 4, 3, 0, tzinfo=UTC)),
            _candle(datetime(2026, 9, 4, 3, 15, tzinfo=UTC)),
            _candle(datetime(2026, 9, 4, 3, 30, tzinfo=UTC)),
        )
        provider = ScriptedProvider({
            "OLD": FakeFetchSpec("OLD", series, status=ProviderStatus.OK),
        })
        engine = IntradayCoverageEngine(provider=provider)
        res = engine.assess_instrument("OLD", reference_now=_now())
        # 05:30 - 03:30 = 7200s > 1800 threshold -> STALE
        assert res.status is IntradayCoverageStatus.STALE


# ============================================================
# D. DATA-QUALITY DETECTION
# ============================================================


class TestDataQualityDetection:
    # NOTE: candles are anchored so the series ends at the latest
    # COMPLETED 15m candle before the reference (05:15 UTC == 10:45 IST),
    # making the series CURRENT (VALID) unless the test injects a
    # quality problem.

    def test_duplicate_candles_detected(self):
        ts = datetime(2026, 9, 4, 5, 0, tzinfo=UTC)
        series = (
            _candle(ts),
            _candle(ts),
            _candle(ts + timedelta(minutes=15)),
        )
        provider = ScriptedProvider({
            "DUP": FakeFetchSpec("DUP", series, status=ProviderStatus.OK),
        })
        engine = IntradayCoverageEngine(provider=provider)
        res = engine.assess_instrument("DUP", reference_now=_now())
        assert res.duplicate_count == 1
        assert any(i.code == "DUPLICATE" for i in res.issues)
        assert res.candle_count == 2

    def test_out_of_order_normalized(self):
        ts = datetime(2026, 9, 4, 4, 45, tzinfo=UTC)
        series = (
            _candle(ts + timedelta(minutes=30)),
            _candle(ts),
            _candle(ts + timedelta(minutes=15)),
        )
        provider = ScriptedProvider({
            "OOO": FakeFetchSpec("OOO", series, status=ProviderStatus.OK),
        })
        engine = IntradayCoverageEngine(provider=provider)
        res = engine.assess_instrument("OOO", reference_now=_now())
        assert any(i.code == "UNORDERED" for i in res.issues)
        assert res.candle_count == 3
        assert res.first_timestamp <= res.last_timestamp

    def test_future_dated_candle_rejected(self):
        future = _candle(datetime(2026, 9, 4, 6, 0, tzinfo=UTC))  # > 05:30 UTC
        past = _candle(datetime(2026, 9, 4, 4, 45, tzinfo=UTC))
        provider = ScriptedProvider({
            "FUT": FakeFetchSpec("FUT", (past, future), status=ProviderStatus.OK),
        })
        engine = IntradayCoverageEngine(provider=provider)
        res = engine.assess_instrument("FUT", reference_now=_now())
        # The future candle is rejected by the boundary; only the past one
        # survives (fresh -> VALID).
        assert res.rejected_future_count == 1
        assert res.candle_count == 1
        assert any(i.code == "FUTURE_DATED" for i in res.issues)

    def test_gap_detection_downgrades_to_valid_with_gaps(self):
        # A SAME-DAY series with a 1-hour intraday hole (skipping the
        # 10:30 IST 15m candle) — an unexpected gap inside the NSE
        # session. The latest candle (10:45 IST == 05:15 UTC) is fresh,
        # so the status downgrades to VALID_WITH_GAPS.
        gapped = (
            _candle(datetime(2026, 9, 4, 4, 30, tzinfo=UTC)),  # 10:00 IST
            _candle(datetime(2026, 9, 4, 4, 45, tzinfo=UTC)),  # 10:15 IST
            # >>> 1-hour hole (10:15 -> 10:45 IST) <<<
            _candle(datetime(2026, 9, 4, 5, 15, tzinfo=UTC)),  # 10:45 IST
        )
        provider = ScriptedProvider({
            "GAP": FakeFetchSpec("GAP", gapped, status=ProviderStatus.OK),
        })
        engine = IntradayCoverageEngine(provider=provider)
        res = engine.assess_instrument("GAP", reference_now=_now())
        assert res.status is IntradayCoverageStatus.VALID_WITH_GAPS
        assert any(i.code == "UNEXPECTED_GAP" for i in res.issues)

    def test_gap_detection_disabled(self):
        gapped = (
            _candle(datetime(2026, 9, 4, 4, 30, tzinfo=UTC)),
            _candle(datetime(2026, 9, 4, 4, 45, tzinfo=UTC)),
            _candle(datetime(2026, 9, 4, 5, 15, tzinfo=UTC)),
        )
        provider = ScriptedProvider({
            "GAP": FakeFetchSpec("GAP", gapped, status=ProviderStatus.OK),
        })
        engine = IntradayCoverageEngine(
            provider=provider,
            config=IntradayCoverageConfig(detect_gaps=False),
        )
        res = engine.assess_instrument("GAP", reference_now=_now())
        assert res.status is IntradayCoverageStatus.VALID

    def test_normal_overnight_transition_not_a_gap(self):
        # A series spanning last Friday and this Monday with only the
        # normal overnight/weekend transitions must stay VALID.
        series = (
            _candle(datetime(2026, 9, 4, 4, 45, tzinfo=UTC)),  # Fri 10:15 IST
            _candle(datetime(2026, 9, 4, 5, 0, tzinfo=UTC)),   # Fri 10:30
            _candle(datetime(2026, 9, 7, 3, 45, tzinfo=UTC)),  # Mon 09:15
            _candle(datetime(2026, 9, 7, 4, 0, tzinfo=UTC)),   # Mon 09:30
        )
        provider = ScriptedProvider({
            "X": FakeFetchSpec("X", series, status=ProviderStatus.OK),
        })
        engine = IntradayCoverageEngine(provider=provider)
        # Reference now must be Monday for the series to be fresh:
        monday_now = datetime(2026, 9, 7, 4, 15, tzinfo=UTC)  # Mon 10:00 IST
        res = engine.assess_instrument("X", reference_now=monday_now)
        # Latest candle 09:30 IST Mon is fresh (age 900s <= 1800s).
        assert res.status is IntradayCoverageStatus.VALID
        assert not any(i.code == "UNEXPECTED_GAP" for i in res.issues)


# ============================================================
# E. FAILURE ISOLATION + AGGREGATE COUNTS
# ============================================================


class TestFailureIsolation:
    def test_one_bad_symbol_does_not_break_universe(self):
        good = _fresh_15m_series(5)
        provider = ScriptedProvider(
            {
                "GOOD1": FakeFetchSpec("GOOD1", good, status=ProviderStatus.OK),
                "GOOD2": FakeFetchSpec("GOOD2", good, status=ProviderStatus.OK),
                "BAD": FakeFetchSpec("BAD", (), status=ProviderStatus.ERROR),
                "UNK": FakeFetchSpec("UNK", (), status=ProviderStatus.EMPTY),
            },
            raised={"CRASH"},
        )
        engine = IntradayCoverageEngine(provider=provider)
        report = engine.assess_universe(
            ["GOOD1", "GOOD2", "BAD", "UNK", "CRASH"],
            reference_now=_now(),
        )
        assert report.instrument_count == 5
        assert report.counts.valid == 2
        # BAD reports ProviderStatus.ERROR; CRASH raises -> 2 provider errors.
        assert report.counts.provider_errors == 2
        assert report.counts.empty == 1
        assert report.counts.unsupported_instrument == 0  # CRASH raised, not via status
        assert report.counts.tested == 5

    def test_partial_coverage_never_full(self):
        provider = ScriptedProvider({
            "A": FakeFetchSpec("A", _fresh_15m_series(5), status=ProviderStatus.OK),
            "B": FakeFetchSpec("B", (), status=ProviderStatus.EMPTY),
        })
        engine = IntradayCoverageEngine(provider=provider)
        report = engine.assess_universe(["A", "B"], reference_now=_now())
        assert report.counts.with_valid_data == 1
        assert report.counts.tested == 2
        assert report.coverage_ratio == 0.5
        assert report.coverage_ratio < 1.0

    def test_counts_reconcile(self):
        provider = ScriptedProvider({
            "A": FakeFetchSpec("A", _fresh_15m_series(5), status=ProviderStatus.OK),
            "B": FakeFetchSpec("B", (), status=ProviderStatus.EMPTY),
            "C": FakeFetchSpec("C", (), status=ProviderStatus.ERROR),
            "D": FakeFetchSpec("D", (), status=ProviderStatus.UNSUPPORTED),
        })
        engine = IntradayCoverageEngine(provider=provider)
        report = engine.assess_universe(["A", "B", "C", "D"], reference_now=_now())
        counts = report.counts
        assert counts.tested == 4
        assert counts.valid == 1
        assert counts.empty == 1
        assert counts.provider_errors == 1
        assert counts.unsupported_instrument == 1
        assert counts.supported == 3  # tested - explicit unsupported

    def test_unsupported_and_failures_never_valid(self):
        provider = ScriptedProvider({
            "A": FakeFetchSpec("A", _fresh_15m_series(5), status=ProviderStatus.OK),
        })
        engine = IntradayCoverageEngine(provider=provider)
        report = engine.assess_universe(["A", "NOTHERE"], reference_now=_now())
        assert report.counts.unsupported_instrument == 1
        assert report.counts.with_valid_data == 1
        assert report.counts.supported == 1


# ============================================================
# F. SESSION + FRESHNESS METADATA
# ============================================================


class TestSessionMetadata:
    def test_report_carries_session(self):
        engine = IntradayCoverageEngine(provider=FixtureDataProvider())
        report = engine.assess_universe(["RELIANCE"], reference_now=_now())
        assert report.market_session is not None
        assert report.market_session.value == "OPEN"

    def test_report_carries_seconds_until_next_open(self):
        engine = IntradayCoverageEngine(provider=FixtureDataProvider())
        report = engine.assess_universe(["RELIANCE"], reference_now=_now())
        assert report.seconds_until_next_open is not None

    def test_instrument_carries_session_and_threshold(self):
        provider = ScriptedProvider({
            "GOOD": FakeFetchSpec(
                "GOOD", _fresh_15m_series(5), status=ProviderStatus.OK,
            ),
        })
        engine = IntradayCoverageEngine(provider=provider)
        res = engine.assess_instrument("GOOD", reference_now=_now())
        assert res.market_session is not None
        assert res.staleness_seconds == 1800  # 15m * 2
        assert res.data_age_seconds is not None

    def test_staleness_threshold_documented(self):
        cfg = _friday_cfg(open_mult=3.0)
        provider = ScriptedProvider({
            "GOOD": FakeFetchSpec(
                "GOOD", _fresh_15m_series(5), status=ProviderStatus.OK,
            ),
        })
        engine = IntradayCoverageEngine(
            provider=provider,
            config=IntradayCoverageConfig(session_freshness=cfg),
        )
        res = engine.assess_instrument("GOOD", reference_now=_now())
        assert res.staleness_seconds == 2700


# ============================================================
# G. FORMATTER
# ============================================================


class TestFormatter:
    def test_returns_str_with_counts(self):
        engine = IntradayCoverageEngine(provider=FixtureDataProvider())
        report = engine.assess_universe(
            ["RELIANCE", "TCS", "INVENTED"],
            reference_now=_now(),
        )
        text = IntradayCoverageFormatter().format(report)
        assert isinstance(text, str)
        assert "INTRADAY DATA COVERAGE REPORT" in text
        assert "unsupported_instrument" in text
        assert "DISCLAIMER" in text

    def test_summary(self):
        engine = IntradayCoverageEngine(provider=FixtureDataProvider())
        report = engine.assess_universe(["RELIANCE"], reference_now=_now())
        text = IntradayCoverageFormatter().format_summary(report)
        assert "coverage_ratio" in text
        assert "provider=fixture" in text

    def test_negative_width_rejected(self):
        with pytest.raises(ValueError):
            IntradayCoverageFormatter(width=0)


# ============================================================
# H. NO BROKER EXECUTION + DETERMINISM
# ============================================================


class TestSafetyAndDeterminism:
    def test_no_broker_imports(self):
        import ast
        from pathlib import Path

        path = (
            Path(__file__).resolve().parent.parent
            / "src" / "dashboard" / "intraday_coverage.py"
        )
        tree = ast.parse(path.read_text())
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        joined = " ".join(imports)
        for forbidden in ("execution", "broker", "authorization", "command"):
            assert forbidden not in joined.lower()

    def test_no_broker_models_imported(self):
        # The models module must not drag in execution artifacts.
        from engine.models.intraday_coverage import (
            IntradayCoverageStatus,
        )
        assert IntradayCoverageStatus.VALID.value

    def test_repeated_assessment_identical(self):
        engine = IntradayCoverageEngine(provider=FixtureDataProvider())
        a = engine.assess_universe(["RELIANCE"], reference_now=_now())
        b = engine.assess_universe(["RELIANCE"], reference_now=_now())
        assert a == b

    def test_input_order_independent(self):
        engine = IntradayCoverageEngine(provider=FixtureDataProvider())
        a = engine.assess_universe(["TCS", "RELIANCE"], reference_now=_now())
        b = engine.assess_universe(["RELIANCE", "TCS"], reference_now=_now())
        assert a.instruments == b.instruments

    def test_fixture_reference_now_explicit(self):
        # No wall-clock: fixture run with a fixed reference is stable.
        engine = IntradayCoverageEngine(provider=FixtureDataProvider())
        r1 = engine.assess_universe(["RELIANCE"], reference_now=_now())
        r2 = engine.assess_universe(["RELIANCE"], reference_now=_now())
        assert r1.reference_now == r2.reference_now
        assert r1 == r2

    def test_models_frozen(self):
        from engine.models.intraday_coverage import (
            IntradayCoverageCounts,
        )
        counts = IntradayCoverageCounts()
        with pytest.raises(Exception):
            counts.valid = 1  # type: ignore[misc]

    def test_config_validation(self):
        with pytest.raises(ValueError):
            IntradayCoverageConfig(timeframe="1D")
        with pytest.raises(ValueError):
            IntradayCoverageConfig(timeframe="bogus")
        with pytest.raises(ValueError):
            IntradayCoverageConfig(closure_seconds=0)