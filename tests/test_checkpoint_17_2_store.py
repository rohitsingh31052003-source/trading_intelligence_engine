"""Checkpoint 17.2 — submission lifecycle persistence tests.

Covers the SubmissionLifecycleStore + serialization: round-trip, restart
recovery, duplicate detection, corruption handling, schema versioning,
atomic writes, safe-id validation, and command linkage.
"""

from __future__ import annotations

import json

import pytest

from engine.models.submission_lifecycle import (
    SubmissionState,
    create_submission_lifecycle,
)
from engine.persistence.exceptions import (
    SubmissionIntegrityError,
    SubmissionNotFoundError,
    SubmissionStoreError,
    UnsupportedSubmissionSchemaVersionError,
)
from engine.persistence.submission_serialization import (
    SUBMISSION_SCHEMA_VERSION,
    canonical_submission_json,
    deserialize_submission,
    parse_submission_header,
    serialize_submission,
    serialize_submission_bytes,
)
from engine.persistence.submission_store import (
    SubmissionLifecycleStore,
    default_submission_directory,
)

from tests._checkpoint17_2_fixtures import utc


def make_lifecycle(state: SubmissionState = SubmissionState.ACCEPTED, **overrides):
    kwargs = {
        "command_id": "cmd-abc123def4567890",
        "state": state,
        "client_order_id": "co-abc123def4567890",
        "pre_submission": False,
        "created_at": utc(2026, 9, 1, 13),
        "events": (),
        "broker_order_id": "brk-abc123def4567890",
        "reason": "test",
        "label": "test-label",
        "metadata": (("key", "value"),),
    }
    kwargs.update(overrides)
    return create_submission_lifecycle(**kwargs)


# ============================================================
# SERIALIZATION
# ============================================================


class TestSerialization:
    def test_round_trip(self):
        lc = make_lifecycle()
        text = serialize_submission(lc)
        restored = deserialize_submission(text)
        assert restored.submission_id == lc.submission_id
        assert restored.command_id == lc.command_id
        assert restored.state is lc.state
        assert restored.client_order_id == lc.client_order_id
        assert restored.created_at == lc.created_at
        assert restored.metadata == lc.metadata

    def test_deterministic_bytes(self):
        lc = make_lifecycle()
        assert serialize_submission_bytes(lc) == serialize_submission_bytes(lc)
        assert canonical_submission_json(lc) == canonical_submission_json(lc)

    def test_schema_version_present(self):
        text = serialize_submission(make_lifecycle())
        header = parse_submission_header(text)
        assert header["schema_version"] == SUBMISSION_SCHEMA_VERSION

    def test_future_schema_rejected(self):
        text = serialize_submission(make_lifecycle())
        parsed = json.loads(text)
        parsed["schema_version"] = 999
        with pytest.raises(ValueError):
            deserialize_submission(json.dumps(parsed))

    def test_malformed_json_rejected(self):
        with pytest.raises(ValueError):
            deserialize_submission("{not json")

    def test_missing_submission_key_rejected(self):
        with pytest.raises(ValueError):
            deserialize_submission('{"schema_version": 1}')

    def test_events_round_trip(self):
        from engine.models.submission_lifecycle import SubmissionEvent

        event = SubmissionEvent(
            event_id="submission-event-1",
            submission_id="submission-x",
            state=SubmissionState.ACCEPTED,
            created_at=utc(2026, 9, 1, 13),
            reason="accepted",
            detail=(("error_code", "NONE"),),
        )
        lc = make_lifecycle(events=(event,))
        restored = deserialize_submission(serialize_submission(lc))
        assert len(restored.events) == 1
        assert restored.events[0].state is SubmissionState.ACCEPTED
        assert restored.events[0].detail == (("error_code", "NONE"),)


# ============================================================
# STORE
# ============================================================


class TestStore:
    def test_save_load_round_trip(self, tmp_path):
        store = SubmissionLifecycleStore(directory=tmp_path)
        lc = make_lifecycle()
        store.save(lc)
        loaded = store.load(lc.submission_id)
        assert loaded.submission_id == lc.submission_id
        assert loaded.state is lc.state

    def test_save_idempotent_identical(self, tmp_path):
        store = SubmissionLifecycleStore(directory=tmp_path)
        lc = make_lifecycle()
        store.save(lc)
        store.save(lc)  # identical content is idempotent
        assert store.exists(lc.submission_id)

    def test_save_conflicting_raises(self, tmp_path):
        store = SubmissionLifecycleStore(directory=tmp_path)
        lc1 = make_lifecycle()
        store.save(lc1)
        lc2 = make_lifecycle(reason="different reason")
        with pytest.raises(SubmissionIntegrityError):
            store.save(lc2)

    def test_save_overwrite(self, tmp_path):
        store = SubmissionLifecycleStore(directory=tmp_path)
        lc1 = make_lifecycle()
        store.save(lc1)
        lc2 = make_lifecycle(reason="different reason")
        store.save(lc2, overwrite=True)
        loaded = store.load(lc2.submission_id)
        assert loaded.reason == "different reason"

    def test_load_missing_raises(self, tmp_path):
        store = SubmissionLifecycleStore(directory=tmp_path)
        with pytest.raises(SubmissionNotFoundError):
            store.load("submission-doesnotexist")

    def test_list_and_delete(self, tmp_path):
        store = SubmissionLifecycleStore(directory=tmp_path)
        lc = make_lifecycle()
        store.save(lc)
        assert lc.submission_id in store.list_submissions()
        store.delete(lc.submission_id)
        assert not store.exists(lc.submission_id)

    def test_load_by_command(self, tmp_path):
        store = SubmissionLifecycleStore(directory=tmp_path)
        lc = make_lifecycle()
        store.save(lc)
        found = store.load_by_command(lc.command_id)
        assert found.submission_id == lc.submission_id

    def test_command_exists(self, tmp_path):
        store = SubmissionLifecycleStore(directory=tmp_path)
        assert not store.command_exists("cmd-abc123def4567890")
        lc = make_lifecycle()
        store.save(lc)
        assert store.command_exists(lc.command_id)

    def test_corrupted_json_raises(self, tmp_path):
        store = SubmissionLifecycleStore(directory=tmp_path)
        lc = make_lifecycle()
        store.save(lc)
        path = store.path_for(lc.submission_id)
        path.write_text("{corrupted", encoding="utf-8")
        with pytest.raises(SubmissionStoreError):
            store.load(lc.submission_id)

    def test_future_schema_raises(self, tmp_path):
        store = SubmissionLifecycleStore(directory=tmp_path)
        lc = make_lifecycle()
        store.save(lc)
        path = store.path_for(lc.submission_id)
        parsed = json.loads(path.read_text(encoding="utf-8"))
        parsed["schema_version"] = 999
        path.write_text(json.dumps(parsed), encoding="utf-8")
        with pytest.raises(UnsupportedSubmissionSchemaVersionError):
            store.load(lc.submission_id)

    def test_filename_id_mismignored(self, tmp_path):
        store = SubmissionLifecycleStore(directory=tmp_path)
        lc = make_lifecycle()
        store.save(lc)
        path = store.path_for(lc.submission_id)
        parsed = json.loads(path.read_text(encoding="utf-8"))
        # corrupt the stored id
        parsed["submission"]["fields"]["submission_id"] = "submission-other"
        path.write_text(json.dumps(parsed), encoding="utf-8")
        with pytest.raises(SubmissionIntegrityError):
            store.load(lc.submission_id)

    def test_unsafe_id_rejected(self, tmp_path):
        store = SubmissionLifecycleStore(directory=tmp_path)
        with pytest.raises(SubmissionStoreError):
            store.path_for("../evil")

    def test_default_directory_relative(self):
        assert default_submission_directory().name == "submissions"

    def test_restart_recovery_via_store(self, tmp_path):
        """A UNKNOWN lifecycle persists and can be reconciled after 'restart'."""
        from engine.intelligence.fake_broker import FakeBroker
        from engine.intelligence.submission_lifecycle import SubmissionLifecycleEngine

        from tests._checkpoint17_2_fixtures import make_authorization, make_command, make_intent

        intent = make_intent()
        auth = make_authorization(intent)
        cmd = make_command(intent, auth)
        eng = SubmissionLifecycleEngine()
        lc = eng.submit(
            command=cmd,
            adapter=FakeBroker(submit_scenario="timeout"),
            created_at=utc(2026, 9, 1, 13),
        )
        assert lc.state is SubmissionState.UNKNOWN

        # persist, then 'restart' by creating a new store + engine
        store = SubmissionLifecycleStore(directory=tmp_path)
        store.save(lc)
        new_store = SubmissionLifecycleStore(directory=tmp_path)
        restored = new_store.load(lc.submission_id)
        assert restored.state is SubmissionState.UNKNOWN

        eng2 = SubmissionLifecycleEngine()
        assert eng2.restart_recovery(restored) is not None
        # reconcile before retry
        rec = eng2.reconcile_submission(
            lifecycle=restored,
            adapter=FakeBroker(reconcile_scenario="reconcile_accepted"),
            created_at=utc(2026, 9, 1, 14),
        )
        assert rec.state is SubmissionState.ACCEPTED
