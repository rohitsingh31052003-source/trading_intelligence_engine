"""
Exceptions for the experiment suite / batch orchestration layer
(Sprint 11M).

These exceptions make suite failure modes EXPLICIT. The suite layer
never swallows errors silently: a corrupted, mismatched or unsupported
suite manifest surfaces a clear, typed exception rather than being
accepted as valid data.

Exception hierarchy:

    SuiteError
        Base class for all suite orchestration / persistence failures.

    SuiteNotFoundError(SuiteError)
        No suite with the requested id is stored.

    SuiteAlreadyExistsError(SuiteError)
        A suite with the same id is already stored and the caller did
        not request an explicit overwrite.

    SuiteIntegrityError(SuiteError)
        A loaded suite's identity metadata is internally inconsistent
        (suite id / configuration hash mismatch, a referenced member
        experiment is missing from the experiment registry, or tampered
        manifest content).

    UnsupportedSuiteSchemaVersionError(SuiteError)
        The persisted manifest uses a schema version this loader does
        not support.
"""

from __future__ import annotations


class SuiteError(Exception):
    """
    Base class for all experiment suite orchestration / persistence
    errors.

    All Sprint 11M suite failures derive from this class so callers can
    catch the whole family with a single ``except``.
    """


class SuiteNotFoundError(SuiteError):
    """No suite with the requested id is stored in the suite registry."""


class SuiteAlreadyExistsError(SuiteError):
    """
    A suite with the same id is already stored.

    Raised when ``register_suite`` / ``save_manifest`` is called for an
    id that already exists and the caller did not request an explicit
    overwrite (``overwrite=False``).
    """

    def __init__(self, suite_id: str) -> None:
        self.suite_id = suite_id
        super().__init__(
            f"Suite {suite_id!r} already exists in the suite "
            f"registry; pass overwrite=True to replace it."
        )


class SuiteIntegrityError(SuiteError):
    """
    A loaded suite's identity metadata is internally inconsistent.

    This is raised when:
    * the manifest suite id does not agree with the stored id or the
      recomputed id;
    * a referenced member experiment id is missing from the experiment
      registry (a suite is not resilient to member deletion by design
      -- honest failure beats silent partial data);
    * the manifest content was tampered with or is otherwise corrupted.
    """


class UnsupportedSuiteSchemaVersionError(SuiteError):
    """
    The persisted suite manifest uses a schema version this loader does
    not support.

    Future sprints may evolve the persisted suite manifest
    representation; each bump of the suite schema version lets this
    loader reject manifests it cannot safely interpret instead of
    guessing. Migration support can be added later without rewriting
    the suite system.
    """

    def __init__(self, found: object, supported: int) -> None:
        self.found = found
        self.supported = supported
        super().__init__(
            f"Unsupported suite manifest schema version: found "
            f"{found!r}, supported {supported}. A newer release may "
            f"be required to read this suite manifest."
        )


__all__ = [
    "SuiteAlreadyExistsError",
    "SuiteError",
    "SuiteIntegrityError",
    "SuiteNotFoundError",
    "UnsupportedSuiteSchemaVersionError",
]
