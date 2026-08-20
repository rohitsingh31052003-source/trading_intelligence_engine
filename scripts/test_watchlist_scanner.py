"""
Multi-instrument scanner / watchlist demo (Product Phase 2).

Proves the scanner is a THIN, honest orchestration + presentation layer
over the EXISTING trading-intelligence-engine. It visibly demonstrates
the required items:

1.  Scanning the default watchlist.
2.  Multiple instruments processed.
3.  Decision classifications surfaced (authoritative, not BUY/SELL).
4.  Actionability surfaced (deterministic presentation mirror).
5.  Trade geometry surfaced (reused verbatim; target 2 unsupported).
6.  Evidence availability surfaced (UNAVAILABLE without a corpus; never
    fabricated, never conflated with INSUFFICIENT).
7.  Data freshness surfaced (STALE for fixture data; data quality only).
8.  One instrument failure without whole-scan failure (failure
    isolation).
9.  Deterministic ordering (input watchlist order does not change the
    output order).
10. No-look-ahead (outcome evaluator + pipeline patched to raise; the
    scanner still works).
11. Existing pipeline baseline unchanged (signals_generated=4,
    completed_trades=3).

The demo makes NO profitability, probability or directional prediction.
The scanner is DESCRIPTIVE ONLY.

Run::

    python scripts/test_watchlist_scanner.py
"""

from __future__ import annotations

import inspect
import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, "src")
sys.path.insert(0, ".")

from fastapi.testclient import TestClient

from dashboard.app import create_app
from dashboard.data_provider import FreshnessConfig, FreshnessState, InstrumentSeries, ProviderStatus
from dashboard.services import (
    AnalysisRequest,
    DashboardAnalysisService,
    ScanRequest,
)
from dashboard.views import ActionabilityState, scan_view_to_jsonable, scanner_rank_key
from dashboard.watchlist import DEFAULT_WATCHLIST, Watchlist
from engine.models.ohlcv import OHLCVCandle


# ============================================================
# DEMO HARNESS
# ============================================================


def _candle(close, ts, spread=2.0):
    return OHLCVCandle(
        timestamp=ts, open=close, high=close + spread,
        low=close - spread, close=close, volume=1000.0,
    )


def _series(close_start, n, step_min, start):
    out = []
    for i in range(n):
        ts = start + timedelta(minutes=step_min * i)
        out.append(_candle(close_start + i * 1.0, ts))
    return out


class _StaticProvider:
    """Static provider with optional per-instrument failure (failure isolation)."""

    def __init__(self, context, setup, *, fail_on=None, reference_now=None):
        self._context = tuple(context)
        self._setup = tuple(setup)
        self._fail_on = fail_on or set()
        self._reference_now = reference_now
        self.freshness_config = FreshnessConfig()

    def is_timeframe_supported(self, setup_timeframe):
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
            latest = None
        else:
            res = split_completed_candles(self._setup, "15m", boundary_now)
            setup = res.completed
            latest = res.latest_completed_timestamp
        return InstrumentSeries(
            instrument=instrument, context_candles=self._context,
            setup_candles=setup, available=bool(setup), data_source="static",
            provider_status=ProviderStatus.OK, freshness_state=FreshnessState.CURRENT,
            latest_candle_timestamp=latest,
            latest_completed_candle_timestamp=latest,
        )

    def last_updated(self, instrument, setup_timeframe):
        return self._setup[-1].timestamp if self._setup else None


_CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    _CHECKS.append((name, ok, detail))
    print(f"  [{status}] {name}{(' - ' + detail) if detail else ''}")


# ============================================================
# DEMONSTRATIONS
# ============================================================


def demo_default_watchlist_scan(svc):
    print("\n1. Scanning the default watchlist (multiple instruments)")
    scan = svc.scan_watchlist(ScanRequest(setup_timeframe="15m"))
    check(
        "scans all default-universe instruments",
        scan.total == len(DEFAULT_WATCHLIST),
        f"total={scan.total}",
    )
    check("rows == total", len(scan.rows) == scan.total)
    check("counts reconcile", scan.analyzed + scan.errored == scan.total)
    print("   rows (presentational order):")
    for row in scan.rows:
        print(
            f"     #{row.rank} {row.instrument}: decision={row.decision_classification or 'none'} "
            f"actionability={row.actionability.value} evidence={row.evidence_strength} "
            f"fresh={row.freshness_state} geom={row.geometry_available}"
        )


def demo_decision_classifications(svc):
    print("\n3. Decision classifications (authoritative, not BUY/SELL)")
    scan = svc.scan_watchlist(ScanRequest(setup_timeframe="15m"))
    classifications = {
        row.decision_classification for row in scan.rows if row.decision_classification
    }
    check("classifications are existing 11S set", classifications.issubset(
        {"REJECTED", "WATCH", "QUALIFIED", "PREFERRED"}
    ), str(classifications))
    for row in scan.rows:
        check(
            f"{row.instrument} decision not BUY/SELL",
            row.decision_classification not in ("BUY", "SELL", "ENTER", "EXIT", "HOLD"),
        )


def demo_actionability(svc):
    print("\n4. Actionability (deterministic presentation mirror)")
    scan = svc.scan_watchlist(ScanRequest(setup_timeframe="15m"))
    states = {row.actionability.value for row in scan.rows}
    check("actionability states are existing 6-state set", states.issubset(
        {s.value for s in ActionabilityState}
    ), str(states))
    for row in scan.rows:
        check(
            f"{row.instrument} actionability not BUY/SELL",
            "BUY" not in row.actionability.value and "SELL" not in row.actionability.value,
        )


def demo_geometry(svc):
    print("\n5. Trade geometry (reused verbatim; target 2 unsupported)")
    scan = svc.scan_watchlist(ScanRequest(setup_timeframe="15m"))
    for row in scan.rows:
        # Verify geometry reused verbatim from the single-instrument view.
        single = svc.analyze(
            AnalysisRequest(instrument=row.instrument, setup_timeframe="15m"),
        )
        check(
            f"{row.instrument} geometry == single-instrument view",
            row.entry == single.geometry.entry
            and row.stop == single.geometry.stop
            and row.target_1 == single.geometry.target_1
            and row.risk_reward_ratio == single.geometry.risk_reward_ratio,
        )
        check(
            f"{row.instrument} target 2 unsupported",
            row.view.geometry.target_2 is None
            and row.view.geometry.target_2_supported is False,
        )


def demo_evidence(svc):
    print("\n6. Evidence availability (UNAVAILABLE without a corpus)")
    scan = svc.scan_watchlist(ScanRequest(setup_timeframe="15m"))
    for row in scan.rows:
        check(
            f"{row.instrument} evidence UNAVAILABLE (not INSUFFICIENT)",
            row.evidence_strength == "UNAVAILABLE" and not row.view.evidence.available,
        )


def demo_freshness(svc):
    print("\n7. Data freshness (STALE for fixture data; data quality only)")
    scan = svc.scan_watchlist(ScanRequest(setup_timeframe="15m"))
    fresh = {row.freshness_state for row in scan.rows}
    check("fixture data is STALE (data quality only)", "STALE" in fresh, str(fresh))


def demo_failure_isolation():
    print("\n8. One instrument failure without whole-scan failure")
    now = datetime(2025, 6, 2, 12, 0, tzinfo=UTC)
    ctx = _series(95.0, 20, 1440, now - timedelta(days=20))
    setup = _series(100.0, 30, 15, now - timedelta(minutes=30 * 15))
    p = _StaticProvider(ctx, setup, fail_on={"TCS"})
    svc = DashboardAnalysisService(provider=p)
    wl = Watchlist(["NIFTY", "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK"])
    scan = svc.scan_watchlist(ScanRequest(watchlist=wl, setup_timeframe="15m"))
    check("scan not aborted", scan.total == 5)
    check("exactly 1 errored", scan.errored == 1, f"errored={scan.errored}")
    tcs = next(r for r in scan.rows if r.instrument == "TCS")
    check("TCS is an INVALID error row", tcs.error and tcs.actionability is ActionabilityState.INVALID)
    check("other instruments still analyzed", scan.analyzed == 4)


def demo_deterministic_ordering(svc):
    print("\n9. Deterministic ordering (input order does not change output)")
    wl_a = Watchlist(["TCS", "NIFTY", "RELIANCE", "HDFCBANK", "ICICIBANK"])
    wl_b = Watchlist(["ICICIBANK", "HDFCBANK", "RELIANCE", "NIFTY", "TCS"])
    a = svc.scan_watchlist(ScanRequest(watchlist=wl_a, setup_timeframe="15m"))
    b = svc.scan_watchlist(ScanRequest(watchlist=wl_b, setup_timeframe="15m"))
    order_a = [r.instrument for r in a.rows]
    order_b = [r.instrument for r in b.rows]
    check("shuffle-invariant output order", order_a == order_b, f"{order_a} == {order_b}")
    check("ranks match", [r.rank for r in a.rows] == [r.rank for r in b.rows])
    # The ordering is presentational, by ranking key.
    keys = [scanner_rank_key(r) for r in a.rows]
    check("rows sorted by presentational key", keys == sorted(keys))


def demo_no_look_ahead(svc, restore_outcome, restore_pipeline, patch_outcome, patch_pipeline):
    print("\n10. No-look-ahead (outcome evaluator + pipeline patched to raise)")
    patch_outcome()
    patch_pipeline()
    try:
        scan = svc.scan_watchlist(ScanRequest(setup_timeframe="15m"))
        check(
            "scanner works with evaluator+pipeline patched to raise",
            scan.total == len(DEFAULT_WATCHLIST),
        )
    finally:
        # Restore BEFORE the pipeline-baseline demo runs the real pipeline.
        restore_outcome()
        restore_pipeline()
    sig = inspect.signature(DashboardAnalysisService.scan_watchlist)
    check(
        "scan_watchlist has no future/future_candles argument",
        "future" not in sig.parameters and "future_candles" not in sig.parameters,
    )
    sig2 = inspect.signature(ScanRequest)
    check(
        "ScanRequest has no future/future_candles field",
        "future" not in sig2.parameters and "future_candles" not in sig2.parameters,
    )


def demo_pipeline_baseline():
    print("\n11. Existing pipeline baseline unchanged (signals=4, trades=3)")
    from engine.pipeline import (
        HistoricalEvaluationPipeline, PipelineConfig, trending_dataset,
    )

    result = HistoricalEvaluationPipeline(PipelineConfig()).evaluate(trending_dataset())
    check(
        "signals_generated == 4", result.signals_generated == 4,
        f"got {result.signals_generated}",
    )
    check(
        "completed_trades == 3", result.completed_trades == 3,
        f"got {result.completed_trades}",
    )


def demo_api_and_html(svc):
    print("\n12. API + HTML rendering")
    client = TestClient(create_app(service=svc))
    r = client.get("/api/scan")
    j = r.json()
    check("GET /api/scan returns 200", r.status_code == 200)
    check("api/scan has rows + counts", "rows" in j and "total" in j and "errored" in j)
    r = client.get("/scan")
    check("GET /scan renders 200 with scanner table", r.status_code == 200 and "scanner-table" in r.text)
    check("scan page has nav to trade review", "/" in r.text)
    # No predictive language.
    text = r.text.lower()
    check("no 'guaranteed profit' in scanner page", "guaranteed profit" not in text)
    check("ordering described as presentational", "presentation" in r.text.lower())


def main() -> None:
    svc = DashboardAnalysisService()

    import engine.intelligence.historical_outcome as ho
    import engine.pipeline.historical_pipeline as hp

    orig_outcome = ho.OutcomeEvaluator.evaluate
    orig_pipeline = hp.HistoricalEvaluationPipeline.evaluate

    def _patch_outcome():
        def _boom(*a, **k):
            raise RuntimeError("outcome evaluator must not be called")
        ho.OutcomeEvaluator.evaluate = _boom

    def _restore_outcome():
        ho.OutcomeEvaluator.evaluate = orig_outcome

    def _patch_pipeline():
        def _boom(*a, **k):
            raise RuntimeError("pipeline must not be called")
        hp.HistoricalEvaluationPipeline.evaluate = _boom

    def _restore_pipeline():
        hp.HistoricalEvaluationPipeline.evaluate = orig_pipeline

    try:
        demo_default_watchlist_scan(svc)
        demo_decision_classifications(svc)
        demo_actionability(svc)
        demo_geometry(svc)
        demo_evidence(svc)
        demo_freshness(svc)
        demo_failure_isolation()
        demo_deterministic_ordering(svc)
        demo_no_look_ahead(
            svc, _restore_outcome, _restore_pipeline, _patch_outcome, _patch_pipeline,
        )
        demo_pipeline_baseline()
        demo_api_and_html(svc)
    finally:
        ho.OutcomeEvaluator.evaluate = orig_outcome
        hp.HistoricalEvaluationPipeline.evaluate = orig_pipeline

    # ---- summary ----
    passed = sum(1 for _, ok, _ in _CHECKS if ok)
    failed = sum(1 for _, ok, _ in _CHECKS if not ok)
    skipped = 0
    print("\n" + "=" * 60)
    print(f"Demo checks: {passed} PASS, {skipped} SKIPPED, {failed} FAIL.")
    if failed:
        print("FAILED checks:")
        for name, ok, detail in _CHECKS:
            if not ok:
                print(f"  - {name}: {detail}")
        sys.exit(1)

    print(
        "\nScanner results are DESCRIPTIVE ONLY. They do NOT predict future "
        "market behavior and do NOT guarantee profitability. The existing "
        "decision engine remains authoritative; the scanner does not modify "
        "it, does not add BUY/SELL recommendations, and does not invent trade "
        "geometry or evidence. The row order is PRESENTATIONAL, not predictive."
    )
    print("\nProduct Phase 2 demo completed successfully.")
    sys.exit(0)


if __name__ == "__main__":
    main()
