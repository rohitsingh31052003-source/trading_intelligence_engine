"""
Experiment Selection & Promotion Layer (Sprint 11N).

This package sits ABOVE the experiment suite / batch orchestration
layer (Sprint 11M). It makes a *defensible selection* among persisted
experiments (or persisted suites) reproducible, identifiable,
persistable and reportable -- WITHOUT rerunning the trading pipeline,
the experiment runner or the suite runner.

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
       ↑
    selection & promotion

The selection layer implements NO trading, validation, performance,
pipeline, research, experiment, registry, query, suite or comparison
logic. Every value is read from the persisted ``ExperimentResult``
(Sprint 11J/11K) or ``SuiteResult`` (Sprint 11M) objects produced by
the existing engines. This is the eighth orchestration package (after
pipeline, reporting, research, experiment, registry, query and suite)
and therefore re-exports its public API for convenience.

Evidence safety is STRUCTURAL, not merely reported:

* An INSUFFICIENT experiment can NEVER become CANDIDATE or SELECTED.
* A PARTIAL experiment can NEVER become SELECTED.
* A suite with no SUFFICIENT member is NOT_ELIGIBLE and can never be
  SELECTED (a suite is never selected merely because it contains a
  high-performing insufficient member).
* Missing evidence is NEVER treated as positive evidence (missing OOS
  is not "successful OOS"; missing robustness is not "robust"; missing
  reproducibility is not "reproducible").
* When no eligible SUFFICIENT candidate exists, ``selected`` is
  ``None`` -- a winner is never manufactured.

Historical selection is DESCRIPTIVE ONLY. It is never presented as
predictive and never implies live-trading readiness.
"""

from engine.models.selection import (
    PromotionDecision,
    RejectionReason,
    SELECTION_SCHEMA_VERSION,
    SelectionCandidate,
    SelectionCriteria,
    SelectionResult,
    SelectionStatus,
    SelectionType,
    SelectedResult,
)
from engine.selection.engine import SelectionEngine
from engine.selection.exceptions import (
    SelectionAlreadyExistsError,
    SelectionError,
    SelectionIntegrityError,
    SelectionNotFoundError,
    UnsupportedSelectionSchemaVersionError,
)
from engine.selection.identity import SelectionIdentity
from engine.selection.persistence import SelectionPersistence
from engine.selection.registry import SelectionRegistry
from engine.selection.serialization import (
    canonical_selection_json,
    deserialize_selection,
    parse_selection_header,
    serialize_selection,
    serialize_selection_bytes,
)
from engine.selection.suite_engine import SuiteSelectionEngine

__all__ = [
    "PromotionDecision",
    "RejectionReason",
    "SELECTION_SCHEMA_VERSION",
    "SelectionAlreadyExistsError",
    "SelectionCandidate",
    "SelectionCriteria",
    "SelectionEngine",
    "SelectionError",
    "SelectionIdentity",
    "SelectionIntegrityError",
    "SelectionNotFoundError",
    "SelectionPersistence",
    "SelectionRegistry",
    "SelectionResult",
    "SelectionStatus",
    "SelectionType",
    "SelectedResult",
    "SuiteSelectionEngine",
    "UnsupportedSelectionSchemaVersionError",
    "canonical_selection_json",
    "deserialize_selection",
    "parse_selection_header",
    "serialize_selection",
    "serialize_selection_bytes",
]
