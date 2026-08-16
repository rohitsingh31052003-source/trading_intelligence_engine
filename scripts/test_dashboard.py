"""
Dashboard trade-review demo / smoke test (productization increment).

Proves the web dashboard is a THIN, honest trade-review presentation
layer over the existing trading-intelligence-engine. It visibly
demonstrates the 16 required items:

1.  Dashboard starts (FastAPI app builds).
2.  Health endpoint works.
3.  Instrument list works.
4.  Supported timeframe works.
5.  Analysis endpoint works.
6.  Decision is displayed (authoritative, not renamed to BUY/SELL).
7.  Entry is displayed.
8.  Stop is displayed.
9.  Target 1 is displayed (or honestly unavailable).
10. R:R is displayed (or honestly unavailable).
11. Target 2 explicitly shows unsupported.
12. Geometry-unavailable scenario is honest (no fabrication).
13. Evidence-unavailable scenario is honest (no fabrication).
14. Unsupported timeframe is honest.
15. No-look-ahead check passes (fixed-T unaffected by future candles;
    outcome evaluator NOT called; pipeline NOT called).
16. Existing pipeline baseline remains: signals_generated = 4,
    completed_trades = 3.

The demo makes NO profitability, probability or directional prediction.
A dashboard view is DESCRIPTIVE ONLY.

Run::

    python scripts/test_dashboard.py
"""

from __future__ import annotations

import inspect
import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, "src")
sys.path.insert(0, ".")

from fastapi.testclient import TestClient

from dashboard.app import create_app
from dashboard.data_provider import FixtureDataProvider, InstrumentSeries
from dashboard.services import (
    AnalysisRequest,
    DashboardAnalysisService,
)
from dashboard.views import ActionabilityState, to_jsonable
from engine.config.market_scan_config import MarketScanConfig
from engine.data.historical_fixtures import historical_candles_by_instrument
from engine.intelligence.market_scanner import (
    InstrumentDataset,
    MarketScanner,
    ScanEngines,
)
from engine.models.ohlcv import OHLCVCandle
from engine.pipeline import (
    HistoricalEvaluationPipeline,
    PipelineConfig,
    trending_dataset,
)


_CHECKS: list[tuple[str, str]] = []


def _check(label: str, condition: bool) -> None:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    _CHECKS.append((label, status))


def _banner(title: str) -> None:
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


class _StaticProvider(FixtureDataProvider):
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


def main() -> int:
    service = DashboardAnalysisService()
    app = create_app(service=service)
    client = TestClient(app)

    # 1. Dashboard starts.
    _banner("1. Dashboard starts")
    _check("FastAPI app builds", app is not None)
    paths = {r.path for r in app.routes}
    _check("routes present", {"/", "/health", "/api/analysis"} <= paths)

    # 2. Health endpoint.
    _banner("2. Health endpoint")
    h = client.get("/health").json()
    print(f"  provider={h['provider']} instruments={len(h['instruments'])}")
    _check("health status ok", h["status"] == "ok")
    _check("instruments listed", "NIFTY" in h["instruments"])

    # 3. Instrument list.
    _banner("3. Instrument list")
    instr = client.get("/api/instruments").json()
    print(f"  instruments={instr['instruments']}")
    _check(
        "all fixture instruments present",
        set(instr["instruments"]) == {"NIFTY", "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK"},
    )

    # 4. Supported timeframe works.
    _banner("4. Supported timeframe")
    v15 = service.analyze(
        AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
    )
    _check("15m supported (not INVALID)", v15.actionability != ActionabilityState.INVALID)
    _check("15m scan complete", v15.complete is True)

    # 5. Analysis endpoint works.
    _banner("5. Analysis endpoint")
    j = client.get("/api/analysis?instrument=NIFTY&timeframe=15m").json()
    _check("analysis returns instrument", j["instrument"] == "NIFTY")
    _check("separated concerns present", all(
        k in j for k in (
            "market_overview", "decision", "geometry", "trade_geometry",
            "evidence", "actionability", "actionability_detail",
        )
    ))
    _check("trade_geometry mirrors geometry", j["trade_geometry"] == j["geometry"])

    # 6. Decision displayed (authoritative).
    _banner("6. Decision displayed")
    dc = j["decision"]["decision_classification"]
    print(f"  NIFTY decision={dc} score={j['decision']['decision_score']}")
    _check("decision is a Sprint 11S classification", dc in (
        "REJECTED", "WATCH", "QUALIFIED", "PREFERRED",
    ))
    _check("decision not renamed to BUY/SELL", dc not in ("BUY", "SELL"))
    _check("actionability not BUY/SELL", j["actionability"] not in ("BUY", "SELL"))

    # 7. Entry displayed.
    _banner("7. Entry displayed")
    g = j["geometry"]
    print(f"  entry={g['entry']} stop={g['stop']} target_1={g['target_1']}")
    _check("entry reused from engine (NIFTY has entry)", g["entry"] is not None)

    # 8. Stop displayed.
    _banner("8. Stop displayed")
    _check("stop reused from engine (NIFTY has stop)", g["stop"] is not None)
    _check("invalidation == stop (honest reuse)", g["invalidation_level"] == g["stop"])

    # 9. Target 1 displayed (or honestly unavailable).
    _banner("9. Target 1")
    # NIFTY fixture is a BREAKOUT with no opposing structural target.
    print(f"  target_1={g['target_1']} geometry_available={g['geometry_available']}")
    _check(
        "target_1 honestly None when unavailable (never fabricated as 0)",
        g["target_1"] is None or g["target_1"] > 0,
    )

    # 10. R:R displayed (or honestly unavailable).
    _banner("10. Risk / Reward")
    rr = g["risk_reward_ratio"]
    print(f"  risk={g['risk_distance']} reward={g['reward_distance']} rr={rr}")
    _check(
        "R:R honestly None when unavailable (never 0)",
        rr is None or rr > 0,
    )

    # 11. Target 2 explicitly unsupported.
    _banner("11. Target 2 unsupported")
    _check("target_2 never fabricated", g["target_2"] is None)
    _check("target_2 not supported (documented)", g["target_2_supported"] is False)
    html = client.get("/?instrument=NIFTY&timeframe=15m").text
    _check("HTML states Target 2 not supported", "Not supported" in html)

    # 12. Geometry-unavailable scenario honest.
    _banner("12. Geometry-unavailable honest")
    _check(
        "NIFTY geometry incomplete flagged (no fabricated target)",
        g["geometry_available"] is False or g["target_1"] is not None,
    )
    _check(
        "HTML shows TRADE GEOMETRY UNAVAILABLE when incomplete",
        "TRADE GEOMETRY UNAVAILABLE" in html
        or g["geometry_available"] is True,
    )
    # HDFCBANK is REJECTED -> NO_OPPORTUNITY, no geometry.
    v_hdfc = service.analyze(
        AnalysisRequest(instrument="HDFCBANK", setup_timeframe="15m"),
    )
    _check(
        "HDFCBANK no geometry (entry None)",
        v_hdfc.geometry.entry is None,
    )

    # 13. Evidence-unavailable scenario honest.
    _banner("13. Evidence-unavailable honest")
    ev = j["evidence"]
    print(f"  evidence available={ev['available']} strength={ev['evidence_strength']}")
    _check(
        "evidence honestly unavailable (no corpus)",
        ev["available"] is False and ev["evidence_strength"] == "UNAVAILABLE",
    )
    _check("no fabricated win rate", ev["win_rate"] is None)

    # 14. Unsupported timeframe honest.
    _banner("14. Unsupported timeframe honest")
    v1m = service.analyze(
        AnalysisRequest(instrument="NIFTY", setup_timeframe="1m"),
    )
    _check(
        "1m honestly INVALID (no fabricated data)",
        v1m.actionability == ActionabilityState.INVALID,
    )
    j1m = client.get("/api/analysis?instrument=NIFTY&timeframe=5m").json()
    _check("5m API honest INVALID", j1m["actionability"] == "INVALID")

    # 15. No-look-ahead.
    _banner("15. No-look-ahead")
    # 15a. Public API has no future-candles argument.
    sig = inspect.signature(DashboardAnalysisService.analyze)
    _check(
        "analyze() has no future-candles argument",
        not any("future" in p for p in sig.parameters),
    )
    # 15b. Outcome evaluator NOT called during current analysis.
    import engine.intelligence.historical_outcome as ho

    orig = ho.OutcomeEvaluator.evaluate
    called = {"v": False}

    def _spy(*a, **k):
        called["v"] = True
        return orig(*a, **k)

    ho.OutcomeEvaluator.evaluate = _spy
    service.analyze(AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"))
    ho.OutcomeEvaluator.evaluate = orig
    _check("outcome evaluator NOT called during current analysis", not called["v"])
    # 15c. Pipeline NOT called during current analysis.
    import engine.pipeline.historical_pipeline as hp

    orig_pipe = hp.HistoricalEvaluationPipeline.evaluate
    pipe_called = {"v": False}

    def _spy_pipe(*a, **k):
        pipe_called["v"] = True
        return orig_pipe(*a, **k)

    hp.HistoricalEvaluationPipeline.evaluate = _spy_pipe
    service.analyze(AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"))
    hp.HistoricalEvaluationPipeline.evaluate = orig_pipe
    _check("pipeline NOT called during current analysis", not pipe_called["v"])
    # 15d. Fixed-T analysis unaffected by future candles (entry/stop/decision).
    data = historical_candles_by_instrument(("NIFTY",), "1D", "15M")
    setup = data["NIFTY"]["15M"]
    ctx = data["NIFTY"]["1D"]
    T = setup[-1].timestamp
    svc_fixed = DashboardAnalysisService(
        provider=_StaticProvider(ctx, setup),
    )
    v_prefix = svc_fixed.analyze(
        AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
    )
    future = setup + [
        OHLCVCandle(
            timestamp=T + timedelta(minutes=15),
            open=setup[-1].close, high=setup[-1].close + 50,
            low=setup[-1].close - 50, close=setup[-1].close + 30,
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
    _check(
        "fixed-T decision unaffected by future candles",
        v_prefix.decision.decision_classification == r.decision_classification,
    )
    _check(
        "fixed-T entry unaffected by future candles",
        v_prefix.geometry.entry
        == getattr(r.decision.candidate, "entry_reference", None),
    )
    _check(
        "fixed-T stop unaffected by future candles",
        v_prefix.geometry.stop
        == getattr(r.decision.candidate, "stop_reference", None),
    )

    # 16. Existing pipeline unchanged.
    _banner("16. Existing pipeline unchanged")
    pipeline = HistoricalEvaluationPipeline(PipelineConfig())
    result = pipeline.evaluate(trending_dataset())
    _check("pipeline signals_generated == 4", result.signals_generated == 4)
    _check("pipeline completed_trades == 3", result.completed_trades == 3)

    # Determinism bonus.
    _banner("Determinism")
    v_a = service.analyze(
        AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
    )
    v_b = service.analyze(
        AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
    )
    _check("repeated analysis identical", to_jsonable(v_a) == to_jsonable(v_b))
    _check(
        "actionability_detail carries state + reason",
        bool(v_a.actionability_detail.state) and bool(v_a.actionability_detail.reason),
    )

    # Summary.
    _banner("Summary")
    passed = sum(1 for _, s in _CHECKS if s == "PASS")
    failed = sum(1 for _, s in _CHECKS if s == "FAIL")
    print(f"  {passed} PASS / {failed} FAIL / {len(_CHECKS)} total")
    print()
    print(
        "Dashboard results are DESCRIPTIVE ONLY. They do NOT predict "
        "future market behavior and do NOT guarantee profitability. "
        "The existing decision engine remains authoritative; the dashboard "
        "does not modify it, does not add BUY/SELL recommendations, and "
        "does not invent trade geometry or evidence.",
    )

    if failed:
        print("\nDashboard trade-review demo FAILED.")
        return 1
    print("\nDashboard trade-review demo completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
