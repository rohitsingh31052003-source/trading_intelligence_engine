"""Filesystem persistence for submission lifecycles (Checkpoint 17.2).

The persistence layer stores
:class:`~engine.models.submission_lifecycle.SubmissionLifecycle` snapshots as
deterministic JSON files so the operational execution state can be recovered
after a process restart WITHOUT recomputing it.

Storage layout::

    <directory>/
        <submission_id>.json
        <submission_id>.json
        ...

One file per submission id. The underlying immutable
:class:`~engine.models.execution_command.ExecutionCommand` remains in its own
frozen store; this store ONLY tracks operational state and references the
command via ``command_id``.

Design rules (mirrors the frozen Checkpoint 16.5 command store):

* Atomic writes (same-dir temp + flush + fsync best-effort + ``os.replace``).
* Cross-platform paths via :mod:`pathlib`.
* Schema-aware load (validates before model reconstruction).
* No silent error swallowing — typed exceptions.
* Identity integrity: the stored ``submission_id`` must agree with the
  reconstructed record's id.
* ``command_id`` is available for duplicate-submission detection across
  restart (``command_exists`` / ``load_by_command``).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from engine.models.submission_lifecycle import (
    SUBMISSION_ID_PREFIX,
    SubmissionLifecycle,
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
)

#: A submission id is a deterministic opaque token restricted to safe
#: file-name characters so it can never escape the store directory.
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")

#: Suffix used for persisted submission lifecycle records.
_RECORD_SUFFIX = ".json"


def _validate_id(submission_id: str) -> None:
    """Ensure a submission id is safe to use as a file name."""

    if not submission_id or not _SAFE_ID_RE.match(submission_id):
        raise SubmissionStoreError(
            f"Unsafe submission id {submission_id!r}: ids must "
            f"match {_SAFE_ID_RE.pattern!r}."
        )


def default_submission_directory() -> Path:
    """Default submission directory (``./submissions`` relative to cwd)."""

    return Path.cwd() / "submissions"


class SubmissionLifecycleStore:
    """Atomic, filesystem-based persistence for submission lifecycles.

    Public API:

        save(lifecycle, overwrite=False) -> Path
        load(submission_id) -> SubmissionLifecycle
        exists(submission_id) -> bool
        load_by_command(command_id) -> SubmissionLifecycle
        command_exists(command_id) -> bool
        list_submissions() -> list[str]
        delete(submission_id) -> None
        path_for(submission_id) -> Path
    """

    def __init__(self, directory: Path | str | None = None) -> None:
        if directory is None:
            directory = default_submission_directory()
        self._directory = Path(directory)

    @property
    def directory(self) -> Path:
        return self._directory

    def path_for(self, submission_id: str) -> Path:
        _validate_id(submission_id)
        return self._directory / f"{submission_id}{_RECORD_SUFFIX}"

    # ---------------------------------------------------------
    # WRITE
    # ---------------------------------------------------------

    def save(
        self,
        lifecycle: SubmissionLifecycle,
        overwrite: bool = False,
    ) -> Path:
        """Persist a lifecycle snapshot atomically.

        Identical content is idempotent; different content for the same
        submission_id raises :class:`SubmissionIntegrityError` unless
        ``overwrite=True`` is passed explicitly.
        """

        submission_id = lifecycle.submission_id
        _validate_id(submission_id)

        self._directory.mkdir(parents=True, exist_ok=True)
        target = self.path_for(submission_id)

        if target.exists() and not overwrite:
            existing_text = self._read_text(target)
            new_text = serialize_submission(lifecycle)
            if existing_text != new_text:
                raise SubmissionIntegrityError(
                    f"Submission {submission_id!r} already exists "
                    f"with different content. Pass overwrite=True to "
                    f"replace it explicitly."
                )
            return target

        text = serialize_submission(lifecycle)
        self._atomic_write(target, text)
        return target

    def _atomic_write(self, target: Path, text: str) -> None:
        directory = target.parent
        directory.mkdir(parents=True, exist_ok=True)
        prefix = target.name + "."
        fd, tmp_name = tempfile.mkstemp(
            prefix=prefix, suffix=".tmp", dir=str(directory)
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
        except SubmissionStoreError:
            raise
        except Exception as exc:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise SubmissionStoreError(
                f"Failed to atomically write submission record "
                f"{target!s}: {exc}"
            ) from exc

    # ---------------------------------------------------------
    # READ
    # ---------------------------------------------------------

    def load(self, submission_id: str) -> SubmissionLifecycle:
        """Load and reconstruct a lifecycle snapshot by id."""

        _validate_id(submission_id)
        path = self.path_for(submission_id)
        if not path.exists():
            raise SubmissionNotFoundError(
                f"Submission {submission_id!r} is not stored in "
                f"{self._directory!s}."
            )
        text = self._read_text(path)

        try:
            header = parse_submission_header(text)
        except (ValueError, json.JSONDecodeError) as exc:
            raise SubmissionStoreError(
                f"Could not parse submission record header {path!s}: {exc}"
            ) from exc
        if not isinstance(header, dict):
            raise SubmissionStoreError(
                f"Malformed submission record header in "
                f"{path!s}: expected a JSON object."
            )
        version = header.get("schema_version")
        if version != SUBMISSION_SCHEMA_VERSION:
            raise UnsupportedSubmissionSchemaVersionError(
                f"Unsupported submission schema version {version!r}; "
                f"supported is {SUBMISSION_SCHEMA_VERSION}."
            )
        try:
            lifecycle = deserialize_submission(text)
        except (ValueError, json.JSONDecodeError) as exc:
            raise SubmissionStoreError(
                f"Could not reconstruct submission from {path!s}: {exc}"
            ) from exc

        if lifecycle.submission_id != submission_id:
            raise SubmissionIntegrityError(
                f"Submission id mismatch: file name "
                f"{submission_id!r} vs stored id "
                f"{lifecycle.submission_id!r}."
            )
        return lifecycle

    def load_by_command(self, command_id: str) -> SubmissionLifecycle:
        """Load the submission lifecycle for a command (single active record).

        Raises :class:`SubmissionNotFoundError` when no lifecycle exists for
        the command and :class:`SubmissionIntegrityError` when multiple
        records claim the same command (duplicate-submission guard).
        """

        if not command_id:
            raise SubmissionStoreError("command_id must be non-empty.")
        matches = [
            sid
            for sid in self.list_submissions()
            if self._command_for(sid) == command_id
        ]
        if not matches:
            raise SubmissionNotFoundError(
                f"No submission lifecycle is stored for command "
                f"{command_id!r}."
            )
        if len(matches) > 1:
            raise SubmissionIntegrityError(
                f"Multiple submission lifecycles reference command "
                f"{command_id!r}: {sorted(matches)!r}. Duplicate-submission "
                f"guard: exactly one active lifecycle per command is allowed."
            )
        return self.load(matches[0])

    def _command_for(self, submission_id: str) -> str:
        """Read the command_id from a stored record header cheaply."""

        path = self.path_for(submission_id)
        try:
            text = path.read_text(encoding="utf-8")
            parsed = json.loads(text)
            submission = parsed.get("submission", {})
            if isinstance(submission, dict):
                fields = submission.get("fields", {})
                if isinstance(fields, dict):
                    command = fields.get("command_id")
                    if isinstance(command, str):
                        return command
        except (OSError, json.JSONDecodeError, SubmissionStoreError):
            return ""
        return ""

    def command_exists(self, command_id: str) -> bool:
        """Whether a submission lifecycle exists for the command.

        This is the restart duplicate-submission guard: it is safe to call
        before any new submission attempt after a restart.
        """

        try:
            self.load_by_command(command_id)
            return True
        except SubmissionNotFoundError:
            return False
        except SubmissionIntegrityError:
            return True

    def _read_text(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SubmissionStoreError(
                f"Could not read submission record {path!s}: {exc}"
            ) from exc

    # ---------------------------------------------------------
    # QUERIES
    # ---------------------------------------------------------

    def exists(self, submission_id: str) -> bool:
        _validate_id(submission_id)
        return self.path_for(submission_id).exists()

    def list_submissions(self) -> list[str]:
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

    # ---------------------------------------------------------
    # DELETE
    # ---------------------------------------------------------

    def delete(self, submission_id: str) -> None:
        _validate_id(submission_id)
        path = self.path_for(submission_id)
        if not path.exists():
            raise SubmissionNotFoundError(
                f"Submission {submission_id!r} is not stored in "
                f"{self._directory!s}."
            )
        try:
            path.unlink()
        except OSError as exc:
            raise SubmissionStoreError(
                f"Could not delete submission record {path!s}: {exc}"
            ) from exc


__all__ = [
    "SUBMISSION_SCHEMA_VERSION",
    "SubmissionLifecycleStore",
    "default_submission_directory",
]