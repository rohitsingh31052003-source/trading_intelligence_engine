"""
Domain models for the reproducible Research Experiment Framework
(Sprint 11J).

This layer sits ABOVE the research / robustness layer
(Sprint 11H/11I). It makes complete research runs reproducible,
identifiable, comparable and reportable.

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

Design rules honoured by this module:

* No duplication of trading, validation, performance, pipeline,
  reporting or research logic. Existing models are referenced
  by composition, never copied.

* Immutable frozen+slots dataclasses throughout.

* Deterministic experiment identity derived from a canonical
  representation of the configuration (no timestamps, no
  nondeterministic values).

* Explicit evidence sufficiency: an experiment result clearly
  distinguishes sufficient evidence from insufficient evidence
  and from raw/descriptive results. The framework never claims
  a strategy "is profitable"; it reports what was observed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


# ============================================================
# EVIDENCE STATUS
# ============================================================


class ExperimentEvidenceStatus(Enum):
    """
    Sufficiency of the evidence produced by an experiment.

    The experiment framework must never imply statistical
    confidence from tiny samples. This status makes the
    distinction explicit so downstream comparison and reporting
    can gate conclusions accordingly.

    INSUFFICIENT
        The run produced fewer completed trades than the
        configured minimum for inference. No reliable
        conclusion may be drawn. Raw/descriptive numbers are
        still available but must not be interpreted as
        evidence.

    PARTIAL
        The run produced enough completed trades for a basic
        overall inference, but at least one secondary evidence
        source (regime samples, out-of-sample trades, parameter
        observations) is insufficient. Conclusions are
        provisional.

    SUFFICIENT
        All configured evidence sources meet their minimum
        thresholds. The descriptive results may be treated as
        internally consistent evidence (still descriptive, not
        predictive).
    """

    INSUFFICIENT = "INSUFFICIENT"
    PARTIAL = "PARTIAL"
    SUFFICIENT = "SUFFICIENT"


# ============================================================
# DATASET SPECIFICATION
# ============================================================


@dataclass(frozen=True, slots=True)
class DatasetSpec:
    """
    Immutable, deterministic identity of a dataset.

    Field semantics:

    name
        Short identifying name. Built-in deterministic datasets
        use ``"trending"`` / ``"flat"`` / ``"minimal"``. Custom
        datasets use any caller-chosen name (e.g.
        ``"custom"``).

    content_hash
        Deterministic SHA-256 hash (hex) of the canonical candle
        representation. For built-in deterministic datasets this
        MAY be None (the name alone is deterministic). For
        custom datasets this MUST be supplied so the experiment
        identity is stable and changes to the data change the
        experiment ID.

    size
        Number of candles in the dataset. Optional; recorded for
        human inspection. Not required for identity because the
        content hash already captures the data.
    """

    name: str
    content_hash: str | None = None
    size: int | None = None


# ============================================================
# REPRODUCIBILITY METADATA
# ============================================================


@dataclass(frozen=True, slots=True)
class ReproducibilityMetadata:
    """
    Explicit reproducibility metadata for an experiment run.

    Every field that cannot be determined is represented
    explicitly as ``None`` rather than fabricated.

    Field semantics:

    experiment_id
        Deterministic identifier derived from the canonical
        configuration representation.

    configuration_hash
        SHA-256 (hex) of the canonical configuration
        representation. A short, stable fingerprint of the full
        configuration.

    configuration_representation
        The canonical (sorted-key) string representation used to
        derive both the experiment ID and the configuration hash.

    dataset_identity
        The dataset name.

    dataset_content_hash
        The actual content hash of the resolved dataset (always
        populated by the runner, even for built-in datasets).

    dataset_size
        Number of candles in the resolved dataset.

    parameter_values
        Relevant strategy / pipeline / research parameter values
        captured for quick inspection. Deterministic.

    code_version
        Installed package version identifier, when safely
        available via ``importlib.metadata``; otherwise
        ``"UNKNOWN"``. Never fabricated.

    random_seed
        The random seed declared in the configuration, if any.
        ``None`` when no seed is applicable.

    reproducible
        Whether the run is fully reproducible from the recorded
        metadata. True when a configuration representation,
        dataset identity and (where applicable) seed are all
        present. This is a structural statement, not a
        statistical guarantee.
    """

    experiment_id: str
    configuration_hash: str
    configuration_representation: str

    dataset_identity: str
    dataset_content_hash: str | None
    dataset_size: int

    parameter_values: Mapping[str, str] = field(default_factory=dict)

    code_version: str = "UNKNOWN"
    random_seed: int | None = None

    reproducible: bool = True

    @property
    def has_seed(self) -> bool:
        return self.random_seed is not None

    @property
    def code_version_known(self) -> bool:
        return self.code_version != "UNKNOWN"


# ============================================================
# EXPERIMENT SUMMARY
# ============================================================


@dataclass(frozen=True, slots=True)
class ExperimentSummary:
    """
    Curated read-only summary of an experiment result.

    This is a thin projection of the reused ``EvaluationReport``
    and ``ResearchReport``. It does NOT recompute any statistic;
    it reads the authoritative values produced by the existing
    engines so comparison and reporting layers have a single
    flat view.

    Field semantics:

    completed_trades
        Completed (WIN/LOSS) trades from the pipeline.

    win_rate / expectancy / total_r / profit_factor / max_drawdown_r
        Overall completed-trade performance (descriptive).

    robust
        Whether the research robustness report identified at
        least one robust parameter configuration. ``None`` when
        no robustness analysis was performed.

    descriptive_best
        The descriptive-best parameter value (descriptive only).
        ``None`` when no parameter sweep was performed.

    oos_expectancy / oos_trades
        Out-of-sample expectancy and completed-trade count.
        ``oos_expectancy`` is ``None`` when no OOS evaluation
        was performed.

    data_sufficient
        Whether the data-sufficiency gate passed.

    leakage_passed
        Whether the leakage audit reported no failures.
        ``None`` when no leakage audit was performed.

    leakage_not_verified
        Whether the leakage audit surfaced any NOT VERIFIED
        items (properties it could not prove).

    evidence_status
        Overall sufficiency classification.
    """

    completed_trades: int
    win_rate: float
    expectancy: float
    total_r: float
    profit_factor: float
    max_drawdown_r: float

    robust: bool | None
    descriptive_best: Any

    oos_expectancy: float | None
    oos_trades: int

    data_sufficient: bool
    leakage_passed: bool | None
    leakage_not_verified: bool

    evidence_status: ExperimentEvidenceStatus

    @property
    def is_insufficient(self) -> bool:
        return self.evidence_status == ExperimentEvidenceStatus.INSUFFICIENT

    @property
    def is_sufficient(self) -> bool:
        return self.evidence_status == ExperimentEvidenceStatus.SUFFICIENT


# ============================================================
# EXPERIMENT RESULT
# ============================================================


@dataclass(frozen=True, slots=True)
class ExperimentResult:
    """
    Immutable result of a complete, reproducible research
    experiment.

    The result references -- never duplicates -- the existing
    ``EvaluationReport`` (Sprint 11G) and ``ResearchReport``
    (Sprint 11H/11I). Downstream layers can access the raw
    pipeline result via ``research_report.result`` and the
    evaluation report via ``evaluation_report``.

    Field semantics:

    experiment_id
        Deterministic identifier (derived from the config).

    config
        The immutable ``ExperimentConfig`` that produced this
        result.

    dataset
        The resolved dataset specification (with actual content
        hash populated by the runner).

    dataset_size
        Number of candles in the resolved dataset.

    evaluation_report
        The reused ``EvaluationReport`` (pipeline / signal /
        trade statistics).

    research_report
        The reused ``ResearchReport`` (regime, segmentation,
        sensitivity, robustness, walk-forward, OOS, data
        sufficiency, leakage, conclusions).

    reproducibility
        Explicit reproducibility metadata.

    summary
        Curated flat summary distinguishing raw/descriptive
        results from sufficient vs insufficient evidence.

    label
        Human-readable experiment label (from the config).

    metadata
        Arbitrary deterministic string metadata (from the
        config).
    """

    experiment_id: str
    config: Any
    dataset: DatasetSpec
    dataset_size: int

    evaluation_report: Any
    research_report: Any

    reproducibility: ReproducibilityMetadata
    summary: ExperimentSummary

    label: str = ""
    metadata: Mapping[str, str] = field(default_factory=dict)

    @property
    def evidence_status(self) -> ExperimentEvidenceStatus:
        return self.summary.evidence_status

    @property
    def is_sufficient(self) -> bool:
        return self.summary.is_sufficient

    @property
    def is_insufficient(self) -> bool:
        return self.summary.is_insufficient


# ============================================================
# COMPARISON
# ============================================================


@dataclass(frozen=True, slots=True)
class ExperimentComparisonRow:
    """
    One row of an experiment comparison.

    A flat, read-only projection of an ``ExperimentResult``
    suitable for side-by-side comparison. Values are read from
    the reused reports; nothing is recomputed.
    """

    experiment_id: str
    label: str

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

    evidence_status: ExperimentEvidenceStatus

    @property
    def is_insufficient(self) -> bool:
        return self.evidence_status == ExperimentEvidenceStatus.INSUFFICIENT


@dataclass(frozen=True, slots=True)
class ExperimentComparison:
    """
    Immutable comparison of multiple experiment results.

    The comparison NEVER automatically declares one experiment
    "best" unless the evidence supports such a conclusion.
    When experiments have insufficient data, that is made
    explicit. Descriptive ranking is provided only where
    appropriate and is always labelled as descriptive.

    Field semantics:

    rows
        One ``ExperimentComparisonRow`` per experiment, in the
        order supplied.

    sufficient_experiments
        Experiment IDs whose evidence status is SUFFICIENT.

    insufficient_experiments
        Experiment IDs whose evidence status is INSUFFICIENT.

    partial_experiments
        Experiment IDs whose evidence status is PARTIAL.

    best_by_expectancy
        The experiment ID with the highest expectancy AMONG
        SUFFICIENT experiments only. ``None`` when no
        experiment is sufficient (insufficient data must not
        produce a "best"). Always descriptive.

    best_by_total_r
        The experiment ID with the highest total R AMONG
        SUFFICIENT experiments only. ``None`` when none
        sufficient. Always descriptive.

    has_sufficient_evidence
        Whether at least one experiment is SUFFICIENT.

    conclusions
        Descriptive, non-predictive comparison conclusions.
        The comparison never claims a strategy "is best" or
        "is profitable"; it reports what was observed and
        flags insufficient evidence explicitly.
    """

    rows: tuple[ExperimentComparisonRow, ...] = field(
        default_factory=tuple,
    )

    sufficient_experiments: tuple[str, ...] = field(default_factory=tuple)
    insufficient_experiments: tuple[str, ...] = field(
        default_factory=tuple,
    )
    partial_experiments: tuple[str, ...] = field(default_factory=tuple)

    best_by_expectancy: str | None = None
    best_by_total_r: str | None = None

    has_sufficient_evidence: bool = False

    conclusions: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_empty(self) -> bool:
        return not self.rows

    @property
    def experiment_count(self) -> int:
        return len(self.rows)
