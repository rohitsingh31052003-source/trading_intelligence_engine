"""
Domain models for the Experiment Suite / Batch Orchestration &
Analysis Layer (Sprint 11M).

This layer sits ABOVE the experiment query / filtering / analysis layer
(Sprint 11L). It makes a *batch of experiments run together as a named
comparison campaign* reproducible, identifiable, persistable and
analysable as a unit -- WITHOUT duplicating any trading, validation,
pipeline, research, registry, query or comparison logic. Every value is
read from the reused ``ExperimentResult`` objects produced by the
existing engines.

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

Design rules honoured by this module:

* No duplication of any existing logic. A suite result REFERENCES (never
  copies) its member ``ExperimentResult`` objects. Suite-level summary
  fields are thin projections / delegations of values already computed
  by the reused engines.

* Immutable frozen+slots dataclasses throughout.

* A single, explicit ``SUITE_SCHEMA_VERSION`` identifies the persisted
  suite manifest representation (separate from the experiment
  ``SCHEMA_VERSION`` because a suite manifest is a structurally
  different artifact: an ordered list of member experiment ids, not a
  full experiment record). The loader rejects unsupported versions so
  migration support can be layered in later.

* Evidence safety is preserved from Sprint 11J/11L: descriptive
  suite-level leaders are populated ONLY when at least one member has
  SUFFICIENT evidence and are ``None`` otherwise. INSUFFICIENT is
  unobserved, NOT zero performance. The framework never turns a
  descriptive historical result into a predictive claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


# ============================================================
# SUITE SCHEMA VERSION
# ============================================================


#: Current persistence schema version for the suite manifest.
#:
#: A suite manifest is a structurally different artifact from an
#: experiment record: it stores the suite identity plus an ordered list
#: of member experiment ids (the heavy per-experiment data stays in the
#: existing per-experiment records). It therefore carries its own
#: version. The loader rejects any manifest whose ``schema_version``
#: differs from this value with a ``SuiteIntegrityError`` so future
#: migration support can be layered in without rewriting the suite
#: system. This is intentionally independent of the experiment
#: ``SCHEMA_VERSION`` (which remains unchanged at 1).
SUITE_SCHEMA_VERSION: int = 1


# ============================================================
# SUITE REPRODUCIBILITY METADATA
# ============================================================


@dataclass(frozen=True)
class SuiteReproducibilityMetadata:
    """
    Explicit reproducibility metadata for an experiment suite run.

    Every value that cannot be determined is represented explicitly as
    ``None`` or ``"UNKNOWN"`` rather than fabricated.

    Field semantics:

    suite_id
        Deterministic identifier derived from the canonical
        representation of the suite configuration (the ordered member
        ``ExperimentConfig`` list + label + metadata).

    suite_configuration_hash
        SHA-256 (hex) of the canonical suite configuration
        representation. A short, stable fingerprint of the full suite
        configuration.

    suite_configuration_representation
        The canonical (sorted-key) string representation used to
        derive both the suite ID and the configuration hash.

    member_experiment_ids
        The ordered list of member experiment identifiers, in the same
        order as the suite members. Order is significant and part of
        the suite identity.

    member_count
        Number of member experiments.

    code_version
        Installed package version identifier, when safely available via
        ``importlib.metadata``; otherwise ``"UNKNOWN"``. Never
        fabricated.

    reproducible
        Whether the suite is fully reproducible from the recorded
        metadata. True when a configuration representation and all
        member experiment ids are present. This is a structural
        statement, not a statistical guarantee.
    """

    suite_id: str
    suite_configuration_hash: str
    suite_configuration_representation: str

    member_experiment_ids: tuple[str, ...] = field(default_factory=tuple)
    member_count: int = 0

    code_version: str = "UNKNOWN"
    reproducible: bool = True

    @property
    def code_version_known(self) -> bool:
        return self.code_version != "UNKNOWN"


# ============================================================
# SUITE SUMMARY
# ============================================================


@dataclass(frozen=True)
class SuiteSummary:
    """
    Curated summary of an experiment suite.

    This is a thin projection / delegation of the suite's member
    experiments. It does NOT recompute any statistic; it delegates to
    the reused Sprint 11L ``ExperimentAnalysisSummary`` and Sprint 11J
    ``ExperimentComparison`` (both referenced, never copied).

    Field semantics:

    member_count
        Number of member experiments in the suite.

    sufficient_count / partial_count / insufficient_count
        Evidence-sufficiency breakdown across the members. An
        INSUFFICIENT count is unobserved, NOT zero performance.

    has_sufficient_evidence
        Whether at least one member has SUFFICIENT evidence. Descriptive
        suite-level leaders are populated only when this is True.

    analysis_summary
        The reused Sprint 11L ``ExperimentAnalysisSummary`` computed
        over the suite's members (referenced, not copied).

    comparison
        The reused Sprint 11J ``ExperimentComparison`` computed over the
        suite's members (referenced, not copied). ``None`` when the
        suite has no members or comparison was not requested.

    conclusions
        Descriptive, non-predictive suite-level conclusions.
    """

    member_count: int = 0

    sufficient_count: int = 0
    partial_count: int = 0
    insufficient_count: int = 0

    has_sufficient_evidence: bool = False

    analysis_summary: Any = None
    comparison: Any = None

    conclusions: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_empty(self) -> bool:
        return self.member_count == 0


# ============================================================
# SUITE RESULT
# ============================================================


@dataclass(frozen=True)
class SuiteResult:
    """
    Immutable result of a complete, reproducible experiment suite.

    The result REFERENCES -- never duplicates -- its member
    ``ExperimentResult`` objects (Sprint 11J). Downstream layers can
    access each member's full evaluation / research reports through
    ``members``.

    Field semantics:

    suite_id
        Deterministic identifier (derived from the config).

    config
        The immutable ``SuiteConfig`` that produced this result.

    members
        Tuple of member ``ExperimentResult`` objects, in the declared
        order.

    reproducibility
        Explicit suite reproducibility metadata.

    summary
        Curated suite summary distinguishing raw/descriptive results
        from sufficient vs insufficient evidence.

    label
        Human-readable suite label (from the config).

    metadata
        Arbitrary deterministic string metadata (from the config).
    """

    suite_id: str
    config: Any

    reproducibility: SuiteReproducibilityMetadata
    summary: SuiteSummary

    members: tuple[Any, ...] = field(default_factory=tuple)

    label: str = ""
    metadata: Mapping[str, str] = field(default_factory=dict)

    @property
    def member_experiment_ids(self) -> tuple[str, ...]:
        """Ordered member experiment identifiers."""

        return tuple(m.experiment_id for m in self.members)

    @property
    def member_count(self) -> int:
        return len(self.members)

    @property
    def has_sufficient_evidence(self) -> bool:
        return self.summary.has_sufficient_evidence

    @property
    def is_empty(self) -> bool:
        return not self.members


# ============================================================
# SUITE COMPARISON
# ============================================================


@dataclass(frozen=True)
class SuiteComparisonRow:
    """
    One row of a suite comparison.

    A flat, read-only projection of a ``SuiteResult`` suitable for
    side-by-side comparison of multiple suites. Values are read from the
    reused suite summaries; nothing is recomputed.

    Field semantics:

    suite_id / label
        Suite identity and human-readable label.

    member_count
        Number of member experiments in the suite.

    sufficient_count / partial_count / insufficient_count
        Evidence-sufficiency breakdown across the suite's members.

    has_sufficient_evidence
        Whether at least one member has SUFFICIENT evidence.

    best_member_by_expectancy
        The experiment id of the member with the highest expectancy
        AMONG SUFFICIENT members of this suite only. ``None`` when no
        member is sufficient. Always descriptive.

    best_member_by_total_r
        The experiment id of the member with the highest total R AMONG
        SUFFICIENT members of this suite only. ``None`` when none
        sufficient. Always descriptive.
    """

    suite_id: str
    label: str

    member_count: int

    sufficient_count: int
    partial_count: int
    insufficient_count: int

    has_sufficient_evidence: bool

    best_member_by_expectancy: str | None = None
    best_member_by_total_r: str | None = None


@dataclass(frozen=True)
class SuiteComparison:
    """
    Immutable comparison of multiple experiment suites.

    The comparison NEVER automatically declares one suite "best" unless
    the evidence supports such a conclusion. Descriptive ranking is
    computed ONLY among suites that each have at least one SUFFICIENT
    member, and is ``None`` otherwise. Insufficient evidence is always
    made explicit.

    Field semantics:

    rows
        One ``SuiteComparisonRow`` per suite, in the order supplied.

    sufficient_suites
        Suite ids with at least one SUFFICIENT member.

    insufficient_suites
        Suite ids with NO SUFFICIENT member (all INSUFFICIENT or empty).

    best_suite_by_member_expectancy
        The suite id containing the highest-expectancy SUFFICIENT member
        AMONG suites that have at least one SUFFICIENT member. ``None``
        when no suite has a SUFFICIENT member. Always descriptive.

    has_sufficient_evidence
        Whether at least one suite has a SUFFICIENT member.

    conclusions
        Descriptive, non-predictive comparison conclusions.
    """

    rows: tuple[SuiteComparisonRow, ...] = field(default_factory=tuple)

    sufficient_suites: tuple[str, ...] = field(default_factory=tuple)
    insufficient_suites: tuple[str, ...] = field(default_factory=tuple)

    best_suite_by_member_expectancy: str | None = None

    has_sufficient_evidence: bool = False

    conclusions: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_empty(self) -> bool:
        return not self.rows

    @property
    def suite_count(self) -> int:
        return len(self.rows)


__all__ = [
    "SUITE_SCHEMA_VERSION",
    "SuiteComparison",
    "SuiteComparisonRow",
    "SuiteReproducibilityMetadata",
    "SuiteResult",
    "SuiteSummary",
]
