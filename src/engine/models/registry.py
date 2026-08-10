"""
Domain models for the experiment registry / persistence layer
(Sprint 11K).

This layer sits ABOVE the experiment framework (Sprint 11J). It
introduces a versioned persistence schema so completed experiment
results can be stored, retrieved and compared later WITHOUT
rerunning the underlying trading pipeline.

Dependency direction:

    models
       ↑
    intelligence engines
       ↑
    pipeline / orchestration
       ↑
    reporting / aggregation
       ↑
    research / robustness
       ↑
    experiment framework
       ↑
    registry / persistence

Design rules:

* No duplication of any existing logic. The persisted record is a
  faithful projection of an existing ``ExperimentResult``; the
  registry loads it back into the SAME model types used live.

* Immutable frozen+slots dataclasses for new models.

* A single, explicit ``SCHEMA_VERSION`` identifies the persisted
  representation. The loader rejects unsupported (e.g. future)
  versions clearly so migration support can be added later
  without rewriting the experiment system.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


# ============================================================
# SCHEMA VERSION
# ============================================================


#: Current persistence schema version.
#:
#: Bump this when the persisted representation changes in a way an
#: older loader cannot safely interpret. The loader rejects any
#: record whose ``schema_version`` differs from this value with an
#: ``UnsupportedSchemaVersionError`` so future migration support
#: can be layered in without rewriting the experiment system.
SCHEMA_VERSION: int = 1


# ============================================================
# PERSISTED RECORD HEADER
# ============================================================


@dataclass(frozen=True, slots=True)
class ExperimentRecordHeader:
    """
    Immutable header written at the top of every persisted record.

    The header carries the persistence ``schema_version`` and the
    deterministic ``experiment_id`` of the experiment. It is the
    first thing the loader inspects: an unsupported schema version
    is rejected before any deserialization is attempted.

    Field semantics:

    schema_version
        Version of the persistence schema used to write the
        record. Must equal :data:`SCHEMA_VERSION` for the current
        loader to accept it.

    experiment_id
        Deterministic identifier of the experiment. Used as the
        storage key (one file per id) and verified against the
        reconstructed result during integrity checking.
    """

    schema_version: int
    experiment_id: str


# ============================================================
# PERSISTED RECORD
# ============================================================


@dataclass(frozen=True, slots=True)
class PersistedExperimentRecord:
    """
    The full persisted representation of one experiment.

    This is the in-memory form of a record AFTER deserialization.
    It bundles the schema header with the reconstructed
    ``ExperimentResult`` and the canonical JSON text it was loaded
    from (so callers can audit / hash the exact bytes on disk).

    Field semantics:

    header
        Schema version + experiment id.

    result
        The reconstructed :class:`ExperimentResult`, reusing the
        exact same model types the live experiment framework
        produces. Downstream comparison and reporting layers work
        on it unchanged.

    canonical_json
        The canonical (sorted-key) JSON text the record was
        deserialized from. Identical experiment content always
        produces an identical canonical text, so this is suitable
        for byte-level identity / audit comparisons.

    raw
        The raw parsed mapping (deserialized JSON object). Retained
        for inspection / future migration tooling.
    """

    header: ExperimentRecordHeader
    result: Any
    canonical_json: str
    raw: Mapping[str, Any] = field(default_factory=dict)
