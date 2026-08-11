"""
Domain models for the experiment query / filtering / analysis layer
(Sprint 11L).

This layer sits ABOVE the experiment registry / persistence layer
(Sprint 11K). It makes persisted experiment results discoverable,
filterable, sortable, groupable and summarizable WITHOUT rerunning
the underlying trading pipeline.

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

Design rules honoured by this module:

* No duplication of any existing logic. A query row is a thin,
  read-only projection of an already-computed
  ``ExperimentResult``. No statistic is recomputed.

* Immutable frozen+slots dataclasses throughout.

* Evidence safety is structural: descriptive "best"/"leader"
  models are populated ONLY among experiments with SUFFICIENT
  evidence and are ``None`` otherwise. The framework never turns
  a descriptive historical result into a predictive claim.

* Determinism: every ordering, grouping and summary is a pure
  function of the persisted data; identical inputs always produce
  identical outputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping


# ============================================================
# EXCEPTION
# ============================================================


class ExperimentQueryError(Exception):
    """
    Raised for invalid query parameters (unknown sort key, unknown
    grouping dimension, malformed filter values, etc.).

    Query errors are surfaced explicitly; they are never silently
    swallowed. Corrupt persisted records remain the registry's
    responsibility (:class:`ExperimentIntegrityError` etc.).
    """


# ============================================================
# SORT + ORDER + GROUP DIMENSIONS
# ============================================================


class ExperimentSortKey(Enum):
    """
    Deterministic dimension along which query results may be sorted.

    Member names are stable identifiers (used in canonical
    serialization and in string coercion for the query API).

    EXPECTANCY / TOTAL_R / WIN_RATE / PROFIT_FACTOR
        Completed-trade performance metrics (descriptive).

    MAX_DRAWDOWN
        Maximum realised drawdown in R (descriptive).

    COMPLETED_TRADES
        Number of completed (WIN/LOSS) trades.

    EXPERIMENT_ID
        The deterministic experiment identifier (lexicographic).

    LABEL
        The human-readable experiment label (lexicographic).
    """

    EXPECTANCY = "EXPECTANCY"
    TOTAL_R = "TOTAL_R"
    WIN_RATE = "WIN_RATE"
    PROFIT_FACTOR = "PROFIT_FACTOR"
    MAX_DRAWDOWN = "MAX_DRAWDOWN"
    COMPLETED_TRADES = "COMPLETED_TRADES"
    EXPERIMENT_ID = "EXPERIMENT_ID"
    LABEL = "LABEL"


class SortOrder(Enum):
    """
    Sort direction.

    ASCENDING
        Lowest-to-highest (alphabetical for string keys).

    DESCENDING
        Highest-to-lowest (reverse alphabetical for string keys).
    """

    ASCENDING = "ASCENDING"
    DESCENDING = "DESCENDING"


class ExperimentGroupDimension(Enum):
    """
    Dimension along which query results may be grouped.

    DATASET
        By resolved dataset name.

    EVIDENCE_STATUS
        By evidence sufficiency (SUFFICIENT / PARTIAL /
        INSUFFICIENT).

    LABEL
        By the human-readable experiment label.

    CONFIGURATION
        By configuration identity (the deterministic configuration
        hash). Two experiments sharing a configuration hash share
        the same parameter/configuration identity.
    """

    DATASET = "DATASET"
    EVIDENCE_STATUS = "EVIDENCE_STATUS"
    LABEL = "LABEL"
    CONFIGURATION = "CONFIGURATION"


# ============================================================
# FILTER
# ============================================================


@dataclass(frozen=True, slots=True)
class ExperimentFilter:
    """
    Immutable, optional-everywhere filter for persisted experiments.

    Every criterion is optional. A criterion that is ``None`` (or,
    for min/max bounds, left unset) imposes no constraint. An
    experiment matches the filter when it satisfies ALL active
    criteria (logical AND).

    Field semantics:

    experiment_id
        Exact experiment identifier match.

    label
        Exact experiment label match.

    dataset_name
        Exact resolved dataset name match
        (``result.dataset.name``).

    evidence_status
        Exact evidence-sufficiency match.

    configuration_hash
        Exact configuration-hash match (the deterministic
        fingerprint of the full configuration).

    dataset_content_hash
        Exact dataset content-hash match.

    reproducible
        When ``True``, only fully reproducible experiments match.
        When ``False``, only non-reproducible experiments match.
        When ``None``, no constraint.

    min_completed_trades / max_completed_trades
        Inclusive bounds on the completed-trade count.

    min_expectancy / max_expectancy
        Inclusive bounds on the (completed-trade) expectancy.

    parameter_values
        A subset/containment match on the experiment's recorded
        parameter values (``reproducibility.parameter_values``).
        An experiment matches when, for EVERY key/value in this
        mapping, the experiment records the SAME value for that
        key. An empty or ``None`` mapping imposes no constraint.
        Experiments without a given key do not match a filter that
        requires it ("where available").
    """

    experiment_id: str | None = None
    label: str | None = None
    dataset_name: str | None = None
    evidence_status: ExperimentEvidenceStatus | None = None
    configuration_hash: str | None = None
    dataset_content_hash: str | None = None
    reproducible: bool | None = None

    min_completed_trades: int | None = None
    max_completed_trades: int | None = None
    min_expectancy: float | None = None
    max_expectancy: float | None = None

    parameter_values: Mapping[str, str] = field(default_factory=dict)


# Forward reference: ExperimentEvidenceStatus lives in the experiment
# models module. Import it lazily to avoid a circular import at module
# load time; the annotation is resolved via ``from __future__ import
# annotations`` so the symbol is only needed at runtime when a filter
# is actually constructed with a status. Import it eagerly here for
# correctness of ``isinstance`` / equality at runtime.
from engine.models.experiment import ExperimentEvidenceStatus  # noqa: E402


# ============================================================
# QUERY ROW (read-only projection)
# ============================================================


@dataclass(frozen=True, slots=True)
class ExperimentQueryRow:
    """
    Flat, read-only projection of one persisted experiment for
    querying, sorting, grouping and summarization.

    This is a query-layer projection (it carries query-relevant
    identity and metric fields). It does NOT recompute any value;
    every field is read directly from the persisted
    ``ExperimentResult`` (its summary, dataset and reproducibility
    metadata). Downstream comparison logic from Sprint 11J is NOT
    duplicated; this row exists for query/sort/group/summary
    operations only.

    Field semantics mirror the authoritative sources:

    * ``experiment_id`` / ``label`` from the result.
    * ``dataset_name`` / ``dataset_content_hash`` from the result's
      resolved dataset spec.
    * ``configuration_hash`` from the reproducibility metadata.
    * ``evidence_status`` and the completed-trade metrics from the
      result summary.
    * ``robust`` / ``oos_expectancy`` / ``oos_trades`` /
      ``data_sufficient`` / ``leakage_passed`` /
      ``leakage_not_verified`` from the summary.
    * ``reproducible`` from the reproducibility metadata.
    * ``parameter_values`` from the reproducibility metadata
      (persisted parameter snapshot).
    """

    experiment_id: str
    label: str

    dataset_name: str
    dataset_content_hash: str | None
    configuration_hash: str

    evidence_status: ExperimentEvidenceStatus

    completed_trades: int
    win_rate: float
    expectancy: float
    total_r: float
    profit_factor: float
    max_drawdown_r: float

    robust: bool | None
    oos_expectancy: float | None
    oos_trades: int
    data_sufficient: bool
    leakage_passed: bool | None
    leakage_not_verified: bool

    reproducible: bool
    parameter_values: Mapping[str, str] = field(default_factory=dict)

    @property
    def is_sufficient(self) -> bool:
        return self.evidence_status == ExperimentEvidenceStatus.SUFFICIENT

    @property
    def is_insufficient(self) -> bool:
        return self.evidence_status == ExperimentEvidenceStatus.INSUFFICIENT

    @property
    def is_partial(self) -> bool:
        return self.evidence_status == ExperimentEvidenceStatus.PARTIAL


# ============================================================
# GROUPING
# ============================================================


@dataclass(frozen=True, slots=True)
class ExperimentGroup:
    """
    One group of experiments produced by a grouping query.

    Field semantics:

    name
        The group's key value (e.g. dataset name, evidence-status
        name, label, configuration hash).

    dimension
        The dimension the group was formed along.

    experiment_ids
        Experiment ids in the group, sorted ascending for
        determinism.

    experiment_count
        Number of experiments in the group.

    sufficient_count / partial_count / insufficient_count
        Evidence-sufficiency breakdown within the group. Zero
        trades in a group is unobserved, NOT zero performance.
    """

    name: str
    dimension: ExperimentGroupDimension
    experiment_ids: tuple[str, ...] = field(default_factory=tuple)
    experiment_count: int = 0
    sufficient_count: int = 0
    partial_count: int = 0
    insufficient_count: int = 0


@dataclass(frozen=True, slots=True)
class ExperimentGrouping:
    """
    The full result of a grouping query.

    Field semantics:

    dimension
        The dimension the groups were formed along.

    groups
        Groups sorted by name ascending for determinism.

    total_experiments
        Total number of filtered experiments across all groups.

    total_groups
        Number of groups (convenience).
    """

    dimension: ExperimentGroupDimension
    groups: tuple[ExperimentGroup, ...] = field(default_factory=tuple)
    total_experiments: int = 0

    @property
    def total_groups(self) -> int:
        return len(self.groups)

    @property
    def is_empty(self) -> bool:
        return not self.groups


# ============================================================
# METRIC LEADER + ANALYSIS SUMMARY
# ============================================================


@dataclass(frozen=True, slots=True)
class MetricLeader:
    """
    The experiment leading on a single metric, AMONG experiments
    with SUFFICIENT evidence only.

    Field semantics:

    experiment_id
        The leading experiment's id.

    value
        The metric value that earned the leadership (descriptive).

    This is a descriptive leader, never a predictive claim. It is
    ``None`` (i.e. the surrounding summary field is ``None``) when
    no experiment has SUFFICIENT evidence -- the core "no best
    without evidence" guarantee.
    """

    experiment_id: str
    value: float


@dataclass(frozen=True, slots=True)
class ExperimentAnalysisSummary:
    """
    Structured analysis summary over a (filtered) set of persisted
    experiments.

    The summary reports counts, descriptive leaders (SUFFICIENT
    only), coverage maps and descriptive conclusions. It NEVER
    declares a "best" experiment when evidence is insufficient, and
    it never claims historical results predict future performance.

    Field semantics:

    total_experiments
        Number of experiments in the analysed set.

    sufficient_count / partial_count / insufficient_count
        Evidence-sufficiency breakdown. An INSUFFICIENT count is
        unobserved, NOT zero performance.

    has_sufficient_evidence
        Whether at least one experiment is SUFFICIENT. Descriptive
        leaders are populated only when this is True.

    best_by_expectancy / best_by_total_r / lowest_drawdown
        Descriptive leaders among SUFFICIENT experiments only.
        ``None`` when no experiment is sufficient. ``best_by_*``
        are the highest-expectancy / highest-total-R experiments;
        ``lowest_drawdown`` is the lowest max-drawdown experiment.

    most_reproducible_experiment_ids
        Experiment ids whose reproducibility metadata marks them
        fully reproducible, sorted ascending. Descriptive only.

    dataset_coverage
        Mapping of dataset name -> number of experiments.

    configuration_coverage
        Mapping of configuration hash -> number of experiments
        (parameter/configuration identity coverage).

    parameter_coverage
        Mapping of distinct persisted parameter-values snapshots
        (canonical string form) -> number of experiments.

    conclusions
        Descriptive, non-predictive summary conclusions.
    """

    total_experiments: int = 0

    sufficient_count: int = 0
    partial_count: int = 0
    insufficient_count: int = 0

    has_sufficient_evidence: bool = False

    best_by_expectancy: MetricLeader | None = None
    best_by_total_r: MetricLeader | None = None
    lowest_drawdown: MetricLeader | None = None

    most_reproducible_experiment_ids: tuple[str, ...] = field(
        default_factory=tuple,
    )

    dataset_coverage: Mapping[str, int] = field(default_factory=dict)
    configuration_coverage: Mapping[str, int] = field(default_factory=dict)
    parameter_coverage: Mapping[str, int] = field(default_factory=dict)

    conclusions: tuple[str, ...] = field(default_factory=tuple)


__all__ = [
    "ExperimentAnalysisSummary",
    "ExperimentFilter",
    "ExperimentGroup",
    "ExperimentGroupDimension",
    "ExperimentGrouping",
    "ExperimentQueryError",
    "ExperimentQueryRow",
    "ExperimentSortKey",
    "MetricLeader",
    "SortOrder",
]
