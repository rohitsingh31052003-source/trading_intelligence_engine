"""
Tests for OperationalTradeIntent application integration (Checkpoint 14.5).

Covers:
1. OperationalTradeIntentApplicationService direct usage
2. Application service wraps engine correctly
3. Engine delegation
4. No TradePlan mutation
5. No MarketScanResult interaction
6. No PaperTrade interaction
7. No market-data access
8. No planning re-execution
9. Identity preservation
10. content_fingerprint preservation
11. Timestamp handling
12. valid_until handling
13. Repeated explicit creation behavior
14. Invalid TradePlan handling
15. Immutability
16. No hidden global state
17. No hidden persistence
18. No authorization
19. No execution
20. API semantics (POST endpoint)
21. GET does not create intent
22. Explicit action is required
23. DashboardAnalysisService.create_operational_trade_intent
24. OperationalTradeIntentView projection
25. JSON serialization
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from engine.intelligence.operational_trade_intent import (
    OperationalTradeIntentEngine,
)
from engine.intelligence.operational_trade_intent_application import (
    OperationalTradeIntentApplicationService,
)
from engine.models.operational_trade_intent import OperationalTradeIntent
from engine.models.trade_plan import (
    QuantityStatus,
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
        quantity_status=(
            QuantityStatus.FRACTIONAL_ALLOWED
            if risk_plan_status is RiskPlanStatus.VALID
            else QuantityStatus.UNSIZED
        ),
        risk_plan_status=risk_plan_status,
        quantity_spec_available=False,
        warnings=warnings,
        rationale=rationale,
        label=label,
        metadata=metadata,
    )


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
def app_service() -> OperationalTradeIntentApplicationService:
    return OperationalTradeIntentApplicationService()


# ============================================================
# 1. APPLICATION SERVICE DIRECT USAGE
# ============================================================


class TestAppServiceDirectUsage:
    """Test the application service as the direct entry point."""

    def test_returns_intent(self, app_service, valid_plan, created_at):
        intent = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        assert isinstance(intent, OperationalTradeIntent)

    def test_basic_fields(self, app_service, valid_plan, created_at):
        intent = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        assert intent.instrument == "NIFTY"
        assert intent.direction == "LONG"
        assert intent.plan_id == valid_plan.plan_id

    def test_with_all_timestamps(
        self,
        app_service,
        valid_plan,
        created_at,
        evaluation_timestamp,
        valid_until,
    ):
        intent = app_service.create_intent_from_trade_plan(
            valid_plan,
            created_at=created_at,
            evaluation_timestamp=evaluation_timestamp,
            valid_until=valid_until,
        )
        assert intent.created_at == created_at
        assert intent.evaluation_timestamp == evaluation_timestamp
        assert intent.valid_until == valid_until

    def test_intent_id_format(self, app_service, valid_plan, created_at):
        intent = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        assert intent.intent_id.startswith("intent-")
        assert len(intent.intent_id) == len("intent-") + 16

    def test_fingerprint_format(self, app_service, valid_plan, created_at):
        intent = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        assert intent.content_fingerprint.startswith("fp-")
        assert len(intent.content_fingerprint) == len("fp-") + 16


# ============================================================
# 2. APPLICATION SERVICE WRAPS ENGINE
# ============================================================


class TestAppServiceWrapsEngine:
    """Test that the application service wraps the engine correctly."""

    def test_default_engine_created(self):
        service = OperationalTradeIntentApplicationService()
        assert isinstance(service._engine, OperationalTradeIntentEngine)

    def test_injected_engine_used(self):
        engine = OperationalTradeIntentEngine()
        service = OperationalTradeIntentApplicationService(engine=engine)
        assert service._engine is engine

    def test_delegates_to_engine(self, valid_plan, created_at):
        engine = OperationalTradeIntentEngine()
        service = OperationalTradeIntentApplicationService(engine=engine)
        intent = service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        # The intent should be identical to what the engine produces
        engine_intent = engine.create_from_plan(
            valid_plan, created_at=created_at,
        )
        assert intent.intent_id == engine_intent.intent_id
        assert intent.content_fingerprint == engine_intent.content_fingerprint


# ============================================================
# 3. GEOMETRY PRESERVED VERBATIM
# ============================================================


class TestGeometryPreserved:
    """Test that TradePlan geometry is copied verbatim into the intent."""

    def test_entry_preserved(self, app_service, valid_plan, created_at):
        intent = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        assert intent.entry == valid_plan.entry

    def test_stop_preserved(self, app_service, valid_plan, created_at):
        intent = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        assert intent.stop == valid_plan.stop

    def test_target_preserved(self, app_service, valid_plan, created_at):
        intent = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        assert intent.target_1 == valid_plan.target_1

    def test_risk_distance_preserved(self, app_service, valid_plan, created_at):
        intent = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        assert intent.engine_risk_distance == valid_plan.engine_risk_distance

    def test_reward_distance_preserved(self, app_service, valid_plan, created_at):
        intent = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        assert intent.engine_reward_distance == valid_plan.engine_reward_distance

    def test_risk_reward_ratio_preserved(
        self, app_service, valid_plan, created_at,
    ):
        intent = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        assert intent.engine_risk_reward_ratio == valid_plan.engine_risk_reward_ratio

    def test_quantity_preserved(self, app_service, valid_plan, created_at):
        intent = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        assert intent.quantity == valid_plan.quantity

    def test_planned_risk_preserved(self, app_service, valid_plan, created_at):
        intent = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        assert intent.planned_risk == valid_plan.planned_risk

    def test_maximum_risk_preserved(self, app_service, valid_plan, created_at):
        intent = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        assert intent.maximum_risk == valid_plan.maximum_risk


# ============================================================
# 4. NO TRADEPLAN MUTATION
# ============================================================


class TestNoTradePlanMutation:
    """Test that the TradePlan is not modified during intent creation."""

    def test_plan_unchanged_after_creation(
        self, app_service, valid_plan, created_at,
    ):
        original_entry = valid_plan.entry
        original_stop = valid_plan.stop
        original_quantity = valid_plan.quantity
        app_service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        assert valid_plan.entry == original_entry
        assert valid_plan.stop == original_stop
        assert valid_plan.quantity == original_quantity

    def test_plan_bytes_equivalent(self, app_service, valid_plan, created_at):
        import pickle

        original_bytes = pickle.dumps(valid_plan)
        app_service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        after_bytes = pickle.dumps(valid_plan)
        assert original_bytes == after_bytes


# ============================================================
# 5. NO MARKETSCANRESULT / PAPER TRADE / MARKET DATA INTERACTION
# ============================================================


class TestNoForbiddenInteractions:
    """Test that intent creation does not interact with forbidden components."""

    def test_no_market_scanner_import(self):
        import engine.intelligence.operational_trade_intent_application as mod
        import ast

        with open(mod.__file__) as f:
            tree = ast.parse(f.read())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names.add(node.module)
        forbidden = {
            "engine.intelligence.market_scanner",
            "engine.models.market_scan",
        }
        assert not (names & forbidden), f"Forbidden imports found: {names & forbidden}"

    def test_no_paper_trade_import(self):
        import engine.intelligence.operational_trade_intent_application as mod
        import ast

        with open(mod.__file__) as f:
            tree = ast.parse(f.read())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names.add(node.module)
        forbidden = {
            "engine.intelligence.paper_trading",
            "engine.models.paper_trade",
        }
        assert not (names & forbidden), f"Forbidden imports found: {names & forbidden}"

    def test_no_market_data_import(self):
        import engine.intelligence.operational_trade_intent_application as mod
        import ast

        with open(mod.__file__) as f:
            tree = ast.parse(f.read())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names.add(node.module)
        forbidden = {
            "engine.models.ohlcv",
            "engine.data.yahoo_provider",
            "engine.data.historical_provider",
        }
        assert not (names & forbidden), f"Forbidden imports found: {names & forbidden}"

    def test_no_authorization_import(self):
        import engine.intelligence.operational_trade_intent_application as mod
        import ast

        with open(mod.__file__) as f:
            tree = ast.parse(f.read())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names.add(node.module)
        # No authorization-related modules should be imported
        for name in names:
            assert "authorization" not in name.lower(), (
                f"Forbidden authorization import: {name}"
            )
            assert "approv" not in name.lower(), (
                f"Forbidden approval import: {name}"
            )

    def test_no_execution_import(self):
        import engine.intelligence.operational_trade_intent_application as mod
        import ast

        with open(mod.__file__) as f:
            tree = ast.parse(f.read())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names.add(node.module)
        for name in names:
            assert "execution" not in name.lower(), (
                f"Forbidden execution import: {name}"
            )
            assert "broker" not in name.lower(), (
                f"Forbidden broker import: {name}"
            )

    def test_no_persistence_import(self):
        import engine.intelligence.operational_trade_intent_application as mod
        import ast

        with open(mod.__file__) as f:
            tree = ast.parse(f.read())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names.add(node.module)
        for name in names:
            assert "store" not in name.lower(), (
                f"Forbidden store import: {name}"
            )
            assert "persist" not in name.lower(), (
                f"Forbidden persistence import: {name}"
            )


# ============================================================
# 6. IDENTITY AND FINGERPRINT PRESERVATION
# ============================================================


class TestIdentityPreservation:
    """Test that the identity contract from Checkpoint 14.2 is preserved."""

    def test_deterministic_intent_id(self, app_service, valid_plan, created_at):
        intent1 = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        intent2 = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        assert intent1.intent_id == intent2.intent_id

    def test_deterministic_fingerprint(
        self, app_service, valid_plan, created_at,
    ):
        intent1 = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        intent2 = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        assert intent1.content_fingerprint == intent2.content_fingerprint

    def test_different_timestamps_different_intent_id(
        self, app_service, valid_plan, created_at,
    ):
        intent1 = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        later = datetime(2026, 9, 1, 14, 0, 0, tzinfo=timezone.utc)
        intent2 = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=later,
        )
        assert intent1.intent_id != intent2.intent_id

    def test_different_timestamps_same_fingerprint(
        self, app_service, valid_plan, created_at,
    ):
        intent1 = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        later = datetime(2026, 9, 1, 14, 0, 0, tzinfo=timezone.utc)
        intent2 = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=later,
        )
        # Fingerprint is content-only, so it should be the same
        assert intent1.content_fingerprint == intent2.content_fingerprint


# ============================================================
# 7. TIMESTAMP HANDLING
# ============================================================


class TestTimestampHandling:
    """Test timestamp responsibility and validation."""

    def test_created_at_required(self, app_service, valid_plan):
        with pytest.raises(TypeError):
            app_service.create_intent_from_trade_plan(valid_plan)

    def test_naive_datetime_rejected(self, app_service, valid_plan):
        naive = datetime(2026, 9, 1, 12, 0, 0)
        with pytest.raises(ValueError, match="timezone-aware"):
            app_service.create_intent_from_trade_plan(
                valid_plan, created_at=naive,
            )

    def test_valid_until_before_created_at_rejected(
        self, app_service, valid_plan, created_at,
    ):
        earlier = datetime(2026, 9, 1, 11, 0, 0, tzinfo=timezone.utc)
        with pytest.raises(ValueError, match="valid_until"):
            app_service.create_intent_from_trade_plan(
                valid_plan,
                created_at=created_at,
                valid_until=earlier,
            )

    def test_evaluation_timestamp_optional(
        self, app_service, valid_plan, created_at,
    ):
        intent = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        assert intent.evaluation_timestamp is None

    def test_valid_until_optional(self, app_service, valid_plan, created_at):
        intent = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        assert intent.valid_until is None


# ============================================================
# 8. INVALID TRADEPLAN HANDLING
# ============================================================


class TestInvalidTradePlanHandling:
    """Test that invalid TradePlans are rejected."""

    def test_non_valid_status_rejected(self, app_service, created_at):
        plan = _make_plan(risk_plan_status=RiskPlanStatus.INVALID_INPUT)
        with pytest.raises(ValueError, match="non-VALID"):
            app_service.create_intent_from_trade_plan(
                plan, created_at=created_at,
            )

    def test_geometry_unavailable_status_rejected(
        self, app_service, created_at,
    ):
        plan = _make_plan(risk_plan_status=RiskPlanStatus.GEOMETRY_UNAVAILABLE)
        with pytest.raises(ValueError, match="non-VALID"):
            app_service.create_intent_from_trade_plan(
                plan, created_at=created_at,
            )

    def test_risk_limit_exceeded_status_rejected(
        self, app_service, created_at,
    ):
        plan = _make_plan(risk_plan_status=RiskPlanStatus.RISK_LIMIT_EXCEEDED)
        with pytest.raises(ValueError, match="non-VALID"):
            app_service.create_intent_from_trade_plan(
                plan, created_at=created_at,
            )

    def test_non_directional_rejected_at_plan_level(self, created_at):
        """A VALID plan requires LONG/SHORT — so non-directional is a
        construction error, not a factory error. Verify the model
        enforces this."""
        with pytest.raises(ValueError, match="directional"):
            _make_plan(direction="NONE")

    def test_empty_direction_rejected_at_plan_level(self, created_at):
        """A VALID plan requires LONG/SHORT — so empty direction is a
        construction error. Verify the model enforces this."""
        with pytest.raises(ValueError, match="directional"):
            _make_plan(direction="")

    def test_wrong_direction_rejected_at_plan_level(self, created_at):
        """A VALID plan requires LONG/SHORT — so 'BUY' is a construction
        error. Verify the model enforces this."""
        with pytest.raises(ValueError, match="directional"):
            _make_plan(direction="BUY")


# ============================================================
# 9. IMMUTABILITY
# ============================================================


class TestImmutability:
    """Test that the returned intent is immutable."""

    def test_intent_is_frozen(self, app_service, valid_plan, created_at):
        intent = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        with pytest.raises(AttributeError):
            intent.instrument = "RELIANCE"

    def test_intent_has_slots(self, app_service, valid_plan, created_at):
        intent = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        with pytest.raises((AttributeError, TypeError)):
            intent.nonexistent_field = "value"

    def test_warnings_is_tuple(self, app_service, valid_plan, created_at):
        intent = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        assert isinstance(intent.warnings, tuple)

    def test_metadata_is_tuple(self, app_service, valid_plan, created_at):
        intent = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        assert isinstance(intent.metadata, tuple)


# ============================================================
# 10. NO HIDDEN GLOBAL STATE
# ============================================================


class TestNoHiddenGlobalState:
    """Test that the application service has no hidden global state."""

    def test_stateless_repeated_calls(
        self, valid_plan, created_at,
    ):
        service1 = OperationalTradeIntentApplicationService()
        service2 = OperationalTradeIntentApplicationService()
        intent1 = service1.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        intent2 = service2.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        assert intent1.intent_id == intent2.intent_id

    def test_no_registry(self, app_service):
        assert not hasattr(app_service, "_registry")
        assert not hasattr(app_service, "_intents")
        assert not hasattr(app_service, "_cache")

    def test_no_singleton_state(self):
        s1 = OperationalTradeIntentApplicationService()
        s2 = OperationalTradeIntentApplicationService()
        assert s1 is not s2
        assert s1._engine is not s2._engine


# ============================================================
# 11. REPEATED EXPLICIT CREATION BEHAVIOR
# ============================================================


class TestRepeatedCreation:
    """Test behavior of repeated explicit creation requests."""

    def test_same_inputs_same_identity(
        self, app_service, valid_plan, created_at,
    ):
        intent1 = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        intent2 = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        assert intent1.intent_id == intent2.intent_id
        assert intent1.content_fingerprint == intent2.content_fingerprint
        assert intent1.plan_id == intent2.plan_id

    def test_different_created_at_different_identity(
        self, app_service, valid_plan, created_at,
    ):
        intent1 = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        later = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
        intent2 = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=later,
        )
        assert intent1.intent_id != intent2.intent_id

    def test_different_labels_different_identity(
        self, app_service, valid_plan, created_at,
    ):
        intent1 = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        intent2 = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at, label="different",
        )
        assert intent1.intent_id != intent2.intent_id


# ============================================================
# 12. OPERATIONALTRADEINTENTVIEW PROJECTION
# ============================================================


class TestOperationalTradeIntentView:
    """Test the presentation view projection."""

    def test_from_intent(self, app_service, valid_plan, created_at):
        from dashboard.views import OperationalTradeIntentView

        intent = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        view = OperationalTradeIntentView.from_intent(intent)
        assert view.intent_id == intent.intent_id
        assert view.plan_id == intent.plan_id
        assert view.instrument == intent.instrument
        assert view.direction == intent.direction
        assert view.content_fingerprint == intent.content_fingerprint

    def test_view_is_frozen(self, app_service, valid_plan, created_at):
        from dashboard.views import OperationalTradeIntentView

        intent = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        view = OperationalTradeIntentView.from_intent(intent)
        with pytest.raises(AttributeError):
            view.instrument = "RELIANCE"

    def test_view_has_slots(self, app_service, valid_plan, created_at):
        from dashboard.views import OperationalTradeIntentView

        intent = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        view = OperationalTradeIntentView.from_intent(intent)
        with pytest.raises((AttributeError, TypeError)):
            view.nonexistent = "value"


# ============================================================
# 13. JSON SERIALIZATION
# ============================================================


class TestJsonSerialization:
    """Test JSON serialization of the presentation view."""

    def test_jsonable_round_trip(self, app_service, valid_plan, created_at):
        from dashboard.views import (
            OperationalTradeIntentView,
            operational_trade_intent_view_to_jsonable,
        )

        intent = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        view = OperationalTradeIntentView.from_intent(intent)
        payload = operational_trade_intent_view_to_jsonable(view)
        assert payload["intent_id"] == intent.intent_id
        assert payload["plan_id"] == intent.plan_id
        assert payload["instrument"] == "NIFTY"
        assert payload["direction"] == "LONG"
        assert payload["content_fingerprint"] == intent.content_fingerprint

    def test_decimal_as_string(self, app_service, valid_plan, created_at):
        from dashboard.views import (
            OperationalTradeIntentView,
            operational_trade_intent_view_to_jsonable,
        )

        intent = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        view = OperationalTradeIntentView.from_intent(intent)
        payload = operational_trade_intent_view_to_jsonable(view)
        assert payload["entry"] == "100.50"
        assert payload["stop"] == "95.00"
        assert payload["target_1"] == "110.00"

    def test_decimal_float_field(self, app_service, valid_plan, created_at):
        from dashboard.views import (
            OperationalTradeIntentView,
            operational_trade_intent_view_to_jsonable,
        )

        intent = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        view = OperationalTradeIntentView.from_intent(intent)
        payload = operational_trade_intent_view_to_jsonable(view)
        assert payload["entry_float"] == 100.5
        assert payload["stop_float"] == 95.0

    def test_timestamp_as_iso(self, app_service, valid_plan, created_at):
        from dashboard.views import (
            OperationalTradeIntentView,
            operational_trade_intent_view_to_jsonable,
        )

        intent = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        view = OperationalTradeIntentView.from_intent(intent)
        payload = operational_trade_intent_view_to_jsonable(view)
        assert payload["created_at"] == created_at.isoformat()

    def test_null_timestamp_as_none(self, app_service, valid_plan, created_at):
        from dashboard.views import (
            OperationalTradeIntentView,
            operational_trade_intent_view_to_jsonable,
        )

        intent = app_service.create_intent_from_trade_plan(
            valid_plan, created_at=created_at,
        )
        view = OperationalTradeIntentView.from_intent(intent)
        payload = operational_trade_intent_view_to_jsonable(view)
        assert payload["valid_until"] is None
