"""
Filesystem-based persistence for selection decisions (Sprint 11N).

The persistence layer stores completed :class:`SelectionResult` objects
as deterministic JSON files so they can be retrieved later WITHOUT
rerunning the trading pipeline, the experiment runner or the suite
runner.

Storage layout::

    <directory>/
        <selection_id>.selection
        <selection_id>.selection
        ...

One file per selection id. The ``.selection`` suffix (intentionally NOT
``.json`` and NOT ``.suite``) keeps selection decisions visually
distinct from the Sprint 11K experiment records (``.json``) and the
Sprint 11M suite manifests (``.suite``) in a shared directory, and
prevents a selection id from ever polluting either listing.

Design rules (reusing the Sprint 11K / 11M discipline):

* Atomic writes. Each decision is written to a temporary file in the
  SAME directory, flushed and fsynced (best-effort), then atomically
  renamed onto its final path so a partially written file is never left
  behind.

* Safe ids (reusing the Sprint 11K validation). Selection ids are
  validated against the same safe-character regex the Sprint 11K
  persistence layer uses, so an id can never escape the directory
  through path traversal.

* Schema-aware. The loader validates the schema version before
  reconstructing any model, raising
  :class:`UnsupportedSelectionSchemaVersionError` on a future version.

* No silent error swallowing. Corrupted JSON, missing decisions and
  integrity failures surface typed exceptions.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from engine.models.selection import SELECTION_SCHEMA_VERSION, SelectionResult
from engine.registry.persistence import _SAFE_ID_RE
from engine.selection.exceptions import (
    SelectionAlreadyExistsError,
    SelectionIntegrityError,
    SelectionNotFoundError,
    SelectionError,
    UnsupportedSelectionSchemaVersionError,
)
from engine.selection.serialization import (
    deserialize_selection,
    serialize_selection,
)


def _validate_selection_id(selection_id: str) -> None:
    """
    Ensure a selection id is safe to use as a file name.

    Wraps the Sprint 11K safe-id rule but raises the selection-layer
    :class:`SelectionError` so callers catch the selection family.
    """

    if not selection_id or not _SAFE_ID_RE.match(selection_id):
        raise SelectionError(
            f"Unsafe selection id {selection_id!r}: ids must "
            f"match {str(_SAFE_ID_RE.pattern)!r}."
        )


#: Suffix used for persisted selection decisions. Intentionally NOT a
#: bare ``.json`` (Sprint 11K experiment records) and NOT ``.suite``
#: (Sprint 11M suite manifests) so a selection decision never pollutes
#: either listing in a shared directory.
_SELECTION_SUFFIX = ".selection"


# ============================================================
# SELECTION PERSISTENCE
# ============================================================


class SelectionPersistence:
    """
    Atomic, filesystem-based persistence for selection decisions.

    Public API:

        save(result, overwrite=False) -> Path
        load(selection_id) -> SelectionResult
        exists(selection_id) -> bool
        list_selections() -> list[str]
        delete(selection_id) -> None
        path_for(selection_id) -> Path

    The persistence layer is stateless across calls: identical inputs
    always produce identical on-disk content. It holds only the
    immutable ``directory`` it was constructed with.

    When ``directory`` is ``None`` the Sprint 11K default registry
    directory is reused so selection decisions live alongside their
    source experiment / suite records by default.
    """

    def __init__(
        self,
        directory: Path | str | None = None,
    ) -> None:
        if directory is None:
            from engine.registry.persistence import (
                default_registry_directory,
            )

            directory = default_registry_directory()
        self._directory = Path(directory)

    # -----------------------------------------------------
    # PROPERTIES
    # -----------------------------------------------------

    @property
    def directory(self) -> Path:
        """The directory this persistence layer writes to."""

        return self._directory

    def path_for(self, selection_id: str) -> Path:
        """The on-disk path for a given selection id."""

        _validate_selection_id(selection_id)
        return self._directory / f"{selection_id}{_SELECTION_SUFFIX}"

    # -----------------------------------------------------
    # WRITE
    # -----------------------------------------------------

    def save(
        self,
        result: SelectionResult,
        overwrite: bool = False,
    ) -> Path:
        """
        Persist a selection decision atomically.

        The decision identity is verified before writing. When
        ``overwrite`` is False and a decision with the same id already
        exists, raises :class:`SelectionAlreadyExistsError`.
        """

        _validate_selection_id(result.selection_id)

        self._ensure_directory()

        target = self.path_for(result.selection_id)

        if target.exists() and not overwrite:
            raise SelectionAlreadyExistsError(result.selection_id)

        text = serialize_selection(result)

        self._atomic_write(target, text)

        return target

    def _atomic_write(self, target: Path, text: str) -> None:
        """
        Write ``text`` to ``target`` atomically.

        Reuses the Sprint 11K atomic-write discipline: temp file in the
        SAME directory, flush, fsync (best-effort), atomic
        ``os.replace``. No partially written target file is ever left.
        """

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
        except Exception as exc:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise SelectionError(
                f"Failed to atomically write selection decision "
                f"{target!s}: {exc}"
            ) from exc

    # -----------------------------------------------------
    # READ
    # -----------------------------------------------------

    def load(self, selection_id: str) -> SelectionResult:
        """
        Load and verify a stored selection decision by id.

        Raises :class:`SelectionNotFoundError` when no decision exists,
        :class:`UnsupportedSelectionSchemaVersionError` on a future
        schema version, and :class:`SelectionIntegrityError` on
        corrupted JSON or a file-name / stored-id mismatch.
        """

        _validate_selection_id(selection_id)
        path = self.path_for(selection_id)

        if not path.exists():
            raise SelectionNotFoundError(
                f"Selection {selection_id!r} is not stored in "
                f"{self._directory!s}."
            )

        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SelectionError(
                f"Could not read selection decision {path!s}: {exc}"
            ) from exc

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SelectionIntegrityError(
                f"Selection decision {selection_id!r} is not valid "
                f"JSON: {exc}"
            ) from exc

        if not isinstance(parsed, dict):
            raise SelectionIntegrityError(
                f"Selection decision {selection_id!r} is not a JSON "
                f"object."
            )

        # Schema version check BEFORE any model reconstruction.
        found_version = parsed.get("schema_version")
        if found_version != SELECTION_SCHEMA_VERSION:
            raise UnsupportedSelectionSchemaVersionError(
                found=found_version,
                supported=SELECTION_SCHEMA_VERSION,
            )

        stored_id = parsed.get("selection_id")
        if stored_id != selection_id:
            raise SelectionIntegrityError(
                f"File name selection id {selection_id!r} does not "
                f"match the stored id {stored_id!r}."
            )

        return deserialize_selection(text)

    # -----------------------------------------------------
    # QUERIES
    # -----------------------------------------------------

    def exists(self, selection_id: str) -> bool:
        """Whether a decision with the given id is stored."""

        _validate_selection_id(selection_id)
        return self.path_for(selection_id).exists()

    def list_selections(self) -> list[str]:
        """
        Sorted list of stored selection ids.

        Only files matching the decision suffix and a safe id are
        considered; stray files in the directory are ignored. Experiment
        records (``.json``) and suite manifests (``.suite``) are NOT
        considered selection decisions.
        """

        if not self._directory.exists():
            return []

        ids: list[str] = []
        for entry in self._directory.iterdir():
            if not entry.is_file():
                continue
            name = entry.name
            if not name.endswith(_SELECTION_SUFFIX):
                continue
            stem = name[: -len(_SELECTION_SUFFIX)]
            if _SAFE_ID_RE.match(stem):
                ids.append(stem)
        return sorted(ids)

    # -----------------------------------------------------
    # DELETE
    # -----------------------------------------------------

    def delete(self, selection_id: str) -> None:
        """
        Delete a stored selection decision.

        Raises :class:`SelectionNotFoundError` when absent. Deleting a
        selection decision does NOT delete the experiments / suites it
        selected from (those remain in their registries).
        """

        _validate_selection_id(selection_id)
        path = self.path_for(selection_id)
        if not path.exists():
            raise SelectionNotFoundError(
                f"Selection {selection_id!r} is not stored in "
                f"{self._directory!s}."
            )
        try:
            path.unlink()
        except OSError as exc:
            raise SelectionError(
                f"Could not delete selection decision {path!s}: {exc}"
            ) from exc

    # -----------------------------------------------------
    # INTEGRITY
    # -----------------------------------------------------

    @staticmethod
    def verify_identity(result: SelectionResult) -> None:
        """
        Verify a selection result's identity is internally consistent.

        Currently the selection id is the authoritative identity (it is
        derived deterministically from the criteria / type / label /
        metadata, so there is no separate configuration hash to
        cross-check). A non-empty id is required.
        """

        if not result.selection_id:
            raise SelectionIntegrityError(
                "Selection result has no selection id."
            )

    # -----------------------------------------------------
    # INTERNAL
    # -----------------------------------------------------

    def _ensure_directory(self) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)


__all__ = ["SelectionPersistence"]
