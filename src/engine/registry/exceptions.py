"""
Exceptions for the experiment registry / persistence layer
(Sprint 11K).

These exceptions make failure modes EXPLICIT. The registry never
swallows errors silently: a corrupted, mismatched or unsupported
record surfaces a clear, typed exception rather than being
accepted as valid data.

Exception hierarchy:

    ExperimentPersistenceError
        Base class for all persistence / registry failures.

    ExperimentNotFoundError(ExperimentPersistenceError)
        No experiment with the requested id is stored.

    ExperimentAlreadyExistsError(ExperimentPersistenceError)
        An experiment with the same id is already stored and the
        caller did not request an explicit overwrite.

    ExperimentIntegrityError(ExperimentPersistenceError)
        A loaded experiment's identity metadata is internally
        inconsistent (experiment id / configuration hash / dataset
        hash mismatch, or tampered content).

    UnsupportedSchemaVersionError(ExperimentPersistenceError)
        The persisted record uses a schema version this loader
        does not support (e.g. a future version written by a
        newer release).
"""

from __future__ import annotations


class ExperimentPersistenceError(Exception):
    """
    Base class for all experiment persistence / registry errors.

    All Sprint 11K registry failures derive from this class so
    callers can catch the whole family with a single ``except``.
    """


class ExperimentNotFoundError(ExperimentPersistenceError):
    """No experiment with the requested id is stored in the registry."""


class ExperimentAlreadyExistsError(ExperimentPersistenceError):
    """
    An experiment with the same id is already stored.

    Raised when ``register`` / ``save`` is called for an id that
    already exists and the caller did not request an explicit
    overwrite (``overwrite=False``).
    """

    def __init__(self, experiment_id: str) -> None:
        self.experiment_id = experiment_id
        super().__init__(
            f"Experiment {experiment_id!r} already exists in the "
            f"registry; pass overwrite=True to replace it."
        )


class ExperimentIntegrityError(ExperimentPersistenceError):
    """
    A loaded experiment's identity metadata is internally
    inconsistent.

    This is raised when the persisted identity fields (experiment
    id, configuration hash, dataset content hash, evidence status)
    do not agree with each other or with the reconstructed result,
    which indicates the record was tampered with or corrupted.
    """


class UnsupportedSchemaVersionError(ExperimentPersistenceError):
    """
    The persisted record uses a schema version this loader does
    not support.

    Future sprints may evolve the persisted representation; each
    bump of the schema version lets this loader reject records it
    cannot safely interpret instead of guessing. Migration support
    can be added later without rewriting the experiment system.
    """

    def __init__(self, found: object, supported: int) -> None:
        self.found = found
        self.supported = supported
        super().__init__(
            f"Unsupported persistence schema version: found "
            f"{found!r}, supported {supported}. A newer release "
            f"may be required to read this record."
        )
