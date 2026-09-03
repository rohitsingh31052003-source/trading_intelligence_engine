"""
Filesystem persistence for execution authorizations (Checkpoint 15.5).

The persistence layer stores :class:`~engine.models.execution_authorization.ExecutionAuthorization`
objects as deterministic JSON files so they can be retrieved after a
process restart WITHOUT recomputing the authorization.

Storage layout::

    <directory>/
        <authorization_id>.json
        <authorization_id>.json
        ...

One file per authorization id. No arbitrary files are written outside
the designated directory.

Design rules:

* Atomic writes. Each record is written to a temporary file, flushed
  and closed, then atomically renamed onto its final path so a
  partially written authorization file is never left behind.
* Cross-platform paths. All path construction uses :mod:`pathlib`
  (no hard-coded path separators); works from the project root on
  Windows and Linux.
* Schema-aware. The loader validates the schema version before
  reconstructing any model, raising
  :class:`UnsupportedAuthorizationSchemaVersionError` on a future
  version.
* No silent error swallowing. Corrupted JSON, missing records and
  integrity failures surface typed exceptions.
* Identity-integrity. The stored authorization_id must agree with the
  reconstructed record's authorization_id. A mismatch is an integrity
  failure, never silently accepted.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from engine.models.execution_authorization import ExecutionAuthorization
from engine.persistence.exceptions import (
    AuthorizationIntegrityError,
    AuthorizationNotFoundError,
    AuthorizationStoreError,
    UnsupportedAuthorizationSchemaVersionError,
)
from engine.persistence.execution_authorization_serialization import (
    AUTHORIZATION_SCHEMA_VERSION,
    canonical_authorization_json,
    deserialize_authorization,
    parse_authorization_header,
    serialize_authorization,
)


# An authorization id is a deterministic opaque token. We restrict
# stored file names to safe characters so the id can never escape
# the store directory through path traversal.
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")

#: Suffix used for persisted authorization records.
_RECORD_SUFFIX = ".json"


# ============================================================
# HELPERS
# ============================================================


def _validate_id(authorization_id: str) -> None:
    """Ensure an authorization id is safe to use as a file name."""

    if not authorization_id or not _SAFE_ID_RE.match(authorization_id):
        raise AuthorizationStoreError(
            f"Unsafe authorization id {authorization_id!r}: ids must "
            f"match {_SAFE_ID_RE.pattern!r}."
        )


def default_authorization_directory() -> Path:
    """
    Default authorization directory, resolved relative to the current
    working directory (``./authorizations``).

    This avoids hard-coded absolute paths and global mutable state.
    Callers wanting a fixed location should pass an explicit
    ``directory`` to the store.
    """

    return Path.cwd() / "authorizations"


# ============================================================
# PERSISTENCE
# ============================================================


class ExecutionAuthorizationStore:
    """
    Atomic, filesystem-based persistence for execution authorizations.

    Public API:

        save(authorization, overwrite=False) -> Path
        load(authorization_id) -> ExecutionAuthorization
        exists(authorization_id) -> bool
        list_authorizations() -> list[str]
        delete(authorization_id) -> None
        path_for(authorization_id) -> Path

    The persistence layer is stateless across calls: identical
    inputs always produce identical on-disk content. It holds only
    the immutable ``directory`` it was constructed with.
    """

    def __init__(
        self,
        directory: Path | str | None = None,
    ) -> None:
        if directory is None:
            directory = default_authorization_directory()
        self._directory = Path(directory)

    # -----------------------------------------------------
    # PROPERTIES
    # -----------------------------------------------------

    @property
    def directory(self) -> Path:
        """The directory this persistence layer writes to."""

        return self._directory

    def path_for(self, authorization_id: str) -> Path:
        """The on-disk path for a given authorization id."""

        _validate_id(authorization_id)
        return self._directory / f"{authorization_id}{_RECORD_SUFFIX}"

    # -----------------------------------------------------
    # WRITE
    # -----------------------------------------------------

    def save(
        self,
        authorization: ExecutionAuthorization,
        overwrite: bool = False,
    ) -> Path:
        """
        Persist an authorization atomically.

        When ``overwrite`` is ``False`` and an authorization with the
        same id already exists, the store compares the existing content
        with the new content:

        * Identical content → idempotent success (returns the path).
        * Different content → raises
          :class:`AuthorizationIntegrityError` so a caller can never
          silently overwrite a stored authorization with a different
          one that happens to share the same deterministic id.

        When ``overwrite`` is ``True``, the existing record is always
        replaced.
        """

        authorization_id = authorization.authorization_id
        _validate_id(authorization_id)

        self._ensure_directory()

        target = self.path_for(authorization_id)

        if target.exists() and not overwrite:
            existing_text = self._read_text(target)
            new_text = serialize_authorization(authorization)
            if existing_text != new_text:
                raise AuthorizationIntegrityError(
                    f"Authorization {authorization_id!r} already exists "
                    f"with different content. Pass overwrite=True to "
                    f"replace it explicitly."
                )
            return target

        text = serialize_authorization(authorization)

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
        except AuthorizationStoreError:
            raise
        except Exception as exc:
            # Clean up the temp file on any failure and surface a
            # typed persistence error (never swallow silently).
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise AuthorizationStoreError(
                f"Failed to atomically write authorization record "
                f"{target!s}: {exc}"
            ) from exc

    # -----------------------------------------------------
    # READ
    # -----------------------------------------------------

    def load(self, authorization_id: str) -> ExecutionAuthorization:
        """
        Load and reconstruct an authorization by id.

        The reconstructed authorization's identity is verified for
        internal consistency before it is returned. Raises
        :class:`AuthorizationNotFoundError` when no record exists,
        :class:`UnsupportedAuthorizationSchemaVersionError` on a
        future schema version, and
        :class:`AuthorizationIntegrityError` when the persisted
        identity is internally inconsistent or tampered with.
        """

        _validate_id(authorization_id)
        path = self.path_for(authorization_id)

        if not path.exists():
            raise AuthorizationNotFoundError(
                f"Authorization {authorization_id!r} is not stored in "
                f"{self._directory!s}."
            )

        text = self._read_text(path)

        # Schema version first (cheap), then full reconstruction.
        # Both steps can raise ValueError for malformed/unexpected
        # payloads — wrap as a typed store error so callers never
        # see a raw json / decode failure as a valid authorization.
        try:
            header = parse_authorization_header(text)
        except (ValueError, json.JSONDecodeError) as exc:
            raise AuthorizationStoreError(
                f"Could not parse authorization record header "
                f"{path!s}: {exc}"
            ) from exc

        if not isinstance(header, dict):
            raise AuthorizationStoreError(
                f"Malformed authorization record header in "
                f"{path!s}: expected a JSON object."
            )

        version = header.get("schema_version")
        if version != AUTHORIZATION_SCHEMA_VERSION:
            raise UnsupportedAuthorizationSchemaVersionError(
                f"Unsupported authorization schema version {version!r}; "
                f"supported is {AUTHORIZATION_SCHEMA_VERSION}."
            )

        try:
            authorization = deserialize_authorization(text)
        except (ValueError, json.JSONDecodeError) as exc:
            raise AuthorizationStoreError(
                f"Could not reconstruct authorization from "
                f"{path!s}: {exc}"
            ) from exc

        # Identity integrity: the loaded id must match the file-name id.
        if authorization.authorization_id != authorization_id:
            raise AuthorizationIntegrityError(
                f"Authorization id mismatch: file name "
                f"{authorization_id!r} vs stored id "
                f"{authorization.authorization_id!r}."
            )

        return authorization

    def _read_text(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise AuthorizationStoreError(
                f"Could not read authorization record {path!s}: {exc}"
            ) from exc

    # -----------------------------------------------------
    # QUERIES
    # -----------------------------------------------------

    def exists(self, authorization_id: str) -> bool:
        """Whether an authorization with the given id is stored."""

        _validate_id(authorization_id)
        return self.path_for(authorization_id).exists()

    def list_authorizations(self) -> list[str]:
        """
        Sorted list of stored authorization ids.

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

    def delete(self, authorization_id: str) -> None:
        """
        Delete a stored authorization.

        Raises :class:`AuthorizationNotFoundError` when no record
        exists. Deletion is intentionally explicit: callers must
        ask for it by id.
        """

        _validate_id(authorization_id)
        path = self.path_for(authorization_id)
        if not path.exists():
            raise AuthorizationNotFoundError(
                f"Authorization {authorization_id!r} is not stored in "
                f"{self._directory!s}."
            )
        try:
            path.unlink()
        except OSError as exc:
            raise AuthorizationStoreError(
                f"Could not delete authorization record {path!s}: {exc}"
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
    "AUTHORIZATION_SCHEMA_VERSION",
    "ExecutionAuthorizationStore",
    "default_authorization_directory",
]
