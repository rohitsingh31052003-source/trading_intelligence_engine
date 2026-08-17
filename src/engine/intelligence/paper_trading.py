"""
Paper trading & real-world validation engine (Product Phase 5).

This is a VALIDATION / RECORDING layer. It is NOT a new decision
algorithm, NOT a prediction engine, NOT a scoring engine, NOT a broker,
NOT an execution engine and NOT a paper-trading auto-strategy that
creates trades for every scanner result.

It answers ONE question:

    "If the system's existing trade opportunities had been followed in
    real / near-live market conditions, how would those trades actually
    have performed?"

A human reviews an opportunity -> creates a paper trade -> the engine
tracks observed entry / exit conditions using COMPLETED market candles
-> the engine records the result. The engine NEVER places a real order,
NEVER connects to a broker, NEVER creates a BUY/SELL/ENTER/EXIT/HOLD
recommendation, and NEVER retroactively rewrites the original system
decision.

AUTHORITATIVE UPSTREAM OUTPUTS (reused VERBATIM, never recomputed):

* Sprint 11S decision classification (REJECTED / WATCH / QUALIFIED /
  PREFERRED) — reused as ``existing_decision``; never renamed /
  upgraded / downgraded. A paper-trade RESULT is a SEPARATE concern from
  the system DECISION.
* Sprint 11R ``TradeCandidate`` geometry (entry / stop / target /
  risk_distance / reward_distance / risk_reward_ratio) — reused
  verbatim. Target 2 is NOT supported.
* Product Phase 4 :class:`~engine.models.trade_plan.TradePlan` (account
  capital / risk % / maximum_risk / quantity / planned_risk) — reused
  verbatim. The paper-trade layer performs NO new position sizing.

ENTRY RULE (conservative, deterministic, documented):

A paper trade enters when a COMPLETED candle AFTER the creation
timestamp touches the entry reference. For LONG: entry when
``candle.low <= entry_reference`` (price came down to the planned
entry). For SHORT: entry when ``candle.high >= entry_reference``. The
entry price IS the planned ``entry_reference`` (a limit-order-style
entry at the structural level). This is a deliberately simple, auditable
rule — there is NO advanced entry algorithm. If no completed candle
after creation touches the entry within ``max_entry_bars``, the trade
remains ``WAITING_FOR_ENTRY`` (no fabricated entry).

EXIT RULE (reuses the established Sprint 11W touch semantics):

Once OPEN, the engine watches completed candles strictly AFTER the
entry candle for stop / target touches. LONG: target touched when
``high >= target``; stop touched when ``low <= stop``. SHORT (mirror):
target touched when ``low <= target``; stop touched when
``high >= stop``. The first-touch comparison determines the outcome:

* target touched strictly before stop (or stop never) -> ``TARGET_HIT``
  at the target level.
* stop touched strictly before target (or target never) -> ``STOP_HIT``
  at the stop level.
* a SINGLE candle touches BOTH -> ``BOTH_TOUCHED`` (ambiguous; a winner
  / loser is NEVER manufactured; ``realized_r`` / ``realized_pnl`` are
  ``None``).
* neither within ``max_holding_bars`` -> ``EXPIRED`` (mark-to-close at
  the last in-window candle).

``MANUAL_CLOSE`` is a HUMAN action: the caller supplies an exit price +
timestamp observed in the market; the engine records it honestly and
computes realized R / P&L from entry / exit / risk / quantity.

REALIZED R (documented):

    risk = abs(entry - stop)        (per-unit ENGINE risk, reused)
    LONG  realized_r = (exit - entry) / risk
    SHORT realized_r = (entry - exit) / risk

``realized_r`` is ``None`` for ``BOTH_TOUCHED`` (ambiguous),
``NO_GEOMETRY``, ``CANCELLED``, and any unresolved state.

REALIZED P&L (documented, Decimal):

    LONG  realized_pnl = (exit - entry) * quantity
    SHORT realized_pnl = (entry - exit) * quantity

``quantity`` is the Product Phase 4 planned quantity reused verbatim.
``realized_pnl`` is ``None`` for ambiguous / no-geometry / unresolved
states.

NO-LOOK-AHEAD (HARD REQUIREMENT, structurally enforced):

The engine consumes ONLY completed candles supplied by the caller. The
public ``track`` / ``resolve`` APIs take an explicit ``reference_now``
timestamp and a sequence of completed candles; the engine inspects ONLY
candles whose timestamp is ``<= reference_now`` AND strictly after the
last processed anchor (creation timestamp for entry, entry candle for
exit). A forming candle (timestamp > reference_now) is NEVER inspected.
A future-dated candle is NEVER inspected. The public APIs take NO
``future`` / ``future_candles`` argument. The engine NEVER calls the
Sprint 11W ``OutcomeEvaluator.evaluate`` and NEVER runs the
``HistoricalEvaluationPipeline.evaluate`` (regression-tested by
patching both to raise). The touch logic is implemented directly in
this engine (it is simple and well-established) so the paper-trade
tracker is independent of the historical outcome evaluator.

DETERMINISM:

The paper-trade id is ``"pt-" + sha256[:16]`` of the canonical identity
(opportunity: plan_id / instrument / timeframe / direction / decision /
setup_type / geometry; instance: created_at ISO / sequence / label /
metadata). No random UUID, no wall-clock in the id (``created_at`` is
caller-supplied so tests are deterministic). Two paper trades created
from the same opportunity at the same ``created_at`` MUST supply a
distinct ``sequence`` so they do not collapse into one record.

This is a recording / validation layer, NOT a new orchestration package.
``intelligence/__init__.py`` stays intentionally empty; import via
``from engine.intelligence.paper_trading import PaperTradingEngine``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime
from decimal import ROUND_FLOOR, Decimal
from typing import Any, Mapping, Sequence

from engine.config.paper_trade_config import PaperTradeConfig
from engine.models.ohlcv import OHLCVCandle
from engine.models.paper_trade import (
    PaperExitReason,
    PaperTrade,
    PaperTradeStatus,
    _to_decimal,
)


# ============================================================
# ENGINE
# ============================================================


class PaperTradingEngine:
    """
    Create, track and resolve paper trades.

    The engine is PURE and DETERMINISTIC. ``create`` is stateless; the
    lifecycle methods (:meth:`track` / :meth:`close_manually` /
    :meth:`cancel`) return a NEW immutable :class:`PaperTrade` (they do
    not mutate the input — ``PaperTrade`` is frozen). The caller is
    responsible for persisting the returned trade (e.g. via the
    dashboard paper-trade store).

    The engine performs NO market analysis, NO decision logic, NO
    prediction, NO execution. The existing decision / geometry / plan
    are AUTHORITATIVE and are never modified.
    """

    def __init__(self, config: PaperTradeConfig | None = None) -> None:
        self.config = config or PaperTradeConfig()

    # ------------------------------------------------------------
    # CREATION
    # ------------------------------------------------------------

    def create(
        self,
        *,
        instrument: str,
        timeframe: str,
        direction: str,
        existing_decision: str = "",
        setup_type: str = "",
        plan: Any | None = None,
        plan_id: str = "",
        created_at: datetime,
        evaluation_timestamp: datetime | None = None,
        entry=None,
        stop=None,
        target_1=None,
        engine_risk_distance=None,
        engine_reward_distance=None,
        engine_risk_reward_ratio=None,
        planned_quantity=None,
        planned_risk=None,
        maximum_risk=None,
        account_capital=None,
        risk_percent=None,
        label: str = "",
        metadata: Mapping[str, str] | None = None,
        sequence: int = 0,
    ) -> PaperTrade:
        """
        Create a paper trade from an EXISTING opportunity / trade plan.

        The geometry / plan values may be supplied EITHER as a Product
        Phase 4 :class:`~engine.models.trade_plan.TradePlan` (or a
        dashboard :class:`~dashboard.views.TradePlanView`) via the
        ``plan`` argument, OR as explicit kwargs (explicit kwargs
        override). The engine reuses these VERBATIM; it performs NO new
        position sizing.

        ``created_at`` is caller-supplied (the human action time, or the
        evaluation timestamp at creation) so tests are deterministic —
        no wall-clock is read. ``sequence`` disambiguates two paper
        trades created from the same opportunity at the same
        ``created_at``.

        A paper trade with incomplete / non-directional geometry is
        created with :attr:`PaperTradeStatus.INVALIDATED` (NO_GEOMETRY) —
        it is never fabricated into an entry.
        """

        # Pull geometry + plan fields from the plan object when supplied.
        def _attr(name: str):
            if plan is not None:
                return getattr(plan, name, None)
            return None

        direction = direction or _attr("direction") or ""
        entry_v = _to_decimal(entry) if entry is not None else _to_decimal(_attr("entry"))
        stop_v = _to_decimal(stop) if stop is not None else _to_decimal(_attr("stop"))
        target_v = (
            _to_decimal(target_1)
            if target_1 is not None
            else _to_decimal(_attr("target_1"))
        )
        risk_v = (
            _to_decimal(engine_risk_distance)
            if engine_risk_distance is not None
            else _to_decimal(_attr("engine_risk_distance"))
        )
        reward_v = (
            _to_decimal(engine_reward_distance)
            if engine_reward_distance is not None
            else _to_decimal(_attr("engine_reward_distance"))
        )
        rr_v = (
            _to_decimal(engine_risk_reward_ratio)
            if engine_risk_reward_ratio is not None
            else _to_decimal(_attr("engine_risk_reward_ratio"))
        )
        qty_v = (
            _to_decimal(planned_quantity)
            if planned_quantity is not None
            else _to_decimal(_attr("quantity"))
        )
        planned_risk_v = (
            _to_decimal(planned_risk)
            if planned_risk is not None
            else _to_decimal(_attr("planned_risk"))
        )
        max_risk_v = (
            _to_decimal(maximum_risk)
            if maximum_risk is not None
            else _to_decimal(_attr("maximum_risk"))
        )
        capital_v = (
            _to_decimal(account_capital)
            if account_capital is not None
            else _to_decimal(_attr("account_capital"))
        )
        risk_pct_v = (
            _to_decimal(risk_percent)
            if risk_percent is not None
            else _to_decimal(_attr("risk_percent"))
        )
        plan_id_v = plan_id or (getattr(plan, "plan_id", "") or "")
        setup_type_v = setup_type or (getattr(plan, "setup_type", "") or "")

        meta = _normalize_metadata(metadata)
        # Merge engine-config metadata (low priority) + caller metadata.
        if self.config.metadata:
            base = {k: v for k, v in self.config.metadata}
            base.update({k: v for k, v in meta})
            meta = tuple(sorted(base.items()))

        has_geometry = (
            entry_v is not None
            and stop_v is not None
            and risk_v is not None
            and risk_v > 0
            and direction in ("LONG", "SHORT")
        )
        if has_geometry:
            status = PaperTradeStatus.WAITING_FOR_ENTRY
            exit_reason = None
        else:
            status = PaperTradeStatus.INVALIDATED
            exit_reason = PaperExitReason.NO_GEOMETRY

        pt_id = _paper_trade_id(
            instrument=instrument,
            timeframe=timeframe,
            direction=direction,
            existing_decision=existing_decision,
            setup_type=setup_type_v,
            plan_id=plan_id_v,
            entry=entry_v,
            stop=stop_v,
            target=target_v,
            engine_risk=risk_v,
            engine_reward=reward_v,
            engine_rr=rr_v,
            created_at=created_at,
            sequence=sequence,
            label=label,
            metadata=meta,
        )

        return PaperTrade(
            paper_trade_id=pt_id,
            instrument=instrument,
            timeframe=timeframe,
            direction=direction,
            existing_decision=existing_decision,
            setup_type=setup_type_v,
            plan_id=plan_id_v,
            created_at=created_at,
            evaluation_timestamp=evaluation_timestamp,
            entry=entry_v,
            stop=stop_v,
            target_1=target_v,
            target_2=None,
            target_2_supported=False,
            engine_risk_distance=risk_v,
            engine_reward_distance=reward_v,
            engine_risk_reward_ratio=rr_v,
            planned_quantity=qty_v,
            planned_risk=planned_risk_v,
            maximum_risk=max_risk_v,
            account_capital=capital_v,
            risk_percent=risk_pct_v,
            status=status,
            exit_reason=exit_reason,
            label=label,
            metadata=meta,
            sequence=sequence,
        )

    # ------------------------------------------------------------
    # LIFECYCLE — TRACK (entry + exit detection)
    # ------------------------------------------------------------

    def track(
        self,
        trade: PaperTrade,
        *,
        completed_candles: Sequence[OHLCVCandle],
        reference_now: datetime,
    ) -> PaperTrade:
        """
        Advance a paper trade's lifecycle using COMPLETED candles.

        Only candles with ``timestamp <= reference_now`` are inspected
        (forming candles are excluded). Entry detection inspects candles
        strictly after ``trade.created_at``; exit detection inspects
        candles strictly after the entry candle. The engine NEVER
        inspects a candle whose timestamp is strictly greater than
        ``reference_now`` (no look-ahead).

        Terminal trades (CLOSED / CANCELLED / INVALIDATED) are returned
        unchanged — a previously resolved paper trade is NEVER altered
        by future candles.

        Returns a NEW immutable :class:`PaperTrade` (the input is never
        mutated).
        """

        # Terminal trades are immutable to further tracking.
        if trade.status.is_terminal:
            return trade

        # INVALIDATED trades have no geometry to track.
        if trade.status is PaperTradeStatus.INVALIDATED:
            return trade

        # Filter to completed candles only (no forming / future candles).
        window = _completed_window(completed_candles, reference_now)
        if not window:
            return trade

        if trade.status is PaperTradeStatus.WAITING_FOR_ENTRY:
            # Advance through entry; if entry is confirmed on a candle,
            # continue to exit detection on the REMAINING candles
            # (strictly after the entry candle) in the SAME call so a
            # single track() advances the trade as far as the available
            # completed candles allow. The entry candle is consumed for
            # entry; exits are detected on subsequent candles only (this
            # avoids fabricating which happened first on the entry
            # candle itself — an honest, conservative rule).
            entered = self._track_entry(trade, window)
            if entered.status is PaperTradeStatus.OPEN:
                return self._track_exit(entered, window)
            return entered

        if trade.status is PaperTradeStatus.OPEN:
            return self._track_exit(trade, window)

        return trade  # pragma: no cover - defensive

    # ------------------------------------------------------------
    # LIFECYCLE — MANUAL CLOSE / CANCEL
    # ------------------------------------------------------------

    def close_manually(
        self,
        trade: PaperTrade,
        *,
        exit_price,
        exit_timestamp: datetime,
    ) -> PaperTrade:
        """
        Close a paper trade manually at an observed market price.

        This is a HUMAN action — the caller supplies an exit price +
        timestamp observed in the market. The engine records it honestly
        and computes realized R / P&L from entry / exit / risk / quantity.
        It is NOT an automatic execution and NOT a broker order.

        Only an OPEN trade may be manually closed. A terminal trade
        raises :class:`ValueError` (illegal transition — never silently
        converted into success). A ``WAITING_FOR_ENTRY`` trade may NOT
        be manually closed with an exit (it has no entry); cancel it
        instead.
        """

        if trade.status.is_terminal:
            raise ValueError(
                f"Cannot manually close a terminal paper trade ({trade.status.value}).",
            )
        if trade.status is not PaperTradeStatus.OPEN:
            raise ValueError(
                "Only an OPEN paper trade may be manually closed "
                "(use cancel() for a waiting trade).",
            )
        if not trade.has_geometry:
            # An OPEN trade always has geometry (entry was confirmed),
            # but guard defensively.
            return self._invalidate(trade)
        exit_price_v = _to_decimal(exit_price)
        if exit_price_v is None:
            raise ValueError("A manual close requires an exit price.")
        realized_r = _realized_r(
            trade.direction, trade.entry, exit_price_v, trade.engine_risk_distance,
        )
        realized_pnl = _realized_pnl(
            trade.direction, trade.entry, exit_price_v, trade.planned_quantity,
        )
        return replace(
            trade,
            status=PaperTradeStatus.CLOSED,
            exit_timestamp=exit_timestamp,
            actual_exit_price=exit_price_v,
            exit_reason=PaperExitReason.MANUAL_CLOSE,
            realized_r=realized_r,
            realized_pnl=realized_pnl,
        )

    def cancel(self, trade: PaperTrade) -> PaperTrade:
        """
        Cancel a paper trade before entry.

        Only a ``WAITING_FOR_ENTRY`` trade may be cancelled. A trade that
        has already entered (OPEN / CLOSED) raises :class:`ValueError`
        (illegal transition). No entry / exit / P&L / R is fabricated.
        """

        if trade.status is not PaperTradeStatus.WAITING_FOR_ENTRY:
            raise ValueError(
                f"Only a WAITING_FOR_ENTRY paper trade may be cancelled "
                f"(trade is {trade.status.value}).",
            )
        return replace(
            trade,
            status=PaperTradeStatus.CANCELLED,
            exit_reason=PaperExitReason.CANCELLED,
        )

    # ------------------------------------------------------------
    # ENTRY DETECTION
    # ------------------------------------------------------------

    def _track_entry(
        self,
        trade: PaperTrade,
        window: Sequence[OHLCVCandle],
    ) -> PaperTrade:
        if not trade.has_geometry:
            return self._invalidate(trade)

        entry_ref = trade.entry
        # Candles strictly after creation (no look-ahead at creation candle).
        candidates = [c for c in window if c.timestamp > trade.created_at]
        candidates = candidates[: self.config.max_entry_bars]

        for candle in candidates:
            if _entry_touched(candle, trade.direction, entry_ref):
                # Entry confirmed at the planned entry reference.
                return replace(
                    trade,
                    status=PaperTradeStatus.OPEN,
                    entry_timestamp=candle.timestamp,
                    actual_entry_price=entry_ref,
                )
        return trade  # still WAITING_FOR_ENTRY (no fabricated entry)

    # ------------------------------------------------------------
    # EXIT DETECTION
    # ------------------------------------------------------------

    def _track_exit(
        self,
        trade: PaperTrade,
        window: Sequence[OHLCVCandle],
    ) -> PaperTrade:
        if not trade.has_geometry or not trade.has_target:
            # No target -> cannot resolve a target/stop outcome. An OPEN
            # trade without a target is unusual (entry requires geometry
            # but target may be None); we leave it OPEN (honest, no
            # fabricated exit). The human may manually close it.
            return trade

        entry_ts = trade.entry_timestamp
        # Candles strictly after the entry candle (no look-ahead at entry).
        candidates = [c for c in window if entry_ts is not None and c.timestamp > entry_ts]
        candidates = candidates[: self.config.max_holding_bars]
        if not candidates:
            return trade

        target = trade.target_1
        stop = trade.stop
        direction = trade.direction

        first_target_bar: int | None = None
        first_stop_bar: int | None = None
        for i, candle in enumerate(candidates):
            target_touched, stop_touched = _touches(
                candle, direction, target, stop,
            )
            if target_touched and first_target_bar is None:
                first_target_bar = i
            if stop_touched and first_stop_bar is None:
                first_stop_bar = i
            # Early exit: both first touches known.
            if first_target_bar is not None and first_stop_bar is not None:
                break

        return self._resolve_exit(
            trade,
            candidates=candidates,
            first_target_bar=first_target_bar,
            first_stop_bar=first_stop_bar,
            target=target,
            stop=stop,
        )

    def _resolve_exit(
        self,
        trade: PaperTrade,
        *,
        candidates: Sequence[OHLCVCandle],
        first_target_bar: int | None,
        first_stop_bar: int | None,
        target: Decimal,
        stop: Decimal,
    ) -> PaperTrade:
        last = candidates[-1]

        # Neither touched -> EXPIRED (mark-to-close at horizon end).
        if first_target_bar is None and first_stop_bar is None:
            exit_price = _to_decimal(last.close)
            realized_r = _realized_r(
                trade.direction, trade.entry, exit_price, trade.engine_risk_distance,
            )
            realized_pnl = _realized_pnl(
                trade.direction, trade.entry, exit_price, trade.planned_quantity,
            )
            return replace(
                trade,
                status=PaperTradeStatus.CLOSED,
                exit_timestamp=last.timestamp,
                actual_exit_price=exit_price,
                exit_reason=PaperExitReason.EXPIRED,
                realized_r=realized_r,
                realized_pnl=realized_pnl,
            )

        # Same-candle ambiguity -> BOTH_TOUCHED (no fabricated winner).
        if first_target_bar is not None and first_stop_bar is not None:
            if first_target_bar == first_stop_bar:
                ambig_candle = candidates[first_target_bar]
                return replace(
                    trade,
                    status=PaperTradeStatus.CLOSED,
                    exit_timestamp=ambig_candle.timestamp,
                    actual_exit_price=None,
                    exit_reason=PaperExitReason.BOTH_TOUCHED,
                    realized_r=None,
                    realized_pnl=None,
                )

        # Target touched first (strictly earlier than stop, or stop never).
        if first_target_bar is not None and (
            first_stop_bar is None or first_target_bar < first_stop_bar
        ):
            realized_r = _realized_r(
                trade.direction, trade.entry, target, trade.engine_risk_distance,
            )
            realized_pnl = _realized_pnl(
                trade.direction, trade.entry, target, trade.planned_quantity,
            )
            target_candle = candidates[first_target_bar]
            return replace(
                trade,
                status=PaperTradeStatus.CLOSED,
                exit_timestamp=target_candle.timestamp,
                actual_exit_price=target,
                exit_reason=PaperExitReason.TARGET_HIT,
                realized_r=realized_r,
                realized_pnl=realized_pnl,
            )

        # Stop touched first.
        realized_r = _realized_r(
            trade.direction, trade.entry, stop, trade.engine_risk_distance,
        )
        realized_pnl = _realized_pnl(
            trade.direction, trade.entry, stop, trade.planned_quantity,
        )
        stop_candle = candidates[first_stop_bar]  # type: ignore[index]
        return replace(
            trade,
            status=PaperTradeStatus.CLOSED,
            exit_timestamp=stop_candle.timestamp,
            actual_exit_price=stop,
            exit_reason=PaperExitReason.STOP_HIT,
            realized_r=realized_r,
            realized_pnl=realized_pnl,
        )

    def _invalidate(self, trade: PaperTrade) -> PaperTrade:
        return replace(
            trade,
            status=PaperTradeStatus.INVALIDATED,
            exit_reason=PaperExitReason.NO_GEOMETRY,
        )


# ============================================================
# HELPERS
# ============================================================


def _completed_window(
    candles: Sequence[OHLCVCandle], reference_now: datetime,
) -> list[OHLCVCandle]:
    """Return candles with ``timestamp <= reference_now`` sorted ascending.

    Forming candles (``timestamp > reference_now``) are excluded.
    Future-dated candles are excluded. The result is sorted by timestamp
    ascending so the first-touch walk is deterministic.
    """

    ref = _naive(reference_now)
    out = [c for c in candles if _naive(c.timestamp) <= ref]
    out.sort(key=lambda c: _naive(c.timestamp))
    return out


def _naive(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt
    return dt.replace(tzinfo=None)


def _entry_touched(
    candle: OHLCVCandle, direction: str, entry_ref: Decimal,
) -> bool:
    """Whether a completed candle touched the entry reference.

    LONG: entry when ``low <= entry`` (price came down to the planned entry).
    SHORT: entry when ``high >= entry`` (price came up to the planned entry).
    """

    e = float(entry_ref)
    if direction == "LONG":
        return candle.low <= e
    if direction == "SHORT":
        return candle.high >= e
    return False


def _touches(
    candle: OHLCVCandle, direction: str, target: Decimal, stop: Decimal,
) -> tuple[bool, bool]:
    """Whether a candle touched the target and/or the stop (OHLC, not close).

    Reuses the established Sprint 11W touch semantics. LONG: target
    touched when ``high >= target``; stop touched when ``low <= stop``.
    SHORT (mirror): target touched when ``low <= target``; stop touched
    when ``high >= stop``.
    """

    t = float(target)
    s = float(stop)
    if direction == "LONG":
        target_touched = candle.high >= t
        stop_touched = candle.low <= s
    else:  # SHORT
        target_touched = candle.low <= t
        stop_touched = candle.high >= s
    return target_touched, stop_touched


def _realized_r(
    direction: str,
    entry: Decimal | None,
    exit_price: Decimal | None,
    risk: Decimal | None,
) -> Decimal | None:
    """Realized R-multiple. ``None`` when any input is missing/invalid."""

    if entry is None or exit_price is None or risk is None or risk <= 0:
        return None
    if direction == "LONG":
        return (exit_price - entry) / risk
    if direction == "SHORT":
        return (entry - exit_price) / risk
    return None


def _realized_pnl(
    direction: str,
    entry: Decimal | None,
    exit_price: Decimal | None,
    quantity: Decimal | None,
) -> Decimal | None:
    """Realized P&L (Decimal). ``None`` when any input is missing."""

    if entry is None or exit_price is None or quantity is None:
        return None
    if direction == "LONG":
        return (exit_price - entry) * quantity
    if direction == "SHORT":
        return (entry - exit_price) * quantity
    return None


def _normalize_metadata(metadata: Mapping[str, str] | None) -> tuple[tuple[str, str], ...]:
    if not metadata:
        return ()
    out: list[tuple[str, str]] = []
    for k, v in metadata.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise ValueError("metadata keys and values must be strings.")
        out.append((k, v))
    out.sort()
    return tuple(out)


def _canonical_value(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, Decimal):
        return f"dec:{value.normalize()!s}"
    if isinstance(value, bool):
        return f"bool:{value}"
    if isinstance(value, int):
        return f"int:{value}"
    if isinstance(value, float):
        return f"num:{value!s}"
    if isinstance(value, datetime):
        return f"dt:{value.isoformat()}"
    return f"str:{value!s}"


def _paper_trade_id(
    *,
    instrument: str,
    timeframe: str,
    direction: str,
    existing_decision: str,
    setup_type: str,
    plan_id: str,
    entry: Decimal | None,
    stop: Decimal | None,
    target: Decimal | None,
    engine_risk: Decimal | None,
    engine_reward: Decimal | None,
    engine_rr: Decimal | None,
    created_at: datetime,
    sequence: int,
    label: str,
    metadata: tuple[tuple[str, str], ...],
) -> str:
    """Deterministic paper-trade id (``"pt-" + sha256[:16]``).

    The id canonicalizes the OPPORTUNITY (plan_id / instrument /
    timeframe / direction / decision / setup_type / geometry) PLUS the
    INSTANCE discriminator (created_at / sequence / label / metadata) so
    two paper trades created from the same opportunity do not collapse
    into one record.
    """

    canonical = json.dumps(
        {
            "instrument": _canonical_value(instrument),
            "timeframe": _canonical_value(timeframe),
            "direction": _canonical_value(direction),
            "existing_decision": _canonical_value(existing_decision),
            "setup_type": _canonical_value(setup_type),
            "plan_id": _canonical_value(plan_id),
            "entry": _canonical_value(entry),
            "stop": _canonical_value(stop),
            "target_1": _canonical_value(target),
            "engine_risk_distance": _canonical_value(engine_risk),
            "engine_reward_distance": _canonical_value(engine_reward),
            "engine_risk_reward_ratio": _canonical_value(engine_rr),
            "created_at": _canonical_value(created_at),
            "sequence": _canonical_value(sequence),
            "label": _canonical_value(label),
            "metadata": [_canonical_value(k) + "=" + _canonical_value(v)
                         for k, v in metadata],
        },
        sort_keys=True,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"pt-{digest[:16]}"


__all__ = ["PaperTradingEngine"]
