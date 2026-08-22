"""
Live Paper Validation (Product Phase 6F).

Product Phase 6F validates the completed Phase 6A-6E historical-research
pipeline against real / near-live market data during the live market
session, using PAPER TRADING ONLY. It answers:

    "When the system sees a current market state, what did historical
    research say about similar setups, and how does that historical
    expectation compare with the actual subsequent paper-trading
    outcome?"

This is a THIN ORCHESTRATION layer. It implements NO new market
analysis, NO new decision logic, NO new geometry, NO new position
sizing, NO new outcome engine, NO probability / prediction, NO broker
and NO BUY/SELL/ENTER/EXIT/HOLD recommendation.

The validation workflow (:meth:`LivePaperValidation.run_once`) is:

    LIVE / NEAR-LIVE DATA (existing provider; completed candles only)
        |
    existing DashboardAnalysisService.analyze  (authoritative decision)
        |
    existing Phase 6E historical evidence context (attached to the view)
        |
    existing PaperTradingOperations.run_once  (track + create + persist)
        |
    LiveValidationObservation per instrument  (this layer)
        |
    LiveValidationStore  (dedicated persistence; references, never
        duplicates, the research + paper-trade records)
        |
    historical-vs-actual comparison report

DESIGN PRINCIPLE — reuse, do not re-invent:

* The data provider + completed-candle boundary are inherited from the
  existing Product Phase 1 provider through the service; the forming
  candle NEVER enters the analysis, the decision or the outcome.
* The existing Sprint 11S decision classification is AUTHORITATIVE and
  reused VERBATIM — never renamed to BUY/SELL, never upgraded /
  downgraded, never overridden by historical evidence.
* The Phase 6E historical evidence context is read from the existing
  ``DashboardTradeView.historical_context`` attachment (built by the
  existing service via the existing
  :class:`~dashboard.services.HistoricalEvidenceSource`). Phase 6F
  NEVER re-runs the Phase 6D research and NEVER performs a second
  lookup with different semantics.
* Paper trades are created / tracked / persisted EXCLUSIVELY through the
  existing :class:`~dashboard.paper_trade_operations.PaperTradingOperations`
  cycle (the same eligibility gate, duplicate prevention and lifecycle).
  Phase 6F NEVER creates a paper trade itself and NEVER modifies the
  existing paper-trade store schema — observations REFERENCE
  paper-trade ids.
* Outcomes are read from the EXISTING paper-trade lifecycle records.
  No second outcome engine is created; no outcome is fabricated.

DESIGN PRINCIPLE — historical evidence remains descriptive:

Historical evidence is recorded ALONGSIDE the current decision. It never
changes REJECTED -> QUALIFIED, never turns NO_OPPORTUNITY into an
opportunity, never creates geometry, never fabricates entry/stop/target,
never changes direction and never modifies the trade plan.

DESIGN PRINCIPLE — point-in-time:

At evaluation time ``T`` the current setup uses setup candles ``<= T``
and context candles ``< T`` (inherited from the existing analysis); the
historical research carries only occurrences strictly before ``T`` whose
outcome was already resolved at ``T`` (inherited from Phase 6E); the
paper-trade outcome resolves strictly AFTER the evaluation timestamp on
completed candles (inherited from the existing lifecycle). The public
:meth:`run_once` API accepts NO ``future`` / ``future_candles`` /
``lookahead`` argument, never calls the Sprint 11W
:class:`~engine.intelligence.historical_outcome.OutcomeEvaluator` and
never runs the historical pipeline.

DESIGN PRINCIPLE — failure isolation + determinism:

One instrument failure NEVER aborts the cycle. Provider failure, stale
data, unavailable research, no historical match, rejected decision, no
opportunity, incomplete geometry, paper-trade persistence failure and
unavailable outcome are recorded EXPLICITLY (status + warnings), never
silently converted into success. Identical inputs produce an identical
``cycle_id`` + identical observations; instruments are processed in
sorted order (shuffle-invariant).

DESIGN PRINCIPLE — no real orders:

This system performs paper trading only. No real orders are placed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from typing import Any

from engine.models.live_validation import (
    LIVE_VALIDATION_LIMITATIONS,
    LiveValidationCycleResult,
    LiveValidationCycleStatus,
    LiveValidationObservation,
    LiveValidationStatus,
)
from engine.models.paper_trade import PaperTrade

from dashboard.paper_trade_operations import (
    OperationalStatus,
    OperationsConfig,
    PaperTradingOperations,
)


#: The fixed, non-configurable Phase 6F disclaimer.
LIVE_VALIDATION_DISCLAIMER = (
    "Historical evidence is observational research context. It does not "
    "predict future returns, guarantee profitability, or override the "
    "authoritative decision engine. This system performs paper trading "
    "only. No real orders are placed."
)


# ============================================================
# CONFIG
# ============================================================


@dataclass(frozen=True, slots=True)
class LiveValidationConfig:
    """
    Configuration for :class:`LivePaperValidation`.

    MINIMAL — NO strategy / optimization / scoring / eligibility
    parameters. The paper-trade eligibility gate remains the FIXED
    existing ``READY_FOR_REVIEW`` actionability mirror inside the reused
    operations layer. The decision / geometry / plan / lifecycle
    semantics are intentionally NOT configurable.

    Attributes:

    account_capital / risk_percent
        User-supplied account-risk parameters forwarded to the reused
        operations cycle (the existing Product Phase 4 planner sizes any
        created paper trade; Phase 6F performs NO sizing itself).

    setup_timeframe / context_timeframe
        Dashboard setup / context timeframe pair (reused).

    label / metadata
        Optional identity / metadata carried onto observations.
    """

    account_capital: Any = None
    risk_percent: Any = None
    setup_timeframe: str = "15m"
    context_timeframe: str | None = None
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = field(default_factory=tuple)


# ============================================================
# VALIDATION ENGINE
# ============================================================


class LivePaperValidation:
    """
    Orchestrates ONE live paper validation cycle (:meth:`run_once`).

    Holds a :class:`~dashboard.services.DashboardAnalysisService`
    reference (existing provider + analysis + Phase 6E evidence source),
    an existing
    :class:`~dashboard.paper_trade_operations.PaperTradingOperations`
    instance (existing paper-trading lifecycle) and a dedicated
    :class:`~dashboard.live_validation_store.LiveValidationStore`.
    STATELESS across cycles; all persisted state lives in the stores.
    """

    def __init__(
        self,
        service: Any,
        *,
        config: LiveValidationConfig | None = None,
        operations: PaperTradingOperations | None = None,
        validation_store: Any | None = None,
    ) -> None:
        self.service = service
        self.config = config or LiveValidationConfig()
        self.operations = operations or PaperTradingOperations(service)
        self.validation_store = validation_store

    # ------------------------------------------------------------
    # PUBLIC API
    # ------------------------------------------------------------

    def run_once(
        self,
        *,
        instruments: Any = None,
        reference_now: datetime | None = None,
        started_at: datetime | None = None,
    ) -> LiveValidationCycleResult:
        """
        Perform exactly ONE deterministic live validation cycle.

        Steps per cycle:

        1. Run the EXISTING paper-trading operations cycle (tracks +
           creates paper trades through the authoritative eligibility
           gate; persists them through the existing paper-trade store).
        2. For each instrument (sorted): run the existing analysis view
           (completed candles only), read the attached Phase 6E
           historical evidence context, link any paper trade the
           operations cycle created / tracked for that instrument, and
           build a :class:`LiveValidationObservation`.
        3. Persist each observation IDEMPOTENTLY (deterministic
           validation id; unchanged observations are not re-written;
           outcome-advancing updates bump ``revision`` + append history).
        4. Refresh previously-recorded OPEN observations whose referenced
           paper trade advanced (outcome now known) — the historical-vs
           -actual comparison is completed honestly when the outcome
           arrives, never fabricated earlier.

        GUARANTEES: reuse-only; completed-candle only; no look-ahead (no
        ``future`` / ``future_candles`` argument); decision / geometry /
        plan preserved verbatim; failure isolation; deterministic;
        paper trading only (no real orders).
        """

        cfg = self.config
        store = self.validation_store
        sorted_instruments = self._resolve_instruments(instruments)
        if store is None:
            return LiveValidationCycleResult(
                cycle_id=self._cycle_id(
                    sorted_instruments, reference_now, started_at, (),
                ),
                status=LiveValidationCycleStatus.NOT_READY,
                started_at=started_at,
                completed_at=reference_now or started_at,
                reference_now=reference_now,
                instruments_scanned=len(sorted_instruments),
                warnings=(
                    "No live-validation store attached; the validation "
                    "cycle cannot persist observations.",
                ),
                rationale=(
                    "Live validation cycle NOT_READY: no validation store "
                    "attached."
                ),
                limitations=self._limitations(),
            )

        # Step 1 — the EXISTING operations cycle (track + create + persist).
        ops_cfg = OperationsConfig(
            account_capital=cfg.account_capital,
            risk_percent=cfg.risk_percent,
            setup_timeframe=cfg.setup_timeframe,
            context_timeframe=cfg.context_timeframe,
            label=cfg.label,
            metadata=cfg.metadata,
        )
        ops_result = self.operations.run_once(
            instruments=sorted_instruments,
            reference_now=reference_now,
            started_at=started_at,
            config=ops_cfg,
        )
        ops_by_instrument = {r.instrument: r for r in ops_result.results}
        paper_store = getattr(self.service, "paper_trade_store", None)
        cycle_time = reference_now or ops_result.reference_now or started_at

        # Steps 2+3 — build + persist per-instrument observations.
        observations: list[LiveValidationObservation] = []
        errors: list[str] = list(ops_result.errors)
        recorded = 0
        updated = 0
        processed_ids: set[str] = set()
        for instrument in sorted_instruments:
            try:
                obs = self._observe_instrument(
                    instrument,
                    ops_by_instrument.get(instrument),
                    paper_store,
                    cycle_time,
                    cfg,
                )
            except Exception as exc:  # noqa: BLE001 - failure isolation
                # An unexpected observation-building failure is recorded
                # EXPLICITLY and never aborts the cycle.
                err = f"{instrument}: validation observation failed: {exc}"
                errors.append(err)
                obs = LiveValidationObservation(
                    validation_id=self._validation_id(
                        instrument, None, "", "", "", "", "UNAVAILABLE", cfg,
                    ),
                    instrument=instrument,
                    setup_timeframe=cfg.setup_timeframe,
                    context_timeframe=cfg.context_timeframe or "",
                    validation_status=LiveValidationStatus.ERROR,
                    recorded_at=cycle_time,
                    updated_at=cycle_time,
                    warnings=(err,),
                    limitations=LIVE_VALIDATION_LIMITATIONS,
                    label=cfg.label,
                    metadata=cfg.metadata,
                )
            try:
                outcome = self._persist(store, obs, cycle_time)
            except Exception as exc:  # noqa: BLE001 - failure isolation
                # A persistence failure is recorded EXPLICITLY (never
                # silently converted into a successful validation) and
                # never aborts the cycle.
                err = f"{instrument}: validation persistence failed: {exc}"
                errors.append(err)
                obs = replace(
                    obs,
                    validation_status=LiveValidationStatus.ERROR,
                    paper_trade_id="",
                    outcome_status="",
                    outcome_timestamp=None,
                    realized_r=None,
                    warnings=obs.warnings + (err,),
                )
            else:
                if outcome == "recorded":
                    recorded += 1
                elif outcome == "updated":
                    updated += 1
            processed_ids.add(obs.validation_id)
            observations.append(obs)

        # Step 4 — refresh previously-recorded OPEN observations whose
        # referenced paper trade advanced this cycle (outcome arrival).
        updated += self._refresh_open_observations(
            store, paper_store, cycle_time, processed_ids,
        )

        # Aggregate + deterministic cycle identity.
        status = self._map_cycle_status(ops_result.status)
        observations_t = tuple(observations)
        cycle_id = self._cycle_id(
            sorted_instruments,
            ops_result.reference_now or reference_now,
            started_at,
            observations_t,
        )
        warnings = self._build_warnings(status, ops_result, errors)
        rationale = self._build_rationale(
            status, len(sorted_instruments), recorded, updated,
            ops_result.trades_created,
        )
        return LiveValidationCycleResult(
            cycle_id=cycle_id,
            status=status,
            operations_cycle_id=ops_result.cycle_id,
            reference_now=ops_result.reference_now or reference_now,
            started_at=started_at,
            completed_at=cycle_time,
            provider=ops_result.provider,
            instruments_scanned=len(sorted_instruments),
            observations=observations_t,
            observations_recorded=recorded,
            observations_updated=updated,
            paper_trades_created=ops_result.trades_created,
            duplicates_skipped=ops_result.duplicates_skipped,
            errors=tuple(errors),
            warnings=tuple(warnings),
            rationale=rationale,
            limitations=self._limitations(),
        )

    # ------------------------------------------------------------
    # OBSERVATION BUILDING
    # ------------------------------------------------------------

    def _observe_instrument(
        self,
        instrument: str,
        ops_result: Any | None,
        paper_store: Any,
        cycle_time: datetime | None,
        cfg: LiveValidationConfig,
    ) -> LiveValidationObservation:
        """Build ONE observation for ONE instrument (failure-isolated).

        Reuses the existing analysis view (completed candles only) for the
        current state + the attached Phase 6E historical evidence context,
        and the existing operations result for paper-trade linkage.
        """

        from dashboard.services import AnalysisRequest

        warnings: list[str] = []
        # --- current market state (existing analysis; completed candles) ---
        view = None
        analyze_error: str | None = None
        try:
            view = self.service.analyze(
                AnalysisRequest(
                    instrument=instrument,
                    setup_timeframe=cfg.setup_timeframe,
                    context_timeframe=cfg.context_timeframe,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - failure isolation
            analyze_error = f"{instrument}: analysis failed: {exc}"
            warnings.append(analyze_error)

        eval_ts = getattr(view, "evaluation_timestamp", None) if view else None
        decision = ""
        actionability = ""
        direction = ""
        geometry_available = False
        provider = ""
        provider_status = ""
        freshness = ""
        analysed = False
        if view is not None:
            decision_view = getattr(view, "decision", None)
            decision = getattr(decision_view, "decision_classification", "") or ""
            act = getattr(view, "actionability", None)
            actionability = getattr(act, "value", "") or ""
            geom = getattr(view, "geometry", None)
            direction = getattr(geom, "direction", "") or ""
            geometry_available = bool(getattr(geom, "geometry_available", False))
            ds = getattr(view, "data_source", None)
            provider = getattr(ds, "data_source", "") or ""
            provider_status = getattr(ds, "provider_status", "") or ""
            freshness = getattr(ds, "freshness_state", "") or ""
            analysed = bool(getattr(view, "complete", False)) or bool(decision)

        # --- Phase 6E historical evidence context (existing attachment) ---
        # Failure-isolated: a broken evidence attachment degrades to the
        # honest UNAVAILABLE sentinels (never fabricated), never corrupts
        # the current assessment.
        hc_status = "UNAVAILABLE"
        hc_strength = "UNAVAILABLE"
        hc_sample = 0
        hc_win_rate = None
        hc_avg_r = None
        hc_pf = None
        research_ids: tuple[str, ...] = ()
        try:
            hc = getattr(view, "historical_context", None) if view is not None else None
            if hc is not None:
                hc_status = getattr(hc, "status", "") or "UNAVAILABLE"
                hc_strength = getattr(hc, "evidence_strength", "") or "UNAVAILABLE"
                hc_sample = int(getattr(hc, "comparable_occurrences", 0) or 0)
                hc_win_rate = getattr(hc, "win_rate", None)
                hc_avg_r = getattr(hc, "average_realized_r", None)
                hc_pf = getattr(hc, "profit_factor", None)
                research_ids = tuple(getattr(hc, "research_ids", ()) or ())
        except Exception as exc:  # noqa: BLE001 - failure isolation
            warnings.append(
                f"{instrument}: historical evidence context unavailable: {exc}"
            )
            hc_status = "UNAVAILABLE"
            hc_strength = "UNAVAILABLE"
            hc_sample = 0
            hc_win_rate = None
            hc_avg_r = None
            hc_pf = None
            research_ids = ()

        # --- paper-trade linkage (existing operations result) ---
        ops_error = bool(getattr(ops_result, "error", False)) if ops_result else False
        ops_reason = getattr(ops_result, "reason", "") if ops_result else ""
        paper_trade_id = ""
        created_this_cycle = False
        if ops_result is not None and not ops_error:
            if getattr(ops_result, "created", ()):
                paper_trade_id = ops_result.created[0]
                created_this_cycle = True
            elif getattr(ops_result, "duplicate", False):
                paper_trade_id = ops_result.duplicate_paper_trade_id
            elif getattr(ops_result, "closed", ()):
                paper_trade_id = ops_result.closed[0]
            elif getattr(ops_result, "updated", ()):
                paper_trade_id = ops_result.updated[0]
        elif ops_error and ops_reason:
            warnings.append(ops_reason)

        trade: PaperTrade | None = None
        if paper_trade_id and paper_store is None:
            warnings.append(
                f"{instrument}: paper trade {paper_trade_id} referenced but "
                "no paper-trade store is attached; outcome is unavailable."
            )
        if paper_trade_id and paper_store is not None:
            try:
                trade = paper_store.load(paper_trade_id)
            except Exception as exc:  # noqa: BLE001 - failure isolation
                warnings.append(
                    f"{instrument}: referenced paper trade {paper_trade_id} "
                    f"could not be loaded: {exc}"
                )

        # --- validation status (descriptive; non-predictive) ---
        status = self._classify(
            analyse_failed=analyze_error is not None,
            analysed=analysed,
            ops_error=ops_error,
            decision=decision,
            actionability=actionability,
            paper_trade_id=paper_trade_id,
            trade=trade,
            created_this_cycle=created_this_cycle,
        )

        # --- outcome fields (reused paper-trade lifecycle; never fabricated) ---
        outcome_status = ""
        outcome_timestamp = None
        realized_r: Decimal | None = None
        if trade is not None:
            if trade.is_terminal:
                reason = getattr(trade, "exit_reason", None)
                outcome_status = getattr(reason, "name", "") or trade.status.name
                outcome_timestamp = trade.exit_timestamp
                realized_r = trade.realized_r
            else:
                outcome_status = trade.status.name

        validation_id = self._validation_id(
            instrument, eval_ts, provider, decision, actionability,
            direction, hc_status, cfg,
        )
        return LiveValidationObservation(
            validation_id=validation_id,
            instrument=instrument,
            setup_timeframe=cfg.setup_timeframe,
            context_timeframe=cfg.context_timeframe or "",
            evaluation_timestamp=eval_ts,
            provider=provider,
            provider_status=provider_status,
            freshness_state=freshness,
            decision_classification=decision,
            actionability=actionability,
            direction=direction,
            geometry_available=geometry_available,
            historical_context_status=hc_status,
            historical_evidence_strength=hc_strength,
            historical_sample_size=hc_sample,
            historical_win_rate=hc_win_rate,
            historical_average_realized_r=hc_avg_r,
            historical_profit_factor=hc_pf,
            research_ids=research_ids,
            paper_trade_id=paper_trade_id if status.references_trade else "",
            outcome_status=outcome_status,
            outcome_timestamp=outcome_timestamp,
            realized_r=realized_r,
            validation_status=status,
            recorded_at=cycle_time,
            updated_at=cycle_time,
            revision=0,
            warnings=tuple(warnings),
            limitations=LIVE_VALIDATION_LIMITATIONS,
            label=cfg.label,
            metadata=cfg.metadata,
        )

    @staticmethod
    def _classify(
        *,
        analyse_failed: bool,
        analysed: bool,
        ops_error: bool,
        decision: str,
        actionability: str,
        paper_trade_id: str,
        trade: PaperTrade | None,
        created_this_cycle: bool,
    ) -> LiveValidationStatus:
        """Deterministic, non-predictive validation status mapping.

        Priority: explicit failure -> trade lifecycle -> decision-based
        states. The trade lifecycle takes precedence over decision-based
        states so an earlier setup's resolving trade is tracked honestly;
        the decision field itself is preserved verbatim on the
        observation regardless.
        """

        if analyse_failed or ops_error:
            return LiveValidationStatus.ERROR
        if not analysed:
            # No usable completed data (provider failure / unsupported /
            # empty) — an explicit failure, never a successful validation.
            return LiveValidationStatus.ERROR
        if paper_trade_id and trade is not None:
            if trade.is_terminal:
                return LiveValidationStatus.COMPLETED
            if created_this_cycle:
                return LiveValidationStatus.PAPER_TRADE_CREATED
            return LiveValidationStatus.PAPER_TRADE_ACTIVE
        if decision == "REJECTED":
            return LiveValidationStatus.REJECTED
        if actionability == "NO_OPPORTUNITY" or not decision:
            return LiveValidationStatus.NO_OPPORTUNITY
        return LiveValidationStatus.OBSERVED

    # ------------------------------------------------------------
    # PERSISTENCE (idempotent + append/history)
    # ------------------------------------------------------------

    def _persist(
        self,
        store: Any,
        obs: LiveValidationObservation,
        cycle_time: datetime | None,
    ) -> str:
        """Persist an observation idempotently.

        Returns ``"recorded"`` (new), ``"updated"`` (outcome/state
        advanced; revision bumped) or ``"unchanged"`` (identical — no
        re-write, no duplicate). Persistence failures are surfaced as an
        ERROR-status observation saved best-effort; a store write failure
        is never silently swallowed — it raises to the caller's cycle
        error handling.
        """

        try:
            exists = store.exists(obs.validation_id)
        except Exception:  # noqa: BLE001 - failure isolation
            exists = False
        if not exists:
            store.save(obs)
            return "recorded"
        try:
            existing = store.load(obs.validation_id)
        except Exception:  # noqa: BLE001 - failure isolation
            store.save(obs, overwrite=True)
            return "updated"
        if _equivalent(existing, obs):
            return "unchanged"
        advanced = replace(
            obs,
            revision=existing.revision + 1,
            recorded_at=existing.recorded_at,
            updated_at=cycle_time,
        )
        store.save(advanced, overwrite=True)
        return "updated"

    def _refresh_open_observations(
        self,
        store: Any,
        paper_store: Any,
        cycle_time: datetime | None,
        processed_ids: set[str],
    ) -> int:
        """Advance previously-recorded OPEN observations (outcome arrival).

        A previously-recorded observation whose referenced paper trade
        became terminal is updated to COMPLETED with the actual outcome
        (the historical-vs-actual comparison is completed honestly when
        the outcome arrives). Failure-isolated per observation. Returns
        the number of observations updated.
        """

        if paper_store is None:
            return 0
        updated = 0
        try:
            ids = store.list_observations()
        except Exception:  # noqa: BLE001 - failure isolation
            return 0
        for vid in ids:
            if vid in processed_ids:
                continue
            try:
                obs = store.load(vid)
            except Exception:  # noqa: BLE001 - failure isolation
                continue
            if not obs.validation_status.is_open or not obs.paper_trade_id:
                continue
            try:
                trade = paper_store.load(obs.paper_trade_id)
            except Exception:  # noqa: BLE001 - failure isolation
                continue
            if trade.is_terminal:
                reason = getattr(trade, "exit_reason", None)
                advanced = replace(
                    obs,
                    outcome_status=(
                        getattr(reason, "name", "") or trade.status.name
                    ),
                    outcome_timestamp=trade.exit_timestamp,
                    realized_r=trade.realized_r,
                    validation_status=LiveValidationStatus.COMPLETED,
                    revision=obs.revision + 1,
                    updated_at=cycle_time,
                )
            elif trade.status.name != obs.outcome_status:
                advanced = replace(
                    obs,
                    outcome_status=trade.status.name,
                    validation_status=LiveValidationStatus.PAPER_TRADE_ACTIVE,
                    revision=obs.revision + 1,
                    updated_at=cycle_time,
                )
            else:
                continue
            try:
                store.save(advanced, overwrite=True)
                updated += 1
            except Exception:  # noqa: BLE001 - failure isolation
                continue
        return updated

    # ------------------------------------------------------------
    # HELPERS
    # ------------------------------------------------------------

    def _resolve_instruments(self, instruments: Any) -> list[str]:
        """Resolve instruments (existing configurable universe/watchlist).

        ``None`` -> the service's ``available_instruments()`` (the
        EXISTING configured universe; nothing is hard-coded here).
        """

        if instruments is None:
            try:
                avail = self.service.available_instruments()
            except Exception:  # noqa: BLE001 - failure isolation
                return []
            return sorted({str(i) for i in avail})
        names: list[str] = []
        try:
            for i in instruments:
                names.append(str(i))
        except TypeError:
            names = [str(instruments)]
        return sorted({n for n in names if n})

    @staticmethod
    def _validation_id(
        instrument: str,
        eval_ts: datetime | None,
        provider: str,
        decision: str,
        actionability: str,
        direction: str,
        hc_status: str,
        cfg: LiveValidationConfig,
    ) -> str:
        """Deterministic observation id (``"lval-" + sha256[:16]``).

        Identity covers the SETUP only (instrument / timeframes /
        evaluation timestamp / provider / decision / actionability /
        direction / historical-context status) — NOT the outcome, so an
        outcome-advancing update keeps the same id (idempotent).
        """

        canonical = json.dumps(
            {
                "instrument": str(instrument).strip().upper(),
                "setup_timeframe": cfg.setup_timeframe,
                "context_timeframe": cfg.context_timeframe or "",
                "evaluation_timestamp": (
                    eval_ts.isoformat() if eval_ts is not None else None
                ),
                "provider": provider,
                "decision": decision,
                "actionability": actionability,
                "direction": direction,
                "historical_context_status": hc_status,
            },
            sort_keys=True,
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return f"lval-{digest[:16]}"

    @staticmethod
    def _cycle_id(
        instruments: list[str],
        reference_now: datetime | None,
        started_at: datetime | None,
        observations: tuple[LiveValidationObservation, ...],
    ) -> str:
        """Deterministic validation cycle id (``"lvcycle-" + sha256[:16]``)."""

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
                "observations": [
                    {
                        "validation_id": o.validation_id,
                        "validation_status": o.validation_status.value,
                        "paper_trade_id": o.paper_trade_id,
                        "outcome_status": o.outcome_status,
                    }
                    for o in sorted(observations, key=lambda o: o.instrument)
                ],
            },
            sort_keys=True,
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return f"lvcycle-{digest[:16]}"

    @staticmethod
    def _map_cycle_status(status: OperationalStatus) -> LiveValidationCycleStatus:
        """Map the reused operations cycle status by name (same vocabulary)."""

        return LiveValidationCycleStatus[status.name]

    @staticmethod
    def _build_warnings(
        status: LiveValidationCycleStatus,
        ops_result: Any,
        errors: list[str],
    ) -> list[str]:
        warnings: list[str] = []
        if status is LiveValidationCycleStatus.NO_DATA:
            warnings.append(
                "No instrument produced usable completed data this cycle; "
                "no validation observation could be made."
            )
        if status is LiveValidationCycleStatus.ERROR:
            warnings.append(
                "Every instrument failed this cycle; failures are surfaced "
                "per-instrument and never converted into success."
            )
        if status is LiveValidationCycleStatus.STALE:
            warnings.append(
                "Latest completed candle is stale for the analysed "
                "instruments (data-quality warning; the cycle still ran on "
                "completed candles only)."
            )
        if ops_result.duplicates_skipped:
            warnings.append(
                f"{ops_result.duplicates_skipped} duplicate paper-trade "
                "creation(s) skipped by the existing operations layer."
            )
        if errors:
            warnings.append(
                f"{len(errors)} instrument failure(s) isolated this cycle."
            )
        return warnings

    @staticmethod
    def _build_rationale(
        status: LiveValidationCycleStatus,
        scanned: int,
        recorded: int,
        updated: int,
        created: int,
    ) -> str:
        return (
            f"Live validation cycle {status.value}: scanned {scanned} "
            f"instrument(s); recorded {recorded} new validation "
            f"observation(s), advanced {updated}; {created} paper trade(s) "
            f"created through the existing operations layer. Historical "
            f"evidence is descriptive context only; the existing decision "
            f"engine remains authoritative. Paper trading only — no real "
            f"orders placed."
        )

    @staticmethod
    def _limitations() -> str:
        return (
            f"{LIVE_VALIDATION_DISCLAIMER} Observations reference persisted "
            f"research + paper-trade records (never duplicate them). Only "
            f"completed candles are used; forming / future candles are "
            f"never fed to the engine."
        )


def _equivalent(
    a: LiveValidationObservation, b: LiveValidationObservation,
) -> bool:
    """Whether two observations are identical ignoring revision/timestamps."""

    normalize = ("revision", "recorded_at", "updated_at")
    a_norm = replace(a, revision=0, recorded_at=None, updated_at=None)
    b_norm = replace(b, revision=0, recorded_at=None, updated_at=None)
    return a_norm == b_norm


__all__ = [
    "LIVE_VALIDATION_DISCLAIMER",
    "LivePaperValidation",
    "LiveValidationConfig",
]
