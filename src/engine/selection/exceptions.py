"""
Exceptions for the experiment selection & promotion layer (Sprint 11N).

These exceptions make selection failure modes EXPLICIT. The selection
layer never swallows errors silently: a corrupted, mismatched or
unsupported persisted selection decision surfaces a clear, typed
exception rather than being accepted as valid data.

Exception hierarchy:

    SelectionError
        Base class for all selection / persistence failures.

    SelectionNotFoundError(SelectionError)
        No selection decision with the requested id is stored.

    SelectionAlreadyExistsError(SelectionError)
        A selection decision with the same id is already stored and the
        caller did not request an explicit overwrite.

    SelectionIntegrityError(SelectionError)
        A loaded selection decision's identity metadata is internally
        inconsistent (selection id mismatch, tampered content, etc.).

    UnsupportedSelectionSchemaVersionError(SelectionError)
        The persisted decision uses a schema version this loader does
        not support.
"""

from __future__ import annotations


class SelectionError(Exception):
    """
    Base class for all experiment selection / persistence errors.

    All Sprint 11N selection failures derive from this class so callers
    can catch the whole family with a single ``except``.
    """


class SelectionNotFoundError(SelectionError):
    """No selection decision with the requested id is stored."""


class SelectionAlreadyExistsError(SelectionError):
    """
    A selection decision with the same id is already stored.

    Raised when ``register_selection`` / ``save`` is called for an id
    that already exists and the caller did not request an explicit
    overwrite (``overwrite=False``).
    """

    def __init__(self, selection_id: str) -> None:
        self.selection_id = selection_id
        super().__init__(
            f"Selection {selection_id!r} already exists in the "
            f"selection registry; pass overwrite=True to replace it."
        )


class SelectionIntegrityError(SelectionError):
    """
    A loaded selection decision's identity metadata is internally
    inconsistent.

    This is raised when the stored selection id does not agree with the
    file-name id or the recomputed id, or when the decision content was
    tampered with or is otherwise corrupted.
    """


class UnsupportedSelectionSchemaVersionError(SelectionError):
    """
    The persisted selection decision uses a schema version this loader
    does not support.

    Future sprints may evolve the persisted selection representation;
    each bump of the selection schema version lets this loader reject
    decisions it cannot safely interpret instead of guessing. Migration
    support can be added later without rewriting the selection system.
    """

    def __init__(self, found: object, supported: int) -> None:
        self.found = found
        self.supported = supported
        super().__init__(
            f"Unsupported selection decision schema version: found "
            f"{found!r}, supported {supported}. A newer release may "
            f"be required to read this selection decision."
        )


__all__ = [
    "SelectionAlreadyExistsError",
    "SelectionError",
    "SelectionIntegrityError",
    "SelectionNotFoundError",
    "UnsupportedSelectionSchemaVersionError",
]
