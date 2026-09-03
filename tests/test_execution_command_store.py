"""
Tests for Execution Command Persistence (Checkpoint 16.5).

Covers:
1. Basic persistence — save, load, exists, missing command
2. Deterministic filename and serialization
3. Round-trip — exact identity/Decimal/datetime/enum/metadata preservation
4. Restart — fresh store instance loads persisted command
5. Duplicate handling — identical save (idempotent), conflicting content (fails)
6. Corruption — malformed JSON, missing fields, unsupported schema, identity mismatch
7. Security — unsafe command IDs, path traversal attempts
8. Immutability — save does not mutate original, load returns frozen artifact
9. List / delete
10. Schema version
11. Atomic write
12. Boundary isolation — store does not import execution/broker functionality
"""

from __future__ import annotations

import json
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
from engine.models.execution_command import (
    COMMAND_ID_PREFIX,
    ExecutionCommand,
    ExecutionMode,
    create_execution_command,
)
from engine.models.operational_trade_intent import (
    OperationalTradeIntent,
    create_intent_from_plan,
)
from engine.models.trade_plan import RiskPlanStatus
from engine.persistence.exceptions import (
    CommandIntegrityError,
    CommandNotFoundError,
    CommandStoreError,
    UnsupportedCommandSchemaVersionError,
)
from engine.persistence.execution_command_serialization import (
    COMMAND_SCHEMA_VERSION,
    canonical_command_json,
    deserialize_command,
    parse_command_header,
    serialize_command,
)
from engine.persistence.execution_command_store import (
    ExecutionCommandStore,
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


def _make_command(**overrides):
    """Create a valid ExecutionCommand for testing."""
    base = {
        "intent": _make_intent(),
        "authorization": _make_authorization(),
        "created_at": datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc),
        "valid_from": datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc),
        "valid_until": datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc),
        "label": "test-label",
        "metadata": (("key1", "val1"), ("key2", "val2")),
    }
    base.update(overrides)
    return create_execution_command(**base)


def _make_command_with_entry(entry: Decimal, **overrides):
    """Create a command with a unique economic content (different entry price)
    so that it has a distinct command_id. Use this when tests need multiple
    commands that don't collide on the deterministic command_id.
    """
    intent = _make_intent(entry=entry)
    auth = _make_authorization(intent=intent)
    base = {
        "intent": intent,
        "authorization": auth,
        "created_at": datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc),
        "valid_from": datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc),
        "valid_until": datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc),
        "label": "test-label",
        "metadata": (("key1", "val1"), ("key2", "val2")),
    }
    base.update(overrides)
    return create_execution_command(**base)


# ============================================================
# 1. BASIC PERSISTENCE
# ============================================================


class TestBasicPersistence:
    """save, load, exists, missing command."""

    def test_save_and_load(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd = _make_command()

        result_path = store.save(cmd)

        assert result_path.exists()
        loaded = store.load(cmd.command_id)
        assert loaded == cmd

    def test_exists_true(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd = _make_command()
        store.save(cmd)

        assert store.exists(cmd.command_id) is True

    def test_exists_false(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)

        assert store.exists("cmd-nonexistent") is False

    def test_load_missing_raises(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)

        with pytest.raises(CommandNotFoundError):
            store.load("cmd-nonexistent")

    def test_save_returns_path(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd = _make_command()

        result_path = store.save(cmd)

        assert result_path == store.path_for(cmd.command_id)

    def test_default_directory_is_cwd_commands(self):
        expected = Path.cwd() / "commands"
        store = ExecutionCommandStore()
        assert store.directory == expected

    def test_custom_directory(self, tmp_path):
        custom = tmp_path / "custom_commands"
        store = ExecutionCommandStore(directory=custom)
        assert store.directory == custom


# ============================================================
# 2. DETERMINISTIC FILENAME AND SERIALIZATION
# ============================================================


class TestDeterministicFilename:
    """command_id format and deterministic file names."""

    def test_filename_matches_command_id(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd = _make_command()

        store.save(cmd)

        expected_path = tmp_path / f"{cmd.command_id}.json"
        assert expected_path.exists()

    def test_command_id_starts_with_prefix(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd = _make_command()

        assert cmd.command_id.startswith(COMMAND_ID_PREFIX)

    def test_deterministic_filename_same_content(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd_a = _make_command()
        cmd_b = _make_command()

        assert cmd_a.command_id == cmd_b.command_id

    def test_deterministic_serialization(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd = _make_command()

        text_a = serialize_command(cmd)
        text_b = serialize_command(cmd)

        assert text_a == text_b

    def test_sorted_keys_in_payload(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd = _make_command()

        text = serialize_command(cmd)
        parsed = json.loads(text)
        assert list(parsed.keys()) == sorted(parsed.keys())


# ============================================================
# 3. ROUND-TRIP
# ============================================================


class TestRoundTrip:
    """Exact preservation of all fields across save/load."""

    def test_command_id_preserved(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd = _make_command()
        store.save(cmd)
        loaded = store.load(cmd.command_id)
        assert loaded.command_id == cmd.command_id

    def test_authorization_id_preserved(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd = _make_command()
        store.save(cmd)
        loaded = store.load(cmd.command_id)
        assert loaded.authorization_id == cmd.authorization_id

    def test_intent_id_preserved(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd = _make_command()
        store.save(cmd)
        loaded = store.load(cmd.command_id)
        assert loaded.intent_id == cmd.intent_id

    def test_content_fingerprint_preserved(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd = _make_command()
        store.save(cmd)
        loaded = store.load(cmd.command_id)
        assert loaded.content_fingerprint == cmd.content_fingerprint

    def test_instrument_preserved(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd = _make_command()
        store.save(cmd)
        loaded = store.load(cmd.command_id)
        assert loaded.instrument == cmd.instrument

    def test_direction_preserved(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        intent = _make_intent(direction="LONG")
        auth = _make_authorization(intent=intent)
        cmd = create_execution_command(
            intent=intent,
            authorization=auth,
            created_at=datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc),
        )
        store.save(cmd)
        loaded = store.load(cmd.command_id)
        assert loaded.direction == "LONG"

    def test_decimal_geometry_preserved(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd = _make_command()
        store.save(cmd)
        loaded = store.load(cmd.command_id)
        assert loaded.entry == cmd.entry
        assert loaded.stop == cmd.stop
        assert loaded.target == cmd.target

    def test_decimal_quantity_preserved(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd = _make_command()
        store.save(cmd)
        loaded = store.load(cmd.command_id)
        assert loaded.quantity == cmd.quantity
        assert loaded.planned_risk == cmd.planned_risk
        assert loaded.maximum_risk == cmd.maximum_risk

    def test_execution_mode_preserved(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd = _make_command()
        store.save(cmd)
        loaded = store.load(cmd.command_id)
        assert loaded.execution_mode == cmd.execution_mode

    def test_paper_mode_roundtrip(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        auth = _make_authorization(scope="paper")
        intent = _make_intent()
        cmd = create_execution_command(
            intent=intent,
            authorization=auth,
            created_at=datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc),
        )
        store.save(cmd)
        loaded = store.load(cmd.command_id)
        assert loaded.execution_mode is ExecutionMode.PAPER
        assert loaded.is_paper is True

    def test_live_mode_roundtrip(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        auth = _make_authorization(scope="live")
        intent = _make_intent()
        cmd = create_execution_command(
            intent=intent,
            authorization=auth,
            created_at=datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc),
        )
        store.save(cmd)
        loaded = store.load(cmd.command_id)
        assert loaded.execution_mode is ExecutionMode.LIVE
        assert loaded.is_live is True

    def test_datetime_preserved(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd = _make_command()
        store.save(cmd)
        loaded = store.load(cmd.command_id)
        assert loaded.created_at == cmd.created_at
        assert loaded.valid_from == cmd.valid_from
        assert loaded.valid_until == cmd.valid_until

    def test_datetime_timezone_preserved(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd = _make_command()
        store.save(cmd)
        loaded = store.load(cmd.command_id)
        assert loaded.created_at.tzinfo is not None
        assert loaded.valid_from.tzinfo is not None
        assert loaded.valid_until.tzinfo is not None

    def test_metadata_preserved(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        metadata = (("z", "last"), ("a", "first"), ("key", "value"))
        cmd = _make_command(metadata=metadata)
        store.save(cmd)
        loaded = store.load(cmd.command_id)
        assert loaded.metadata == tuple(sorted(metadata))

    def test_label_preserved(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd = _make_command(label="custom-label")
        store.save(cmd)
        loaded = store.load(cmd.command_id)
        assert loaded.label == "custom-label"

    def test_short_direction_roundtrip(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        intent = _make_intent(direction="SHORT")
        auth = _make_authorization(intent=intent)
        cmd = create_execution_command(
            intent=intent,
            authorization=auth,
            created_at=datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc),
        )
        store.save(cmd)
        loaded = store.load(cmd.command_id)
        assert loaded.direction == "SHORT"


# ============================================================
# 4. RESTART
# ============================================================


class TestRestart:
    """Fresh store instance loads persisted command."""

    def test_fresh_store_loads_persisted(self, tmp_path):
        cmd = _make_command()
        store_a = ExecutionCommandStore(directory=tmp_path)
        store_a.save(cmd)

        store_b = ExecutionCommandStore(directory=tmp_path)
        loaded = store_b.load(cmd.command_id)

        assert loaded.command_id == cmd.command_id
        assert loaded.authorization_id == cmd.authorization_id
        assert loaded.intent_id == cmd.intent_id
        assert loaded.content_fingerprint == cmd.content_fingerprint
        assert loaded.instrument == cmd.instrument
        assert loaded.direction == cmd.direction
        assert loaded.entry == cmd.entry
        assert loaded.stop == cmd.stop
        assert loaded.target == cmd.target
        assert loaded.quantity == cmd.quantity
        assert loaded.execution_mode == cmd.execution_mode
        assert loaded.created_at == cmd.created_at
        assert loaded.valid_until == cmd.valid_until
        assert loaded.label == cmd.label
        assert loaded.metadata == cmd.metadata

    def test_file_survives_store_recreation(self, tmp_path):
        cmd = _make_command()
        store_a = ExecutionCommandStore(directory=tmp_path)
        store_a.save(cmd)

        store_b = ExecutionCommandStore(directory=tmp_path)
        assert store_b.exists(cmd.command_id)
        loaded = store_b.load(cmd.command_id)
        assert loaded.command_id == cmd.command_id


# ============================================================
# 5. DUPLICATE HANDLING
# ============================================================


class TestDuplicateHandling:
    """Repeated identical save and conflicting content under same ID."""

    def test_identical_save_idempotent(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd = _make_command()

        store.save(cmd)
        store.save(cmd)

        loaded = store.load(cmd.command_id)
        assert loaded == cmd

    def test_identical_save_returns_path(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd = _make_command()

        store.save(cmd)
        path = store.save(cmd)

        assert path.exists()

    def test_conflicting_content_raises_by_default(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd = _make_command()
        store.save(cmd)

        conflicting = _make_command(label="different-label")

        with pytest.raises(CommandIntegrityError):
            store.save(conflicting)

    def test_conflicting_content_with_overwrite_true(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd = _make_command()
        store.save(cmd)

        replacement = _make_command(label="replacement")

        store.save(replacement, overwrite=True)

        loaded = store.load(cmd.command_id)
        assert loaded.label == "replacement"


# ============================================================
# 6. CORRUPTION
# ============================================================


class TestCorruption:
    """Malformed persisted records must never be returned as valid."""

    def test_malformed_json_raises(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd = _make_command()
        store.save(cmd)

        target = store.path_for(cmd.command_id)
        target.write_text("not valid json {{{", encoding="utf-8")

        with pytest.raises(CommandStoreError):
            store.load(cmd.command_id)

    def test_missing_command_key_raises(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd = _make_command()
        store.save(cmd)

        target = store.path_for(cmd.command_id)
        target.write_text(
            json.dumps({"schema_version": COMMAND_SCHEMA_VERSION}, sort_keys=True),
            encoding="utf-8",
        )

        with pytest.raises(CommandStoreError):
            store.load(cmd.command_id)

    def test_unsupported_schema_version_raises(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd = _make_command()
        store.save(cmd)

        target = store.path_for(cmd.command_id)
        payload = {
            "schema_version": 999,
            "command": _encode_command(cmd),
        }
        target.write_text(
            json.dumps(payload, sort_keys=True),
            encoding="utf-8",
        )

        with pytest.raises(UnsupportedCommandSchemaVersionError):
            store.load(cmd.command_id)

    def test_identity_mismatch_raises(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd = _make_command()
        store.save(cmd)

        target = store.path_for(cmd.command_id)
        other = _make_command_with_entry(Decimal("200.50"))
        raw = json.loads(serialize_command(other))
        target.write_text(json.dumps(raw, sort_keys=True), encoding="utf-8")

        with pytest.raises(CommandIntegrityError):
            store.load(cmd.command_id)

    def test_truncated_json_raises(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd = _make_command()
        store.save(cmd)

        target = store.path_for(cmd.command_id)
        full = serialize_command(cmd)
        target.write_text(full[: len(full) // 2], encoding="utf-8")

        with pytest.raises(CommandStoreError):
            store.load(cmd.command_id)

    def test_non_dict_payload_raises(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd = _make_command()
        store.save(cmd)

        target = store.path_for(cmd.command_id)
        target.write_text("[1, 2, 3]", encoding="utf-8")

        with pytest.raises(CommandStoreError):
            store.load(cmd.command_id)

    def test_missing_schema_version_raises(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd = _make_command()
        store.save(cmd)

        target = store.path_for(cmd.command_id)
        target.write_text(
            json.dumps({"command": {}}, sort_keys=True),
            encoding="utf-8",
        )

        with pytest.raises(UnsupportedCommandSchemaVersionError):
            store.load(cmd.command_id)


def _encode_command(cmd):
    """Encode a command to JSON-safe dict for test tampering."""
    import dataclasses

    if cmd is None:
        return None
    if isinstance(cmd, bool):
        return cmd
    if isinstance(cmd, Decimal):
        return {"__decimal__": str(cmd)}
    if isinstance(cmd, datetime):
        return {"__datetime__": cmd.isoformat()}
    if isinstance(cmd, ExecutionMode):
        return {"__enum__": cmd.name}
    if dataclasses.is_dataclass(cmd) and not isinstance(cmd, type):
        return {
            "__dataclass__": type(cmd).__name__,
            "fields": {
                f.name: _encode_command(getattr(cmd, f.name))
                for f in cmd.__dataclass_fields__.values()
            },
        }
    if isinstance(cmd, (list, tuple)):
        return {"__tuple__": [_encode_command(item) for item in cmd]}
    if isinstance(cmd, dict):
        return {str(k): _encode_command(v) for k, v in cmd.items()}
    return cmd


# ============================================================
# 7. SECURITY
# ============================================================


class TestSecurity:
    """Unsafe command IDs must be rejected."""

    def test_path_traversal_rejected(self):
        bad_ids = [
            "../etc/passwd",
            "../../commands/evil",
            "cmd-../../../windows/system32",
            "cmd-../../",
            "",
            "cmd with spaces",
            "cmd\nwith\nnewlines",
            "cmd;rm -rf /",
        ]
        for bad_id in bad_ids:
            with pytest.raises(CommandStoreError):
                _validate_id(bad_id)

    def test_safe_ids_accepted(self):
        safe_ids = [
            "cmd-abc123def4567890",
            "cmd-ABC_123.def-456",
            "cmd-a" * 20,
        ]
        for safe_id in safe_ids:
            _validate_id(safe_id)

    def test_load_path_traversal_rejected(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)

        with pytest.raises(CommandStoreError):
            store.load("../etc/passwd")

    def test_exists_path_traversal_rejected(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)

        with pytest.raises(CommandStoreError):
            store.exists("../etc/passwd")


# ============================================================
# 8. IMMUTABILITY
# ============================================================


class TestImmutability:
    """Save does not mutate the original; load returns frozen artifact."""

    def test_save_does_not_mutate_command(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd = _make_command()
        original_label = cmd.label
        original_metadata = cmd.metadata

        store.save(cmd)

        assert cmd.label == original_label
        assert cmd.metadata == original_metadata

    def test_load_returns_independent_artifact(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd = _make_command()
        store.save(cmd)

        loaded = store.load(cmd.command_id)

        assert loaded == cmd
        with pytest.raises(dataclasses.FrozenInstanceError):
            loaded.label = "mutated"  # type: ignore[misc]

    def test_original_unchanged_after_load(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd = _make_command()
        store.save(cmd)

        store.load(cmd.command_id)

        assert cmd == _make_command(label=cmd.label)


# ============================================================
# 9. LIST / DELETE
# ============================================================


class TestListDelete:
    """list_commands and delete."""

    def test_list_empty_directory(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        assert store.list_commands() == []

    def test_list_after_save(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd1 = _make_command_with_entry(Decimal("100.50"))
        cmd2 = _make_command_with_entry(Decimal("200.50"))
        store.save(cmd1)
        store.save(cmd2)

        ids = store.list_commands()
        assert len(ids) == 2
        assert cmd1.command_id in ids
        assert cmd2.command_id in ids

    def test_list_sorted(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd1 = _make_command_with_entry(Decimal("100.50"))
        cmd2 = _make_command_with_entry(Decimal("200.50"))
        store.save(cmd1)
        store.save(cmd2)

        ids = store.list_commands()
        assert ids == sorted(ids)

    def test_delete_existing(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd = _make_command()
        store.save(cmd)

        store.delete(cmd.command_id)

        assert not store.exists(cmd.command_id)
        with pytest.raises(CommandNotFoundError):
            store.load(cmd.command_id)

    def test_delete_missing_raises(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)

        with pytest.raises(CommandNotFoundError):
            store.delete("cmd-nonexistent")

    def test_list_ignores_stray_files(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        stray = tmp_path / "stray.txt"
        stray.write_text("hello", encoding="utf-8")
        not_json = tmp_path / "not_json"
        not_json.write_text("hello", encoding="utf-8")

        assert store.list_commands() == []


# ============================================================
# 10. SCHEMA VERSION
# ============================================================


class TestSchemaVersion:
    """Schema version validated before reconstruction."""

    def test_schema_version_carried(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd = _make_command()
        store.save(cmd)

        text = (tmp_path / f"{cmd.command_id}.json").read_text(encoding="utf-8")
        payload = json.loads(text)
        assert payload["schema_version"] == COMMAND_SCHEMA_VERSION

    def test_parse_header(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd = _make_command()
        store.save(cmd)

        text = (tmp_path / f"{cmd.command_id}.json").read_text(encoding="utf-8")
        header = parse_command_header(text)
        assert header["schema_version"] == COMMAND_SCHEMA_VERSION
        assert "command" in header


# ============================================================
# 11. ATOMIC WRITE
# ============================================================


class TestAtomicWrite:
    """Atomic write guarantees."""

    def test_no_temp_file_left_after_success(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd = _make_command()
        store.save(cmd)

        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []

    def test_file_created_atomically(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd = _make_command()
        store.save(cmd)

        target = store.path_for(cmd.command_id)
        assert target.exists()
        assert target.is_file()

    def test_overwrite_replaces_file(self, tmp_path):
        store = ExecutionCommandStore(directory=tmp_path)
        cmd = _make_command(label="original")
        store.save(cmd)

        replacement = _make_command(label="replacement")

        store.save(replacement, overwrite=True)
        loaded = store.load(cmd.command_id)
        assert loaded.label == "replacement"


# ============================================================
# 12. SERIALIZATION MODULE
# ============================================================


class TestSerializationModule:
    """Direct serialization module tests."""

    def test_round_trip(self):
        cmd = _make_command()
        text = serialize_command(cmd)
        loaded = deserialize_command(text)
        assert loaded == cmd

    def test_deterministic_bytes(self):
        cmd = _make_command()
        bytes_a = serialize_command(cmd).encode("utf-8")
        bytes_b = serialize_command(cmd).encode("utf-8")
        assert bytes_a == bytes_b

    def test_unsupported_schema_in_module(self):
        payload = json.dumps({"schema_version": 999, "command": {}})
        with pytest.raises(ValueError):
            deserialize_command(payload)

    def test_malformed_json_in_module(self):
        with pytest.raises(ValueError):
            deserialize_command("not json")

    def test_canonical_json_sorted_keys(self):
        cmd = _make_command()
        text = serialize_command(cmd)
        parsed = json.loads(text)
        assert list(parsed.keys()) == sorted(parsed.keys())

    def test_canonical_command_json(self):
        cmd = _make_command()
        a = canonical_command_json(cmd)
        b = canonical_command_json(cmd)
        assert a == b
        assert a == serialize_command(cmd)

    def test_serialize_bytes(self):
        cmd = _make_command()
        text = serialize_command(cmd)
        bytes_data = text.encode("utf-8")
        assert isinstance(bytes_data, bytes)
        assert bytes_data.decode("utf-8") == text


# ============================================================
# 13. BOUNDARY ISOLATION
# ============================================================


class TestBoundaryIsolation:
    """Store does not import execution/broker functionality."""

    def test_store_module_no_execution_broker_imports(self):
        import engine.persistence.execution_command_store as store_mod

        source = Path(store_mod.__file__).read_text(encoding="utf-8")
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
        import engine.persistence.execution_command_serialization as ser_mod

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
                f"Serialization module must not contain functional reference {term!r}"
            )
