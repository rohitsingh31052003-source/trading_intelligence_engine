"""
Filesystem persistence for execution commands (Checkpoint 16.5).

The persistence layer stores :class:`~engine.models.execution_command.ExecutionCommand`
objects as deterministic JSON files so they can be retrieved after a
process restart WITHOUT recomputing the command.

Storage layout::

    <directory>/
        <command_id>.json
        <command_id>.json
        ...

One file per command id. No arbitrary files are written outside
the designated directory.

Design rules:

* Atomic writes. Each record is written to a temporary file, flushed
  and closed, then atomically renamed onto its final path so a
  partially written command file is never left behind.
* Cross-platform paths. All path construction uses :mod:`pathlib`
  (no hard-coded path separators); works from the project root on
  Windows and Linux.
* Schema-aware. The loader validates the schema version before
  reconstructing any model, raising
  :class:`UnsupportedCommandSchemaVersionError` on a future
  version.
* No silent error swallowing. Corrupted JSON, missing records and
  integrity failures surface typed exceptions.
* Identity-integrity. The stored command_id must agree with the
  reconstructed record's command_id. A mismatch is an integrity
  failure, never silently accepted.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from engine.models.execution_command import (
    COMMAND_ID_PREFIX,
    ExecutionCommand,
)
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


# A command id is a deterministic opaque token. We restrict
# stored file names to safe characters so the id can never escape
# the store directory through path traversal.
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")

#: Suffix used for persisted command records.
_RECORD_SUFFIX = ".json"


# ============================================================
# HELPERS
# ============================================================


def _validate_id(command_id: str) -> None:
    """Ensure a command id is safe to use as a file name."""

    if not command_id or not _SAFE_ID_RE.match(command_id):
        raise CommandStoreError(
            f"Unsafe command id {command_id!r}: ids must "
            f"match {_SAFE_ID_RE.pattern!r}."
        )


def default_command_directory() -> Path:
    """
    Default command directory, resolved relative to the current
    working directory (``./commands``).

    This avoids hard-coded absolute paths and global mutable state.
    Callers wanting a fixed location should pass an explicit
    ``directory`` to the store.
    """

    return Path.cwd() / "commands"


# ============================================================
# PERSISTENCE
# ============================================================


class ExecutionCommandStore:
    """
    Atomic, filesystem-based persistence for execution commands.

    Public API:

        save(command, overwrite=False) -> Path
        load(command_id) -> ExecutionCommand
        exists(command_id) -> bool
        list_commands() -> list[str]
        delete(command_id) -> None
        path_for(command_id) -> Path

    The persistence layer is stateless across calls: identical
    inputs always produce identical on-disk content. It holds only
    the immutable ``directory`` it was constructed with.
    """

    def __init__(
        self,
        directory: Path | str | None = None,
    ) -> None:
        if directory is None:
            directory = default_command_directory()
        self._directory = Path(directory)

    # -----------------------------------------------------
    # PROPERTIES
    # -----------------------------------------------------

    @property
    def directory(self) -> Path:
        """The directory this persistence layer writes to."""

        return self._directory

    def path_for(self, command_id: str) -> Path:
        """The on-disk path for a given command id."""

        _validate_id(command_id)
        return self._directory / f"{command_id}{_RECORD_SUFFIX}"

    # -----------------------------------------------------
    # WRITE
    # -----------------------------------------------------

    def save(
        self,
        command: ExecutionCommand,
        overwrite: bool = False,
    ) -> Path:
        """
        Persist a command atomically.

        When ``overwrite`` is ``False`` and a command with the
        same id already exists, the store compares the existing content
        with the new content:

        * Identical content → idempotent success (returns the path).
        * Different content → raises
          :class:`CommandIntegrityError` so a caller can never
          silently overwrite a stored command with a different
          one that happens to share the same deterministic id.

        When ``overwrite`` is ``True``, the existing record is always
        replaced.
        """

        command_id = command.command_id
        _validate_id(command_id)

        self._ensure_directory()

        target = self.path_for(command_id)

        if target.exists() and not overwrite:
            existing_text = self._read_text(target)
            new_text = serialize_command(command)
            if existing_text != new_text:
                raise CommandIntegrityError(
                    f"Command {command_id!r} already exists "
                    f"with different content. Pass overwrite=True to "
                    f"replace it explicitly."
                )
            return target

        text = serialize_command(command)

        self._atomic_write(target, text)

        return target

    def _atomic_write(self, target: Path, text: str) -> None:
        """
        Write ``text`` to ``target`` atomically.

        Writes to a temporary file in the SAME directory, flushes,
        fsyncs and closes it, then atomically replaces the target.
        Using the same directory guarantees the ``os.replace`` call
        is a single-filesystem rename (atomic on Windows and
        POSIX). No partially written target file is ever left.
        """

        directory = target.parent
        directory.mkdir(parents=True, exist_ok=True)

        # Named temp file in the same directory for atomic rename.
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
                    # fsync may be unavailable on some platforms /
                    # filesystems; the flush + atomic rename still
                    # guards against partial content within a
                    # single process.
                    pass

            os.replace(tmp_path, target)
        except CommandStoreError:
            raise
        except Exception as exc:
            # Clean up the temp file on any failure and surface a
            # typed persistence error (never swallow silently).
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise CommandStoreError(
                f"Failed to atomically write command record "
                f"{target!s}: {exc}"
            ) from exc

    # -----------------------------------------------------
    # READ
    # -----------------------------------------------------

    def load(self, command_id: str) -> ExecutionCommand:
        """
        Load and reconstruct a command by id.

        The reconstructed command's identity is verified for
        internal consistency before it is returned. Raises
        :class:`CommandNotFoundError` when no record exists,
        :class:`UnsupportedCommandSchemaVersionError` on a
        future schema version, and
        :class:`CommandIntegrityError` when the persisted
        identity is internally inconsistent or tampered with.
        """

        _validate_id(command_id)
        path = self.path_for(command_id)

        if not path.exists():
            raise CommandNotFoundError(
                f"Command {command_id!r} is not stored in "
                f"{self._directory!s}."
            )

        text = self._read_text(path)

        # Schema version first (cheap), then full reconstruction.
        # Both steps can raise ValueError for malformed/unexpected
        # payloads — wrap as a typed store error so callers never
        # see a raw json / decode failure as a valid command.
        try:
            header = parse_command_header(text)
        except (ValueError, json.JSONDecodeError) as exc:
            raise CommandStoreError(
                f"Could not parse command record header "
                f"{path!s}: {exc}"
            ) from exc

        if not isinstance(header, dict):
            raise CommandStoreError(
                f"Malformed command record header in "
                f"{path!s}: expected a JSON object."
            )

        version = header.get("schema_version")
        if version != COMMAND_SCHEMA_VERSION:
            raise UnsupportedCommandSchemaVersionError(
                f"Unsupported command schema version {version!r}; "
                f"supported is {COMMAND_SCHEMA_VERSION}."
            )

        try:
            command = deserialize_command(text)
        except (ValueError, json.JSONDecodeError) as exc:
            raise CommandStoreError(
                f"Could not reconstruct command from "
                f"{path!s}: {exc}"
            ) from exc

        # Identity integrity: the loaded id must match the file-name id.
        if command.command_id != command_id:
            raise CommandIntegrityError(
                f"Command id mismatch: file name "
                f"{command_id!r} vs stored id "
                f"{command.command_id!r}."
            )

        return command

    def _read_text(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CommandStoreError(
                f"Could not read command record {path!s}: {exc}"
            ) from exc

    # -----------------------------------------------------
    # QUERIES
    # -----------------------------------------------------

    def exists(self, command_id: str) -> bool:
        """Whether a command with the given id is stored."""

        _validate_id(command_id)
        return self.path_for(command_id).exists()

    def list_commands(self) -> list[str]:
        """
        Sorted list of stored command ids.

        Only files matching the record suffix and a safe id are
        considered; stray files in the directory are ignored.
        """

        if not self._directory.exists():
            return []

        ids: list[str] = []
        for entry in self._directory.iterdir():
            if not entry.is_file():
                continue
            name = entry.name
            if not name.endswith(_RECORD_SUFFIX):
                continue
            stem = name[: -len(_RECORD_SUFFIX)]
            if _SAFE_ID_RE.match(stem):
                ids.append(stem)
        return sorted(ids)

    # -----------------------------------------------------
    # DELETE
    # -----------------------------------------------------

    def delete(self, command_id: str) -> None:
        """
        Delete a stored command.

        Raises :class:`CommandNotFoundError` when no record
        exists. Deletion is intentionally explicit: callers must
        ask for it by id.
        """

        _validate_id(command_id)
        path = self.path_for(command_id)
        if not path.exists():
            raise CommandNotFoundError(
                f"Command {command_id!r} is not stored in "
                f"{self._directory!s}."
            )
        try:
            path.unlink()
        except OSError as exc:
            raise CommandStoreError(
                f"Could not delete command record {path!s}: {exc}"
            ) from exc

    # -----------------------------------------------------
    # INTERNAL
    # -----------------------------------------------------

    def _ensure_directory(self) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)


# ============================================================
# MODULE HELPERS
# ============================================================


def _raw_mapping(text: str) -> dict[str, object]:
    import json

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


# Re-export the schema constant for convenience.
__all__ = [
    "COMMAND_SCHEMA_VERSION",
    "ExecutionCommandStore",
    "default_command_directory",
]
