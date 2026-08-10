"""
Experiment registry (Sprint 11K).

The :class:`ExperimentRegistry` sits ABOVE the filesystem
persistence layer and integrates it with the existing Sprint 11J
experiment comparison / reporting layer.

It supports the operations required to persist completed
experiments and later retrieve and compare them WITHOUT rerunning
the underlying trading pipeline:

* ``register(result, overwrite=False)`` -- persist a result.
* ``get(experiment_id)`` -- load and verify a stored result.
* ``exists(experiment_id)`` -- cheap existence check.
* ``list()`` -- sorted stored experiment ids.
* ``delete(experiment_id)`` -- explicit removal.
* ``load_many(ids)`` -- retrieve multiple results.
* ``compare(ids)`` -- compare stored experiments using the
  EXISTING :class:`ExperimentComparisonEngine` (no comparison
  logic is duplicated).

Design rules:

* Never silently overwrite. ``register`` refuses to replace an
  existing experiment with the same id unless ``overwrite=True``.

* Integrity verification on every load. The reconstructed result's
  identity metadata (experiment id, configuration hash, dataset
  content hash) is checked for internal consistency before it is
  returned.

* No global mutable state. The registry holds only the immutable
  persistence layer it was constructed with.

* Cross-platform paths via :mod:`pathlib`; no hard-coded absolute
  paths.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

from engine.experiment.comparison import ExperimentComparisonEngine
from engine.models.experiment import (
    ExperimentComparison,
    ExperimentResult,
)
from engine.registry.exceptions import (
    ExperimentNotFoundError,
    ExperimentPersistenceError,
)
from engine.registry.persistence import (
    ExperimentPersistence,
    default_registry_directory,
)


# ============================================================
# REGISTRY
# ============================================================


class ExperimentRegistry:
    """
    A registry of completed, reproducible experiment results.

    The registry wraps an :class:`ExperimentPersistence` and adds
    registry-level operations (existence, listing, multi-load and
    comparison) on top of it.

    Public API:

        register(result, overwrite=False) -> ExperimentResult
        get(experiment_id) -> ExperimentResult
        exists(experiment_id) -> bool
        list() -> list[str]
        delete(experiment_id) -> None
        load_many(ids) -> list[ExperimentResult]
        compare(ids) -> ExperimentComparison
        verify_identity(result) -> None

    Parameters:

    directory
        Directory the registry stores experiment records in. When
        ``None`` the persistence layer's default
        (``./experiments`` relative to the current working
        directory) is used. Pass an explicit directory for a
        fixed, reproducible location.

    persistence
        Optional pre-constructed :class:`ExperimentPersistence`.
        When supplied it takes precedence over ``directory`` so
        callers can reuse an existing persistence instance (e.g.
        for tests or shared registries).
    """

    def __init__(
        self,
        directory: Path | str | None = None,
        persistence: ExperimentPersistence | None = None,
    ) -> None:
        if persistence is not None:
            self._persistence = persistence
        else:
            self._persistence = ExperimentPersistence(directory)

        self._comparison_engine = ExperimentComparisonEngine()

    # -----------------------------------------------------
    # PROPERTIES
    # -----------------------------------------------------

    @property
    def persistence(self) -> ExperimentPersistence:
        """The underlying persistence layer."""

        return self._persistence

    @property
    def directory(self) -> Path:
        """The directory this registry stores experiment records in."""

        return self._persistence.directory

    # -----------------------------------------------------
    # WRITE
    # -----------------------------------------------------

    def register(
        self,
        result: ExperimentResult,
        overwrite: bool = False,
    ) -> ExperimentResult:
        """
        Persist a completed experiment result.

        The result's identity is verified before writing. When
        ``overwrite`` is False and an experiment with the same id
        is already stored, raises
        :class:`ExperimentAlreadyExistsError` so a caller can never
        silently replace a stored experiment.

        Returns the registered result unchanged.
        """

        self._persistence.save(result, overwrite=overwrite)
        return result

    # -----------------------------------------------------
    # READ
    # -----------------------------------------------------

    def get(self, experiment_id: str) -> ExperimentResult:
        """
        Load and verify a stored experiment by id.

        Raises :class:`ExperimentNotFoundError` when no record
        exists and :class:`ExperimentIntegrityError` when the
        loaded identity is internally inconsistent.
        """

        return self._persistence.load(experiment_id)

    def exists(self, experiment_id: str) -> bool:
        """Whether an experiment with the given id is stored."""

        return self._persistence.exists(experiment_id)

    def list(self) -> list[str]:
        """Sorted list of stored experiment ids."""

        return self._persistence.list_experiments()

    # -----------------------------------------------------
    # DELETE
    # -----------------------------------------------------

    def delete(self, experiment_id: str) -> None:
        """
        Delete a stored experiment by id.

        Raises :class:`ExperimentNotFoundError` when absent.
        """

        self._persistence.delete(experiment_id)

    # -----------------------------------------------------
    # MULTI-LOAD + COMPARISON
    # -----------------------------------------------------

    def load_many(
        self,
        ids: Iterable[str],
    ) -> list[ExperimentResult]:
        """
        Load multiple stored experiments, preserving order.

        Missing ids raise :class:`ExperimentNotFoundError` so a
        partial comparison is never silently produced.
        """

        return [self.get(experiment_id) for experiment_id in ids]

    def compare(
        self,
        ids: Sequence[str],
    ) -> ExperimentComparison:
        """
        Compare stored experiments WITHOUT rerunning them.

        The experiments are loaded from the registry and passed to
        the EXISTING :class:`ExperimentComparisonEngine`. No
        comparison logic is duplicated here; this method is pure
        integration.

        Raises :class:`ExperimentNotFoundError` for any missing id
        so a comparison can never silently omit an experiment.
        """

        results = self.load_many(ids)
        return self._comparison_engine.compare(results)

    # -----------------------------------------------------
    # INTEGRITY
    # -----------------------------------------------------

    @staticmethod
    def verify_identity(result: ExperimentResult) -> None:
        """
        Verify a result's identity metadata is internally
        consistent (delegates to the persistence integrity check).

        Raises :class:`ExperimentIntegrityError` on mismatch. This
        is the same check applied on every ``register`` / ``get``.
        """

        ExperimentPersistence._verify_identity(result)


__all__ = [
    "ExperimentRegistry",
    "default_registry_directory",
]
