"""
Dashboard demo / smoke test (productization phase).

Proves the web dashboard is a THIN, honest presentation layer over the
existing trading-intelligence-engine. It visibly demonstrates:

1.  Dashboard starts (FastAPI app builds).
2.  Health endpoint works.
3.  Instrument can be selected.
4.  Timeframe can be selected.
5.  Existing decision is displayed (authoritative, not renamed).
6.  Evidence is displayed (honest unavailable when no corpus).
7.  Valid trade geometry is displayed when available.
8.  Missing geometry is handled honestly (no fabrication).
9.  No-look-ahead test passes.
10. Existing pipeline remains unchanged (signals=4, trades=3).

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

    # 3. Instrument selection.
    _banner("3. Instrument selection")
    for inst in ("NIFTY", "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK"):
        view = service.analyze(
            AnalysisRequest(instrument=inst, setup_timeframe="15m"),
        )
        _check(f"{inst} analyzes", view.instrument == inst)

    # 4. Timeframe selection.
    _banner("4. Timeframe selection")
    v15 = service.analyze(
        AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"),
    )
    _check("15m supported", v15.actionability != ActionabilityState.UNAVAILABLE)
    v1m = service.analyze(
        AnalysisRequest(instrument="NIFTY", setup_timeframe="1m"),
    )
    _check(
        "1m honestly unavailable",
        v1m.actionability == ActionabilityState.UNAVAILABLE,
    )

    # 5. Existing decision displayed (authoritative).
    _banner("5. Existing decision displayed")
    j = client.get("/api/analysis?instrument=NIFTY&timeframe=15m").json()
    dc = j["decision"]["decision_classification"]
    print(f"  NIFTY decision={dc} score={j['decision']['decision_score']}")
    _check("decision is a Sprint 11S classification", dc in (
        "REJECTED", "WATCH", "QUALIFIED", "PREFERRED",
    ))
    _check("decision not renamed to BUY/SELL", dc not in ("BUY", "SELL"))

    # 6. Evidence displayed (honest unavailable).
    _banner("6. Evidence displayed")
    ev = j["evidence"]
    print(
        f"  evidence available={ev['available']} "
        f"strength={ev['evidence_strength']}",
    )
    _check(
        "evidence honestly unavailable (no corpus)",
        ev["available"] is False and ev["evidence_strength"] == "UNAVAILABLE",
    )
    _check("no fabricated win rate", ev["win_rate"] is None)

    # 7. Valid trade geometry displayed when available.
    _banner("7. Trade geometry displayed")
    g = j["geometry"]
    print(
        f"  direction={g['direction']} entry={g['entry']} stop={g['stop']} "
        f"target_1={g['target_1']} rr={g['risk_reward_ratio']} "
        f"geom_available={g['geometry_available']}",
    )
    _check("entry reused from engine", g["entry"] is not None)
    _check("stop reused from engine", g["stop"] is not None)
    _check("invalidation == stop (honest reuse)", g["invalidation_level"] == g["stop"])
    _check("target_2 never fabricated", g["target_2"] is None)
    _check(
        "target_2 not supported (documented)",
        g["target_2_supported"] is False,
    )

    # 8. Missing geometry handled honestly.
    _banner("8. Missing geometry handled honestly")
    # NIFTY fixture BREAKOUT has no target -> geometry_available False.
    _check(
        "incomplete geometry flagged (no fabricated target)",
        g["geometry_available"] is False or g["target_1"] is not None,
    )
    _check(
        "warnings mention incomplete geometry or no evidence",
        any("geometry" in w.lower() or "evidence" in w.lower() for w in j["warnings"]),
    )
    html = client.get("/?instrument=NIFTY&timeframe=15m").text
    _check(
        "HTML shows TRADE GEOMETRY UNAVAILABLE when incomplete",
        "TRADE GEOMETRY UNAVAILABLE" in html
        or g["geometry_available"] is True,
    )

    # 9. No-look-ahead.
    _banner("9. No-look-ahead")
    # 9a. Public API has no future-candles argument.
    sig = inspect.signature(DashboardAnalysisService.analyze)
    _check(
        "analyze() has no future-candles argument",
        not any("future" in p for p in sig.parameters),
    )
    # 9b. Service does not call the outcome evaluator.
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

    # 9c. Fixed-T analysis unaffected by future candles.
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

    # 10. Existing pipeline unchanged.
    _banner("10. Existing pipeline unchanged")
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

    # Summary.
    _banner("Summary")
    passed = sum(1 for _, s in _CHECKS if s == "PASS")
    failed = sum(1 for _, s in _CHECKS if s == "FAIL")
    print(f"  {passed} PASS / {failed} FAIL / {len(_CHECKS)} total")
    print()
    print(
        "Dashboard results are DESCRIPTIVE ONLY. They do NOT predict "
        "future market behavior and do NOT guarantee profitability. "
        "The existing decision engine remains authoritative.",
    )

    if failed:
        print("\nSprint dashboard demo FAILED.")
        return 1
    print("\nSprint dashboard demo completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
