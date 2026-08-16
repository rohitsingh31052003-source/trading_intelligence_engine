"""
Domain models for the robustness, failure-mode & edge-case hardening
validation layer (Sprint 12D).

Sprint 12D is NOT another intelligence / scoring layer. It is the
HARDENING validation layer that asks:

    "Does the complete 11V -> 11W -> 11X -> 11Y -> 11Z -> 12A -> 12B
    -> 12C architecture remain correct and safe under difficult
    boundary conditions, malformed-but-representable inputs, empty
    data, partial data, serialization edge cases, unusual cohort
    structures, deterministic replay variations and failure
    isolation?"

The models here describe the STRUCTURED, MACHINE-READABLE result of
running that hardening validation. They are DESCRIPTIVE: they report
whether the existing architecture remained internally consistent under
the supplied boundary / adversarial / empty conditions. They do NOT
predict future market behavior and do NOT guarantee profitability.

Architecture (12D sits BELOW 12C; it consumes already-computed outputs
of the existing chain):

    11V -> 11W -> 11X -> 11Y -> 11Z -> 12A -> 12B -> 12C -> 12D (this layer)

DESIGN PRINCIPLE — reuse, do not re-invent:

The validation layer REUSES the existing Sprint 11X / 11Y / 11Z / 12A
/ 12B / 12C engines. It performs INDEPENDENT CROSS-CHECKS of their
already-computed outputs; it never duplicates the trading / outcome /
analytics / evidence / decision logic. Where accounting identities
must be checked, the layer recomputes the EXPECTED values directly
from the raw Sprint 11W HistoricalOutcome objects (an independent
validator) and compares them to the Sprint 11X reported statistics —
it does NOT call the Sprint 11X engine a second time to "check itself".

DESIGN PRINCIPLE — no fabricated results:

A check that cannot be performed (e.g. an empty scenario) is reported
as ``SKIPPED`` / ``UNAVAILABLE`` with a descriptive reason — never as
a fake ``PASS``. A check that fails reports the specific failure
detail. The overall validation status is ``PASS`` only when every
non-skipped check passed and at least one check ran.

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


class RobustnessCheckStatus(Enum):
    """
    The status of a single robustness validation check.

    PASS
        The check ran and the invariant held.

    FAIL
        The check ran and the invariant was violated. The
        :attr:`RobustnessCheckResult.detail` carries the specific
        failure.

    SKIPPED
        The check could not be performed because the scenario had no
        data to exercise it (e.g. an empty scenario, no outcomes to
        reconcile). Never a fake PASS; the detail explains why it was
        skipped. A SKIPPED check is distinguishable from PASS and from
        UNAVAILABLE.

    UNAVAILABLE
        The check could not be performed because a required upstream
        artifact was missing / unavailable (e.g. no matched cohort to
        look up, no strategy interpretation present, no existing
        decision supplied). Never a fake PASS; the detail explains
        which artifact was unavailable. UNAVAILABLE is deliberately
        DISTINCT from SKIPPED: SKIPPED means "no data to exercise the
        check"; UNAVAILABLE means "the precondition artifact was
        absent / unavailable but the scenario was otherwise
        non-empty".

    INVALID
        The check could not be performed because an input was
        malformed-but-representable in a way the public contract
        rejects (e.g. a non-HistoricalOutcome entry, a malformed
        serialized payload, an invalid profile). The detail explains
        the malformation. INVALID is never silently converted to PASS.
    """

    PASS = "PASS"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"
    UNAVAILABLE = "UNAVAILABLE"
    INVALID = "INVALID"

    @property
    def is_pass(self) -> bool:
        return self == RobustnessCheckStatus.PASS

    @property
    def ran(self) -> bool:
        """Whether the check actually ran (PASS or FAIL)."""

        return self.status in (
            RobustnessCheckStatus.PASS,
            RobustnessCheckStatus.FAIL,
        )


class RobustnessCategory(Enum):
    """
    The category of a robustness validation check (mirrors the Sprint
    12D scope areas A-M). Used for deterministic grouping / reporting
    only.
    """

    EMPTY_MINIMAL_INPUTS = "EMPTY_MINIMAL_INPUTS"
    BOUNDARY_SAMPLE_SIZE = "BOUNDARY_SAMPLE_SIZE"
    MIXED_STATUS_CONTAMINATION = "MIXED_STATUS_CONTAMINATION"
    ADVERSARIAL_COHORTS = "ADVERSARIAL_COHORTS"
    LOOKUP_MATCHING = "LOOKUP_MATCHING"
    INTEGRATION_ISOLATION = "INTEGRATION_ISOLATION"
    SERIALIZATION_ADVERSARIAL = "SERIALIZATION_ADVERSARIAL"
    DETERMINISM_SHUFFLE = "DETERMINISM_SHUFFLE"
    INPUT_IMMUTABILITY = "INPUT_IMMUTABILITY"
    FAILURE_ISOLATION = "FAILURE_ISOLATION"
    CROSS_LAYER_CONSISTENCY = "CROSS_LAYER_CONSISTENCY"
    ACCOUNTING_INVARIANTS = "ACCOUNTING_INVARIANTS"
    REPORTING_HONESTY = "REPORTING_HONESTY"
    PIPELINE_REGRESSION = "PIPELINE_REGRESSION"
    NO_LOOK_AHEAD = "NO_LOOK_AHEAD"


@dataclass(frozen=True, slots=True)
class RobustnessCheckResult:
    """
    The result of one robustness validation check.

    Attributes:

    name
        Short, deterministic, human-readable check name.

    category
        The :class:`RobustnessCategory` the check belongs to.

    status
        The :class:`RobustnessCheckStatus`.

    detail
        Human-readable, descriptive detail. For a ``FAIL`` this
        carries the specific failure; for ``SKIPPED`` /
        ``UNAVAILABLE`` / ``INVALID`` it explains why; for a ``PASS``
        it may carry a short confirmation. Descriptive only.
    """

    name: str
    category: RobustnessCategory
    status: RobustnessCheckStatus
    detail: str = ""

    @property
    def passed(self) -> bool:
        return self.status == RobustnessCheckStatus.PASS

    @property
    def failed(self) -> bool:
        return self.status == RobustnessCheckStatus.FAIL

    @property
    def ran(self) -> bool:
        """Whether the check actually ran (PASS or FAIL)."""

        return self.status in (
            RobustnessCheckStatus.PASS,
            RobustnessCheckStatus.FAIL,
        )

    @property
    def skipped(self) -> bool:
        return self.status in (
            RobustnessCheckStatus.SKIPPED,
            RobustnessCheckStatus.UNAVAILABLE,
            RobustnessCheckStatus.INVALID,
        )

    @property
    def not_run(self) -> bool:
        """Whether the check did not run (anything other than PASS/FAIL)."""

        return not self.ran


@dataclass(frozen=True, slots=True)
class RobustnessScenarioResult:
    """
    The validation result for one robustness scenario.

    A scenario is a named collection of Sprint 11W HistoricalOutcome
    objects (plus, optionally, a profile for the 11Z/12A/12B checks,
    an existing decision for the integration-isolation checks, and an
    expected classification for the decision-authority checks). The
    validation layer runs a set of checks against the scenario and
    aggregates them here.

    Attributes:

    name
        Deterministic scenario name.

    outcome_count
        Number of outcomes in the scenario.

    checks
        Tuple of :class:`RobustnessCheckResult`, deterministically
        ordered by ``(category, name)``.

    edge_case
        A short descriptive tag identifying which edge-case class the
        scenario exercises (e.g. ``"empty"``, ``"all-both-touched"``,
        ``"boundary-below-min"``, ``"mixed-contamination"``). Used for
        reporting only; never affects the PASS/FAIL logic.
    """

    name: str
    outcome_count: int
    checks: tuple[RobustnessCheckResult, ...] = field(default_factory=tuple)
    edge_case: str = ""

    @property
    def passed(self) -> bool:
        """Whether every non-skipped check passed (and >=1 ran)."""

        ran = [c for c in self.checks if c.ran]
        return bool(ran) and all(c.passed for c in ran)

    @property
    def failed_checks(self) -> tuple[RobustnessCheckResult, ...]:
        return tuple(c for c in self.checks if c.failed)

    @property
    def not_run_count(self) -> int:
        return sum(1 for c in self.checks if c.not_run)


@dataclass(frozen=True, slots=True)
class RobustnessCategorySummary:
    """
    A summary of the checks in one :class:`RobustnessCategory`.

    Attributes:

    category
        The :class:`RobustnessCategory`.

    total
        Total number of checks in the category.

    passed
        Number of checks that passed.

    failed
        Number of checks that failed.

    skipped
        Number of checks that were skipped / unavailable / invalid
        (collapsed for compact reporting; the per-check detail retains
        the exact status).
    """

    category: RobustnessCategory
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0

    @property
    def passed_category(self) -> bool:
        """Whether the category passed (no failures; >=1 ran check)."""

        ran = self.total - self.skipped
        return ran > 0 and self.failed == 0


@dataclass(frozen=True, slots=True)
class RobustnessValidationReport:
    """
    The structured, machine-readable result of a Sprint 12D robustness
    validation run.

    The report is DESCRIPTIVE. It reports whether the existing
    architecture remained internally consistent, deterministic,
    leak-free and accounting-consistent under the supplied boundary /
    adversarial / empty conditions. It does NOT predict future market
    behavior and do NOT guarantee profitability.

    Attributes:

    validation_id
        Deterministic identifier (``"robustness-"`` + sha256[:16] of
        the canonical validation identity).

    label
        Optional descriptive label identifying the validation run.

    metadata
        Optional descriptive metadata (sorted key/value pairs).

    scenarios
        Tuple of :class:`RobustnessScenarioResult`, deterministically
        ordered by scenario name.

    categories
        Tuple of :class:`RobustnessCategorySummary`, one per
        :class:`RobustnessCategory` that appeared, deterministically
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
        Number of checks that were skipped / unavailable / invalid.

    overall_status
        The :class:`RobustnessCheckStatus` of the WHOLE run. ``PASS``
        only when every non-skipped check passed and at least one
        check ran; ``FAIL`` if any check failed; ``SKIPPED`` if every
        check was not-run (no validation performed).

    determinism_status
        ``PASS`` / ``FAIL`` / ``SKIPPED`` for the determinism /
        shuffle checks.

    look_ahead_status
        ``PASS`` / ``FAIL`` / ``SKIPPED`` for the no-look-ahead /
        point-in-time checks.

    accounting_status
        ``PASS`` / ``FAIL`` / ``SKIPPED`` for the accounting
        invariants checks.

    serialization_status
        ``PASS`` / ``FAIL`` / ``SKIPPED`` for the serialization
        adversarial checks.

    integration_status
        ``PASS`` / ``FAIL`` / ``SKIPPED`` for the 12A/12B integration
        failure-isolation checks.

    cross_layer_status
        ``PASS`` / ``FAIL`` / ``SKIPPED`` for the cross-layer
        consistency checks (INSUFFICIENT stays insufficient; existing
        decision stays authoritative).

    pipeline_regression_status
        ``PASS`` / ``FAIL`` / ``SKIPPED`` for the pipeline regression
        baseline check.

    outcome_distribution
        Tuple of ``(status_name, count)`` pairs describing the
        distribution of Sprint 11W outcome statuses across all
        scenarios, deterministically ordered by status name.

    edge_case_coverage
        Tuple of edge-case tags exercised across the scenarios
        (deduplicated, sorted). Descriptive only.

    report_checks
        Tuple of :class:`RobustnessCheckResult` for REPORT-LEVEL checks
        (pipeline regression, failure isolation) that are NOT attached
        to any single scenario. These count toward the report-level
        check / passed / failed / skipped totals and category summaries
        but do NOT affect any scenario's :attr:`passed` property (so an
        empty scenario cannot auto-pass merely because the global
        pipeline baseline is intact).

    rationale
        Human-readable, descriptive summary of the validation run.
        Descriptive only.

    limitations
        The explicit, fixed limitations / disclaimer carried on every
        report.
    """

    validation_id: str
    scenarios: tuple[RobustnessScenarioResult, ...] = field(
        default_factory=tuple,
    )
    categories: tuple[RobustnessCategorySummary, ...] = field(
        default_factory=tuple,
    )
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    scenario_count: int = 0
    check_count: int = 0
    passed_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    overall_status: RobustnessCheckStatus = RobustnessCheckStatus.SKIPPED
    determinism_status: RobustnessCheckStatus = RobustnessCheckStatus.SKIPPED
    look_ahead_status: RobustnessCheckStatus = RobustnessCheckStatus.SKIPPED
    accounting_status: RobustnessCheckStatus = RobustnessCheckStatus.SKIPPED
    serialization_status: RobustnessCheckStatus = (
        RobustnessCheckStatus.SKIPPED
    )
    integration_status: RobustnessCheckStatus = RobustnessCheckStatus.SKIPPED
    cross_layer_status: RobustnessCheckStatus = RobustnessCheckStatus.SKIPPED
    pipeline_regression_status: RobustnessCheckStatus = (
        RobustnessCheckStatus.SKIPPED
    )
    outcome_distribution: tuple[tuple[str, int], ...] = field(
        default_factory=tuple,
    )
    edge_case_coverage: tuple[str, ...] = field(default_factory=tuple)
    report_checks: tuple[RobustnessCheckResult, ...] = field(
        default_factory=tuple,
    )
    rationale: str = ""
    limitations: str = ""

    @property
    def passed(self) -> bool:
        """Whether the whole validation run passed."""

        return self.overall_status == RobustnessCheckStatus.PASS

    @property
    def is_empty(self) -> bool:
        """Whether no scenarios were validated."""

        return self.scenario_count == 0


#: The fixed, explicit limitations / disclaimer carried on every
#: robustness validation report. It is intentionally NOT configurable
#: so the honesty contract cannot be weakened.
ROBUSTNESS_VALIDATION_LIMITATIONS = (
    "Robustness validation verifies implementation invariants and "
    "historical accounting behavior; it does not establish predictive "
    "validity, statistical significance, or future profitability. "
    "Results are descriptive only."
)


__all__ = [
    "ROBUSTNESS_VALIDATION_LIMITATIONS",
    "RobustnessCategory",
    "RobustnessCategorySummary",
    "RobustnessCheckResult",
    "RobustnessCheckStatus",
    "RobustnessScenarioResult",
    "RobustnessValidationReport",
]
