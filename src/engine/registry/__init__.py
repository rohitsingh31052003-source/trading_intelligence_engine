"""
Research Dataset / Experiment Registry and Result Persistence
Layer (Sprint 11K).

This package sits ABOVE the experiment framework (Sprint 11J). It
makes completed, reproducible experiment results persistable so
they can be retrieved and compared later WITHOUT rerunning the
underlying trading pipeline.

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

The registry implements NO trading, validation, performance,
pipeline, research or comparison logic. Every engine is reused
as-is. This is the fifth orchestration package (after pipeline,
reporting, research and experiment) and therefore re-exports its
public API for convenience.
"""

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
from engine.registry.persistence import (
    ExperimentPersistence,
    default_registry_directory,
)
from engine.registry.registry import ExperimentRegistry
from engine.registry.serialization import (
    canonical_json,
    deserialize_experiment,
    parse_record,
    serialize_experiment,
    serialize_experiment_bytes,
)

__all__ = [
    "SCHEMA_VERSION",
    "ExperimentAlreadyExistsError",
    "ExperimentIntegrityError",
    "ExperimentNotFoundError",
    "ExperimentPersistence",
    "ExperimentPersistenceError",
    "ExperimentRecordHeader",
    "ExperimentRegistry",
    "PersistedExperimentRecord",
    "UnsupportedSchemaVersionError",
    "canonical_json",
    "default_registry_directory",
    "deserialize_experiment",
    "parse_record",
    "serialize_experiment",
    "serialize_experiment_bytes",
]
