"""
FastAPI application factory for the trading-intelligence dashboard.

The app is a THIN HTTP layer over :class:`DashboardAnalysisService`. It
holds no intelligence of its own. Every response is built from the
existing engine outputs via the service.

Run locally::

    python -m uvicorn dashboard.app:app --reload

or::

    python -m dashboard.app

The dashboard is DESCRIPTIVE ONLY. It does NOT guarantee future
performance, does NOT constitute a trading recommendation, and does NOT
modify the existing decision / scoring logic.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from dashboard.data_provider import make_provider
from dashboard.paper_trade_store import (
    PaperTradeStore,
    default_paper_trade_directory,
)
from dashboard.services import (
    AnalysisRequest,
    DashboardAnalysisService,
    EvidenceSource,
    OperationalTradeIntentRequest,
    PaperTradeManualCloseRequest,
    PaperTradeRequest,
    PaperTradeTrackRequest,
    ScanRequest,
    TradePlanRequest,
    WorkstationRequest,
    default_service,
)
from dashboard.views import (
    operational_trade_intent_view_to_jsonable,
    operations_cycle_view_to_jsonable,
    paper_trade_journal_view_to_jsonable,
    paper_trade_view_to_jsonable,
    scan_view_to_jsonable,
    to_jsonable,
    trade_plan_view_to_jsonable,
    workstation_view_to_jsonable,
    workstation_why,
)
from dashboard.watchlist import Watchlist

_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATES_DIR = _BASE_DIR / "templates"
_STATIC_DIR = _BASE_DIR / "static"

#: Module-level default service. Built lazily so importing the module
#: (e.g. for OpenAPI schema generation) never requires the engine.
_DEFAULT_SERVICE: DashboardAnalysisService | None = None


def _register_filters(templates: Jinja2Templates) -> None:
    """Register the small presentation-only formatting filters."""

    from datetime import datetime

    env = templates.env

    def fmt_price(value):
        return "unavailable" if value is None else f"{value:.4f}"

    def fmt_ratio(value):
        return "unavailable" if value is None else f"{value:.2f}"

    def fmt_pct(value):
        return "unavailable" if value is None else f"{value * 100:.1f}%"

    def fmt_r(value):
        return "unavailable" if value is None else f"{value:.2f}"

    def fmt_money(value):
        # Decimal-aware money formatter for the trade-plan section.
        if value is None:
            return "unavailable"
        try:
            from decimal import Decimal
            if isinstance(value, Decimal):
                q = Decimal(1).scaleb(-2)
                return f"{value.quantize(q):,.2f}"
        except Exception:  # pragma: no cover - defensive
            pass
        try:
            return f"{float(value):,.2f}"
        except (TypeError, ValueError):
            return str(value)

    def fmt_ts(value):
        if value is None:
            return "unavailable"
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d %H:%M %Z").strip()
        return str(value)

    env.filters["fmt_price"] = fmt_price
    env.filters["fmt_ratio"] = fmt_ratio
    env.filters["fmt_pct"] = fmt_pct
    env.filters["fmt_r"] = fmt_r
    env.filters["fmt_money"] = fmt_money
    env.filters["fmt_ts"] = fmt_ts


def _build_default_service() -> DashboardAnalysisService:
    global _DEFAULT_SERVICE
    if _DEFAULT_SERVICE is None:
        provider_name = os.environ.get("DASHBOARD_PROVIDER", "fixture")
        evidence_path = os.environ.get("DASHBOARD_EVIDENCE_PATH", "")
        evidence_report = None
        if evidence_path:
            try:
                evidence_report = _load_evidence_report(evidence_path)
            except Exception:  # pragma: no cover - env dependent
                evidence_report = None
        # Product Phase 5 — paper-trade persistence. Defaults to a local
        # ``./paper_trades`` directory (overridable via DASHBOARD_PAPER_TRADE_DIR).
        # When the env var is explicitly empty, persistence is disabled.
        pt_dir_env = os.environ.get("DASHBOARD_PAPER_TRADE_DIR", "")
        if pt_dir_env == "":
            pt_dir_env = str(default_paper_trade_directory())
        paper_trade_store = PaperTradeStore(directory=pt_dir_env)
        _DEFAULT_SERVICE = default_service(
            provider_name=provider_name,
            evidence_report=evidence_report,
            paper_trade_store=paper_trade_store,
        )
    return _DEFAULT_SERVICE


def _load_evidence_report(path: str):  # pragma: no cover - env dependent
    from engine.intelligence.historical_evidence_serialization import (
        deserialize_evidence,
    )
    with open(path, "r", encoding="utf-8") as fh:
        return deserialize_evidence(fh.read())


def set_service(service: DashboardAnalysisService) -> None:
    """Inject a service (used by tests / demos)."""

    global _DEFAULT_SERVICE
    _DEFAULT_SERVICE = service


def create_app(service: DashboardAnalysisService | None = None) -> FastAPI:
    """
    Build the FastAPI application.

    ``service`` lets tests inject a configured service. When omitted the
    module-level default service is used (built from the
    ``DASHBOARD_PROVIDER`` env var, defaulting to the deterministic
    fixture provider).
    """

    app = FastAPI(
        title="Trading Intelligence Dashboard",
        description=(
            "A descriptive productization layer over the trading-"
            "intelligence-engine. Provides technical-analysis and "
            "historical research context only. Does NOT guarantee future "
            "performance and does NOT constitute a trading recommendation."
        ),
        version="0.1.0",
    )
    app.mount(
        "/static", StaticFiles(directory=str(_STATIC_DIR)), name="static",
    )
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    _register_filters(templates)

    def _service() -> DashboardAnalysisService:
        return service or _build_default_service()

    # ------------------------------------------------------------
    # HEALTH
    # ------------------------------------------------------------

    @app.get("/health", response_class=JSONResponse)
    def health() -> dict:
        svc = _service()
        provider = svc.provider
        data_source = getattr(provider, "data_source", "") or type(
            provider,
        ).__name__
        # The provider's own supported timeframes (honest capability
        # reporting), when available.
        supported_timeframes: list[str] = []
        try:
            supported_timeframes = [
                tf for tf in svc.available_timeframes()
                if svc.is_timeframe_supported(tf)
            ]
        except Exception:  # pragma: no cover - defensive
            supported_timeframes = []
        return {
            "status": "ok",
            "provider": type(provider).__name__,
            "data_source": data_source,
            "instruments": list(svc.available_instruments()),
            "timeframes": list(svc.available_timeframes()),
            "supported_timeframes": supported_timeframes,
            "evidence_attached": svc.evidence_source is not None
            and svc.evidence_source.evidence_report is not None,
        }

    @app.get("/api/health", response_class=JSONResponse)
    def api_health() -> dict:
        return health()

    # ------------------------------------------------------------
    # DASHBOARD PAGE (HTML)
    # ------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def dashboard_page(
        request: Request,
        instrument: str = "",
        timeframe: str = "",
    ):
        svc = _service()
        instruments = svc.available_instruments()
        timeframes = svc.available_timeframes()
        # Defaults: first instrument + first supported timeframe.
        if not instrument:
            instrument = instruments[0] if instruments else ""
        if not timeframe:
            timeframe = "15m"
        view = None
        chart = None
        if instrument and timeframe:
            view = svc.analyze(
                AnalysisRequest(instrument=instrument, setup_timeframe=timeframe),
            )
            chart = svc.chart_payload(
                AnalysisRequest(instrument=instrument, setup_timeframe=timeframe),
                view,
            )
        ctx = {
            "request": request,
            "instruments": instruments,
            "timeframes": timeframes,
            "selected_instrument": instrument,
            "selected_timeframe": timeframe,
            "view": view,
            "view_json": to_jsonable(view) if view else None,
            "chart": chart,
            "chart_json": _chart_to_dict(chart) if chart else None,
        }
        return templates.TemplateResponse(request, "dashboard.html", ctx)

    # ------------------------------------------------------------
    # API (JSON)
    # ------------------------------------------------------------

    @app.get("/api/analysis", response_class=JSONResponse)
    def api_analysis(instrument: str, timeframe: str = "15m"):
        svc = _service()
        view = svc.analyze(
            AnalysisRequest(instrument=instrument, setup_timeframe=timeframe),
        )
        chart = svc.chart_payload(
            AnalysisRequest(instrument=instrument, setup_timeframe=timeframe),
            view,
        )
        payload = to_jsonable(view)
        payload["chart"] = _chart_to_dict(chart)
        return payload

    @app.get("/api/instruments", response_class=JSONResponse)
    def api_instruments():
        svc = _service()
        return {
            "instruments": list(svc.available_instruments()),
            "timeframes": list(svc.available_timeframes()),
        }

    # ------------------------------------------------------------
    # MULTI-INSTRUMENT SCANNER (Product Phase 2)
    # ------------------------------------------------------------

    @app.get("/scan", response_class=HTMLResponse)
    def scanner_page(
        request: Request,
        timeframe: str = "15m",
        instruments: str = "",
    ):
        """Multi-instrument scanner / watchlist view (Product Phase 2)."""

        svc = _service()
        if instruments:
            parsed = [i.strip() for i in instruments.split(",") if i.strip()]
            watchlist = Watchlist(parsed) if parsed else svc.default_watchlist()
        else:
            watchlist = svc.default_watchlist()
        scan = svc.scan_watchlist(
            ScanRequest(watchlist=watchlist, setup_timeframe=timeframe),
        )
        ctx = {
            "request": request,
            "timeframes": svc.available_timeframes(),
            "selected_timeframe": timeframe,
            "instruments_param": instruments,
            "scan": scan,
            "scan_json": scan_view_to_jsonable(scan),
        }
        return templates.TemplateResponse(request, "scanner.html", ctx)

    @app.get("/api/scan", response_class=JSONResponse)
    def api_scan(
        timeframe: str = "15m",
        instruments: str = "",
    ):
        """Structured JSON for the multi-instrument scanner (Product Phase 2)."""

        svc = _service()
        if instruments:
            parsed = [i.strip() for i in instruments.split(",") if i.strip()]
            watchlist = Watchlist(parsed) if parsed else svc.default_watchlist()
        else:
            watchlist = svc.default_watchlist()
        scan = svc.scan_watchlist(
            ScanRequest(watchlist=watchlist, setup_timeframe=timeframe),
        )
        return scan_view_to_jsonable(scan)

    # ------------------------------------------------------------
    # LIVE TRADING WORKSTATION (Product Phase 3)
    # ------------------------------------------------------------

    def _parse_watchlist(instruments: str, svc: DashboardAnalysisService):
        if instruments:
            parsed = [i.strip() for i in instruments.split(",") if i.strip()]
            return Watchlist(parsed) if parsed else svc.default_watchlist()
        return svc.default_watchlist()

    @app.get("/workstation", response_class=HTMLResponse)
    def workstation_page(
        request: Request,
        instrument: str = "",
        timeframe: str = "15m",
        instruments: str = "",
        account_capital: str = "",
        risk_percent: str = "",
    ):
        """Live trading workstation (Product Phase 3 + Phase 4 trade plan).

        Bundles the watchlist scan + the selected instrument's detailed
        trade review into one coherent view. Manual refresh only — no
        background polling. The analysis always uses the latest
        COMPLETED candle; no future candle is read.

        Product Phase 4: when the user supplies ``account_capital`` and
        ``risk_percent`` a deterministic RISK / TRADE PLAN is built from
        the existing current analysis' trade geometry (reused verbatim).
        The plan never modifies the existing decision / geometry and
        never produces a BUY/SELL recommendation.
        """

        svc = _service()
        watchlist = _parse_watchlist(instruments, svc)
        wv = svc.workstation(
            WorkstationRequest(
                instrument=instrument,
                setup_timeframe=timeframe,
                watchlist=watchlist,
            ),
        )
        # Build the chart payload for the selected instrument (reuses
        # the existing service chart_payload — backend-authored only).
        chart = None
        if wv.has_selected and wv.selected_view is not None:
            chart = svc.chart_payload(
                AnalysisRequest(
                    instrument=wv.selected_instrument,
                    setup_timeframe=timeframe,
                ),
                wv.selected_view,
            )
        # Build the optional risk / trade plan (Product Phase 4). Only
        # when the user supplied both account_capital and risk_percent.
        trade_plan_view = None
        trade_plan_json = None
        if (
            wv.has_selected
            and wv.selected_view is not None
            and account_capital.strip()
            and risk_percent.strip()
        ):
            try:
                trade_plan_view = svc.plan_trade(
                    TradePlanRequest(
                        instrument=wv.selected_instrument,
                        account_capital=account_capital.strip(),
                        risk_percent=risk_percent.strip(),
                        setup_timeframe=timeframe,
                    ),
                )
                trade_plan_json = trade_plan_view_to_jsonable(trade_plan_view)
            except Exception:  # noqa: BLE001 - failure isolation
                trade_plan_view = None
                trade_plan_json = None
        ctx = {
            "request": request,
            "timeframes": svc.available_timeframes(),
            "selected_timeframe": timeframe,
            "instruments_param": instruments,
            "workstation": wv,
            "view": wv.selected_view,
            "selected_instrument": wv.selected_instrument,
            "chart": chart,
            "chart_json": _chart_to_dict(chart) if chart else None,
            "workstation_json": workstation_view_to_jsonable(wv),
            "workstation_why": _workstation_why_text(wv),
            "account_capital": account_capital,
            "risk_percent": risk_percent,
            "trade_plan": trade_plan_view,
            "trade_plan_json": trade_plan_json,
            "last_cycle": getattr(svc, "last_operations_cycle", None),
        }
        return templates.TemplateResponse(request, "workstation.html", ctx)

    @app.get("/api/workstation", response_class=JSONResponse)
    def api_workstation(
        instrument: str = "",
        timeframe: str = "15m",
        instruments: str = "",
    ):
        """Structured JSON for the live trading workstation (Product Phase 3)."""

        svc = _service()
        watchlist = _parse_watchlist(instruments, svc)
        wv = svc.workstation(
            WorkstationRequest(
                instrument=instrument,
                setup_timeframe=timeframe,
                watchlist=watchlist,
            ),
        )
        return workstation_view_to_jsonable(wv)

    # ------------------------------------------------------------
    # RISK / TRADE PLAN (Product Phase 4)
    # ------------------------------------------------------------

    @app.get("/api/trade-plan", response_class=JSONResponse)
    def api_trade_plan(
        instrument: str,
        timeframe: str = "15m",
        account_capital: str = "",
        risk_percent: str = "",
    ):
        """Structured JSON for a risk / trade plan (Product Phase 4).

        Accepts ``instrument``, ``timeframe``, ``account_capital`` and
        ``risk_percent``. The plan reuses the EXISTING current analysis'
        trade geometry verbatim; it never accepts arbitrary entry / stop
        / target values (those would bypass the authoritative engine
        geometry). All inputs are validated; invalid inputs become an
        ``INVALID_INPUT`` plan (never a successful trade plan). The
        response never contains a BUY/SELL recommendation.
        """

        svc = _service()
        plan_view = svc.plan_trade(
            TradePlanRequest(
                instrument=instrument,
                account_capital=account_capital,
                risk_percent=risk_percent,
                setup_timeframe=timeframe,
            ),
        )
        return trade_plan_view_to_jsonable(plan_view)

    # ------------------------------------------------------------
    # OPERATIONAL TRADE INTENT (Checkpoint 14.5)
    # ------------------------------------------------------------

    @app.post("/api/operational-trade-intent", response_class=JSONResponse)
    def api_create_operational_trade_intent(
        instrument: str,
        account_capital: str,
        risk_percent: str,
        created_at: str,
        timeframe: str = "15m",
        context_timeframe: str | None = None,
        label: str = "",
    ):
        """Create an OperationalTradeIntent from the EXISTING current analysis.

        This is an EXPLICIT mutation/action endpoint. It is NOT a GET
        endpoint — intent creation requires an explicit POST action.

        Accepts ``instrument``, ``timeframe``, ``account_capital``,
        ``risk_percent``, and ``created_at`` (ISO 8601, timezone-aware).
        The intent reuses the EXISTING current analysis' trade geometry +
        the Phase 4 trade plan VERBATIM; it never accepts arbitrary
        entry / stop / target values. All inputs are validated; invalid
        inputs result in a 400 response (never a successful intent).

        The created intent is an immutable operational snapshot/reference
        of the TradePlan. It is NOT authorization, NOT execution, NOT a
        paper trade. The response never contains a BUY/SELL recommendation.
        """

        from datetime import datetime

        svc = _service()
        try:
            created_at_dt = datetime.fromisoformat(created_at)
            if created_at_dt.tzinfo is None:
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": "invalid_created_at",
                        "detail": "created_at must be timezone-aware "
                        "(ISO 8601 with offset).",
                    },
                )
        except (ValueError, TypeError) as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "invalid_created_at",
                    "detail": f"created_at must be a valid ISO 8601 "
                    f"timestamp: {exc}",
                },
            )
        try:
            intent_view = svc.create_operational_trade_intent(
                OperationalTradeIntentRequest(
                    instrument=instrument,
                    account_capital=account_capital,
                    risk_percent=risk_percent,
                    created_at=created_at_dt,
                    setup_timeframe=timeframe,
                    context_timeframe=context_timeframe,
                    label=label,
                ),
            )
        except (ValueError, TypeError) as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "intent_creation_failed",
                    "detail": str(exc),
                },
            )
        return operational_trade_intent_view_to_jsonable(intent_view)

    # ------------------------------------------------------------
    # PAPER TRADING (Product Phase 5)
    # ------------------------------------------------------------

    @app.get("/paper-trading", response_class=HTMLResponse)
    def paper_trading_page(request: Request):
        """Paper-trading journal + performance view (Product Phase 5).

        Lists all persisted paper trades (ordered by id) + the descriptive
        performance analytics. A human creates paper trades deliberately
        from the workstation; this page is the review / validation
        surface. No automatic trading; no BUY/SELL recommendation.
        """

        svc = _service()
        journal = svc.paper_trade_journal(include_performance=True)
        ctx = {
            "request": request,
            "journal": journal,
            "journal_json": paper_trade_journal_view_to_jsonable(journal),
        }
        return templates.TemplateResponse(request, "paper_trading.html", ctx)

    @app.get("/api/paper-trades", response_class=JSONResponse)
    def api_paper_trades():
        """Structured JSON for the paper-trading journal + performance."""

        svc = _service()
        journal = svc.paper_trade_journal(include_performance=True)
        return paper_trade_journal_view_to_jsonable(journal)

    @app.get("/api/paper-trades/{paper_trade_id}", response_class=JSONResponse)
    def api_paper_trade(paper_trade_id: str):
        """Structured JSON for a single paper trade."""

        svc = _service()
        view = svc.load_paper_trade(paper_trade_id)
        return paper_trade_view_to_jsonable(view)

    @app.post("/api/paper-trades", response_class=JSONResponse)
    def api_create_paper_trade(
        instrument: str,
        timeframe: str = "15m",
        account_capital: str = "",
        risk_percent: str = "",
    ):
        """Create a paper trade from the EXISTING current analysis + plan.

        Accepts ``instrument``, ``timeframe``, ``account_capital`` and
        ``risk_percent``. The paper trade reuses the EXISTING current
        analysis' trade geometry + the Phase 4 trade plan VERBATIM; it
        never accepts arbitrary entry / stop / target values. The created
        trade is ``WAITING_FOR_ENTRY`` (or ``INVALIDATED`` when geometry
        is incomplete — never fabricated). A human creates the trade
        deliberately; this is NOT automatic trading. The response never
        contains a BUY/SELL recommendation.
        """

        from datetime import datetime,timezone

        svc = _service()
        view = svc.create_paper_trade(
            PaperTradeRequest(
                instrument=instrument,
                account_capital=account_capital,
                risk_percent=risk_percent,
                setup_timeframe=timeframe,
                created_at=datetime.now(timezone.utc),
            ),
        )
        return paper_trade_view_to_jsonable(view)

    @app.post(
        "/api/paper-trades/{paper_trade_id}/track", response_class=JSONResponse,
    )
    def api_track_paper_trade(paper_trade_id: str):
        """Advance a paper trade's lifecycle using the latest completed candles.

        Only completed candles are inspected (no look-ahead; no forming
        candle). A previously-resolved (terminal) paper trade is returned
        unchanged. The Sprint 11W outcome evaluator + historical pipeline
        are NEVER invoked.
        """

        from datetime import datetime,timezone

        svc = _service()
        view = svc.track_paper_trade(
            PaperTradeTrackRequest(
                paper_trade_id=paper_trade_id,
                reference_now=datetime.now(timezone.utc),
            ),
        )
        return paper_trade_view_to_jsonable(view)

    @app.post(
        "/api/paper-trades/{paper_trade_id}/close", response_class=JSONResponse,
    )
    def api_close_paper_trade(
        paper_trade_id: str,
        exit_price: str,
        exit_timestamp: str = "",
    ):
        """Manually close an OPEN paper trade at an observed market price.

        This is a HUMAN action — the caller supplies an exit price +
        timestamp. It is NOT an automatic execution and NOT a broker
        order. Only an OPEN trade may be closed; illegal transitions fail
        safely (HTTP 400).
        """

        from datetime import datetime,timezone
        from decimal import Decimal

        svc = _service()
        try:
            ts = (
                datetime.fromisoformat(exit_timestamp)
                if exit_timestamp
                else datetime.now(timezone.utc)
            )
            view = svc.manually_close_paper_trade(
                PaperTradeManualCloseRequest(
                    paper_trade_id=paper_trade_id,
                    exit_price=Decimal(exit_price),
                    exit_timestamp=ts,
                ),
            )
        except (ValueError, LookupError) as exc:
            return JSONResponse(
                status_code=400,
                content={"error": "invalid_manual_close", "detail": str(exc)},
            )
        return paper_trade_view_to_jsonable(view)

    @app.post(
        "/api/paper-trades/{paper_trade_id}/cancel", response_class=JSONResponse,
    )
    def api_cancel_paper_trade(paper_trade_id: str):
        """Cancel a WAITING_FOR_ENTRY paper trade (human action)."""

        svc = _service()
        try:
            view = svc.cancel_paper_trade(paper_trade_id)
        except (ValueError, LookupError) as exc:
            return JSONResponse(
                status_code=400,
                content={"error": "invalid_cancel", "detail": str(exc)},
            )
        return paper_trade_view_to_jsonable(view)

    @app.post("/api/paper-trading/run-once", response_class=JSONResponse)
    def api_run_paper_trading_cycle(
        instrument: str = "",
        instruments: str = "",
        timeframe: str = "15m",
        account_capital: str = "",
        risk_percent: str = "",
    ):
        """Run ONE deterministic paper-trading operational cycle.

        Orchestrates the EXISTING provider + analysis + paper-trading layers
        over a watchlist. Paper trading only — no real order is placed. New
        paper trades are created ONLY from the latest COMPLETED candle when
        the existing opportunity is ``READY_FOR_REVIEW`` (eligible + complete
        geometry + QUALIFIED/PREFERRED). Duplicate trades against the same
        completed candle are skipped. Existing open / waiting trades are
        tracked against completed candles chronologically. One instrument
        failure never aborts the cycle.

        The response NEVER contains a BUY/SELL/ENTER/EXIT/HOLD
        recommendation.
        """

        from datetime import datetime, timezone

        from dashboard.services import OperationsRequest

        svc = _service()
        watchlist = _parse_watchlist(instruments, svc) if instruments else None
        if instrument and not instruments:
            watchlist = [instrument]
        request = OperationsRequest(
            account_capital=account_capital.strip() or None,
            risk_percent=risk_percent.strip() or None,
            setup_timeframe=timeframe,
            watchlist=watchlist,
            reference_now=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
        )
        try:
            view = svc.run_paper_trading_cycle(request)
        except Exception as exc:  # noqa: BLE001 - failure isolation
            return JSONResponse(
                status_code=500,
                content={"error": "operations_cycle_failed", "detail": str(exc)},
            )
        return operations_cycle_view_to_jsonable(view)

    # ------------------------------------------------------------
    # HISTORICAL DATA STATUS (Product Phase 6A)
    # ------------------------------------------------------------

    @app.get("/historical-data", response_class=HTMLResponse)
    def historical_data_page(request: Request) -> HTMLResponse:
        """Minimal historical-data status surface (Phase 6A)."""

        svc = _service()
        datasets = svc.historical_datasets()
        return templates.TemplateResponse(
            request,
            "historical_data.html",
            {
                "datasets": datasets,
                "dataset_jsonable": svc.historical_dataset_jsonable(),
            },
        )

    @app.get("/api/historical-data", response_class=JSONResponse)
    def api_historical_data() -> dict:
        svc = _service()
        datasets = svc.historical_dataset_jsonable()
        return {
            "datasets": list(datasets),
            "dataset_count": len(datasets),
        }

    return app


def _chart_to_dict(chart) -> dict:
    return {
        "candles": [
            {"t": t, "o": o, "h": h, "l": l, "c": cl}
            for (t, o, h, l, cl) in chart.candles
        ],
        "entry": chart.entry,
        "stop": chart.stop,
        "target_1": chart.target_1,
        "support": chart.support,
        "resistance": chart.resistance,
        "invalidation_level": chart.invalidation_level,
    }


def _workstation_why_text(wv) -> str:
    """Render the workstation 'why' explanation (presentation text only)."""

    try:
        return workstation_why(wv)
    except Exception:  # pragma: no cover - defensive
        return ""


#: A module-level app instance for `uvicorn dashboard.app:app`.
app = create_app()


def main() -> None:
    """Run the dashboard with uvicorn (for ``python -m dashboard.app``)."""

    import uvicorn

    host = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
    port = int(os.environ.get("DASHBOARD_PORT", "8000"))
    uvicorn.run(
        "dashboard.app:app", host=host, port=port, reload=False,
    )


if __name__ == "__main__":  # pragma: no cover
    main()


__all__ = ["app", "create_app", "set_service"]
