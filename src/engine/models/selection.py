"""
Domain models for the Experiment Selection & Promotion layer
(Sprint 11N).

This layer sits ABOVE the experiment suite / batch orchestration layer
(Sprint 11M). It makes a *defensible selection* among persisted
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

Design rules honoured by this module:

* No duplication of any existing logic. A selection candidate is a thin,
  read-only projection of an already-computed ``ExperimentResult`` (or
  ``SuiteResult``). No statistic is recomputed and no pipeline is rerun.

* Immutable frozen+slots dataclasses throughout.

* Evidence safety is STRUCTURAL, not merely reported. The selection
  statuses make the evidence gating explicit:

      NOT_ELIGIBLE -- cannot even be considered (INSUFFICIENT or
                      PARTIAL evidence, or a suite with no SUFFICIENT
                      member).
      CANDIDATE    -- SUFFICIENT evidence AND passes all explicit
                      selection criteria.
      REJECTED     -- SUFFICIENT evidence but failed at least one
                      explicit selection criterion.
      SELECTED     -- the single highest-ranked CANDIDATE.

  An INSUFFICIENT experiment can NEVER become CANDIDATE or SELECTED.
  A PARTIAL experiment can NEVER become SELECTED. No winner is ever
  manufactured when no eligible SUFFICIENT candidate exists.

* Missing evidence is NEVER treated as positive evidence. Missing OOS
  evidence is not "successful OOS"; missing robustness is not "robust";
  missing reproducibility is not "reproducible".

* Determinism. The selection identity, ranking and tie-breaking are
  pure functions of the persisted data. Two identical persisted datasets
  always produce the same selected entity. No random selection.

* A single, explicit ``SELECTION_SCHEMA_VERSION`` identifies the
  persisted selection decision representation (separate from the
  experiment ``SCHEMA_VERSION`` and the suite ``SUITE_SCHEMA_VERSION``
  because a selection decision is a structurally different artifact).
  The loader rejects unsupported versions so migration support can be
  layered in later.

* Descriptive-only semantics. Historical selection is DESCRIPTIVE ONLY.
  It must never be presented as predictive. The models and conclusions
  never imply live-trading readiness.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping


# ============================================================
# SELECTION SCHEMA VERSION
# ============================================================


#: Current persistence schema version for the selection decision.
#:
#: A selection decision is a structurally different artifact from an
#: experiment record or a suite manifest: it stores the selection
#: identity, the explicit criteria, the candidate projections, the
#: rejection reasons and the selected result. It therefore carries its
#: own version. The loader rejects any decision whose ``schema_version``
#: differs from this value so future migration support can be layered in
#: without rewriting the selection system. This is intentionally
#: independent of the experiment ``SCHEMA_VERSION`` (1) and the suite
#: ``SUITE_SCHEMA_VERSION`` (1).
SELECTION_SCHEMA_VERSION: int = 1


# ============================================================
# SELECTION STATUS
# ============================================================


class SelectionStatus(Enum):
    """
    The promotion status of a selection candidate.

    The status makes the evidence gating EXPLICIT and structural rather
    than relying only on reporting:

    NOT_ELIGIBLE
        The entity cannot even be considered for selection. This is the
        status assigned to experiments with INSUFFICIENT or PARTIAL
        evidence, and to suites with no SUFFICIENT member. An
        INSUFFICIENT experiment can NEVER become CANDIDATE or SELECTED;
        a PARTIAL experiment can NEVER become SELECTED.

    CANDIDATE
        The entity has SUFFICIENT evidence AND satisfies every active
        explicit selection criterion. Only CANDIDATEs are ranked for
        selection.

    REJECTED
        The entity has SUFFICIENT evidence (so it was eligible to be
        considered) but failed at least one explicit selection
        criterion. The specific failing criterion is recorded as the
        rejection reason.

    SELECTED
        The single highest-ranked CANDIDATE. There is at most ONE
        selected entity per selection. When no CANDIDATE exists, no
        entity is SELECTED -- a winner is never manufactured.
    """

    NOT_ELIGIBLE = "NOT_ELIGIBLE"
    CANDIDATE = "CANDIDATE"
    REJECTED = "REJECTED"
    SELECTED = "SELECTED"


# Forward reference: ExperimentEvidenceStatus lives in the experiment
# models module. Import it eagerly so ``isinstance`` / equality work at
# runtime when a criterion / candidate is constructed.
from engine.models.experiment import ExperimentEvidenceStatus  # noqa: E402


# ============================================================
# SELECTION TYPE
# ============================================================


class SelectionType(Enum):
    """
    The kind of entity being selected among.

    EXPERIMENT
        Select among persisted experiment results (Sprint 11J/11K).

    SUITE
        Select among persisted experiment suites (Sprint 11M). A suite
        is eligible only when it has at least one SUFFICIENT member; a
        suite is never selected merely because it contains a
        high-performing insufficient member.
    """

    EXPERIMENT = "EXPERIMENT"
    SUITE = "SUITE"


# ============================================================
# SELECTION CRITERIA
# ============================================================


@dataclass(frozen=True, slots=True)
class SelectionCriteria:
    """
    Immutable, explicit, configurable selection criteria.

    Every criterion is optional. A criterion that is ``None`` (or, for
    the boolean ``require_*`` toggles, ``False``) imposes no constraint.
    A SUFFICIENT candidate becomes a CANDIDATE when it satisfies ALL
    active criteria (logical AND); it becomes REJECTED otherwise, with
    the specific failing criterion recorded as the rejection reason.

    Missing evidence is NEVER treated as positive evidence:

    * ``require_robust=True`` rejects a candidate whose ``robust`` is
      ``None`` (robustness not assessed) -- it is NOT treated as robust.
    * ``require_oos_evidence=True`` rejects a candidate with no OOS
      evidence (``oos_expectancy is None`` or ``oos_trades == 0``) --
      missing OOS is NOT treated as successful OOS.
    * ``require_reproducible=True`` rejects a candidate whose
      ``reproducible`` is ``False``.

    Field semantics:

    require_evidence_status
        When set, a candidate's evidence status must equal this value.
        In practice this is used to require ``SUFFICIENT``. The
        structural evidence gating already excludes INSUFFICIENT /
        PARTIAL from selection, so this is an additional explicit
        declaration. ``None`` imposes no extra constraint.

    min_expectancy
        Inclusive lower bound on the completed-trade expectancy.

    min_total_r
        Inclusive lower bound on the completed-trade total R.

    max_drawdown_r
        Inclusive upper bound on the maximum drawdown (in R). A
        candidate whose ``max_drawdown_r`` exceeds this is rejected.

    require_robust
        When ``True``, the candidate must have ``robust is True``.

    require_oos_evidence
        When ``True``, the candidate must have out-of-sample evidence
        (``oos_expectancy is not None`` and ``oos_trades > 0``).

    require_reproducible
        When ``True``, the candidate must be marked reproducible.

    min_completed_trades
        Inclusive lower bound on the completed-trade count.
    """

    require_evidence_status: ExperimentEvidenceStatus | None = None

    min_expectancy: float | None = None
    min_total_r: float | None = None
    max_drawdown_r: float | None = None

    require_robust: bool = False
    require_oos_evidence: bool = False
    require_reproducible: bool = False

    min_completed_trades: int | None = None

    @property
    def is_default(self) -> bool:
        """Whether every criterion is left unset (no constraints)."""

        return (
            self.require_evidence_status is None
            and self.min_expectancy is None
            and self.min_total_r is None
            and self.max_drawdown_r is None
            and not self.require_robust
            and not self.require_oos_evidence
            and not self.require_reproducible
            and self.min_completed_trades is None
        )


# ============================================================
# REJECTION REASON
# ============================================================


@dataclass(frozen=True, slots=True)
class RejectionReason:
    """
    Why a candidate was rejected or marked ineligible.

    Field semantics:

    entity_id
        The id of the experiment or suite.

    status
        The selection status assigned (NOT_ELIGIBLE or REJECTED).

    reason
        Human-readable, deterministic explanation of why the entity
        was not promoted. Descriptive only.

    evidence_status
        The entity's evidence status, when applicable. ``None`` for a
        suite whose evidence status could not be classified (should not
        normally happen).
    """

    entity_id: str
    status: SelectionStatus
    reason: str
    evidence_status: ExperimentEvidenceStatus | None = None


# ============================================================
# SELECTION CANDIDATE
# ============================================================


@dataclass(frozen=True, slots=True)
class SelectionCandidate:
    """
    A read-only projection of one experiment (or suite) for selection.

    Every metric field is read directly from the persisted
    ``ExperimentResult`` (or, for a suite, from the metrics of its best
    SUFFICIENT member). Nothing is recomputed.

    Field semantics:

    entity_id
        The experiment id or suite id.

    label
        Human-readable label.

    selection_type
        Whether this candidate represents an experiment or a suite.

    evidence_status
        The entity's evidence status. For a suite, this is derived from
        its members (SUFFICIENT if any member is SUFFICIENT; PARTIAL if
        none sufficient but some partial; INSUFFICIENT otherwise).

    expectancy / total_r / max_drawdown_r / completed_trades
        Completed-trade performance (descriptive). For a suite, the
        metrics of its best SUFFICIENT member.

    robust
        Whether the research robustness report identified a robust
        configuration. ``None`` when not assessed. Missing robustness
        is NEVER treated as robust.

    oos_expectancy / oos_trades
        Out-of-sample expectancy and completed-trade count.
        ``oos_expectancy`` is ``None`` when no OOS evaluation was
        performed. Missing OOS is NEVER treated as successful OOS.

    reproducible
        Whether the entity is marked fully reproducible from recorded
        metadata.

    eligible
        Whether the entity is eligible to be considered (SUFFICIENT
        evidence for an experiment; at least one SUFFICIENT member for
        a suite). Eligible entities are then subject to the explicit
        criteria.

    status
        The selection status assigned by the engine
        (NOT_ELIGIBLE / CANDIDATE / REJECTED / SELECTED).

    rejection_reason
        The reason the entity was rejected or marked ineligible, when
        applicable. ``None`` for CANDIDATE and SELECTED.
    """

    entity_id: str
    label: str
    selection_type: SelectionType

    evidence_status: ExperimentEvidenceStatus

    expectancy: float
    total_r: float
    max_drawdown_r: float
    completed_trades: int

    robust: bool | None
    oos_expectancy: float | None
    oos_trades: int
    reproducible: bool

    eligible: bool

    status: SelectionStatus = SelectionStatus.NOT_ELIGIBLE
    rejection_reason: str | None = None

    @property
    def has_oos_evidence(self) -> bool:
        """Whether the entity has out-of-sample evidence."""

        return self.oos_expectancy is not None and self.oos_trades > 0

    @property
    def is_selected(self) -> bool:
        return self.status == SelectionStatus.SELECTED

    @property
    def is_candidate(self) -> bool:
        return self.status == SelectionStatus.CANDIDATE


# ============================================================
# SELECTED RESULT
# ============================================================


@dataclass(frozen=True, slots=True)
class SelectedResult:
    """
    The promoted entity, when one exists.

    There is at most ONE selected entity per selection. When no eligible
    SUFFICIENT candidate exists, the surrounding ``SelectionResult``
    carries ``selected=None`` instead -- a winner is never manufactured.

    Field semantics:

    entity_id
        The selected experiment id or suite id.

    label
        Human-readable label of the selected entity.

    selection_type
        Whether the selected entity is an experiment or a suite.

    rationale
        Deterministic, descriptive explanation of why this entity was
        selected (its ranking position and the criteria it satisfied).
        Descriptive only; never a predictive claim or live-trading
        readiness statement.
    """

    entity_id: str
    label: str
    selection_type: SelectionType
    rationale: str


# ============================================================
# PROMOTION DECISION
# ============================================================


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    """
    The final promotion outcome of a selection.

    Field semantics:

    promoted
        Whether an entity was promoted (i.e. a SELECTED entity exists).

    selected
        The :class:`SelectedResult` when an entity was promoted, else
        ``None``.

    rationale
        Deterministic, descriptive explanation of the promotion
        decision. When no entity was promoted, explains that no
        eligible SUFFICIENT candidate existed. Descriptive only.
    """

    promoted: bool
    selected: SelectedResult | None
    rationale: str


# ============================================================
# SELECTION RESULT
# ============================================================


@dataclass(frozen=True, slots=True)
class SelectionResult:
    """
    The complete, immutable result of a selection.

    The result bundles every evaluated entity (as a
    :class:`SelectionCandidate`), the rejection / ineligibility reasons,
    the explicit criteria, the selected entity (if any), the promotion
    decision and descriptive conclusions.

    Field semantics:

    selection_id
        Deterministic identifier derived from the canonical
        representation of the selection identity (selection type +
        criteria + label + metadata). No timestamps.

    label
        Human-readable selection label.

    selection_type
        Whether this selection was among experiments or suites.

    criteria
        The explicit :class:`SelectionCriteria` used.

    candidates
        One :class:`SelectionCandidate` per evaluated entity, in
        ascending entity-id order (deterministic baseline). Each
        candidate carries its assigned ``status``.

    rejected
        :class:`RejectionReason` for every entity that was NOT_ELIGIBLE
        or REJECTED, in ascending entity-id order.

    selected
        The :class:`SelectedResult` when an entity was promoted, else
        ``None``. ``None`` when no eligible SUFFICIENT candidate exists.

    promotion
        The :class:`PromotionDecision` summarising the outcome.

    conclusions
        Descriptive, non-predictive conclusions. Always state that the
        result is descriptive and that historical performance is not
        predictive; never imply live-trading readiness.

    metadata
        Arbitrary deterministic string metadata identifying the run.
    """

    selection_id: str
    label: str
    selection_type: SelectionType
    criteria: SelectionCriteria

    candidates: tuple[SelectionCandidate, ...] = field(default_factory=tuple)
    rejected: tuple[RejectionReason, ...] = field(default_factory=tuple)
    selected: SelectedResult | None = None
    promotion: PromotionDecision | None = None

    conclusions: tuple[str, ...] = field(default_factory=tuple)
    metadata: Mapping[str, str] = field(default_factory=dict)

    @property
    def has_selected(self) -> bool:
        """Whether an entity was promoted."""

        return self.selected is not None

    @property
    def all_evaluated(self) -> int:
        """Number of entities evaluated (candidates)."""

        return len(self.candidates)

    @property
    def candidate_count(self) -> int:
        """Number of CANDIDATEs (SUFFICIENT + passing all criteria)."""

        return sum(
            1 for c in self.candidates if c.status == SelectionStatus.CANDIDATE
        )

    @property
    def selected_entity_id(self) -> str | None:
        """The selected entity id, or ``None`` when nothing was promoted."""

        return self.selected.entity_id if self.selected is not None else None


__all__ = [
    "PromotionDecision",
    "RejectionReason",
    "SELECTION_SCHEMA_VERSION",
    "SelectionCandidate",
    "SelectionCriteria",
    "SelectionResult",
    "SelectionStatus",
    "SelectionType",
    "SelectedResult",
]
