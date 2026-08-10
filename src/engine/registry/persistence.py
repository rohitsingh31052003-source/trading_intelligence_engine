"""
Filesystem-based persistence for experiment results (Sprint 11K).

The persistence layer stores completed :class:`ExperimentResult`
objects as deterministic JSON files so they can be retrieved and
compared later WITHOUT rerunning the underlying trading pipeline.

Storage layout::

    <directory>/
        <experiment_id>.json
        <experiment_id>.json
        ...

One file per experiment id. No arbitrary files are written
outside the designated directory.

Design rules:

* Atomic writes.
  Each record is written to a temporary file, flushed and closed,
  then atomically renamed onto its final path so a partially
  written experiment file is never left behind.

* Cross-platform paths.
  All path construction uses :mod:`pathlib` (no hard-coded path
  separators); works from the project root on Windows and Linux.

* Schema-aware.
  The loader validates the schema version before reconstructing
  any model, raising :class:`UnsupportedSchemaVersionError` on a
  future version.

* No silent error swallowing.
  Corrupted JSON, missing records and integrity failures surface
  typed exceptions.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from engine.models.experiment import ExperimentResult
from engine.models.registry import (
    SCHEMA_VERSION,
    ExperimentRecordHeader,
    PersistedExperimentRecord,
)
from engine.registry.exceptions import (
    ExperimentAlreadyExistsError,
    ExperimentIntegrityError,
    ExperimentNotFoundError,
    ExperimentPersistenceError,
    UnsupportedSchemaVersionError,
)
from engine.registry.serialization import (
    canonical_json,
    deserialize_experiment,
    parse_record,
    serialize_experiment,
)


# An experiment id is a deterministic opaque token. We restrict
# stored file names to safe characters so the id can never escape
# the registry directory through path traversal.
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")

#: Suffix used for persisted records.
_RECORD_SUFFIX = ".json"


# ============================================================
# HELPERS
# ============================================================


def _validate_id(experiment_id: str) -> None:
    """Ensure an experiment id is safe to use as a file name."""

    if not experiment_id or not _SAFE_ID_RE.match(experiment_id):
        raise ExperimentPersistenceError(
            f"Unsafe experiment id {experiment_id!r}: ids must "
            f"match {str(_SAFE_ID_RE.pattern)!r}."
        )


def default_registry_directory() -> Path:
    """
    Default registry directory, resolved relative to the current
    working directory (``./experiments``).

    This avoids hard-coded absolute paths and global mutable state.
    Callers wanting a fixed location should pass an explicit
    ``directory`` to the registry / persistence layer.
    """

    return Path.cwd() / "experiments"


# ============================================================
# PERSISTENCE
# ============================================================


class ExperimentPersistence:
    """
    Atomic, filesystem-based persistence for experiment results.

    Public API:

        save(result, overwrite=False) -> Path
        load(experiment_id) -> ExperimentResult
        load_record(experiment_id) -> PersistedExperimentRecord
        exists(experiment_id) -> bool
        list_experiments() -> list[str]
        delete(experiment_id) -> None
        path_for(experiment_id) -> Path

    The persistence layer is stateless across calls: identical
    inputs always produce identical on-disk content. It holds only
    the immutable ``directory`` it was constructed with.
    """

    def __init__(
        self,
        directory: Path | str | None = None,
    ) -> None:
        if directory is None:
            directory = default_registry_directory()
        self._directory = Path(directory)

    # -----------------------------------------------------
    # PROPERTIES
    # -----------------------------------------------------

    @property
    def directory(self) -> Path:
        """The directory this persistence layer writes to."""

        return self._directory

    def path_for(self, experiment_id: str) -> Path:
        """The on-disk path for a given experiment id."""

        _validate_id(experiment_id)
        return self._directory / f"{experiment_id}{_RECORD_SUFFIX}"

    # -----------------------------------------------------
    # WRITE
    # -----------------------------------------------------

    def save(
        self,
        result: ExperimentResult,
        overwrite: bool = False,
    ) -> Path:
        """
        Persist an experiment result atomically.

        When ``overwrite`` is False and an experiment with the
        same id already exists, raises
        :class:`ExperimentAlreadyExistsError` so a caller can never
        silently overwrite a stored experiment.
        """

        experiment_id = result.experiment_id
        _validate_id(experiment_id)

        self._ensure_directory()

        target = self.path_for(experiment_id)

        if target.exists() and not overwrite:
            raise ExperimentAlreadyExistsError(experiment_id)

        # Integrity pre-check: the id on the result must agree
        # with the config and reproducibility metadata before we
        # ever write to disk.
        self._verify_identity(result)

        text = serialize_experiment(result)

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
        except ExperimentPersistenceError:
            raise
        except Exception as exc:
            # Clean up the temp file on any failure and surface a
            # typed persistence error (never swallow silently).
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise ExperimentPersistenceError(
                f"Failed to atomically write experiment record "
                f"{target!s}: {exc}"
            ) from exc

    # -----------------------------------------------------
    # READ
    # -----------------------------------------------------

    def load(self, experiment_id: str) -> ExperimentResult:
        """
        Load and reconstruct an experiment by id.

        The reconstructed result's identity metadata is verified
        for internal consistency before it is returned.
        """

        record = self.load_record(experiment_id)
        return record.result

    def load_record(
        self,
        experiment_id: str,
    ) -> PersistedExperimentRecord:
        """
        Load the full persisted record (header + result + raw).

        Raises :class:`ExperimentNotFoundError` when no record
        exists, :class:`UnsupportedSchemaVersionError` on a
        future schema version, and
        :class:`ExperimentIntegrityError` when the persisted
        identity is internally inconsistent or tampered with.
        """

        _validate_id(experiment_id)
        path = self.path_for(experiment_id)

        if not path.exists():
            raise ExperimentNotFoundError(
                f"Experiment {experiment_id!r} is not stored in "
                f"{self._directory!s}."
            )

        text = self._read_text(path)

        # Header / schema version first (cheap).
        header = parse_record(text)

        # Sanity: the file name id must agree with the stored id.
        if header.experiment_id != experiment_id:
            raise ExperimentIntegrityError(
                f"File name experiment id {experiment_id!r} does "
                f"not match the stored id "
                f"{header.experiment_id!r}."
            )

        result = deserialize_experiment(text)

        self._verify_identity(result)

        return PersistedExperimentRecord(
            header=header,
            result=result,
            canonical_json=canonical_json(text),
            raw=_raw_mapping(text),
        )

    def _read_text(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ExperimentPersistenceError(
                f"Could not read experiment record {path!s}: {exc}"
            ) from exc

    # -----------------------------------------------------
    # QUERIES
    # -----------------------------------------------------

    def exists(self, experiment_id: str) -> bool:
        """Whether an experiment with the given id is stored."""

        _validate_id(experiment_id)
        return self.path_for(experiment_id).exists()

    def list_experiments(self) -> list[str]:
        """
        Sorted list of stored experiment ids.

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

    def delete(self, experiment_id: str) -> None:
        """
        Delete a stored experiment.

        Raises :class:`ExperimentNotFoundError` when no record
        exists. Deletion is intentionally explicit: callers must
        ask for it by id.
        """

        _validate_id(experiment_id)
        path = self.path_for(experiment_id)
        if not path.exists():
            raise ExperimentNotFoundError(
                f"Experiment {experiment_id!r} is not stored in "
                f"{self._directory!s}."
            )
        try:
            path.unlink()
        except OSError as exc:
            raise ExperimentPersistenceError(
                f"Could not delete experiment record {path!s}: {exc}"
            ) from exc

    # -----------------------------------------------------
    # INTEGRITY
    # -----------------------------------------------------

    @staticmethod
    def _verify_identity(result: ExperimentResult) -> None:
        """
        Verify that a result's identity metadata is internally
        consistent.

        Raises :class:`ExperimentIntegrityError` on any mismatch.
        """

        config = result.config

        # The config may be None for a hand-constructed minimal
        # result; in that case there is nothing to cross-check
        # against and we only require a non-empty experiment id.
        if config is None:
            if not result.experiment_id:
                raise ExperimentIntegrityError(
                    "Experiment result has no experiment id."
                )
            return

        config_id = _safe_get(config, "experiment_id")
        config_hash = _safe_get(config, "configuration_hash")

        repro = result.reproducibility

        if config_id is not None and config_id != result.experiment_id:
            raise ExperimentIntegrityError(
                f"Experiment id mismatch: result "
                f"{result.experiment_id!r} vs config {config_id!r}."
            )

        if repro is not None:
            if repro.experiment_id != result.experiment_id:
                raise ExperimentIntegrityError(
                    f"Experiment id mismatch: result "
                    f"{result.experiment_id!r} vs reproducibility "
                    f"{repro.experiment_id!r}."
                )

            if (
                config_hash is not None
                and repro.configuration_hash != config_hash
            ):
                raise ExperimentIntegrityError(
                    f"Configuration hash mismatch: config "
                    f"{config_hash!r} vs reproducibility "
                    f"{repro.configuration_hash!r}."
                )

            if (
                repro.dataset_content_hash is not None
                and result.dataset is not None
                and result.dataset.content_hash is not None
                and repro.dataset_content_hash
                != result.dataset.content_hash
            ):
                raise ExperimentIntegrityError(
                    f"Dataset content hash mismatch: dataset "
                    f"{result.dataset.content_hash!r} vs "
                    f"reproducibility {repro.dataset_content_hash!r}."
                )

    # -----------------------------------------------------
    # INTERNAL
    # -----------------------------------------------------

    def _ensure_directory(self) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)


# ============================================================
# MODULE HELPERS
# ============================================================


def _safe_get(obj: object, name: str) -> object:
    value = getattr(obj, name, None)
    return value


def _raw_mapping(text: str) -> dict[str, object]:
    import json

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


# Re-export the schema constant for convenience.
__all__ = [
    "SCHEMA_VERSION",
    "ExperimentPersistence",
    "default_registry_directory",
]
