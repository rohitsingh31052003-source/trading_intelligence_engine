"""
Experiment Suite / Batch Orchestration & Analysis Layer (Sprint 11M).

This package sits ABOVE the experiment query / filtering / analysis
layer (Sprint 11L). It makes a *batch of experiments run together as a
named comparison campaign* reproducible, identifiable, persistable and
analysable as a unit -- WITHOUT duplicating any trading, validation,
pipeline, research, registry, query or comparison logic.

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
       ↑
    query / filtering / analysis
       ↑
    suite / batch orchestration

The suite layer implements NO trading, validation, performance,
pipeline, research, experiment, registry, query or comparison logic.
Every value is read from the reused ``ExperimentResult`` objects
produced by the existing engines. This is the seventh orchestration
package (after pipeline, reporting, research, experiment, registry and
query) and therefore re-exports its public API for convenience.

Evidence safety is preserved from Sprint 11J/11L: descriptive
suite-level leaders are populated ONLY when at least one member has
SUFFICIENT evidence and are ``None`` otherwise. INSUFFICIENT is
unobserved, NOT zero performance. The layer never turns a descriptive
historical result into a predictive claim.
"""

from engine.models.suite import (
    SUITE_SCHEMA_VERSION,
    SuiteComparison,
    SuiteComparisonRow,
    SuiteReproducibilityMetadata,
    SuiteResult,
    SuiteSummary,
)
from engine.suite.analysis import SuiteAnalysisEngine
from engine.suite.config import SuiteConfig
from engine.suite.exceptions import (
    SuiteAlreadyExistsError,
    SuiteError,
    SuiteIntegrityError,
    SuiteNotFoundError,
    UnsupportedSuiteSchemaVersionError,
)
from engine.suite.manifest import SuiteManifestPersistence
from engine.suite.registry import SuiteRegistry
from engine.suite.runner import SuiteRunner

__all__ = [
    "SUITE_SCHEMA_VERSION",
    "SuiteAlreadyExistsError",
    "SuiteAnalysisEngine",
    "SuiteComparison",
    "SuiteComparisonRow",
    "SuiteConfig",
    "SuiteError",
    "SuiteIntegrityError",
    "SuiteManifestPersistence",
    "SuiteNotFoundError",
    "SuiteRegistry",
    "SuiteReproducibilityMetadata",
    "SuiteResult",
    "SuiteRunner",
    "SuiteSummary",
    "UnsupportedSuiteSchemaVersionError",
]
