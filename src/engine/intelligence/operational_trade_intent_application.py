"""
Operational Trade Intent application service (Checkpoint 14.5).

This is the APPLICATION-LEVEL OWNER of explicit OperationalTradeIntent
creation. It wraps the :class:`OperationalTradeIntentEngine` and exposes
the clean application-level workflow:

    TradePlan -> OperationalTradeIntentApplicationService -> OperationalTradeIntent

The service is the single entry point through which any explicit caller
(dashboard, CLI, future authorization layer) requests creation of an
OperationalTradeIntent from an existing authoritative TradePlan.

It is NOT a market-analysis engine, NOT a decision engine, NOT a
prediction engine, NOT a scoring engine, NOT an evidence engine, NOT a
strategy engine, NOT a broker, NOT an execution engine, NOT a
paper-trading engine, and NOT an authorization engine.

Its ONLY responsibility is to provide a clean, reusable, testable
application-level facade over the OperationalTradeIntentEngine. It
delegates ALL authoritative construction to the engine (which in turn
delegates to the factory).

DESIGN:

* Stateless service (no mutable state, no caching, no registry).
* Pure delegation to the authoritative engine.
* Deterministic: repeated calls with the same inputs produce intents with
  identical identity per the Checkpoint 14.2 contract.
* No market data, no candles, no PaperTrade, no authorization, no
  execution, no broker, no persistence.
* Reusable outside the dashboard (CLI, future authorization layer, tests).
"""

from __future__ import annotations

from datetime import datetime
from typing import Mapping

from engine.intelligence.operational_trade_intent import (
    OperationalTradeIntentEngine,
)
from engine.models.operational_trade_intent import OperationalTradeIntent
from engine.models.trade_plan import TradePlan


class OperationalTradeIntentApplicationService:
    """
    Application-level owner of explicit OperationalTradeIntent creation.

    This service is the single entry point through which any explicit
    caller requests creation of an OperationalTradeIntent from an existing
    authoritative TradePlan.

    The service is STATELESS across calls. It holds no mutable state, no
    cache, no registry. Repeated calls with the same authoritative inputs
    behave according to the Checkpoint 14.2 identity contract.

    The service performs NO market analysis, NO decision logic, NO
    prediction, NO execution, NO authorization, NO paper-trading
    lifecycle management. It ONLY delegates to the authoritative engine.
    """

    def __init__(
        self,
        engine: OperationalTradeIntentEngine | None = None,
    ) -> None:
        """
        Initialize the application service.

        Args:
            engine:
                Optional :class:`OperationalTradeIntentEngine` instance.
                When ``None``, a default engine is created. Injection
                supports testing and future engine customization.
        """

        self._engine = engine or OperationalTradeIntentEngine()

    def create_intent_from_trade_plan(
        self,
        plan: TradePlan,
        *,
        created_at: datetime,
        evaluation_timestamp: datetime | None = None,
        valid_until: datetime | None = None,
        label: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> OperationalTradeIntent:
        """
        Create an OperationalTradeIntent from an authoritative TradePlan.

        This is the EXPLICIT application-level workflow. The intent is
        created ONLY through this call. It is NEVER created automatically
        as a side effect of any other operation.

        Args:
            plan:
                The already-created authoritative :class:`TradePlan`. Must
                be a ``TradePlan`` instance. The plan is consumed by value
                (fields extracted); it is NEVER mutated.
            created_at:
                Timezone-aware creation timestamp. REQUIRED. Supplied by
                the caller/application layer; the service NEVER generates
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
                fails (propagated from the engine/factory).
        """

        return self._engine.create_from_plan(
            plan,
            created_at=created_at,
            evaluation_timestamp=evaluation_timestamp,
            valid_until=valid_until,
            label=label,
            metadata=metadata,
        )


__all__ = ["OperationalTradeIntentApplicationService"]
