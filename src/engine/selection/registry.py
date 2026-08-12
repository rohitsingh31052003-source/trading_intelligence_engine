"""
Selection registry (Sprint 11N).

The :class:`SelectionRegistry` sits ABOVE the selection persistence
layer. It persists completed selection decisions and later retrieves
them WITHOUT rerunning the trading pipeline, the experiment runner or
the suite runner.

It supports:

* ``register_selection(result, overwrite=False)`` -- persist a decision.
* ``load_selection(selection_id)`` -- reconstruct a decision from
  persisted data (no pipeline / runner rerun).
* ``exists(selection_id)`` / ``list()`` / ``delete(selection_id)``.

Design rules:

* Never silently overwrite. ``register_selection`` refuses to replace
  an existing decision with the same id unless ``overwrite=True``.

* Integrity verification on every load. The reconstructed decision's
  selection id is checked for internal consistency.

* No global mutable state. The registry holds only the immutable
  persistence layer it was constructed with. Cross-platform paths via
  :mod:`pathlib`; no hard-coded absolute paths.

* ``load_selection`` operates ENTIRELY from persisted data; the trading
  pipeline, the experiment runner and the suite runner are never rerun.
"""

from __future__ import annotations

from pathlib import Path

from engine.models.selection import SelectionResult
from engine.selection.exceptions import SelectionIntegrityError
from engine.selection.persistence import SelectionPersistence


# ============================================================
# SELECTION REGISTRY
# ============================================================


class SelectionRegistry:
    """
    A registry of completed, reproducible selection decisions.

    Public API:

        register_selection(result, overwrite=False) -> SelectionResult
        load_selection(selection_id) -> SelectionResult
        exists(selection_id) -> bool
        list() -> list[str]
        delete(selection_id) -> None
        verify_identity(result) -> None

    Parameters:

    directory
        Directory the selection decisions are stored in. When ``None``
        the Sprint 11K default registry directory is reused so selection
        decisions live alongside their source experiment / suite
        records.

    persistence
        Optional pre-constructed :class:`SelectionPersistence`. When
        supplied it takes precedence over ``directory`` so callers can
        reuse an existing persistence instance (e.g. for tests or
        shared registries).
    """

    def __init__(
        self,
        directory: Path | str | None = None,
        persistence: SelectionPersistence | None = None,
    ) -> None:
        if persistence is not None:
            self._persistence = persistence
        else:
            self._persistence = SelectionPersistence(directory)

    # -----------------------------------------------------
    # PROPERTIES
    # -----------------------------------------------------

    @property
    def persistence(self) -> SelectionPersistence:
        """The underlying selection persistence layer."""

        return self._persistence

    @property
    def directory(self) -> Path:
        """The directory this registry stores selection decisions in."""

        return self._persistence.directory

    # -----------------------------------------------------
    # WRITE
    # -----------------------------------------------------

    def register_selection(
        self,
        result: SelectionResult,
        overwrite: bool = False,
    ) -> SelectionResult:
        """
        Persist a completed selection decision.

        The decision identity is verified before writing. When
        ``overwrite`` is False and a decision with the same id is
        already stored, raises :class:`SelectionAlreadyExistsError` so a
        caller can never silently replace a stored decision.

        Returns the registered result unchanged.
        """

        SelectionPersistence.verify_identity(result)
        self._persistence.save(result, overwrite=overwrite)
        return result

    # -----------------------------------------------------
    # READ
    # -----------------------------------------------------

    def load_selection(self, selection_id: str) -> SelectionResult:
        """
        Reconstruct a :class:`SelectionResult` from persisted data.

        The decision is loaded from the filesystem (persisted data only;
        the trading pipeline, the experiment runner and the suite runner
        are never rerun). The reconstructed decision's identity is
        verified for internal consistency before it is returned.

        Raises :class:`SelectionNotFoundError` when no decision exists,
        :class:`SelectionIntegrityError` when the identity is
        inconsistent.
        """

        result = self._persistence.load(selection_id)
        SelectionPersistence.verify_identity(result)
        return result

    def exists(self, selection_id: str) -> bool:
        """Whether a decision with the given id is stored."""

        return self._persistence.exists(selection_id)

    def list(self) -> list[str]:
        """Sorted list of stored selection ids."""

        return self._persistence.list_selections()

    # -----------------------------------------------------
    # DELETE
    # -----------------------------------------------------

    def delete(self, selection_id: str) -> None:
        """
        Delete a stored selection decision by id.

        Raises :class:`SelectionNotFoundError` when absent.
        """

        self._persistence.delete(selection_id)

    # -----------------------------------------------------
    # INTEGRITY
    # -----------------------------------------------------

    @staticmethod
    def verify_identity(result: SelectionResult) -> None:
        """
        Verify a result's identity metadata is internally consistent
        (delegates to the persistence integrity check).
        """

        SelectionPersistence.verify_identity(result)


__all__ = ["SelectionRegistry"]
