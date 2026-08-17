"""
Risk & trade planning engine (Product Phase 4).

This is a RISK / PLANNING layer. It is NOT a market-analysis engine, NOT
a decision engine, NOT a prediction engine, NOT a new scoring engine,
NOT an evidence engine, NOT a strategy engine, NOT a broker, NOT an
execution engine and NOT a paper-trading engine.

It takes an EXISTING trade candidate / trade geometry plus user-supplied
account risk parameters and produces a disciplined, user-specific
TRADE PLAN. It answers ONE question:

    "If I choose to review/take this existing trade candidate, how much
    should I risk and what position size follows from my risk rules?"

It does NOT answer "will this trade definitely win?" It does NOT create
BUY/SELL/ENTER/EXIT/HOLD recommendations. The existing Sprint 11S
decision classification (REJECTED / WATCH / QUALIFIED / PREFERRED) is
AUTHORITATIVE and is never renamed / upgraded / downgraded. The existing
Sprint 11R ``TradeCandidate`` geometry (entry / stop / target /
risk_distance / reward_distance / risk_reward_ratio) is AUTHORITATIVE and
is never recomputed; Phase 4 only performs deterministic calculations
AROUND those existing values.

ACCOUNT RISK vs ENGINE RISK (kept separate):

* ENGINE RISK  = the candidate's existing ``risk_distance`` (per-unit risk
  implied by the structural geometry).
* ENGINE REWARD = the candidate's existing ``reward_distance``.
* ACCOUNT RISK = user-configured ``account_capital`` + ``risk_percent``
  -> ``maximum_risk = account_capital * risk_percent / 100``.

Phase 4 converts engine geometry into account-level risk. It NEVER
modifies the underlying engine geometry.

RISK CALCULATION (documented, deterministic):

    maximum_risk = account_capital * risk_percent / 100
    raw_quantity  = maximum_risk / engine_risk_distance

If the product supports integer quantities only (``allow_fractional_quantity``
is False), the quantity is FLOOR-rounded to the largest integer whose
``planned_risk = quantity * engine_risk_distance`` does NOT exceed
``maximum_risk``. Floor is the ONLY rounding mode that guarantees
``planned_risk <= maximum_risk``; ``round`` / ``ceil`` are rejected by the
config because they could over-risk the account.

    planned_risk   = quantity * engine_risk_distance   (MAXIMUM PLANNED LOSS)
    planned_reward = quantity * engine_reward_distance  (POTENTIAL PLANNED REWARD)

``planned_reward`` is DETERMINISTIC from the engine geometry. It is NOT an
expected return and NOT a prediction. The engine distinguishes "potential
planned reward" from "expected return" explicitly.

NO PREDICTION:

The engine MUST NOT create probability / win probability / expected win
rate / AI confidence / predictive score / expected return based on
prediction / BUY/SELL classification / automatic trade recommendation. It
may display ``planned_reward`` (deterministic) and ``R:R`` (already in the
engine). Evidence is NEVER used to calculate position size and NEVER
converted into a risk percentage.

INSTRUMENT-SPECIFIC QUANTITY:

The repository does NOT contain authoritative broker / exchange contract
metadata (lot sizes, contract multipliers, tick sizes). To avoid
fabricating NSE lot sizes or broker-specific rules, the planner uses a
SAFE GENERIC quantity model by default (unit step, unit multiplier,
fractional allowed). A caller MAY supply a real
:class:`~engine.models.trade_plan.QuantitySpec` for an instrument whose
contract semantics are known; when none is supplied the planner surfaces
``QUANTITY_SPEC_UNAVAILABLE`` in the warnings so a downstream caller
knows the generic model was used. No NSE lot size or broker-specific
contract rule is hard-coded.

TRADE DIRECTION:

The existing trade direction is reused where available (``LONG`` /
``SHORT``). Risk is based on the ABSOLUTE distance between entry and stop
for BOTH directions, so long and short position sizing are
mathematically correct and symmetric. Direction is NEVER inferred from
arbitrary price comparisons when the engine already provides it.

VALIDATION (never silently repairs invalid financial inputs):

The engine rejects or safely marks invalid:

* account capital <= 0
* risk percentage <= 0
* risk percentage above the configured maximum (and below the min when
  a positive min is set)
* missing entry / stop
* zero / negative risk distance
* incomplete geometry
* NaN / infinity
* unsupported quantity specification
* inconsistent candidate geometry (the candidate's own consistency is
  trusted, but the planner guards against zero / negative risk distance)

Invalid inputs become an ``INVALID_INPUT`` plan (or
``GEOMETRY_UNAVAILABLE`` for geometry problems); they are NEVER converted
into a successful trade plan.

DETERMINISTIC ID:

The plan id is ``"plan-" + sha256[:16]`` of the canonical normalized plan
inputs (instrument, timeframe, direction, existing decision,
actionability, account capital, risk percent, engine geometry, quantity
spec availability, label, metadata). No random UUID, no wall-clock, no
memory address. Repeated identical inputs produce identical plan ids;
changing account capital or risk percent changes the plan id.

NO-LOOK-AHEAD (HARD REQUIREMENT, structurally enforced):

The engine consumes ALREADY-COMPUTED engine geometry only and adds NO
future information. The public API ``plan(...)`` takes NO candle /
future-market-data argument. It NEVER calls the Sprint 11W
``OutcomeEvaluator`` and NEVER runs the ``HistoricalEvaluationPipeline``.
Patching both to raise does NOT break trade-plan calculation (a plan
can be built from any already-computed geometry / view).
"""

from __future__ import annotations

import hashlib
import json
from decimal import ROUND_FLOOR, Decimal
from typing import Any, Mapping

from engine.config.trade_plan_config import TradePlanConfig
from engine.models.trade_plan import (
    DEFAULT_QUANTITY_SPEC,
    QuantitySpec,
    QuantityStatus,
    RiskPlanStatus,
    TradePlan,
    _to_decimal,
)


# ============================================================
# ENGINE
# ============================================================


class TradePlanningEngine:
    """
    Convert an EXISTING trade candidate / trade geometry into a
    disciplined, user-specific trade plan.

    The engine is PURE, DETERMINISTIC and STATELESS across calls. It
    consumes already-computed engine geometry (a Sprint 11R
    ``TradeCandidate`` OR a dashboard :class:`~dashboard.views.DashboardTradeView`
    geometry OR explicit geometry values) plus user-supplied account
    risk parameters. It performs NO market analysis, NO decision logic,
    NO prediction, NO execution. The existing decision / geometry are
    AUTHORITATIVE and are never modified.
    """

    def __init__(self, config: TradePlanConfig | None = None) -> None:
        self.config = config or TradePlanConfig()

    # ------------------------------------------------------------
    # PUBLIC API
    # ------------------------------------------------------------

    def plan(
        self,
        *,
        instrument: str,
        timeframe: str,
        account_capital,
        risk_percent,
        geometry: Any | None = None,
        direction: str | None = None,
        existing_decision: str = "",
        actionability: str = "",
        quantity_spec: QuantitySpec | None = None,
        entry=None,
        stop=None,
        target_1=None,
        risk_distance=None,
        reward_distance=None,
        risk_reward_ratio=None,
        label: str = "",
        metadata: Mapping[str, str] | None = None,
    ) -> TradePlan:
        """
        Build a deterministic :class:`TradePlan` from existing geometry +
        user-supplied account-risk parameters.

        The geometry may be supplied EITHER as a ``geometry`` object that
        exposes ``entry`` / ``stop`` / ``target_1`` / ``risk_distance`` /
        ``reward_distance`` / ``risk_reward_ratio`` / ``direction``
        attributes (a dashboard :class:`GeometryView` or a Sprint 11R
        ``TradeCandidate``), OR as explicit ``entry`` / ``stop`` / ... /
        ``risk_reward_ratio`` keyword arguments. Explicit keyword
        arguments OVERRIDE the geometry object's attributes when both are
        supplied.

        The public API takes NO candle / future-market-data argument
        (no-look-ahead). It NEVER calls the outcome evaluator or the
        historical pipeline.

        All financial math is performed in :class:`~decimal.Decimal`.
        """

        cfg = self.config

        # --- Coerce account-risk inputs to Decimal (may raise / mark invalid) ---
        capital_dec, risk_pct_dec, cap_error = _coerce_account_inputs(
            account_capital, risk_percent, cfg,
        )

        # --- Resolve geometry (engine-authoritative, reused verbatim) ---
        geom = _resolve_geometry(
            geometry,
            entry=entry,
            stop=stop,
            target_1=target_1,
            risk_distance=risk_distance,
            reward_distance=reward_distance,
            risk_reward_ratio=risk_reward_ratio,
            direction=direction,
        )
        direction_str = geom["direction"]
        entry_dec = geom["entry"]
        stop_dec = geom["stop"]
        target_dec = geom["target_1"]
        engine_risk = geom["risk_distance"]
        engine_reward = geom["reward_distance"]
        engine_rr = geom["risk_reward_ratio"]

        # --- Determine quantity spec availability ---
        spec_available = quantity_spec is not None
        spec = quantity_spec if spec_available else DEFAULT_QUANTITY_SPEC

        meta = _normalize_metadata(metadata)
        # Merge config label/metadata with call-site label/metadata
        # (call-site wins for label; metadata is concatenated, sorted later).
        effective_label = label or cfg.label
        effective_meta = tuple(cfg.metadata) + tuple(meta)

        warnings: list[str] = []
        rationale = ""

        # --- INVALID INPUT (capital / risk percent) ---
        if cap_error is not None:
            warnings.append(cap_error)
            rationale = (
                "Trade plan could not be computed: invalid account-risk "
                "inputs. No position is sized; nothing is fabricated. "
                "Descriptive only; not a prediction."
            )
            plan = TradePlan(
                plan_id=_plan_id(
                    instrument, timeframe, direction_str, existing_decision,
                    actionability, capital_dec, risk_pct_dec, entry_dec,
                    stop_dec, target_dec, engine_risk, engine_reward,
                    engine_rr, spec_available, effective_label, effective_meta,
                ),
                instrument=instrument,
                timeframe=timeframe,
                direction=direction_str,
                existing_decision=existing_decision or "",
                actionability=actionability or "",
                account_capital=capital_dec,
                risk_percent=risk_pct_dec,
                maximum_risk=None,
                entry=entry_dec,
                stop=stop_dec,
                target_1=target_dec,
                engine_risk_distance=engine_risk,
                engine_reward_distance=engine_reward,
                engine_risk_reward_ratio=engine_rr,
                target_2=None,
                target_2_supported=False,
                quantity=None,
                planned_risk=None,
                planned_reward=None,
                quantity_status=QuantityStatus.UNSIZED,
                risk_plan_status=RiskPlanStatus.INVALID_INPUT,
                quantity_spec_available=spec_available,
                warnings=tuple(warnings),
                rationale=rationale,
                label=effective_label,
                metadata=effective_meta,
            )
            return plan

        # Geometry unavailable -> cannot size a position.
        if not _geometry_has_risk(entry_dec, stop_dec, engine_risk):
            warnings.append(
                "Trade geometry is incomplete: entry / stop or the engine "
                "risk distance is unavailable / non-positive. The plan "
                "reuses the engine geometry verbatim and invents no level.",
            )
            if not spec_available:
                warnings.append(
                    "Instrument quantity specification unavailable: the safe "
                    "generic quantity model is used by default "
                    "(unit step, unit multiplier). No NSE lot size or "
                    "broker-specific contract rule is fabricated.",
                )
            rationale = (
                "Trade plan could not be sized: the existing engine "
                "geometry does not provide a usable entry/stop/risk "
                "distance at this point. No quantity is invented. "
                "Descriptive only; not a prediction."
            )
            return TradePlan(
                plan_id=_plan_id(
                    instrument, timeframe, direction_str, existing_decision,
                    actionability, capital_dec, risk_pct_dec, entry_dec,
                    stop_dec, target_dec, engine_risk, engine_reward,
                    engine_rr, spec_available, effective_label, effective_meta,
                ),
                instrument=instrument,
                timeframe=timeframe,
                direction=direction_str,
                existing_decision=existing_decision or "",
                actionability=actionability or "",
                account_capital=capital_dec,
                risk_percent=risk_pct_dec,
                maximum_risk=(capital_dec * risk_pct_dec / Decimal("100")),
                entry=entry_dec,
                stop=stop_dec,
                target_1=target_dec,
                engine_risk_distance=engine_risk,
                engine_reward_distance=engine_reward,
                engine_risk_reward_ratio=engine_rr,
                target_2=None,
                target_2_supported=False,
                quantity=None,
                planned_risk=None,
                planned_reward=None,
                quantity_status=QuantityStatus.UNSIZED,
                risk_plan_status=RiskPlanStatus.GEOMETRY_UNAVAILABLE,
                quantity_spec_available=spec_available,
                warnings=tuple(warnings),
                rationale=rationale,
                label=effective_label,
                metadata=effective_meta,
            )

        # --- Geometry is available: compute the position size ---
        maximum_risk = capital_dec * risk_pct_dec / Decimal("100")

        # Direction must be directional to size a meaningful position.
        if direction_str not in ("LONG", "SHORT"):
            warnings.append(
                "Trade direction is not directional (LONG/SHORT); the plan "
                "cannot size a position. The engine geometry is reused "
                "verbatim; no quantity is invented.",
            )
            if not spec_available:
                warnings.append(
                    "Instrument quantity specification unavailable: the safe "
                    "generic quantity model is used by default. No NSE lot "
                    "size or broker-specific contract rule is fabricated.",
                )
            rationale = (
                "Trade plan could not be sized: the existing engine did not "
                "provide a directional intent. No quantity is invented. "
                "Descriptive only; not a prediction."
            )
            return TradePlan(
                plan_id=_plan_id(
                    instrument, timeframe, direction_str, existing_decision,
                    actionability, capital_dec, risk_pct_dec, entry_dec,
                    stop_dec, target_dec, engine_risk, engine_reward,
                    engine_rr, spec_available, effective_label, effective_meta,
                ),
                instrument=instrument,
                timeframe=timeframe,
                direction=direction_str,
                existing_decision=existing_decision or "",
                actionability=actionability or "",
                account_capital=capital_dec,
                risk_percent=risk_pct_dec,
                maximum_risk=maximum_risk,
                entry=entry_dec,
                stop=stop_dec,
                target_1=target_dec,
                engine_risk_distance=engine_risk,
                engine_reward_distance=engine_reward,
                engine_risk_reward_ratio=engine_rr,
                target_2=None,
                target_2_supported=False,
                quantity=None,
                planned_risk=None,
                planned_reward=None,
                quantity_status=QuantityStatus.UNSIZED,
                risk_plan_status=RiskPlanStatus.GEOMETRY_UNAVAILABLE,
                quantity_spec_available=spec_available,
                warnings=tuple(warnings),
                rationale=rationale,
                label=effective_label,
                metadata=effective_meta,
            )

        # Raw quantity = maximum_risk / engine_risk_distance.
        raw_quantity = maximum_risk / engine_risk
        step = spec.quantity_step
        mult = spec.contract_multiplier

        # Apply contract multiplier: the risk per "unit" of the tradable
        # contract is engine_risk * mult (one contract covers `mult`
        # underlying units). We size in CONTRACTS first, then the quantity
        # reported is in contracts (the tradable unit). When mult == 1
        # (default), contracts == underlying units.
        risk_per_contract = engine_risk * mult
        if risk_per_contract <= 0:
            warnings.append(
                "Engine risk distance or contract multiplier is non-positive; "
                "the plan cannot size a position.",
            )
            rationale = (
                "Trade plan could not be sized: the per-contract risk is "
                "non-positive. No quantity is invented."
            )
            return TradePlan(
                plan_id=_plan_id(
                    instrument, timeframe, direction_str, existing_decision,
                    actionability, capital_dec, risk_pct_dec, entry_dec,
                    stop_dec, target_dec, engine_risk, engine_reward,
                    engine_rr, spec_available, effective_label, effective_meta,
                ),
                instrument=instrument,
                timeframe=timeframe,
                direction=direction_str,
                existing_decision=existing_decision or "",
                actionability=actionability or "",
                account_capital=capital_dec,
                risk_percent=risk_pct_dec,
                maximum_risk=maximum_risk,
                entry=entry_dec,
                stop=stop_dec,
                target_1=target_dec,
                engine_risk_distance=engine_risk,
                engine_reward_distance=engine_reward,
                engine_risk_reward_ratio=engine_rr,
                target_2=None,
                target_2_supported=False,
                quantity=None,
                planned_risk=None,
                planned_reward=None,
                quantity_status=QuantityStatus.UNSIZED,
                risk_plan_status=RiskPlanStatus.QUANTITY_UNAVAILABLE,
                quantity_spec_available=spec_available,
                warnings=tuple(warnings),
                rationale=rationale,
                label=effective_label,
                metadata=effective_meta,
            )

        raw_contracts = maximum_risk / risk_per_contract

        allow_fractional = (
            spec.allow_fractional_quantity
            if spec_available
            else cfg.allow_fractional_quantity
        )

        quantity, qty_status, qty_warning = _size_quantity(
            raw_contracts, step, allow_fractional, maximum_risk,
            risk_per_contract,
        )

        # Risk-limit exceeded: even one step costs more than maximum_risk.
        if quantity is None or quantity <= 0:
            warnings.append(qty_warning)
            if not spec_available:
                warnings.append(
                    "Instrument quantity specification unavailable: the safe "
                    "generic quantity model is used by default. No NSE lot "
                    "size or broker-specific contract rule is fabricated.",
                )
            rationale = (
                "Trade plan could not be sized: the smallest valid position "
                "would commit more risk than the configured maximum risk. No "
                "oversized position is fabricated. Descriptive only; not a "
                "prediction."
            )
            return TradePlan(
                plan_id=_plan_id(
                    instrument, timeframe, direction_str, existing_decision,
                    actionability, capital_dec, risk_pct_dec, entry_dec,
                    stop_dec, target_dec, engine_risk, engine_reward,
                    engine_rr, spec_available, effective_label, effective_meta,
                ),
                instrument=instrument,
                timeframe=timeframe,
                direction=direction_str,
                existing_decision=existing_decision or "",
                actionability=actionability or "",
                account_capital=capital_dec,
                risk_percent=risk_pct_dec,
                maximum_risk=maximum_risk,
                entry=entry_dec,
                stop=stop_dec,
                target_1=target_dec,
                engine_risk_distance=engine_risk,
                engine_reward_distance=engine_reward,
                engine_risk_reward_ratio=engine_rr,
                target_2=None,
                target_2_supported=False,
                quantity=None,
                planned_risk=None,
                planned_reward=None,
                quantity_status=QuantityStatus.UNSIZED,
                risk_plan_status=RiskPlanStatus.RISK_LIMIT_EXCEEDED,
                quantity_spec_available=spec_available,
                warnings=tuple(warnings),
                rationale=rationale,
                label=effective_label,
                metadata=effective_meta,
            )

        # --- Quantity determined: compute planned risk / reward ---
        planned_risk = quantity * risk_per_contract
        # planned_reward uses the engine reward distance scaled by the
        # same contract multiplier (one contract covers `mult` units, so
        # reward per contract = engine_reward * mult).
        planned_reward = None
        if engine_reward is not None and engine_reward > 0:
            reward_per_contract = engine_reward * mult
            planned_reward = quantity * reward_per_contract

        # Final no-over-risk guard: planned_risk must NOT exceed maximum_risk
        # (floor rounding guarantees this, but we double-check and, if a
        # fractional quantity somehow over-risks due to Decimal quantize
        # edge cases, we floor it).
        if planned_risk > maximum_risk:
            # Defensive: floor to the largest integer that fits.
            safe_q = _floor_to_fit(maximum_risk, risk_per_contract, step)
            if safe_q is None or safe_q <= 0:
                warnings.append(
                    "Planned risk would exceed the configured maximum risk; "
                    "the plan could not be sized safely.",
                )
                rationale = (
                    "Trade plan could not be sized: planned_risk exceeds "
                    "maximum_risk after rounding. No oversized position is "
                    "fabricated."
                )
                return TradePlan(
                    plan_id=_plan_id(
                        instrument, timeframe, direction_str, existing_decision,
                        actionability, capital_dec, risk_pct_dec, entry_dec,
                        stop_dec, target_dec, engine_risk, engine_reward,
                        engine_rr, spec_available, effective_label, effective_meta,
                    ),
                    instrument=instrument,
                    timeframe=timeframe,
                    direction=direction_str,
                    existing_decision=existing_decision or "",
                    actionability=actionability or "",
                    account_capital=capital_dec,
                    risk_percent=risk_pct_dec,
                    maximum_risk=maximum_risk,
                    entry=entry_dec,
                    stop=stop_dec,
                    target_1=target_dec,
                    engine_risk_distance=engine_risk,
                    engine_reward_distance=engine_reward,
                    engine_risk_reward_ratio=engine_rr,
                    target_2=None,
                    target_2_supported=False,
                    quantity=None,
                    planned_risk=None,
                    planned_reward=None,
                    quantity_status=QuantityStatus.UNSIZED,
                    risk_plan_status=RiskPlanStatus.RISK_LIMIT_EXCEEDED,
                    quantity_spec_available=spec_available,
                    warnings=tuple(warnings),
                    rationale=rationale,
                    label=effective_label,
                    metadata=effective_meta,
                )
            quantity = safe_q
            qty_status = QuantityStatus.FLOOR_ROUNDED
            planned_risk = quantity * risk_per_contract
            if engine_reward is not None and engine_reward > 0:
                planned_reward = quantity * (engine_reward * mult)
            warnings.append(
                "Planned risk was floored to the largest quantity that does "
                "not exceed the configured maximum risk.",
            )

        # --- VALID plan ---
        if not spec_available:
            warnings.append(
                "Instrument quantity specification unavailable: the safe "
                "generic quantity model is used by default (unit step, unit "
                "multiplier). No NSE lot size or broker-specific contract "
                "rule is fabricated. Supply an instrument QuantitySpec when "
                "authoritative contract metadata is available.",
            )
        if qty_warning and qty_warning not in warnings:
            warnings.append(qty_warning)
        rationale = (
            f"Trade plan computed: maximum_risk={_fmt_money(maximum_risk)}, "
            f"engine_risk_distance={_fmt_money(engine_risk)}, "
            f"quantity={quantity}, planned_risk={_fmt_money(planned_risk)}"
            + (f", planned_reward={_fmt_money(planned_reward)}"
               if planned_reward is not None else "")
            + ". Entry/stop/target/R:R reused verbatim from the existing "
            "engine geometry; not recomputed. Descriptive only; not a "
            "prediction or guarantee of profitability."
        )
        return TradePlan(
            plan_id=_plan_id(
                instrument, timeframe, direction_str, existing_decision,
                actionability, capital_dec, risk_pct_dec, entry_dec, stop_dec,
                target_dec, engine_risk, engine_reward, engine_rr,
                spec_available, effective_label, effective_meta,
            ),
            instrument=instrument,
            timeframe=timeframe,
            direction=direction_str,
            existing_decision=existing_decision or "",
            actionability=actionability or "",
            account_capital=capital_dec,
            risk_percent=risk_pct_dec,
            maximum_risk=maximum_risk,
            entry=entry_dec,
            stop=stop_dec,
            target_1=target_dec,
            engine_risk_distance=engine_risk,
            engine_reward_distance=engine_reward,
            engine_risk_reward_ratio=engine_rr,
            target_2=None,
            target_2_supported=False,
            quantity=quantity,
            planned_risk=planned_risk,
            planned_reward=planned_reward,
            quantity_status=qty_status,
            risk_plan_status=RiskPlanStatus.VALID,
            quantity_spec_available=spec_available,
            warnings=tuple(warnings),
            rationale=rationale,
            label=effective_label,
            metadata=effective_meta,
        )


# ============================================================
# HELPERS
# ============================================================


def _coerce_account_inputs(
    account_capital, risk_percent, cfg: TradePlanConfig,
) -> tuple[Decimal | None, Decimal | None, str | None]:
    """Coerce capital + risk percent to Decimal; validate bounds.

    Returns ``(capital, risk_pct, error)``. When ``error`` is not None the
    inputs are invalid and the caller builds an INVALID_INPUT plan.
    """

    try:
        capital_dec = _to_decimal(account_capital)
    except (ValueError, TypeError, ArithmeticError):
        return None, None, "Account capital could not be parsed as a number."
    try:
        risk_pct_dec = _to_decimal(risk_percent)
    except (ValueError, TypeError, ArithmeticError):
        return capital_dec, None, "Risk percentage could not be parsed as a number."

    if capital_dec is None or capital_dec <= 0:
        return capital_dec, risk_pct_dec, "Account capital must be positive."
    if risk_pct_dec is None or risk_pct_dec <= 0:
        return (
            capital_dec, risk_pct_dec,
            "Risk percentage must be strictly greater than zero.",
        )
    if risk_pct_dec > cfg.max_risk_percent:
        return (
            capital_dec, risk_pct_dec,
            f"Risk percentage exceeds the configured maximum "
            f"({cfg.max_risk_percent}%).",
        )
    if cfg.min_risk_percent > 0 and risk_pct_dec < cfg.min_risk_percent:
        return (
            capital_dec, risk_pct_dec,
            f"Risk percentage is below the configured minimum "
            f"({cfg.min_risk_percent}%).",
        )
    return capital_dec, risk_pct_dec, None


def _resolve_geometry(
    geometry: Any | None,
    *,
    entry=None,
    stop=None,
    target_1=None,
    risk_distance=None,
    reward_distance=None,
    risk_reward_ratio=None,
    direction: str | None = None,
) -> dict:
    """Resolve the authoritative engine geometry.

    Explicit keyword arguments OVERRIDE the geometry object's attributes
    when both are supplied (so a caller can supply explicit values for
    unit testing without a geometry object). The geometry is reused
    VERBATIM — never recomputed.
    """

    def _attr(name: str, default=None):
        if geometry is None:
            return default
        return getattr(geometry, name, default)

    def _coerce_or_none(value):
        if value is None:
            return None
        try:
            return _to_decimal(value)
        except (ValueError, TypeError, ArithmeticError):
            return None

    # Direction: prefer explicit, then geometry attribute names.
    dir_str = direction
    if dir_str is None and geometry is not None:
        for attr in ("direction", "candidate_direction"):
            v = getattr(geometry, attr, None)
            if v is not None:
                dir_str = v.name if hasattr(v, "name") else str(v)
                break
    if dir_str is not None and hasattr(dir_str, "name"):
        dir_str = dir_str.name
    dir_str = (dir_str or "") if dir_str is not None else ""

    # Geometry values: explicit overrides geometry object.
    entry_v = entry if entry is not None else _attr("entry")
    stop_v = stop if stop is not None else _attr("stop")
    target_v = target_1 if target_1 is not None else _attr("target_1")
    risk_v = risk_distance if risk_distance is not None else _attr("risk_distance")
    reward_v = (
        reward_distance if reward_distance is not None else _attr("reward_distance")
    )
    rr_v = (
        risk_reward_ratio if risk_reward_ratio is not None
        else _attr("risk_reward_ratio")
    )

    # Fall back to *_reference names for a raw TradeCandidate.
    if geometry is not None:
        if entry_v is None:
            entry_v = _attr("entry_reference")
        if stop_v is None:
            stop_v = _attr("stop_reference")
        if target_v is None:
            target_v = _attr("target_reference")

    return {
        "direction": dir_str,
        "entry": _coerce_or_none(entry_v),
        "stop": _coerce_or_none(stop_v),
        "target_1": _coerce_or_none(target_v),
        "risk_distance": _coerce_or_none(risk_v),
        "reward_distance": _coerce_or_none(reward_v),
        "risk_reward_ratio": _coerce_or_none(rr_v),
    }


def _geometry_has_risk(
    entry: Decimal | None, stop: Decimal | None, risk: Decimal | None,
) -> bool:
    """Whether the geometry provides a usable risk distance."""

    return (
        entry is not None
        and stop is not None
        and risk is not None
        and risk > 0
    )


def _size_quantity(
    raw: Decimal,
    step: Decimal,
    allow_fractional: bool,
    maximum_risk: Decimal,
    risk_per_contract: Decimal,
) -> tuple[Decimal | None, QuantityStatus, str]:
    """Size the quantity from the raw division.

    Returns ``(quantity, status, warning)``. ``quantity`` is ``None`` when
    the smallest valid position exceeds the maximum risk (RISK_LIMIT).

    Rounding policy (documented):

    * When ``allow_fractional`` is ``True`` the quantity is the RAW value
      ``maximum_risk / risk_per_contract`` at full ``Decimal`` precision
      (any fractional position is permitted). The ``quantity_step`` is
      NOT enforced as a multiple in this mode — it is the natural unit
      only. ``planned_risk = raw * risk_per_contract = maximum_risk``
      exactly, so a fractional quantity NEVER over-risks the account.
    * When ``allow_fractional`` is ``False`` the quantity is FLOOR-rounded
      to the largest integer multiple of ``quantity_step`` whose
      ``planned_risk`` does NOT exceed ``maximum_risk``. Floor is the
      ONLY rounding mode that guarantees ``planned_risk <= maximum_risk``;
      ``round`` / ``ceil`` are rejected by the config.

    ``quantity_status`` distinguishes DETERMINED (exact integer),
    FRACTIONAL_ALLOWED (fractional) and FLOOR_ROUNDED (floored).
    """

    if raw <= 0:
        return None, QuantityStatus.UNSIZED, (
            "Maximum risk is too small to size any position for the given "
            "risk distance."
        )

    if allow_fractional:
        # Fractional allowed: raw value at full precision. planned_risk
        # == maximum_risk exactly (raw = maximum_risk / risk_per_contract),
        # so the no-over-risk invariant holds by construction.
        q = raw
        if q <= 0:
            return None, QuantityStatus.UNSIZED, (
                "Fractional quantity fell to zero; the smallest valid "
                "position would exceed the maximum risk."
            )
        return q, QuantityStatus.FRACTIONAL_ALLOWED, ""

    # Integer-only: floor to the largest integer-step quantity whose
    # planned_risk does NOT exceed maximum_risk.
    q = _floor_to_fit(maximum_risk, risk_per_contract, step)
    if q is None or q <= 0:
        return None, QuantityStatus.UNSIZED, (
            "The smallest valid integer position would commit more risk "
            "than the configured maximum risk (risk-limit exceeded)."
        )
    # If raw was already an exact integer and equal to q, it is
    # DETERMINED; otherwise FLOOR_ROUNDED.
    raw_int = raw.to_integral_value(rounding=ROUND_FLOOR)
    if raw == raw_int and raw == q:
        return q, QuantityStatus.DETERMINED, ""
    return q, QuantityStatus.FLOOR_ROUNDED, ""


def _quantize_floor_to_step(raw: Decimal, step: Decimal) -> Decimal | None:
    """Floor-quantize ``raw`` to the nearest multiple of ``step`` > 0.

    Returns the largest ``k * step`` with ``k > 0`` and ``k * step <= raw``
    (so a fractional quantity never over-risks). Returns ``None`` when no
    positive multiple fits.
    """

    if raw <= 0 or step <= 0:
        return None
    # k = floor(raw / step); quantity = k * step.
    k = (raw / step).to_integral_value(rounding=ROUND_FLOOR)
    if k <= 0:
        return None
    return k * step


def _floor_to_fit(
    maximum_risk: Decimal, risk_per_contract: Decimal, step: Decimal,
) -> Decimal | None:
    """Largest integer-step quantity whose planned_risk <= maximum_risk."""

    if risk_per_contract <= 0 or step <= 0:
        return None
    # max_contracts = floor(maximum_risk / risk_per_contract)
    max_contracts = (
        maximum_risk / risk_per_contract
    ).to_integral_value(rounding=ROUND_FLOOR)
    # Snap to the step (when step > 1, only multiples of step are tradable).
    if step != 1:
        max_steps = (max_contracts / step).to_integral_value(
            rounding=ROUND_FLOOR,
        )
        max_contracts = max_steps * step
    if max_contracts <= 0:
        return None
    return max_contracts


def _normalize_metadata(metadata: Mapping[str, str] | None) -> tuple[tuple[str, str], ...]:
    if not metadata:
        return ()
    out: list[tuple[str, str]] = []
    for k, v in metadata.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise ValueError("metadata keys and values must be strings.")
        out.append((k, v))
    # Sort for determinism.
    out.sort()
    return tuple(out)


def _canonical_value(value) -> str:
    """Canonical string representation of a value for the plan id."""

    if value is None:
        return "null"
    if isinstance(value, Decimal):
        # Normalize so e.g. Decimal("1.0") and Decimal("1") hash the same.
        return f"dec:{value.normalize()!s}"
    if isinstance(value, bool):
        return f"bool:{value}"
    if isinstance(value, (int, float)):
        return f"num:{value!s}"
    return f"str:{value!s}"


def _plan_id(
    instrument: str,
    timeframe: str,
    direction: str,
    existing_decision: str,
    actionability: str,
    capital: Decimal | None,
    risk_pct: Decimal | None,
    entry: Decimal | None,
    stop: Decimal | None,
    target: Decimal | None,
    engine_risk: Decimal | None,
    engine_reward: Decimal | None,
    engine_rr: Decimal | None,
    spec_available: bool,
    label: str,
    metadata: tuple[tuple[str, str], ...],
) -> str:
    """Deterministic plan id (``"plan-" + sha256[:16]``)."""

    canonical = json.dumps(
        {
            "instrument": _canonical_value(instrument),
            "timeframe": _canonical_value(timeframe),
            "direction": _canonical_value(direction),
            "existing_decision": _canonical_value(existing_decision),
            "actionability": _canonical_value(actionability),
            "account_capital": _canonical_value(capital),
            "risk_percent": _canonical_value(risk_pct),
            "entry": _canonical_value(entry),
            "stop": _canonical_value(stop),
            "target_1": _canonical_value(target),
            "engine_risk_distance": _canonical_value(engine_risk),
            "engine_reward_distance": _canonical_value(engine_reward),
            "engine_risk_reward_ratio": _canonical_value(engine_rr),
            "quantity_spec_available": _canonical_value(spec_available),
            "label": _canonical_value(label),
            "metadata": [_canonical_value(k) + "=" + _canonical_value(v)
                         for k, v in metadata],
        },
        sort_keys=True,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"plan-{digest[:16]}"


def _fmt_money(value: Decimal | None) -> str:
    if value is None:
        return "unavailable"
    return f"{value}"


__all__ = ["TradePlanningEngine"]
