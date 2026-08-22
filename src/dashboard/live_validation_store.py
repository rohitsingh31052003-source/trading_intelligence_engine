"""
Filesystem persistence for live paper validation observations
(Product Phase 6F).

Follows the established persistence architecture (one JSON file per
record; atomic same-dir temp + ``os.replace``; safe-id validation
against path traversal; schema version checked BEFORE model
reconstruction; corruption detection via typed errors). One JSON document per observation (``<directory>/<validation_id>.validation``).
The ``.validation`` suffix (intentionally NOT ``.json``) keeps validation
records visually distinct and prevents polluting the existing paper-trade
store listing (``*.json``), the experiment registry listing (``*.json``)
and the Phase 6D research store listing (``*.research.json``) in a shared
directory. The existing
:class:`~dashboard.paper_trade_store.PaperTradeStore` schema is NOT
modified — Phase 6F observations REFERENCE paper-trade ids instead of
embedding paper-trade records.

APPEND / HISTORY SEMANTICS: saving an observation appends one JSON line
to a per-observation history journal
(``<validation_id>.validation.history.jsonl``) recording (revision, status,
updated_at) for every persisted revision, so the observation's evolution
(setup -> active -> completed) is auditable without duplicating the full
record. The main file always holds the LATEST revision; the observation
``validation_id`` is deterministic for the same setup at the same
evaluation time ``T``, so re-running a cycle against the same completed
candle is IDEMPOTENT (no duplicate observation).

No ``pickle`` / ``eval`` / ``exec``; stdlib only; no database server;
no cloud. Observations survive page refreshes / process restarts.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from engine.intelligence.live_validation_serialization import (
    LIVE_VALIDATION_SCHEMA_VERSION,
    deserialize_observation,
    parse_observation_header,
    serialize_observation,
)
from engine.models.live_validation import LiveValidationObservation


class LiveValidationStoreError(Exception):
    """Base error for live-validation persistence."""


class LiveValidationNotFoundError(LiveValidationStoreError):
    """A requested validation observation was not found."""


class LiveValidationIntegrityError(LiveValidationStoreError):
    """A persisted validation observation failed an integrity check."""


_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _validate_id(validation_id: str) -> None:
    if not validation_id or not _SAFE_ID_RE.match(validation_id):
        raise LiveValidationStoreError(
            f"Unsafe validation id {validation_id!r}: ids must match "
            f"{str(_SAFE_ID_RE.pattern)!r}."
        )


def default_live_validation_directory() -> Path:
    """Default validation root (``./data/live_validation``)."""

    return Path.cwd() / "data" / "live_validation"


class LiveValidationStore:
    """Filesystem persistence for live-validation observations."""

    def __init__(self, directory: str | Path | None = None) -> None:
        if directory is None:
            directory = default_live_validation_directory()
        self.directory = Path(directory)

    # ------------------------------------------------------------
    # PATHS
    # ------------------------------------------------------------

    def path_for(self, validation_id: str) -> Path:
        _validate_id(validation_id)
        return self.directory / f"{validation_id}.validation"

    def _history_path_for(self, validation_id: str) -> Path:
        _validate_id(validation_id)
        return self.directory / f"{validation_id}.validation.history.jsonl"

    # ------------------------------------------------------------
    # SAVE / LOAD
    # ------------------------------------------------------------

    def save(
        self,
        observation: LiveValidationObservation,
        *,
        overwrite: bool = False,
    ) -> Path:
        """Persist an observation. No silent overwrite.

        Appends one history-journal line per persisted revision (append /
        history semantics). The write of the main record is atomic.
        """

        _validate_id(observation.validation_id)
        target = self.path_for(observation.validation_id)
        if target.exists() and not overwrite:
            raise LiveValidationStoreError(
                f"Validation observation {observation.validation_id!r} "
                "already exists; pass overwrite=True to replace it.",
            )
        self._atomic_write(target, serialize_observation(observation))
        self._append_history(observation)
        return target

    def load(self, validation_id: str) -> LiveValidationObservation:
        """Load an observation (schema + integrity checked)."""

        path = self.path_for(validation_id)
        if not path.exists():
            raise LiveValidationNotFoundError(
                f"Validation observation {validation_id!r} not found."
            )
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise LiveValidationStoreError(
                f"Failed to read validation observation {validation_id!r}: {exc}"
            ) from exc
        # Cheap header parse first: schema version is checked BEFORE any
        # model reconstruction; corrupted JSON -> typed integrity error.
        try:
            header = parse_observation_header(text)
        except Exception as exc:
            raise LiveValidationIntegrityError(
                f"Validation observation {validation_id!r} is corrupted "
                f"(unreadable JSON): {exc}"
            ) from exc
        version = header.get("schema_version") if isinstance(header, dict) else None
        if version != LIVE_VALIDATION_SCHEMA_VERSION:
            raise LiveValidationIntegrityError(
                f"Validation observation {validation_id!r} has unsupported "
                f"schema version {version!r}; supported is "
                f"{LIVE_VALIDATION_SCHEMA_VERSION}."
            )
        stored_id = header.get("validation_id")
        if stored_id != validation_id:
            raise LiveValidationIntegrityError(
                f"Validation observation filename id {validation_id!r} does "
                f"not match stored id {stored_id!r}."
            )
        try:
            return deserialize_observation(text)
        except ValueError as exc:
            raise LiveValidationIntegrityError(
                f"Validation observation {validation_id!r} failed "
                f"deserialization: {exc}"
            ) from exc

    def exists(self, validation_id: str) -> bool:
        _validate_id(validation_id)
        return self.path_for(validation_id).exists()

    def list_observations(self) -> list[str]:
        """Sorted list of persisted observation ids."""

        if not self.directory.exists():
            return []
        ids: list[str] = []
        for path in self.directory.glob("*.validation"):
            stem = path.name[: -len(".validation")]
            if _SAFE_ID_RE.match(stem):
                ids.append(stem)
        return sorted(ids)

    def load_all(self) -> list[LiveValidationObservation]:
        """Load every persisted observation (sorted by id)."""

        return [self.load(vid) for vid in self.list_observations()]

    def load_history(self, validation_id: str) -> list[dict]:
        """Load the append-only history journal for an observation."""

        path = self._history_path_for(validation_id)
        if not path.exists():
            return []
        entries: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries

    def delete(self, validation_id: str) -> None:
        """Delete an observation + its history journal."""

        path = self.path_for(validation_id)
        if not path.exists():
            raise LiveValidationNotFoundError(
                f"Validation observation {validation_id!r} not found."
            )
        path.unlink()
        history = self._history_path_for(validation_id)
        if history.exists():
            history.unlink()

    # ------------------------------------------------------------
    # INTERNALS
    # ------------------------------------------------------------

    def _append_history(self, observation: LiveValidationObservation) -> None:
        """Append one history-journal line for a persisted revision."""

        path = self._history_path_for(observation.validation_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "validation_id": observation.validation_id,
            "revision": observation.revision,
            "validation_status": observation.validation_status.value,
            "updated_at": (
                observation.updated_at.isoformat()
                if observation.updated_at is not None
                else None
            ),
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")

    def _atomic_write(self, target: Path, text: str) -> None:
        """Write ``text`` to ``target`` atomically (same-dir temp + replace)."""

        directory = target.parent
        directory.mkdir(parents=True, exist_ok=True)

        prefix = target.name + "."
        fd, tmp_name = tempfile.mkstemp(
            prefix=prefix,
            suffix=".tmp",
            dir=str(directory),
        )
        tmp_path = Path(tmp_name)

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass
            os.replace(tmp_path, target)
        except LiveValidationStoreError:
            raise
        except Exception as exc:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise LiveValidationStoreError(
                f"Failed to atomically write validation record "
                f"{target!s}: {exc}"
            ) from exc


__all__ = [
    "LiveValidationIntegrityError",
    "LiveValidationNotFoundError",
    "LiveValidationStore",
    "LiveValidationStoreError",
    "default_live_validation_directory",
]
