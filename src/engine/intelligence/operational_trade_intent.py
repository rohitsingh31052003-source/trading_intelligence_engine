"""
Operational Trade Intent engine (Checkpoint 14.4).

This is the dedicated, explicit creation workflow for
:class:`~engine.models.operational_trade_intent.OperationalTradeIntent`.

It is NOT a market-analysis engine, NOT a decision engine, NOT a
prediction engine, NOT a scoring engine, NOT an evidence engine, NOT a
strategy engine, NOT a broker, NOT an execution engine, NOT a
paper-trading engine, and NOT an authorization engine.

Its ONLY responsibility is to convert an ALREADY-CREATED authoritative
:class:`~engine.models.trade_plan.TradePlan` into an
:class:`~engine.models.operational_trade_intent.OperationalTradeIntent`
through an explicit, deterministic, validated workflow:

    TradePlan -> OperationalTradeIntentEngine -> OperationalTradeIntent

The engine delegates authoritative construction to the existing factory
:func:`~engine.models.operational_trade_intent.create_intent_from_plan`.
It does NOT recalculate entry, stop, target, quantity, planned risk,
planned reward, risk distance, reward distance, risk/reward ratio, or any
planning geometry. It copies TradePlan values VERBATIM into the factory.

EXPLICIT WORKFLOW:

    The intent is created ONLY through an explicit call to
    :meth:`OperationalTradeIntentEngine.create_from_plan`. It is NEVER
    created automatically as a side effect of market scanning, trade
    planning, paper trading, dashboard rendering, or any other path.

TIMESTAMPS:

    Per Checkpoint 14.3, timestamps are supplied by the caller/application
    layer. The engine NEVER generates timestamps silently and NEVER calls
    ``datetime.now()``. The ``created_at`` timestamp is REQUIRED;
    ``evaluation_timestamp`` and ``valid_until`` are optional.

DESIGN:

* Stateless engine (no mutable state, no caching, no registry).
* Pure delegation to the authoritative factory.
* Deterministic: repeated calls with the same inputs produce intents with
  identical identity per the Checkpoint 14.2 contract.
* No market data, no candles, no PaperTrade, no authorization, no
  execution, no broker, no persistence.
"""

from __future__ import annotations

from datetime import datetime
from typing import Mapping

from engine.models.operational_trade_intent import create_intent_from_plan
from engine.models.trade_plan import RiskPlanStatus, TradePlan


class OperationalTradeIntentEngine:
    """
    Convert an already-created authoritative TradePlan into an
    OperationalTradeIntent through an explicit, deterministic, validated
    workflow.

    The engine is STATELESS across calls. It holds no mutable state, no
    cache, no registry. Repeated calls with the same authoritative inputs
    behave according to the Checkpoint 14.2 identity contract.

    The engine performs NO market analysis, NO decision logic, NO
    prediction, NO execution, NO authorization, NO paper-trading
    lifecycle management. It ONLY delegates to the authoritative factory.
    """

    def create_from_plan(
        self,
        plan: TradePlan,
        *,
        created_at: datetime,
        evaluation_timestamp: datetime | None = None,
        valid_until: datetime | None = None,
        label: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> object:
        """
        Create an OperationalTradeIntent from an authoritative TradePlan.

        This is the EXPLICIT creation workflow. The intent is created ONLY
        through this call. It is NEVER created automatically as a side
        effect of any other operation.

        Args:
            plan:
                The already-created authoritative :class:`TradePlan`. Must
                be a ``TradePlan`` instance. The plan is consumed by value
                (fields extracted); it is NEVER mutated.
            created_at:
                Timezone-aware creation timestamp. REQUIRED. Supplied by
                the caller/application layer; the engine NEVER generates
                it silently.
            evaluation_timestamp:
                Timezone-aware timestamp of when market data was
                evaluated, or ``None`` if not applicable.
            valid_until:
                Timezone-aware policy-derived expiry, or ``None`` if no
                expiry.
            label:
                Optional caller-supplied identity label. When ``None``, the
                plan's label is used.
            metadata:
                Optional caller-supplied metadata. When ``None``, the plan's
                metadata is used.

        Returns:
            An immutable :class:`~engine.models.operational_trade_intent.OperationalTradeIntent`.

        Raises:
            TypeError:
                If ``plan`` is not a ``TradePlan`` instance.
            ValueError:
                If the plan's risk plan status is not VALID, if the
                direction is not LONG/SHORT, or if timestamp validation
                fails (propagated from the factory).
        """

        # --- Type validation ---
        if not isinstance(plan, TradePlan):
            raise TypeError(
                f"Expected a TradePlan instance; got {type(plan).__name__!r}.",
            )

        # --- Engine-level precondition validation ---
        # The factory also validates these; we validate here to provide a
        # clear engine-boundary error message before delegation.
        if plan.risk_plan_status is not RiskPlanStatus.VALID:
            raise ValueError(
                "Cannot create intent from non-VALID TradePlan "
                f"(risk_plan_status={plan.risk_plan_status.value})."
            )
        if plan.direction not in ("LONG", "SHORT"):
            raise ValueError(
                f"Intent requires directional bias (LONG/SHORT); "
                f"got {plan.direction!r}."
            )

        # --- Resolve caller-supplied vs plan-sourced values ---
        effective_label = label if label is not None else plan.label
        effective_metadata = (
            _normalize_metadata(metadata)
            if metadata is not None
            else plan.metadata
        )

        # --- Delegate to the authoritative factory ---
        # The factory copies TradePlan values VERBATIM and computes the
        # deterministic identity + content fingerprint. The engine performs
        # NO recalculation.
        return create_intent_from_plan(
            plan_id=plan.plan_id,
            instrument=plan.instrument,
            timeframe=plan.timeframe,
            direction=plan.direction,
            entry=plan.entry,
            stop=plan.stop,
            target_1=plan.target_1,
            engine_risk_distance=plan.engine_risk_distance,
            engine_reward_distance=plan.engine_reward_distance,
            engine_risk_reward_ratio=plan.engine_risk_reward_ratio,
            quantity=plan.quantity,
            planned_risk=plan.planned_risk,
            maximum_risk=plan.maximum_risk,
            risk_plan_status=plan.risk_plan_status,
            existing_decision=plan.existing_decision,
            actionability=plan.actionability,
            created_at=created_at,
            evaluation_timestamp=evaluation_timestamp,
            valid_until=valid_until,
            warnings=plan.warnings,
            rationale=plan.rationale,
            label=effective_label,
            metadata=effective_metadata,
        )


def _normalize_metadata(
    metadata: Mapping[str, str] | None,
) -> tuple[tuple[str, str], ...]:
    """Normalize caller-supplied metadata to a sorted tuple of pairs."""
    if not metadata:
        return ()
    out: list[tuple[str, str]] = []
    for k, v in metadata.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise ValueError("metadata keys and values must be strings.")
        out.append((k, v))
    out.sort()
    return tuple(out)


__all__ = ["OperationalTradeIntentEngine"]
