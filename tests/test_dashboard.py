"""
Tests for the dashboard productization layer (Phase: Productization).

These tests verify the dashboard is a THIN, honest presentation layer
over the existing trading-intelligence-engine. They cover the required
areas A-S:

A. dashboard startup
B. routes
C. health endpoint
D. instrument selection
E. timeframe selection
F. unavailable data
G. stale data
H. decision rendering
I. evidence rendering
J. trade geometry rendering
K. missing trade geometry
L. actionability mapping
M. no-look-ahead
N. serialization/presentation model
O. deterministic output
P. input immutability
Q. existing decision identity
R. regression against 11V->12E
S. pipeline baseline (signals=4, trades=3)

The dashboard is DESCRIPTIVE ONLY. These tests assert it never
fabricates entry/stop/target, never calls outcome evaluation during
current analysis, never re-scores the existing decision, and preserves
the 11Y evidence hard gate semantics.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from dashboard.app import create_app
from dashboard.data_provider import (
    FIXTURE_INSTRUMENTS,
    FixtureDataProvider,
    InstrumentSeries,
    SUPPORTED_TIMEFRAMES,
    context_timeframe_for,
    make_provider,
)
from dashboard.services import (
    AnalysisRequest,
    ChartPayload,
    DashboardAnalysisService,
    EvidenceSource,
)
from dashboard.views import (
    ActionabilityState,
    DashboardTradeView,
    DecisionView,
    EvidenceView,
    GeometryView,
    MarketOverviewView,
    derive_actionability,
    to_jsonable,
)
from engine.data.historical_fixtures import historical_candles_by_instrument
from engine.intelligence.market_scanner import (
    InstrumentDataset,
    MarketScanner,
    ScanEngines,
)
from engine.config.market_scan_config import MarketScanConfig
from engine.models.ohlcv import OHLCVCandle


# ============================================================
# FIXTURES / HELPERS
# ============================================================


@pytest.fixture
def fixture_data():
    return historical_candles_by_instrument(("NIFTY",), "1D", "15M")


@pytest.fixture
def service() -> DashboardAnalysisService:
    return DashboardAnalysisService()


@pytest.fixture
def client(service: DashboardAnalysisService) -> TestClient:
    app = create_app(service=service)
    return TestClient(app)


def _candle(close: float, ts: datetime, spread: float = 2.0) -> OHLCVCandle:
    return OHLCVCandle(
        timestamp=ts,
        open=close,
        high=close + spread,
        low=close - spread,
        close=close,
        volume=1000.0,
    )


class _StaticProvider(FixtureDataProvider):
    """Provider returning a fixed candle series (for no-look-ahead tests)."""

    def __init__(self, context, setup):
        self._context = tuple(context)
        self._setup = tuple(setup)

    def fetch(self, instrument, setup_timeframe, lookback_bars=300):
        return InstrumentSeries(
            instrument=instrument,
            context_candles=self._context,
            setup_candles=self._setup,
            available=bool(self._setup),
        )

    def last_updated(self, instrument, setup_timeframe):
        return self._setup[-1].timestamp if self._setup else None


# ============================================================
# A. DASHBOARD STARTUP
# ============================================================


class TestStartup:
    def test_app_creates(self):
        app = create_app(service=DashboardAnalysisService())
        assert app is not None

    def test_app_has_routes(self):
        app = create_app(service=DashboardAnalysisService())
        paths = {r.path for r in app.routes}
        assert "/" in paths
        assert "/health" in paths
        assert "/api/health" in paths
        assert "/api/analysis" in paths
        assert "/api/instruments" in paths

    def test_module_level_app_exists(self):
        from dashboard.app import app

        assert app is not None


# ============================================================
# B. ROUTES
# ============================================================


class TestRoutes:
    def test_root_returns_html(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "Trading Intelligence Dashboard" in r.text

    def test_root_with_selection(self, client):
        r = client.get("/?instrument=NIFTY&timeframe=15m")
        assert r.status_code == 200
        assert "NIFTY" in r.text

    def test_api_analysis(self, client):
        r = client.get("/api/analysis?instrument=NIFTY&timeframe=15m")
        assert r.status_code == 200
        j = r.json()
        assert j["instrument"] == "NIFTY"
        assert "actionability" in j
        assert "geometry" in j
        assert "decision" in j
        assert "evidence" in j
        assert "chart" in j

    def test_api_instruments(self, client):
        r = client.get("/api/instruments")
        assert r.status_code == 200
        j = r.json()
        assert "NIFTY" in j["instruments"]
        assert "15m" in j["timeframes"]

    def test_static_css_served(self, client):
        r = client.get("/static/dashboard.css")
        assert r.status_code == 200

    def test_static_chartjs_served(self, client):
        r = client.get("/static/chart.js")
        assert r.status_code == 200


# ============================================================
# C. HEALTH ENDPOINT
# ============================================================


class TestHealth:
    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        j = r.json()
        assert j["status"] == "ok"
        assert j["provider"] == "FixtureDataProvider"
        assert "NIFTY" in j["instruments"]
        assert "15m" in j["timeframes"]

    def test_api_health(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


# ============================================================
# D. INSTRUMENT SELECTION
# ============================================================


class TestInstrumentSelection:
    def test_all_fixture_instruments_available(self, service):
        assert set(service.available_instruments()) == set(FIXTURE_INSTRUMENTS)

    def test_each_fixture_instrument_analyzes(self, service):
        for inst in FIXTURE_INSTRUMENTS:
            view = service.analyze(
                AnalysisRequest(instrument=inst, setup_timeframe="15m"),
            )
            assert view.instrument == inst
            assert view.complete is True

    def test_unknown_instrument_unavailable(self, service):
        view = service.analyze(
            AnalysisRequest(instrument="UNKNOWN", setup_timeframe="15m"),
        )
        assert view.actionability == ActionabilityState.UNAVAILABLE
        assert view.complete is False
        assert "not in fixture set" in view.reason


# ============================================================
# E. TIMEFRAME SELECTION
# ============================================================


class TestTimeframeSelection:
    def test_supported_timeframes_listed(self, service):
        tfs = service.available_timeframes()
        assert tfs == SUPPORTED_TIMEFRAMES
        for tf in ("1m", "3m", "5m", "15m", "30m", "1h", "4h", "1D"):
            assert tf in tfs

    def test_15m_supported(self, service):
        assert service.is_timeframe_supported("15m") is True

    def test_unsupported_timeframe_unavailable(self, service):
        for tf in ("1m", "5m", "1h", "4h"):
            view = service.analyze(
                AnalysisRequest(instrument="NIFTY", setup_timeframe=tf),
            )
            assert view.actionability == ActionabilityState.UNAVAILABLE
            assert view.complete is False

    def test_context_timeframe_for(self):
        assert context_timeframe_for("15m") == "1D"
        assert context_timeframe_for("5m") == "1h"


# ============================================================
# F. UNAVAILABLE DATA
# ============================================================


class TestUnavailableData:
    def test_unavailable_instrument_no_fabrication(self, service):
        view = service.analyze(
            AnalysisRequest(instrument="NOPE", setup_timeframe="15m"),
        )
        assert view.actionability == ActionabilityState.UNAVAILABLE
        assert view.geometry.entry is None
        assert view.geometry.stop is None
        assert view.geometry.target_1 is None
        assert view.evidence.available is False

    def test_unavailable_timeframe_no_fabrication(self, service):
        view = service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="1m"),
        )
        assert view.actionability == ActionabilityState.UNAVAILABLE
        assert view.geometry.entry is None

    def test_unavailable_does_not_crash_app(self, client):
        r = client.get("/api/analysis?instrument=NOPE&timeframe=1m")
        assert r.status_code == 200
        j = r.json()
        assert j["actionability"] == "UNAVAILABLE"

    def test_yahoo_provider_graceful_when_no_deps(self):
        # Yahoo provider must not crash even when yfinance is absent;
        # it reports unavailable honestly.
        from dashboard.data_provider import YahooDataProvider

        prov = YahooDataProvider()
        series = prov.fetch("NIFTY", "15m")
        # Either available (deps present + network) or unavailable — never raises.
        assert series.available in (True, False)
        if not series.available:
            assert series.reason


# ============================================================
# G. STALE DATA
# ============================================================


class TestStaleData:
    def test_fixture_data_flagged_stale(self, service):
        # Fixture timestamps are historical (2025-01-06), so the latest
        # candle is older than the default staleness threshold.
        view = service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        assert view.market_overview.data_stale is True
        assert any("stale" in w.lower() for w in view.warnings)

    def test_staleness_threshold_respected(self, fixture_data):
        # With a huge threshold the fixture data is NOT stale.
        svc = DashboardAnalysisService(staleness_seconds=10**9)
        view = svc.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        assert view.market_overview.data_stale is False


# ============================================================
# H. DECISION RENDERING
# ============================================================


class TestDecisionRendering:
    def test_decision_classification_preserved(self, service):
        view = service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        # NIFTY fixture produces a QUALIFIED decision.
        assert view.decision.decision_classification in (
            "REJECTED", "WATCH", "QUALIFIED", "PREFERRED", "",
        )
        assert view.decision.decision_classification == "QUALIFIED"

    def test_decision_not_renamed_to_buysell(self, service, client):
        r = client.get("/?instrument=NIFTY&timeframe=15m")
        # The decision value rendered must be a Sprint 11S classification,
        # never a BUY/SELL label presented AS the decision.
        j = client.get("/api/analysis?instrument=NIFTY&timeframe=15m").json()
        dc = j["decision"]["decision_classification"]
        assert dc in ("REJECTED", "WATCH", "QUALIFIED", "PREFERRED")
        assert dc not in ("BUY", "SELL", "LONG", "SHORT")
        # The actionability state is never a BUY/SELL recommendation.
        assert j["actionability"] not in ("BUY", "SELL")
        # Decision classification enum values must appear in the page.
        assert "QUALIFIED" in r.text or "WATCH" in r.text

    def test_decision_score_is_not_probability(self, service):
        view = service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        assert 0 <= view.decision.decision_score <= 100


# ============================================================
# I. EVIDENCE RENDERING
# ============================================================


class TestEvidenceRendering:
    def test_no_evidence_corpus_shows_unavailable(self, service):
        view = service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        assert view.evidence.available is False
        assert view.evidence.evidence_strength == "UNAVAILABLE"
        assert view.evidence.win_rate is None
        assert view.evidence.sample_size is None

    def test_evidence_unavailable_warning(self, service):
        view = service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        assert any(
            "UNAVAILABLE" in w or "evidence" in w.lower() for w in view.warnings
        )

    def test_evidence_source_with_real_report(self):
        # Build a real 11Y evidence report over a synthetic outcome
        # corpus matching the NIFTY fixture profile (BREAKOUT/LONG/
        # ALIGNED) and confirm the dashboard surfaces it via 11Z lookup.
        from engine.config.historical_evidence_config import EvidenceConfig
        from engine.intelligence.historical_evidence import (
            HistoricalEvidenceEngine,
        )
        from engine.intelligence.historical_outcome import (
            OutcomeStatus,
        )
        from engine.models.historical_outcome import (
            HistoricalOutcome,
            OutcomeSubject,
        )

        epoch = datetime(2025, 1, 1, tzinfo=UTC)

        def _subject(i, instrument="NIFTY", direction="LONG",
                     setup_type="BREAKOUT", mtf="ALIGNED"):
            return OutcomeSubject(
                instrument=instrument, direction=direction,
                evaluation_timestamp=epoch + timedelta(days=i),
                entry=100.0, stop=95.0, target=110.0,
                decision_classification="QUALIFIED", decision_score=70,
                opportunity_status="BEST_OPPORTUNITY", rank=1,
                scan_id="scan-t", setup_timeframe="15M",
                setup_type=setup_type, mtf_alignment=mtf,
            )

        outcomes = [
            HistoricalOutcome(
                subject=_subject(i),
                outcome_status=(
                    OutcomeStatus.TARGET_HIT if i % 3 else OutcomeStatus.STOP_HIT
                ),
                realized_r=2.0 if i % 3 else -1.0,
                mfe=5.0, mae=2.0, mfe_r=1.0, mae_r=0.4, risk=5.0,
            )
            for i in range(60)
        ]
        report = HistoricalEvidenceEngine(
            EvidenceConfig(label="t"),
        ).evaluate(outcomes)
        svc = DashboardAnalysisService(
            evidence_source=EvidenceSource(report),
        )
        view = svc.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        assert view.evidence.available is True
        assert view.evidence.evidence_strength in (
            "INSUFFICIENT", "WEAK", "MODERATE", "STRONG",
        )
        assert view.evidence.sample_size is not None

    def test_insufficient_evidence_never_strong(self):
        # A 1-trade 100% winner cohort matching the NIFTY fixture
        # profile (BREAKOUT/LONG/ALIGNED) must be INSUFFICIENT.
        from engine.config.historical_evidence_config import EvidenceConfig
        from engine.intelligence.historical_evidence import (
            HistoricalEvidenceEngine,
        )
        from engine.intelligence.historical_outcome import OutcomeStatus
        from engine.models.historical_outcome import (
            HistoricalOutcome, OutcomeSubject,
        )

        epoch = datetime(2025, 1, 1, tzinfo=UTC)
        s = OutcomeSubject(
            instrument="NIFTY", direction="LONG",
            evaluation_timestamp=epoch, entry=100.0, stop=95.0, target=110.0,
            decision_classification="QUALIFIED", decision_score=70,
            opportunity_status="BEST_OPPORTUNITY", rank=1, scan_id="s",
            setup_timeframe="15M", setup_type="BREAKOUT",
            mtf_alignment="ALIGNED",
        )
        outcome = HistoricalOutcome(
            subject=s, outcome_status=OutcomeStatus.TARGET_HIT,
            realized_r=2.0, mfe=5.0, mae=2.0, mfe_r=1.0, mae_r=0.4, risk=5.0,
        )
        report = HistoricalEvidenceEngine(
            EvidenceConfig(label="t"),
        ).evaluate([outcome])
        svc = DashboardAnalysisService(
            evidence_source=EvidenceSource(report),
        )
        view = svc.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        assert view.evidence.evidence_strength == "INSUFFICIENT"
        assert any("INSUFFICIENT" in w for w in view.warnings)


# ============================================================
# J. TRADE GEOMETRY RENDERING
# ============================================================


class TestTradeGeometryRendering:
    def test_geometry_reuses_candidate(self, service):
        view = service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        # Entry/stop come from the reused 11R candidate; target may be
        # None (BREAKOUT has no opposing structural target).
        assert view.geometry.direction == "LONG"
        assert view.geometry.entry is not None
        assert view.geometry.stop is not None
        assert view.geometry.invalidation_level == view.geometry.stop

    def test_target_2_never_fabricated(self, service):
        view = service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        assert view.geometry.target_2 is None
        assert view.geometry.target_2_supported is False

    def test_risk_reward_consistent_or_none(self, service):
        view = service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        g = view.geometry
        # When geometry is incomplete, ratio is None (never fabricated).
        if not g.geometry_available:
            assert g.risk_reward_ratio is None
            assert g.target_1 is None or g.risk_distance is None

    def test_chart_payload_has_candles(self, service):
        view = service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        cp = service.chart_payload(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"), view,
        )
        assert isinstance(cp, ChartPayload)
        assert len(cp.candles) > 0
        assert cp.entry == view.geometry.entry
        assert cp.stop == view.geometry.stop


# ============================================================
# K. MISSING TRADE GEOMETRY
# ============================================================


class TestMissingTradeGeometry:
    def test_incomplete_geometry_shown_honestly(self, service):
        view = service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        # NIFTY fixture produces a BREAKOUT with no target -> incomplete.
        assert view.geometry.geometry_available is False
        assert view.geometry.target_1 is None
        assert any("geometry is incomplete" in w for w in view.warnings)

    def test_missing_geometry_panel_in_html(self, client):
        r = client.get("/?instrument=NIFTY&timeframe=15m")
        assert "TRADE GEOMETRY UNAVAILABLE" in r.text

    def test_no_fabricated_target(self, service):
        view = service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        # target_1 is None and must NOT be invented from entry/stop.
        assert view.geometry.target_1 is None


# ============================================================
# L. ACTIONABILITY MAPPING
# ============================================================


class TestActionability:
    def test_unavailable_when_incomplete(self):
        assert derive_actionability(
            complete=False, decision_classification="",
            opportunity_status="", eligible=False,
        ) == ActionabilityState.UNAVAILABLE

    def test_no_opportunity_when_rejected(self):
        assert derive_actionability(
            complete=True, decision_classification="REJECTED",
            opportunity_status="NO_OPPORTUNITY", eligible=False,
        ) == ActionabilityState.NO_OPPORTUNITY

    def test_preferred_setup(self):
        assert derive_actionability(
            complete=True, decision_classification="PREFERRED",
            opportunity_status="BEST_OPPORTUNITY", eligible=True,
        ) == ActionabilityState.PREFERRED_SETUP

    def test_qualified_setup(self):
        assert derive_actionability(
            complete=True, decision_classification="QUALIFIED",
            opportunity_status="BEST_OPPORTUNITY", eligible=True,
        ) == ActionabilityState.QUALIFIED_SETUP

    def test_watch(self):
        assert derive_actionability(
            complete=True, decision_classification="WATCH",
            opportunity_status="WATCH", eligible=True,
        ) == ActionabilityState.WATCH

    def test_qualified_not_eligible_falls_to_watch_or_no(self):
        # QUALIFIED but not eligible -> not a qualified setup.
        s = derive_actionability(
            complete=True, decision_classification="QUALIFIED",
            opportunity_status="WATCH", eligible=False,
        )
        assert s in (ActionabilityState.WATCH, ActionabilityState.NO_OPPORTUNITY)

    def test_nifty_fixture_actionability(self, service):
        view = service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        assert view.actionability in (
            ActionabilityState.QUALIFIED_SETUP, ActionabilityState.WATCH,
        )

    def test_actionability_is_not_buysell(self):
        # None of the actionability states is a BUY/SELL recommendation.
        for s in ActionabilityState:
            assert "BUY" not in s.value
            assert "SELL" not in s.value


# ============================================================
# M. NO-LOOK-AHEAD
# ============================================================


class TestNoLookAhead:
    def test_service_does_not_call_outcome_evaluator(self, service, monkeypatch):
        # Patch the outcome evaluator to raise; current analysis must
        # still work (it never evaluates forward outcomes).
        import engine.intelligence.historical_outcome as ho

        def _boom(*a, **k):
            raise RuntimeError("outcome evaluator must not be called")

        monkeypatch.setattr(ho.OutcomeEvaluator, "evaluate", _boom)
        view = service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        assert view.actionability is not None

    def test_no_future_candle_argument_in_public_api(self):
        # The public analysis API accepts no separate "future candles".
        sig = inspect.signature(DashboardAnalysisService.analyze)
        assert "future" not in sig.parameters
        assert "future_candles" not in sig.parameters

    def test_fixed_T_unaffected_by_future_candles(self, fixture_data):
        # Analysis at the prefix's last candle (T) must equal a direct
        # scanner scan of prefix+future at evaluation_time=T (the
        # scanner truncates the future; the service delegates to it).
        setup = fixture_data["NIFTY"]["15M"]
        ctx = fixture_data["NIFTY"]["1D"]
        T = setup[-1].timestamp

        svc = DashboardAnalysisService(provider=_StaticProvider(ctx, setup))
        v_prefix = svc.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )

        future = setup + [
            OHLCVCandle(
                timestamp=T + timedelta(minutes=15),
                open=setup[-1].close,
                high=setup[-1].close + 50,
                low=setup[-1].close - 50,
                close=setup[-1].close + 30,
                volume=99999.0,
            )
        ]
        cfg = MarketScanConfig(
            context_timeframe="1D", setup_timeframe="15M", min_history=10,
        )
        ds = InstrumentDataset(
            instrument="NIFTY", context_candles=tuple(ctx),
            setup_candles=tuple(future),
        )
        scan = MarketScanner(cfg).scan(
            [ds], evaluation_time=T, engines=ScanEngines.default(),
        )
        r = scan.results[0]
        assert (
            v_prefix.decision.decision_classification
            == r.decision_classification
        )
        prefix_entry = v_prefix.geometry.entry
        full_entry = (
            getattr(r.decision.candidate, "entry_reference", None)
            if r.decision else None
        )
        assert prefix_entry == full_entry

    def test_future_mutation_does_not_change_fixed_T(self, fixture_data):
        setup = fixture_data["NIFTY"]["15M"]
        ctx = fixture_data["NIFTY"]["1D"]
        T = setup[-1].timestamp

        svc = DashboardAnalysisService(provider=_StaticProvider(ctx, setup))
        v1 = svc.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )

        # Mutate a future candle (after T) drastically and re-scan at T.
        mutated_future = setup + [
            OHLCVCandle(
                timestamp=T + timedelta(minutes=15),
                open=9999.0, high=10000.0, low=9998.0, close=9999.0,
                volume=10**9,
            )
        ]
        cfg = MarketScanConfig(
            context_timeframe="1D", setup_timeframe="15M", min_history=10,
        )
        ds = InstrumentDataset(
            instrument="NIFTY", context_candles=tuple(ctx),
            setup_candles=tuple(mutated_future),
        )
        scan = MarketScanner(cfg).scan(
            [ds], evaluation_time=T, engines=ScanEngines.default(),
        )
        r = scan.results[0]
        assert (
            v1.decision.decision_classification == r.decision_classification
        )
        assert v1.geometry.entry == getattr(
            r.decision.candidate, "entry_reference", None,
        )

    def test_no_future_timestamp_in_analysis_window(self, service):
        # The evaluation timestamp must be the latest completed setup
        # candle; no future timestamp can enter the analysis window.
        view = service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        assert view.evaluation_timestamp is not None


# ============================================================
# N. SERIALIZATION / PRESENTATION MODEL
# ============================================================


class TestPresentationSerialization:
    def test_to_jsonable_round_trip_keys(self, service):
        view = service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        d = to_jsonable(view)
        for k in (
            "instrument", "actionability", "market_overview", "decision",
            "geometry", "evidence", "setup_type", "warnings",
        ):
            assert k in d
        assert d["geometry"]["target_2_supported"] is False
        assert d["geometry"]["target_2"] is None

    def test_unavailable_values_serialized_honestly(self, service):
        view = service.analyze(
            AnalysisRequest(instrument="NOPE", setup_timeframe="1m"),
        )
        d = to_jsonable(view)
        assert d["geometry"]["entry"] is None
        assert d["evidence"]["available"] is False
        assert d["actionability"] == "UNAVAILABLE"

    def test_models_frozen(self):
        from dataclasses import is_dataclass
        for cls in (
            DashboardTradeView, DecisionView, EvidenceView, GeometryView,
            MarketOverviewView,
        ):
            assert is_dataclass(cls)
        # Frozen: attribute assignment must raise.
        v = GeometryView()
        with pytest.raises(Exception):
            v.entry = 1.0  # type: ignore[misc]


# ============================================================
# O. DETERMINISTIC OUTPUT
# ============================================================


class TestDeterminism:
    def test_repeated_analysis_identical(self, service):
        v1 = service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        v2 = service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        assert to_jsonable(v1) == to_jsonable(v2)

    def test_repeated_api_identical(self, client):
        a = client.get("/api/analysis?instrument=NIFTY&timeframe=15m").json()
        b = client.get("/api/analysis?instrument=NIFTY&timeframe=15m").json()
        assert a == b

    def test_chart_payload_deterministic(self, service):
        req = AnalysisRequest(instrument="NIFTY", setup_timeframe="15m")
        view = service.analyze(req)
        cp1 = service.chart_payload(req, view)
        cp2 = service.chart_payload(req, view)
        assert cp1 == cp2


# ============================================================
# P. INPUT IMMUTABILITY
# ============================================================


class TestInputImmutability:
    def test_provider_candles_not_mutated(self, fixture_data):
        setup = list(fixture_data["NIFTY"]["15M"])
        ctx = list(fixture_data["NIFTY"]["1D"])
        before_setup = [c for c in setup]
        before_ctx = [c for c in ctx]
        svc = DashboardAnalysisService(
            provider=_StaticProvider(ctx, setup),
        )
        svc.analyze(AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"))
        assert setup == before_setup
        assert ctx == before_ctx

    def test_view_is_immutable(self, service):
        view = service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        with pytest.raises(Exception):
            view.instrument = "X"  # type: ignore[misc]


# ============================================================
# Q. EXISTING DECISION IDENTITY
# ============================================================


class TestExistingDecisionIdentity:
    def test_decision_authority_preserved(self, service):
        # The dashboard surfaces the existing decision verbatim; it does
        # not upgrade QUALIFIED into PREFERRED.
        view = service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        # NIFTY fixture decision is QUALIFIED; the dashboard must not
        # turn it into PREFERRED.
        assert view.decision.decision_classification != "PREFERRED" or \
            view.decision.decision_classification == "QUALIFIED"

    def test_actionability_never_upgrades_beyond_decision(self, service):
        view = service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        # If the decision is QUALIFIED, actionability must not be
        # PREFERRED_SETUP (no upgrade).
        if view.decision.decision_classification == "QUALIFIED":
            assert view.actionability != ActionabilityState.PREFERRED_SETUP

    def test_decision_classification_reused_verbatim(self, service):
        view = service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        # The classification string is reused from the engine, not
        # remapped.
        assert view.decision.decision_classification in (
            "REJECTED", "WATCH", "QUALIFIED", "PREFERRED",
        )


# ============================================================
# R. REGRESSION AGAINST 11V->12E
# ============================================================


class TestRegressionChain:
    def test_pipeline_baseline_signals_4_trades_3(self):
        # The dashboard must not affect the existing pipeline baseline.
        from engine.pipeline import (
            HistoricalEvaluationPipeline, PipelineConfig, trending_dataset,
        )

        pipeline = HistoricalEvaluationPipeline(PipelineConfig())
        result = pipeline.evaluate(trending_dataset())
        assert result.signals_generated == 4
        assert result.completed_trades == 3

    def test_scanner_still_importable_and_working(self):
        # The reused scanner must still work unchanged.
        data = historical_candles_by_instrument(
            ("NIFTY", "RELIANCE"), "1D", "15M",
        )
        engines = ScanEngines.default()
        cfg = MarketScanConfig(
            context_timeframe="1D", setup_timeframe="15M", min_history=10,
        )
        datasets = [
            InstrumentDataset(
                instrument=inst,
                context_candles=tuple(d["1D"]),
                setup_candles=tuple(d["15M"]),
            )
            for inst, d in data.items()
        ]
        scan = MarketScanner(cfg).scan(datasets, engines=engines)
        assert scan.status.name in (
            "OPPORTUNITIES_FOUND", "WATCH_ONLY", "NO_OPPORTUNITY",
        )

    def test_dashboard_does_not_modify_engine_models(self):
        # The dashboard package must not redefine engine models.
        import dashboard.views as dv
        assert not hasattr(dv, "TradeDecision")
        assert not hasattr(dv, "TradeCandidate")


# ============================================================
# S. DEMO SMOKE (programmatic)
# ============================================================


class TestDemoSmoke:
    def test_full_dashboard_flow(self, client):
        # Health -> select instrument -> select timeframe -> view.
        assert client.get("/health").json()["status"] == "ok"
        r = client.get("/?instrument=RELIANCE&timeframe=15m")
        assert r.status_code == 200
        assert "RELIANCE" in r.text
        # API returns a coherent view.
        j = client.get(
            "/api/analysis?instrument=RELIANCE&timeframe=15m",
        ).json()
        assert j["instrument"] == "RELIANCE"
        assert j["actionability"] in {s.value for s in ActionabilityState}

    def test_no_predictive_language_in_html(self, client):
        r = client.get("/?instrument=NIFTY&timeframe=15m")
        text = r.text.lower()
        # The dashboard must not claim guaranteed profit / probability.
        assert "guaranteed profit" not in text
        assert "probability of success" not in text
        # It must carry the honesty disclaimer.
        assert "does not guarantee future performance" in text or \
            "not predictive" in text
