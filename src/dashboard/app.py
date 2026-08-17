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
from dashboard.services import (
    AnalysisRequest,
    DashboardAnalysisService,
    EvidenceSource,
    ScanRequest,
    WorkstationRequest,
    default_service,
)
from dashboard.views import (
    scan_view_to_jsonable,
    to_jsonable,
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
        _DEFAULT_SERVICE = default_service(
            provider_name=provider_name,
            evidence_report=evidence_report,
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
    ):
        """Live trading workstation (Product Phase 3).

        Bundles the watchlist scan + the selected instrument's detailed
        trade review into one coherent view. Manual refresh only — no
        background polling. The analysis always uses the latest
        COMPLETED candle; no future candle is read.
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
