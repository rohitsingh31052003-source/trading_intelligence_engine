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
from typing import Any, Mapping

from engine.config.market_scan_config import MarketScanConfig
from engine.intelligence.market_scanner import (
    InstrumentDataset,
    MarketScanner,
    ScanEngines,
)
from engine.models.market_context import MarketContext
from engine.models.market_scan import InstrumentScanResult, MTFAlignment
from engine.models.ohlcv import OHLCVCandle

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
    MarketOverviewView,
    derive_actionability,
    derive_actionability_reason,
)

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

    # ------------------------------------------------------------
    # METADATA (for selectors)
    # ------------------------------------------------------------

    def available_instruments(self) -> tuple[str, ...]:
        """Instruments the configured provider can serve."""

        if isinstance(self.provider, FixtureDataProvider):
            return FIXTURE_INSTRUMENTS
        # Live providers: expose a sensible default set; the actual
        # availability is checked per-request (graceful on miss).
        return FIXTURE_INSTRUMENTS

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


def default_service(
    provider_name: str = "fixture",
    evidence_report: Any | None = None,
    *,
    freshness_config: FreshnessConfig | None = None,
    symbol_map: dict[str, str] | None = None,
) -> DashboardAnalysisService:
    """Build a dashboard service with sensible defaults."""

    provider = make_provider(
        provider_name,
        freshness_config=freshness_config,
        symbol_map=symbol_map,
    )
    evidence_source = (
        EvidenceSource(evidence_report) if evidence_report is not None else None
    )
    return DashboardAnalysisService(
        provider=provider,
        evidence_source=evidence_source,
        freshness_config=freshness_config,
    )


__all__ = [
    "AnalysisRequest",
    "ChartPayload",
    "DashboardAnalysisService",
    "DEFAULT_STALENESS_SECONDS",
    "EvidenceSource",
    "default_service",
]
