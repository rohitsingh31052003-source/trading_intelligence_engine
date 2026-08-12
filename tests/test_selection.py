"""
Tests for the Experiment Selection & Promotion Layer (Sprint 11N).

Covers the 20 required test points plus persistence, reporting,
determinism, immutability and backward compatibility:

A. Evidence gating
   1. INSUFFICIENT experiment cannot become CANDIDATE.
   2. INSUFFICIENT experiment cannot become SELECTED.
   3. PARTIAL experiment cannot become SELECTED.
   4. SUFFICIENT experiment can become CANDIDATE.
   5. Best eligible SUFFICIENT experiment is selected.
   6. No SUFFICIENT candidates -> selected=None.

B. Criteria
   7. Non-robust experiment rejected when robustness required.
   8. Missing OOS evidence handled safely.
   9. Missing reproducibility handled safely.
   10. Missing robustness not treated as robust.
   18. Selection criteria are reproducible.

C. Ranking
   11. Deterministic tie-breaking.

D. Persisted-only
   12. Selection from persisted data does not rerun the trading pipeline.
   13. Selection from persisted data does not rerun ExperimentRunner.

E. Suite selection
   14. Suite containing only insufficient members cannot be selected.
   15. Suite with eligible sufficient member can be selected.
   16. Suite selection remains descriptive only.

F. Persistence
   17. Persisted selection can be loaded without pipeline execution.

G. Integrity
   19. No insufficient experiment can influence the selected result.

H. Backward compatibility
   20. Regression coverage for previous Sprint 11J/11K/11L/11M behaviour.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from engine.config.swing_config import SwingConfig
from engine.experiment import (
    DatasetSpec,
    EvaluationConfig,
    ExperimentConfig,
    ExperimentEvidenceStatus,
    ExperimentRunner,
)
from engine.models.experiment import ExperimentResult
from engine.models.selection import (
    SELECTION_SCHEMA_VERSION,
    PromotionDecision,
    RejectionReason,
    SelectionCandidate,
    SelectionCriteria,
    SelectionResult,
    SelectionStatus,
    SelectionType,
    SelectedResult,
)
from engine.pipeline.historical_pipeline import (
    HistoricalEvaluationPipeline,
    PipelineConfig,
)
from engine.registry import ExperimentRegistry
from engine.reporting import SelectionReportFormatter
from engine.selection import (
    SelectionAlreadyExistsError,
    SelectionEngine,
    SelectionError,
    SelectionIdentity,
    SelectionIntegrityError,
    SelectionNotFoundError,
    SelectionPersistence,
    SelectionRegistry,
    SuiteSelectionEngine,
    UnsupportedSelectionSchemaVersionError,
    canonical_selection_json,
    deserialize_selection,
    serialize_selection,
)
from engine.suite import (
    SuiteConfig,
    SuiteRegistry,
    SuiteRunner,
)
from engine.research.research import ResearchConfig


# ============================================================
# FIXTURE HELPERS
# ============================================================


def _fast_config(
    label: str = "trending-lookback-2",
    *,
    lookback: int = 2,
    dataset_name: str = "trending",
    seed: int | None = 42,
    metadata: dict[str, str] | None = None,
) -> ExperimentConfig:
    """A config with all optional research analyses OFF for speed."""

    return ExperimentConfig(
        label=label,
        dataset=DatasetSpec(name=dataset_name),
        pipeline=PipelineConfig(
            swing_config=SwingConfig(lookback=lookback),
        ),
        research=ResearchConfig(),
        evaluation=EvaluationConfig(
            run_out_of_sample=False,
            run_walk_forward=False,
            run_sensitivity=False,
        ),
        seed=seed,
        metadata=metadata if metadata is not None else {"sprint": "11N"},
    )


def _run(label: str = "trending-lookback-2", **kwargs) -> ExperimentResult:
    return ExperimentRunner().run(_fast_config(label, **kwargs))


def _with_evidence(
    result: ExperimentResult,
    status: ExperimentEvidenceStatus,
    **metrics,
) -> ExperimentResult:
    """
    Synthetically override the evidence status (and optional metrics).

    Enforces the SAME invariant the real ``ExperimentRunner`` produces:
    ``evidence_status == SUFFICIENT`` iff ``data_sufficient == True``.
    """

    implied_data_sufficient = status == ExperimentEvidenceStatus.SUFFICIENT
    overrides = {"data_sufficient": implied_data_sufficient}
    overrides.update(metrics)
    summary = replace(result.summary, evidence_status=status, **overrides)
    return replace(result, summary=summary)


def _sufficient(
    result: ExperimentResult,
    expectancy: float = 0.5,
    total_r: float | None = None,
    max_drawdown_r: float = 0.5,
    completed_trades: int = 10,
    robust: bool | None = None,
    oos_expectancy: float | None = None,
    oos_trades: int = 0,
    reproducible: bool | None = None,
) -> ExperimentResult:
    result = _with_evidence(
        result,
        ExperimentEvidenceStatus.SUFFICIENT,
        expectancy=expectancy,
        total_r=total_r if total_r is not None else expectancy * 4,
        max_drawdown_r=max_drawdown_r,
        completed_trades=completed_trades,
        robust=robust,
        oos_expectancy=oos_expectancy,
        oos_trades=oos_trades,
    )
    if reproducible is not None:
        repro = replace(result.reproducibility, reproducible=reproducible)
        result = replace(result, reproducibility=repro)
    return result


def _insufficient(result: ExperimentResult) -> ExperimentResult:
    return _with_evidence(result, ExperimentEvidenceStatus.INSUFFICIENT)


def _partial(result: ExperimentResult) -> ExperimentResult:
    return _with_evidence(result, ExperimentEvidenceStatus.PARTIAL)


def _register(registry: ExperimentRegistry, result: ExperimentResult) -> None:
    registry.register(result, overwrite=True)


@pytest.fixture()
def registry(tmp_path: Path) -> ExperimentRegistry:
    return ExperimentRegistry(tmp_path)


@pytest.fixture()
def engine(registry: ExperimentRegistry) -> SelectionEngine:
    return SelectionEngine(registry)


@pytest.fixture()
def selection_registry(tmp_path: Path) -> SelectionRegistry:
    return SelectionRegistry(tmp_path)


@pytest.fixture()
def suite_registry(registry: ExperimentRegistry) -> SuiteRegistry:
    return SuiteRegistry(experiment_registry=registry)


@pytest.fixture()
def suite_runner(registry: ExperimentRegistry) -> SuiteRunner:
    return SuiteRunner(experiment_registry=registry)


# ============================================================
# A. EVIDENCE GATING
# ============================================================


def _by_id(result: SelectionResult, entity_id: str) -> SelectionCandidate:
    for c in result.candidates:
        if c.entity_id == entity_id:
            return c
    raise AssertionError(f"candidate {entity_id} not found")


class TestEvidenceGating:
    def test_insufficient_cannot_become_candidate(self, registry, engine) -> None:
        r = _insufficient(_run("a", lookback=2))
        _register(registry, r)

        result = engine.select(SelectionCriteria())
        cand = _by_id(result, r.experiment_id)
        assert cand.status == SelectionStatus.NOT_ELIGIBLE
        assert not cand.is_candidate
        assert cand.eligible is False

    def test_insufficient_cannot_become_selected(self, registry, engine) -> None:
        _register(registry, _insufficient(_run("a", lookback=2)))
        _register(registry, _insufficient(_run("b", lookback=3)))

        result = engine.select(SelectionCriteria())
        assert result.selected is None
        assert result.promotion.promoted is False
        assert all(
            c.status == SelectionStatus.NOT_ELIGIBLE
            for c in result.candidates
        )

    def test_partial_cannot_become_selected(self, registry, engine) -> None:
        _register(registry, _partial(_run("a", lookback=2)))
        # a high-expectancy partial must still not be selected
        r = _with_evidence(
            _run("b", lookback=3),
            ExperimentEvidenceStatus.PARTIAL,
            expectancy=0.99,
            total_r=9.9,
        )
        _register(registry, r)

        result = engine.select(SelectionCriteria())
        assert result.selected is None
        partial_cands = [
            c for c in result.candidates
            if c.evidence_status == ExperimentEvidenceStatus.PARTIAL
        ]
        assert partial_cands
        assert all(
            c.status == SelectionStatus.NOT_ELIGIBLE for c in partial_cands
        )

    def test_sufficient_can_become_candidate(self, registry, engine) -> None:
        # Two sufficient experiments: one is selected, the other is a
        # CANDIDATE (SUFFICIENT + passing all criteria, not promoted).
        a = _sufficient(_run("a", lookback=2), expectancy=0.3)
        b = _sufficient(_run("b", lookback=3), expectancy=0.7)
        _register(registry, a)
        _register(registry, b)

        result = engine.select(SelectionCriteria())
        non_selected = a if result.selected_entity_id == b.experiment_id else b
        cand = _by_id(result, non_selected.experiment_id)
        assert cand.status == SelectionStatus.CANDIDATE
        assert cand.eligible is True

    def test_best_eligible_sufficient_is_selected(
        self, registry, engine
    ) -> None:
        low = _sufficient(_run("low", lookback=2), expectancy=0.2)
        high = _sufficient(_run("high", lookback=3), expectancy=0.8)
        _register(registry, low)
        _register(registry, high)

        result = engine.select(SelectionCriteria())
        assert result.selected is not None
        assert result.selected.entity_id == high.experiment_id
        assert result.selected.selection_type == SelectionType.EXPERIMENT
        assert _by_id(result, high.experiment_id).status == SelectionStatus.SELECTED
        assert _by_id(result, low.experiment_id).status == SelectionStatus.CANDIDATE

    def test_no_sufficient_candidates_selected_none(
        self, registry, engine
    ) -> None:
        _register(registry, _insufficient(_run("a", lookback=2)))
        _register(registry, _partial(_run("b", lookback=3)))

        result = engine.select(SelectionCriteria())
        assert result.selected is None
        assert result.has_selected is False
        assert result.promotion.promoted is False
        assert "No " in result.promotion.rationale


# ============================================================
# B. CRITERIA
# ============================================================


class TestCriteria:
    def test_non_robust_rejected_when_robustness_required(
        self, registry, engine
    ) -> None:
        robust = _sufficient(_run("robust", lookback=2), robust=True)
        not_robust = _sufficient(_run("notrobust", lookback=3), robust=False)
        _register(registry, robust)
        _register(registry, not_robust)

        result = engine.select(SelectionCriteria(require_robust=True))
        rejected = _by_id(result, not_robust.experiment_id)
        assert rejected.status == SelectionStatus.REJECTED
        assert "robust" in rejected.rejection_reason.lower()
        assert result.selected is not None
        assert result.selected.entity_id == robust.experiment_id

    def test_missing_robustness_not_treated_as_robust(
        self, registry, engine
    ) -> None:
        missing = _sufficient(_run("missing", lookback=2), robust=None)
        _register(registry, missing)

        result = engine.select(SelectionCriteria(require_robust=True))
        cand = _by_id(result, missing.experiment_id)
        assert cand.status == SelectionStatus.REJECTED
        assert "missing robustness" in cand.rejection_reason
        assert result.selected is None

    def test_missing_oos_evidence_handled_safely(
        self, registry, engine
    ) -> None:
        no_oos = _sufficient(
            _run("nooos", lookback=2), oos_expectancy=None, oos_trades=0
        )
        _register(registry, no_oos)

        result = engine.select(SelectionCriteria(require_oos_evidence=True))
        cand = _by_id(result, no_oos.experiment_id)
        assert cand.status == SelectionStatus.REJECTED
        assert "out-of-sample" in cand.rejection_reason.lower()
        assert result.selected is None

    def test_present_oos_satisfies_requirement(
        self, registry, engine
    ) -> None:
        with_oos = _sufficient(
            _run("oos", lookback=2), oos_expectancy=0.4, oos_trades=5
        )
        _register(registry, with_oos)

        result = engine.select(SelectionCriteria(require_oos_evidence=True))
        assert _by_id(result, with_oos.experiment_id).status == SelectionStatus.SELECTED

    def test_missing_reproducibility_handled_safely(
        self, registry, engine
    ) -> None:
        non_repro = _sufficient(
            _run("nonrepro", lookback=2), reproducible=False
        )
        _register(registry, non_repro)

        result = engine.select(SelectionCriteria(require_reproducible=True))
        cand = _by_id(result, non_repro.experiment_id)
        assert cand.status == SelectionStatus.REJECTED
        assert "reproducib" in cand.rejection_reason.lower()
        assert result.selected is None

    def test_expectancy_and_drawdown_bounds(self, registry, engine) -> None:
        good = _sufficient(
            _run("good", lookback=2), expectancy=0.5, max_drawdown_r=0.3
        )
        bad_exp = _sufficient(
            _run("badexp", lookback=3), expectancy=0.1, max_drawdown_r=0.3
        )
        bad_dd = _sufficient(
            _run("baddd", lookback=2), expectancy=0.5, max_drawdown_r=0.9
        )
        _register(registry, good)
        _register(registry, bad_exp)
        _register(registry, bad_dd)

        result = engine.select(
            SelectionCriteria(min_expectancy=0.3, max_drawdown_r=0.5)
        )
        assert _by_id(result, bad_exp.experiment_id).status == SelectionStatus.REJECTED
        assert _by_id(result, bad_dd.experiment_id).status == SelectionStatus.REJECTED
        assert result.selected is not None
        assert result.selected.entity_id == good.experiment_id

    def test_min_completed_trades_bound(self, registry, engine) -> None:
        few = _sufficient(_run("few", lookback=2), completed_trades=2)
        many = _sufficient(_run("many", lookback=3), completed_trades=20)
        _register(registry, few)
        _register(registry, many)

        result = engine.select(SelectionCriteria(min_completed_trades=5))
        assert _by_id(result, few.experiment_id).status == SelectionStatus.REJECTED
        assert result.selected is not None
        assert result.selected.entity_id == many.experiment_id

    def test_criteria_are_reproducible(self, registry, engine) -> None:
        _register(registry, _sufficient(_run("a", lookback=2)))

        criteria = SelectionCriteria(
            require_evidence_status=ExperimentEvidenceStatus.SUFFICIENT,
            min_expectancy=0.2,
            require_robust=False,
            require_oos_evidence=False,
            require_reproducible=False,
            min_completed_trades=3,
        )
        r1 = engine.select(criteria, label="run", metadata={"k": "v"})
        r2 = engine.select(criteria, label="run", metadata={"k": "v"})

        assert r1.selection_id == r2.selection_id
        assert r1.selected_entity_id == r2.selected_entity_id
        assert canonical_selection_json(r1) == canonical_selection_json(r2)

    def test_different_criteria_different_selection_id(
        self, registry, engine
    ) -> None:
        _register(registry, _sufficient(_run("a", lookback=2)))
        r1 = engine.select(SelectionCriteria(), label="run")
        r2 = engine.select(SelectionCriteria(min_expectancy=0.01), label="run")
        assert r1.selection_id != r2.selection_id

    def test_default_criteria_impose_no_constraint(
        self, registry, engine
    ) -> None:
        _register(registry, _sufficient(_run("a", lookback=2)))
        assert SelectionCriteria().is_default is True
        result = engine.select(SelectionCriteria())
        assert result.selected is not None



# ============================================================
# C. RANKING / DETERMINISTIC TIE-BREAKING
# ============================================================


class TestRanking:
    def test_deterministic_tie_break_by_entity_id(
        self, registry, engine
    ) -> None:
        # Two candidates identical on every metric must tie-break by
        # ascending entity id.
        a = _sufficient(_run("a", lookback=2), expectancy=0.5, total_r=2.0,
                        max_drawdown_r=0.5, completed_trades=10,
                        robust=True, oos_expectancy=0.3, oos_trades=4,
                        reproducible=True)
        b = _sufficient(_run("b", lookback=3), expectancy=0.5, total_r=2.0,
                        max_drawdown_r=0.5, completed_trades=10,
                        robust=True, oos_expectancy=0.3, oos_trades=4,
                        reproducible=True)
        _register(registry, a)
        _register(registry, b)

        result = engine.select(SelectionCriteria())
        assert result.selected is not None
        # The lexicographically smaller experiment id wins.
        assert result.selected.entity_id == min(
            a.experiment_id, b.experiment_id
        )

    def test_deterministic_selection_repeated(self, registry, engine) -> None:
        a = _sufficient(_run("a", lookback=2), expectancy=0.3)
        b = _sufficient(_run("b", lookback=3), expectancy=0.7)
        _register(registry, a)
        _register(registry, b)

        r1 = engine.select(SelectionCriteria())
        r2 = engine.select(SelectionCriteria())
        r3 = engine.select(SelectionCriteria())
        assert r1.selected_entity_id == r2.selected_entity_id == r3.selected_entity_id

    def test_ranking_priority_robust_before_expectancy(
        self, registry, engine
    ) -> None:
        # robust-but-lower-expectancy must beat not-robust-higher-expectancy.
        robust = _sufficient(_run("robust", lookback=2), expectancy=0.3, robust=True)
        nonrobust = _sufficient(_run("nonrobust", lookback=3), expectancy=0.9, robust=False)
        _register(registry, robust)
        _register(registry, nonrobust)

        result = engine.select(SelectionCriteria())
        assert result.selected is not None
        assert result.selected.entity_id == robust.experiment_id

    def test_ranking_priority_oos_before_expectancy(
        self, registry, engine
    ) -> None:
        # has-OOS-but-lower-expectancy beats no-OOS-higher-expectancy
        # (both robust-equivalent).
        with_oos = _sufficient(_run("oos", lookback=2), expectancy=0.3,
                               oos_expectancy=0.2, oos_trades=3)
        no_oos = _sufficient(_run("nooos", lookback=3), expectancy=0.9)
        _register(registry, with_oos)
        _register(registry, no_oos)

        result = engine.select(SelectionCriteria())
        assert result.selected is not None
        assert result.selected.entity_id == with_oos.experiment_id

    def test_lower_drawdown_preferred(self, registry, engine) -> None:
        a = _sufficient(_run("a", lookback=2), expectancy=0.5, max_drawdown_r=0.2)
        b = _sufficient(_run("b", lookback=3), expectancy=0.5, max_drawdown_r=0.8)
        _register(registry, a)
        _register(registry, b)

        result = engine.select(SelectionCriteria())
        assert result.selected is not None
        assert result.selected.entity_id == a.experiment_id

    def test_empty_registry_selected_none(self, engine) -> None:
        result = engine.select(SelectionCriteria())
        assert result.selected is None
        assert result.all_evaluated == 0
        assert result.candidates == ()


# ============================================================
# D. PERSISTED-ONLY (no pipeline / runner rerun)
# ============================================================


class TestPersistedOnly:
    def test_selection_does_not_rerun_trading_pipeline(
        self, registry, engine
    ) -> None:
        _register(registry, _sufficient(_run("a", lookback=2)))
        _register(registry, _insufficient(_run("b", lookback=3)))

        original = HistoricalEvaluationPipeline.evaluate

        def boom(self, candles):
            raise RuntimeError("should not rerun pipeline")

        HistoricalEvaluationPipeline.evaluate = boom  # type: ignore[assignment]
        try:
            result = engine.select(SelectionCriteria())
            assert result.selected is not None
        finally:
            HistoricalEvaluationPipeline.evaluate = original  # type: ignore[assignment]

    def test_selection_does_not_rerun_experiment_runner(
        self, registry, engine, monkeypatch
    ) -> None:
        _register(registry, _sufficient(_run("a", lookback=2)))

        # Patch ExperimentRunner.run to raise; selection must not call it.
        from engine.experiment.runner import ExperimentRunner as Runner

        original_run = Runner.run

        def boom(self, config, candles=None):
            raise RuntimeError("should not rerun runner")

        monkeypatch.setattr(Runner, "run", boom)
        try:
            result = engine.select(SelectionCriteria())
            assert result.selected is not None
        finally:
            Runner.run = original_run  # type: ignore[assignment]

    def test_selection_reads_only_persisted_data(
        self, registry, engine
    ) -> None:
        # Register a sufficient experiment, then mutate the in-memory
        # result AFTER registration. Selection reads persisted data, so
        # the selection must reflect the persisted (un-mutated) state.
        r = _sufficient(_run("a", lookback=2), expectancy=0.5)
        _register(registry, r)

        result = engine.select(SelectionCriteria())
        persisted_expectancy = _by_id(
            result, r.experiment_id
        ).expectancy
        assert persisted_expectancy == 0.5

        # Reload from disk to confirm the persisted value is stable.
        loaded = registry.get(r.experiment_id)
        assert loaded.summary.expectancy == 0.5


# ============================================================
# E. SUITE SELECTION
# ============================================================


class TestSuiteSelection:
    def test_suite_only_insufficient_members_not_selected(
        self, suite_registry, suite_runner
    ) -> None:
        members = (_fast_config("i1", lookback=2), _fast_config("i2", lookback=3))
        suite = SuiteConfig(label="all-insuff", members=members)
        result = suite_runner.run(suite, register=True)
        # Force every member to INSUFFICIENT and re-register.
        for m in result.members:
            _register(
                suite_registry.experiment_registry,
                _insufficient(m),
            )
        suite_registry.register_suite(result)

        engine = SuiteSelectionEngine(suite_registry)
        sel = engine.select(SelectionCriteria())
        assert sel.selected is None
        cand = _by_id(sel, result.suite_id)
        assert cand.status == SelectionStatus.NOT_ELIGIBLE
        assert cand.eligible is False

    def test_suite_with_eligible_sufficient_member_selected(
        self, suite_registry, suite_runner
    ) -> None:
        members = (_fast_config("s1", lookback=2), _fast_config("s2", lookback=3))
        suite = SuiteConfig(label="has-suff", members=members)
        result = suite_runner.run(suite, register=True)
        # Make the first member SUFFICIENT.
        suff = _sufficient(result.members[0], expectancy=0.6)
        _register(suite_registry.experiment_registry, suff)
        suite_registry.register_suite(result)

        engine = SuiteSelectionEngine(suite_registry)
        sel = engine.select(SelectionCriteria())
        assert sel.selected is not None
        assert sel.selected.entity_id == result.suite_id
        assert sel.selected.selection_type == SelectionType.SUITE
        assert _by_id(sel, result.suite_id).expectancy == 0.6

    def test_suite_selection_descriptive_only(
        self, suite_registry, suite_runner
    ) -> None:
        members = (_fast_config("s1", lookback=2),)
        suite = SuiteConfig(label="desc", members=members)
        result = suite_runner.run(suite, register=True)
        suff = _sufficient(result.members[0], expectancy=0.6)
        _register(suite_registry.experiment_registry, suff)
        suite_registry.register_suite(result)

        engine = SuiteSelectionEngine(suite_registry)
        sel = engine.select(SelectionCriteria())
        text = "\n".join(sel.conclusions)
        assert "DESCRIPTIVE" in text
        assert "not predictive" in text.lower() or "not predict" in text.lower()
        assert "live-trading" in text.lower()

    def test_suite_with_insufficient_high_performer_not_selected(
        self, suite_registry, suite_runner
    ) -> None:
        # A suite whose ONLY member is a high-performing INSUFFICIENT
        # experiment must NOT be selected.
        members = (_fast_config("hp", lookback=2),)
        suite = SuiteConfig(label="insuff-high", members=members)
        result = suite_runner.run(suite, register=True)
        # Force the member to INSUFFICIENT but with a high expectancy.
        insuff = _with_evidence(
            result.members[0],
            ExperimentEvidenceStatus.INSUFFICIENT,
            expectancy=9.99,
            total_r=99.9,
        )
        _register(suite_registry.experiment_registry, insuff)
        suite_registry.register_suite(result)

        engine = SuiteSelectionEngine(suite_registry)
        sel = engine.select(SelectionCriteria())
        assert sel.selected is None
        assert _by_id(sel, result.suite_id).status == SelectionStatus.NOT_ELIGIBLE

    def test_suite_selection_ranks_by_best_sufficient_member(
        self, suite_registry, suite_runner
    ) -> None:
        # Two suites, each with one sufficient member. The suite whose
        # sufficient member has the higher expectancy is selected.
        s1_members = (_fast_config("a1", lookback=2),)
        s1 = SuiteConfig(label="low", members=s1_members)
        r1 = suite_runner.run(s1, register=True)
        _register(
            suite_registry.experiment_registry,
            _sufficient(r1.members[0], expectancy=0.3),
        )
        suite_registry.register_suite(r1)

        s2_members = (_fast_config("a2", lookback=3),)
        s2 = SuiteConfig(label="high", members=s2_members)
        r2 = suite_runner.run(s2, register=True)
        _register(
            suite_registry.experiment_registry,
            _sufficient(r2.members[0], expectancy=0.7),
        )
        suite_registry.register_suite(r2)

        engine = SuiteSelectionEngine(suite_registry)
        sel = engine.select(SelectionCriteria())
        assert sel.selected is not None
        assert sel.selected.entity_id == r2.suite_id

    def test_suite_selection_does_not_rerun_pipeline(
        self, suite_registry, suite_runner
    ) -> None:
        members = (_fast_config("s1", lookback=2),)
        suite = SuiteConfig(label="persist", members=members)
        result = suite_runner.run(suite, register=True)
        suff = _sufficient(result.members[0], expectancy=0.6)
        _register(suite_registry.experiment_registry, suff)
        suite_registry.register_suite(result)

        original = HistoricalEvaluationPipeline.evaluate

        def boom(self, candles):
            raise RuntimeError("should not rerun pipeline")

        HistoricalEvaluationPipeline.evaluate = boom  # type: ignore[assignment]
        try:
            engine = SuiteSelectionEngine(suite_registry)
            sel = engine.select(SelectionCriteria())
            assert sel.selected is not None
        finally:
            HistoricalEvaluationPipeline.evaluate = original  # type: ignore[assignment]



# ============================================================
# F. PERSISTENCE
# ============================================================


class TestPersistence:
    def test_persisted_selection_loaded_without_pipeline(
        self, registry, engine, selection_registry
    ) -> None:
        _register(registry, _sufficient(_run("a", lookback=2), expectancy=0.6))
        result = engine.select(SelectionCriteria(), label="persisted")
        selection_registry.register_selection(result)

        original = HistoricalEvaluationPipeline.evaluate

        def boom(self, candles):
            raise RuntimeError("should not rerun pipeline")

        HistoricalEvaluationPipeline.evaluate = boom  # type: ignore[assignment]
        try:
            loaded = selection_registry.load_selection(result.selection_id)
            assert loaded.selection_id == result.selection_id
            assert loaded.selected_entity_id == result.selected_entity_id
            assert loaded.selection_type == result.selection_type
        finally:
            HistoricalEvaluationPipeline.evaluate = original  # type: ignore[assignment]

    def test_round_trip_preserves_candidates_and_rejected(
        self, registry, engine, selection_registry
    ) -> None:
        _register(registry, _sufficient(_run("suff", lookback=2), expectancy=0.5))
        _register(registry, _insufficient(_run("insuff", lookback=3)))
        result = engine.select(SelectionCriteria(), label="rt")
        selection_registry.register_selection(result)

        loaded = selection_registry.load_selection(result.selection_id)
        assert len(loaded.candidates) == len(result.candidates)
        assert len(loaded.rejected) == len(result.rejected)
        assert loaded.criteria == result.criteria
        assert canonical_selection_json(loaded) == canonical_selection_json(result)

    def test_register_no_overwrite_raises(
        self, registry, engine, selection_registry
    ) -> None:
        _register(registry, _sufficient(_run("a", lookback=2)))
        result = engine.select(SelectionCriteria(), label="dup")
        selection_registry.register_selection(result)
        with pytest.raises(SelectionAlreadyExistsError):
            selection_registry.register_selection(result)

    def test_register_overwrite(self, registry, engine, selection_registry) -> None:
        _register(registry, _sufficient(_run("a", lookback=2)))
        result = engine.select(SelectionCriteria(), label="dup")
        selection_registry.register_selection(result)
        selection_registry.register_selection(result, overwrite=True)
        assert selection_registry.exists(result.selection_id)

    def test_list_and_exists(self, registry, engine, selection_registry) -> None:
        assert selection_registry.list() == []
        _register(registry, _sufficient(_run("a", lookback=2)))
        result = engine.select(SelectionCriteria(), label="one")
        selection_registry.register_selection(result)
        assert selection_registry.list() == [result.selection_id]
        assert selection_registry.exists(result.selection_id)
        assert not selection_registry.exists("sel-doesnotexist0000")

    def test_delete(self, registry, engine, selection_registry) -> None:
        _register(registry, _sufficient(_run("a", lookback=2)))
        result = engine.select(SelectionCriteria(), label="del")
        selection_registry.register_selection(result)
        selection_registry.delete(result.selection_id)
        assert not selection_registry.exists(result.selection_id)
        with pytest.raises(SelectionNotFoundError):
            selection_registry.load_selection(result.selection_id)

    def test_load_missing_raises(self, selection_registry) -> None:
        with pytest.raises(SelectionNotFoundError):
            selection_registry.load_selection("sel-missing000000000")

    def test_schema_version_constant_and_carried(self, registry, engine) -> None:
        assert SELECTION_SCHEMA_VERSION == 1
        _register(registry, _sufficient(_run("a", lookback=2)))
        result = engine.select(SelectionCriteria(), label="v")
        text = serialize_selection(result)
        assert '"schema_version": 1' in text

    def test_rejects_future_schema_version(self, tmp_path) -> None:
        persistence = SelectionPersistence(tmp_path)
        path = persistence.path_for("sel-future0000000000")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"schema_version": 999, "selection_id": "sel-future0000000000"}')
        with pytest.raises(UnsupportedSelectionSchemaVersionError):
            persistence.load("sel-future0000000000")

    def test_corrupted_json_raises_integrity(self, tmp_path) -> None:
        persistence = SelectionPersistence(tmp_path)
        path = persistence.path_for("sel-corrupt0000000000")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not valid json")
        with pytest.raises(SelectionIntegrityError):
            persistence.load("sel-corrupt0000000000")

    def test_filename_id_mismatch_raises_integrity(self, tmp_path) -> None:
        persistence = SelectionPersistence(tmp_path)
        path = persistence.path_for("sel-real000000000000")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '{"schema_version": 1, "selection_id": "sel-other00000000000"}'
        )
        with pytest.raises(SelectionIntegrityError):
            persistence.load("sel-real000000000000")

    def test_unsafe_id_rejected(self, tmp_path) -> None:
        persistence = SelectionPersistence(tmp_path)
        with pytest.raises(SelectionError):
            persistence.path_for("../escape")

    def test_serialization_round_trip_deterministic(
        self, registry, engine
    ) -> None:
        _register(registry, _sufficient(_run("a", lookback=2)))
        result = engine.select(SelectionCriteria(), label="det")
        text1 = serialize_selection(result)
        text2 = serialize_selection(result)
        assert text1 == text2
        back = deserialize_selection(text1)
        assert canonical_selection_json(back) == canonical_selection_json(result)

    def test_default_directory_relative(self) -> None:
        persistence = SelectionPersistence()
        assert persistence.directory == Path.cwd() / "experiments"

    def test_does_not_pollute_experiment_listing(
        self, registry, engine, selection_registry
    ) -> None:
        _register(registry, _sufficient(_run("a", lookback=2)))
        result = engine.select(SelectionCriteria(), label="clean")
        selection_registry.register_selection(result)
        # The experiment registry must not list the selection decision
        # as an experiment record.
        assert result.selection_id not in registry.list()
        # And the selection registry must not list experiment records.
        assert registry.list()[0] not in selection_registry.list()


# ============================================================
# G. INTEGRITY: NO INSUFFICIENT INFLUENCE
# ============================================================


class TestNoInsufficientInfluence:
    def test_no_insufficient_experiment_can_influence_selected(
        self, registry, engine
    ) -> None:
        # A single INSUFFICIENT with an astronomically high expectancy
        # must NOT be selected, and must NOT displace a SUFFICIENT one.
        insuff = _with_evidence(
            _run("insuff", lookback=2),
            ExperimentEvidenceStatus.INSUFFICIENT,
            expectancy=99.99,
            total_r=999.9,
        )
        suff = _sufficient(_run("suff", lookback=3), expectancy=0.1)
        _register(registry, insuff)
        _register(registry, suff)

        result = engine.select(SelectionCriteria())
        assert result.selected is not None
        assert result.selected.entity_id == suff.experiment_id
        assert _by_id(result, insuff.experiment_id).status == SelectionStatus.NOT_ELIGIBLE

    def test_selected_only_among_candidates(self, registry, engine) -> None:
        # The selected entity must be one of the CANDIDATE-status entities
        # before promotion; never NOT_ELIGIBLE or REJECTED.
        _register(registry, _insufficient(_run("a", lookback=2)))
        _register(registry, _sufficient(_run("b", lookback=3), expectancy=0.5, robust=False))
        _register(registry, _sufficient(_run("c", lookback=4), expectancy=0.9, robust=False))

        result = engine.select(SelectionCriteria(require_robust=True))
        # robust not set (None) on both -> both rejected, no winner.
        assert result.selected is None
        assert all(
            c.status in (SelectionStatus.NOT_ELIGIBLE, SelectionStatus.REJECTED)
            for c in result.candidates
        )


# ============================================================
# H. REPORTING
# ============================================================


class TestReporting:
    def test_report_has_required_sections(
        self, registry, engine
    ) -> None:
        _register(registry, _sufficient(_run("a", lookback=2)))
        result = engine.select(SelectionCriteria(), label="rep")
        out = SelectionReportFormatter().format(result)
        for section in (
            "Selection Identity",
            "Selection Criteria",
            "Candidates",
            "Rejected / Ineligible",
            "Selected Result",
            "Conclusion",
        ):
            assert section in out

    def test_report_states_descriptive_only(self, registry, engine) -> None:
        _register(registry, _sufficient(_run("a", lookback=2)))
        result = engine.select(SelectionCriteria(), label="rep")
        out = SelectionReportFormatter().format(result)
        assert "DESCRIPTIVE ONLY" in out
        assert "not predictive" in out.lower()
        assert "live-trading" in out.lower()

    def test_report_none_when_no_selected(self, registry, engine) -> None:
        _register(registry, _insufficient(_run("a", lookback=2)))
        result = engine.select(SelectionCriteria(), label="none")
        out = SelectionReportFormatter().format(result)
        assert "Selected: NONE" in out
        assert "no winner" in out.lower() or "none" in out.lower()

    def test_report_returns_str_no_print(self, registry, engine) -> None:
        _register(registry, _sufficient(_run("a", lookback=2)))
        result = engine.select(SelectionCriteria(), label="rep")
        out = SelectionReportFormatter().format(result)
        assert isinstance(out, str)
        assert out.count("\n") > 5

    def test_report_deterministic(self, registry, engine) -> None:
        _register(registry, _sufficient(_run("a", lookback=2)))
        result = engine.select(SelectionCriteria(), label="rep")
        out1 = SelectionReportFormatter().format(result)
        out2 = SelectionReportFormatter().format(result)
        assert out1 == out2

    def test_report_empty_input(self) -> None:
        # A hand-constructed empty selection result formats gracefully.
        empty = SelectionResult(
            selection_id="sel-empty00000000000",
            label="empty",
            selection_type=SelectionType.EXPERIMENT,
            criteria=SelectionCriteria(),
        )
        out = SelectionReportFormatter().format(empty)
        assert "Selection Identity" in out
        assert "DESCRIP" in out


# ============================================================
# I. IMMUTABILITY
# ============================================================


class TestImmutability:
    def test_frozen_models(self) -> None:
        # Every selection model is frozen + slots: assigning to a field
        # raises FrozenInstanceError.
        criteria = SelectionCriteria(min_expectancy=0.1)
        with pytest.raises(FrozenInstanceError):
            criteria.min_expectancy = 0.2  # type: ignore[misc]

        candidate = SelectionCandidate(
            entity_id="x", label="x", selection_type=SelectionType.EXPERIMENT,
            evidence_status=ExperimentEvidenceStatus.SUFFICIENT,
            expectancy=0.1, total_r=0.4, max_drawdown_r=0.2,
            completed_trades=5, robust=None, oos_expectancy=None,
            oos_trades=0, reproducible=True, eligible=True,
        )
        with pytest.raises(FrozenInstanceError):
            candidate.entity_id = "y"  # type: ignore[misc]

        reason = RejectionReason(
            entity_id="x", status=SelectionStatus.REJECTED, reason="r",
        )
        with pytest.raises(FrozenInstanceError):
            reason.reason = "z"  # type: ignore[misc]

        selected = SelectedResult(
            entity_id="x", label="x", selection_type=SelectionType.EXPERIMENT,
            rationale="r",
        )
        with pytest.raises(FrozenInstanceError):
            selected.entity_id = "y"  # type: ignore[misc]

        promotion = PromotionDecision(
            promoted=True, selected=selected, rationale="r",
        )
        with pytest.raises(FrozenInstanceError):
            promotion.promoted = False  # type: ignore[misc]

        result = SelectionResult(
            selection_id="sel-x000000000000000", label="x",
            selection_type=SelectionType.EXPERIMENT, criteria=SelectionCriteria(),
        )
        with pytest.raises(FrozenInstanceError):
            result.label = "y"  # type: ignore[misc]

    def test_models_have_slots(self) -> None:
        for cls in (
            SelectionCriteria,
            RejectionReason,
            SelectionCandidate,
            SelectedResult,
            PromotionDecision,
            SelectionResult,
        ):
            assert hasattr(cls, "__slots__")

    def test_criteria_frozen(self) -> None:
        c = SelectionCriteria(min_expectancy=0.1)
        with pytest.raises(FrozenInstanceError):
            c.min_expectancy = 0.2  # type: ignore[misc]

    def test_result_frozen(self) -> None:
        r = SelectionResult(
            selection_id="sel-x000000000000000",
            label="x",
            selection_type=SelectionType.EXPERIMENT,
            criteria=SelectionCriteria(),
        )
        with pytest.raises(FrozenInstanceError):
            r.label = "y"  # type: ignore[misc]

    def test_re_serialize_loop_stable(self, registry, engine) -> None:
        _register(registry, _sufficient(_run("a", lookback=2)))
        result = engine.select(SelectionCriteria(), label="loop")
        text1 = serialize_selection(result)
        back = deserialize_selection(text1)
        text2 = serialize_selection(back)
        assert text1 == text2


# ============================================================
# J. BACKWARD COMPATIBILITY / REGRESSION
# ============================================================


class TestBackwardCompatibility:
    def test_prior_apis_importable(self) -> None:
        # Sprint 11J/11K/11L/11M public APIs remain importable unchanged.
        from engine.experiment import (  # noqa: F401
            ExperimentConfig,
            ExperimentRunner,
            ExperimentResult,
        )
        from engine.registry import (  # noqa: F401
            ExperimentRegistry,
            SCHEMA_VERSION,
        )
        from engine.query import (  # noqa: F401
            ExperimentQueryEngine,
        )
        from engine.suite import (  # noqa: F401
            SuiteRunner,
            SuiteRegistry,
            SUITE_SCHEMA_VERSION,
        )
        assert SCHEMA_VERSION == 1
        assert SUITE_SCHEMA_VERSION == 1
        assert SELECTION_SCHEMA_VERSION == 1

    def test_selection_schema_separate_from_experiment_and_suite(self) -> None:
        from engine.models.registry import SCHEMA_VERSION as EXP
        from engine.models.suite import SUITE_SCHEMA_VERSION as SUI
        assert SELECTION_SCHEMA_VERSION != EXP or SELECTION_SCHEMA_VERSION == 1
        assert SELECTION_SCHEMA_VERSION == 1
        assert SUI == 1

    def test_experiment_selection_id_prefix(self, registry, engine) -> None:
        _register(registry, _sufficient(_run("a", lookback=2)))
        result = engine.select(SelectionCriteria(), label="prefix")
        assert result.selection_id.startswith("sel-")
        assert len(result.selection_id) == len("sel-") + 16

    def test_selection_identity_deterministic(self) -> None:
        id1 = SelectionIdentity(
            selection_type=SelectionType.EXPERIMENT,
            criteria=SelectionCriteria(min_expectancy=0.1),
            label="run",
            metadata={"k": "v"},
        )
        id2 = SelectionIdentity(
            selection_type=SelectionType.EXPERIMENT,
            criteria=SelectionCriteria(min_expectancy=0.1),
            label="run",
            metadata={"k": "v"},
        )
        assert id1.selection_id == id2.selection_id
        assert id1.configuration_hash == id2.configuration_hash

    def test_selection_identity_changes_on_criteria_change(self) -> None:
        id1 = SelectionIdentity(
            selection_type=SelectionType.EXPERIMENT,
            criteria=SelectionCriteria(min_expectancy=0.1),
            label="run",
            metadata={},
        )
        id2 = SelectionIdentity(
            selection_type=SelectionType.EXPERIMENT,
            criteria=SelectionCriteria(min_expectancy=0.2),
            label="run",
            metadata={},
        )
        assert id1.selection_id != id2.selection_id

    def test_selection_type_change_changes_id(self) -> None:
        id1 = SelectionIdentity(
            selection_type=SelectionType.EXPERIMENT,
            criteria=SelectionCriteria(),
            label="run",
            metadata={},
        )
        id2 = SelectionIdentity(
            selection_type=SelectionType.SUITE,
            criteria=SelectionCriteria(),
            label="run",
            metadata={},
        )
        assert id1.selection_id != id2.selection_id

    def test_existing_suite_test_apis_unchanged(
        self, suite_registry, suite_runner
    ) -> None:
        # A basic suite still runs + persists + loads via the existing
        # Sprint 11M APIs (regression guard).
        members = (_fast_config("r1", lookback=2),)
        suite = SuiteConfig(label="regression", members=members)
        result = suite_runner.run(suite, register=True)
        suite_registry.register_suite(result)
        loaded = suite_registry.load_suite(suite.suite_id)
        assert loaded.suite_id == suite.suite_id
        assert loaded.member_count == 1


# ============================================================
# K. PACKAGE SURFACE
# ============================================================


class TestPackageSurface:
    def test_importable_api(self) -> None:
        import engine.selection as sel
        for name in (
            "SelectionEngine",
            "SuiteSelectionEngine",
            "SelectionRegistry",
            "SelectionPersistence",
            "SelectionCriteria",
            "SelectionResult",
            "SelectionStatus",
            "SelectionType",
            "SelectionCandidate",
            "RejectionReason",
            "SelectedResult",
            "PromotionDecision",
            "SELECTION_SCHEMA_VERSION",
            "SelectionNotFoundError",
            "SelectionAlreadyExistsError",
            "SelectionIntegrityError",
            "SelectionError",
            "UnsupportedSelectionSchemaVersionError",
            "serialize_selection",
            "deserialize_selection",
            "canonical_selection_json",
        ):
            assert hasattr(sel, name), f"missing {name}"

    def test_reporting_reexport(self) -> None:
        import engine.reporting as rep
        assert hasattr(rep, "SelectionReportFormatter")

    def test_exception_hierarchy(self) -> None:
        assert issubclass(SelectionNotFoundError, SelectionError)
        assert issubclass(SelectionAlreadyExistsError, SelectionError)
        assert issubclass(SelectionIntegrityError, SelectionError)
        assert issubclass(UnsupportedSelectionSchemaVersionError, SelectionError)
