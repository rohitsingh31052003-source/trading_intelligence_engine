"""
Domain models for the robust historical backtesting & adversarial
validation layer (Sprint 12C).

Sprint 12C is NOT another intelligence / scoring layer. It is the
VALIDATION layer that asks:

    "Does the complete 11V -> 11W -> 11X -> 11Y -> 11Z -> 12A -> 12B
    architecture remain correct, deterministic, leak-free and
    statistically / accounting-ly consistent when subjected to broader
    historical replay and adversarial conditions?"

The models here describe the STRUCTURED, MACHINE-READABLE result of
running that validation. They are DESCRIPTIVE: they report whether the
existing architecture remains internally consistent under the supplied
scenarios. They do NOT predict future market behavior and do NOT
guarantee profitability.

Architecture (12C sits BELOW 12B; it consumes already-computed outputs
of the existing chain):

    11V -> 11W -> 11X -> 11Y -> 11Z -> 12A -> 12B -> 12C (this layer)

DESIGN PRINCIPLE — reuse, do not re-invent:

The validation layer REUSES the existing Sprint 11X / 11Y / 11Z /
12A / 12B engines. It performs INDEPENDENT CROSS-CHECKS of their
already-computed outputs; it never duplicates the trading / outcome /
analytics / evidence / decision logic. Where accounting identities must
be checked, the layer recomputes the EXPECTED values directly from the
raw Sprint 11W :class:`~engine.models.historical_outcome.HistoricalOutcome`
objects (an independent validator) and compares them to the Sprint 11X
reported statistics — it does NOT call the Sprint 11X engine a second
time to "check itself".

DESIGN PRINCIPLE — no fabricated results:

A check that cannot be performed (e.g. an empty scenario) is reported as
``SKIPPED`` with a descriptive reason — never as a fake ``PASS``. A
check that fails reports the specific failure detail. The overall
validation status is ``PASSED`` only when every non-skipped check
passed and at least one check ran.

DESIGN PRINCIPLE — deterministic:

Identical scenarios always produce identical validation results. No
wall-clock time, no randomness, no unordered iteration. The validation
identifier hashes the SORTED canonical identity of the scenarios +
checks so a shuffled scenario order yields the same id.

Design rules:

* Frozen + slots dataclasses (matches the rest of the model layer).
* Optional detail fields use empty string / empty tuple so "no detail"
  is never silently a real value.
* No business logic lives here; the models are data carriers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ValidationCheckStatus(Enum):
    """
    The status of a single validation check.

    PASS
        The check ran and the invariant held.

    FAIL
        The check ran and the invariant was violated. The
        :attr:`CheckResult.detail` carries the specific failure.

    SKIPPED
        The check could not be performed (e.g. an empty scenario, no
        outcomes to reconcile, a missing prerequisite). Never a fake
        PASS; the :attr:`CheckResult.detail` explains why it was
        skipped.
    """

    PASS = "PASS"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"

    @property
    def is_pass(self) -> bool:
        return self == ValidationCheckStatus.PASS


class ValidationCategory(Enum):
    """
    The category of a validation check (mirrors the Sprint 12C scope
    areas A-O). Used for deterministic grouping / reporting only.
    """

    SCENARIO_CONSTRUCTION = "SCENARIO_CONSTRUCTION"
    END_TO_END_REPLAY = "END_TO_END_REPLAY"
    POINT_IN_TIME_SAFETY = "POINT_IN_TIME_SAFETY"
    LOOK_AHEAD_PROTECTION = "LOOK_AHEAD_PROTECTION"
    ACCOUNTING_RECONCILIATION = "ACCOUNTING_RECONCILIATION"
    COHORT_RECONCILIATION = "COHORT_RECONCILIATION"
    SHUFFLE_INVARIANCE = "SHUFFLE_INVARIANCE"
    DETERMINISTIC_IDS = "DETERMINISTIC_IDS"
    SERIALIZATION = "SERIALIZATION"
    IMMUTABILITY = "IMMUTABILITY"
    ADVERSARIAL_DATA = "ADVERSARIAL_DATA"
    EVIDENCE_GATING = "EVIDENCE_GATING"
    DECISION_AUTHORITY = "DECISION_AUTHORITY"
    END_TO_END_INTEGRATION = "END_TO_END_INTEGRATION"


@dataclass(frozen=True, slots=True)
class CheckResult:
    """
    The result of one validation check.

    Attributes:

    name
        Short, deterministic, human-readable check name.

    category
        The :class:`ValidationCategory` the check belongs to.

    status
        The :class:`ValidationCheckStatus`.

    detail
        Human-readable, descriptive detail. For a ``FAIL`` this carries
        the specific failure; for a ``SKIPP`` it explains why; for a
        ``PASS`` it may carry a short confirmation. Descriptive only.
    """

    name: str
    category: ValidationCategory
    status: ValidationCheckStatus
    detail: str = ""

    @property
    def passed(self) -> bool:
        return self.status == ValidationCheckStatus.PASS

    @property
    def failed(self) -> bool:
        return self.status == ValidationCheckStatus.FAIL

    @property
    def skipped(self) -> bool:
        return self.status == ValidationCheckStatus.SKIPPED


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    """
    The validation result for one scenario.

    A scenario is a named collection of Sprint 11W
    :class:`~engine.models.historical_outcome.HistoricalOutcome` objects
    (plus, optionally, existing decisions for the decision-authority
    checks). The validation layer runs a set of checks against the
    scenario and aggregates them here.

    Attributes:

    name
        Deterministic scenario name.

    outcome_count
        Number of outcomes in the scenario.

    checks
        Tuple of :class:`CheckResult`, deterministically ordered by
        ``(category, name)``.
    """

    name: str
    outcome_count: int
    checks: tuple[CheckResult, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        """Whether every non-skipped check passed (and >=1 ran)."""

        ran = [c for c in self.checks if c.status != ValidationCheckStatus.SKIPPED]
        return bool(ran) and all(c.passed for c in ran)

    @property
    def failed_checks(self) -> tuple[CheckResult, ...]:
        return tuple(c for c in self.checks if c.failed)


@dataclass(frozen=True, slots=True)
class CategorySummary:
    """
    A summary of the checks in one :class:`ValidationCategory`.

    Attributes:

    category
        The :class:`ValidationCategory`.

    total
        Total number of checks in the category.

    passed
        Number of checks that passed.

    failed
        Number of checks that failed.

    skipped
        Number of checks that were skipped.
    """

    category: ValidationCategory
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0

    @property
    def passed_category(self) -> bool:
        """Whether the category passed (no failures; >=1 non-skipped check)."""

        ran = self.total - self.skipped
        return ran > 0 and self.failed == 0


@dataclass(frozen=True, slots=True)
class BacktestValidationReport:
    """
    The structured, machine-readable result of a Sprint 12C validation
    run.

    The report is DESCRIPTIVE. It reports whether the existing
    architecture remained internally consistent, deterministic,
    leak-free and accounting-consistent under the supplied scenarios.
    It does NOT predict future market behavior and does NOT guarantee
    profitability.

    Attributes:

    validation_id
        Deterministic identifier (``"validation-"`` + sha256[:16] of
        the canonical validation identity).

    label
        Optional descriptive label identifying the validation run.

    metadata
        Optional descriptive metadata (sorted key/value pairs).

    scenarios
        Tuple of :class:`ScenarioResult`, deterministically ordered by
        scenario name.

    categories
        Tuple of :class:`CategorySummary`, one per
        :class:`ValidationCategory` that appeared, deterministically
        ordered by category.

    scenario_count
        Number of scenarios validated.

    check_count
        Total number of checks across all scenarios.

    passed_count
        Number of checks that passed.

    failed_count
        Number of checks that failed.

    skipped_count
        Number of checks that were skipped.

    overall_status
        The :class:`ValidationCheckStatus` of the WHOLE run. ``PASS``
        only when every non-skipped check passed and at least one
        check ran; ``FAIL`` if any check failed; ``SKIPPED`` if every
        check was skipped (no validation performed).

    determinism_status
        ``PASS`` / ``FAIL`` / ``SKIPPED`` for the determinism checks
        (repeated for convenient downstream consumption).

    look_ahead_status
        ``PASS`` / ``FAIL`` / ``SKIPPED`` for the look-ahead /
        point-in-time checks.

    accounting_status
        ``PASS`` / ``FAIL`` / ``SKIPPED`` for the accounting
        reconciliation checks.

    serialization_status
        ``PASS`` / ``FAIL`` / ``SKIPPED`` for the serialization checks.

    decision_authority_status
        ``PASS`` / ``FAIL`` / ``SKIPPED`` for the decision-authority
        checks (the existing decision is never modified by 12B).

    outcome_distribution
        Tuple of ``(status_name, count)`` pairs describing the
        distribution of Sprint 11W outcome statuses across all
        scenarios, deterministically ordered by status name.

    rationale
        Human-readable, descriptive summary of the validation run.
        Descriptive only.
    """

    validation_id: str
    scenarios: tuple[ScenarioResult, ...] = field(default_factory=tuple)
    categories: tuple[CategorySummary, ...] = field(default_factory=tuple)
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    scenario_count: int = 0
    check_count: int = 0
    passed_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    overall_status: ValidationCheckStatus = ValidationCheckStatus.SKIPPED
    determinism_status: ValidationCheckStatus = ValidationCheckStatus.SKIPPED
    look_ahead_status: ValidationCheckStatus = ValidationCheckStatus.SKIPPED
    accounting_status: ValidationCheckStatus = ValidationCheckStatus.SKIPPED
    serialization_status: ValidationCheckStatus = ValidationCheckStatus.SKIPPED
    decision_authority_status: ValidationCheckStatus = (
        ValidationCheckStatus.SKIPPED
    )
    outcome_distribution: tuple[tuple[str, int], ...] = field(
        default_factory=tuple,
    )
    rationale: str = ""

    @property
    def passed(self) -> bool:
        """Whether the whole validation run passed."""

        return self.overall_status == ValidationCheckStatus.PASS

    @property
    def is_empty(self) -> bool:
        """Whether no scenarios were validated."""

        return self.scenario_count == 0


__all__ = [
    "BacktestValidationReport",
    "CategorySummary",
    "CheckResult",
    "ScenarioResult",
    "ValidationCategory",
    "ValidationCheckStatus",
]
