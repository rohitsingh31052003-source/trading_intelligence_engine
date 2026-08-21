"""
Dashboard analysis service (productization layer).

This service is the ORCHESTRATION boundary between the HTTP layer and
the existing intelligence engine. It:

* fetches normalized market data via a :class:`DashboardDataProvider`,
* builds the Sprint 11U :class:`InstrumentDataset` the scanner expects,
* runs :meth:`MarketScanner.scan` over the selected instrument (reusing
  :class:`ScanEngines.default()`),
* projects the resulting :class:`InstrumentScanResult` into a
  :class:`DashboardTradeView` presentation model,
* OPTIONALLY attaches an offline historical evidence view (reused
  Sprint 11Y/11Z/12A/12B/12E chain) when an evidence report is
  supplied — it NEVER recomputes evidence and NEVER fabricates it.

CRITICAL — no look-ahead (NON-NEGOTIABLE):

The service computes the CURRENT analysis using ONLY candles that had
CLOSED at or before the evaluation point. Concretely:

* The evaluation point is the close timestamp of the latest COMPLETED
  setup-timeframe candle (``setup_candles[-1]`` of the fetched series).
* The scanner's own point-in-time guarantees (completed-HTF-candle-only
  via ``_latest_completed_before``; setup slice ``candles[:T+1]`` via
  ``_latest_completed_at_or_before``) are preserved unchanged — the
  service feeds the SAME candle tuples + the explicit
  ``evaluation_time``.
* The service NEVER calls the Sprint 11W outcome evaluator. Outcome
  evaluation is forward-looking / historical-only and has no place in a
  CURRENT analysis. (Regression-tested: the service works with
  ``OutcomeEvaluator.evaluate`` patched to raise.)
* The service's public API accepts NO "future candles" argument. There
  is no way to feed tomorrow's candles into today's analysis.

The service is DESCRIPTIVE ONLY. It does NOT guarantee future
performance, does NOT constitute a trading recommendation, and does NOT
modify the existing decision / scoring logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Mapping

from engine.config.market_scan_config import MarketScanConfig
from engine.config.paper_trade_config import PaperTradeConfig
from engine.config.trade_plan_config import TradePlanConfig
from engine.intelligence.market_scanner import (
    InstrumentDataset,
    MarketScanner,
    ScanEngines,
)
from engine.intelligence.paper_trade_performance import (
    PaperTradePerformanceEngine,
)
from engine.intelligence.paper_trading import PaperTradingEngine
from engine.intelligence.trade_planning import TradePlanningEngine
from engine.models.market_context import MarketContext
from engine.models.market_scan import InstrumentScanResult, MTFAlignment
from engine.models.ohlcv import OHLCVCandle
from engine.models.paper_trade import PaperTrade
from engine.models.trade_plan import TradePlan

from dashboard.data_provider import (
    DashboardDataProvider,
    FIXTURE_INSTRUMENTS,
    FixtureDataProvider,
    FreshnessConfig,
    FreshnessState,
    InstrumentSeries,
    ProviderStatus,
    SUPPORTED_TIMEFRAMES,
    context_timeframe_for,
    make_provider,
)
from dashboard.views import (
    ActionabilityDetail,
    ActionabilityState,
    DashboardTradeView,
    DataSourceView,
    DecisionView,
    EvidenceView,
    GeometryView,
    HistoricalContextView,
    HistoricalDatasetStatusView,
    InstrumentOperationRowView,
    MarketOverviewView,
    OperationsCycleView,
    PaperTradeJournalView,
    PaperTradeView,
    TradePlanView,
    WatchlistRowView,
    WatchlistScanView,
    WorkstationView,
    derive_actionability,
    derive_actionability_reason,
    historical_dataset_view_to_jsonable,
    operations_cycle_view_to_jsonable,
    paper_trade_journal_view_to_jsonable,
    paper_trade_view_to_jsonable,
    scanner_rank_key,
    to_jsonable,
    to_operations_cycle_view,
    to_paper_trade_view,
    workstation_why,
)
from engine.data.historical_service import HistoricalMarketDataService
from dashboard.watchlist import Watchlist

#: Honest staleness threshold for the "data stale" warning. The latest
#: completed candle is considered stale when it is older than this many
#: seconds relative to "now". For fixture data the timestamp is
#: historical, so the warning is informational only.
DEFAULT_STALENESS_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class AnalysisRequest:
    """
    A dashboard analysis request.

    Attributes:

    instrument
        Canonical instrument name (e.g. ``"NIFTY"``).

    setup_timeframe
        Dashboard setup (execution) timeframe label
        (``"1m"``/``"3m"``/``"5m"``/``"15m"``/``"30m"``/``"1h"``/``"4h"``
        /``"1D"``).

    context_timeframe
        Optional explicit context (higher) timeframe. When ``None`` a
        sensible fallback is derived; when no higher timeframe is
        available the scan is reported INCOMPLETE.
    """

    instrument: str
    setup_timeframe: str = "15m"
    context_timeframe: str | None = None


@dataclass(frozen=True)
class ScanRequest:
    """
    A multi-instrument scanner request (Product Phase 2).

    Attributes:

    watchlist
        Optional :class:`~dashboard.watchlist.Watchlist` to scan. When
        ``None`` the provider's default watchlist is used.

    setup_timeframe
        Dashboard setup (execution) timeframe label applied to EVERY
        instrument in the watchlist.

    context_timeframe
        Optional explicit context (higher) timeframe. When ``None`` a
        sensible fallback is derived per the existing single-instrument
        behavior.
    """

    watchlist: Watchlist | None = None
    setup_timeframe: str = "15m"
    context_timeframe: str | None = None


@dataclass(frozen=True)
class WorkstationRequest:
    """
    A live-trading-workstation request (Product Phase 3).

    The workstation bundles the watchlist scan + the selected
    instrument's detailed review into one coherent view. This request
    carries the selection + an optional watchlist override.

    Attributes:

    instrument
        Canonical instrument name selected for detailed review. When
        empty / not in the watchlist the workstation still renders the
        watchlist status table and selects the first analyzed row's
        instrument (deterministic fallback; never invents an
        opportunity).

    setup_timeframe
        Dashboard setup (execution) timeframe label.

    context_timeframe
        Optional explicit context (higher) timeframe. When ``None`` a
        sensible fallback is derived per the existing single-instrument
        behavior.

    watchlist
        Optional :class:`~dashboard.watchlist.Watchlist` to scan. When
        ``None`` the provider's default watchlist is used.
    """

    instrument: str = ""
    setup_timeframe: str = "15m"
    context_timeframe: str | None = None
    watchlist: Watchlist | None = None


@dataclass(frozen=True)
class TradePlanRequest:
    """
    A risk / trade-plan request (Product Phase 4).

    The request pairs the existing current analysis (instrument + setup
    timeframe) with user-supplied account-risk parameters. The planner
    reuses the EXISTING current analysis' trade geometry verbatim; it
    NEVER accepts arbitrary entry / stop / target values that would
    bypass the authoritative engine geometry.

    Attributes:

    instrument
        Canonical instrument name to plan for.

    setup_timeframe
        Dashboard setup (execution) timeframe label.

    account_capital
        User-supplied account capital. May be a number or a string; the
        engine coerces to ``Decimal``. Must be positive.

    risk_percent
        User-supplied risk percentage per trade (e.g. ``1`` means 1%).
        Must be strictly greater than zero and within the configured
        ``[min_risk_percent, max_risk_percent]`` bounds.

    context_timeframe
        Optional explicit context (higher) timeframe.

    quantity_spec
        Optional instrument-specific
        :class:`~engine.models.trade_plan.QuantitySpec`. When ``None`` the
        safe generic default model is used and the plan surfaces
        ``QUANTITY_SPEC_UNAVAILABLE``.

    label / metadata
        Optional identity / metadata carried onto the plan for audit.
    """

    instrument: str
    account_capital: Any
    risk_percent: Any
    setup_timeframe: str = "15m"
    context_timeframe: str | None = None
    quantity_spec: Any | None = None
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class PaperTradeRequest:
    """
    A paper-trade creation request (Product Phase 5).

    Pairs the existing current analysis (instrument + setup timeframe) +
    the existing trade geometry + the existing Product Phase 4 trade plan
    (reused verbatim) to create a PAPER TRADE. The paper trade reuses the
    EXISTING decision / geometry / plan verbatim; it NEVER accepts
    arbitrary entry / stop / target values that would bypass the
    authoritative engine geometry.

    A human creates a paper trade deliberately; this is NOT automatic
    trading. The scanner is NOT turned into an auto-trading strategy.

    Attributes:

    instrument
        Canonical instrument name to paper-trade.

    account_capital / risk_percent
        User-supplied account-risk parameters (reused by the Phase 4
        planner to size the position). The paper-trade layer performs NO
        new position sizing.

    setup_timeframe / context_timeframe
        Dashboard setup / context timeframe pair.

    created_at
        Explicit creation timestamp (the human action time). Caller-
        supplied so tests are deterministic (no wall-clock read).

    sequence
        Instance discriminator so two paper trades created from the same
        opportunity at the same ``created_at`` do not collapse into one
        record. Default ``0``.

    label / metadata
        Optional identity / metadata carried onto the paper trade.
    """

    instrument: str
    account_capital: Any
    risk_percent: Any
    setup_timeframe: str = "15m"
    context_timeframe: str | None = None
    created_at: datetime | None = None
    sequence: int = 0
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class PaperTradeTrackRequest:
    """
    A paper-trade tracking request (Product Phase 5).

    Advances a paper trade's lifecycle using COMPLETED market candles up
    to ``reference_now``. Only candles with ``timestamp <= reference_now``
    are inspected (forming candles excluded). No future candle is read.
    """

    paper_trade_id: str
    reference_now: datetime


@dataclass(frozen=True)
class PaperTradeManualCloseRequest:
    """A manual-close request for an OPEN paper trade (human action)."""

    paper_trade_id: str
    exit_price: Any
    exit_timestamp: datetime


@dataclass(frozen=True)
class OperationsRequest:
    """
    A paper-trading OPERATIONAL cycle request (Product Phase 5 operations).

    Drives ONE deterministic :meth:`DashboardAnalysisService.run_paper_trading_cycle`
    observation cycle over a watchlist of instruments using the EXISTING
    provider + analysis + paper-trading layers. Paper trading only — no real
    order is placed.

    Attributes:

    account_capital / risk_percent
        User-supplied account-risk parameters reused by the existing Phase 4
        planner to size each eligible paper trade. Required for paper-trade
        creation (the plan is reused verbatim).

    setup_timeframe / context_timeframe
        Dashboard setup / context timeframe pair (reused).

    watchlist
        Optional iterable of instrument names. When ``None`` the service's
        available instruments are used; an explicitly empty watchlist
        produces an empty cycle (honest, not a fallback).

    reference_now
        Optional deterministic reference timestamp for tracking. When
        ``None`` the latest completed candle across analysed instruments is
        used. NEVER a wall-clock in tests.

    started_at
        Optional deterministic cycle start timestamp (audit metadata).

    label / metadata
        Optional identity / metadata carried onto created paper trades.
    """

    account_capital: Any = None
    risk_percent: Any = None
    setup_timeframe: str = "15m"
    context_timeframe: str | None = None
    watchlist: Any = None
    reference_now: datetime | None = None
    started_at: datetime | None = None
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ChartPayload:
    """
    Candlestick chart payload (backend-authored; the frontend renders
    it verbatim, never inventing levels).

    Attributes:

    candles
        Tuple of ``(timestamp_iso, open, high, low, close)`` for the
        setup timeframe (capped for readability).

    entry / stop / target_1
        Trade geometry levels to overlay, each ``None`` when unavailable.

    support / resistance
        Nearest structural support / resistance to overlay, each ``None``
        when unavailable.

    invalidation_level
        Invalidation level (== stop) to overlay, or ``None``.
    """

    candles: tuple[tuple[str, float, float, float, float], ...] = ()
    entry: float | None = None
    stop: float | None = None
    target_1: float | None = None
    support: float | None = None
    resistance: float | None = None
    invalidation_level: float | None = None


class DashboardAnalysisService:
    """
    Orchestrates the existing intelligence engine into a dashboard view.

    The service holds a data provider and an optional offline evidence
    source. It is stateless across calls (no mutable caches that could
    leak future data). Every public method returns an immutable
    presentation artifact.
    """

    def __init__(
        self,
        provider: DashboardDataProvider | None = None,
        *,
        scanner: MarketScanner | None = None,
        engines: ScanEngines | None = None,
        evidence_source: "EvidenceSource | None" = None,
        min_history: int = 10,
        staleness_seconds: int = DEFAULT_STALENESS_SECONDS,
        max_chart_candles: int = 120,
        freshness_config: FreshnessConfig | None = None,
        paper_trade_store: Any | None = None,
        historical_service: "HistoricalMarketDataService | None" = None,
        historical_evidence_source: "HistoricalEvidenceSource | None" = None,
    ) -> None:
        # Freshness config is DATA QUALITY only — it never alters the
        # intelligence engine's decision semantics. When the provider was
        # built without one, derive a default from the legacy
        # ``staleness_seconds`` so behavior stays backward-compatible.
        if freshness_config is None:
            freshness_config = FreshnessConfig(
                default_staleness_seconds=staleness_seconds,
            )
        self.freshness_config = freshness_config
        if provider is None:
            provider = FixtureDataProvider(freshness_config=freshness_config)
        self.provider = provider
        # If the provider accepts a freshness_config and was constructed
        # externally, propagate the service-level config where supported
        # (best-effort; providers are duck-typed).
        if hasattr(provider, "freshness_config") and getattr(
            provider, "freshness_config", None,
        ) is None:
            try:
                object.__setattr__(provider, "freshness_config", freshness_config)
            except (AttributeError, TypeError):
                pass
        self.scanner = scanner or MarketScanner(
            MarketScanConfig(min_history=min_history),
        )
        self._default_engines = engines
        self.evidence_source = evidence_source
        self.min_history = min_history
        self.staleness_seconds = staleness_seconds
        self.max_chart_candles = max_chart_candles
        # Product Phase 4 — risk / trade planning engine. Reuses the
        # existing current analysis' trade geometry verbatim; performs NO
        # market analysis / decision / prediction. The existing decision
        # and geometry remain AUTHORITATIVE.
        self.trade_planning_engine = TradePlanningEngine(TradePlanConfig())
        # Product Phase 5 — paper trading & real-world validation. A
        # recording / validation layer that reuses the existing decision
        # + geometry + Phase 4 plan verbatim. It performs NO market
        # analysis / decision / prediction / execution. The existing
        # decision is AUTHORITATIVE; a paper-trade RESULT never rewrites
        # the original system decision. The paper-trade store is OPTIONAL
        # — when None, paper trades are not persisted (the service still
        # creates / tracks / resolves in-memory trades).
        self.paper_trading_engine = PaperTradingEngine(PaperTradeConfig())
        self.paper_trade_performance_engine = PaperTradePerformanceEngine()
        self.paper_trade_store = paper_trade_store
        # Product Phase 5 operations — a THIN orchestration layer that runs
        # one deterministic paper-trading observation cycle over a watchlist
        # using the EXISTING provider + analysis + paper-trading layers. It
        # implements NO new intelligence; the existing decision / geometry /
        # plan / lifecycle are reused VERBATIM. Paper trading only — no real
        # order is placed. Imported lazily to avoid a circular import
        # (paper_trade_operations imports DashboardAnalysisService for typing).
        from dashboard.paper_trade_operations import (
            OperationsConfig,
            PaperTradingOperations,
        )
        self.paper_trading_operations = PaperTradingOperations(self)
        # Session-level cache of the last operations cycle (NOT persisted —
        # purely so the workstation can surface the most recent cycle). It
        # holds no market data and no future information; it is a read-only
        # projection of an already-completed cycle.
        self.last_operations_cycle: OperationsCycleView | None = None
        # Product Phase 6A — historical market-data foundation. Optional:
        # when supplied, the dashboard surfaces a minimal historical-data
        # status page (which datasets are stored). It is DATA FOUNDATION
        # ONLY — no prediction, no evidence, no trade recommendations.
        # The existing decision / geometry / plan / lifecycle and the LIVE
        # path remain AUTHORITATIVE and untouched.
        self.historical_service = historical_service
        # Product Phase 6E — historical + current intelligence. Optional:
        # when supplied, the dashboard attaches a DESCRIPTIVE historical
        # evidence context (comparable historical setups from the
        # persisted Phase 6D research) to the current assessment. It is
        # CONTEXTUAL ONLY — it NEVER modifies the authoritative existing
        # decision / actionability / geometry / trade plan /
        # paper-trading eligibility and NEVER fabricates evidence.
        self.historical_evidence_source = historical_evidence_source

    # ------------------------------------------------------------
    # PRODUCT PHASE 6A — HISTORICAL DATA STATUS
    # ------------------------------------------------------------

    def historical_datasets(self) -> tuple[HistoricalDatasetStatusView, ...]:
        """
        Status of every STORED historical dataset (read-only projection).

        When no historical service is configured or nothing is stored,
        returns an empty tuple (honest; never fabricates a dataset).
        Only the /historical-data page consumes this. It NEVER
        recomputes anything — it reads the store's own summaries +
        persisted provenance.
        """

        svc = self.historical_service
        if svc is None or svc.store is None:
            return ()
        views: list[HistoricalDatasetStatusView] = []
        for info in svc.store.list_datasets():
            provider = "unavailable"
            status = "UNAVAILABLE"
            reason = ""
            try:
                provenance = svc.store.load_provenance(info.instrument, info.timeframe)
                if provenance:
                    import json

                    try:
                        latest = json.loads(provenance[-1])
                        provider = latest.get("provider", "unavailable")
                        status = latest.get("status", "UNAVAILABLE")
                        reason = latest.get("reason", "")
                    except Exception:  # pragma: no cover - corrupted line tolerated
                        pass
            except Exception:  # pragma: no cover - provenance read failure tolerated
                pass
            views.append(
                HistoricalDatasetStatusView(
                    instrument=info.instrument,
                    timeframe=info.timeframe,
                    available=info.candle_count > 0,
                    provider=provider,
                    status=status,
                    record_count=info.candle_count,
                    first_timestamp=info.first_timestamp,
                    last_timestamp=info.last_timestamp,
                    reason=reason,
                ),
            )
        return tuple(views)

    def historical_dataset_jsonable(self) -> tuple[dict, ...]:
        """JSON projection of :meth:`historical_datasets`."""

        return tuple(
            historical_dataset_view_to_jsonable(v)
            for v in self.historical_datasets()
        )

    # ------------------------------------------------------------
    # METADATA (for selectors)
    # ------------------------------------------------------------

    def available_instruments(self) -> tuple[str, ...]:
        """Instruments the configured provider can serve."""

        if isinstance(self.provider, FixtureDataProvider):
            return FIXTURE_INSTRUMENTS
        # Live providers: expose the monitored universe (NIFTY 50 ∪
        # SENSEX constituents, de-duplicated, plus the benchmark index);
        # the actual availability is checked per-request (graceful on
        # miss).
        return self.default_watchlist().instruments

    def available_timeframes(self) -> tuple[str, ...]:
        """Timeframe labels the dashboard offers for selection."""

        return SUPPORTED_TIMEFRAMES

    def is_timeframe_supported(self, setup_timeframe: str) -> bool:
        return self.provider.is_timeframe_supported(setup_timeframe)

    # ------------------------------------------------------------
    # MAIN ANALYSIS
    # ------------------------------------------------------------

    def analyze(self, request: AnalysisRequest) -> DashboardTradeView:
        """
        Produce the current dashboard view for one instrument + timeframe.

        The analysis uses ONLY candles that closed at or before the
        latest completed setup candle. No future candle is read and no
        outcome evaluation is performed.
        """

        setup_tf = request.setup_timeframe
        ctx_tf = request.context_timeframe or context_timeframe_for(setup_tf)

        # --- Fetch normalized data (graceful on failure) ---
        series = self.provider.fetch(request.instrument, setup_tf)
        if not series.available or not series.setup_candles:
            return self._unavailable_view(
                request, ctx_tf or "", series.reason or "no setup data",
                series=series,
            )

        # --- Determine the context timeframe ---
        # The fixture provider returns 1D context + 15M setup. For a
        # live provider the context may have been fetched too. If the
        # context is empty and a context timeframe was expected, the
        # scan will be INCOMPLETE (the scanner enforces this).
        context_candles = series.context_candles
        # If the provider did not supply a context but the fixture
        # provider happens to have a 1D series for this instrument,
        # reuse it (keeps the fixture path coherent).
        if not context_candles and isinstance(self.provider, FixtureDataProvider):
            ctx_series = self.provider.fetch(request.instrument, "1D")
            context_candles = ctx_series.context_candles or ctx_series.setup_candles
            if ctx_tf is None and context_candles:
                ctx_tf = "1D"

        # --- Build the scanner dataset + run the scan ---
        scan_config = MarketScanConfig(
            context_timeframe=ctx_tf or "1D",
            setup_timeframe=self._scanner_setup_label(setup_tf),
            min_history=self.min_history,
        )
        # Reconfigure the scanner with this request's timeframe pair.
        scanner = MarketScanner(scan_config)
        engines = self._default_engines or ScanEngines.default()
        dataset = InstrumentDataset(
            instrument=request.instrument,
            context_candles=context_candles,
            setup_candles=series.setup_candles,
        )

        # The evaluation point is the close of the latest COMPLETED
        # setup candle — established by the provider's completed-candle
        # boundary. This is NEVER the forming candle. When the provider
        # supplies an explicit latest_completed_candle_timestamp we use
        # it; otherwise we fall back to the last setup candle's
        # timestamp (which, post-boundary, IS the latest completed).
        evaluation_time = series.latest_completed_candle_timestamp
        if evaluation_time is None:
            evaluation_time = series.setup_candles[-1].timestamp
        scan_result_obj = scanner.scan(
            [dataset], evaluation_time=evaluation_time, engines=engines,
        )
        # The scan returns one InstrumentScanResult for our instrument.
        if scan_result_obj.results:
            instrument_result = scan_result_obj.results[0]
        else:  # pragma: no cover - defensive
            return self._unavailable_view(
                request, ctx_tf or "", "scanner produced no result",
                series=series,
            )

        # --- Project into the presentation view ---
        view = self._build_view(
            request, setup_tf, ctx_tf or "", instrument_result, series=series,
        )
        return view

    # ------------------------------------------------------------
    # MULTI-INSTRUMENT SCANNER (Product Phase 2)
    # ------------------------------------------------------------

    def scan_watchlist(self, request: ScanRequest) -> WatchlistScanView:
        """
        Scan every instrument in a watchlist independently and present
        the resulting opportunities in one coherent, deterministically
        ordered view.

        This method ORCHESTRATES the existing single-instrument
        :meth:`analyze` only. It implements NO new market-analysis,
        decision, scoring, geometry or evidence logic — every per-row
        value is read from the reused :class:`DashboardTradeView` that
        :meth:`analyze` already produces.

        GUARANTEES (Product Phase 2):

        * **Reuse-only**: reuses :meth:`analyze` (which reuses the
          existing scanner + intelligence pipeline) per instrument.
        * **Failure isolation**: one instrument that fails (provider
          timeout / unsupported instrument / unsupported timeframe /
          empty data / malformed data / invalid analysis) is reported
          as an honest ``ActionabilityState.INVALID`` row and the scan
          CONTINUES with the remaining instruments. One bad symbol
          never aborts the whole scan.
        * **Completed-candle guarantee**: inherited from
          :meth:`analyze` — each instrument is evaluated using the
          latest COMPLETED setup candle; a forming candle is never fed
          to the engine and no future candle is read.
        * **Determinism**: the rows are ordered by the fixed
          PRESENTATIONAL key (:func:`scanner_rank_key`) — decision
          classification, actionability, evidence strength, geometry
          availability, freshness, then instrument name. Input
          watchlist ordering NEVER changes the final ordering; two
          scans of identical data always produce identical results and
          identical row order.
        * **No look-ahead**: this method accepts NO ``future`` /
          ``future_candles`` argument; it never calls the Sprint 11W
          outcome evaluator and never runs the historical pipeline
          (inherited from :meth:`analyze`).
        * **Decision authority**: the existing decision classification
          is reused VERBATIM — never renamed to BUY/SELL, never
          upgraded / downgraded. The presentational ordering is a sort,
          NOT a new score, NOT a probability, NOT a prediction.

        The result is DESCRIPTIVE ONLY. It does NOT guarantee future
        performance and does NOT constitute a trading recommendation.
        """

        watchlist = (
            request.watchlist
            if request.watchlist is not None
            else self.default_watchlist()
        )
        instruments = watchlist.instruments
        setup_tf = request.setup_timeframe

        rows: list[WatchlistRowView] = []
        analyzed = 0
        errored = 0
        actionable = 0
        scan_warnings: list[str] = []

        for instrument in instruments:
            row = self._scan_one(
                instrument, setup_tf, request.context_timeframe,
            )
            rows.append(row)
            if row.error:
                errored += 1
            else:
                analyzed += 1
            if row.actionability is ActionabilityState.READY_FOR_REVIEW:
                actionable += 1

        if errored:
            scan_warnings.append(
                f"{errored} instrument(s) could not be analysed (provider "
                "failure / unsupported instrument / timeframe / empty or "
                "invalid data). They are reported honestly as INVALID rows "
                "and were NOT fabricated into opportunities; the remaining "
                "instruments were still scanned (failure isolation).",
            )
        # Determine the context timeframe label used (for display) from
        # the first successfully-analyzed row, falling back to the
        # derived context for the setup timeframe.
        ctx_tf = request.context_timeframe or context_timeframe_for(setup_tf) or ""
        for row in rows:
            if not row.error and row.view.context_timeframe:
                ctx_tf = row.view.context_timeframe
                break

        # Deterministic PRESENTATIONAL ordering (NOT a score). Stable
        # sort by the ranking key; identical data -> identical order
        # regardless of input watchlist order.
        rows.sort(key=scanner_rank_key)
        ranked = tuple(
            WatchlistRowView(
                instrument=row.instrument,
                view=row.view,
                error=row.error,
                rank=i + 1,
            )
            for i, row in enumerate(rows)
        )

        rationale = (
            "Rows are ordered by a fixed PRESENTATIONAL key (decision "
            "classification, actionability, evidence strength, geometry "
            "availability, freshness, then instrument name). This is "
            "presentation ordering, NOT a predictive score, NOT a "
            "probability, and NOT a BUY/SELL recommendation. The existing "
            "decision classification is reused verbatim and never upgraded "
            "or downgraded."
        )

        return WatchlistScanView(
            watchlist_instruments=instruments,
            setup_timeframe=setup_tf,
            context_timeframe=ctx_tf,
            rows=ranked,
            total=len(rows),
            analyzed=analyzed,
            errored=errored,
            actionable_count=actionable,
            warnings=tuple(scan_warnings),
            rationale=rationale,
        )

    # ------------------------------------------------------------
    # LIVE TRADING WORKSTATION (Product Phase 3)
    # ------------------------------------------------------------

    def workstation(self, request: WorkstationRequest) -> WorkstationView:
        """
        Build the coherent live-trading-workstation view: the watchlist
        status table + the selected instrument's detailed review.

        This method is PURE ORCHESTRATION. It reuses
        :meth:`scan_watchlist` (which reuses :meth:`analyze`) and
        :meth:`analyze` for the selected instrument. It implements NO
        new market-analysis, decision, scoring, geometry or evidence
        logic — every value is read from the reused outputs. The
        embedded scan + trade view are retained BY REFERENCE and never
        modified.

        GUARANTEES (Product Phase 3):

        * **Reuse-only**: reuses :meth:`scan_watchlist` +
          :meth:`analyze` only. No new intelligence, no new score.
        * **Completed-candle guarantee**: inherited from
          :meth:`analyze` / :meth:`scan_watchlist` — the selected
          instrument is evaluated using the latest COMPLETED setup
          candle; a forming candle is never fed to the engine and no
          future candle is read.
        * **No look-ahead**: this method accepts NO ``future`` /
          ``future_candles`` argument; it never calls the Sprint 11W
          outcome evaluator and never runs the historical pipeline
          (inherited from :meth:`analyze`).
        * **Decision authority**: the existing decision classification
          is reused VERBATIM — never renamed to BUY/SELL, never
          upgraded / downgraded. The watchlist row order is the reused
          PRESENTATIONAL ordering (a sort, not a score).
        * **Deterministic selection**: when the requested instrument is
          empty / not analyzable, the workstation deterministically
          selects the first analyzed (non-error) row's instrument —
          never invents an opportunity. When nothing is analyzable the
          selected view is an honest unavailable view.
        * **Manual refresh only**: there is NO background polling. The
          ``refresh_token`` is the honest evaluation boundary (latest
          completed candle timestamp of the selected view) so a
          deliberate refresh re-runs the analysis over the latest
          completed candle.

        The result is DESCRIPTIVE ONLY. It does NOT guarantee future
        performance and does NOT constitute a trading recommendation.
        """

        watchlist = (
            request.watchlist
            if request.watchlist is not None
            else self.default_watchlist()
        )
        scan = self.scan_watchlist(
            ScanRequest(
                watchlist=watchlist,
                setup_timeframe=request.setup_timeframe,
                context_timeframe=request.context_timeframe,
            ),
        )

        # Deterministic instrument selection for the detailed review.
        requested = request.instrument.strip().upper() if request.instrument else ""
        selected_instrument = ""
        if requested and requested in {r.instrument for r in scan.rows}:
            # Only select an instrument the scan actually analyzed (or
            # at least attempted). A requested instrument not in the
            # watchlist falls back to the first analyzed row.
            selected_instrument = requested
        if not selected_instrument:
            for row in scan.rows:
                if not row.error:
                    selected_instrument = row.instrument
                    break
            # If every row errored, still pick the first row's
            # instrument so the detail panel can show the honest
            # unavailable state for that instrument.
            if not selected_instrument and scan.rows:
                selected_instrument = scan.rows[0].instrument

        selected_view: DashboardTradeView | None = None
        if selected_instrument:
            try:
                selected_view = self.analyze(
                    AnalysisRequest(
                        instrument=selected_instrument,
                        setup_timeframe=request.setup_timeframe,
                        context_timeframe=request.context_timeframe,
                    ),
                )
            except Exception:  # noqa: BLE001 - failure isolation
                selected_view = self._unavailable_view(
                    AnalysisRequest(
                        instrument=selected_instrument,
                        setup_timeframe=request.setup_timeframe,
                        context_timeframe=request.context_timeframe,
                    ),
                    scan.context_timeframe or "",
                    f"workstation could not analyze {selected_instrument}",
                )

        # Deterministic refresh token = the honest evaluation boundary
        # of the selected view (latest completed candle timestamp).
        # NEVER a wall-clock value during fixture analysis; "" when
        # unavailable.
        refresh_token = ""
        if selected_view is not None:
            ts = selected_view.data_source.latest_completed_candle_timestamp
            if ts is None and selected_view.evaluation_timestamp is not None:
                ts = selected_view.evaluation_timestamp
            if ts is not None:
                refresh_token = ts.isoformat()

        # Consolidated honesty limitations (workstation-level, in
        # addition to the selected view's own warnings).
        limitations: list[str] = [
            "The workstation is a coherent presentation bundle of "
            "already-computed descriptive artifacts; it does NOT "
            "establish predictive validity, statistical significance, "
            "or future profitability, and does NOT constitute a "
            "trading recommendation.",
            "Refresh is a deliberate manual action; there is NO "
            "background polling or WebSocket streaming. The analysis "
            "always uses the latest COMPLETED candle.",
            "Target 2 is not supported by the architecture "
            "(target_2 = None, target_2_supported = False).",
            "Risk management, position sizing, broker integration, "
            "order execution, paper trading and portfolio management "
            "are intentionally out of scope (later product phases).",
        ]
        if scan.has_errors:
            limitations.append(
                f"{scan.errored} instrument(s) could not be analyzed "
                "(provider failure / unsupported instrument / "
                "timeframe / empty or invalid data); they are reported "
                "honestly as INVALID rows and were NOT fabricated into "
                "opportunities (failure isolation).",
            )

        rationale = (
            "The live trading workstation bundles the watchlist scan "
            "(PRESENTATIONAL row order — a sort, not a predictive "
            "score) with the selected instrument's detailed trade "
            "review. Every value is reused verbatim from the existing "
            "intelligence engine. The existing decision classification "
            "(REJECTED / WATCH / QUALIFIED / PREFERRED) is "
            "authoritative and is never renamed to BUY/SELL or "
            "upgraded / downgraded. The workstation is DESCRIPTIVE "
            "ONLY."
        )

        return WorkstationView(
            selected_instrument=selected_instrument,
            setup_timeframe=request.setup_timeframe,
            context_timeframe=scan.context_timeframe or "",
            scan=scan,
            selected_view=selected_view,
            refresh_token=refresh_token,
            rationale=rationale,
            limitations=tuple(limitations),
        )

    def default_watchlist(self) -> Watchlist:
        """The watchlist the scanner uses when none is supplied.

        The monitored universe is the NIFTY 50 ∪ SENSEX constituents
        (de-duplicated) plus the pre-existing NIFTY benchmark index
        instrument (see :data:`dashboard.watchlist.DEFAULT_WATCHLIST`).
        On the fixture provider only the fixture instruments have data;
        every other constituent is reported honestly as unavailable
        (failure isolation), never fabricated.
        """

        return Watchlist.default()

    # ------------------------------------------------------------
    # RISK / TRADE PLANNING (Product Phase 4)
    # ------------------------------------------------------------

    def plan_trade(self, request: TradePlanRequest) -> TradePlanView:
        """
        Build a risk / trade plan from the EXISTING current analysis'
        trade geometry + user-supplied account-risk parameters.

        This method ORCHESTRATES the existing single-instrument
        :meth:`analyze` (which reuses the existing scanner + intelligence
        pipeline) and then delegates the deterministic risk / position
        sizing to the :class:`TradePlanningEngine`. It implements NO new
        market-analysis, decision, scoring, geometry, evidence or
        prediction logic — the trade geometry is reused VERBATIM from
        the Sprint 11R ``TradeCandidate`` reached via the scan decision.

        GUARANTEES (Product Phase 4):

        * **Reuse-only**: reuses :meth:`analyze` (existing pipeline) +
          the :class:`TradePlanningEngine` (deterministic calculation).
          The existing decision classification is reused VERBATIM —
          never renamed to BUY/SELL, never upgraded / downgraded.
        * **Authoritative geometry**: entry / stop / target_1 /
          risk_distance / reward_distance / risk_reward_ratio are reused
          VERBATIM from the Sprint 11R candidate. The plan NEVER
          recomputes a second entry / stop / target / R:R and NEVER
          invents Target 2 (``target_2_supported = False``).
        * **Completed-candle guarantee**: inherited from :meth:`analyze`
          — the analysis uses the latest COMPLETED setup candle; a
          forming candle is never fed to the engine and no future candle
          is read.
        * **No look-ahead**: this method accepts NO ``future`` /
          ``future_candles`` argument; it never calls the Sprint 11W
          outcome evaluator and never runs the historical pipeline
          (inherited from :meth:`analyze` + the planning engine which
          consumes already-computed geometry only).
        * **No prediction**: the plan produces NO probability, NO
          win-rate, NO AI confidence, NO predictive score, NO
          BUY/SELL/ENTER/EXIT/HOLD recommendation. ``planned_reward`` is
          deterministic from ``quantity * reward_distance`` and is
          explicitly distinguished from an expected return.
        * **Evidence separation**: evidence is NEVER used to calculate
          position size and NEVER converted into a risk percentage.
        * **Determinism**: identical inputs produce identical plan ids +
          identical plans.

        The result is DESCRIPTIVE ONLY. It does NOT guarantee future
        performance and does NOT constitute a trading recommendation.
        """

        # Reuse the existing current analysis (completed-candle, no
        # look-ahead). The view carries the authoritative geometry +
        # decision + actionability reused verbatim.
        view = self.analyze(
            AnalysisRequest(
                instrument=request.instrument,
                setup_timeframe=request.setup_timeframe,
                context_timeframe=request.context_timeframe,
            ),
        )
        geom = view.geometry
        plan = self.trade_planning_engine.plan(
            instrument=request.instrument,
            timeframe=request.setup_timeframe,
            account_capital=request.account_capital,
            risk_percent=request.risk_percent,
            geometry=geom,
            direction=geom.direction,
            existing_decision=view.decision.decision_classification,
            actionability=view.actionability.value,
            quantity_spec=request.quantity_spec,
            label=request.label,
            metadata=dict(request.metadata) if request.metadata else None,
        )
        return _to_trade_plan_view(plan)

    # ------------------------------------------------------------
    # PAPER TRADING (Product Phase 5)
    # ------------------------------------------------------------

    def create_paper_trade(self, request: PaperTradeRequest) -> PaperTradeView:
        """
        Create a paper trade from the EXISTING current analysis + the
        existing Product Phase 4 trade plan.

        ORCHESTRATION ONLY: reuses :meth:`analyze` (existing pipeline) +
        :meth:`plan_trade`'s planner to obtain the authoritative geometry
        + plan, then delegates to :class:`PaperTradingEngine.create`. The
        existing decision / geometry / plan are reused VERBATIM — never
        recomputed, never renamed to BUY/SELL, never upgraded / downgraded.
        Target 2 remains unsupported.

        The paper trade is persisted to the store (when one is attached)
        so it survives restarts. The created trade is in
        ``WAITING_FOR_ENTRY`` (or ``INVALIDATED`` when geometry is
        incomplete — never fabricated).

        GUARANTEES: reuse-only; completed-candle guarantee inherited
        from :meth:`analyze`; no look-ahead (accepts NO
        ``future`` / ``future_candles`` argument; never calls the Sprint
        11W outcome evaluator; never runs the historical pipeline); no
        prediction (NO BUY/SELL/ENTER/EXIT/HOLD recommendation; NO
        probability). A human creates the trade deliberately — this is
        NOT automatic trading.
        """

        view = self.analyze(
            AnalysisRequest(
                instrument=request.instrument,
                setup_timeframe=request.setup_timeframe,
                context_timeframe=request.context_timeframe,
            ),
        )
        geom = view.geometry
        # Reuse the Phase 4 planner to size the position verbatim.
        plan = self.trade_planning_engine.plan(
            instrument=request.instrument,
            timeframe=request.setup_timeframe,
            account_capital=request.account_capital,
            risk_percent=request.risk_percent,
            geometry=geom,
            direction=geom.direction,
            existing_decision=view.decision.decision_classification,
            actionability=view.actionability.value,
            label=request.label,
            metadata=dict(request.metadata) if request.metadata else None,
        )
        created_at = request.created_at or view.evaluation_timestamp or datetime.utcnow()
        setup_type = view.setup_type or ""
        trade = self.paper_trading_engine.create(
            instrument=request.instrument,
            timeframe=request.setup_timeframe,
            direction=geom.direction,
            existing_decision=view.decision.decision_classification,
            setup_type=setup_type,
            plan=plan,
            plan_id=plan.plan_id,
            created_at=created_at,
            evaluation_timestamp=view.evaluation_timestamp,
            label=request.label,
            metadata=dict(request.metadata) if request.metadata else None,
            sequence=request.sequence,
        )
        if self.paper_trade_store is not None:
            self.paper_trade_store.save(trade, overwrite=True)
        return to_paper_trade_view(trade)

    def track_paper_trade(
        self, request: PaperTradeTrackRequest,
    ) -> PaperTradeView:
        """
        Advance a paper trade's lifecycle using COMPLETED market candles.

        Loads the persisted paper trade (when a store is attached) or
        raises :class:`LookupError`. Fetches the instrument's completed
        setup candles via the provider (completed-candle boundary
        inherited from Product Phase 1) and delegates to
        :meth:`PaperTradingEngine.track`. Only candles with
        ``timestamp <= reference_now`` are inspected (forming candles
        excluded); no future candle is read. A previously-resolved
        (terminal) paper trade is returned UNCHANGED.

        The engine NEVER calls the Sprint 11W ``OutcomeEvaluator`` and
        NEVER runs the ``HistoricalEvaluationPipeline`` (the touch logic
        is implemented directly in the paper-trading engine). The
        updated trade is persisted.
        """

        if self.paper_trade_store is None:
            raise LookupError(
                "No paper-trade store attached; cannot load paper trade "
                f"{request.paper_trade_id!r}."
            )
        trade = self.paper_trade_store.load(request.paper_trade_id)
        return self._track_trade_with_view(trade, request.reference_now)

    def track_open_paper_trades(
        self, reference_now: datetime,
    ) -> list[PaperTradeView]:
        """
        Advance ALL non-terminal paper trades using the latest completed
        candles. Returns the updated views (terminal trades unchanged).

        Failure isolation: a single trade that fails to load / track is
        skipped (the failure is surfaced in the returned list as the
        trade's prior state, never raised). One bad trade never aborts
        the whole batch.
        """

        if self.paper_trade_store is None:
            return []
        updated: list[PaperTradeView] = []
        for pid in self.paper_trade_store.list_trades():
            try:
                trade = self.paper_trade_store.load(pid)
            except Exception:  # noqa: BLE001 - failure isolation
                continue
            if trade.is_terminal:
                updated.append(to_paper_trade_view(trade))
                continue
            try:
                view = self._track_trade_with_view(trade, reference_now)
                updated.append(view)
            except Exception:  # noqa: BLE001 - failure isolation
                updated.append(to_paper_trade_view(trade))
        return updated

    def _track_trade_with_view(
        self, trade: PaperTrade, reference_now: datetime,
    ) -> PaperTradeView:
        # Fetch completed setup candles for the trade's instrument +
        # timeframe (completed-candle boundary inherited from Phase 1).
        series = self.provider.fetch(trade.instrument, trade.timeframe)
        candles = series.setup_candles if series.available else ()
        updated = self.paper_trading_engine.track(
            trade,
            completed_candles=candles,
            reference_now=reference_now,
        )
        if self.paper_trade_store is not None:
            self.paper_trade_store.save(updated, overwrite=True)
        return to_paper_trade_view(updated)

    def manually_close_paper_trade(
        self, request: PaperTradeManualCloseRequest,
    ) -> PaperTradeView:
        """
        Close an OPEN paper trade manually at an observed market price.

        This is a HUMAN action — the caller supplies an exit price +
        timestamp. The engine records it honestly and computes realized
        R / P&L. It is NOT an automatic execution and NOT a broker
        order. Only an OPEN trade may be manually closed; illegal
        transitions raise :class:`ValueError` (never silently converted
        into success).
        """

        if self.paper_trade_store is None:
            raise LookupError(
                "No paper-trade store attached; cannot load paper trade "
                f"{request.paper_trade_id!r}."
            )
        trade = self.paper_trade_store.load(request.paper_trade_id)
        updated = self.paper_trading_engine.close_manually(
            trade,
            exit_price=request.exit_price,
            exit_timestamp=request.exit_timestamp,
        )
        self.paper_trade_store.save(updated, overwrite=True)
        return to_paper_trade_view(updated)

    def cancel_paper_trade(self, paper_trade_id: str) -> PaperTradeView:
        """Cancel a WAITING_FOR_ENTRY paper trade (human action)."""

        if self.paper_trade_store is None:
            raise LookupError(
                "No paper-trade store attached; cannot load paper trade "
                f"{paper_trade_id!r}."
            )
        trade = self.paper_trade_store.load(paper_trade_id)
        updated = self.paper_trading_engine.cancel(trade)
        self.paper_trade_store.save(updated, overwrite=True)
        return to_paper_trade_view(updated)

    def load_paper_trade(self, paper_trade_id: str) -> PaperTradeView:
        """Load a single persisted paper trade as a view."""

        if self.paper_trade_store is None:
            raise LookupError("No paper-trade store attached.")
        trade = self.paper_trade_store.load(paper_trade_id)
        return to_paper_trade_view(trade)

    def paper_trade_journal(
        self,
        *,
        include_performance: bool = True,
    ) -> PaperTradeJournalView:
        """
        Build the paper-trading journal view (ordered trades + optional
        descriptive performance analytics).

        Loads all persisted paper trades (sorted by id), projects them
        into views, and (when ``include_performance``) aggregates them
        via :class:`PaperTradePerformanceEngine`. The five concerns
        (system decision / geometry / plan / lifecycle / result) stay
        separate on every trade row — never collapsed into one signal /
        score. Performance is DESCRIPTIVE ONLY.
        """

        if self.paper_trade_store is None:
            return PaperTradeJournalView(
                rationale="No paper-trade store attached.",
                limitations="Paper trading persistence is not configured.",
            )
        trades = self.paper_trade_store.load_all()
        views = tuple(to_paper_trade_view(t) for t in trades)
        performance_dict: dict[str, Any] | None = None
        rationale = ""
        limitations = ""
        if include_performance:
            analytics = self.paper_trade_performance_engine.analyze(trades)
            performance_dict = _performance_to_jsonable(analytics)
            rationale = analytics.rationale
            limitations = (
                "Paper-trading performance is DESCRIPTIVE observational "
                "validation; it does NOT predict future performance and "
                "does NOT constitute financial advice. BOTH_TOUCHED "
                "(ambiguous) trades are excluded from win/loss + R; "
                "NO_GEOMETRY trades are excluded from R/P&L."
            )
        return PaperTradeJournalView(
            trades=views,
            performance=performance_dict,
            rationale=rationale,
            limitations=limitations,
        )

    # ------------------------------------------------------------
    # PAPER TRADING OPERATIONS (Product Phase 5 operational increment)
    # ------------------------------------------------------------

    def run_paper_trading_cycle(
        self, request: OperationsRequest,
    ) -> OperationsCycleView:
        """
        Run ONE deterministic paper-trading operational observation cycle.

        ORCHESTRATION ONLY: delegates to :class:`PaperTradingOperations.run_once`,
        which reuses the EXISTING provider + :meth:`analyze` + the EXISTING
        :class:`PaperTradingEngine` create / track lifecycle + the EXISTING
        :class:`PaperTradeStore`. The operations layer implements NO new
        market-analysis / decision / scoring / geometry / position-sizing /
        prediction / execution logic. The existing decision is AUTHORITATIVE;
        a paper-trade RESULT never rewrites it.

        GUARANTEES:

        * **Reuse-only** — reuses :meth:`analyze` + the existing paper-trading
          engine + the existing store.
        * **Completed-candle only** — new trades use the latest COMPLETED
          setup candle; the forming candle never creates / changes / closes
          a trade; future-dated candles are rejected.
        * **No look-ahead** — accepts NO ``future`` / ``future_candles``
          argument; never calls the Sprint 11W outcome evaluator; never
          runs the historical pipeline.
        * **Duplicate prevention** — repeated cycles against the same
          completed candle do not create duplicate trades.
        * **Failure isolation** — one instrument failure never aborts the
          cycle.
        * **Determinism** — identical inputs produce an identical
          ``cycle_id`` + outcome; instrument order is shuffle-invariant.

        Paper trading only — NO real order is placed, NO broker is involved,
        NO BUY/SELL/ENTER/EXIT/HOLD recommendation is produced. The result is
        DESCRIPTIVE ONLY.
        """

        from dashboard.paper_trade_operations import OperationsConfig

        cfg = OperationsConfig(
            account_capital=request.account_capital,
            risk_percent=request.risk_percent,
            setup_timeframe=request.setup_timeframe,
            context_timeframe=request.context_timeframe,
            label=request.label,
            metadata=request.metadata,
        )
        result = self.paper_trading_operations.run_once(
            instruments=request.watchlist,
            reference_now=request.reference_now,
            started_at=request.started_at,
            config=cfg,
        )
        view = to_operations_cycle_view(result)
        # Cache the last cycle so the workstation can surface it (session
        # level; not persisted; read-only projection).
        self.last_operations_cycle = view
        return view

    def _scan_one(
        self,
        instrument: str,
        setup_tf: str,
        context_timeframe: str | None,
    ) -> WatchlistRowView:
        """Analyze one instrument for the scanner (failure-isolated).

        Any exception from the analysis is caught and converted into an
        honest ``INVALID`` row — never raised, never fabricated. This is
        the failure-isolation boundary: one bad symbol never aborts the
        whole scan.
        """

        try:
            view = self.analyze(
                AnalysisRequest(
                    instrument=instrument,
                    setup_timeframe=setup_tf,
                    context_timeframe=context_timeframe,
                ),
            )
            error = view.actionability is ActionabilityState.INVALID and not view.complete
            return WatchlistRowView(
                instrument=instrument, view=view, error=error,
            )
        except Exception as exc:  # noqa: BLE001 - failure isolation
            # Build an honest unavailable view for this instrument. This
            # mirrors _unavailable_view but does not require a series.
            unavailable = self._unavailable_view(
                AnalysisRequest(
                    instrument=instrument,
                    setup_timeframe=setup_tf,
                    context_timeframe=context_timeframe,
                ),
                context_timeframe or "",
                f"scanner error for {instrument}: {exc}",
            )
            return WatchlistRowView(
                instrument=instrument, view=unavailable, error=True,
            )

    # ------------------------------------------------------------
    # VIEW BUILDING (pure projection — no intelligence recomputed)
    # ------------------------------------------------------------

    def _build_view(
        self,
        request: AnalysisRequest,
        setup_tf: str,
        ctx_tf: str,
        result: InstrumentScanResult,
        *,
        series: "InstrumentSeries | None" = None,
    ) -> DashboardTradeView:
        decision = result.decision
        opportunity = result.opportunity
        candidate = getattr(decision, "candidate", None) if decision else None

        # --- Decision view (reused 11S/11T) ---
        decision_classification = result.decision_classification or ""
        decision_score = int(result.decision_score or 0)
        opportunity_status = ""
        rank = 0
        eligible = bool(result.eligible)
        confluence = 0
        decision_rationale = ""
        if opportunity is not None:
            opp_status = getattr(opportunity, "status", None)
            opportunity_status = getattr(opp_status, "name", "") or ""
            rank = int(getattr(opportunity, "rank", 0) or 0)
            confluence = int(getattr(opportunity, "confluence_score", 0) or 0)
            decision_rationale = getattr(opportunity, "ranking_reason", "") or ""
        if decision is not None and not decision_rationale:
            decision_rationale = getattr(decision, "rationale", "") or ""
        decision_view = DecisionView(
            decision_classification=decision_classification,
            decision_score=decision_score,
            opportunity_status=opportunity_status,
            rank=rank,
            eligible=eligible,
            confluence_score=confluence,
            rationale=decision_rationale,
        )

        # --- Geometry view (reused 11R candidate) ---
        direction = result.direction or ""
        entry = stop = target_1 = None
        risk = reward = ratio = None
        geom_complete = False
        setup_type = ""
        if candidate is not None:
            entry = getattr(candidate, "entry_reference", None)
            stop = getattr(candidate, "stop_reference", None)
            target_1 = getattr(candidate, "target_reference", None)
            risk = getattr(candidate, "risk_distance", None)
            reward = getattr(candidate, "reward_distance", None)
            ratio = getattr(candidate, "risk_reward_ratio", None)
            geom_complete = bool(getattr(candidate, "geometry_complete", False))
            st = getattr(candidate, "setup_type", None)
            setup_type = getattr(st, "name", "") or ""
        geometry_view = GeometryView(
            direction=direction,
            entry=entry,
            stop=stop,
            target_1=target_1,
            target_2=None,
            target_2_supported=False,
            risk_distance=risk,
            reward_distance=reward,
            risk_reward_ratio=ratio,
            invalidation_level=stop,
            geometry_available=geom_complete,
            geometry_complete_source=geom_complete,
        )

        # --- Market overview view (reused 11P context + 11U alignment) ---
        market_overview = self._build_market_overview(result, setup_tf)

        # --- Evidence view (reused 11Y/11Z/12A/12B/12E or honest unavailable) ---
        evidence_view = self._build_evidence_view(request, result, candidate)

        # --- Historical context view (Phase 6E; additive, contextual only) ---
        historical_context_view = self._build_historical_context_view(
            request, setup_tf, ctx_tf, result, candidate,
        )

        # --- Actionability (derived presentation mirror) ---
        # Evidence strength is surfaced to the actionability layer ONLY when
        # an offline corpus is attached; a missing corpus is NOT treated as
        # INSUFFICIENT evidence (it is surfaced as a separate warning).
        evidence_strength_for_actionability: str | None = None
        if evidence_view.available:
            evidence_strength_for_actionability = evidence_view.evidence_strength
        actionability = derive_actionability(
            complete=bool(result.complete),
            decision_classification=decision_classification,
            opportunity_status=opportunity_status,
            eligible=eligible,
            geometry_available=geom_complete,
            evidence_strength=evidence_strength_for_actionability,
        )
        actionability_detail = ActionabilityDetail(
            state=actionability,
            reason=derive_actionability_reason(actionability),
        )

        # --- Honesty warnings ---
        warnings: list[str] = []
        if not result.complete:
            warnings.append(
                "Scan INCOMPLETE: required timeframe/context data is "
                "missing; no directional conclusion is drawn.",
            )
        if candidate is not None and not geom_complete:
            warnings.append(
                "Trade geometry is incomplete: the structural references "
                "needed for a complete entry/stop/target are not all "
                "available at this point. No level is fabricated.",
            )
        if (
            evidence_view.available
            and evidence_view.evidence_strength == "INSUFFICIENT"
        ):
            warnings.append(
                "Historical evidence is INSUFFICIENT (sample size below "
                "the configured minimum). Observed metrics are not "
                "reliable evidence and must not be treated as such.",
            )
        if not evidence_view.available:
            warnings.append(
                "No offline historical evidence corpus is attached; "
                "evidence is UNAVAILABLE (never fabricated).",
            )
        if market_overview.data_stale:
            warnings.append(
                "Latest candle appears stale relative to the configured "
                "staleness threshold.",
            )
        if series is not None and series.freshness_state == FreshnessState.STALE:
            warnings.append(
                "Data source reports the latest completed candle as STALE "
                "relative to the configured freshness threshold. The "
                "analysis is still produced over completed candles; "
                "freshness is data quality, not a trading signal.",
            )
        if series is not None and series.rejected_future_count > 0:
            warnings.append(
                f"Provider returned {series.rejected_future_count} "
                "future-dated candle(s); they were rejected and never "
                "used by the analysis.",
            )

        data_source_view = self._build_data_source_view(series)

        return DashboardTradeView(
            instrument=request.instrument,
            context_timeframe=ctx_tf,
            setup_timeframe=setup_tf,
            evaluation_timestamp=result.timestamp,
            scan_status=_scan_status_name(result),
            complete=bool(result.complete),
            market_overview=market_overview,
            decision=decision_view,
            geometry=geometry_view,
            evidence=evidence_view,
            setup_type=setup_type,
            actionability=actionability,
            actionability_detail=actionability_detail,
            data_source=data_source_view,
            historical_context=historical_context_view,
            reason=result.reason or "",
            warnings=tuple(warnings),
        )

    def _build_market_overview(
        self, result: InstrumentScanResult, setup_tf: str,
    ) -> MarketOverviewView:
        lower: MarketContext | None = result.lower_context  # type: ignore[assignment]
        higher: MarketContext | None = result.higher_context  # type: ignore[assignment]

        last_price = None
        latest_ts = result.timestamp
        if result.decision is not None:
            cand = getattr(result.decision, "candidate", None)
            if cand is not None:
                last_price = getattr(cand, "entry_reference", None)

        htf_trend = "UNKNOWN"
        ltf_trend = "UNKNOWN"
        range_state = "UNKNOWN"
        recent_structure = ""
        support = None
        resistance = None
        price_location = "UNKNOWN"
        confirmed_swings = 0
        if higher is not None:
            htf_trend = higher.trend.state.name
        if lower is not None:
            ltf_trend = lower.trend.state.name
            range_state = lower.range.state.name
            confirmed_swings = int(lower.confirmed_swings)
            sr = lower.support_resistance
            support = sr.support
            resistance = sr.resistance
            price_location = sr.location.name
            if lower.recent_structure:
                recent_structure = ", ".join(
                    _structure_short(s) for s in lower.recent_structure
                )
            if last_price is None:
                # Fall back to a price implied by the S/R context if the
                # candidate did not carry an entry (e.g. NO_CANDIDATE).
                pass

        # If we still have no last_price, derive it from the setup
        # candle close via the decision timestamp is not directly
        # available here; the service keeps the candidate entry as the
        # honest reference and leaves None otherwise.
        mtf_alignment = result.alignment.name if result.alignment else "UNKNOWN"

        data_stale = False
        if latest_ts is not None:
            data_stale = (
                datetime.now(latest_ts.tzinfo) - latest_ts
            ).total_seconds() > self.staleness_seconds

        return MarketOverviewView(
            last_price=last_price,
            latest_candle_timestamp=latest_ts,
            htf_trend=htf_trend,
            ltf_trend=ltf_trend,
            range_state=range_state,
            recent_structure=recent_structure,
            support=support,
            resistance=resistance,
            price_location=price_location,
            mtf_alignment=mtf_alignment,
            confirmed_swings=confirmed_swings,
            data_stale=data_stale,
        )

    def _build_evidence_view(
        self,
        request: AnalysisRequest,
        result: InstrumentScanResult,
        candidate: Any,
    ) -> EvidenceView:
        if self.evidence_source is None:
            return EvidenceView(
                available=False,
                limitations=(
                    "No offline historical evidence corpus attached. "
                    "Evidence is UNAVAILABLE; no strength, win rate or "
                    "sample size is fabricated."
                ),
            )
        return self.evidence_source.evidence_for(request, result, candidate)

    def _build_historical_context_view(
        self,
        request: AnalysisRequest,
        setup_tf: str,
        ctx_tf: str,
        result: InstrumentScanResult,
        candidate: Any,
    ) -> HistoricalContextView:
        """Product Phase 6E — attach the historical evidence context.

        CONTEXTUAL ONLY: the lookup NEVER modifies the authoritative
        existing decision / geometry / plan. When no Phase 6D research
        store is attached, the honest "UNAVAILABLE" view is returned —
        never fabricated evidence. Failures are isolated (a lookup
        problem must never corrupt the current assessment).
        """

        if self.historical_evidence_source is None:
            return HistoricalContextView(
                limitations=(
                    "No Phase 6D historical research store attached. "
                    "Historical evidence is UNAVAILABLE (never fabricated)."
                ),
            )
        try:
            return self.historical_evidence_source.context_for(
                request, setup_tf, ctx_tf, result, candidate,
            )
        except Exception:  # noqa: BLE001 - failure isolation
            return HistoricalContextView(
                limitations=(
                    "Historical evidence lookup failed; historical "
                    "evidence is UNAVAILABLE (never fabricated)."
                ),
            )

    @staticmethod
    def _build_data_source_view(
        series: "InstrumentSeries | None",
    ) -> DataSourceView:
        """Project the provider's :class:`InstrumentSeries` metadata.

        Pure projection — DATA QUALITY only; never alters decision
        semantics. When no series is available (e.g. a hand-built
        unavailable view), every field is the honest empty/None
        sentinel.
        """

        if series is None:
            return DataSourceView()
        return DataSourceView(
            data_source=series.data_source,
            provider_status=series.provider_status.value,
            freshness_state=series.freshness_state.value,
            latest_candle_timestamp=series.latest_candle_timestamp,
            latest_completed_candle_timestamp=(
                series.latest_completed_candle_timestamp
            ),
            forming_candle_present=series.forming_setup_candle is not None,
            last_successful_fetch_time=series.last_successful_fetch_time,
            rejected_future_count=series.rejected_future_count,
        )

    def _unavailable_view(
        self,
        request: AnalysisRequest,
        ctx_tf: str,
        reason: str,
        *,
        series: "InstrumentSeries | None" = None,
    ) -> DashboardTradeView:
        data_source_view = self._build_data_source_view(series)
        warnings: list[str] = [
            "Market data unavailable for the selected instrument / "
            "timeframe. No analysis, geometry or evidence is "
            "fabricated.",
        ]
        if (
            series is not None
            and series.provider_status == ProviderStatus.ERROR
        ):
            warnings.append(
                "Provider reported an ERROR fetching market data; "
                "the failure is NOT converted into a successful "
                "analysis and NO fixture fallback was applied.",
            )
        if (
            series is not None
            and series.provider_status == ProviderStatus.NOT_READY
        ):
            warnings.append(
                "Provider is NOT READY (optional dependency missing or "
                "init failed); live data is unavailable. Select the "
                "fixture data source for a runnable deterministic view.",
            )
        return DashboardTradeView(
            instrument=request.instrument,
            context_timeframe=ctx_tf,
            setup_timeframe=request.setup_timeframe,
            evaluation_timestamp=None,
            scan_status="INCOMPLETE",
            complete=False,
            market_overview=MarketOverviewView(),
            decision=DecisionView(),
            geometry=GeometryView(),
            evidence=EvidenceView(
                available=False,
                limitations=(
                    "Market data unavailable; no analysis is produced. "
                    + reason
                ),
            ),
            setup_type="",
            actionability=ActionabilityState.INVALID,
            actionability_detail=ActionabilityDetail(
                state=ActionabilityState.INVALID,
                reason=derive_actionability_reason(ActionabilityState.INVALID),
            ),
            data_source=data_source_view,
            reason=reason,
            warnings=tuple(warnings),
        )

    # ------------------------------------------------------------
    # CHART PAYLOAD
    # ------------------------------------------------------------

    def chart_payload(
        self, request: AnalysisRequest, view: DashboardTradeView,
    ) -> ChartPayload:
        """
        Build the candlestick chart payload from the fetched setup
        candles + the view's geometry / levels.

        The frontend renders this verbatim; it never invents levels.
        """

        series = self.provider.fetch(request.instrument, request.setup_timeframe)
        setup = series.setup_candles
        if not setup:
            return ChartPayload()
        # Cap to the most recent N candles for readability.
        visible = setup[-self.max_chart_candles:]
        candles = tuple(
            (
                c.timestamp.isoformat(),
                float(c.open),
                float(c.high),
                float(c.low),
                float(c.close),
            )
            for c in visible
        )
        return ChartPayload(
            candles=candles,
            entry=view.geometry.entry,
            stop=view.geometry.stop,
            target_1=view.geometry.target_1,
            support=view.market_overview.support,
            resistance=view.market_overview.resistance,
            invalidation_level=view.geometry.invalidation_level,
        )

    # ------------------------------------------------------------
    # HELPERS
    # ------------------------------------------------------------

    @staticmethod
    def _scanner_setup_label(setup_tf: str) -> str:
        # The fixtures use "15M"; normalize 15m -> 15M for the scanner
        # config label so the fixture lookup matches.
        return setup_tf.upper() if setup_tf.lower() in ("15m",) else setup_tf


def _scan_status_name(result: InstrumentScanResult) -> str:
    """Derive a coarse scan-status label for the view (presentation only)."""

    if not result.complete:
        return "INCOMPLETE"
    if result.eligible:
        return "OPPORTUNITIES_FOUND"
    if result.opportunity is not None:
        return "WATCH_ONLY"
    return "NO_OPPORTUNITY"


def _to_trade_plan_view(plan: TradePlan) -> TradePlanView:
    """Project a :class:`TradePlan` into a :class:`TradePlanView`.

    Pure projection — every value is copied verbatim from the plan model.
    No value is recomputed; no decision / geometry semantics are
    duplicated. Used by :meth:`DashboardAnalysisService.plan_trade`.
    """

    return TradePlanView(
        plan_id=plan.plan_id,
        instrument=plan.instrument,
        timeframe=plan.timeframe,
        direction=plan.direction,
        existing_decision=plan.existing_decision,
        actionability=plan.actionability,
        account_capital=plan.account_capital,
        risk_percent=plan.risk_percent,
        maximum_risk=plan.maximum_risk,
        entry=plan.entry,
        stop=plan.stop,
        target_1=plan.target_1,
        target_2=plan.target_2,
        target_2_supported=plan.target_2_supported,
        engine_risk_distance=plan.engine_risk_distance,
        engine_reward_distance=plan.engine_reward_distance,
        engine_risk_reward_ratio=plan.engine_risk_reward_ratio,
        quantity=plan.quantity,
        planned_risk=plan.planned_risk,
        planned_reward=plan.planned_reward,
        quantity_status=plan.quantity_status.value,
        risk_plan_status=plan.risk_plan_status.value,
        quantity_spec_available=plan.quantity_spec_available,
        warnings=plan.warnings,
        rationale=plan.rationale,
        label=plan.label,
        metadata=plan.metadata,
    )


def _performance_to_jsonable(analytics: Any) -> dict[str, Any]:
    """Project a :class:`PaperTradePerformanceAnalytics` into a JSON dict.

    Pure projection — DESCRIPTIVE ONLY. ``Decimal`` P&L values are
    rendered as strings so monetary precision survives the JSON round
    trip; a parallel ``_float`` field is included for convenience. No
    statistical-significance / predictive claim is made.
    """

    def _dec(d: Decimal | None) -> str | None:
        return None if d is None else str(d)

    def _decf(d: Decimal | None) -> float | None:
        return None if d is None else float(d)

    def _rate(r: float | None) -> float | None:
        return None if r is None else float(r)

    def _stats(s: Any) -> dict[str, Any]:
        return {
            "total": s.total,
            "waiting": s.waiting,
            "open": s.open,
            "closed": s.closed,
            "cancelled": s.cancelled,
            "invalidated": s.invalidated,
            "wins": s.wins,
            "losses": s.losses,
            "ambiguous": s.ambiguous,
            "expired": s.expired,
            "manual_close": s.manual_close,
            "win_rate": _rate(s.win_rate),
            "loss_rate": _rate(s.loss_rate),
            "total_realized_r": s.total_realized_r,
            "average_realized_r": s.average_realized_r,
            "median_realized_r": s.median_realized_r,
            "gross_positive_r": s.gross_positive_r,
            "gross_negative_r": s.gross_negative_r,
            "profit_factor": s.profit_factor,
            "valid_r_count": s.valid_r_count,
            "total_realized_pnl": _dec(s.total_realized_pnl),
            "total_realized_pnl_float": _decf(s.total_realized_pnl),
            "average_realized_pnl": _dec(s.average_realized_pnl),
            "average_realized_pnl_float": _decf(s.average_realized_pnl),
            "valid_pnl_count": s.valid_pnl_count,
        }

    breakdowns: list[dict[str, Any]] = []
    for bd in analytics.breakdowns:
        breakdowns.append(
            {
                "dimension": bd.dimension.value,
                "groups": [
                    {"key": g.key, "statistics": _stats(g.statistics)}
                    for g in bd.groups
                ],
            }
        )
    return {
        "analytics_id": analytics.analytics_id,
        "trade_count": analytics.trade_count,
        "label": analytics.label,
        "metadata": [[k, v] for k, v in analytics.metadata],
        "rationale": analytics.rationale,
        "overall": _stats(analytics.overall),
        "breakdowns": breakdowns,
        "is_empty": analytics.is_empty,
    }


def _structure_short(point: Any) -> str:
    """Short label for a recent structure point (e.g. ``HH``)."""

    name = getattr(getattr(point, "structure", None), "name", "")
    return {
        "HIGHER_HIGH": "HH",
        "LOWER_HIGH": "LH",
        "HIGHER_LOW": "HL",
        "LOWER_LOW": "LL",
        "FIRST_HIGH": "FH",
        "FIRST_LOW": "FL",
    }.get(name, name)


# ============================================================
# OPTIONAL OFFLINE EVIDENCE SOURCE
# ============================================================


class EvidenceSource:
    """
    Optional offline evidence source.

    The dashboard does NOT compute evidence. A caller may pre-compute a
    Sprint 11Y :class:`HistoricalEvidenceReport` (over an offline
    historical outcome corpus) and attach it here; the dashboard then
    reuses the Sprint 11Z :meth:`StrategyIntelligenceEngine.lookup` to
    surface the matched cohort's evidence for the current opportunity —
    WITHOUT re-evaluating outcomes, re-reading candles or using future
    information.

    When no evidence report is attached, the dashboard shows an honest
    "UNAVAILABLE" evidence state.
    """

    def __init__(
        self,
        evidence_report: Any | None = None,
        strategy_engine: Any | None = None,
    ) -> None:
        self.evidence_report = evidence_report
        self.strategy_engine = strategy_engine
        if strategy_engine is None and evidence_report is not None:
            from engine.intelligence.strategy_intelligence import (
                StrategyIntelligenceEngine,
            )
            self.strategy_engine = StrategyIntelligenceEngine()

    def evidence_for(
        self,
        request: AnalysisRequest,
        result: InstrumentScanResult,
        candidate: Any,
    ) -> EvidenceView:
        if self.evidence_report is None or self.strategy_engine is None:
            return EvidenceView(
                available=False,
                limitations="No offline historical evidence corpus attached.",
            )
        from engine.models.strategy_intelligence import OpportunityProfile

        direction = result.direction or ""
        setup_type = ""
        if candidate is not None:
            st = getattr(candidate, "setup_type", None)
            setup_type = getattr(st, "name", "") or ""
        mtf_alignment = result.alignment.name if result.alignment else ""
        profile = OpportunityProfile(
            instrument=request.instrument,
            direction=direction,
            setup_type=setup_type,
            mtf_alignment=mtf_alignment,
        )
        lookup = self.strategy_engine.lookup(self.evidence_report, profile)
        assessment = getattr(lookup, "assessment", None)
        cohort = getattr(lookup, "matched_cohort", None)

        available = cohort is not None and assessment is not None
        strength = "UNAVAILABLE"
        strategy_interp = "UNAVAILABLE"
        cohort_key = ""
        sample = None
        win_rate = None
        avg_r = None
        profit_factor = None
        limitations = ""
        if available:
            strength = getattr(
                getattr(assessment, "evidence_strength", None), "name", "UNAVAILABLE",
            ) or "UNAVAILABLE"
            strategy_interp = getattr(
                getattr(assessment, "assessment_status", None), "name", "UNAVAILABLE",
            ) or "UNAVAILABLE"
            cohort_key = getattr(cohort, "key", "") or ""
            stats = getattr(cohort, "statistics", None)
            sample = int(getattr(stats, "total", 0) or 0) if stats else 0
            if stats is not None:
                win_rate = getattr(stats, "win_rate", None)
                avg_r = getattr(stats, "average_realized_r", None)
                profit_factor = getattr(stats, "profit_factor", None)
            limitations = getattr(assessment, "limitations", "") or ""
        else:
            limitations = (
                "No historical cohort matches this opportunity's profile; "
                "evidence is UNAVAILABLE (never fabricated)."
            )
        return EvidenceView(
            available=available,
            evidence_strength=strength,
            strategy_interpretation=strategy_interp,
            cohort_key=cohort_key,
            sample_size=sample,
            win_rate=win_rate,
            avg_realized_r=avg_r,
            profit_factor=profit_factor,
            limitations=limitations,
        )


class HistoricalEvidenceSource:
    """
    Optional Phase 6D historical evidence source (Product Phase 6E).

    The dashboard does NOT run research. A caller persists Phase 6D
    :class:`~engine.models.setup_research.SetupResearchResult` objects
    in a :class:`~engine.data.setup_research_store.SetupResearchStore`
    and attaches the store here; the dashboard then reuses the Phase 6E
    :class:`~engine.data.historical_evidence_lookup.HistoricalEvidenceLookupEngine`
    to surface the historical evidence available for setups COMPARABLE
    to the current assessment — WITHOUT re-running research, re-reading
    candles or using future information.

    The historical context is CONTEXTUAL ONLY: it NEVER modifies the
    authoritative existing decision / actionability / geometry / trade
    plan / paper-trading eligibility. When no store is attached, the
    dashboard shows an honest "UNAVAILABLE" historical context.
    """

    def __init__(
        self,
        store: Any | None = None,
        lookup_engine: Any | None = None,
        config: Any | None = None,
    ) -> None:
        if lookup_engine is None and store is not None:
            from engine.data.historical_evidence_lookup import (
                HistoricalEvidenceLookupEngine,
            )

            lookup_engine = HistoricalEvidenceLookupEngine(store, config)
        self.lookup_engine = lookup_engine

    @staticmethod
    def _clean(value: str) -> str:
        """Drop absent sentinels — an UNKNOWN/NONE current label is NOT a
        comparison dimension (matching only on what is actually known)."""

        return "" if value in ("", "NONE", "UNKNOWN") else value

    def context_for(
        self,
        request: AnalysisRequest,
        setup_tf: str,
        ctx_tf: str,
        result: InstrumentScanResult,
        candidate: Any,
    ) -> HistoricalContextView:
        if self.lookup_engine is None:
            return HistoricalContextView(
                limitations=(
                    "No Phase 6D historical research store attached. "
                    "Historical evidence is UNAVAILABLE (never fabricated)."
                ),
            )
        from engine.models.historical_context import HistoricalEvidenceRequest

        setup_type = ""
        if candidate is not None:
            st = getattr(candidate, "setup_type", None)
            setup_type = getattr(st, "name", "") or ""
        direction = getattr(result, "direction", "") or ""
        mtf_alignment = result.alignment.name if result.alignment else ""
        trend_state = ""
        range_state = ""
        lower = getattr(result, "lower_context", None)
        if lower is not None:
            trend = getattr(lower, "trend", None)
            trend_state = getattr(getattr(trend, "state", None), "name", "") or ""
            rng = getattr(lower, "range", None)
            range_state = getattr(getattr(rng, "state", None), "name", "") or ""

        lookup_request = HistoricalEvidenceRequest(
            instrument=request.instrument,
            setup_timeframe=setup_tf,
            context_timeframe=ctx_tf,
            evaluation_time=getattr(result, "timestamp", None),
            setup_type=self._clean(setup_type),
            direction=self._clean(direction),
            trend_state=self._clean(trend_state),
            range_state=self._clean(range_state),
            mtf_alignment=self._clean(mtf_alignment),
        )
        context = self.lookup_engine.lookup(lookup_request)

        strength = (
            context.strength.name if context.strength is not None else "UNAVAILABLE"
        )
        stats = context.statistics
        return HistoricalContextView(
            available=context.is_available,
            status=context.status.name,
            evidence_strength=strength,
            match_key=context.match_key,
            comparable_occurrences=context.comparable_occurrences,
            completed_outcomes=context.completed_outcomes,
            ambiguous_count=context.ambiguous_count,
            unresolved_count=context.unresolved_count,
            win_rate=getattr(stats, "win_rate", None) if stats else None,
            average_realized_r=(
                getattr(stats, "average_realized_r", None) if stats else None
            ),
            median_realized_r=(
                getattr(stats, "median_realized_r", None) if stats else None
            ),
            profit_factor=getattr(stats, "profit_factor", None) if stats else None,
            research_ids=context.research_ids,
            reason=context.reason,
            limitations=" ".join(context.limitations),
        )


def default_service(
    provider_name: str = "fixture",
    evidence_report: Any | None = None,
    *,
    freshness_config: FreshnessConfig | None = None,
    symbol_map: dict[str, str] | None = None,
    paper_trade_store: Any | None = None,
    historical_service: "HistoricalMarketDataService | None" = None,
    historical_evidence_source: "HistoricalEvidenceSource | None" = None,
) -> DashboardAnalysisService:
    """Build a dashboard service with sensible defaults.

    ``paper_trade_store`` is optional (Product Phase 5). When supplied,
    paper trades are persisted and survive restarts; when ``None`` the
    paper-trade service methods raise :class:`LookupError` on load.

    ``historical_service`` is optional (Product Phase 6A). When
    supplied, the /historical-data status surface lists stored
    historical datasets; when ``None`` the surface reports an honest
    empty state (nothing is fabricated). When omitted entirely the
    default service builds a local deterministic historical service
    with the default store so the status page is functional out of the
    box with NO network / API-key dependency.
    """

    provider = make_provider(
        provider_name,
        freshness_config=freshness_config,
        symbol_map=symbol_map,
    )
    evidence_source = (
        EvidenceSource(evidence_report) if evidence_report is not None else None
    )
    if historical_service is None:
        from engine.data.historical_store import HistoricalDataStore

        historical_service = HistoricalMarketDataService(
            store=HistoricalDataStore(),
        )
    return DashboardAnalysisService(
        provider=provider,
        evidence_source=evidence_source,
        freshness_config=freshness_config,
        paper_trade_store=paper_trade_store,
        historical_service=historical_service,
        historical_evidence_source=historical_evidence_source,
    )


__all__ = [
    "AnalysisRequest",
    "ChartPayload",
    "DashboardAnalysisService",
    "DEFAULT_STALENESS_SECONDS",
    "EvidenceSource",
    "HistoricalEvidenceSource",
    "OperationsRequest",
    "PaperTradeManualCloseRequest",
    "PaperTradeRequest",
    "PaperTradeTrackRequest",
    "ScanRequest",
    "TradePlanRequest",
    "WorkstationRequest",
    "default_service",
]
