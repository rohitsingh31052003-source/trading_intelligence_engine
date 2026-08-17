"""
Live trading workstation demo (Product Phase 3).

Proves the workstation is a THIN, honest orchestration + presentation
layer that bundles the EXISTING watchlist scanner + single-instrument
trade review into one coherent view. It visibly demonstrates the
required items:

1.  Workstation starts.
2.  Health works.
3.  Scanner works.
4.  Instrument selection.
5.  Live / near-live provider metadata.
6.  Freshness.
7.  Completed-candle analysis.
8.  Decision (authoritative, not BUY/SELL).
9.  Actionability (deterministic presentation mirror).
10. Trade geometry (reused verbatim; target 2 unsupported).
11. Evidence (UNAVAILABLE without a corpus; never fabricated).
12. Chart payload (backend-authored).
13. Refresh (deliberate, manual; no background polling).
14. Unsupported timeframe handling.
15. Provider failure handling (failure isolation).
16. No-look-ahead (outcome evaluator + pipeline patched to raise).
17. Decision authority (REJECTED/WATCH/QUALIFIED/PREFERRED preserved).
18. Pipeline baseline unchanged (signals_generated=4, completed_trades=3).

The workstation is DESCRIPTIVE ONLY. It does NOT predict, does NOT
guarantee profitability, and does NOT constitute a trading
recommendation. The existing decision engine remains authoritative.

Run::

    python scripts/test_workstation.py
"""

from __future__ import annotations

import inspect
import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, "src")
sys.path.insert(0, ".")

from fastapi.testclient import TestClient

from dashboard.app import create_app
from dashboard.data_provider import (
    FreshnessConfig,
    FreshnessState,
    InstrumentSeries,
    ProviderStatus,
    split_completed_candles,
)
from dashboard.services import (
    AnalysisRequest,
    DashboardAnalysisService,
    WorkstationRequest,
)
from dashboard.views import (
    ActionabilityState,
    workstation_view_to_jsonable,
    workstation_why,
)
from dashboard.watchlist import Watchlist
from engine.models.ohlcv import OHLCVCandle


# ============================================================
# DEMO HARNESS
# ============================================================


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
    """Static provider with optional per-instrument failure."""

    def __init__(
        self,
        context,
        setup,
        *,
        fail_on=None,
        reference_now=None,
        freshness=FreshnessState.CURRENT,
        provider_status=ProviderStatus.OK,
        data_source="static",
    ):
        self._context = tuple(context)
        self._setup = tuple(setup)
        self._fail_on = fail_on or set()
        self._reference_now = reference_now
        self._freshness = freshness
        self._provider_status = provider_status
        self._data_source = data_source
        self.freshness_config = FreshnessConfig()

    def is_timeframe_supported(self, setup_timeframe: str) -> bool:
        return True

    def fetch(self, instrument, setup_timeframe, lookback_bars=300):
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
            data_source=self._data_source,
            provider_status=self._provider_status,
            freshness_state=self._freshness,
            latest_candle_timestamp=latest_completed,
            latest_completed_candle_timestamp=latest_completed,
        )

    def last_updated(self, instrument, setup_timeframe):
        return self._setup[-1].timestamp if self._setup else None


def _fixture_service() -> DashboardAnalysisService:
    from dashboard.data_provider import FixtureDataProvider

    return DashboardAnalysisService(provider=FixtureDataProvider())


# ============================================================
# DEMONSTRATIONS
# ============================================================


def demo_workstation_starts(client: TestClient) -> None:
    _banner("1. Workstation starts")
    r = client.get("/workstation")
    _check("GET /workstation returns 200", r.status_code == 200)
    _check("page contains 'Trading Workstation'", "Trading Workstation" in r.text)
    _check(
        "page contains watchlist status section",
        "Market / Watchlist Status" in r.text,
    )
    _check(
        "page contains selected instrument section",
        "Selected Instrument" in r.text,
    )


def demo_health(client: TestClient) -> None:
    _banner("2. Health works")
    r = client.get("/health")
    j = r.json()
    _check("health status ok", j["status"] == "ok")
    _check("health lists instruments", len(j["instruments"]) > 0)
    _check("health lists timeframes", len(j["timeframes"]) > 0)


def demo_scanner(client: TestClient) -> None:
    _banner("3. Scanner works")
    r = client.get("/api/scan")
    j = r.json()
    _check("scanner returns rows", len(j["rows"]) > 0)
    _check("scanner row order present", all("rank" in row for row in j["rows"]))


def demo_instrument_selection(client: TestClient) -> None:
    _banner("4. Instrument selection")
    r = client.get("/api/workstation", params={"instrument": "NIFTY"})
    j = r.json()
    _check("selected instrument is NIFTY", j["selected_instrument"] == "NIFTY")
    _check("has_selected is True", j["has_selected"] is True)
    # Unknown instrument falls back deterministically.
    r2 = client.get("/api/workstation", params={"instrument": "ZZZZ"})
    j2 = r2.json()
    _check(
        "unknown instrument falls back (not ZZZZ)",
        j2["selected_instrument"] != "ZZZZ",
    )


def demo_provider_metadata(client: TestClient) -> None:
    _banner("5. Live / near-live provider metadata")
    r = client.get("/api/workstation", params={"instrument": "NIFTY"})
    j = r.json()
    ds = j["selected_view"]["data_source"]
    _check("data_source field present", bool(ds["data_source"]))
    _check("provider_status field present", bool(ds["provider_status"]))
    _check(
        "latest_completed_candle_timestamp present",
        bool(ds["latest_completed_candle_timestamp"]),
    )


def demo_freshness(client: TestClient) -> None:
    _banner("6. Freshness")
    r = client.get("/api/workstation", params={"instrument": "NIFTY"})
    j = r.json()
    ds = j["selected_view"]["data_source"]
    _check("freshness_state present", bool(ds["freshness_state"]))
    _check(
        "freshness is data-quality vocabulary",
        ds["freshness_state"] in {"CURRENT", "STALE", "UNAVAILABLE", "INVALID"},
    )


def demo_completed_candle_analysis(client: TestClient) -> None:
    _banner("7. Completed-candle analysis")
    r = client.get("/api/workstation", params={"instrument": "NIFTY"})
    j = r.json()
    ds = j["selected_view"]["data_source"]
    _check(
        "refresh_token == latest completed candle timestamp",
        j["refresh_token"] == ds["latest_completed_candle_timestamp"],
    )
    _check(
        "evaluation anchored to completed candle",
        bool(j["refresh_token"]),
    )


def demo_decision(client: TestClient) -> None:
    _banner("8. Decision (authoritative, not BUY/SELL)")
    r = client.get("/api/workstation", params={"instrument": "NIFTY"})
    j = r.json()
    dc = j["selected_view"]["decision"]["decision_classification"]
    _check(
        "decision classification is existing vocabulary",
        dc in {"", "REJECTED", "WATCH", "QUALIFIED", "PREFERRED"},
    )
    _check(
        "decision never BUY/SELL/ENTER/EXIT/HOLD",
        dc not in {"BUY", "SELL", "ENTER", "EXIT", "HOLD"},
    )
    _check(
        "html marks decision authoritative",
        "authoritative" in client.get("/workstation").text.lower(),
    )


def demo_actionability(client: TestClient) -> None:
    _banner("9. Actionability (deterministic presentation mirror)")
    r = client.get("/api/workstation", params={"instrument": "NIFTY"})
    j = r.json()
    a = j["selected_view"]["actionability"]
    _check(
        "actionability is existing state vocabulary",
        a in {s.value for s in ActionabilityState},
    )
    _check(
        "actionability not a BUY/SELL recommendation",
        a not in {"BUY", "SELL", "ENTER", "EXIT", "HOLD"},
    )


def demo_trade_geometry(client: TestClient) -> None:
    _banner("10. Trade geometry (reused verbatim; target 2 unsupported)")
    r = client.get("/api/workstation", params={"instrument": "NIFTY"})
    j = r.json()
    geom = j["selected_view"]["geometry"]
    _check("target_2 is None", geom["target_2"] is None)
    _check("target_2_supported is False", geom["target_2_supported"] is False)
    # Geometry values match the single-instrument analysis (reused).
    svc = _fixture_service()
    single = svc.analyze(AnalysisRequest(instrument="NIFTY", setup_timeframe="15m"))
    _check(
        "entry matches single analysis",
        geom["entry"] == single.geometry.entry,
    )
    _check(
        "stop matches single analysis",
        geom["stop"] == single.geometry.stop,
    )
    _check(
        "target_1 matches single analysis",
        geom["target_1"] == single.geometry.target_1,
    )


def demo_evidence(client: TestClient) -> None:
    _banner("11. Evidence (UNAVAILABLE without a corpus; never fabricated)")
    r = client.get("/api/workstation", params={"instrument": "NIFTY"})
    j = r.json()
    ev = j["selected_view"]["evidence"]
    _check("evidence not available (no corpus)", ev["available"] is False)
    _check(
        "evidence strength UNAVAILABLE (not INSUFFICIENT)",
        ev["evidence_strength"] == "UNAVAILABLE",
    )
    _check("win_rate not fabricated (None)", ev["win_rate"] is None)


def demo_chart_payload(client: TestClient) -> None:
    _banner("12. Chart payload (backend-authored)")
    r = client.get("/api/analysis", params={"instrument": "NIFTY"})
    j = r.json()
    chart = j["chart"]
    _check("chart has candles", len(chart["candles"]) > 0)
    _check(
        "chart entry matches geometry entry",
        chart["entry"] == j["geometry"]["entry"],
    )
    _check(
        "chart stop matches geometry stop",
        chart["stop"] == j["geometry"]["stop"],
    )


def demo_refresh(client: TestClient) -> None:
    _banner("13. Refresh (deliberate, manual; no background polling)")
    r = client.get("/workstation", params={"instrument": "NIFTY"})
    _check("refresh button present", 'name="refresh"' in r.text)
    _check("no setInterval polling", "setInterval" not in r.text)
    _check("no WebSocket connection", "new WebSocket" not in r.text)
    # Refresh token is deterministic for the same data.
    svc = _fixture_service()
    a = svc.workstation(WorkstationRequest(instrument="NIFTY", setup_timeframe="15m"))
    b = svc.workstation(WorkstationRequest(instrument="NIFTY", setup_timeframe="15m"))
    _check("refresh token deterministic", a.refresh_token == b.refresh_token)


def demo_unsupported_timeframe(client: TestClient) -> None:
    _banner("14. Unsupported timeframe handling")
    r = client.get("/workstation", params={"timeframe": "3m"})
    _check("unsupported timeframe does not crash (200)", r.status_code == 200)
    r2 = client.get("/api/workstation", params={"timeframe": "3m"})
    j2 = r2.json()
    if j2["has_selected"]:
        _check(
            "unsupported timeframe -> INVALID actionability",
            j2["selected_view"]["actionability"] == "INVALID",
        )
    else:
        _check("unsupported timeframe -> no selection", True)


def demo_provider_failure() -> None:
    _banner("15. Provider failure handling (failure isolation)")
    setup = _series(100, 60, 15, datetime(2025, 1, 25, 5, 0, tzinfo=UTC))
    ctx = _series(100, 30, 1440, datetime(2025, 1, 1, tzinfo=UTC))
    provider = _StaticProvider(context=ctx, setup=setup, fail_on={"RELIANCE"})
    svc = DashboardAnalysisService(provider=provider)
    wv = svc.workstation(
        WorkstationRequest(
            setup_timeframe="15m",
            watchlist=Watchlist(["NIFTY", "RELIANCE", "TCS"]),
        ),
    )
    _check("scan total 3", wv.scan.total == 3)
    _check("scan errored 1 (RELIANCE)", wv.scan.errored == 1)
    _check("scan analyzed 2", wv.scan.analyzed == 2)
    _check("workstation still has selected view", wv.has_selected)
    failed = [r for r in wv.scan.rows if r.instrument == "RELIANCE"]
    _check(
        "failed row is INVALID (not fabricated)",
        failed and failed[0].actionability is ActionabilityState.INVALID,
    )


def demo_no_look_ahead() -> None:
    _banner("16. No-look-ahead (outcome evaluator + pipeline patched to raise)")
    from engine.intelligence import historical_outcome as ho
    from engine.pipeline import historical_pipeline as hp

    orig_outcome = ho.OutcomeEvaluator.evaluate
    orig_pipeline = hp.HistoricalEvaluationPipeline.evaluate

    def _boom_outcome(*a, **k):
        raise RuntimeError("outcome evaluator must not be called")

    def _boom_pipeline(*a, **k):
        raise RuntimeError("pipeline must not be called")

    ho.OutcomeEvaluator.evaluate = _boom_outcome
    hp.HistoricalEvaluationPipeline.evaluate = _boom_pipeline
    try:
        svc = _fixture_service()
        wv = svc.workstation(
            WorkstationRequest(instrument="NIFTY", setup_timeframe="15m"),
        )
        _check("workstation works with evaluator patched", wv.has_selected)
        _check("workstation works with pipeline patched", wv.has_selected)
    finally:
        ho.OutcomeEvaluator.evaluate = orig_outcome
        hp.HistoricalEvaluationPipeline.evaluate = orig_pipeline

    # Public API accepts no future / future_candles argument.
    sig = set(inspect.signature(DashboardAnalysisService.workstation).parameters)
    _check("no future argument in workstation()", "future" not in sig)
    _check("no future_candles argument in workstation()", "future_candles" not in sig)


def demo_decision_authority(client: TestClient) -> None:
    _banner("17. Decision authority (REJECTED/WATCH/QUALIFIED/PREFERRED preserved)")
    # Each fixture instrument's decision classification must be a valid
    # existing vocabulary member; never renamed to BUY/SELL.
    r = client.get("/api/workstation")
    j = r.json()
    valid = {"", "REJECTED", "WATCH", "QUALIFIED", "PREFERRED"}
    all_ok = True
    for row in j["scan"]["rows"]:
        dc = row["decision_classification"]
        if dc not in valid or dc in {"BUY", "SELL", "ENTER", "EXIT", "HOLD"}:
            all_ok = False
            break
    _check("all rows use existing decision vocabulary", all_ok)
    sv = j["selected_view"]
    _check(
        "selected decision in existing vocabulary",
        sv["decision"]["decision_classification"] in valid,
    )


def demo_pipeline_baseline() -> None:
    _banner("18. Pipeline baseline unchanged (signals=4, trades=3)")
    from engine.pipeline import HistoricalEvaluationPipeline, PipelineConfig, trending_dataset

    candles = trending_dataset()
    result = HistoricalEvaluationPipeline(PipelineConfig()).evaluate(candles)
    _check("signals_generated == 4", result.signals_generated == 4)
    _check("completed_trades == 3", result.completed_trades == 3)


# ============================================================
# MAIN
# ============================================================


def main() -> None:
    print("Product Phase 3 — Live Trading Workstation demo")
    svc = _fixture_service()
    client = TestClient(create_app(service=svc))

    try:
        demo_workstation_starts(client)
        demo_health(client)
        demo_scanner(client)
        demo_instrument_selection(client)
        demo_provider_metadata(client)
        demo_freshness(client)
        demo_completed_candle_analysis(client)
        demo_decision(client)
        demo_actionability(client)
        demo_trade_geometry(client)
        demo_evidence(client)
        demo_chart_payload(client)
        demo_refresh(client)
        demo_unsupported_timeframe(client)
        demo_provider_failure()
        demo_no_look_ahead()
        demo_decision_authority(client)
        demo_pipeline_baseline()
    finally:
        pass

    # ---- summary ----
    passed = sum(1 for _, s in _CHECKS if s == "PASS")
    failed = sum(1 for _, s in _CHECKS if s == "FAIL")
    print("\n" + "=" * 60)
    print(f"Demo checks: {passed} PASS / {failed} FAIL / {len(_CHECKS)} total")
    if failed:
        print("FAILED checks:")
        for label, status in _CHECKS:
            if status == "FAIL":
                print(f"  - {label}")
        sys.exit(1)

    print(
        "\nThe live trading workstation is DESCRIPTIVE ONLY. It does NOT "
        "predict future market behavior, does NOT guarantee profitability, "
        "and does NOT constitute a trading recommendation. The existing "
        "decision classification (REJECTED / WATCH / QUALIFIED / PREFERRED) "
        "is authoritative and is never renamed to BUY/SELL or upgraded / "
        "downgraded. Refresh is a deliberate manual action; there is no "
        "background polling or WebSocket streaming. The analysis always "
        "uses the latest COMPLETED candle. Risk management, broker "
        "integration, order execution, paper trading and portfolio "
        "management are intentionally out of scope (later product phases)."
    )
    print("\nProduct Phase 3 demo completed successfully.")
    sys.exit(0)


if __name__ == "__main__":
    main()
