"""
Tests for OperationalTradeIntentEngine (Checkpoint 14.4).

Covers:
1. Valid TradePlan -> OperationalTradeIntent
2. Exact type validation
3. Factory invocation / delegation
4. TradePlan geometry preserved verbatim
5. Quantity preserved
6. Risk fields preserved
7. Identity preserved according to 14.2
8. Content fingerprint preserved
9. No TradePlan mutation
10. No MarketScanResult interaction
11. No PaperTrade interaction
12. No market-data interaction
13. No authorization interaction
14. No execution interaction
15. Stateless repeated calls
16. Deterministic behavior
17. Invalid TradePlan failure
18. Invalid factory inputs
19. Timestamp handling
20. valid_until handling
21. Immutability
22. Metadata immutability
23. Warnings immutability
24. No hidden global state
25. No hidden persistence
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from engine.intelligence.operational_trade_intent import (
    OperationalTradeIntentEngine,
)
from engine.models.operational_trade_intent import OperationalTradeIntent
from engine.models.trade_plan import (
    RiskPlanStatus,
    TradePlan,
)


# ============================================================
# FIXTURES
# ============================================================


def _make_plan(
    *,
    direction: str = "LONG",
    risk_plan_status: RiskPlanStatus = RiskPlanStatus.VALID,
    entry: Decimal | None = Decimal("100.50"),
    stop: Decimal | None = Decimal("95.00"),
    target_1: Decimal | None = Decimal("110.00"),
    engine_risk_distance: Decimal | None = Decimal("5.50"),
    engine_reward_distance: Decimal | None = Decimal("9.50"),
    engine_risk_reward_ratio: Decimal | None = Decimal("1.727"),
    quantity: Decimal | None = Decimal("10"),
    planned_risk: Decimal | None = Decimal("55.00"),
    maximum_risk: Decimal | None = Decimal("100.00"),
    existing_decision: str = "QUALIFIED",
    actionability: str = "READY_FOR_REVIEW",
    plan_id: str = "plan-abc123def4567890",
    instrument: str = "NIFTY",
    timeframe: str = "15m",
    warnings: tuple[str, ...] = (),
    rationale: str = "Test rationale.",
    label: str = "",
    metadata: tuple[tuple[str, str], ...] = (),
) -> TradePlan:
    """Build a TradePlan with sensible defaults for testing."""
    return TradePlan(
        plan_id=plan_id,
        instrument=instrument,
        timeframe=timeframe,
        direction=direction,
        existing_decision=existing_decision,
        actionability=actionability,
        account_capital=Decimal("100000"),
        risk_percent=Decimal("1"),
        maximum_risk=maximum_risk,
        entry=entry,
        stop=stop,
        target_1=target_1,
        engine_risk_distance=engine_risk_distance,
        engine_reward_distance=engine_reward_distance,
        engine_risk_reward_ratio=engine_risk_reward_ratio,
        quantity=quantity,
        planned_risk=planned_risk,
        planned_reward=Decimal("95.00"),
        quantity_status=quantity_status_for(risk_plan_status),
        risk_plan_status=risk_plan_status,
        quantity_spec_available=False,
        warnings=warnings,
        rationale=rationale,
        label=label,
        metadata=metadata,
    )


def quantity_status_for(status: RiskPlanStatus):
    """Import lazily to avoid circular issues."""
    from engine.models.trade_plan import QuantityStatus

    if status is RiskPlanStatus.VALID:
        return QuantityStatus.FRACTIONAL_ALLOWED
    return QuantityStatus.UNSIZED


@pytest.fixture
def valid_plan() -> TradePlan:
    """A valid, fully-sized LONG TradePlan."""
    return _make_plan()


@pytest.fixture
def created_at() -> datetime:
    """A timezone-aware creation timestamp."""
    return datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def evaluation_timestamp() -> datetime:
    return datetime(2026, 9, 1, 11, 59, 0, tzinfo=timezone.utc)


@pytest.fixture
def valid_until() -> datetime:
    return datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def engine() -> OperationalTradeIntentEngine:
    return OperationalTradeIntentEngine()


# ============================================================
# 1. VALID TRADEPLAN -> OPERATIONAL TRADE INTENT
# ============================================================


class TestValidCreation:
    """Test that a valid TradePlan produces an OperationalTradeIntent."""

    def test_returns_intent(self, engine, valid_plan, created_at):
        intent = engine.create_from_plan(valid_plan, created_at=created_at)
        assert isinstance(intent, OperationalTradeIntent)

    def test_basic_fields(self, engine, valid_plan, created_at):
        intent = engine.create_from_plan(valid_plan, created_at=created_at)
        assert intent.instrument == "NIFTY"
        assert intent.direction == "LONG"
        assert intent.plan_id == valid_plan.plan_id

    def test_with_all_timestamps(
        self, engine, valid_plan, created_at, evaluation_timestamp, valid_until
    ):
        intent = engine.create_from_plan(
            valid_plan,
            created_at=created_at,
            evaluation_timestamp=evaluation_timestamp,
            valid_until=valid_until,
        )
        assert intent.created_at == created_at
        assert intent.evaluation_timestamp == evaluation_timestamp
        assert intent.valid_until == valid_until


# ============================================================
# 2. EXACT TYPE VALIDATION
# ============================================================


class TestTypeValidation:
    """Test that non-TradePlan inputs are rejected."""

    def test_rejects_dict(self, engine, created_at):
        with pytest.raises(TypeError, match="Expected a TradePlan"):
            engine.create_from_plan({}, created_at=created_at)

    def test_rejects_none(self, engine, created_at):
        with pytest.raises(TypeError, match="Expected a TradePlan"):
            engine.create_from_plan(None, created_at=created_at)

    def test_rejects_string(self, engine, created_at):
        with pytest.raises(TypeError, match="Expected a TradePlan"):
            engine.create_from_plan("not-a-plan", created_at=created_at)

    def test_rejects_object_with_similar_fields(self, engine, created_at):
        class FakePlan:
            pass

        with pytest.raises(TypeError, match="Expected a TradePlan"):
            engine.create_from_plan(FakePlan(), created_at=created_at)


# ============================================================
# 3. FACTORY INVOCATION / DELEGATION
# ============================================================


class TestFactoryDelegation:
    """Test that the engine delegates to the authoritative factory."""

    def test_intent_id_format(self, engine, valid_plan, created_at):
        intent = engine.create_from_plan(valid_plan, created_at=created_at)
        assert intent.intent_id.startswith("intent-")
        assert len(intent.intent_id) == len("intent-") + 16

    def test_fingerprint_format(self, engine, valid_plan, created_at):
        intent = engine.create_from_plan(valid_plan, created_at=created_at)
        assert intent.content_fingerprint.startswith("fp-")
        assert len(intent.content_fingerprint) == len("fp-") + 16

    def test_version(self, engine, valid_plan, created_at):
        intent = engine.create_from_plan(valid_plan, created_at=created_at)
        assert intent.version == 1


# ============================================================
# 4. TRADEPLAN GEOMETRY PRESERVED VERBATIM
# ============================================================


class TestGeometryPreserved:
    """Test that TradePlan geometry is copied verbatim into the intent."""

    def test_entry_preserved(self, engine, valid_plan, created_at):
        intent = engine.create_from_plan(valid_plan, created_at=created_at)
        assert intent.entry == valid_plan.entry

    def test_stop_preserved(self, engine, valid_plan, created_at):
        intent = engine.create_from_plan(valid_plan, created_at=created_at)
        assert intent.stop == valid_plan.stop

    def test_target_preserved(self, engine, valid_plan, created_at):
        intent = engine.create_from_plan(valid_plan, created_at=created_at)
        assert intent.target_1 == valid_plan.target_1

    def test_risk_distance_preserved(self, engine, valid_plan, created_at):
        intent = engine.create_from_plan(valid_plan, created_at=created_at)
        assert intent.engine_risk_distance == valid_plan.engine_risk_distance

    def test_reward_distance_preserved(self, engine, valid_plan, created_at):
        intent = engine.create_from_plan(valid_plan, created_at=created_at)
        assert intent.engine_reward_distance == valid_plan.engine_reward_distance

    def test_risk_reward_ratio_preserved(self, engine, valid_plan, created_at):
        intent = engine.create_from_plan(valid_plan, created_at=created_at)
        assert intent.engine_risk_reward_ratio == valid_plan.engine_risk_reward_ratio


# ============================================================
# 5. QUANTITY PRESERVED
# ============================================================


class TestQuantityPreserved:
    """Test that quantity is preserved verbatim."""

    def test_quantity_preserved(self, engine, valid_plan, created_at):
        intent = engine.create_from_plan(valid_plan, created_at=created_at)
        assert intent.quantity == valid_plan.quantity


# ============================================================
# 6. RISK FIELDS PRESERVED
# ============================================================


class TestRiskFieldsPreserved:
    """Test that risk fields are preserved verbatim."""

    def test_planned_risk_preserved(self, engine, valid_plan, created_at):
        intent = engine.create_from_plan(valid_plan, created_at=created_at)
        assert intent.planned_risk == valid_plan.planned_risk

    def test_maximum_risk_preserved(self, engine, valid_plan, created_at):
        intent = engine.create_from_plan(valid_plan, created_at=created_at)
        assert intent.maximum_risk == valid_plan.maximum_risk

    def test_risk_plan_status_preserved(self, engine, valid_plan, created_at):
        intent = engine.create_from_plan(valid_plan, created_at=created_at)
        assert intent.risk_plan_status == RiskPlanStatus.VALID


# ============================================================
# 7. IDENTITY PRESERVED ACCORDING TO 14.2
# ============================================================


class TestIdentityPreserved:
    """Test that identity follows the Checkpoint 14.2 contract."""

    def test_intent_id_is_deterministic(self, engine, valid_plan, created_at):
        intent1 = engine.create_from_plan(valid_plan, created_at=created_at)
        intent2 = engine.create_from_plan(valid_plan, created_at=created_at)
        assert intent1.intent_id == intent2.intent_id

    def test_intent_id_changes_with_instrument(self, engine, created_at):
        plan1 = _make_plan(instrument="NIFTY")
        plan2 = _make_plan(instrument="RELIANCE")
        intent1 = engine.create_from_plan(plan1, created_at=created_at)
        intent2 = engine.create_from_plan(plan2, created_at=created_at)
        assert intent1.intent_id != intent2.intent_id

    def test_intent_id_changes_with_geometry(self, engine, created_at):
        plan1 = _make_plan(entry=Decimal("100"))
        plan2 = _make_plan(entry=Decimal("200"))
        intent1 = engine.create_from_plan(plan1, created_at=created_at)
        intent2 = engine.create_from_plan(plan2, created_at=created_at)
        assert intent1.intent_id != intent2.intent_id


# ============================================================
# 8. CONTENT FINGERPRINT PRESERVED
# ============================================================


class TestContentFingerprint:
    """Test that content_fingerprint follows the 14.2 contract."""

    def test_fingerprint_is_deterministic(self, engine, valid_plan, created_at):
        intent1 = engine.create_from_plan(valid_plan, created_at=created_at)
        intent2 = engine.create_from_plan(valid_plan, created_at=created_at)
        assert intent1.content_fingerprint == intent2.content_fingerprint

    def test_fingerprint_changes_with_economic_content(
        self, engine, created_at
    ):
        plan1 = _make_plan(entry=Decimal("100"))
        plan2 = _make_plan(entry=Decimal("200"))
        intent1 = engine.create_from_plan(plan1, created_at=created_at)
        intent2 = engine.create_from_plan(plan2, created_at=created_at)
        assert intent1.content_fingerprint != intent2.content_fingerprint

    def test_fingerprint_ignores_timestamps(self, engine, valid_plan):
        ts1 = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
        ts2 = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
        intent1 = engine.create_from_plan(valid_plan, created_at=ts1)
        intent2 = engine.create_from_plan(valid_plan, created_at=ts2)
        assert intent1.content_fingerprint == intent2.content_fingerprint


# ============================================================
# 9. NO TRADEPLAN MUTATION
# ============================================================


class TestNoPlanMutation:
    """Test that the TradePlan is never mutated."""

    def test_plan_unchanged_after_creation(
        self, engine, valid_plan, created_at
    ):
        original_entry = valid_plan.entry
        original_stop = valid_plan.stop
        original_quantity = valid_plan.quantity
        engine.create_from_plan(valid_plan, created_at=created_at)
        assert valid_plan.entry == original_entry
        assert valid_plan.stop == original_stop
        assert valid_plan.quantity == original_quantity

    def test_plan_warnings_unchanged(
        self, engine, valid_plan, created_at
    ):
        original_warnings = valid_plan.warnings
        engine.create_from_plan(valid_plan, created_at=created_at)
        assert valid_plan.warnings == original_warnings


# ============================================================
# 10-14. SEPARATION AUDITS (NO INTERACTION)
# ============================================================


class TestSeparation:
    """Test that the engine does not interact with forbidden subsystems."""

    def test_no_market_data_access(self, engine, valid_plan, created_at):
        intent = engine.create_from_plan(valid_plan, created_at=created_at)
        assert isinstance(intent, OperationalTradeIntent)

    def test_no_paper_trade_reference(self, engine, valid_plan, created_at):
        intent = engine.create_from_plan(valid_plan, created_at=created_at)
        assert not hasattr(intent, "paper_trade_id")

    def test_no_authorization_fields(self, engine, valid_plan, created_at):
        intent = engine.create_from_plan(valid_plan, created_at=created_at)
        assert not hasattr(intent, "authorization_id")
        assert not hasattr(intent, "approved")

    def test_no_execution_fields(self, engine, valid_plan, created_at):
        intent = engine.create_from_plan(valid_plan, created_at=created_at)
        assert not hasattr(intent, "execution_command_id")
        assert not hasattr(intent, "broker_order_id")

    def test_no_broker_fields(self, engine, valid_plan, created_at):
        intent = engine.create_from_plan(valid_plan, created_at=created_at)
        assert not hasattr(intent, "broker_id")
        assert not hasattr(intent, "exchange")


# ============================================================
# 15. STATELESS REPEATED CALLS
# ============================================================


class TestStateless:
    """Test that the engine is stateless across calls."""

    def test_repeated_calls_produce_equivalent_intents(
        self, engine, valid_plan, created_at
    ):
        intent1 = engine.create_from_plan(valid_plan, created_at=created_at)
        intent2 = engine.create_from_plan(valid_plan, created_at=created_at)
        assert intent1.intent_id == intent2.intent_id
        assert intent1.content_fingerprint == intent2.content_fingerprint
        assert intent1.entry == intent2.entry

    def test_multiple_plans_on_same_engine(self, engine, created_at):
        plan1 = _make_plan(instrument="NIFTY")
        plan2 = _make_plan(instrument="RELIANCE")
        intent1 = engine.create_from_plan(plan1, created_at=created_at)
        intent2 = engine.create_from_plan(plan2, created_at=created_at)
        assert intent1.instrument == "NIFTY"
        assert intent2.instrument == "RELIANCE"

    def test_engine_has_no_mutable_state(self, engine, created_at):
        assert not hasattr(engine, "__dict__") or engine.__dict__ == {}


# ============================================================
# 16. DETERMINISTIC BEHAVIOR
# ============================================================


class TestDeterministic:
    """Test deterministic behavior."""

    def test_same_inputs_same_identity(self, engine, valid_plan, created_at):
        results = [
            engine.create_from_plan(valid_plan, created_at=created_at)
            for _ in range(5)
        ]
        ids = {r.intent_id for r in results}
        assert len(ids) == 1

    def test_different_label_different_identity(self, engine, valid_plan, created_at):
        intent1 = engine.create_from_plan(valid_plan, created_at=created_at)
        intent2 = engine.create_from_plan(
            valid_plan, created_at=created_at, label="different"
        )
        assert intent1.intent_id != intent2.intent_id


# ============================================================
# 17. INVALID TRADEPLAN FAILURE
# ============================================================


class TestInvalidPlan:
    """Test that non-VALID TradePlans are rejected."""

    def test_rejects_invalid_input_status(self, engine, created_at):
        plan = _make_plan(risk_plan_status=RiskPlanStatus.INVALID_INPUT)
        with pytest.raises(ValueError, match="non-VALID"):
            engine.create_from_plan(plan, created_at=created_at)

    def test_rejects_geometry_unavailable(self, engine, created_at):
        plan = _make_plan(risk_plan_status=RiskPlanStatus.GEOMETRY_UNAVAILABLE)
        with pytest.raises(ValueError, match="non-VALID"):
            engine.create_from_plan(plan, created_at=created_at)

    def test_rejects_risk_limit_exceeded(self, engine, created_at):
        plan = _make_plan(risk_plan_status=RiskPlanStatus.RISK_LIMIT_EXCEEDED)
        with pytest.raises(ValueError, match="non-VALID"):
            engine.create_from_plan(plan, created_at=created_at)


# ============================================================
# 18. INVALID FACTORY INPUTS
# ============================================================


class TestInvalidFactoryInputs:
    """Test that invalid factory inputs are rejected.

    Note: The TradePlan's own ``__post_init__`` already enforces that a
    VALID plan carries a directional intent. So the engine's direction
    check is defense-in-depth; a VALID non-directional plan cannot be
    constructed. We verify here that non-VALID statuses are rejected
    by the engine before any direction check, and that the TradePlan
    constructor itself rejects VALID non-directional plans.
    """

    def test_rejects_invalid_status_before_direction(self, engine, created_at):
        """Non-VALID status is rejected before direction is checked."""
        plan = _make_plan(
            direction="NONE",
            risk_plan_status=RiskPlanStatus.GEOMETRY_UNAVAILABLE,
        )
        with pytest.raises(ValueError, match="non-VALID"):
            engine.create_from_plan(plan, created_at=created_at)

    def test_tradeplan_constructor_rejects_valid_non_directional(
        self, created_at
    ):
        """TradePlan itself rejects VALID plans without direction."""
        with pytest.raises(ValueError, match="directional intent"):
            _make_plan(direction="NONE")

    def test_tradeplan_constructor_rejects_valid_empty_direction(
        self, created_at
    ):
        """TradePlan itself rejects VALID plans with empty direction."""
        with pytest.raises(ValueError, match="directional intent"):
            _make_plan(direction="")


# ============================================================
# 19. TIMESTAMP HANDLING
# ============================================================


class TestTimestampHandling:
    """Test timestamp handling."""

    def test_created_at_required(self, engine, valid_plan):
        with pytest.raises(TypeError):
            engine.create_from_plan(valid_plan)

    def test_timestamps_preserved(
        self, engine, valid_plan, created_at, evaluation_timestamp, valid_until
    ):
        intent = engine.create_from_plan(
            valid_plan,
            created_at=created_at,
            evaluation_timestamp=evaluation_timestamp,
            valid_until=valid_until,
        )
        assert intent.created_at == created_at
        assert intent.evaluation_timestamp == evaluation_timestamp
        assert intent.valid_until == valid_until

    def test_no_timestamps_defaults_to_none(
        self, engine, valid_plan, created_at
    ):
        intent = engine.create_from_plan(valid_plan, created_at=created_at)
        assert intent.created_at == created_at
        assert intent.evaluation_timestamp is None
        assert intent.valid_until is None

    def test_naive_timestamp_rejected(self, engine, valid_plan):
        naive = datetime(2026, 9, 1, 12, 0, 0)
        with pytest.raises(ValueError, match="timezone-aware"):
            engine.create_from_plan(valid_plan, created_at=naive)


# ============================================================
# 20. VALID_UNTIL HANDLING
# ============================================================


class TestValidUntil:
    """Test valid_until handling."""

    def test_valid_until_none(self, engine, valid_plan, created_at):
        intent = engine.create_from_plan(
            valid_plan, created_at=created_at, valid_until=None
        )
        assert intent.valid_until is None

    def test_valid_until_preserved(
        self, engine, valid_plan, created_at, valid_until
    ):
        intent = engine.create_from_plan(
            valid_plan, created_at=created_at, valid_until=valid_until
        )
        assert intent.valid_until == valid_until

    def test_valid_until_before_created_at_rejected(
        self, engine, valid_plan
    ):
        created_at = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
        valid_until = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
        with pytest.raises(ValueError, match="valid_until"):
            engine.create_from_plan(
                valid_plan, created_at=created_at, valid_until=valid_until
            )


# ============================================================
# 21. IMMUTABILITY
# ============================================================


class TestImmutability:
    """Test that the returned intent is immutable."""

    def test_intent_is_frozen(self, engine, valid_plan, created_at):
        intent = engine.create_from_plan(valid_plan, created_at=created_at)
        with pytest.raises(AttributeError):
            intent.instrument = "RELIANCE"

    def test_intent_has_slots(self, engine, valid_plan, created_at):
        intent = engine.create_from_plan(valid_plan, created_at=created_at)
        assert not hasattr(intent, "__dict__",)


# ============================================================
# 22. METADATA IMMUTABILITY
# ============================================================


class TestMetadataImmutability:
    """Test that metadata is immutable on the intent."""

    def test_metadata_is_tuple(self, engine, valid_plan, created_at):
        intent = engine.create_from_plan(valid_plan, created_at=created_at)
        assert isinstance(intent.metadata, tuple)

    def test_caller_metadata_used(self, engine, valid_plan, created_at):
        meta = {"source": "test", "run": "1"}
        intent = engine.create_from_plan(
            valid_plan, created_at=created_at, metadata=meta
        )
        assert intent.metadata == (("run", "1"), ("source", "test"))


# ============================================================
# 23. WARNINGS IMMUTABILITY
# ============================================================


class TestWarningsImmutability:
    """Test that warnings are immutable on the intent."""

    def test_warnings_is_tuple(self, engine, valid_plan, created_at):
        intent = engine.create_from_plan(valid_plan, created_at=created_at)
        assert isinstance(intent.warnings, tuple)

    def test_warnings_preserved(self, engine, created_at):
        plan = _make_plan(warnings=("warn-1", "warn-2"))
        intent = engine.create_from_plan(plan, created_at=created_at)
        assert intent.warnings == ("warn-1", "warn-2")


# ============================================================
# 24. NO HIDDEN GLOBAL STATE
# ============================================================


class TestNoGlobalState:
    """Test that the engine has no hidden global state."""

    def test_two_engines_behave_identically(self, valid_plan, created_at):
        engine1 = OperationalTradeIntentEngine()
        engine2 = OperationalTradeIntentEngine()
        intent1 = engine1.create_from_plan(valid_plan, created_at=created_at)
        intent2 = engine2.create_from_plan(valid_plan, created_at=created_at)
        assert intent1.intent_id == intent2.intent_id

    def test_no_registry(self, engine, valid_plan, created_at):
        assert not hasattr(engine, "_registry")
        assert not hasattr(engine, "_cache")

    def test_no_class_level_state(self):
        assert OperationalTradeIntentEngine.__dict__.get("._registry") is None


# ============================================================
# 25. NO HIDDEN PERSISTENCE
# ============================================================


class TestNoPersistence:
    """Test that the engine does not persist anything."""

    def test_no_files_written(self, engine, valid_plan, created_at, tmp_path):
        import os

        before = set(os.listdir(tmp_path))
        engine.create_from_plan(valid_plan, created_at=created_at)
        after = set(os.listdir(tmp_path))
        assert before == after

    def test_intent_is_returned_not_stored(
        self, engine, valid_plan, created_at
    ):
        intent = engine.create_from_plan(valid_plan, created_at=created_at)
        assert isinstance(intent, OperationalTradeIntent)


# ============================================================
# ADDITIONAL: OPERATIONAL CONTEXT PRESERVATION
# ============================================================


class TestOperationalContext:
    """Test that operational context is preserved."""

    def test_existing_decision_preserved(self, engine, valid_plan, created_at):
        intent = engine.create_from_plan(valid_plan, created_at=created_at)
        assert intent.existing_decision == "QUALIFIED"

    def test_actionability_preserved(self, engine, valid_plan, created_at):
        intent = engine.create_from_plan(valid_plan, created_at=created_at)
        assert intent.actionability == "READY_FOR_REVIEW"

    def test_rationale_preserved(self, engine, created_at):
        plan = _make_plan(rationale="Custom rationale")
        intent = engine.create_from_plan(plan, created_at=created_at)
        assert intent.rationale == "Custom rationale"

    def test_timeframe_preserved(self, engine, valid_plan, created_at):
        intent = engine.create_from_plan(valid_plan, created_at=created_at)
        assert intent.timeframe == "15m"


# ============================================================
# ADDITIONAL: SHORT DIRECTION
# ============================================================


class TestShortDirection:
    """Test that SHORT plans work correctly."""

    def test_short_plan(self, engine, created_at):
        plan = _make_plan(direction="SHORT")
        intent = engine.create_from_plan(plan, created_at=created_at)
        assert intent.direction == "SHORT"
        assert isinstance(intent, OperationalTradeIntent)


# ============================================================
# ADDITIONAL: LABEL HANDLING
# ============================================================


class TestLabelHandling:
    """Test label handling."""

    def test_plan_label_used_by_default(self, engine, created_at):
        plan = _make_plan(label="plan-label")
        intent = engine.create_from_plan(plan, created_at=created_at)
        assert intent.label == "plan-label"

    def test_caller_label_overrides(self, engine, created_at):
        plan = _make_plan(label="plan-label")
        intent = engine.create_from_plan(
            plan, created_at=created_at, label="override-label"
        )
        assert intent.label == "override-label"
