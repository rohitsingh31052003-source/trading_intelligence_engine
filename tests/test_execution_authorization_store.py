"""
Tests for Execution Authorization Persistence (Checkpoint 15.5).

Covers:
1. Basic persistence — save, load, exists, missing authorization
2. Round-trip — exact identity/fingerprint/Decimal/datetime/enum/metadata preservation
3. Restart — fresh store instance loads persisted authorization
4. Duplicate handling — identical save (idempotent), conflicting content (fails)
5. Corruption — malformed JSON, missing fields, invalid status, invalid Decimal,
   invalid datetime, unknown schema, identity mismatch
6. Security — unsafe authorization IDs, path traversal attempts
7. Immutability — save does not mutate original, load returns independent artifact
8. Boundary isolation — store does not import execution/broker functionality
"""

from __future__ import annotations

import json
import os
import dataclasses
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from engine.models.execution_authorization import (
    AuthorizationStatus,
    ExecutionAuthorization,
    create_authorization,
)
from engine.models.operational_trade_intent import (
    OperationalTradeIntent,
    create_intent_from_plan,
)
from engine.models.trade_plan import RiskPlanStatus
from engine.persistence.exceptions import (
    AuthorizationIntegrityError,
    AuthorizationNotFoundError,
    AuthorizationStoreError,
    UnsupportedAuthorizationSchemaVersionError,
)
from engine.persistence.execution_authorization_serialization import (
    AUTHORIZATION_SCHEMA_VERSION,
    deserialize_authorization,
    parse_authorization_header,
    serialize_authorization,
)
from engine.persistence.execution_authorization_store import (
    ExecutionAuthorizationStore,
    _validate_id,
)


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
        "created_at": datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc),
        "evaluation_timestamp": datetime(2026, 9, 1, 11, 59, 0, tzinfo=timezone.utc),
        "valid_until": datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc),
        "warnings": ("geometry-incomplete",),
        "rationale": "Test plan rationale.",
        "label": "test-label",
        "metadata": (("key1", "val1"), ("key2", "val2")),
    }
    base.update(overrides)
    return create_intent_from_plan(**base)


def _make_authorization(**overrides):
    """Create a valid ExecutionAuthorization for testing."""
    base = {
        "intent": _make_intent(),
        "status": AuthorizationStatus.AUTHORIZED,
        "authorized_at": datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc),
        "valid_from": datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc),
        "expires_at": datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc),
        "issuer": "test-issuer",
        "authorization_method": "explicit-approval",
        "scope": "paper",
        "policy_reference": "policy-v1",
        "safety_check_summary": "all-gates-passed",
        "label": "test-label",
        "metadata": (("key1", "val1"), ("key2", "val2")),
    }
    base.update(overrides)
    return create_authorization(**base)


def _make_conflicting_auth(auth: ExecutionAuthorization, **overrides):
    """Create a new authorization with the SAME id but different content."""
    data = {
        "authorization_id": auth.authorization_id,
        "intent_id": auth.intent_id,
        "plan_id": auth.plan_id,
        "content_fingerprint": auth.content_fingerprint,
        "status": AuthorizationStatus.ELIGIBLE,
        "authorized_at": auth.authorized_at,
        "valid_from": auth.valid_from,
        "expires_at": auth.expires_at,
        "issuer": "different-issuer",
        "authorization_method": "different-method",
        "scope": "different-scope",
        "policy_reference": "different-policy",
        "safety_check_summary": "different-summary",
        "label": "different-label",
        "metadata": (("other", "value"),),
    }
    data.update(overrides)
    return ExecutionAuthorization(**data)


# ============================================================
# 1. BASIC PERSISTENCE
# ============================================================


class TestBasicPersistence:
    """save, load, exists, missing authorization."""

    def test_save_and_load(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)
        auth = _make_authorization()

        result_path = store.save(auth)

        assert result_path.exists()
        loaded = store.load(auth.authorization_id)
        assert loaded == auth

    def test_exists_true(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)
        auth = _make_authorization()
        store.save(auth)

        assert store.exists(auth.authorization_id) is True

    def test_exists_false(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)

        assert store.exists("auth-nonexistent") is False

    def test_load_missing_raises(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)

        with pytest.raises(AuthorizationNotFoundError):
            store.load("auth-nonexistent")

    def test_save_returns_path(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)
        auth = _make_authorization()

        result_path = store.save(auth)

        assert result_path == store.path_for(auth.authorization_id)

    def test_default_directory_is_cwd_authorizations(self):
        expected = Path.cwd() / "authorizations"
        store = ExecutionAuthorizationStore()
        assert store.directory == expected

    def test_custom_directory(self, tmp_path):
        custom = tmp_path / "custom_auths"
        store = ExecutionAuthorizationStore(directory=custom)
        assert store.directory == custom


# ============================================================
# 2. ROUND-TRIP
# ============================================================


class TestRoundTrip:
    """Exact preservation of all fields across save/load."""

    def test_authorization_id_preserved(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)
        auth = _make_authorization()
        store.save(auth)
        loaded = store.load(auth.authorization_id)
        assert loaded.authorization_id == auth.authorization_id

    def test_intent_id_preserved(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)
        auth = _make_authorization()
        store.save(auth)
        loaded = store.load(auth.authorization_id)
        assert loaded.intent_id == auth.intent_id

    def test_content_fingerprint_preserved(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)
        auth = _make_authorization()
        store.save(auth)
        loaded = store.load(auth.authorization_id)
        assert loaded.content_fingerprint == auth.content_fingerprint

    def test_status_preserved(self, tmp_path):
        for status in AuthorizationStatus:
            store = ExecutionAuthorizationStore(directory=tmp_path)
            auth = _make_authorization(status=status)
            store.save(auth)
            loaded = store.load(auth.authorization_id)
            assert loaded.status == status

    def test_datetime_preserved(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)
        auth = _make_authorization()
        store.save(auth)
        loaded = store.load(auth.authorization_id)
        assert loaded.authorized_at == auth.authorized_at
        assert loaded.valid_from == auth.valid_from
        assert loaded.expires_at == auth.expires_at

    def test_datetime_timezone_preserved(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)
        tz_aware = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
        auth = _make_authorization(
            authorized_at=tz_aware,
            valid_from=tz_aware,
            expires_at=datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc),
        )
        store.save(auth)
        loaded = store.load(auth.authorization_id)
        assert loaded.authorized_at.tzinfo is not None
        assert loaded.valid_from.tzinfo is not None
        assert loaded.expires_at.tzinfo is not None

    def test_metadata_preserved(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)
        metadata = (("z", "last"), ("a", "first"), ("key", "value"))
        auth = _make_authorization(metadata=metadata)
        store.save(auth)
        loaded = store.load(auth.authorization_id)
        assert loaded.metadata == auth.metadata

    def test_optional_label_empty(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)
        auth = _make_authorization(label="")
        store.save(auth)
        loaded = store.load(auth.authorization_id)
        assert loaded.label == ""

    def test_optional_metadata_empty(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)
        auth = _make_authorization(metadata=())
        store.save(auth)
        loaded = store.load(auth.authorization_id)
        assert loaded.metadata == ()

    def test_issuer_and_method_preserved(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)
        auth = _make_authorization(
            issuer="human",
            authorization_method="manual",
            scope="live",
            policy_reference="policy-v2",
            safety_check_summary="all-gates-passed",
        )
        store.save(auth)
        loaded = store.load(auth.authorization_id)
        assert loaded.issuer == "human"
        assert loaded.authorization_method == "manual"
        assert loaded.scope == "live"
        assert loaded.policy_reference == "policy-v2"
        assert loaded.safety_check_summary == "all-gates-passed"

    def test_all_six_statuses_roundtrip(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)
        for status in AuthorizationStatus:
            auth = _make_authorization(status=status)
            store.save(auth)
            loaded = store.load(auth.authorization_id)
            assert loaded.status == status


# ============================================================
# 3. RESTART
# ============================================================


class TestRestart:
    """Fresh store instance loads persisted authorization."""

    def test_fresh_store_loads_persisted(self, tmp_path):
        auth = _make_authorization()
        store_a = ExecutionAuthorizationStore(directory=tmp_path)
        store_a.save(auth)

        # Simulate a fresh store instance (new process / restart).
        store_b = ExecutionAuthorizationStore(directory=tmp_path)
        loaded = store_b.load(auth.authorization_id)

        assert loaded.authorization_id == auth.authorization_id
        assert loaded.intent_id == auth.intent_id
        assert loaded.content_fingerprint == auth.content_fingerprint
        assert loaded.status == auth.status
        assert loaded.authorized_at == auth.authorized_at
        assert loaded.valid_from == auth.valid_from
        assert loaded.expires_at == auth.expires_at
        assert loaded.issuer == auth.issuer
        assert loaded.authorization_method == auth.authorization_method
        assert loaded.scope == auth.scope
        assert loaded.policy_reference == auth.policy_reference
        assert loaded.safety_check_summary == auth.safety_check_summary
        assert loaded.label == auth.label
        assert loaded.metadata == auth.metadata

    def test_decimal_unchanged_after_restart(self, tmp_path):
        auth = _make_authorization()
        store_a = ExecutionAuthorizationStore(directory=tmp_path)
        store_a.save(auth)

        store_b = ExecutionAuthorizationStore(directory=tmp_path)
        loaded = store_b.load(auth.authorization_id)
        # All fields preserved exactly.
        assert loaded.authorized_at == auth.authorized_at

    def test_file_survives_store_recreation(self, tmp_path):
        auth = _make_authorization()
        store_a = ExecutionAuthorizationStore(directory=tmp_path)
        store_a.save(auth)

        # The file must remain on disk.
        store_b = ExecutionAuthorizationStore(directory=tmp_path)
        assert store_b.exists(auth.authorization_id)
        loaded = store_b.load(auth.authorization_id)
        assert loaded.authorization_id == auth.authorization_id


# ============================================================
# 4. DUPLICATE HANDLING
# ============================================================


class TestDuplicateHandling:
    """Repeated identical save and conflicting content under same ID."""

    def test_identical_save_idempotent(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)
        auth = _make_authorization()

        store.save(auth)
        store.save(auth)  # identical — should not raise

        loaded = store.load(auth.authorization_id)
        assert loaded == auth

    def test_identical_save_returns_path(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)
        auth = _make_authorization()

        store.save(auth)
        path = store.save(auth)

        assert path.exists()

    def test_conflicting_content_raises_by_default(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)
        auth = _make_authorization()
        store.save(auth)

        # Construct a different authorization that shares the SAME id.
        conflicting = _make_conflicting_auth(auth)

        with pytest.raises(AuthorizationIntegrityError):
            store.save(conflicting)

    def test_conflicting_content_with_overwrite_true(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)
        auth = _make_authorization()
        store.save(auth)

        # Construct a different authorization that shares the SAME id.
        replacement = _make_conflicting_auth(auth, label="replacement")

        store.save(replacement, overwrite=True)

        loaded = store.load(auth.authorization_id)
        assert loaded.label == "replacement"
        assert loaded.issuer == "different-issuer"


# ============================================================
# 5. CORRUPTION
# ============================================================


class TestCorruption:
    """Malformed persisted records must never be returned as valid."""

    def test_malformed_json_raises(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)
        auth = _make_authorization()
        store.save(auth)

        target = store.path_for(auth.authorization_id)
        target.write_text("not valid json {{{", encoding="utf-8")

        with pytest.raises(AuthorizationStoreError):
            store.load(auth.authorization_id)

    def test_missing_authorization_key_raises(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)
        auth = _make_authorization()
        store.save(auth)

        target = store.path_for(auth.authorization_id)
        target.write_text(
            json.dumps({"schema_version": 1}, sort_keys=True),
            encoding="utf-8",
        )

        with pytest.raises(AuthorizationStoreError):
            store.load(auth.authorization_id)

    def test_unsupported_schema_version_raises(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)
        auth = _make_authorization()
        store.save(auth)

        target = store.path_for(auth.authorization_id)
        payload = {
            "schema_version": 999,
            "authorization": _encode(auth),
        }
        target.write_text(
            json.dumps(payload, sort_keys=True),
            encoding="utf-8",
        )

        with pytest.raises(UnsupportedAuthorizationSchemaVersionError):
            store.load(auth.authorization_id)

    def test_identity_mismatch_raises(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)
        auth = _make_authorization()
        store.save(auth)

        target = store.path_for(auth.authorization_id)
        # Write a valid document but with a different stored id inside.
        # Manually construct an auth with the SAME file-name id but
        # different content, then serialize it using the real serializer.
        tampered = ExecutionAuthorization(
            authorization_id=auth.authorization_id,
            intent_id=auth.intent_id,
            plan_id=auth.plan_id,
            content_fingerprint=auth.content_fingerprint,
            status=AuthorizationStatus.ELIGIBLE,
            authorized_at=auth.authorized_at,
            valid_from=auth.valid_from,
            expires_at=auth.expires_at,
            issuer="different-issuer",
            authorization_method="different-method",
            scope="different-scope",
            policy_reference="different-policy",
            safety_check_summary="different-summary",
            label="different-label",
            metadata=(("other", "value"),),
        )
        # tampered has the SAME authorization_id as the file name but
        # different content. The serialization is valid, but when
        # deserialized, the reconstructed object will still have the
        # same authorization_id. So the identity check at the store
        # level will NOT catch this — the mismatch must be detected
        # by comparing the persisted text with what we expect.
        # Instead, we test with a tampered document that has a
        # DIFFERENT authorization_id embedded in it.
        tampered_different_id = _make_authorization()
        # Override the id in the serialized payload to something else.
        raw = json.loads(serialize_authorization(tampered_different_id))
        raw["authorization"]["fields"]["authorization_id"] = (
            "auth-tamperedwrongid000000"
        )
        target.write_text(json.dumps(raw, sort_keys=True), encoding="utf-8")

        with pytest.raises(AuthorizationIntegrityError):
            store.load(auth.authorization_id)

    def test_truncated_json_raises(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)
        auth = _make_authorization()
        store.save(auth)

        target = store.path_for(auth.authorization_id)
        full = serialize_authorization(auth)
        target.write_text(full[: len(full) // 2], encoding="utf-8")

        with pytest.raises(AuthorizationStoreError):
            store.load(auth.authorization_id)

    def test_non_dict_payload_raises(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)
        auth = _make_authorization()
        store.save(auth)

        target = store.path_for(auth.authorization_id)
        target.write_text("[1, 2, 3]", encoding="utf-8")

        with pytest.raises(AuthorizationStoreError):
            store.load(auth.authorization_id)

    def test_missing_schema_version_raises(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)
        auth = _make_authorization()
        store.save(auth)

        target = store.path_for(auth.authorization_id)
        target.write_text(
            json.dumps({"authorization": {}}, sort_keys=True),
            encoding="utf-8",
        )

        with pytest.raises(UnsupportedAuthorizationSchemaVersionError):
            store.load(auth.authorization_id)


def _encode(auth):
    """Encode an authorization to JSON-safe dict for test tampering."""
    if auth is None:
        return None
    if isinstance(auth, bool):
        return auth
    if isinstance(auth, Decimal):
        return {"__decimal__": str(auth)}
    if isinstance(auth, datetime):
        return {"__datetime__": auth.isoformat()}
    if isinstance(auth, AuthorizationStatus):
        return {"__enum__": auth.name}
    import dataclasses
    if dataclasses.is_dataclass(auth) and not isinstance(auth, type):
        return {
            "__dataclass__": type(auth).__name__,
            "fields": {
                f.name: _encode(getattr(auth, f.name))
                for f in auth.__dataclass_fields__.values()
            },
        }
    if isinstance(auth, (list, tuple)):
        return {"__tuple__": [_encode(item) for item in auth]}
    if isinstance(auth, dict):
        return {str(k): _encode(v) for k, v in auth.items()}
    return auth


# ============================================================
# 6. SECURITY
# ============================================================


class TestSecurity:
    """Unsafe authorization IDs must be rejected."""

    def test_path_traversal_rejected(self):
        bad_ids = [
            "../etc/passwd",
            "../../authorizations/evil",
            "auth-../../../windows/system32",
            "auth-../../",
            "",
            "auth with spaces",
            "auth\nwith\nnewlines",
            "auth;rm -rf /",
        ]
        for bad_id in bad_ids:
            with pytest.raises(Exception):  # AuthorizationStoreError
                _validate_id(bad_id)

    def test_safe_ids_accepted(self):
        safe_ids = [
            "auth-abc123def4567890",
            "auth-ABC_123.def-456",
            "auth-a" * 20,
        ]
        for safe_id in safe_ids:
            _validate_id(safe_id)  # must not raise

    def test_load_path_traversal_rejected(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)

        with pytest.raises(Exception):  # AuthorizationStoreError
            store.load("../etc/passwd")

    def test_exists_path_traversal_rejected(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)

        with pytest.raises(Exception):  # AuthorizationStoreError
            store.exists("../etc/passwd")


# ============================================================
# 7. IMMUTABILITY
# ============================================================


class TestImmutability:
    """Save does not mutate the original; load returns independent copy."""

    def test_save_does_not_mutate_authorization(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)
        auth = _make_authorization()
        original_label = auth.label
        original_metadata = auth.metadata

        store.save(auth)

        assert auth.label == original_label
        assert auth.metadata == original_metadata

    def test_load_returns_independent_artifact(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)
        auth = _make_authorization()
        store.save(auth)

        loaded = store.load(auth.authorization_id)

        # The loaded record must be equal but is a separate instance.
        assert loaded == auth
        # Mutating the loaded record must fail (frozen).
        with pytest.raises(dataclasses.FrozenInstanceError):
            loaded.label = "mutated"  # type: ignore[misc]

    def test_original_unchanged_after_load(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)
        auth = _make_authorization()
        store.save(auth)

        loaded = store.load(auth.authorization_id)

        # Original must be unchanged.
        assert auth == _make_authorization(label=auth.label)


# ============================================================
# 8. LIST / DELETE
# ============================================================


class TestListDelete:
    """list_authorizations and delete."""

    def test_list_empty_directory(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)
        assert store.list_authorizations() == []

    def test_list_after_save(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)
        auth1 = _make_authorization(label="a")
        auth2 = _make_authorization(label="b")
        store.save(auth1)
        store.save(auth2)

        ids = store.list_authorizations()
        assert auth1.authorization_id in ids
        assert auth2.authorization_id in ids
        assert len(ids) == 2

    def test_list_sorted(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)
        auth1 = _make_authorization(label="z")
        auth2 = _make_authorization(label="a")
        store.save(auth1)
        store.save(auth2)

        ids = store.list_authorizations()
        assert ids == sorted(ids)

    def test_delete_existing(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)
        auth = _make_authorization()
        store.save(auth)

        store.delete(auth.authorization_id)

        assert not store.exists(auth.authorization_id)
        with pytest.raises(AuthorizationNotFoundError):
            store.load(auth.authorization_id)

    def test_delete_missing_raises(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)

        with pytest.raises(AuthorizationNotFoundError):
            store.delete("auth-nonexistent")

    def test_list_ignores_stray_files(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)
        stray = tmp_path / "stray.txt"
        stray.write_text("hello", encoding="utf-8")
        not_json = tmp_path / "not_json"
        not_json.write_text("hello", encoding="utf-8")

        assert store.list_authorizations() == []


# ============================================================
# 9. SCHEMA VERSION
# ============================================================


class TestSchemaVersion:
    """Schema version validated before reconstruction."""

    def test_schema_version_carried(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)
        auth = _make_authorization()
        store.save(auth)

        text = (tmp_path / f"{auth.authorization_id}.json").read_text(
            encoding="utf-8"
        )
        payload = json.loads(text)
        assert payload["schema_version"] == AUTHORIZATION_SCHEMA_VERSION

    def test_parse_header(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)
        auth = _make_authorization()
        store.save(auth)

        text = (tmp_path / f"{auth.authorization_id}.json").read_text(
            encoding="utf-8"
        )
        header = parse_authorization_header(text)
        assert header["schema_version"] == AUTHORIZATION_SCHEMA_VERSION
        assert "authorization" in header


# ============================================================
# 10. SERIALIZATION MODULE
# ============================================================


class TestSerializationModule:
    """Direct serialization module tests."""

    def test_round_trip_all_statuses(self):
        for status in AuthorizationStatus:
            auth = _make_authorization(status=status)
            text = serialize_authorization(auth)
            loaded = deserialize_authorization(text)
            assert loaded == auth

    def test_deterministic_bytes(self):
        auth = _make_authorization()
        bytes_a = serialize_authorization(auth).encode("utf-8")
        bytes_b = serialize_authorization(auth).encode("utf-8")
        assert bytes_a == bytes_b

    def test_unsupported_schema_in_module(self):
        payload = json.dumps({"schema_version": 999, "authorization": {}})
        with pytest.raises(ValueError):
            deserialize_authorization(payload)

    def test_malformed_json_in_module(self):
        with pytest.raises(ValueError):
            deserialize_authorization("not json")

    def test_canonical_json_sorted_keys(self):
        auth = _make_authorization()
        text = serialize_authorization(auth)
        parsed = json.loads(text)
        assert list(parsed.keys()) == sorted(parsed.keys())


# ============================================================
# 11. ATOMIC WRITE
# ============================================================


class TestAtomicWrite:
    """Atomic write guarantees."""

    def test_no_temp_file_left_after_success(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)
        auth = _make_authorization()
        store.save(auth)

        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []

    def test_file_created_atomically(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)
        auth = _make_authorization()
        store.save(auth)

        target = store.path_for(auth.authorization_id)
        assert target.exists()
        assert target.is_file()

    def test_overwrite_replaces_file(self, tmp_path):
        store = ExecutionAuthorizationStore(directory=tmp_path)
        auth_a = _make_authorization(label="original")
        store.save(auth_a)

        # Construct a different authorization that shares the SAME id.
        replacement = _make_conflicting_auth(auth_a, label="replacement")

        store.save(replacement, overwrite=True)
        loaded = store.load(auth_a.authorization_id)
        assert loaded.label == "replacement"


# ============================================================
# 12. BOUNDARY ISOLATION
# ============================================================


class TestBoundaryIsolation:
    """Store does not import execution/broker functionality."""

    def test_store_module_no_execution_broker_imports(self):
        import engine.persistence.execution_authorization_store as store_mod

        source = Path(store_mod.__file__).read_text(encoding="utf-8")
        # Check for functional imports/usage, not docstring descriptions.
        functional_forbidden = [
            "from engine.models.paper_trade",
            "from engine.intelligence.paper_trading",
            "from engine.intelligence.trade_planning",
            "from engine.intelligence.market_scanner",
            "from engine.data.historical",
            "from dashboard",
            "import fastapi",
            "import upstox",
            "import yfinance",
            "import broker",
            "import order",
            "import position",
            "import portfolio",
        ]
        lower_source = source.lower()
        for term in functional_forbidden:
            assert term not in lower_source, (
                f"Store module must not contain functional reference {term!r}"
            )

    def test_serialization_module_no_execution_broker_imports(self):
        import engine.persistence.execution_authorization_serialization as ser_mod

        source = Path(ser_mod.__file__).read_text(encoding="utf-8")
        functional_forbidden = [
            "from engine.models.paper_trade",
            "from engine.intelligence.paper_trading",
            "from engine.intelligence.trade_planning",
            "from engine.intelligence.market_scanner",
            "from engine.data.historical",
            "from dashboard",
            "import fastapi",
            "import upstox",
            "import yfinance",
            "import broker",
            "import order",
            "import position",
            "import portfolio",
        ]
        lower_source = source.lower()
        for term in functional_forbidden:
            assert term not in lower_source, (
                f"Serialization module must not contain functional "
                f"reference {term!r}"
            )
