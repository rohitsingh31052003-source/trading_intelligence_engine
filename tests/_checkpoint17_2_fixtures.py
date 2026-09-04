"""Shared fixtures for Checkpoint 17.2 broker-adapter contract tests.

These helpers build the full frozen chain (TradePlan -> OperationalTradeIntent
-> ExecutionAuthorization -> ExecutionCommand) so tests can exercise the
broker-neutral adapter contract without re-deriving the frozen artifacts.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Any

from engine.models.execution_authorization import (
    AuthorizationStatus,
    ExecutionAuthorization,
    create_authorization,
)
from engine.models.execution_command import ExecutionCommand, ExecutionMode, create_execution_command
from engine.models.operational_trade_intent import OperationalTradeIntent, create_intent_from_plan
from engine.models.trade_plan import RiskPlanStatus


def make_intent(**overrides: Any) -> OperationalTradeIntent:
    """Create a valid OperationalTradeIntent for testing."""

    base = {
        "plan_id": "plan-abc123def4567890",
        "instrument": "NIFTY",
        "timeframe": "15m",
        "direction": "LONG",
        "entry": Decimal("100.50"),
        "stop": Decimal("95.00"),
        "target_1": Decimal("110.00"),
        "engine_risk_distance": Decimal("5.50"),
        "engine_reward_distance": Decimal("9.50"),
        "engine_risk_reward_ratio": Decimal("1.727"),
        "quantity": Decimal("10"),
        "planned_risk": Decimal("55.00"),
        "maximum_risk": Decimal("100.00"),
        "risk_plan_status": RiskPlanStatus.VALID,
        "existing_decision": "QUALIFIED",
        "actionability": "READY_FOR_REVIEW",
        "created_at": datetime.datetime(2026, 9, 1, 12, 0, 0, tzinfo=datetime.timezone.utc),
        "evaluation_timestamp": datetime.datetime(2026, 9, 1, 11, 59, 0, tzinfo=datetime.timezone.utc),
        "valid_until": datetime.datetime(2026, 9, 2, 12, 0, 0, tzinfo=datetime.timezone.utc),
        "warnings": ("geometry-incomplete",),
        "rationale": "Test plan rationale.",
        "label": "test-label",
        "metadata": (("key", "value"),),
    }
    base.update(overrides)
    return create_intent_from_plan(**base)


def make_authorization(intent: OperationalTradeIntent, **overrides: Any) -> ExecutionAuthorization:
    """Create a valid AUTHORIZED ExecutionAuthorization for testing."""

    base = {
        "intent": intent,
        "status": AuthorizationStatus.AUTHORIZED,
        "authorized_at": datetime.datetime(2026, 9, 1, 12, 0, 0, tzinfo=datetime.timezone.utc),
        "valid_from": datetime.datetime(2026, 9, 1, 12, 0, 0, tzinfo=datetime.timezone.utc),
        "expires_at": datetime.datetime(2026, 9, 2, 12, 0, 0, tzinfo=datetime.timezone.utc),
        "issuer": "test-issuer",
        "authorization_method": "explicit-approval",
        "scope": "paper",
        "policy_reference": "policy-v1",
        "safety_check_summary": "all-checks-passed",
        "label": "test-auth-label",
        "metadata": (("auth-key", "auth-value"),),
    }
    base.update(overrides)
    return create_authorization(**base)


def make_command(
    intent: OperationalTradeIntent,
    authorization: ExecutionAuthorization,
    **overrides: Any,
) -> ExecutionCommand:
    """Create a valid ExecutionCommand for testing."""

    base = {
        "intent": intent,
        "authorization": authorization,
        "created_at": datetime.datetime(2026, 9, 1, 12, 0, 0, tzinfo=datetime.timezone.utc),
        "valid_from": None,
        "valid_until": None,
        "label": "test-cmd-label",
        "metadata": (("cmd-key", "cmd-value"),),
    }
    base.update(overrides)
    return create_execution_command(**base)


def make_live_command(
    intent: OperationalTradeIntent,
    authorization: ExecutionAuthorization,
    **overrides: Any,
) -> ExecutionCommand:
    """Create a LIVE-mode command (authorization scope 'live')."""

    auth = make_authorization(intent, scope="live", **{})
    return make_command(intent, auth, **overrides)


def make_paper_command(
    intent: OperationalTradeIntent,
    authorization: ExecutionAuthorization,
    **overrides: Any,
) -> ExecutionCommand:
    """Create a PAPER-mode command."""

    return make_command(intent, authorization, **overrides)


def utc(y: int, m: int, d: int, h: int = 12, minute: int = 0) -> datetime.datetime:
    """Timezone-aware UTC datetime helper."""

    return datetime.datetime(y, m, d, h, minute, 0, tzinfo=datetime.timezone.utc)
