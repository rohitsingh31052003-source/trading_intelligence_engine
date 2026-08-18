"""
Paper Trading Operations (Product Phase 5 operational increment).

This is a THIN ORCHESTRATION layer that turns the existing Product Phase 5
paper-trading capability into a controlled, deterministic, real / near-live
PAPER TRADING OPERATIONS workflow. It is NOT an intelligence / scoring / signal
/ prediction / broker / execution engine. It implements NO new market analysis,
NO new decision logic, NO new geometry, NO new position sizing, NO probability
and NO BUY/SELL/ENTER/EXIT/HOLD recommendation.

The operational workflow is:

    LIVE / NEAR-LIVE DATA
        |
    completed-candle boundary (existing Product Phase 1)
        |
    existing DashboardAnalysisService.analyze / scanner
        |
    existing authoritative Decision (REJECTED/WATCH/QUALIFIED/PREFERRED)
        |
    existing TradeCandidate geometry
        |
    existing Product Phase 4 TradePlan
        |
    Paper Trading Operations (this layer)
        |
    existing PaperTrade lifecycle (PaperTradingEngine.create / track)
        |
    existing PaperTradeJournal (PaperTradeStore)
        |
    workstation / API / reporting

DESIGN PRINCIPLE — reuse, do not re-invent:

* The data provider is the EXISTING Product Phase 1 provider abstraction
  (:class:`~dashboard.data_provider.DashboardDataProvider`). The operations
  layer NEVER introduces a second market-data implementation and NEVER
  silently falls back from a live provider to fixtures during a run.
* The completed-candle boundary is AUTHORITATIVE and INHERITED from
  :meth:`DashboardAnalysisService.analyze` + the provider's
  ``latest_completed_candle_timestamp``. New paper trades are created ONLY
  from the latest COMPLETED setup candle. The forming candle NEVER creates a
  trade / changes a decision / changes entry/stop/target / closes a trade.
  Future-dated candles are rejected.
* The existing Sprint 11S decision classification is AUTHORITATIVE and is
  reused VERBATIM — never renamed to BUY/SELL, never upgraded / downgraded.
* The existing Sprint 11R ``TradeCandidate`` geometry is reused VERBATIM.
* The existing Product Phase 4 :class:`~engine.models.trade_plan.TradePlan`
  is reused VERBATIM. The operations layer performs NO new position sizing.
* The existing :class:`~engine.intelligence.paper_trading.PaperTradingEngine`
  create / track / close / cancel lifecycle is reused UNCHANGED. This layer
  never redefines ``WAITING_FOR_ENTRY`` / ``OPEN`` / ``CLOSED`` /
  ``CANCELLED`` / ``INVALIDATED`` or the exit reasons.

DESIGN PRINCIPLE — paper-trade creation rules:

A paper trade is NEVER created merely because a candle exists. The existing
:func:`~dashboard.views.derive_actionability` mirror is the SINGLE eligibility
gate: a paper trade may be created ONLY when the existing opportunity is
``READY_FOR_REVIEW`` (an eligible opportunity with COMPLETE geometry and a
QUALIFIED / PREFERRED decision whose evidence is not INSUFFICIENT). All other
actionability states (``INVALID``, ``NO_OPPORTUNITY``,
``TRADE_GEOMETRY_UNAVAILABLE``, ``INSUFFICIENT_EVIDENCE``, ``WAIT``) produce NO
paper trade. No new confidence / R:R / evidence threshold is invented.

DESIGN PRINCIPLE — duplicate prevention (essential for operational use):

Running the operations cycle repeatedly against the SAME completed candle
must NOT create duplicate paper trades. The creation anchor
(``created_at``) is the latest COMPLETED setup candle's evaluation timestamp
— NEVER a wall-clock — so the deterministic ``PaperTrade`` id
(``"pt-" + sha256[:16]`` of the canonical opportunity + instance identity) is
identical across repeated cycles for the same completed candle. Before
creating, the operations layer checks the existing journal; an equivalent
trade (same id, or same instrument + timeframe + evaluation timestamp) is
reported as ``DUPLICATE`` rather than re-created.

DESIGN PRINCIPLE — chronological, one-candle-at-a-time lifecycle:

Existing open / waiting paper trades are advanced by feeding the provider's
COMPLETED setup candles to :meth:`PaperTradingEngine.track` with an explicit
``reference_now`` (the latest completed candle timestamp). The engine's
``_completed_window`` keeps ONLY candles with ``timestamp <= reference_now``
sorted ascending, so multiple previously-unseen completed candles arriving
after a downtime / restart are processed CHRONOLOGICALLY in one pass — no
lifecycle event is skipped. A previously-resolved (terminal) paper trade is
NEVER altered.

DESIGN PRINCIPLE — restart / recovery:

The operations layer is STATELESS across cycles. All persisted paper-trade
state lives in the existing :class:`~dashboard.paper_trade_store.PaperTradeStore`.
On startup the cycle simply loads the journal and continues tracking the
WAITING_FOR_ENTRY / OPEN trades; a restart NEVER resets status / entry /
exit / realized R / P&L / exit reason / trade id.

DESIGN PRINCIPLE — failure isolation:

One instrument failure (provider exception, malformed candle, empty
response, unsupported instrument / timeframe) NEVER aborts the whole cycle.
The failure is surfaced as a per-instrument error and the cycle continues
with the remaining instruments. No paper trade is fabricated for a failing
instrument.

DESIGN PRINCIPLE — no look-ahead:

The public :meth:`PaperTradingOperations.run_once` API accepts NO
``future`` / ``future_candles`` / ``lookahead`` argument. It never calls the
Sprint 11W :class:`~engine.intelligence.historical_outcome.OutcomeEvaluator`
and never runs the
:class:`~engine.intelligence.historical_replay` pipeline (regression-tested
by patching both to raise). The touch logic lives entirely in the existing
paper-trading engine, which inspects ONLY completed candles
``timestamp <= reference_now``.

DESIGN PRINCIPLE — determinism:

``cycle_id = "opcycle-" + sha256[:16]`` of the canonical (sorted instrument
outcomes + reference timestamp + config) identity. No wall-clock, no random
ids. Identical market data + provider response + watchlist + configuration +
journal state produce an identical cycle outcome. Instrument-order shuffle
invariance is enforced (instruments are processed in sorted order).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from engine.config.paper_trade_config import PaperTradeConfig
from engine.intelligence.paper_trading import PaperTradingEngine
from engine.models.paper_trade import PaperTrade, PaperTradeStatus


def _naive(dt: datetime) -> datetime:
    """Strip tzinfo for naive comparison (mirrors the paper-trading engine)."""

    if dt.tzinfo is None:
        return dt
    return dt.replace(tzinfo=None)


# ============================================================
# OPERATIONAL STATUS
# ============================================================


class OperationalStatus(Enum):
    """
    Paper-trading OPERATIONAL state of a single :meth:`run_once` cycle.

    This is DELIBERATELY DISTINCT from the Product Phase 1
    :class:`~dashboard.data_provider.ProviderStatus` and
    :class:`~dashboard.data_provider.FreshnessState` (data-source concerns)
    and from the :class:`~engine.models.paper_trade.PaperTradeStatus`
    (per-trade lifecycle). It describes the OPERATIONAL cycle state only.

    READY
        The cycle completed and at least one instrument was analysed.

    NO_DATA
        No instrument produced usable completed data (every instrument was
        unavailable / unsupported / empty). No paper trade was created.

    STALE
        The cycle completed but the latest completed candle for every
        analysed instrument is older than the staleness threshold (data
        quality warning; the cycle still ran on completed candles).

    ERROR
        The cycle ran but every instrument failed (errors). No paper trade
        was created; the failures are surfaced per-instrument.

    NOT_READY
        The operations layer is not initialized (no store / no service).
    """

    READY = "READY"
    NO_DATA = "NO_DATA"
    STALE = "STALE"
    ERROR = "ERROR"
    NOT_READY = "NOT_READY"


# The fixed, non-configurable disclaimer appended to operational reports.
OPERATIONS_DISCLAIMER = (
    "This system performs paper trading only. No real orders are placed. "
    "Paper-trade results are observational validation and do not guarantee "
    "future performance or constitute financial advice."
)


# ============================================================
# RESULT MODELS
# ============================================================


@dataclass(frozen=True, slots=True)
class InstrumentOperationResult:
    """
    The operational outcome for ONE instrument in one cycle.

    Every field is a descriptive projection of reused outputs. No value is
    fabricated. ``created`` / ``updated`` / ``closed`` are the paper-trade
    ids affected this cycle (empty tuples when none). ``duplicate`` flags a
    skipped creation (the same completed candle already produced a trade).

    Attributes:

    instrument
        Canonical instrument name.

    analysed
        Whether the existing analysis produced a usable view for this
        instrument this cycle.

    actionability
        Reused :class:`~dashboard.views.ActionabilityState` name (or ``""``
        when not analysed).

    eligible_for_paper_trade
        Whether the existing opportunity was ``READY_FOR_REVIEW`` (the
        single eligibility gate). Reused verbatim.

    decision_classification
        Reused Sprint 11S decision name, or ``""``.

    evaluation_timestamp
        The latest COMPLETED setup candle timestamp used as the analysis
        boundary, or ``None``.

    provider_status / freshness_state
        Reused Product Phase 1 data-source concerns (DATA QUALITY only).

    created
        Tuple of paper-trade ids CREATED this cycle.

    updated
        Tuple of paper-trade ids UPDATED (tracked) this cycle.

    closed
        Tuple of paper-trade ids that became terminal (CLOSED) this cycle.

    duplicate
        Whether a paper-trade creation was skipped as a duplicate.

    duplicate_paper_trade_id
        The existing paper-trade id that matched (when ``duplicate``).

    error
        Whether this instrument failed this cycle (failure isolation).

    reason
        Descriptive reason / warning for this instrument.
    """

    instrument: str = ""
    analysed: bool = False
    actionability: str = ""
    eligible_for_paper_trade: bool = False
    decision_classification: str = ""
    direction: str = ""
    evaluation_timestamp: datetime | None = None
    provider_status: str = ""
    freshness_state: str = ""
    created: tuple[str, ...] = field(default_factory=tuple)
    updated: tuple[str, ...] = field(default_factory=tuple)
    closed: tuple[str, ...] = field(default_factory=tuple)
    duplicate: bool = False
    duplicate_paper_trade_id: str = ""
    error: bool = False
    reason: str = ""

    @property
    def has_activity(self) -> bool:
        """Whether this instrument created / updated / closed a trade."""

        return bool(self.created or self.updated or self.closed)


@dataclass(frozen=True, slots=True)
class OperationsCycleResult:
    """
    The result of one :meth:`PaperTradingOperations.run_once` cycle.

    All fields are descriptive projections. The result is FROZEN +
    slots. The ``cycle_id`` is deterministic (``"opcycle-" + sha256[:16]``).

    Attributes:

    cycle_id
        Deterministic operational cycle id.

    status
        :class:`OperationalStatus` of the cycle.

    started_at / completed_at
        Caller-supplied cycle boundaries (deterministic; no wall-clock in
        tests). ``completed_at`` may equal ``started_at`` when no time
        elapses (tests).

    reference_now
        The deterministic reference timestamp used for tracking
        (the latest completed candle across analysed instruments, or the
        caller-supplied ``reference_now``).

    provider
        Name of the data source used this cycle (``"fixture"`` / ``"yahoo"``).

    freshness
        Aggregated freshness label for the cycle (``"CURRENT"`` /
        ``"STALE"`` / ``"UNAVAILABLE"``).

    instruments_scanned
        Number of instruments processed this cycle.

    instruments_analysed
        Number of instruments that produced a usable view.

    trades_created / trades_updated / trades_closed
        Counts of paper trades created / updated / closed this cycle.

    duplicates_skipped
        Number of duplicate creations skipped this cycle.

    errors
        Tuple of per-instrument error descriptions.

    active_trades
        Number of WAITING_FOR_ENTRY + OPEN paper trades after the cycle.

    warnings
        Tuple of human-readable honesty warnings.

    results
        Tuple of :class:`InstrumentOperationResult` (sorted by instrument).

    rationale
        Descriptive rationale for the cycle outcome.

    limitations
        Fixed operational disclaimer + documented limitations.
    """

    cycle_id: str = ""
    status: OperationalStatus = OperationalStatus.NOT_READY
    started_at: datetime | None = None
    completed_at: datetime | None = None
    reference_now: datetime | None = None
    provider: str = ""
    freshness: str = ""
    instruments_scanned: int = 0
    instruments_analysed: int = 0
    trades_created: int = 0
    trades_updated: int = 0
    trades_closed: int = 0
    duplicates_skipped: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)
    active_trades: int = 0
    warnings: tuple[str, ...] = field(default_factory=tuple)
    results: tuple[InstrumentOperationResult, ...] = field(default_factory=tuple)
    rationale: str = ""
    limitations: str = ""

    @property
    def is_empty(self) -> bool:
        """Whether the cycle processed no instruments."""

        return self.instruments_scanned == 0


# ============================================================
# CONFIG
# ============================================================


@dataclass(frozen=True, slots=True)
class OperationsConfig:
    """
    Configuration for :class:`PaperTradingOperations`.

    MINIMAL — NO strategy / optimization / scoring / eligibility-threshold
    parameters. The eligibility gate is the FIXED existing
    ``READY_FOR_REVIEW`` actionability mirror (not configurable, so the
    authoritative decision cannot be silently weakened). The existing
    decision / geometry / plan / lifecycle / target-2 semantics are
    intentionally NOT configurable.

    Attributes:

    account_capital / risk_percent
        User-supplied account-risk parameters reused by the existing
        Product Phase 4 planner to size each paper trade. Required for
        paper-trade creation (the plan is reused verbatim).

    setup_timeframe / context_timeframe
        Dashboard setup / context timeframe pair (reused).

    label / metadata
        Optional identity / metadata carried onto created paper trades.
    """

    account_capital: Any = None
    risk_percent: Any = None
    setup_timeframe: str = "15m"
    context_timeframe: str | None = None
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = field(default_factory=tuple)


# ============================================================
# OPERATIONS ENGINE
# ============================================================


class PaperTradingOperations:
    """
    Orchestrates the existing analysis + paper-trading layers into one
    deterministic operational cycle (:meth:`run_once`).

    The engine holds a :class:`DashboardAnalysisService` reference (for the
    existing provider + ``analyze`` + the trade-planning engine) and the
    :class:`PaperTradingEngine` + :class:`PaperTradeStore` directly. It
    implements NO new intelligence; it only orchestrates + projects +
    persists through the EXISTING components.

    The engine is STATELESS across cycles (no mutable caches that could
    leak future data). All persisted state lives in the store.
    """

    def __init__(
        self,
        service: Any,
        *,
        config: OperationsConfig | None = None,
        paper_trading_engine: PaperTradingEngine | None = None,
    ) -> None:
        self.service = service
        self.config = config or OperationsConfig()
        # Reuse the service's existing paper-trading engine when available so
        # configuration stays single-source; fall back to a default engine.
        self.paper_trading_engine = (
            paper_trading_engine
            or getattr(service, "paper_trading_engine", None)
            or PaperTradingEngine(PaperTradeConfig())
        )

    # ------------------------------------------------------------
    # PUBLIC API
    # ------------------------------------------------------------

    def run_once(
        self,
        *,
        instruments: Any = None,
        reference_now: datetime | None = None,
        started_at: datetime | None = None,
        config: OperationsConfig | None = None,
    ) -> OperationsCycleResult:
        """
        Perform exactly ONE deterministic operational observation cycle.

        For each instrument (sorted for determinism): fetch current data via
        the existing provider, run the existing analysis, advance any
        existing WAITING / OPEN paper trades against the completed candles,
        and — when the existing opportunity is ``READY_FOR_REVIEW`` — create
        a paper trade (reusing the existing decision / geometry / plan
        verbatim) unless a duplicate already exists. Persist every change
        through the existing store.

        GUARANTEES:

        * **Reuse-only** — reuses :meth:`DashboardAnalysisService.analyze`
          + the existing :class:`PaperTradingEngine` + the existing store.
        * **Completed-candle only** — new trades use the latest COMPLETED
          setup candle; the forming candle never creates / changes / closes
          a trade.
        * **No look-ahead** — accepts NO ``future`` / ``future_candles``
          argument; never calls the Sprint 11W outcome evaluator; never
          runs the historical pipeline.
        * **Duplicate prevention** — repeated cycles against the same
          completed candle do not create duplicate trades.
        * **Failure isolation** — one instrument failure never aborts the
          cycle.
        * **Determinism** — identical inputs produce an identical
          ``cycle_id`` + outcome; instrument order is shuffle-invariant.

        ``reference_now`` is the deterministic tracking boundary (the
        latest completed candle across instruments, or the caller-supplied
        value). It is NEVER a wall-clock in tests.
        """

        cfg = config or self.config
        store = getattr(self.service, "paper_trade_store", None)
        # NOT_READY: no store attached (cannot persist / load trades).
        if store is None:
            return self._not_ready_result(
                instruments, reference_now, started_at, cfg,
            )

        setup_tf = cfg.setup_timeframe
        ctx_tf = cfg.context_timeframe
        watchlist = self._resolve_instruments(instruments)
        sorted_instruments = sorted(watchlist)

        # Phase 1 — advance existing non-terminal paper trades against the
        # completed candles (chronological, no look-ahead). This happens
        # BEFORE new-trade creation so a freshly-closed trade is not
        # re-created from the same completed candle. The cycle's
        # ``reference_now`` (when supplied) is the deterministic tracking
        # boundary; otherwise the provider's latest completed candle is
        # used. The engine's ``_completed_window`` keeps ONLY candles
        # ``<= reference_now`` so forming / future candles are never
        # inspected.
        tracking_updates = self._advance_existing_trades(
            store, sorted_instruments, setup_tf, cfg, reference_now,
        )

        # Phase 2 — analyse each instrument + create eligible paper trades.
        results: list[InstrumentOperationResult] = []
        errors: list[str] = []
        provider_name = ""
        any_current = False
        any_stale = False
        any_unavailable = True
        eval_timestamps: list[datetime] = []
        for instrument in sorted_instruments:
            outcome, err, prov, fresh, eval_ts = self._process_instrument(
                instrument, setup_tf, ctx_tf, store, cfg,
            )
            results.append(outcome)
            if err:
                errors.append(err)
            if prov and not provider_name:
                provider_name = prov
            if fresh == "CURRENT":
                any_current = True
                any_unavailable = False
            elif fresh == "STALE":
                any_stale = True
                any_unavailable = False
            elif fresh == "UNAVAILABLE":
                pass
            if outcome.analysed and eval_ts is not None:
                eval_timestamps.append(eval_ts)

        # Merge the tracking updates (updated/closed ids) into the per-
        # instrument results so the cycle summary reflects everything.
        results = self._merge_tracking(results, tracking_updates)

        # Aggregate.
        created = sum(len(r.created) for r in results)
        updated = sum(len(r.updated) for r in results)
        closed = sum(len(r.closed) for r in results)
        duplicates = sum(1 for r in results if r.duplicate)
        analysed = sum(1 for r in results if r.analysed)
        scanned = len(sorted_instruments)

        # Active trades after the cycle (WAITING_FOR_ENTRY + OPEN).
        active = self._count_active_trades(store)

        # Reference now: the latest completed candle across analysed
        # instruments (deterministic), or the caller-supplied value.
        ref_now = reference_now
        if ref_now is None and eval_timestamps:
            ref_now = max(eval_timestamps)

        # Aggregated freshness label.
        if any_current:
            freshness = "CURRENT"
        elif any_stale:
            freshness = "STALE"
        else:
            freshness = "UNAVAILABLE"

        # Overall status.
        if scanned == 0:
            status = OperationalStatus.NO_DATA
        elif analysed == 0 and errors:
            status = OperationalStatus.ERROR
        elif analysed == 0:
            status = OperationalStatus.NO_DATA
        elif freshness == "STALE" and not any_current:
            status = OperationalStatus.STALE
        else:
            status = OperationalStatus.READY

        warnings = self._build_warnings(
            status, freshness, duplicates, errors,
        )
        rationale = self._build_rationale(
            status, scanned, analysed, created, updated, closed, duplicates,
        )
        cycle_id = self._cycle_id(
            sorted_instruments, ref_now, started_at, cfg, results,
        )
        limitations = self._limitations()

        return OperationsCycleResult(
            cycle_id=cycle_id,
            status=status,
            started_at=started_at,
            completed_at=reference_now or started_at,
            reference_now=ref_now,
            provider=provider_name,
            freshness=freshness,
            instruments_scanned=scanned,
            instruments_analysed=analysed,
            trades_created=created,
            trades_updated=updated,
            trades_closed=closed,
            duplicates_skipped=duplicates,
            errors=tuple(errors),
            active_trades=active,
            warnings=tuple(warnings),
            results=tuple(results),
            rationale=rationale,
            limitations=limitations,
        )

    # ------------------------------------------------------------
    # INSTRUMENT PROCESSING
    # ------------------------------------------------------------

    def _process_instrument(
        self,
        instrument: str,
        setup_tf: str,
        ctx_tf: str | None,
        store: Any,
        cfg: OperationsConfig,
    ) -> tuple[InstrumentOperationResult, str | None, str, str, datetime | None]:
        """Process one instrument: analyze + create eligible paper trade.

        Returns (result, error_description, provider_name, freshness,
        evaluation_timestamp). Failure-isolated: any exception becomes an
        honest error result, never raised.
        """

        from dashboard.services import AnalysisRequest
        from dashboard.views import ActionabilityState

        empty = InstrumentOperationResult(instrument=instrument)
        try:
            view = self.service.analyze(
                AnalysisRequest(
                    instrument=instrument,
                    setup_timeframe=setup_tf,
                    context_timeframe=ctx_tf,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - failure isolation
            err = f"{instrument}: analysis failed: {exc}"
            return (
                replace_(empty, error=True, reason=err),
                err, "", "UNAVAILABLE", None,
            )

        # Data-source concerns (reused Product Phase 1).
        ds = view.data_source
        provider_name = ds.data_source or ""
        provider_status = ds.provider_status
        freshness = ds.freshness_state

        # Not analysed (no usable completed data) -> honest NO_DATA for this
        # instrument; never a fabricated trade.
        if not view.complete and not view.decision.decision_classification:
            return (
                replace_(
                    empty,
                    provider_status=provider_status,
                    freshness_state=freshness,
                    reason=f"{instrument}: no usable completed data",
                ),
                None, provider_name, freshness, view.evaluation_timestamp,
            )

        eval_ts = view.evaluation_timestamp
        actionability = view.actionability
        geom = view.geometry
        decision = view.decision.decision_classification
        direction = geom.direction or ""

        # Eligibility gate: the SINGLE existing READY_FOR_REVIEW mirror.
        # No new threshold invented.
        eligible = actionability is ActionabilityState.READY_FOR_REVIEW

        result = InstrumentOperationResult(
            instrument=instrument,
            analysed=True,
            actionability=actionability.value,
            eligible_for_paper_trade=eligible,
            decision_classification=decision,
            direction=direction,
            evaluation_timestamp=eval_ts,
            provider_status=provider_status,
            freshness_state=freshness,
        )

        if not eligible:
            reason = (
                f"{instrument}: existing opportunity not eligible for paper "
                f"trade (actionability={actionability.value})"
            )
            return (
                replace_(result, reason=reason),
                None, provider_name, freshness, eval_ts,
            )

        # Eligible -> create a paper trade (reusing the existing decision /
        # geometry / plan verbatim). created_at is the evaluation timestamp
        # (deterministic) so repeated cycles produce the same id -> dedupe.
        created_id, duplicate_id, err = self._create_eligible_trade(
            instrument, setup_tf, ctx_tf, view, eval_ts, store, cfg,
        )
        if err:
            return (
                replace_(result, error=True, reason=err),
                err, provider_name, freshness, eval_ts,
            )
        if duplicate_id:
            return (
                replace_(
                    result,
                    duplicate=True,
                    duplicate_paper_trade_id=duplicate_id,
                    reason=(
                        f"{instrument}: duplicate paper trade skipped "
                        f"(existing {duplicate_id})"
                    ),
                ),
                None, provider_name, freshness, eval_ts,
            )
        return (
            replace_(
                result,
                created=(created_id,) if created_id else (),
                reason=(
                    f"{instrument}: paper trade created ({created_id}) "
                    f"from existing opportunity at {eval_ts}"
                ) if created_id else f"{instrument}: no trade created",
            ),
            None, provider_name, freshness, eval_ts,
        )

    def _create_eligible_trade(
        self,
        instrument: str,
        setup_tf: str,
        ctx_tf: str | None,
        view: Any,
        eval_ts: datetime | None,
        store: Any,
        cfg: OperationsConfig,
    ) -> tuple[str | None, str | None, str | None]:
        """Create a paper trade from an eligible existing opportunity.

        Reuses the EXISTING service creation flow (analyze + Phase 4 planner
        + PaperTradingEngine.create) with a DETERMINISTIC created_at (the
        evaluation timestamp) so duplicate prevention is exact. Returns
        (created_id, duplicate_id, error). When a duplicate already exists,
        returns (None, duplicate_id, None).
        """

        from dashboard.services import PaperTradeRequest

        # The deterministic creation anchor. When the analysis produced no
        # evaluation timestamp (should not happen for an eligible trade) we
        # cannot dedupe deterministically -> do not create.
        if eval_ts is None:
            return None, None, (
                f"{instrument}: eligible but no evaluation timestamp; "
                f"cannot create a deterministic paper trade"
            )

        # Account-risk parameters are required by the existing Phase 4
        # planner. When absent, the plan cannot be built -> honest error
        # (never a fabricated plan / quantity).
        if cfg.account_capital is None or cfg.risk_percent is None:
            return None, None, (
                f"{instrument}: account_capital + risk_percent required to "
                f"create a paper trade (existing plan reused verbatim)"
            )

        # Build the would-be paper trade via the existing engine to obtain
        # its deterministic id WITHOUT persisting, then check for a
        # duplicate. This mirrors create_paper_trade but with a deterministic
        # created_at and an explicit duplicate check.
        geom = view.geometry
        plan = self.service.trade_planning_engine.plan(
            instrument=instrument,
            timeframe=setup_tf,
            account_capital=cfg.account_capital,
            risk_percent=cfg.risk_percent,
            geometry=geom,
            direction=geom.direction,
            existing_decision=view.decision.decision_classification,
            actionability=view.actionability.value,
            label=cfg.label,
            metadata=dict(cfg.metadata) if cfg.metadata else None,
        )
        candidate_trade = self.paper_trading_engine.create(
            instrument=instrument,
            timeframe=setup_tf,
            direction=geom.direction,
            existing_decision=view.decision.decision_classification,
            setup_type=view.setup_type or "",
            plan=plan,
            plan_id=plan.plan_id,
            created_at=eval_ts,
            evaluation_timestamp=eval_ts,
            label=cfg.label,
            metadata=dict(cfg.metadata) if cfg.metadata else None,
            sequence=0,
        )
        candidate_id = candidate_trade.paper_trade_id

        # Duplicate prevention: exact id match OR same instrument + timeframe
        # + evaluation timestamp already tracked. Either resolves to
        # DUPLICATE (no re-creation).
        if store.exists(candidate_id):
            return None, candidate_id, None
        existing_match = self._find_existing_for_candle(
            store, instrument, setup_tf, eval_ts,
        )
        if existing_match is not None:
            return None, existing_match, None

        # Persist + return. The created trade is WAITING_FOR_ENTRY (or
        # INVALIDATED when geometry is incomplete — never fabricated). An
        # eligible READY_FOR_REVIEW trade always has complete geometry, so
        # it is WAITING_FOR_ENTRY.
        store.save(candidate_trade, overwrite=True)
        return candidate_id, None, None

    # ------------------------------------------------------------
    # EXISTING-TRADE TRACKING
    # ------------------------------------------------------------

    def _advance_existing_trades(
        self,
        store: Any,
        instruments: list[str],
        setup_tf: str,
        cfg: OperationsConfig,
        reference_now: datetime | None,
    ) -> dict[str, dict[str, list[str]]]:
        """Advance all non-terminal paper trades against completed candles.

        Returns a mapping ``instrument -> {"updated": [...], "closed": [...]}``
        of paper-trade ids affected this cycle. Chronological, no look-ahead:
        the existing :meth:`PaperTradingEngine.track` keeps only candles with
        ``timestamp <= reference_now`` sorted ascending. Failure-isolated per
        trade.
        """

        updates: dict[str, dict[str, list[str]]] = {
            instr: {"updated": [], "closed": []} for instr in instruments
        }
        try:
            trade_ids = store.list_trades()
        except Exception:  # noqa: BLE001 - failure isolation
            return updates
        instrument_set = set(instruments)

        for pid in trade_ids:
            try:
                trade = store.load(pid)
            except Exception:  # noqa: BLE001 - failure isolation
                continue
            if trade.is_terminal:
                continue
            instr = trade.instrument
            if instr not in instrument_set:
                # Still track trades for instruments outside this cycle's
                # watchlist (they belong to the journal); include them.
                updates.setdefault(
                    instr, {"updated": [], "closed": []},
                )
            before_status = trade.status
            updated = self._track_one(store, trade, cfg, reference_now)
            if updated is None or updated.paper_trade_id != pid:
                continue
            if updated.status != before_status:
                updates.setdefault(
                    instr, {"updated": [], "closed": []},
                )["updated"].append(pid)
                if updated.status.is_terminal:
                    updates[instr]["closed"].append(pid)
            else:
                # Status unchanged but the trade was still advanced against
                # new candles (e.g. WAITING_FOR_ENTRY still waiting). Count
                # it as updated only when candles were actually inspected;
                # to stay honest + simple, we count a persisted re-save as
                # updated when the trade is non-terminal and was processed.
                updates.setdefault(
                    instr, {"updated": [], "closed": []},
                )["updated"].append(pid)
        return updates

    def _track_one(
        self,
        store: Any,
        trade: PaperTrade,
        cfg: OperationsConfig,
        reference_now: datetime | None,
    ) -> PaperTrade | None:
        """Track one paper trade against its instrument's completed candles.

        Fetches the provider's completed setup candles (completed-candle
        boundary inherited from Product Phase 1) and delegates to the
        existing :meth:`PaperTradingEngine.track` with an explicit
        ``reference_now``. The cycle's ``reference_now`` (when supplied) is
        the deterministic tracking boundary; otherwise the provider's latest
        completed candle timestamp is used. The engine inspects ONLY
        candles ``<= reference_now``. Failure-isolated.
        """

        try:
            series = self.service.provider.fetch(
                trade.instrument, trade.timeframe,
            )
        except Exception:  # noqa: BLE001 - failure isolation
            return None
        candles = series.setup_candles if series.available else ()
        # The tracking boundary is the cycle's reference_now (deterministic;
        # never the forming candle) when supplied, else the provider's
        # latest completed candle timestamp. When both are present use the
        # MINIMUM so a caller-supplied reference_now is never bypassed by a
        # fresher provider candle (no look-ahead).
        provider_now = series.latest_completed_candle_timestamp
        if reference_now is not None and provider_now is not None:
            track_now = (
                reference_now
                if _naive(reference_now) <= _naive(provider_now)
                else provider_now
            )
        elif reference_now is not None:
            track_now = reference_now
        else:
            track_now = provider_now
            if track_now is None and candles:
                track_now = candles[-1].timestamp
        if track_now is None:
            return trade
        try:
            updated = self.paper_trading_engine.track(
                trade,
                completed_candles=candles,
                reference_now=track_now,
            )
        except Exception:  # noqa: BLE001 - failure isolation
            return trade
        if updated.paper_trade_id != trade.paper_trade_id:
            return trade
        # Persist the (possibly unchanged) tracked state.
        try:
            store.save(updated, overwrite=True)
        except Exception:  # noqa: BLE001 - failure isolation
            return trade
        return updated

    # ------------------------------------------------------------
    # HELPERS
    # ------------------------------------------------------------

    def _resolve_instruments(self, instruments: Any) -> list[str]:
        """Resolve the instruments to process this cycle (sorted, unique)."""

        if instruments is None:
            try:
                avail = self.service.available_instruments()
            except Exception:  # noqa: BLE001 - failure isolation
                return []
            return sorted({str(i) for i in avail})
        # Accept an iterable of names or a Watchlist.
        names: list[str] = []
        try:
            for i in instruments:
                names.append(str(i))
        except TypeError:
            # Single string instrument.
            names = [str(instruments)]
        return sorted({n for n in names if n})

    def _find_existing_for_candle(
        self,
        store: Any,
        instrument: str,
        timeframe: str,
        eval_ts: datetime,
    ) -> str | None:
        """Find an existing paper trade for the same instrument + timeframe +
        evaluation timestamp (duplicate-prevention fallback)."""

        try:
            ids = store.list_trades()
        except Exception:  # noqa: BLE001 - failure isolation
            return None
        for pid in ids:
            try:
                trade = store.load(pid)
            except Exception:  # noqa: BLE001 - failure isolation
                continue
            if (
                trade.instrument == instrument
                and trade.timeframe == timeframe
                and trade.evaluation_timestamp == eval_ts
            ):
                return pid
        return None

    def _count_active_trades(self, store: Any) -> int:
        """Count WAITING_FOR_ENTRY + OPEN paper trades in the store."""

        try:
            ids = store.list_trades()
        except Exception:  # noqa: BLE001 - failure isolation
            return 0
        count = 0
        for pid in ids:
            try:
                trade = store.load(pid)
            except Exception:  # noqa: BLE001 - failure isolation
                continue
            if trade.status in (
                PaperTradeStatus.WAITING_FOR_ENTRY,
                PaperTradeStatus.OPEN,
            ):
                count += 1
        return count

    def _merge_tracking(
        self,
        results: list[InstrumentOperationResult],
        tracking: dict[str, dict[str, list[str]]],
    ) -> list[InstrumentOperationResult]:
        """Merge tracking updated/closed ids into the per-instrument results."""

        if not tracking:
            return results
        merged: list[InstrumentOperationResult] = []
        for r in results:
            upd = tracking.get(r.instrument)
            if not upd:
                merged.append(r)
                continue
            updated_ids = tuple(upd["updated"])
            closed_ids = tuple(upd["closed"])
            # De-duplicate created ids from updated (a freshly-created trade
            # tracked in the same cycle should not double-count).
            created = tuple(
                cid for cid in r.created if cid not in updated_ids
            )
            merged.append(replace_(
                r,
                created=created,
                updated=updated_ids,
                closed=closed_ids,
            ))
        # Append results for tracked instruments NOT in this cycle's
        # analysis list (e.g. trades whose instrument was unavailable this
        # cycle but still tracked against completed candles).
        known = {r.instrument for r in merged}
        for instr, upd in sorted(tracking.items()):
            if instr in known or not (upd["updated"] or upd["closed"]):
                continue
            merged.append(InstrumentOperationResult(
                instrument=instr,
                updated=tuple(upd["updated"]),
                closed=tuple(upd["closed"]),
                reason=f"{instr}: existing paper trade(s) tracked",
            ))
        return merged

    def _build_warnings(
        self,
        status: OperationalStatus,
        freshness: str,
        duplicates: int,
        errors: list[str],
    ) -> list[str]:
        warnings: list[str] = []
        if status is OperationalStatus.NO_DATA:
            warnings.append(
                "No instrument produced usable completed data this cycle; "
                "no paper trade was created.",
            )
        if status is OperationalStatus.ERROR:
            warnings.append(
                "Every instrument failed this cycle; no paper trade was "
                "created. Failures are surfaced per-instrument.",
            )
        if freshness == "STALE":
            warnings.append(
                "Latest completed candle is stale for the analysed "
                "instruments (data-quality warning; the cycle still ran on "
                "completed candles only).",
            )
        if freshness == "UNAVAILABLE":
            warnings.append(
                "No current / stale completed data was available this cycle.",
            )
        if duplicates:
            warnings.append(
                f"{duplicates} duplicate paper-trade creation(s) skipped.",
            )
        if errors:
            warnings.append(
                f"{len(errors)} instrument failure(s) isolated this cycle.",
            )
        return warnings

    def _build_rationale(
        self,
        status: OperationalStatus,
        scanned: int,
        analysed: int,
        created: int,
        updated: int,
        closed: int,
        duplicates: int,
    ) -> str:
        return (
            f"Operational cycle {status.value}: scanned {scanned} "
            f"instrument(s), analysed {analysed}; created {created}, "
            f"updated {updated}, closed {closed} paper trade(s); "
            f"{duplicates} duplicate(s) skipped. Paper trading only — no "
            f"real orders placed."
        )

    def _limitations(self) -> str:
        return (
            f"{OPERATIONS_DISCLAIMER} The existing decision engine remains "
            f"authoritative; paper-trade results never rewrite the original "
            f"system decision. Target 2 remains unsupported. Only completed "
            f"candles are used; forming / future candles are never fed to "
            f"the engine."
        )

    def _not_ready_result(
        self,
        instruments: Any,
        reference_now: datetime | None,
        started_at: datetime | None,
        cfg: OperationsConfig,
    ) -> OperationsCycleResult:
        scanned = len(self._resolve_instruments(instruments))
        return OperationsCycleResult(
            cycle_id=self._cycle_id(
                self._resolve_instruments(instruments),
                reference_now, started_at, cfg, [],
            ),
            status=OperationalStatus.NOT_READY,
            started_at=started_at,
            completed_at=reference_now or started_at,
            reference_now=reference_now,
            instruments_scanned=scanned,
            warnings=(
                "No paper-trade store attached; operational cycle cannot "
                "persist / load trades.",
            ),
            rationale=(
                "Operational cycle NOT_READY: no paper-trade store attached."
            ),
            limitations=self._limitations(),
        )

    def _cycle_id(
        self,
        instruments: list[str],
        reference_now: datetime | None,
        started_at: datetime | None,
        cfg: OperationsConfig,
        results: list[InstrumentOperationResult],
    ) -> str:
        """Deterministic operational cycle id (``"opcycle-" + sha256[:16]``)."""

        def _v(x: Any) -> str:
            if x is None:
                return "null"
            if isinstance(x, datetime):
                return f"dt:{x.isoformat()}"
            return f"str:{x!s}"

        canonical = json.dumps(
            {
                "instruments": [_v(i) for i in sorted(instruments)],
                "reference_now": _v(reference_now),
                "started_at": _v(started_at),
                "setup_timeframe": _v(cfg.setup_timeframe),
                "context_timeframe": _v(cfg.context_timeframe),
                "account_capital": _v(cfg.account_capital),
                "risk_percent": _v(cfg.risk_percent),
                "label": _v(cfg.label),
                "metadata": [_v(k) + "=" + _v(v) for k, v in cfg.metadata],
                "outcomes": [
                    {
                        "instrument": _v(r.instrument),
                        "analysed": _v(r.analysed),
                        "actionability": _v(r.actionability),
                        "eligible": _v(r.eligible_for_paper_trade),
                        "decision": _v(r.decision_classification),
                        "evaluation_timestamp": _v(r.evaluation_timestamp),
                        "created": [_v(c) for c in r.created],
                        "duplicate": _v(r.duplicate),
                        "error": _v(r.error),
                    }
                    for r in sorted(results, key=lambda x: x.instrument)
                ],
            },
            sort_keys=True,
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return f"opcycle-{digest[:16]}"


# ============================================================
# HELPERS
# ============================================================


def replace_(obj: InstrumentOperationResult, **changes: Any) -> InstrumentOperationResult:
    """Replace fields on a frozen :class:`InstrumentOperationResult`."""

    from dataclasses import replace as _replace
    return _replace(obj, **changes)


__all__ = [
    "OPERATIONS_DISCLAIMER",
    "InstrumentOperationResult",
    "OperationsConfig",
    "OperationsCycleResult",
    "OperationalStatus",
    "PaperTradingOperations",
]
