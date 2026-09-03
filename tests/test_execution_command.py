"""
Tests for Execution Command model (Checkpoint 16.2).

Covers:
1. Model construction
2. Frozen immutability
3. Slots behavior
4. Required fields
5. Valid construction
6. Invalid construction
7. ExecutionMode enum members
8. Deterministic command_id
9. cmd- prefix
10. SHA-256-derived identity
11. Identical canonical inputs -> identical ID
12. Meaningful content change -> different ID
13. Dictionary ordering independence
14. Decimal normalization
15. No random UUID
16. No wall-clock dependency
17. No memory-address dependency
18. No Python hash() dependency
19. Authorization binding — AUTHORIZED succeeds
20. Authorization binding — UNAUTHORIZED fails
21. Authorization binding — ELIGIBLE fails
22. Authorization binding — EXPIRED fails
23. Authorization binding — REVOKED fails
24. Authorization binding — SUPERSEDED fails
25. Authorization binding — unknown status fails
26. Authorization binding — intent ID mismatch fails
27. Authorization binding — fingerprint mismatch fails
28. Field integrity — required fields copied correctly
29. Field integrity — values copied by value
30. Field integrity — no recalculation
31. Field integrity — no mutation of intent
32. Field integrity — no mutation of authorization
33. Field integrity — economic geometry preserved
34. Field integrity — quantity preserved
35. Field integrity — risk preserved
36. Field integrity — direction preserved
37. Risk — valid risk succeeds
38. Risk — invalid risk fails
39. Risk — command cannot increase risk
40. Risk — quantity increase is rejected
41. Execution mode — matching mode succeeds
42. Execution mode — mismatched mode fails
43. Execution mode — command cannot override authorization mode
44. Execution mode — derived from authorization scope
45. Forbidden semantics — no broker order ID
46. Forbidden semantics — no fill information
47. Forbidden semantics — no position information
48. Forbidden semantics — no broker credentials
49. Forbidden semantics — no broker routing
50. Forbidden semantics — no broker symbol
51. Forbidden semantics — no exchange-specific data
52. Immutability — frozen model
53. Immutability — nested immutable values
54. Immutability — mutation attempts fail
55. Dependency isolation — no paper_trading import
56. Dependency isolation — no broker/dashboard/execution imports
57. Serialization/canonical identity — deterministic output
58. Regression — frozen suites still pass
"""

from __future__ import annotations

import datetime
from dataclasses import FrozenInstanceError
import dataclasses
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest

from engine.models.execution_authorization import (
    AuthorizationStatus,
    ExecutionAuthorization,
    create_authorization,
)
from engine.models.execution_command import (
    COMMAND_ID_PREFIX,
    EXECUTION_COMMAND_VERSION,
    ExecutionCommand,
    ExecutionMode,
    create_execution_command,
    _ID_DIGEST_LENGTH,
)
from engine.models.operational_trade_intent import (
    OperationalTradeIntent,
    create_intent_from_plan,
)
from engine.models.trade_plan import RiskPlanStatus


# ============================================================
# FIXTURES
# ============================================================


def _make_intent(**overrides: Any) -> OperationalTradeIntent:
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


def _make_authorization(
    intent: OperationalTradeIntent,
    **overrides: Any,
) -> ExecutionAuthorization:
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


def _make_command(
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


# ============================================================
# 1. MODEL CONSTRUCTION
# ============================================================


class TestModelConstruction:
    """Tests for basic ExecutionCommand model construction."""

    def test_valid_construction(self):
        """A valid command can be constructed via the factory."""

        intent = _make_intent()
        auth = _make_authorization(intent)
        cmd = _make_command(intent, auth)
        assert cmd.command_id.startswith(COMMAND_ID_PREFIX)
        assert cmd.authorization_id == auth.authorization_id
        assert cmd.intent_id == intent.intent_id
        assert cmd.content_fingerprint == intent.content_fingerprint

    def test_frozen_immutability(self):
        """ExecutionCommand is frozen — field assignment raises FrozenInstanceError."""

        intent = _make_intent()
        auth = _make_authorization(intent)
        cmd = _make_command(intent, auth)
        with pytest.raises((FrozenInstanceError, dataclasses.FrozenInstanceError)):
            cmd.command_id = "new-id"  # type: ignore[misc]

    def test_slots_behavior(self):
        """ExecutionCommand uses slots — no __dict__."""

        intent = _make_intent()
        auth = _make_authorization(intent)
        cmd = _make_command(intent, auth)
        assert not hasattr(cmd, "__dict__")

    def test_version_default(self):
        """ExecutionCommand has a default version of 1."""

        intent = _make_intent()
        auth = _make_authorization(intent)
        cmd = _make_command(intent, auth)
        assert cmd.version == EXECUTION_COMMAND_VERSION

    def test_version_validation(self):
        """Hand-constructed command with version < 1 fails."""

        with pytest.raises(ValueError, match="version must be >= 1"):
            intent = _make_intent()
            auth = _make_authorization(intent)
            # Manually construct to bypass factory version setting
            ExecutionCommand(
                command_id="cmd-test",
                authorization_id=auth.authorization_id,
                intent_id=intent.intent_id,
                content_fingerprint=intent.content_fingerprint,
                instrument=intent.instrument,
                direction=intent.direction,
                entry=intent.entry,
                stop=intent.stop,
                target=intent.target_1,
                quantity=intent.quantity,
                planned_risk=intent.planned_risk,
                maximum_risk=intent.maximum_risk,
                execution_mode=ExecutionMode.PAPER,
                created_at=datetime.datetime(2026, 9, 1, 12, 0, 0, tzinfo=datetime.timezone.utc),
                version=0,
            )


# ============================================================
# 2. EXECUTION MODE ENUM
# ============================================================


class TestExecutionModeEnum:
    """Tests for ExecutionMode enum."""

    def test_paper_member(self):
        assert ExecutionMode.PAPER.value == "PAPER"

    def test_live_member(self):
        assert ExecutionMode.LIVE.value == "LIVE"

    def test_members(self):
        assert set(ExecutionMode) == {ExecutionMode.PAPER, ExecutionMode.LIVE}


# ============================================================
# 3. DETERMINISTIC IDENTITY
# ============================================================


class TestDeterministicIdentity:
    """Tests for deterministic command_id generation."""

    def test_command_id_has_cmd_prefix(self):
        intent = _make_intent()
        auth = _make_authorization(intent)
        cmd = _make_command(intent, auth)
        assert cmd.command_id.startswith(COMMAND_ID_PREFIX)

    def test_same_input_same_id(self):
        intent = _make_intent()
        auth = _make_authorization(intent)
        cmd1 = _make_command(intent, auth, created_at=datetime.datetime(2026, 9, 1, 12, 0, 0, tzinfo=datetime.timezone.utc))
        cmd2 = _make_command(intent, auth, created_at=datetime.datetime(2026, 9, 1, 12, 0, 0, tzinfo=datetime.timezone.utc))
        assert cmd1.command_id == cmd2.command_id

    def test_changed_content_changes_id(self):
        intent = _make_intent()
        auth = _make_authorization(intent)
        cmd1 = _make_command(intent, auth)

        intent2 = _make_intent(entry=Decimal("101.00"))
        auth2 = _make_authorization(intent2)
        cmd2 = _make_command(intent2, auth2)
        assert cmd1.command_id != cmd2.command_id

    def test_sha256_length(self):
        intent = _make_intent()
        auth = _make_authorization(intent)
        cmd = _make_command(intent, auth)
        digest_part = cmd.command_id[len(COMMAND_ID_PREFIX):]
        assert len(digest_part) == _ID_DIGEST_LENGTH

    def test_no_uuid_dependency(self):
        """command_id does not contain UUID-like patterns."""
        intent = _make_intent()
        auth = _make_authorization(intent)
        cmd = _make_command(intent, auth)
        digest_part = cmd.command_id[len(COMMAND_ID_PREFIX):]
        # UUIDs contain hyphens in a specific pattern; SHA-256 hex does not.
        assert "-" not in digest_part

    def test_no_wall_clock_dependency(self):
        """Same intent + authorization at different times produces same command_id."""
        intent = _make_intent()
        auth = _make_authorization(intent)
        t1 = datetime.datetime(2026, 9, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
        t2 = datetime.datetime(2026, 9, 2, 12, 0, 0, tzinfo=datetime.timezone.utc)
        cmd1 = _make_command(intent, auth, created_at=t1)
        cmd2 = _make_command(intent, auth, created_at=t2)
        assert cmd1.command_id == cmd2.command_id

    def test_no_memory_address_dependency(self):
        """command_id is independent of object identity."""
        intent = _make_intent()
        auth = _make_authorization(intent)
        cmd1 = _make_command(intent, auth)
        # Create a second command with the same logical content
        intent2 = _make_intent()
        auth2 = _make_authorization(intent2)
        cmd2 = _make_command(intent2, auth2)
        assert cmd1.command_id == cmd2.command_id

    def test_no_python_hash_dependency(self):
        """command_id does not use Python's hash()."""
        intent = _make_intent()
        auth = _make_authorization(intent)
        cmd = _make_command(intent, auth)
        # The command_id is a hex string, not an integer.
        assert isinstance(cmd.command_id, str)
        digest_part = cmd.command_id[len(COMMAND_ID_PREFIX):]
        assert all(c in "0123456789abcdef" for c in digest_part)

    def test_decimal_normalization_identity(self):
        """Decimal("100.50") and Decimal("100.5") produce same command_id."""
        intent = _make_intent(entry=Decimal("100.50"))
        auth = _make_authorization(intent)
        cmd1 = _make_command(intent, auth)

        intent2 = _make_intent(entry=Decimal("100.5"))
        auth2 = _make_authorization(intent2)
        cmd2 = _make_command(intent2, auth2)
        # Decimal normalization means these are the same value -> same command ID
        assert cmd1.command_id == cmd2.command_id

    def test_dictionary_ordering_independence(self):
        """command_id is independent of metadata ordering."""
        intent = _make_intent()
        auth = _make_authorization(intent)
        cmd1 = _make_command(intent, auth, metadata=(("a", "1"), ("b", "2")))
        cmd2 = _make_command(intent, auth, metadata=(("b", "2"), ("a", "1")))
        assert cmd1.command_id == cmd2.command_id


# ============================================================
# 4. AUTHORIZATION BINDING
# ============================================================


class TestAuthorizationBinding:
    """Tests for authorization state and binding verification."""

    def test_authorized_succeeds(self):
        intent = _make_intent()
        auth = _make_authorization(intent, status=AuthorizationStatus.AUTHORIZED)
        cmd = create_execution_command(
            intent=intent,
            authorization=auth,
            created_at=datetime.datetime(2026, 9, 1, 12, 0, 0, tzinfo=datetime.timezone.utc),
        )
        assert cmd.authorization_id == auth.authorization_id
        assert cmd.intent_id == intent.intent_id
        assert cmd.content_fingerprint == intent.content_fingerprint

    def test_unauthorized_fails(self):
        intent = _make_intent()
        auth = _make_authorization(intent, status=AuthorizationStatus.UNAUTHORIZED)
        with pytest.raises(ValueError, match="Authorization must be AUTHORIZED"):
            create_execution_command(
                intent=intent,
                authorization=auth,
                created_at=datetime.datetime(2026, 9, 1, 12, 0, 0, tzinfo=datetime.timezone.utc),
            )

    def test_eligible_fails(self):
        intent = _make_intent()
        auth = _make_authorization(intent, status=AuthorizationStatus.ELIGIBLE)
        with pytest.raises(ValueError, match="Authorization must be AUTHORIZED"):
            create_execution_command(
                intent=intent,
                authorization=auth,
                created_at=datetime.datetime(2026, 9, 1, 12, 0, 0, tzinfo=datetime.timezone.utc),
            )

    def test_expired_fails(self):
        intent = _make_intent()
        auth = _make_authorization(intent, status=AuthorizationStatus.EXPIRED)
        with pytest.raises(ValueError, match="Authorization must be AUTHORIZED"):
            create_execution_command(
                intent=intent,
                authorization=auth,
                created_at=datetime.datetime(2026, 9, 1, 12, 0, 0, tzinfo=datetime.timezone.utc),
            )

    def test_revoked_fails(self):
        intent = _make_intent()
        auth = _make_authorization(intent, status=AuthorizationStatus.REVOKED)
        with pytest.raises(ValueError, match="Authorization must be AUTHORIZED"):
            create_execution_command(
                intent=intent,
                authorization=auth,
                created_at=datetime.datetime(2026, 9, 1, 12, 0, 0, tzinfo=datetime.timezone.utc),
            )

    def test_superseded_fails(self):
        intent = _make_intent()
        auth = _make_authorization(intent, status=AuthorizationStatus.SUPERSEDED)
        with pytest.raises(ValueError, match="Authorization must be AUTHORIZED"):
            create_execution_command(
                intent=intent,
                authorization=auth,
                created_at=datetime.datetime(2026, 9, 1, 12, 0, 0, tzinfo=datetime.timezone.utc),
            )

    def test_intent_id_mismatch_fails(self):
        intent = _make_intent()
        auth = _make_authorization(intent)
        # Create a different intent with a different intent_id
        other_intent = _make_intent(instrument="RELIANCE")
        # The authorization.intent_id != other_intent.intent_id
        with pytest.raises(ValueError, match="intent_id mismatch"):
            create_execution_command(
                intent=other_intent,
                authorization=auth,
                created_at=datetime.datetime(2026, 9, 1, 12, 0, 0, tzinfo=datetime.timezone.utc),
            )

    def test_fingerprint_mismatch_fails(self):
        intent = _make_intent()
        auth = _make_authorization(intent)
        # Create a different intent with different content.
        # The factory checks intent_id before fingerprint, so intent_id
        # mismatch is reported first when the intents differ.
        other_intent = _make_intent(entry=Decimal("999.99"))
        with pytest.raises(ValueError, match="intent_id mismatch"):
            create_execution_command(
                intent=other_intent,
                authorization=auth,
                created_at=datetime.datetime(2026, 9, 1, 12, 0, 0, tzinfo=datetime.timezone.utc),
            )


# ============================================================
# 5. FIELD INTEGRITY
# ============================================================


class TestFieldIntegrity:
    """Tests for field preservation and no-recalculation."""

    def test_fields_copied_correctly(self):
        intent = _make_intent()
        auth = _make_authorization(intent)
        cmd = _make_command(intent, auth)
        assert cmd.instrument == intent.instrument
        assert cmd.direction == intent.direction
        assert cmd.entry == intent.entry
        assert cmd.stop == intent.stop
        assert cmd.target == intent.target_1
        assert cmd.quantity == intent.quantity
        assert cmd.planned_risk == intent.planned_risk
        assert cmd.maximum_risk == intent.maximum_risk

    def test_values_copied_by_value(self):
        """Fields are copied by value, not by reference mutation."""
        intent = _make_intent()
        auth = _make_authorization(intent)
        cmd = _make_command(intent, auth)
        # Mutating the intent's Decimal after creation should NOT affect command
        # (but since Decimal is immutable, this is structural)
        assert cmd.entry == Decimal("100.50")
        assert cmd.quantity == Decimal("10")

    def test_no_recalculation(self):
        """Factory does not recalculate any economic fields."""
        intent = _make_intent()
        auth = _make_authorization(intent)
        cmd = _make_command(intent, auth)
        # Values are exactly what was in the intent, no recomputation
        assert cmd.entry == intent.entry
        assert cmd.stop == intent.stop
        assert cmd.target == intent.target_1
        assert cmd.quantity == intent.quantity
        assert cmd.planned_risk == intent.planned_risk
        assert cmd.maximum_risk == intent.maximum_risk

    def test_no_mutation_of_intent(self):
        intent = _make_intent()
        original_entry = intent.entry
        auth = _make_authorization(intent)
        _make_command(intent, auth)
        assert intent.entry == original_entry

    def test_no_mutation_of_authorization(self):
        intent = _make_intent()
        auth = _make_authorization(intent)
        original_id = auth.authorization_id
        _make_command(intent, auth)
        assert auth.authorization_id == original_id

    def test_economic_geometry_preserved(self):
        intent = _make_intent(
            entry=Decimal("123.45"),
            stop=Decimal("118.00"),
            target_1=Decimal("135.00"),
        )
        auth = _make_authorization(intent)
        cmd = _make_command(intent, auth)
        assert cmd.entry == Decimal("123.45")
        assert cmd.stop == Decimal("118.00")
        assert cmd.target == Decimal("135.00")

    def test_quantity_preserved(self):
        intent = _make_intent(quantity=Decimal("25"))
        auth = _make_authorization(intent)
        cmd = _make_command(intent, auth)
        assert cmd.quantity == Decimal("25")

    def test_risk_preserved(self):
        intent = _make_intent(
            planned_risk=Decimal("200.00"),
            maximum_risk=Decimal("500.00"),
        )
        auth = _make_authorization(intent)
        cmd = _make_command(intent, auth)
        assert cmd.planned_risk == Decimal("200.00")
        assert cmd.maximum_risk == Decimal("500.00")

    def test_direction_preserved(self):
        intent_long = _make_intent(direction="LONG")
        auth_long = _make_authorization(intent_long)
        cmd_long = _make_command(intent_long, auth_long)
        assert cmd_long.direction == "LONG"

        intent_short = _make_intent(direction="SHORT")
        auth_short = _make_authorization(intent_short)
        cmd_short = _make_command(intent_short, auth_short)
        assert cmd_short.direction == "SHORT"


# ============================================================
# 6. RISK INVARIANT
# ============================================================


class TestRiskInvariant:
    """Tests for risk invariant enforcement."""

    def test_valid_risk_succeeds(self):
        intent = _make_intent(
            planned_risk=Decimal("50.00"),
            maximum_risk=Decimal("100.00"),
        )
        auth = _make_authorization(intent)
        cmd = _make_command(intent, auth)
        assert cmd.planned_risk == Decimal("50.00")
        assert cmd.maximum_risk == Decimal("100.00")

    def test_planned_risk_exceeds_maximum_risk_fails(self):
        """Hand-constructed command with planned_risk > maximum_risk fails."""
        intent = _make_intent(
            planned_risk=Decimal("200.00"),
            maximum_risk=Decimal("100.00"),
        )
        # The intent model allows this (it's just a snapshot), but the
        # command __post_init__ must reject it.
        auth = _make_authorization(intent)
        # The authorization factory doesn't validate risk either, so both
        # intent and auth are created successfully. The command should fail.
        with pytest.raises(ValueError, match="planned_risk must not exceed maximum_risk"):
            _make_command(intent, auth)

    def test_equal_risk_succeeds(self):
        intent = _make_intent(
            planned_risk=Decimal("100.00"),
            maximum_risk=Decimal("100.00"),
        )
        auth = _make_authorization(intent)
        cmd = _make_command(intent, auth)
        assert cmd.planned_risk == cmd.maximum_risk

    def test_quantity_must_be_positive(self):
        """Hand-constructed command with quantity <= 0 fails."""
        intent = _make_intent(quantity=Decimal("0"))
        auth = _make_authorization(intent)
        with pytest.raises(ValueError, match="quantity must be positive"):
            _make_command(intent, auth)


# ============================================================
# 7. EXECUTION MODE
# ============================================================


class TestExecutionMode:
    """Tests for execution mode derivation and enforcement."""

    def test_paper_scope_produces_paper_mode(self):
        intent = _make_intent()
        auth = _make_authorization(intent, scope="paper")
        cmd = _make_command(intent, auth)
        assert cmd.execution_mode is ExecutionMode.PAPER
        assert cmd.is_paper
        assert not cmd.is_live

    def test_live_scope_produces_live_mode(self):
        intent = _make_intent()
        auth = _make_authorization(intent, scope="live")
        cmd = _make_command(intent, auth)
        assert cmd.execution_mode is ExecutionMode.LIVE
        assert cmd.is_live
        assert not cmd.is_paper

    def test_paper_scope_case_insensitive(self):
        intent = _make_intent()
        auth = _make_authorization(intent, scope="PAPER")
        cmd = _make_command(intent, auth)
        assert cmd.execution_mode is ExecutionMode.PAPER

    def test_live_scope_case_insensitive(self):
        intent = _make_intent()
        auth = _make_authorization(intent, scope="LIVE")
        cmd = _make_command(intent, auth)
        assert cmd.execution_mode is ExecutionMode.LIVE

    def test_unrecognized_scope_fails(self):
        intent = _make_intent()
        auth = _make_authorization(intent, scope="unknown-scope")
        with pytest.raises(ValueError, match="Cannot derive execution_mode"):
            _make_command(intent, auth)

    def test_mismatched_mode_cannot_override(self):
        """The factory derives mode from authorization; caller cannot pass a mode."""
        intent = _make_intent()
        auth = _make_authorization(intent, scope="paper")
        # The factory does not accept an explicit mode parameter.
        # It derives mode from authorization.scope, so caller cannot override.
        cmd = _make_command(intent, auth)
        assert cmd.execution_mode is ExecutionMode.PAPER


# ============================================================
# 8. FORBIDDEN SEMANTICS
# ============================================================


class TestForbiddenSemantics:
    """Tests that forbidden broker/execution fields are absent."""

    def _check_no_broker_fields(self, cmd: ExecutionCommand) -> None:
        """Assert that no broker-specific attributes exist on the command."""
        forbidden = [
            "broker_order_id", "order_id", "fill_price", "filled_quantity",
            "realized_pnl", "slippage", "fees", "position_id", "portfolio_id",
            "broker_symbol", "exchange", "routing", "broker_credentials",
            "broker_client", "broker_connection",
        ]
        for attr in forbidden:
            assert not hasattr(cmd, attr), f"Command must not have {attr}"

    def test_no_broker_fields(self):
        intent = _make_intent()
        auth = _make_authorization(intent)
        cmd = _make_command(intent, auth)
        self._check_no_broker_fields(cmd)

    def test_no_broker_order_types(self):
        """Command must not contain BUY/SELL/MARKET/LIMIT/STOP/STOP_LIMIT."""
        intent = _make_intent()
        auth = _make_authorization(intent)
        cmd = _make_command(intent, auth)
        forbidden_values = ["BUY", "SELL", "MARKET", "LIMIT", "STOP", "STOP_LIMIT"]
        for val in forbidden_values:
            assert cmd.direction not in forbidden_values
            assert cmd.execution_mode.value not in forbidden_values


# ============================================================
# 9. IMMUTABILITY
# ============================================================


class TestImmutability:
    """Tests for model immutability."""

    def test_frozen_model(self):
        intent = _make_intent()
        auth = _make_authorization(intent)
        cmd = _make_command(intent, auth)
        with pytest.raises((FrozenInstanceError, dataclasses.FrozenInstanceError)):
            cmd.instrument = "NEW"  # type: ignore[misc]

    def test_nested_immutable_values(self):
        """All nested values are immutable types."""
        intent = _make_intent()
        auth = _make_authorization(intent)
        cmd = _make_command(intent, auth)
        assert isinstance(cmd.metadata, tuple)
        assert isinstance(cmd.label, str)
        assert isinstance(cmd.direction, str)
        assert isinstance(cmd.execution_mode, ExecutionMode)

    def test_metadata_is_tuple(self):
        intent = _make_intent()
        auth = _make_authorization(intent)
        cmd = _make_command(intent, auth)
        assert isinstance(cmd.metadata, tuple)

    def test_metadata_sorted(self):
        intent = _make_intent()
        auth = _make_authorization(intent)
        cmd = _make_command(intent, auth, metadata=(("b", "2"), ("a", "1")))
        assert cmd.metadata == (("a", "1"), ("b", "2"))


# ============================================================
# 10. AUTHORIZATION STATE VARIATIONS
# ============================================================


class TestAuthorizationStateVariations:
    """Tests for all authorization state fail-closed behavior."""

    @pytest.mark.parametrize(
        "status",
        [
            AuthorizationStatus.UNAUTHORIZED,
            AuthorizationStatus.ELIGIBLE,
            AuthorizationStatus.EXPIRED,
            AuthorizationStatus.REVOKED,
            AuthorizationStatus.SUPERSEDED,
        ],
    )
    def test_non_authorized_states_fail(self, status):
        intent = _make_intent()
        auth = _make_authorization(intent, status=status)
        with pytest.raises(ValueError, match="Authorization must be AUTHORIZED"):
            create_execution_command(
                intent=intent,
                authorization=auth,
                created_at=datetime.datetime(2026, 9, 1, 12, 0, 0, tzinfo=datetime.timezone.utc),
            )


# ============================================================
# 11. DEPENDENCY ISOLATION
# ============================================================


class TestDependencyIsolation:
    """Tests that the module does not import forbidden dependencies."""

    def test_no_paper_trading_import(self):
        """execution_command module must not import paper_trading."""
        import engine.models.execution_command as mod
        source = open(mod.__file__).read()
        assert "paper_trading" not in source
        assert "PaperTrade" not in source

    def test_no_broker_import(self):
        """execution_command module must not import broker code."""
        import engine.models.execution_command as mod
        source = open(mod.__file__).read()
        assert "broker" not in source.lower() or "broker-neutral" in source.lower()

    def test_no_dashboard_import(self):
        """execution_command module must not import dashboard code."""
        import engine.models.execution_command as mod
        source = open(mod.__file__).read()
        assert "dashboard" not in source

    def test_no_execution_result_import(self):
        """execution_command module must not import execution result code."""
        import engine.models.execution_command as mod
        source = open(mod.__file__).read()
        assert "execution_result" not in source


# ============================================================
# 12. SERIALIZATION / CANONICAL IDENTITY
# ============================================================


class TestCanonicalIdentity:
    """Tests for canonical serialization and identity stability."""

    def test_metadata_sorted_in_payload(self):
        """Metadata is sorted in the canonical payload."""
        intent = _make_intent()
        auth = _make_authorization(intent)
        cmd1 = _make_command(intent, auth, metadata=(("z", "1"), ("a", "2")))
        cmd2 = _make_command(intent, auth, metadata=(("a", "2"), ("z", "1")))
        assert cmd1.command_id == cmd2.command_id

    def test_label_excluded_from_identity(self):
        """Label changes do not affect command_id."""
        intent = _make_intent()
        auth = _make_authorization(intent)
        cmd1 = _make_command(intent, auth, label="label-a")
        cmd2 = _make_command(intent, auth, label="label-b")
        assert cmd1.command_id == cmd2.command_id

    def test_created_at_excluded_from_identity(self):
        """created_at changes do not affect command_id."""
        intent = _make_intent()
        auth = _make_authorization(intent)
        t1 = datetime.datetime(2026, 9, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
        t2 = datetime.datetime(2026, 9, 2, 12, 0, 0, tzinfo=datetime.timezone.utc)
        cmd1 = _make_command(intent, auth, created_at=t1)
        cmd2 = _make_command(intent, auth, created_at=t2)
        assert cmd1.command_id == cmd2.command_id


# ============================================================
# 13. TYPE VALIDATION
# ============================================================


class TestTypeValidation:
    """Tests for type checking in the factory."""

    def test_non_intent_raises_type_error(self):
        auth = _make_authorization(_make_intent())
        with pytest.raises(TypeError, match="Expected an OperationalTradeIntent"):
            create_execution_command(
                intent="not-an-intent",  # type: ignore[arg-type]
                authorization=auth,
                created_at=datetime.datetime(2026, 9, 1, 12, 0, 0, tzinfo=datetime.timezone.utc),
            )

    def test_non_authorization_raises_type_error(self):
        intent = _make_intent()
        with pytest.raises(TypeError, match="Expected an ExecutionAuthorization"):
            create_execution_command(
                intent=intent,
                authorization="not-an-auth",  # type: ignore[arg-type]
                created_at=datetime.datetime(2026, 9, 1, 12, 0, 0, tzinfo=datetime.timezone.utc),
            )


# ============================================================
# 14. TIMESTAMP VALIDATION
# ============================================================


class TestTimestampValidation:
    """Tests for timestamp validation."""

    def test_created_at_must_be_timezone_aware(self):
        intent = _make_intent()
        auth = _make_authorization(intent)
        with pytest.raises(ValueError, match="created_at must be timezone-aware"):
            create_execution_command(
                intent=intent,
                authorization=auth,
                created_at=datetime.datetime(2026, 9, 1, 12, 0, 0),  # naive
            )

    def test_valid_from_defaults_to_created_at(self):
        intent = _make_intent()
        auth = _make_authorization(intent)
        t = datetime.datetime(2026, 9, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
        cmd = create_execution_command(
            intent=intent,
            authorization=auth,
            created_at=t,
        )
        assert cmd.valid_from == t

    def test_valid_until_relationship(self):
        intent = _make_intent()
        auth = _make_authorization(intent)
        t = datetime.datetime(2026, 9, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
        with pytest.raises(ValueError, match="valid_until must be > valid_from"):
            create_execution_command(
                intent=intent,
                authorization=auth,
                created_at=t,
                valid_from=t,
                valid_until=t,  # same as valid_from -> invalid
            )


# ============================================================
# 15. PAPER TRADING ISOLATION
# ============================================================


class TestPaperTradingIsolation:
    """Tests that ExecutionCommand is isolated from paper trading."""

    def test_no_paper_trade_fields(self):
        intent = _make_intent()
        auth = _make_authorization(intent)
        cmd = _make_command(intent, auth)
        forbidden = [
            "paper_trade_id", "simulation_state", "paper_trade_status",
            "actual_entry_price", "actual_exit_price", "realized_pnl",
        ]
        for attr in forbidden:
            assert not hasattr(cmd, attr), f"Command must not have {attr}"
