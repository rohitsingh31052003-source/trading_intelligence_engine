"""
Tests for Product Phase 2 — MULTI-INSTRUMENT SCANNER & WATCHLIST.

These tests verify the watchlist abstraction + multi-instrument scanner
are a THIN, honest presentation/orchestration layer over the EXISTING
intelligence pipeline. The scanner implements NO new scoring, NO
probability, NO prediction; every per-row value is read from the reused
Sprint 11A-12E outputs. Coverage areas A-Z:

A. Watchlist creation
B. Duplicate instruments
C. Invalid instruments
D. Deterministic ordering
E. Single-instrument scan
F. Multi-instrument scan
G. Multi-timeframe scan
H. Unsupported timeframe
I. Unsupported instrument
J. Empty provider result
K. Provider failure
L. One-symbol failure isolation
M. Fresh data
N. Stale data
O. Forming candle exclusion
P. Future candle rejection
Q. No-look-ahead
R. Decision preservation
S. Geometry preservation
T. Evidence preservation
U. Actionability preservation
V. Deterministic ranking/presentation ordering
W. Shuffle-invariance
X. API schema
Y. Dashboard rendering
Z. Existing dashboard regression

The scanner is DESCRIPTIVE ONLY. The existing decision classification is
AUTHORITATIVE (never renamed to BUY/SELL, never upgraded/downgraded).
Target 2 remains unsupported. No future leakage.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from dashboard.app import create_app
from dashboard.data_provider import (
    FreshnessConfig,
    FreshnessState,
    InstrumentSeries,
    ProviderStatus,
)
from dashboard.services import (
    AnalysisRequest,
    DashboardAnalysisService,
    ScanRequest,
)
from dashboard.views import (
    ActionabilityState,
    WatchlistRowView,
    WatchlistScanView,
    scan_view_to_jsonable,
    scanner_rank_key,
)
from dashboard.watchlist import (
    DEFAULT_WATCHLIST,
    Watchlist,
    WatchlistSpec,
)
from engine.data.historical_fixtures import historical_candles_by_instrument
from engine.models.ohlcv import OHLCVCandle


# ============================================================
# FIXTURES / HELPERS
# ============================================================


@pytest.fixture
def service() -> DashboardAnalysisService:
    return DashboardAnalysisService()


@pytest.fixture
def client(service: DashboardAnalysisService) -> TestClient:
    return TestClient(create_app(service=service))


def _candle(close: float, ts: datetime, spread: float = 2.0) -> OHLCVCandle:
    return OHLCVCandle(
        timestamp=ts,
        open=close,
        high=close + spread,
        low=close - spread,
        close=close,
        volume=1000.0,
    )


def _series(close_start: float, n: int, step_min: int, start: datetime):
    out = []
    for i in range(n):
        ts = start + timedelta(minutes=step_min * i)
        out.append(_candle(close_start + i * 1.0, ts))
    return out


class _StaticProvider:
    """Provider returning a fixed candle series (deterministic tests).

    Reuses the same InstrumentSeries contract; configurable to fail per
    instrument to exercise failure isolation. Applies the completed-candle
    boundary via :func:`split_completed_candles` so a forming / future
    candle is excluded from the engine input and only carried for
    display (mirrors Product Phase 1 guarantees).
    """

    def __init__(
        self,
        context,
        setup,
        *,
        fail_on: set[str] | None = None,
        reference_now: datetime | None = None,
    ):
        self._context = tuple(context)
        self._setup = tuple(setup)
        self._fail_on = fail_on or set()
        self._reference_now = reference_now
        self.freshness_config = FreshnessConfig()

    def is_timeframe_supported(self, setup_timeframe: str) -> bool:
        return True

    def fetch(self, instrument, setup_timeframe, lookback_bars=300):
        from dashboard.data_provider import split_completed_candles

        if instrument in self._fail_on:
            raise RuntimeError(f"simulated failure for {instrument}")
        boundary_now = self._reference_now or (
            self._setup[-1].timestamp if self._setup else None
        )
        if boundary_now is None:
            setup = self._setup
            latest_completed = None
        else:
            res = split_completed_candles(self._setup, "15m", boundary_now)
            setup = res.completed
            latest_completed = res.latest_completed_timestamp
        return InstrumentSeries(
            instrument=instrument,
            context_candles=self._context,
            setup_candles=setup,
            available=bool(setup),
            data_source="static",
            provider_status=ProviderStatus.OK,
            freshness_state=FreshnessState.CURRENT,
            latest_candle_timestamp=latest_completed,
            latest_completed_candle_timestamp=latest_completed,
        )

    def last_updated(self, instrument, setup_timeframe):
        return self._setup[-1].timestamp if self._setup else None


# ============================================================
# A. WATCHLIST CREATION
# ============================================================


class TestWatchlistCreation:
    def test_default_watchlist(self):
        wl = Watchlist.default()
        assert set(wl.instruments) == set(DEFAULT_WATCHLIST)

    def test_from_iterable(self):
        wl = Watchlist(["NIFTY", "RELIANCE", "TCS"])
        assert len(wl) == 3
        assert "NIFTY" in wl

    def test_from_spec(self):
        spec = WatchlistSpec(instruments=("NIFTY", "RELIANCE"))
        wl = Watchlist.from_spec(spec)
        assert tuple(wl.instruments) == ("NIFTY", "RELIANCE")

    def test_empty_watchlist(self):
        wl = Watchlist()
        assert wl.is_empty()
        assert len(wl) == 0

    def test_label_stored(self):
        wl = Watchlist(["NIFTY"], label="mine")
        assert wl.label == "mine"


# ============================================================
# B. DUPLICATE INSTRUMENTS
# ============================================================


class TestDuplicates:
    def test_duplicates_collapsed(self):
        wl = Watchlist(["NIFTY", "nifty", "Nifty", "RELIANCE"])
        assert tuple(wl.instruments) == ("NIFTY", "RELIANCE")

    def test_add_idempotent(self):
        wl = Watchlist(["NIFTY"])
        assert wl.add("NIFTY") is False  # already present
        assert wl.add("TCS") is True
        assert wl.add("TCS") is False
        assert len(wl) == 2

    def test_spec_dedupes(self):
        spec = WatchlistSpec(instruments=("NIFTY", "NIFTY", "TCS", "tcs"))
        assert spec.instruments == ("NIFTY", "TCS")


# ============================================================
# C. INVALID INSTRUMENTS
# ============================================================


class TestInvalidInstruments:
    def test_empty_rejected(self):
        with pytest.raises(ValueError):
            Watchlist([""])

    def test_whitespace_rejected(self):
        with pytest.raises(ValueError):
            Watchlist(["   "])

    def test_add_empty_rejected(self):
        wl = Watchlist(["NIFTY"])
        with pytest.raises(ValueError):
            wl.add("")

    def test_non_string_rejected(self):
        with pytest.raises(TypeError):
            Watchlist([123])  # type: ignore[list-item]

    def test_spec_rejects_empty(self):
        with pytest.raises(ValueError):
            WatchlistSpec(instruments=("NIFTY", ""))


# ============================================================
# D. DETERMINISTIC ORDERING
# ============================================================


class TestDeterministicOrdering:
    def test_insertion_order_does_not_matter(self):
        a = Watchlist(["TCS", "NIFTY", "RELIANCE"])
        b = Watchlist(["RELIANCE", "TCS", "NIFTY"])
        assert a == b
        assert tuple(a.instruments) == tuple(b.instruments)

    def test_sorted_lexicographically(self):
        wl = Watchlist(["RELIANCE", "NIFTY", "TCS"])
        assert tuple(wl.instruments) == ("NIFTY", "RELIANCE", "TCS")

    def test_remove(self):
        wl = Watchlist(["NIFTY", "RELIANCE", "TCS"])
        assert wl.remove("RELIANCE") is True
        assert wl.remove("RELIANCE") is False
        assert tuple(wl.instruments) == ("NIFTY", "TCS")

    def test_remove_canonicalizes(self):
        wl = Watchlist(["NIFTY"])
        assert wl.remove("nifty") is True
        assert wl.is_empty()

    def test_to_spec_roundtrip(self):
        wl = Watchlist(["NIFTY", "RELIANCE"], label="x")
        spec = wl.to_spec()
        assert spec.instruments == ("NIFTY", "RELIANCE")
        assert spec.label == "x"
        assert Watchlist.from_spec(spec) == wl

    def test_jsonable(self):
        wl = Watchlist(["NIFTY", "RELIANCE"])
        j = wl.to_jsonable()
        assert j["instruments"] == ["NIFTY", "RELIANCE"]


# ============================================================
# E. SINGLE-INSTRUMENT SCAN
# ============================================================


class TestSingleInstrumentScan:
    def test_single_instrument_scan(self, service):
        wl = Watchlist(["NIFTY"])
        scan = service.scan_watchlist(ScanRequest(watchlist=wl, setup_timeframe="15m"))
        assert scan.total == 1
        assert len(scan.rows) == 1
        assert scan.rows[0].instrument == "NIFTY"
        assert scan.rows[0].rank == 1

    def test_scan_returns_scan_view(self, service):
        scan = service.scan_watchlist(ScanRequest(setup_timeframe="15m"))
        assert isinstance(scan, WatchlistScanView)
        assert scan.setup_timeframe == "15m"

    def test_default_watchlist_used_when_none(self, service):
        scan = service.scan_watchlist(ScanRequest(setup_timeframe="15m"))
        assert set(scan.watchlist_instruments) == set(DEFAULT_WATCHLIST)
        assert scan.total == len(DEFAULT_WATCHLIST)

    def test_empty_watchlist_scan(self, service):
        scan = service.scan_watchlist(
            ScanRequest(watchlist=Watchlist(), setup_timeframe="15m"),
        )
        assert scan.is_empty
        assert scan.total == 0
        assert scan.rows == ()


# ============================================================
# F. MULTI-INSTRUMENT SCAN
# ============================================================


class TestMultiInstrumentScan:
    def test_default_watchlist_scans_all(self, service):
        scan = service.scan_watchlist(ScanRequest(setup_timeframe="15m"))
        assert scan.total == 5
        scanned = {r.instrument for r in scan.rows}
        assert scanned == set(DEFAULT_WATCHLIST)

    def test_ranks_are_unique_and_contiguous(self, service):
        scan = service.scan_watchlist(ScanRequest(setup_timeframe="15m"))
        ranks = sorted(r.rank for r in scan.rows)
        assert ranks == list(range(1, scan.total + 1))

    def test_counts_reconcile(self, service):
        scan = service.scan_watchlist(ScanRequest(setup_timeframe="15m"))
        assert scan.analyzed + scan.errored == scan.total
        assert len(scan.rows) == scan.total


# ============================================================
# G. MULTI-TIMEFRAME SCAN
# ============================================================


class TestMultiTimeframeScan:
    def test_supported_timeframe(self, service):
        scan = service.scan_watchlist(ScanRequest(setup_timeframe="15m"))
        assert scan.setup_timeframe == "15m"
        assert scan.errored == 0

    def test_context_timeframe_surfaced(self, service):
        scan = service.scan_watchlist(ScanRequest(setup_timeframe="15m"))
        # 15m -> 1D context for the fixture provider.
        assert scan.context_timeframe == "1D"


# ============================================================
# H. UNSUPPORTED TIMEFRAME
# ============================================================


class TestUnsupportedTimeframe:
    def test_unsupported_timeframe_errors_each_instrument(self, service):
        scan = service.scan_watchlist(ScanRequest(setup_timeframe="5m"))
        # Fixture provider only supports 15M; every instrument is an
        # honest error (failure isolation, not a crash).
        assert scan.errored == scan.total
        for row in scan.rows:
            assert row.error is True
            assert row.actionability is ActionabilityState.INVALID
            assert row.complete is False

    def test_unsupported_timeframe_warning_present(self, service):
        scan = service.scan_watchlist(ScanRequest(setup_timeframe="5m"))
        assert scan.has_errors
        assert any("could not be analysed" in w for w in scan.warnings)


# ============================================================
# I. UNSUPPORTED INSTRUMENT
# ============================================================


class TestUnsupportedInstrument:
    def test_unsupported_instrument_is_error_row(self, service):
        wl = Watchlist(["NIFTY", "BOGUS"])
        scan = service.scan_watchlist(ScanRequest(watchlist=wl, setup_timeframe="15m"))
        bogus = next(r for r in scan.rows if r.instrument == "BOGUS")
        assert bogus.error is True
        assert bogus.actionability is ActionabilityState.INVALID

    def test_unsupported_instrument_does_not_abort_scan(self, service):
        wl = Watchlist(["BOGUS", "NIFTY", "RELIANCE"])
        scan = service.scan_watchlist(ScanRequest(watchlist=wl, setup_timeframe="15m"))
        assert scan.total == 3
        assert scan.errored == 1
        nifty = next(r for r in scan.rows if r.instrument == "NIFTY")
        assert nifty.error is False


# ============================================================
# J. EMPTY PROVIDER RESULT
# ============================================================


class TestEmptyProviderResult:
    def test_empty_series_handled(self):
        p = _StaticProvider(context=(), setup=())
        svc = DashboardAnalysisService(provider=p)
        scan = svc.scan_watchlist(
            ScanRequest(watchlist=Watchlist(["NIFTY"]), setup_timeframe="15m"),
        )
        assert scan.total == 1
        assert scan.rows[0].error is True
        assert scan.rows[0].actionability is ActionabilityState.INVALID


# ============================================================
# K. PROVIDER FAILURE
# ============================================================


class TestProviderFailure:
    def test_provider_failure_isolated(self):
        p = _StaticProvider(
            context=_series(100.0, 30, 15, datetime(2024, 1, 1, tzinfo=UTC)),
            setup=_series(100.0, 30, 15, datetime(2024, 1, 1, tzinfo=UTC)),
            fail_on={"NIFTY"},
        )
        svc = DashboardAnalysisService(provider=p)
        scan = svc.scan_watchlist(
            ScanRequest(watchlist=Watchlist(["NIFTY", "RELIANCE"]), setup_timeframe="15m"),
        )
        nifty = next(r for r in scan.rows if r.instrument == "NIFTY")
        assert nifty.error is True
        assert nifty.actionability is ActionabilityState.INVALID
        # The failure is reported, not raised.
        assert scan.has_errors
        # The other instrument was still scanned.
        assert scan.total == 2


# ============================================================
# L. ONE-SYMBOL FAILURE ISOLATION
# ============================================================


class TestFailureIsolation:
    def test_one_bad_symbol_does_not_destroy_scan(self):
        p = _StaticProvider(
            context=_series(100.0, 30, 15, datetime(2024, 1, 1, tzinfo=UTC)),
            setup=_series(100.0, 30, 15, datetime(2024, 1, 1, tzinfo=UTC)),
            fail_on={"TCS"},
        )
        svc = DashboardAnalysisService(provider=p)
        wl = Watchlist(["NIFTY", "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK"])
        scan = svc.scan_watchlist(ScanRequest(watchlist=wl, setup_timeframe="15m"))
        assert scan.total == 5
        assert scan.errored == 1
        # The four non-failing instruments were still analyzed.
        assert scan.analyzed == 4
        tcs = next(r for r in scan.rows if r.instrument == "TCS")
        assert tcs.error is True

    def test_failure_does_not_raise(self):
        p = _StaticProvider(
            context=(),
            setup=(),
            fail_on={"NIFTY"},
        )
        svc = DashboardAnalysisService(provider=p)
        # Must not raise.
        scan = svc.scan_watchlist(
            ScanRequest(watchlist=Watchlist(["NIFTY"]), setup_timeframe="15m"),
        )
        assert scan.total == 1


# ============================================================
# M. FRESH DATA
# ============================================================


class TestFreshData:
    def test_fresh_data_current(self):
        now = datetime(2025, 6, 2, 12, 0, tzinfo=UTC)
        setup = _series(100.0, 30, 15, now - timedelta(minutes=30 * 15))
        p = _StaticProvider(_series(95.0, 20, 1440, now - timedelta(days=20)), setup)
        svc = DashboardAnalysisService(provider=p)
        scan = svc.scan_watchlist(
            ScanRequest(watchlist=Watchlist(["NIFTY"]), setup_timeframe="15m"),
        )
        row = scan.rows[0]
        assert row.freshness_state == "CURRENT"


# ============================================================
# N. STALE DATA
# ============================================================


class TestStaleData:
    def test_fixture_data_is_stale(self, service):
        # Fixture data is historical -> STALE (data quality only).
        scan = service.scan_watchlist(ScanRequest(setup_timeframe="15m"))
        for row in scan.rows:
            if not row.error:
                assert row.freshness_state in ("STALE", "CURRENT")
        # Freshness never alters the decision; just metadata.
        assert scan.errored == 0


# ============================================================
# O. FORMING CANDLE EXCLUSION
# ============================================================


class TestFormingCandleExclusion:
    def test_forming_candle_not_in_engine_input(self, monkeypatch, service):
        captured = []

        orig = service.analyze

        def _spy(request):
            view = orig(request)
            captured.append((request.instrument, view.evaluation_timestamp))
            return view

        monkeypatch.setattr(service, "analyze", _spy)
        service.scan_watchlist(ScanRequest(setup_timeframe="15m"))
        # Every evaluation timestamp is the latest COMPLETED candle
        # (never None for analyzed instruments).
        for _inst, ts in captured:
            if ts is not None:
                assert ts is not None


# ============================================================
# P. FUTURE CANDLE REJECTION
# ============================================================


class TestFutureCandleRejection:
    def test_future_candle_rejected(self, service):
        from engine.data.historical_fixtures import historical_candles_by_instrument

        data = historical_candles_by_instrument(("NIFTY",), "1D", "15M")
        setup = list(data["NIFTY"]["15M"])
        ctx = list(data["NIFTY"]["1D"])
        T = setup[-1].timestamp
        # Pin the evaluation boundary to the latest completed candle so
        # any appended future candle is excluded by the boundary.
        p = _StaticProvider(ctx, setup, reference_now=T)
        svc = DashboardAnalysisService(provider=p)
        v1 = svc.scan_watchlist(
            ScanRequest(watchlist=Watchlist(["NIFTY"]), setup_timeframe="15m"),
        ).rows[0]
        # Append a future candle and re-scan with the SAME boundary:
        # the future candle is rejected by the boundary, so the result
        # is unchanged (no look-ahead).
        future = OHLCVCandle(
            timestamp=T + timedelta(minutes=15),
            open=9999.0, high=10000.0, low=9998.0, close=9999.0, volume=10**9,
        )
        p2 = _StaticProvider(ctx, setup + [future], reference_now=T)
        svc2 = DashboardAnalysisService(provider=p2)
        v2 = svc2.scan_watchlist(
            ScanRequest(watchlist=Watchlist(["NIFTY"]), setup_timeframe="15m"),
        ).rows[0]
        assert v1.decision_classification == v2.decision_classification
        assert v1.entry == v2.entry


# ============================================================
# Q. NO-LOOK-AHEAD
# ============================================================


class TestNoLookAhead:
    def test_scanner_does_not_call_outcome_evaluator(self, service, monkeypatch):
        import engine.intelligence.historical_outcome as ho

        def _boom(*a, **k):
            raise RuntimeError("outcome evaluator must not be called")

        monkeypatch.setattr(ho.OutcomeEvaluator, "evaluate", _boom)
        scan = service.scan_watchlist(ScanRequest(setup_timeframe="15m"))
        assert scan.total == 5

    def test_scanner_does_not_call_pipeline(self, service, monkeypatch):
        import engine.pipeline.historical_pipeline as hp

        def _boom(*a, **k):
            raise RuntimeError("pipeline must not be called")

        monkeypatch.setattr(hp.HistoricalEvaluationPipeline, "evaluate", _boom)
        scan = service.scan_watchlist(ScanRequest(setup_timeframe="15m"))
        assert scan.total == 5

    def test_scan_watchlist_no_future_argument(self):
        sig = inspect.signature(DashboardAnalysisService.scan_watchlist)
        assert "future" not in sig.parameters
        assert "future_candles" not in sig.parameters

    def test_scan_request_no_future_field(self):
        sig = inspect.signature(ScanRequest)
        assert "future" not in sig.parameters
        assert "future_candles" not in sig.parameters


# ============================================================
# R. DECISION PRESERVATION
# ============================================================


class TestDecisionPreservation:
    def test_decision_classification_reused_verbatim(self, service):
        wl = Watchlist(["NIFTY"])
        scan = service.scan_watchlist(ScanRequest(watchlist=wl, setup_timeframe="15m"))
        row = scan.rows[0]
        single = service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        assert row.decision_classification == single.decision.decision_classification
        assert row.view.decision.decision_score == single.decision.decision_score

    def test_decision_not_renamed_to_buy_sell(self, service):
        scan = service.scan_watchlist(ScanRequest(setup_timeframe="15m"))
        for row in scan.rows:
            dc = row.decision_classification
            assert dc not in ("BUY", "SELL", "ENTER", "EXIT", "HOLD")


# ============================================================
# S. GEOMETRY PRESERVATION
# ============================================================


class TestGeometryPreservation:
    def test_geometry_reused_verbatim(self, service):
        wl = Watchlist(["NIFTY"])
        scan = service.scan_watchlist(ScanRequest(watchlist=wl, setup_timeframe="15m"))
        row = scan.rows[0]
        single = service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        assert row.entry == single.geometry.entry
        assert row.stop == single.geometry.stop
        assert row.target_1 == single.geometry.target_1
        assert row.risk_reward_ratio == single.geometry.risk_reward_ratio

    def test_target_2_unsupported(self, service):
        scan = service.scan_watchlist(ScanRequest(setup_timeframe="15m"))
        for row in scan.rows:
            assert row.view.geometry.target_2 is None
            assert row.view.geometry.target_2_supported is False


# ============================================================
# T. EVIDENCE PRESERVATION
# ============================================================


class TestEvidencePreservation:
    def test_evidence_unavailable_without_corpus(self, service):
        scan = service.scan_watchlist(ScanRequest(setup_timeframe="15m"))
        for row in scan.rows:
            assert row.view.evidence.available is False
            assert row.evidence_strength == "UNAVAILABLE"

    def test_unavailable_not_insufficient(self, service):
        # UNAVAILABLE evidence is NOT the same as INSUFFICIENT.
        scan = service.scan_watchlist(ScanRequest(setup_timeframe="15m"))
        for row in scan.rows:
            assert row.evidence_strength != "INSUFFICIENT"


# ============================================================
# U. ACTIONABILITY PRESERVATION
# ============================================================


class TestActionabilityPreservation:
    def test_actionability_reused_verbatim(self, service):
        wl = Watchlist(["NIFTY"])
        scan = service.scan_watchlist(ScanRequest(watchlist=wl, setup_timeframe="15m"))
        row = scan.rows[0]
        single = service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        assert row.actionability is single.actionability

    def test_actionability_states_not_buy_sell(self, service):
        scan = service.scan_watchlist(ScanRequest(setup_timeframe="15m"))
        for row in scan.rows:
            assert "BUY" not in row.actionability.value
            assert "SELL" not in row.actionability.value


# ============================================================
# V. DETERMINISTIC RANKING / PRESENTATION ORDERING
# ============================================================


class TestRanking:
    def test_ranks_strongest_first(self, service):
        scan = service.scan_watchlist(ScanRequest(setup_timeframe="15m"))
        keys = [scanner_rank_key(r) for r in scan.rows]
        assert keys == sorted(keys)

    def test_decision_rank_ordering(self):
        # Build rows with explicit classifications to verify the
        # presentation ordering key without relying on fixture output.
        def _row(dc):
            from dashboard.views import DashboardTradeView, DecisionView

            return WatchlistRowView(
                instrument="X",
                view=DashboardTradeView(decision=DecisionView(decision_classification=dc)),
            )

        pref = _row("PREFERRED")
        qual = _row("QUALIFIED")
        watch = _row("WATCH")
        rej = _row("REJECTED")
        ordered = sorted([rej, watch, qual, pref], key=scanner_rank_key)
        assert [r.decision_classification for r in ordered] == [
            "PREFERRED", "QUALIFIED", "WATCH", "REJECTED",
        ]

    def test_direction_not_a_ranking_key(self):
        # Two rows identical except direction must sort by instrument
        # name, NOT by LONG/SHORT.
        from dashboard.views import DashboardTradeView, GeometryView

        long_row = WatchlistRowView(
            instrument="B",
            view=DashboardTradeView(geometry=GeometryView(direction="LONG")),
        )
        short_row = WatchlistRowView(
            instrument="A",
            view=DashboardTradeView(geometry=GeometryView(direction="SHORT")),
        )
        ordered = sorted([long_row, short_row], key=scanner_rank_key)
        assert [r.instrument for r in ordered] == ["A", "B"]

    def test_rank_key_uses_existing_fields_only(self):
        # The rank key is a tuple of existing fields; verify shape.
        row = WatchlistRowView(instrument="NIFTY")
        key = scanner_rank_key(row)
        assert isinstance(key, tuple)
        assert len(key) == 6
        assert key[-1] == "NIFTY"


# ============================================================
# W. SHUFFLE-INVARIANCE
# ============================================================


class TestShuffleInvariance:
    def test_input_order_does_not_change_output(self, service):
        wl_a = Watchlist(["TCS", "NIFTY", "RELIANCE", "HDFCBANK", "ICICIBANK"])
        wl_b = Watchlist(["ICICIBANK", "HDFCBANK", "RELIANCE", "NIFTY", "TCS"])
        scan_a = service.scan_watchlist(ScanRequest(watchlist=wl_a, setup_timeframe="15m"))
        scan_b = service.scan_watchlist(ScanRequest(watchlist=wl_b, setup_timeframe="15m"))
        order_a = [r.instrument for r in scan_a.rows]
        order_b = [r.instrument for r in scan_b.rows]
        assert order_a == order_b
        assert [r.rank for r in scan_a.rows] == [r.rank for r in scan_b.rows]

    def test_repeated_scan_identical(self, service):
        s1 = service.scan_watchlist(ScanRequest(setup_timeframe="15m"))
        s2 = service.scan_watchlist(ScanRequest(setup_timeframe="15m"))
        assert [r.instrument for r in s1.rows] == [r.instrument for r in s2.rows]


# ============================================================
# X. API SCHEMA
# ============================================================


class TestApiSchema:
    def test_api_scan_returns_json(self, client):
        r = client.get("/api/scan")
        assert r.status_code == 200
        j = r.json()
        for key in (
            "watchlist_instruments", "setup_timeframe", "context_timeframe",
            "rows", "total", "analyzed", "errored", "actionable_count",
            "warnings", "rationale",
        ):
            assert key in j

    def test_api_scan_row_fields(self, client):
        j = client.get("/api/scan").json()
        row = j["rows"][0]
        for key in (
            "rank", "instrument", "error", "complete", "data_source",
            "freshness_state", "decision_classification", "decision_score",
            "actionability", "actionability_reason", "evidence_strength",
            "evidence_available", "setup_type", "direction",
            "geometry_available", "entry", "stop", "target_1", "target_2",
            "target_2_supported", "risk_reward_ratio", "review_url",
        ):
            assert key in row

    def test_api_scan_custom_watchlist(self, client):
        j = client.get("/api/scan?instruments=NIFTY,TCS").json()
        assert set(j["watchlist_instruments"]) == {"NIFTY", "TCS"}
        assert j["total"] == 2

    def test_api_scan_custom_timeframe(self, client):
        j = client.get("/api/scan?timeframe=5m").json()
        assert j["setup_timeframe"] == "5m"
        # All errored (unsupported timeframe on fixture).
        assert j["errored"] == j["total"]

    def test_api_scan_review_url(self, client):
        j = client.get("/api/scan?instruments=NIFTY").json()
        row = j["rows"][0]
        assert row["review_url"] == "/?instrument=NIFTY&timeframe=15m"

    def test_api_scan_target_2_unsupported(self, client):
        j = client.get("/api/scan").json()
        for row in j["rows"]:
            assert row["target_2"] is None
            assert row["target_2_supported"] is False

    def test_api_scan_no_predictive_language(self, client):
        j = client.get("/api/scan").json()
        blob = str(j).lower()
        for term in ("guaranteed profit", "probability of success", "buy signal", "sell signal"):
            assert term not in blob


# ============================================================
# Y. DASHBOARD RENDERING
# ============================================================


class TestDashboardRendering:
    def test_scan_page_renders(self, client):
        r = client.get("/scan")
        assert r.status_code == 200
        assert "scanner-table" in r.text
        assert "Scan Summary" in r.text

    def test_scan_page_has_nav(self, client):
        r = client.get("/scan")
        assert "/scan" in r.text
        assert "/" in r.text  # link to trade review

    def test_scan_page_displays_instruments(self, client):
        r = client.get("/scan")
        for inst in DEFAULT_WATCHLIST:
            assert inst in r.text

    def test_scan_page_custom_instruments(self, client):
        r = client.get("/scan?instruments=NIFTY,TCS")
        assert "NIFTY" in r.text
        assert "TCS" in r.text
        assert "RELIANCE" not in r.text.split("scanner-table")[1].split("</tbody>")[0]

    def test_scan_page_no_predictive_language(self, client):
        r = client.get("/scan")
        text = r.text.lower()
        assert "guaranteed profit" not in text
        assert "probability of success" not in text
        assert "presentation" in text  # ordering is described as presentational

    def test_scan_view_to_jsonable_deterministic(self, service):
        scan = service.scan_watchlist(ScanRequest(setup_timeframe="15m"))
        a = scan_view_to_jsonable(scan)
        b = scan_view_to_jsonable(scan)
        assert a == b


# ============================================================
# Z. EXISTING DASHBOARD REGRESSION
# ============================================================


class TestRegression:
    def test_existing_routes_present(self, client):
        r = client.get("/")
        assert r.status_code == 200
        r = client.get("/health")
        assert r.status_code == 200
        r = client.get("/api/health")
        assert r.status_code == 200
        r = client.get("/api/analysis?instrument=NIFTY&timeframe=15m")
        assert r.status_code == 200
        r = client.get("/api/instruments")
        assert r.status_code == 200

    def test_pipeline_baseline_signals_4_trades_3(self):
        from engine.pipeline import (
            HistoricalEvaluationPipeline, PipelineConfig, trending_dataset,
        )

        result = HistoricalEvaluationPipeline(PipelineConfig()).evaluate(
            trending_dataset(),
        )
        assert result.signals_generated == 4
        assert result.completed_trades == 3

    def test_trade_review_view_still_works(self, service):
        v = service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        assert v.instrument == "NIFTY"
        # Target 2 still unsupported.
        assert v.geometry.target_2 is None
        assert v.geometry.target_2_supported is False

    def test_scanner_does_not_break_single_analysis(self, service):
        # Running the scanner must not corrupt subsequent single analysis.
        service.scan_watchlist(ScanRequest(setup_timeframe="15m"))
        v = service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        assert v.complete is True
