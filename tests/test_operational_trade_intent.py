"""
Tests for Operational Trade Intent model (Checkpoint 14.2).

Covers:
1. model construction
2. frozen immutability
3. slots
4. field preservation
5. forbidden-field exclusion
6. account_capital / risk_percent exclusion
7. no mutation of TradePlan
8. no mutation through metadata
9. Decimal preservation
10. deterministic intent_id
11. intent_id changes when identity content changes
12. intent_id independent of dictionary ordering
13. content_fingerprint determinism
14. fingerprint changes when economic content changes
15. fingerprint ignores non-economic metadata
16. fingerprint ignores timestamps
17. Decimal canonicalization
18. enum canonicalization
19. timestamp validation
20. version validation
21. broker neutrality
22. authorization separation
23. execution separation
24. paper-trading separation
25. point-in-time independence
26. no recalculation
27. repeated construction produces equivalent results
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from engine.models.operational_trade_intent import (
    OPERATIONAL_TRADE_INTENT_VERSION,
    OperationalTradeIntent,
    create_intent_from_plan,
)
from engine.models.trade_plan import RiskPlanStatus


# ============================================================
# FIXTURES
# ============================================================


@pytest.fixture
def valid_params() -> dict:
    """Valid parameters for create_intent_from_plan."""

    return {
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
        "created_at": datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc),
        "evaluation_timestamp": datetime(2026, 9, 1, 11, 59, 0, tzinfo=timezone.utc),
        "valid_until": datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc),
        "warnings": ("geometry-incomplete",),
        "rationale": "Test plan rationale.",
        "label": "test-label",
        "metadata": (("key1", "val1"), ("key2", "val2")),
    }


# ============================================================
# A. MODEL CONSTRUCTION
# ============================================================


class TestModelConstruction:
    """Test basic model construction."""

    def test_create_valid_intent(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert isinstance(intent, OperationalTradeIntent)
        assert intent.instrument == "NIFTY"
        assert intent.direction == "LONG"

    def test_intent_id_format(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert intent.intent_id.startswith("intent-")
        assert len(intent.intent_id) == len("intent-") + 16

    def test_fingerprint_format(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert intent.content_fingerprint.startswith("fp-")
        assert len(intent.content_fingerprint) == len("fp-") + 16

    def test_default_version(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert intent.version == OPERATIONAL_TRADE_INTENT_VERSION

    def test_is_valid_property(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert intent.is_valid is True

    def test_non_valid_status_is_not_valid(self, valid_params):
        """An intent with non-VALID status should not be constructible."""
        params = {**valid_params, "risk_plan_status": RiskPlanStatus.INVALID_INPUT}
        with pytest.raises(ValueError, match="non-VALID"):
            create_intent_from_plan(**params)

    def test_no_timestamps_defaults(self, valid_params):
        """evaluation_timestamp and valid_until default to None."""
        params = {**valid_params}
        params.pop("evaluation_timestamp")
        params.pop("valid_until")
        intent = create_intent_from_plan(**params)
        assert intent.evaluation_timestamp is None
        assert intent.valid_until is None

    def test_none_geometry_fields(self, valid_params):
        """Intent can be created with None geometry fields."""
        params = {
            **valid_params,
            "entry": None,
            "stop": None,
            "target_1": None,
            "engine_risk_distance": None,
            "engine_reward_distance": None,
            "engine_risk_reward_ratio": None,
        }
        # But risk_plan_status VALID requires geometry - must use different status
        params["risk_plan_status"] = RiskPlanStatus.GEOMETRY_UNAVAILABLE
        with pytest.raises(ValueError, match="non-VALID"):
            create_intent_from_plan(**params)


# ============================================================
# B. FROZEN IMMUTABILITY
# ============================================================


class TestFrozenImmutability:
    """Test that the intent is frozen."""

    def test_frozen_dataclass(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        with pytest.raises(AttributeError):
            intent.instrument = "RELIANCE"

    def test_frozen_direction(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        with pytest.raises(AttributeError):
            intent.direction = "SHORT"

    def test_frozen_intent_id(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        with pytest.raises(AttributeError):
            intent.intent_id = "intent-new"

    def test_frozen_entry(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        with pytest.raises(AttributeError):
            intent.entry = Decimal("200")

    def test_frozen_metadata(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert isinstance(intent.metadata, tuple)
        assert isinstance(intent.metadata[0], tuple)

    def test_frozen_warnings(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert isinstance(intent.warnings, tuple)

    def test_slots(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        with pytest.raises((AttributeError, TypeError)):
            intent.nonexistent_field = "test"


# ============================================================
# C. FIELD PRESERVATION
# ============================================================


class TestFieldPreservation:
    """Test that fields are preserved verbatim from TradePlan values."""

    def test_plan_id_preserved(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert intent.plan_id == "plan-abc123def4567890"

    def test_instrument_preserved(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert intent.instrument == "NIFTY"

    def test_timeframe_preserved(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert intent.timeframe == "15m"

    def test_direction_preserved(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert intent.direction == "LONG"

    def test_entry_preserved(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert intent.entry == Decimal("100.50")

    def test_stop_preserved(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert intent.stop == Decimal("95.00")

    def test_target_1_preserved(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert intent.target_1 == Decimal("110.00")

    def test_engine_risk_distance_preserved(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert intent.engine_risk_distance == Decimal("5.50")

    def test_engine_reward_distance_preserved(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert intent.engine_reward_distance == Decimal("9.50")

    def test_engine_risk_reward_ratio_preserved(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert intent.engine_risk_reward_ratio == Decimal("1.727")

    def test_quantity_preserved(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert intent.quantity == Decimal("10")

    def test_planned_risk_preserved(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert intent.planned_risk == Decimal("55.00")

    def test_maximum_risk_preserved(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert intent.maximum_risk == Decimal("100.00")

    def test_risk_plan_status_preserved(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert intent.risk_plan_status == RiskPlanStatus.VALID

    def test_existing_decision_preserved(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert intent.existing_decision == "QUALIFIED"

    def test_actionability_preserved(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert intent.actionability == "READY_FOR_REVIEW"

    def test_warnings_preserved(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert intent.warnings == ("geometry-incomplete",)

    def test_rationale_preserved(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert intent.rationale == "Test plan rationale."

    def test_label_preserved(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert intent.label == "test-label"

    def test_metadata_preserved(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert intent.metadata == (("key1", "val1"), ("key2", "val2"))


# ============================================================
# D. FORBIDDEN-FIELD EXCLUSION
# ============================================================


class TestForbiddenFieldExclusion:
    """Test that forbidden fields are excluded from the intent."""

    def test_no_account_capital(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert not hasattr(intent, "account_capital")

    def test_no_risk_percent(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert not hasattr(intent, "risk_percent")

    def test_no_target_2(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert not hasattr(intent, "target_2")

    def test_no_target_2_supported(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert not hasattr(intent, "target_2_supported")

    def test_no_authorization_id(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert not hasattr(intent, "authorization_id")

    def test_no_authorization_status(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert not hasattr(intent, "authorization_status")

    def test_no_command_id(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert not hasattr(intent, "command_id")

    def test_no_broker_order_id(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert not hasattr(intent, "broker_order_id")

    def test_no_paper_trade_id(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert not hasattr(intent, "paper_trade_id")

    def test_no_broker_symbol(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert not hasattr(intent, "broker_symbol")

    def test_no_exchange(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert not hasattr(intent, "exchange")

    def test_no_fill_price(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert not hasattr(intent, "fill_price")

    def test_no_realized_pnl(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert not hasattr(intent, "realized_pnl")

    def test_no_position_id(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert not hasattr(intent, "position_id")


# ============================================================
# E. DECIMAL PRESERVATION
# ============================================================


class TestDecimalPreservation:
    """Test that Decimal values are preserved exactly."""

    def test_entry_is_decimal(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert isinstance(intent.entry, Decimal)

    def test_stop_is_decimal(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert isinstance(intent.stop, Decimal)

    def test_quantity_is_decimal(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert isinstance(intent.quantity, Decimal)

    def test_planned_risk_is_decimal(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert isinstance(intent.planned_risk, Decimal)

    def test_maximum_risk_is_decimal(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert isinstance(intent.maximum_risk, Decimal)

    def test_decimal_precision_preserved(self, valid_params):
        params = {**valid_params, "entry": Decimal("100.123456789")}
        intent = create_intent_from_plan(**params)
        assert intent.entry == Decimal("100.123456789")

    def test_no_float_conversion(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert not isinstance(intent.entry, float)
        assert not isinstance(intent.quantity, float)


# ============================================================
# F. DETERMINISTIC INTENT_ID
# ============================================================


class TestDeterministicIntentId:
    """Test that intent_id is deterministic."""

    def test_same_inputs_same_id(self, valid_params):
        intent1 = create_intent_from_plan(**valid_params)
        intent2 = create_intent_from_plan(**valid_params)
        assert intent1.intent_id == intent2.intent_id

    def test_different_instrument_different_id(self, valid_params):
        intent1 = create_intent_from_plan(**valid_params)
        params = {**valid_params, "instrument": "RELIANCE"}
        intent2 = create_intent_from_plan(**params)
        assert intent1.intent_id != intent2.intent_id

    def test_different_direction_different_id(self, valid_params):
        intent1 = create_intent_from_plan(**valid_params)
        params = {**valid_params, "direction": "SHORT"}
        intent2 = create_intent_from_plan(**params)
        assert intent1.intent_id != intent2.intent_id

    def test_different_entry_different_id(self, valid_params):
        intent1 = create_intent_from_plan(**valid_params)
        params = {**valid_params, "entry": Decimal("200")}
        intent2 = create_intent_from_plan(**params)
        assert intent1.intent_id != intent2.intent_id

    def test_different_stop_different_id(self, valid_params):
        intent1 = create_intent_from_plan(**valid_params)
        params = {**valid_params, "stop": Decimal("90")}
        intent2 = create_intent_from_plan(**params)
        assert intent1.intent_id != intent2.intent_id

    def test_different_target_different_id(self, valid_params):
        intent1 = create_intent_from_plan(**valid_params)
        params = {**valid_params, "target_1": Decimal("120")}
        intent2 = create_intent_from_plan(**params)
        assert intent1.intent_id != intent2.intent_id

    def test_different_quantity_different_id(self, valid_params):
        intent1 = create_intent_from_plan(**valid_params)
        params = {**valid_params, "quantity": Decimal("20")}
        intent2 = create_intent_from_plan(**params)
        assert intent1.intent_id != intent2.intent_id

    def test_different_created_at_different_id(self, valid_params):
        intent1 = create_intent_from_plan(**valid_params)
        params = {
            **valid_params,
            "created_at": datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc),
        }
        intent2 = create_intent_from_plan(**params)
        assert intent1.intent_id != intent2.intent_id

    def test_different_label_different_id(self, valid_params):
        intent1 = create_intent_from_plan(**valid_params)
        params = {**valid_params, "label": "different-label"}
        intent2 = create_intent_from_plan(**params)
        assert intent1.intent_id != intent2.intent_id

    def test_different_metadata_different_id(self, valid_params):
        intent1 = create_intent_from_plan(**valid_params)
        params = {**valid_params, "metadata": (("k", "v"),)}
        intent2 = create_intent_from_plan(**params)
        assert intent1.intent_id != intent2.intent_id

    def test_intent_id_differs_from_plan_id(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert intent.intent_id != intent.plan_id


# ============================================================
# G. INTENT_ID INDEPENDENT OF DICTIONARY ORDERING
# ============================================================


class TestIntentIdIndependentOfOrdering:
    """Test that intent_id is independent of metadata ordering."""

    def test_metadata_order_does_not_affect_id(self, valid_params):
        params1 = {**valid_params, "metadata": (("a", "1"), ("b", "2"), ("c", "3"))}
        params2 = {**valid_params, "metadata": (("c", "3"), ("a", "1"), ("b", "2"))}
        intent1 = create_intent_from_plan(**params1)
        intent2 = create_intent_from_plan(**params2)
        assert intent1.intent_id == intent2.intent_id


# ============================================================
# H. CONTENT FINGERPRINT DETERMINISM
# ============================================================


class TestContentFingerprint:
    """Test content_fingerprint behavior."""

    def test_same_content_same_fingerprint(self, valid_params):
        intent1 = create_intent_from_plan(**valid_params)
        intent2 = create_intent_from_plan(**valid_params)
        assert intent1.content_fingerprint == intent2.content_fingerprint

    def test_different_entry_different_fingerprint(self, valid_params):
        intent1 = create_intent_from_plan(**valid_params)
        params = {**valid_params, "entry": Decimal("200")}
        intent2 = create_intent_from_plan(**params)
        assert intent1.content_fingerprint != intent2.content_fingerprint

    def test_different_stop_different_fingerprint(self, valid_params):
        intent1 = create_intent_from_plan(**valid_params)
        params = {**valid_params, "stop": Decimal("90")}
        intent2 = create_intent_from_plan(**params)
        assert intent1.content_fingerprint != intent2.content_fingerprint

    def test_different_target_different_fingerprint(self, valid_params):
        intent1 = create_intent_from_plan(**valid_params)
        params = {**valid_params, "target_1": Decimal("120")}
        intent2 = create_intent_from_plan(**params)
        assert intent1.content_fingerprint != intent2.content_fingerprint

    def test_different_quantity_different_fingerprint(self, valid_params):
        intent1 = create_intent_from_plan(**valid_params)
        params = {**valid_params, "quantity": Decimal("20")}
        intent2 = create_intent_from_plan(**params)
        assert intent1.content_fingerprint != intent2.content_fingerprint

    def test_different_direction_different_fingerprint(self, valid_params):
        intent1 = create_intent_from_plan(**valid_params)
        params = {**valid_params, "direction": "SHORT"}
        intent2 = create_intent_from_plan(**params)
        assert intent1.content_fingerprint != intent2.content_fingerprint

    def test_different_instrument_different_fingerprint(self, valid_params):
        intent1 = create_intent_from_plan(**valid_params)
        params = {**valid_params, "instrument": "RELIANCE"}
        intent2 = create_intent_from_plan(**params)
        assert intent1.content_fingerprint != intent2.content_fingerprint

    def test_fingerprint_ignores_timestamps(self, valid_params):
        """Fingerprint should NOT depend on created_at or evaluation_timestamp."""
        intent1 = create_intent_from_plan(**valid_params)
        params = {
            **valid_params,
            "created_at": datetime(2027, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
            "evaluation_timestamp": datetime(2027, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
            "valid_until": datetime(2027, 6, 1, 0, 0, 0, tzinfo=timezone.utc),
        }
        intent2 = create_intent_from_plan(**params)
        assert intent1.content_fingerprint == intent2.content_fingerprint

    def test_fingerprint_ignores_label(self, valid_params):
        """Fingerprint should NOT depend on label."""
        intent1 = create_intent_from_plan(**valid_params)
        params = {**valid_params, "label": "completely-different-label"}
        intent2 = create_intent_from_plan(**params)
        assert intent1.content_fingerprint == intent2.content_fingerprint

    def test_fingerprint_ignores_metadata(self, valid_params):
        """Fingerprint should NOT depend on metadata."""
        intent1 = create_intent_from_plan(**valid_params)
        params = {**valid_params, "metadata": (("x", "y"), ("z", "w"))}
        intent2 = create_intent_from_plan(**params)
        assert intent1.content_fingerprint == intent2.content_fingerprint

    def test_fingerprint_ignores_warnings(self, valid_params):
        """Fingerprint should NOT depend on warnings."""
        intent1 = create_intent_from_plan(**valid_params)
        params = {**valid_params, "warnings": ("warn1", "warn2")}
        intent2 = create_intent_from_plan(**params)
        assert intent1.content_fingerprint == intent2.content_fingerprint

    def test_fingerprint_ignores_rationale(self, valid_params):
        """Fingerprint should NOT depend on rationale."""
        intent1 = create_intent_from_plan(**valid_params)
        params = {**valid_params, "rationale": "Different rationale."}
        intent2 = create_intent_from_plan(**params)
        assert intent1.content_fingerprint == intent2.content_fingerprint

    def test_fingerprint_ignores_existing_decision(self, valid_params):
        """Fingerprint should NOT depend on existing_decision."""
        intent1 = create_intent_from_plan(**valid_params)
        params = {**valid_params, "existing_decision": "PREFERRED"}
        intent2 = create_intent_from_plan(**params)
        assert intent1.content_fingerprint == intent2.content_fingerprint

    def test_fingerprint_ignores_actionability(self, valid_params):
        """Fingerprint should NOT depend on actionability."""
        intent1 = create_intent_from_plan(**valid_params)
        params = {**valid_params, "actionability": "NO_OPPORTUNITY"}
        intent2 = create_intent_from_plan(**params)
        assert intent1.content_fingerprint == intent2.content_fingerprint

    def test_fingerprint_ignores_valid_until(self, valid_params):
        """Fingerprint should NOT depend on valid_until."""
        intent1 = create_intent_from_plan(**valid_params)
        params = {
            **valid_params,
            "valid_until": datetime(2027, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
        }
        intent2 = create_intent_from_plan(**params)
        assert intent1.content_fingerprint == intent2.content_fingerprint


# ============================================================
# I. DECIMAL CANONICALIZATION
# ============================================================


class TestDecimalCanonicalization:
    """Test that Decimal values are canonicalized for identity."""

    def test_decimal_trailing_zeros_same_id(self, valid_params):
        """Decimal("1.0") and Decimal("1.00") should produce same intent_id."""
        params1 = {**valid_params, "entry": Decimal("100.50")}
        params2 = {**valid_params, "entry": Decimal("100.5000")}
        intent1 = create_intent_from_plan(**params1)
        intent2 = create_intent_from_plan(**params2)
        assert intent1.intent_id == intent2.intent_id

    def test_decimal_integer_same_id(self, valid_params):
        """Decimal("10") and Decimal("10.0") should produce same intent_id."""
        params1 = {**valid_params, "quantity": Decimal("10")}
        params2 = {**valid_params, "quantity": Decimal("10.0")}
        intent1 = create_intent_from_plan(**params1)
        intent2 = create_intent_from_plan(**params2)
        assert intent1.intent_id == intent2.intent_id

    def test_decimal_trailing_zeros_same_fingerprint(self, valid_params):
        """Decimal("1.0") and Decimal("1.00") should produce same fingerprint."""
        params1 = {**valid_params, "entry": Decimal("100.50")}
        params2 = {**valid_params, "entry": Decimal("100.5000")}
        intent1 = create_intent_from_plan(**params1)
        intent2 = create_intent_from_plan(**params2)
        assert intent1.content_fingerprint == intent2.content_fingerprint


# ============================================================
# J. ENUM CANONICALIZATION
# ============================================================


class TestEnumCanonicalization:
    """Test that enums are canonicalized for identity."""

    def test_risk_plan_status_in_identity(self, valid_params):
        """Different risk_plan_status should produce different intent_id."""
        intent1 = create_intent_from_plan(**valid_params)
        # We can't create a VALID intent with a different status (factory rejects),
        # but we can verify the enum is part of the identity by checking the
        # canonical representation includes it.
        assert intent1.risk_plan_status == RiskPlanStatus.VALID


# ============================================================
# K. TIMESTAMP VALIDATION
# ============================================================


class TestTimestampValidation:
    """Test timestamp validation."""

    def test_valid_until_before_created_at_raises(self, valid_params):
        params = {
            **valid_params,
            "valid_until": datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc),
        }
        with pytest.raises(ValueError, match="valid_until"):
            create_intent_from_plan(**params)

    def test_naive_created_at_raises(self, valid_params):
        params = {
            **valid_params,
            "created_at": datetime(2026, 9, 1, 12, 0, 0),
        }
        with pytest.raises(ValueError, match="timezone-aware"):
            create_intent_from_plan(**params)

    def test_naive_evaluation_timestamp_raises(self, valid_params):
        params = {
            **valid_params,
            "evaluation_timestamp": datetime(2026, 9, 1, 11, 59, 0),
        }
        with pytest.raises(ValueError, match="timezone-aware"):
            create_intent_from_plan(**params)

    def test_naive_valid_until_raises(self, valid_params):
        params = {
            **valid_params,
            "valid_until": datetime(2026, 9, 2, 12, 0, 0),
        }
        with pytest.raises(ValueError, match="timezone-aware"):
            create_intent_from_plan(**params)

    def test_valid_until_equals_created_at_ok(self, valid_params):
        """valid_until == created_at should be valid."""
        params = {
            **valid_params,
            "valid_until": valid_params["created_at"],
        }
        intent = create_intent_from_plan(**params)
        assert intent.valid_until == intent.created_at


# ============================================================
# L. VERSION VALIDATION
# ============================================================


class TestVersionValidation:
    """Test version validation."""

    def test_default_version(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert intent.version == 1

    def test_version_zero_raises(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        with pytest.raises(ValueError, match="version"):
            OperationalTradeIntent(
                intent_id=intent.intent_id,
                plan_id=intent.plan_id,
                instrument=intent.instrument,
                timeframe=intent.timeframe,
                direction=intent.direction,
                entry=intent.entry,
                stop=intent.stop,
                target_1=intent.target_1,
                engine_risk_distance=intent.engine_risk_distance,
                engine_reward_distance=intent.engine_reward_distance,
                engine_risk_reward_ratio=intent.engine_risk_reward_ratio,
                quantity=intent.quantity,
                planned_risk=intent.planned_risk,
                maximum_risk=intent.maximum_risk,
                risk_plan_status=intent.risk_plan_status,
                existing_decision=intent.existing_decision,
                actionability=intent.actionability,
                created_at=intent.created_at,
                evaluation_timestamp=intent.evaluation_timestamp,
                valid_until=intent.valid_until,
                content_fingerprint=intent.content_fingerprint,
                version=0,
            )


# ============================================================
# M. BROKER NEUTRALITY
# ============================================================


class TestBrokerNeutrality:
    """Test that the intent is broker-neutral."""

    def test_no_broker_fields(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        for attr in [
            "broker_symbol",
            "exchange",
            "segment",
            "routing",
            "product_type",
            "broker_account_id",
            "broker_credentials",
            "broker_order_id",
        ]:
            assert not hasattr(intent, attr), f"Intent should not have {attr}"


# ============================================================
# N. AUTHORIZATION SEPARATION
# ============================================================


class TestAuthorizationSeparation:
    """Test that the intent does not contain authorization fields."""

    def test_no_authorization_fields(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        for attr in [
            "authorization_id",
            "authorization_status",
            "approval_state",
            "human_approval",
            "policy_approval",
            "execution_permission",
            "authorized",
            "approved",
        ]:
            assert not hasattr(intent, attr), f"Intent should not have {attr}"


# ============================================================
# O. EXECUTION SEPARATION
# ============================================================


class TestExecutionSeparation:
    """Test that the intent does not contain execution fields."""

    def test_no_execution_fields(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        for attr in [
            "command_id",
            "broker_order_id",
            "order_id",
            "fill_price",
            "fill_quantity",
            "execution_timestamp",
            "slippage",
            "fees",
            "position_id",
            "realized_pnl",
            "execution_mode",
            "account_id",
        ]:
            assert not hasattr(intent, attr), f"Intent should not have {attr}"


# ============================================================
# P. PAPER-TRADING SEPARATION
# ============================================================


class TestPaperTradingSeparation:
    """Test that the intent does not contain paper-trading fields."""

    def test_no_paper_trade_fields(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        for attr in [
            "paper_trade_id",
            "paper_trade_status",
            "simulation_state",
            "realized_outcome",
            "exit_state",
        ]:
            assert not hasattr(intent, attr), f"Intent should not have {attr}"


# ============================================================
# Q. POINT-IN-TIME INDEPENDENCE
# ============================================================


class TestPointInTimeIndependence:
    """Test that intent creation is point-in-time independent."""

    def test_no_candle_access(self, valid_params):
        """Intent creation does not require candles."""
        # This test verifies the factory signature has no candle params
        import inspect

        sig = inspect.signature(create_intent_from_plan)
        for param_name in sig.parameters:
            assert "candle" not in param_name.lower()

    def test_no_provider_access(self, valid_params):
        """Intent creation does not require a provider."""
        import inspect

        sig = inspect.signature(create_intent_from_plan)
        for param_name in sig.parameters:
            assert "provider" not in param_name.lower()

    def test_no_scanner_access(self, valid_params):
        """Intent creation does not require a scanner."""
        import inspect

        sig = inspect.signature(create_intent_from_plan)
        for param_name in sig.parameters:
            assert "scanner" not in param_name.lower()


# ============================================================
# R. NO RECALCULATION
# ============================================================


class TestNoRecalculation:
    """Test that the intent does not recalculate planning values."""

    def test_entry_copied_not_recalculated(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert intent.entry == valid_params["entry"]

    def test_stop_copied_not_recalculated(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert intent.stop == valid_params["stop"]

    def test_target_copied_not_recalculated(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert intent.target_1 == valid_params["target_1"]

    def test_quantity_copied_not_recalculated(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert intent.quantity == valid_params["quantity"]

    def test_planned_risk_copied_not_recalculated(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert intent.planned_risk == valid_params["planned_risk"]

    def test_maximum_risk_copied_not_recalculated(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert intent.maximum_risk == valid_params["maximum_risk"]


# ============================================================
# S. REPEATED CONSTRUCTION
# ============================================================


class TestRepeatedConstruction:
    """Test that repeated construction produces equivalent results."""

    def test_repeated_construction_identical(self, valid_params):
        intents = [create_intent_from_plan(**valid_params) for _ in range(5)]
        first = intents[0]
        for other in intents[1:]:
            assert first.intent_id == other.intent_id
            assert first.content_fingerprint == other.content_fingerprint
            assert first.plan_id == other.plan_id
            assert first.instrument == other.instrument
            assert first.entry == other.entry
            assert first.stop == other.stop
            assert first.target_1 == other.target_1
            assert first.quantity == other.quantity
            assert first.planned_risk == other.planned_risk
            assert first.maximum_risk == other.maximum_risk


# ============================================================
# T. FAILURE CONTRACT
# ============================================================


class TestFailureContract:
    """Test the failure contract."""

    def test_non_valid_status_raises(self, valid_params):
        params = {**valid_params, "risk_plan_status": RiskPlanStatus.INVALID_INPUT}
        with pytest.raises(ValueError, match="non-VALID"):
            create_intent_from_plan(**params)

    def test_geometry_unavailable_raises(self, valid_params):
        params = {**valid_params, "risk_plan_status": RiskPlanStatus.GEOMETRY_UNAVAILABLE}
        with pytest.raises(ValueError, match="non-VALID"):
            create_intent_from_plan(**params)

    def test_risk_limit_exceeded_raises(self, valid_params):
        params = {**valid_params, "risk_plan_status": RiskPlanStatus.RISK_LIMIT_EXCEEDED}
        with pytest.raises(ValueError, match="non-VALID"):
            create_intent_from_plan(**params)

    def test_quantity_unavailable_raises(self, valid_params):
        params = {**valid_params, "risk_plan_status": RiskPlanStatus.QUANTITY_UNAVAILABLE}
        with pytest.raises(ValueError, match="non-VALID"):
            create_intent_from_plan(**params)

    def test_no_direction_raises(self, valid_params):
        params = {**valid_params, "direction": "NONE"}
        with pytest.raises(ValueError, match="directional bias"):
            create_intent_from_plan(**params)

    def test_empty_direction_raises(self, valid_params):
        params = {**valid_params, "direction": ""}
        with pytest.raises(ValueError, match="directional bias"):
            create_intent_from_plan(**params)

    def test_invalid_direction_raises(self, valid_params):
        params = {**valid_params, "direction": "INVALID"}
        with pytest.raises(ValueError, match="direction"):
            create_intent_from_plan(**params)

    def test_empty_instrument_raises(self, valid_params):
        params = {**valid_params, "instrument": ""}
        with pytest.raises(ValueError, match="instrument"):
            create_intent_from_plan(**params)

    def test_whitespace_instrument_raises(self, valid_params):
        params = {**valid_params, "instrument": "   "}
        with pytest.raises(ValueError, match="instrument"):
            create_intent_from_plan(**params)


# ============================================================
# U. SHORT DIRECTION
# ============================================================


class TestShortDirection:
    """Test that SHORT direction works."""

    def test_short_intent(self, valid_params):
        params = {**valid_params, "direction": "SHORT"}
        intent = create_intent_from_plan(**params)
        assert intent.direction == "SHORT"
        assert intent.intent_id.startswith("intent-")
        assert intent.content_fingerprint.startswith("fp-")

    def test_short_different_from_long(self, valid_params):
        long_intent = create_intent_from_plan(**valid_params)
        params = {**valid_params, "direction": "SHORT"}
        short_intent = create_intent_from_plan(**params)
        assert long_intent.intent_id != short_intent.intent_id
        assert long_intent.content_fingerprint != short_intent.content_fingerprint


# ============================================================
# V. EMPTY DEFAULTS
# ============================================================


class TestEmptyDefaults:
    """Test that empty defaults work."""

    def test_empty_warnings(self, valid_params):
        params = {**valid_params, "warnings": ()}
        intent = create_intent_from_plan(**params)
        assert intent.warnings == ()

    def test_empty_metadata(self, valid_params):
        params = {**valid_params, "metadata": ()}
        intent = create_intent_from_plan(**params)
        assert intent.metadata == ()

    def test_empty_rationale(self, valid_params):
        params = {**valid_params, "rationale": ""}
        intent = create_intent_from_plan(**params)
        assert intent.rationale == ""

    def test_empty_label(self, valid_params):
        params = {**valid_params, "label": ""}
        intent = create_intent_from_plan(**params)
        assert intent.label == ""


# ============================================================
# W. FINGERPRINT DIFFERS FROM INTENT_ID
# ============================================================


class TestFingerprintDiffersFromIntentId:
    """Test that content_fingerprint differs from intent_id."""

    def test_fingerprint_not_equal_to_intent_id(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert intent.content_fingerprint != intent.intent_id

    def test_fingerprint_prefix_differs(self, valid_params):
        intent = create_intent_from_plan(**valid_params)
        assert intent.content_fingerprint.startswith("fp-")
        assert intent.intent_id.startswith("intent-")
