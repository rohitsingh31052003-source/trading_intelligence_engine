"""
Suite manifest persistence (Sprint 11M).

The suite manifest is a THIN artifact stored ALONGSIDE the existing
per-experiment records (Sprint 11K). It does NOT duplicate any
experiment data: it stores only the suite identity plus an ordered list
of member experiment ids. The heavy per-experiment data stays in the
existing per-experiment records produced by the Sprint 11K persistence
layer.

Storage layout::

    <directory>/
        <suite_id>.suite.json
        <suite_id>.suite.json
        ...
        (<experiment_id>.json files remain owned by Sprint 11K)

One manifest file per suite id. The ``.suite.json`` suffix keeps suite
manifests visually distinct from the Sprint 11K ``.json`` experiment
records in a shared directory and prevents a suite id from ever
colliding with an experiment id on disk.

Design rules:

* Atomic writes (reusing the Sprint 11K discipline).
  Each manifest is written to a temporary file in the SAME directory,
  flushed and fsynced (best-effort), then atomically renamed onto its
  final path so a partially written manifest is never left behind.

* Safe ids (reusing the Sprint 11K validation).
  Suite ids are validated against the same safe-character regex the
  Sprint 11K persistence layer uses for experiment ids, so a suite id
  can never escape the registry directory through path traversal.

* Schema-aware.
  The loader validates the suite schema version before reconstructing
  any model, raising :class:`UnsupportedSuiteSchemaVersionError` on a
  future version.

* No silent error swallowing.
  Corrupted JSON, missing manifests and integrity failures surface
  typed exceptions.

* No duplication of experiment persistence.
  This module persists ONLY the suite manifest. Member experiments are
  loaded from / saved to the existing Sprint 11K experiment registry.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from engine.models.suite import SUITE_SCHEMA_VERSION
from engine.registry.persistence import _SAFE_ID_RE, _validate_id
from engine.suite.exceptions import (
    SuiteAlreadyExistsError,
    SuiteIntegrityError,
    SuiteNotFoundError,
    SuiteError,
    UnsupportedSuiteSchemaVersionError,
)


#: Suffix used for persisted suite manifests. This is intentionally NOT
#: a bare ``.json`` suffix: the Sprint 11K ``ExperimentRegistry.list()``
#: scans the shared directory for any file ending in ``.json`` and a
#: safe-id stem, so a manifest named ``<id>.json`` would pollute the
#: experiment listing. The ``.suite`` marker makes the manifest visually
#: distinct AND ensures it is never mistaken for an experiment record.
_SUITE_MANIFEST_SUFFIX = ".suite"


# ============================================================
# MANIFEST PAYLOAD
# ============================================================


def _manifest_payload(
    suite_id: str,
    configuration_hash: str,
    label: str,
    member_experiment_ids: tuple[str, ...],
    metadata: dict[str, str],
) -> dict[str, Any]:
    """
    Build the deterministic manifest payload.

    Keys are written in a fixed order for human readability; the
    persisted text is canonicalized (sorted keys) on write so identical
    manifests always produce identical bytes.
    """

    return {
        "schema_version": SUITE_SCHEMA_VERSION,
        "suite_id": suite_id,
        "configuration_hash": configuration_hash,
        "label": label,
        "member_experiment_ids": list(member_experiment_ids),
        "metadata": dict(metadata),
    }


# ============================================================
# MANIFEST PERSISTENCE
# ============================================================


class SuiteManifestPersistence:
    """
    Atomic, filesystem-based persistence for suite manifests.

    Public API:

        save(suite_id, configuration_hash, label,
             member_experiment_ids, metadata, overwrite=False) -> Path
        load(suite_id) -> dict
        exists(suite_id) -> bool
        list_suites() -> list[str]
        delete(suite_id) -> None
        path_for(suite_id) -> Path

    The manifest layer is stateless across calls: identical inputs
    always produce identical on-disk content. It holds only the
    immutable ``directory`` it was constructed with.

    When ``directory`` is ``None`` the Sprint 11K default registry
    directory (``./experiments`` relative to the current working
    directory) is reused so suite manifests live alongside their member
    experiment records by default.
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
        """The directory this manifest layer writes to."""

        return self._directory

    def path_for(self, suite_id: str) -> Path:
        """The on-disk path for a given suite id."""

        _validate_id(suite_id)
        return self._directory / f"{suite_id}{_SUITE_MANIFEST_SUFFIX}"

    # -----------------------------------------------------
    # WRITE
    # -----------------------------------------------------

    def save(
        self,
        suite_id: str,
        configuration_hash: str,
        label: str,
        member_experiment_ids: tuple[str, ...],
        metadata: dict[str, str],
        overwrite: bool = False,
    ) -> Path:
        """
        Persist a suite manifest atomically.

        When ``overwrite`` is False and a manifest with the same id
        already exists, raises :class:`SuiteAlreadyExistsError`.
        """

        _validate_id(suite_id)

        self._ensure_directory()

        target = self.path_for(suite_id)

        if target.exists() and not overwrite:
            raise SuiteAlreadyExistsError(suite_id)

        payload = _manifest_payload(
            suite_id=suite_id,
            configuration_hash=configuration_hash,
            label=label,
            member_experiment_ids=member_experiment_ids,
            metadata=metadata,
        )

        text = json.dumps(payload, sort_keys=True, ensure_ascii=False)

        self._atomic_write(target, text)

        return target

    def _atomic_write(self, target: Path, text: str) -> None:
        """
        Write ``text`` to ``target`` atomically.

        Reuses the Sprint 11K atomic-write discipline: temp file in the
        SAME directory, flush, fsync (best-effort), atomic ``os.replace``.
        No partially written target file is ever left.
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
            raise SuiteError(
                f"Failed to atomically write suite manifest "
                f"{target!s}: {exc}"
            ) from exc

    # -----------------------------------------------------
    # READ
    # -----------------------------------------------------

    def load(self, suite_id: str) -> dict[str, Any]:
        """
        Load and return the raw manifest mapping for a suite id.

        Raises :class:`SuiteNotFoundError` when no manifest exists,
        :class:`UnsupportedSuiteSchemaVersionError` on a future schema
        version, and :class:`SuiteIntegrityError` on corrupted JSON or
        a file-name / stored-id mismatch.
        """

        _validate_id(suite_id)
        path = self.path_for(suite_id)

        if not path.exists():
            raise SuiteNotFoundError(
                f"Suite {suite_id!r} is not stored in "
                f"{self._directory!s}."
            )

        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SuiteError(
                f"Could not read suite manifest {path!s}: {exc}"
            ) from exc

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SuiteIntegrityError(
                f"Suite manifest {suite_id!r} is not valid JSON: {exc}"
            ) from exc

        if not isinstance(parsed, dict):
            raise SuiteIntegrityError(
                f"Suite manifest {suite_id!r} is not a JSON object."
            )

        # Schema version check BEFORE any further interpretation.
        found_version = parsed.get("schema_version")
        if found_version != SUITE_SCHEMA_VERSION:
            raise UnsupportedSuiteSchemaVersionError(
                found=found_version,
                supported=SUITE_SCHEMA_VERSION,
            )

        stored_id = parsed.get("suite_id")
        if stored_id != suite_id:
            raise SuiteIntegrityError(
                f"File name suite id {suite_id!r} does not match the "
                f"stored id {stored_id!r}."
            )

        return parsed

    # -----------------------------------------------------
    # QUERIES
    # -----------------------------------------------------

    def exists(self, suite_id: str) -> bool:
        """Whether a manifest with the given id is stored."""

        _validate_id(suite_id)
        return self.path_for(suite_id).exists()

    def list_suites(self) -> list[str]:
        """
        Sorted list of stored suite ids.

        Only files matching the manifest suffix and a safe id are
        considered; stray files in the directory are ignored. Experiment
        records (``.json``) from Sprint 11K are NOT considered suite
        manifests.
        """

        if not self._directory.exists():
            return []

        ids: list[str] = []
        for entry in self._directory.iterdir():
            if not entry.is_file():
                continue
            name = entry.name
            if not name.endswith(_SUITE_MANIFEST_SUFFIX):
                continue
            stem = name[: -len(_SUITE_MANIFEST_SUFFIX)]
            if _SAFE_ID_RE.match(stem):
                ids.append(stem)
        return sorted(ids)

    # -----------------------------------------------------
    # DELETE
    # -----------------------------------------------------

    def delete(self, suite_id: str) -> None:
        """
        Delete a stored suite manifest.

        Raises :class:`SuiteNotFoundError` when no manifest exists.
        Deleting a suite manifest does NOT delete its member experiment
        records (those remain in the Sprint 11K experiment registry).
        """

        _validate_id(suite_id)
        path = self.path_for(suite_id)
        if not path.exists():
            raise SuiteNotFoundError(
                f"Suite {suite_id!r} is not stored in "
                f"{self._directory!s}."
            )
        try:
            path.unlink()
        except OSError as exc:
            raise SuiteError(
                f"Could not delete suite manifest {path!s}: {exc}"
            ) from exc

    # -----------------------------------------------------
    # INTERNAL
    # -----------------------------------------------------

    def _ensure_directory(self) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)


__all__ = [
    "SuiteManifestPersistence",
]
