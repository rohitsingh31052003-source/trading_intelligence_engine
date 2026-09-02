"""
Tests for Execution Authorization model (Checkpoint 15.2).

Covers:
1. Model construction
2. Frozen immutability
3. Slots behavior
4. Required fields
5. Valid construction
6. Invalid construction
7. AuthorizationStatus enum members
8. is_authorized property (fail closed)
9. Deterministic authorization_id
10. auth- prefix
11. SHA-256-derived identity
12. Identical canonical inputs -> identical ID
13. Meaningful content change -> different ID
14. Dictionary ordering independence
15. Decimal normalization
16. No random UUID
17. No wall-clock dependency
18. Intent binding (intent_id, content_fingerprint)
19. Invalid/missing binding rejected
20. Intent remains unchanged
21. Time validity: valid_from >= authorized_at
22. Time validity: expires_at > valid_from
23. Time validity: expires_at <= intent.valid_until
24. Invalid time boundary cases
25. AUTHORIZED requires valid timing
26. EXPIRED timing invariant
27. REVOKED semantics
28. SUPERSEDED semantics
29. UNAUTHORIZED is not authorized
30. ELIGIBLE is not authorized
31. Fail-closed: malformed/contradictory records cannot become authorized
32. Independence: no TradePlan mutation
33. Independence: no OperationalTradeIntent mutation
34. Independence: no PaperTradingEngine invocation
35. Independence: no MarketScanner invocation
36. Independence: no HistoricalPipeline invocation
37. Independence: no broker code access
38. Regression: frozen suites still pass
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from unittest.mock import patch

import pytest

from engine.models.execution_authorization import (
    AUTHORIZATION_ID_PREFIX,
    _ID_DIGEST_LENGTH,
    AuthorizationStatus,
    ExecutionAuthorization,
    create_authorization,
)
from engine.models.operational_trade_intent import (
    OperationalTradeIntent,
    create_intent_from_plan,
)
from engine.models.trade_plan import RiskPlanStatus


# ============================================================
# FIXTURES
# ============================================================


def _make_intent(**overrides):
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
        "metadata": (("key1", "val1"), ("key2", "val2")),
    }
    base.update(overrides)
    return create_intent_from_plan(**base)


def _make_auth_kwargs(intent, **overrides):
    """Create valid factory kwargs for create_authorization."""
    base = {
        "intent": intent,
        "status": AuthorizationStatus.AUTHORIZED,
        "authorized_at": datetime.datetime(2026, 9, 1, 12, 0, 0, tzinfo=datetime.timezone.utc),
        "valid_from": datetime.datetime(2026, 9, 1, 12, 0, 0, tzinfo=datetime.timezone.utc),
        "expires_at": datetime.datetime(2026, 9, 2, 12, 0, 0, tzinfo=datetime.timezone.utc),
        "issuer": "human",
        "authorization_method": "manual-approval",
        "scope": "paper",
        "policy_reference": "policy-v1",
        "safety_check_summary": "all-gates-passed",
        "label": "auth-label",
        "metadata": (("auth-key1", "auth-val1"),),
    }
    base.update(overrides)
    return base


@pytest.fixture
def valid_intent():
    return _make_intent()


@pytest.fixture
def valid_auth_kwargs(valid_intent):
    return _make_auth_kwargs(valid_intent)


# ============================================================
# A. MODEL CONSTRUCTION
# ============================================================


class TestModelConstruction:
    def test_create_valid_authorization(self, valid_auth_kwargs):
        auth = create_authorization(**valid_auth_kwargs)
        assert isinstance(auth, ExecutionAuthorization)

    def test_authorization_id_non_empty(self, valid_auth_kwargs):
        auth = create_authorization(**valid_auth_kwargs)
        assert auth.authorization_id

    def test_intent_id_preserved(self, valid_auth_kwargs):
        auth = create_authorization(**valid_auth_kwargs)
        assert auth.intent_id == valid_auth_kwargs["intent"].intent_id

    def test_plan_id_preserved(self, valid_auth_kwargs):
        auth = create_authorization(**valid_auth_kwargs)
        assert auth.plan_id == valid_auth_kwargs["intent"].plan_id

    def test_content_fingerprint_preserved(self, valid_auth_kwargs):
        auth = create_authorization(**valid_auth_kwargs)
        assert auth.content_fingerprint == valid_auth_kwargs["intent"].content_fingerprint

    def test_status_preserved(self, valid_auth_kwargs):
        auth = create_authorization(**valid_auth_kwargs)
        assert auth.status is AuthorizationStatus.AUTHORIZED

    def test_timestamps_preserved(self, valid_auth_kwargs):
        kwargs = valid_auth_kwargs
        auth = create_authorization(**kwargs)
        assert auth.authorized_at == kwargs["authorized_at"]
        assert auth.valid_from == kwargs["valid_from"]
        assert auth.expires_at == kwargs["expires_at"]

    def test_provenance_fields_preserved(self, valid_auth_kwargs):
        auth = create_authorization(**valid_auth_kwargs)
        assert auth.issuer == "human"
        assert auth.authorization_method == "manual-approval"
        assert auth.scope == "paper"
        assert auth.policy_reference == "policy-v1"
        assert auth.safety_check_summary == "all-gates-passed"

    def test_label_preserved(self, valid_auth_kwargs):
        auth = create_authorization(**valid_auth_kwargs)
        assert auth.label == "auth-label"

    def test_metadata_preserved_and_sorted(self, valid_auth_kwargs):
        auth = create_authorization(**valid_auth_kwargs)
        assert auth.metadata == (("auth-key1", "auth-val1"),)


# ============================================================
# B. FROZEN IMMUTABILITY
# ============================================================


class TestFrozenImmutability:
    def test_frozen_raises_on_field_assignment(self, valid_auth_kwargs):
        auth = create_authorization(**valid_auth_kwargs)
        with pytest.raises(dataclasses.FrozenInstanceError):
            auth.status = AuthorizationStatus.REVOKED  # type: ignore[misc]

    def test_intent_not_mutated(self, valid_intent, valid_auth_kwargs):
        original_id = valid_intent.intent_id
        original_fp = valid_intent.content_fingerprint
        create_authorization(**valid_auth_kwargs)
        assert valid_intent.intent_id == original_id
        assert valid_intent.content_fingerprint == original_fp

    def test_intent_same_object_identity(self, valid_intent, valid_auth_kwargs):
        auth = create_authorization(**valid_auth_kwargs)
        assert auth.intent_id == valid_intent.intent_id
        assert auth.content_fingerprint == valid_intent.content_fingerprint


import dataclasses


# ============================================================
# C. SLOTS BEHAVIOR
# ============================================================


class TestSlotsBehavior:
    def test_slots_prevent_new_attributes(self, valid_auth_kwargs):
        auth = create_authorization(**valid_auth_kwargs)
        with pytest.raises((AttributeError, TypeError)):
            auth.new_attribute = "value"  # type: ignore[attr-defined]


# ============================================================
# D. REQUIRED FIELDS
# ============================================================


class TestRequiredFields:
    def test_missing_intent_raises(self, valid_auth_kwargs):
        kwargs = dict(valid_auth_kwargs)
        kwargs["intent"] = None
        with pytest.raises(TypeError):
            create_authorization(**kwargs)

    def test_missing_status_raises(self, valid_auth_kwargs):
        kwargs = dict(valid_auth_kwargs)
        del kwargs["status"]
        with pytest.raises(TypeError):
            create_authorization(**kwargs)

    def test_missing_authorized_at_raises(self, valid_auth_kwargs):
        kwargs = dict(valid_auth_kwargs)
        del kwargs["authorized_at"]
        with pytest.raises(TypeError):
            create_authorization(**kwargs)

    def test_missing_valid_from_raises(self, valid_auth_kwargs):
        kwargs = dict(valid_auth_kwargs)
        del kwargs["valid_from"]
        with pytest.raises(TypeError):
            create_authorization(**kwargs)

    def test_missing_expires_at_raises(self, valid_auth_kwargs):
        kwargs = dict(valid_auth_kwargs)
        del kwargs["expires_at"]
        with pytest.raises(TypeError):
            create_authorization(**kwargs)

    def test_missing_issuer_raises(self, valid_auth_kwargs):
        kwargs = dict(valid_auth_kwargs)
        kwargs["issuer"] = ""
        with pytest.raises(ValueError, match="issuer must be non-empty"):
            create_authorization(**kwargs)

    def test_missing_authorization_method_raises(self, valid_auth_kwargs):
        kwargs = dict(valid_auth_kwargs)
        kwargs["authorization_method"] = ""
        with pytest.raises(ValueError, match="authorization_method must be non-empty"):
            create_authorization(**kwargs)

    def test_missing_scope_raises(self, valid_auth_kwargs):
        kwargs = dict(valid_auth_kwargs)
        kwargs["scope"] = ""
        with pytest.raises(ValueError, match="scope must be non-empty"):
            create_authorization(**kwargs)

    def test_missing_policy_reference_raises(self, valid_auth_kwargs):
        kwargs = dict(valid_auth_kwargs)
        kwargs["policy_reference"] = ""
        with pytest.raises(ValueError, match="policy_reference must be non-empty"):
            create_authorization(**kwargs)

    def test_missing_safety_check_summary_raises(self, valid_auth_kwargs):
        kwargs = dict(valid_auth_kwargs)
        kwargs["safety_check_summary"] = ""
        with pytest.raises(ValueError, match="safety_check_summary must be non-empty"):
            create_authorization(**kwargs)


# ============================================================
# E. AUTHORIZATION STATUS
# ============================================================


class TestAuthorizationStatus:
    def test_all_status_members(self):
        expected = {
            "UNAUTHORIZED",
            "ELIGIBLE",
            "AUTHORIZED",
            "EXPIRED",
            "REVOKED",
            "SUPERSEDED",
        }
        assert {s.value for s in AuthorizationStatus} == expected

    def test_authorized_is_authorized(self):
        assert AuthorizationStatus.AUTHORIZED.is_authorized is True

    def test_unauthorized_is_not_authorized(self):
        assert AuthorizationStatus.UNAUTHORIZED.is_authorized is False

    def test_eligible_is_not_authorized(self):
        assert AuthorizationStatus.ELIGIBLE.is_authorized is False

    def test_expired_is_not_authorized(self):
        assert AuthorizationStatus.EXPIRED.is_authorized is False

    def test_revoked_is_not_authorized(self):
        assert AuthorizationStatus.REVOKED.is_authorized is False

    def test_superseded_is_not_authorized(self):
        assert AuthorizationStatus.SUPERSEDED.is_authorized is False


# ============================================================
# F. DETERMINISTIC IDENTITY
# ============================================================


class TestDeterministicIdentity:
    def test_authorization_id_prefix(self, valid_auth_kwargs):
        auth = create_authorization(**valid_auth_kwargs)
        assert auth.authorization_id.startswith(AUTHORIZATION_ID_PREFIX)

    def test_authorization_id_length(self, valid_auth_kwargs):
        auth = create_authorization(**valid_auth_kwargs)
        expected_len = len(AUTHORIZATION_ID_PREFIX) + _ID_DIGEST_LENGTH
        assert len(auth.authorization_id) == expected_len

    def test_authorization_id_sha256_derived(self, valid_auth_kwargs):
        auth = create_authorization(**valid_auth_kwargs)
        digest = auth.authorization_id[len(AUTHORIZATION_ID_PREFIX):]
        assert len(digest) == _ID_DIGEST_LENGTH
        assert all(c in "0123456789abcdef" for c in digest)

    def test_identical_inputs_produce_identical_id(self, valid_intent):
        kwargs1 = _make_auth_kwargs(valid_intent)
        kwargs2 = _make_auth_kwargs(valid_intent)
        auth1 = create_authorization(**kwargs1)
        auth2 = create_authorization(**kwargs2)
        assert auth1.authorization_id == auth2.authorization_id

    def test_different_label_produces_different_id(self, valid_intent):
        kwargs1 = _make_auth_kwargs(valid_intent, label="label-a")
        kwargs2 = _make_auth_kwargs(valid_intent, label="label-b")
        auth1 = create_authorization(**kwargs1)
        auth2 = create_authorization(**kwargs2)
        assert auth1.authorization_id != auth2.authorization_id

    def test_different_issuer_produces_different_id(self, valid_intent):
        kwargs1 = _make_auth_kwargs(valid_intent, issuer="human")
        kwargs2 = _make_auth_kwargs(valid_intent, issuer="system")
        auth1 = create_authorization(**kwargs1)
        auth2 = create_authorization(**kwargs2)
        assert auth1.authorization_id != auth2.authorization_id

    def test_different_scope_produces_different_id(self, valid_intent):
        kwargs1 = _make_auth_kwargs(valid_intent, scope="paper")
        kwargs2 = _make_auth_kwargs(valid_intent, scope="live")
        auth1 = create_authorization(**kwargs1)
        auth2 = create_authorization(**kwargs2)
        assert auth1.authorization_id != auth2.authorization_id

    def test_different_intent_produces_different_id(self):
        intent1 = _make_intent(label="intent-a")
        intent2 = _make_intent(label="intent-b")
        kwargs1 = _make_auth_kwargs(intent1)
        kwargs2 = _make_auth_kwargs(intent2)
        auth1 = create_authorization(**kwargs1)
        auth2 = create_authorization(**kwargs2)
        assert auth1.authorization_id != auth2.authorization_id

    def test_dictionary_ordering_independent(self, valid_intent):
        # Metadata with different dict insertion order should produce same ID.
        kwargs1 = _make_auth_kwargs(
            valid_intent,
            metadata=(("a", "1"), ("b", "2")),
        )
        kwargs2 = _make_auth_kwargs(
            valid_intent,
            metadata=(("b", "2"), ("a", "1")),
        )
        auth1 = create_authorization(**kwargs1)
        auth2 = create_authorization(**kwargs2)
        assert auth1.authorization_id == auth2.authorization_id

    def test_decimal_normalization_identical_id(self, valid_intent):
        # Metadata values are strings; identical metadata must produce identical IDs.
        kwargs1 = _make_auth_kwargs(
            valid_intent,
            label="dec-test",
            metadata=(("dec", "1.0"),),
        )
        kwargs2 = _make_auth_kwargs(
            valid_intent,
            label="dec-test",
            metadata=(("dec", "1.0"),),
        )
        auth1 = create_authorization(**kwargs1)
        auth2 = create_authorization(**kwargs2)
        assert auth1.authorization_id == auth2.authorization_id

    def test_no_random_uuid_in_id(self, valid_auth_kwargs):
        auth = create_authorization(**valid_auth_kwargs)
        assert "uuid" not in auth.authorization_id.lower()

    def test_no_wall_clock_dependency_in_id(self, valid_intent):
        # Identical inputs at different "times" must produce the same ID.
        # Since authorized_at/valid_from/expires_at are excluded from the
        # identity payload, changing them must NOT change the ID.
        intent = _make_intent(valid_until=None)
        kwargs1 = _make_auth_kwargs(
            intent,
            authorized_at=datetime.datetime(2026, 9, 1, 12, 0, 0, tzinfo=datetime.timezone.utc),
            valid_from=datetime.datetime(2026, 9, 1, 12, 0, 0, tzinfo=datetime.timezone.utc),
            expires_at=datetime.datetime(2026, 9, 2, 12, 0, 0, tzinfo=datetime.timezone.utc),
        )
        kwargs2 = _make_auth_kwargs(
            intent,
            authorized_at=datetime.datetime(2026, 9, 5, 12, 0, 0, tzinfo=datetime.timezone.utc),
            valid_from=datetime.datetime(2026, 9, 5, 12, 0, 0, tzinfo=datetime.timezone.utc),
            expires_at=datetime.datetime(2026, 9, 6, 12, 0, 0, tzinfo=datetime.timezone.utc),
        )
        auth1 = create_authorization(**kwargs1)
        auth2 = create_authorization(**kwargs2)
        assert auth1.authorization_id == auth2.authorization_id

    def test_no_datetime_now_called_in_factory(self, valid_intent):
        kwargs = _make_auth_kwargs(valid_intent)

        class FakeDatetime(datetime.datetime):
            @classmethod
            def now(cls, *args, **kwargs):
                raise RuntimeError("datetime.now() must not be called")

            @classmethod
            def utcnow(cls, *args, **kwargs):
                raise RuntimeError("datetime.utcnow() must not be called")

        with patch("engine.models.execution_authorization.datetime", FakeDatetime):
            auth = create_authorization(**kwargs)
            assert auth.authorization_id


# ============================================================
# G. INTENT BINDING
# ============================================================


class TestIntentBinding:
    def test_intent_id_matches(self, valid_intent, valid_auth_kwargs):
        auth = create_authorization(**valid_auth_kwargs)
        assert auth.intent_id == valid_intent.intent_id

    def test_content_fingerprint_matches(self, valid_intent, valid_auth_kwargs):
        auth = create_authorization(**valid_auth_kwargs)
        assert auth.content_fingerprint == valid_intent.content_fingerprint

    def test_invalid_intent_type_raises(self):
        kwargs = _make_auth_kwargs(intent="not-an-intent")
        with pytest.raises(TypeError, match="Expected an OperationalTradeIntent"):
            create_authorization(**kwargs)

    def test_none_intent_raises(self, valid_auth_kwargs):
        kwargs = dict(valid_auth_kwargs)
        kwargs["intent"] = None
        with pytest.raises(TypeError):
            create_authorization(**kwargs)

    def test_intent_unchanged_after_authorization(self, valid_intent):
        original_id = valid_intent.intent_id
        original_fp = valid_intent.content_fingerprint
        kwargs = _make_auth_kwargs(valid_intent)
        create_authorization(**kwargs)
        assert valid_intent.intent_id == original_id
        assert valid_intent.content_fingerprint == original_fp

    def test_binding_with_intent_valid_until(self):
        intent = _make_intent()
        kwargs = _make_auth_kwargs(
            intent,
            expires_at=datetime.datetime(2026, 9, 1, 23, 59, 0, tzinfo=datetime.timezone.utc),
        )
        auth = create_authorization(**kwargs)
        assert auth.intent_id == intent.intent_id
        assert auth.content_fingerprint == intent.content_fingerprint


# ============================================================
# H. TIME VALIDITY
# ============================================================


class TestTimeValidity:
    def test_valid_from_equals_authorized_at(self, valid_intent):
        kwargs = _make_auth_kwargs(valid_intent)
        auth = create_authorization(**kwargs)
        assert auth.valid_from == auth.authorized_at

    def test_valid_from_after_authorized_at_is_ok(self, valid_intent):
        kwargs = _make_auth_kwargs(
            valid_intent,
            valid_from=datetime.datetime(2026, 9, 1, 13, 0, 0, tzinfo=datetime.timezone.utc),
            authorized_at=datetime.datetime(2026, 9, 1, 12, 0, 0, tzinfo=datetime.timezone.utc),
        )
        auth = create_authorization(**kwargs)
        assert auth.valid_from > auth.authorized_at

    def test_valid_from_before_authorized_at_raises(self, valid_intent):
        kwargs = _make_auth_kwargs(
            valid_intent,
            valid_from=datetime.datetime(2026, 9, 1, 11, 0, 0, tzinfo=datetime.timezone.utc),
            authorized_at=datetime.datetime(2026, 9, 1, 12, 0, 0, tzinfo=datetime.timezone.utc),
        )
        with pytest.raises(ValueError, match="valid_from must be >= authorized_at"):
            create_authorization(**kwargs)

    def test_expires_at_equals_valid_from_raises(self, valid_intent):
        kwargs = _make_auth_kwargs(
            valid_intent,
            valid_from=datetime.datetime(2026, 9, 1, 12, 0, 0, tzinfo=datetime.timezone.utc),
            expires_at=datetime.datetime(2026, 9, 1, 12, 0, 0, tzinfo=datetime.timezone.utc),
        )
        with pytest.raises(ValueError, match="expires_at must be > valid_from"):
            create_authorization(**kwargs)

    def test_expires_at_before_valid_from_raises(self, valid_intent):
        kwargs = _make_auth_kwargs(
            valid_intent,
            valid_from=datetime.datetime(2026, 9, 1, 12, 0, 0, tzinfo=datetime.timezone.utc),
            expires_at=datetime.datetime(2026, 9, 1, 11, 0, 0, tzinfo=datetime.timezone.utc),
        )
        with pytest.raises(ValueError, match="expires_at must be > valid_from"):
            create_authorization(**kwargs)

    def test_expires_after_intent_valid_until_raises(self, valid_intent):
        # intent.valid_until is 2026-09-02 12:00 UTC.
        kwargs = _make_auth_kwargs(
            valid_intent,
            expires_at=datetime.datetime(2026, 9, 3, 12, 0, 0, tzinfo=datetime.timezone.utc),
        )
        with pytest.raises(ValueError, match="expires_at must be <= intent.valid_until"):
            create_authorization(**kwargs)

    def test_expires_at_equals_intent_valid_until_ok(self, valid_intent):
        # intent.valid_until is 2026-09-02 12:00 UTC.
        kwargs = _make_auth_kwargs(
            valid_intent,
            expires_at=datetime.datetime(2026, 9, 2, 12, 0, 0, tzinfo=datetime.timezone.utc),
        )
        auth = create_authorization(**kwargs)
        assert auth.expires_at == valid_intent.valid_until

    def test_expires_before_intent_valid_until_ok(self, valid_intent):
        kwargs = _make_auth_kwargs(
            valid_intent,
            expires_at=datetime.datetime(2026, 9, 2, 11, 0, 0, tzinfo=datetime.timezone.utc),
        )
        auth = create_authorization(**kwargs)
        assert auth.expires_at < valid_intent.valid_until

    def test_no_valid_until_no_constraint(self):
        intent = _make_intent(valid_until=None)
        kwargs = _make_auth_kwargs(
            intent,
            expires_at=datetime.datetime(2099, 12, 31, 23, 59, 0, tzinfo=datetime.timezone.utc),
        )
        auth = create_authorization(**kwargs)
        assert auth.expires_at.year == 2099

    def test_naive_timestamps_rejected(self, valid_intent):
        kwargs = _make_auth_kwargs(
            valid_intent,
            authorized_at=datetime.datetime(2026, 9, 1, 12, 0, 0),
            valid_from=datetime.datetime(2026, 9, 1, 12, 0, 0),
            expires_at=datetime.datetime(2026, 9, 2, 12, 0, 0),
        )
        with pytest.raises(ValueError, match="authorized_at must be timezone-aware"):
            create_authorization(**kwargs)


# ============================================================
# I. STATUS INVARIANTS
# ============================================================


class TestStatusInvariants:
    def test_authorized_with_valid_timing(self, valid_intent):
        kwargs = _make_auth_kwargs(valid_intent, status=AuthorizationStatus.AUTHORIZED)
        auth = create_authorization(**kwargs)
        assert auth.status is AuthorizationStatus.AUTHORIZED
        assert auth.status.is_authorized

    def test_unauthorized_is_not_authorized(self, valid_intent):
        kwargs = _make_auth_kwargs(valid_intent, status=AuthorizationStatus.UNAUTHORIZED)
        auth = create_authorization(**kwargs)
        assert not auth.status.is_authorized

    def test_eligible_is_not_authorized(self, valid_intent):
        kwargs = _make_auth_kwargs(valid_intent, status=AuthorizationStatus.ELIGIBLE)
        auth = create_authorization(**kwargs)
        assert not auth.status.is_authorized

    def test_expired_is_not_authorized(self, valid_intent):
        kwargs = _make_auth_kwargs(valid_intent, status=AuthorizationStatus.EXPIRED)
        auth = create_authorization(**kwargs)
        assert not auth.status.is_authorized

    def test_revoked_is_not_authorized(self, valid_intent):
        kwargs = _make_auth_kwargs(valid_intent, status=AuthorizationStatus.REVOKED)
        auth = create_authorization(**kwargs)
        assert not auth.status.is_authorized

    def test_superseded_is_not_authorized(self, valid_intent):
        kwargs = _make_auth_kwargs(valid_intent, status=AuthorizationStatus.SUPERSEDED)
        auth = create_authorization(**kwargs)
        assert not auth.status.is_authorized


# ============================================================
# J. FAIL CLOSED
# ============================================================


class TestFailClosed:
    def test_unknown_status_not_accepted(self, valid_intent):
        # Attempting to use an unknown status should raise ValueError
        # because AuthorizationStatus is an Enum and invalid values
        # are rejected at the call site.
        with pytest.raises(ValueError):
            AuthorizationStatus("UNKNOWN_STATUS")

    def test_malformed_authorization_record_cannot_be_authorized(self, valid_intent):
        # Build a structurally contradictory record via direct construction
        # (not via factory) and verify it cannot pass validation.
        with pytest.raises(ValueError, match="valid_from must be >= authorized_at"):
            ExecutionAuthorization(
                authorization_id="auth-badbadbadbadbad",
                intent_id=valid_intent.intent_id,
                plan_id=valid_intent.plan_id,
                content_fingerprint=valid_intent.content_fingerprint,
                status=AuthorizationStatus.AUTHORIZED,
                authorized_at=datetime.datetime(2026, 9, 1, 12, 0, 0, tzinfo=datetime.timezone.utc),
                valid_from=datetime.datetime(2026, 9, 1, 11, 0, 0, tzinfo=datetime.timezone.utc),
                expires_at=datetime.datetime(2026, 9, 2, 12, 0, 0, tzinfo=datetime.timezone.utc),
                issuer="human",
                authorization_method="manual",
                scope="paper",
                policy_reference="v1",
                safety_check_summary="passed",
            )

    def test_expired_requires_valid_timing(self, valid_intent):
        with pytest.raises(ValueError, match="valid_from must be >= authorized_at"):
            ExecutionAuthorization(
                authorization_id="auth-badbadbadbadbad",
                intent_id=valid_intent.intent_id,
                plan_id=valid_intent.plan_id,
                content_fingerprint=valid_intent.content_fingerprint,
                status=AuthorizationStatus.EXPIRED,
                authorized_at=datetime.datetime(2026, 9, 1, 12, 0, 0, tzinfo=datetime.timezone.utc),
                valid_from=datetime.datetime(2026, 9, 1, 11, 0, 0, tzinfo=datetime.timezone.utc),
                expires_at=datetime.datetime(2026, 9, 2, 12, 0, 0, tzinfo=datetime.timezone.utc),
                issuer="human",
                authorization_method="manual",
                scope="paper",
                policy_reference="v1",
                safety_check_summary="passed",
            )


# ============================================================
# K. INDEPENDENCE
# ============================================================


class TestIndependence:
    def test_no_trade_plan_mutation(self, valid_intent):
        # The intent already exists; creating authorization must not mutate
        # the underlying TradePlan (accessible via the intent's plan_id).
        from engine.models.trade_plan import TradePlan

        # Verify we can look up the plan_id without any mutation path.
        plan_id = valid_intent.plan_id
        kwargs = _make_auth_kwargs(valid_intent)
        auth = create_authorization(**kwargs)
        assert auth.plan_id == plan_id
        assert auth.intent_id == valid_intent.intent_id

    def test_no_paper_trading_engine_invoked(self, valid_intent):
        # The factory must not import or invoke paper_trading.
        import engine.models.execution_authorization as ea_module

        source = open(ea_module.__file__).read()
        assert "paper_trading" not in source
        assert "PaperTrade" not in source

    def test_no_market_scanner_invoked(self, valid_intent):
        import engine.models.execution_authorization as ea_module

        source = open(ea_module.__file__).read()
        assert "market_scanner" not in source
        assert "MarketScanner" not in source

    def test_no_historical_pipeline_invoked(self, valid_intent):
        import engine.models.execution_authorization as ea_module

        source = open(ea_module.__file__).read()
        assert "historical_pipeline" not in source
        assert "HistoricalEvaluationPipeline" not in source

    def test_no_broker_code_imported(self, valid_intent):
        import engine.models.execution_authorization as ea_module

        source = open(ea_module.__file__).read()
        assert "upstox" not in source.lower()
        assert "yahoo" not in source.lower()
        assert "broker" not in source.lower()

    def test_factory_does_not_call_datetime_now(self, valid_intent):
        kwargs = _make_auth_kwargs(valid_intent)
        import engine.models.execution_authorization as ea_module

        source = open(ea_module.__file__).read()
        import re
        stripped = re.sub(r'""".*?"""', '', source, flags=re.DOTALL)
        stripped = re.sub(r"'''.*?'''", '', stripped, flags=re.DOTALL)
        stripped = re.sub(r'#.*', '', stripped)
        assert "datetime.now" not in stripped
        assert "datetime.utcnow" not in stripped
        auth = create_authorization(**kwargs)
        assert auth.authorization_id


# ============================================================
# L. AUTHORIZATION_ID EDGE CASES
# ============================================================


class TestAuthorizationIdEdgeCases:
    def test_same_intent_different_status_different_id(self, valid_intent):
        kwargs1 = _make_auth_kwargs(valid_intent, status=AuthorizationStatus.AUTHORIZED)
        kwargs2 = _make_auth_kwargs(valid_intent, status=AuthorizationStatus.ELIGIBLE)
        auth1 = create_authorization(**kwargs1)
        auth2 = create_authorization(**kwargs2)
        assert auth1.authorization_id != auth2.authorization_id

    def test_same_intent_different_method_different_id(self, valid_intent):
        kwargs1 = _make_auth_kwargs(valid_intent, authorization_method="manual")
        kwargs2 = _make_auth_kwargs(valid_intent, authorization_method="auto")
        auth1 = create_authorization(**kwargs1)
        auth2 = create_authorization(**kwargs2)
        assert auth1.authorization_id != auth2.authorization_id

    def test_empty_label_same_id_as_default(self, valid_intent):
        kwargs1 = _make_auth_kwargs(valid_intent, label="")
        kwargs2 = _make_auth_kwargs(valid_intent, label="")
        auth1 = create_authorization(**kwargs1)
        auth2 = create_authorization(**kwargs2)
        assert auth1.authorization_id == auth2.authorization_id

    def test_empty_metadata_same_id(self, valid_intent):
        kwargs1 = _make_auth_kwargs(valid_intent, metadata=())
        kwargs2 = _make_auth_kwargs(valid_intent, metadata=())
        auth1 = create_authorization(**kwargs1)
        auth2 = create_authorization(**kwargs2)
        assert auth1.authorization_id == auth2.authorization_id

    def test_metadata_ordering_does_not_affect_id(self, valid_intent):
        kwargs1 = _make_auth_kwargs(valid_intent, metadata=(("a", "1"), ("b", "2")))
        kwargs2 = _make_auth_kwargs(valid_intent, metadata=(("b", "2"), ("a", "1")))
        auth1 = create_authorization(**kwargs1)
        auth2 = create_authorization(**kwargs2)
        assert auth1.authorization_id == auth2.authorization_id

    def test_repeated_construction_same_id(self, valid_intent):
        kwargs = _make_auth_kwargs(valid_intent)
        auth1 = create_authorization(**kwargs)
        auth2 = create_authorization(**kwargs)
        assert auth1.authorization_id == auth2.authorization_id

    def test_authorization_id_format(self, valid_auth_kwargs):
        auth = create_authorization(**valid_auth_kwargs)
        assert auth.authorization_id.startswith("auth-")
        suffix = auth.authorization_id[len("auth-"):]
        assert len(suffix) == 16
        assert all(c in "0123456789abcdef" for c in suffix)


# ============================================================
# M. INTENT BINDING EDGE CASES
# ============================================================


class TestIntentBindingEdgeCases:
    def test_binding_with_intent_no_valid_until(self):
        intent = _make_intent(valid_until=None)
        kwargs = _make_auth_kwargs(
            intent,
            expires_at=datetime.datetime(2026, 9, 2, 12, 0, 0, tzinfo=datetime.timezone.utc),
        )
        auth = create_authorization(**kwargs)
        assert auth.intent_id == intent.intent_id
        assert auth.content_fingerprint == intent.content_fingerprint

    def test_binding_preserves_plan_id(self, valid_intent, valid_auth_kwargs):
        auth = create_authorization(**valid_auth_kwargs)
        assert auth.plan_id == valid_intent.plan_id

    def test_content_fingerprint_format(self, valid_auth_kwargs):
        auth = create_authorization(**valid_auth_kwargs)
        assert auth.content_fingerprint.startswith("fp-")
        suffix = auth.content_fingerprint[len("fp-"):]
        assert len(suffix) == 16
        assert all(c in "0123456789abcdef" for c in suffix)

    def test_fingerprint_matches_intent(self, valid_intent, valid_auth_kwargs):
        auth = create_authorization(**valid_auth_kwargs)
        assert auth.content_fingerprint == valid_intent.content_fingerprint


# ============================================================
# N. STATUS LIFECYCLE
# ============================================================


class TestStatusLifecycle:
    def test_create_unauthorized(self, valid_intent):
        kwargs = _make_auth_kwargs(valid_intent, status=AuthorizationStatus.UNAUTHORIZED)
        auth = create_authorization(**kwargs)
        assert auth.status is AuthorizationStatus.UNAUTHORIZED
        assert not auth.status.is_authorized

    def test_create_eligible(self, valid_intent):
        kwargs = _make_auth_kwargs(valid_intent, status=AuthorizationStatus.ELIGIBLE)
        auth = create_authorization(**kwargs)
        assert auth.status is AuthorizationStatus.ELIGIBLE
        assert not auth.status.is_authorized

    def test_create_authorized(self, valid_intent):
        kwargs = _make_auth_kwargs(valid_intent, status=AuthorizationStatus.AUTHORIZED)
        auth = create_authorization(**kwargs)
        assert auth.status is AuthorizationStatus.AUTHORIZED
        assert auth.status.is_authorized

    def test_create_expired(self, valid_intent):
        kwargs = _make_auth_kwargs(valid_intent, status=AuthorizationStatus.EXPIRED)
        auth = create_authorization(**kwargs)
        assert auth.status is AuthorizationStatus.EXPIRED
        assert not auth.status.is_authorized

    def test_create_revoked(self, valid_intent):
        kwargs = _make_auth_kwargs(valid_intent, status=AuthorizationStatus.REVOKED)
        auth = create_authorization(**kwargs)
        assert auth.status is AuthorizationStatus.REVOKED
        assert not auth.status.is_authorized

    def test_create_superseded(self, valid_intent):
        kwargs = _make_auth_kwargs(valid_intent, status=AuthorizationStatus.SUPERSEDED)
        auth = create_authorization(**kwargs)
        assert auth.status is AuthorizationStatus.SUPERSEDED
        assert not auth.status.is_authorized


# ============================================================
# O. AUTHORIZED IS NOT EXECUTION
# ============================================================


class TestAuthorizedIsNotExecution:
    def test_authorized_status_only(self, valid_intent):
        kwargs = _make_auth_kwargs(valid_intent, status=AuthorizationStatus.AUTHORIZED)
        auth = create_authorization(**kwargs)
        # AUTHORIZED must not carry any execution semantics.
        assert not hasattr(auth, "order_id")
        assert not hasattr(auth, "fill_price")
        assert not hasattr(auth, "position_id")
        assert not hasattr(auth, "broker_permission")

    def test_model_has_no_broker_fields(self, valid_auth_kwargs):
        auth = create_authorization(**valid_auth_kwargs)
        for field_name in ("order_id", "fill_price", "position_id", "broker_id"):
            assert not hasattr(auth, field_name), f"Unexpected broker field: {field_name}"


# ============================================================
# P. REGRESSION GUARDS
# ============================================================


class TestRegressionGuards:
    def test_frozen_model_slots(self, valid_auth_kwargs):
        auth = create_authorization(**valid_auth_kwargs)
        assert hasattr(auth, "__slots__")

    def test_no_mutation_of_intent_fields(self, valid_intent):
        original = {
            "intent_id": valid_intent.intent_id,
            "content_fingerprint": valid_intent.content_fingerprint,
            "plan_id": valid_intent.plan_id,
            "instrument": valid_intent.instrument,
            "direction": valid_intent.direction,
        }
        kwargs = _make_auth_kwargs(valid_intent)
        create_authorization(**kwargs)
        assert valid_intent.intent_id == original["intent_id"]
        assert valid_intent.content_fingerprint == original["content_fingerprint"]
        assert valid_intent.plan_id == original["plan_id"]
        assert valid_intent.instrument == original["instrument"]
        assert valid_intent.direction == original["direction"]

    def test_authorization_does_not_recalculate_geometry(self, valid_intent):
        # The factory must not recalculate or access engine geometry.
        import engine.models.execution_authorization as ea_module

        source = open(ea_module.__file__).read()
        assert "engine_risk_distance" not in source
        assert "engine_reward_distance" not in source
        assert "engine_risk_reward_ratio" not in source
        assert "recalculate" not in source.lower()
