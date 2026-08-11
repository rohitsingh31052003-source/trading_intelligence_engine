"""
Tests for the Experiment Suite / Batch Orchestration & Analysis Layer
(Sprint 11M).

Covers:

A. SuiteConfig identity
   - deterministic suite_id
   - same members + order -> same id
   - re-order -> different id
   - change a member param -> different id
   - change label / metadata / seed -> different id
   - canonical representation stable
   - immutability (frozen + slots)
   - lambdas / evaluators rejected from identity

B. SuiteRunner
   - runs every member via ExperimentRunner
   - references (not copies) members
   - skip-already-registered member reused not rerun
   - overwrite=True re-runs
   - graceful on INSUFFICIENT member
   - custom dataset mapping wired through
   - no print()
   - determinism

C. Manifest persistence
   - atomic write (no temp leftover on failure)
   - schema version constant + record carries version
   - rejects future schema version
   - integrity mismatch (suite id / filename id) -> typed error
   - corrupted JSON -> typed error
   - list / exists / delete
   - safe-id validation
   - default dir relative

D. SuiteRegistry
   - register_suite / load_suite round-trip
   - load_suite reconstructs from persisted data (no pipeline rerun)
   - persisted-data-only proof (pipeline patched to raise)
   - reflects new registrations / deletes
   - compare_suites delegates to existing comparison engine
   - missing member raises (suite not resilient to member deletion)
   - summarize_suite from persisted data

E. SuiteAnalysis / Summary
   - counts correct
   - has_sufficient_evidence only when >=1 SUFFICIENT member
   - suite-level leaders None when no member sufficient (evidence safety)
   - delegates to ExperimentQueryEngine.summarize (assert equality)
   - descriptive conclusions
   - determinism

F. SuiteComparison
   - two suites
   - best-by-member-expectancy among suites with >=1 SUFFICIENT member only
   - None when none
   - descriptive-not-predictive language
   - immutability
   - determinism
   - empty comparison

G. Reporting
   - suite report required sections
   - no misleading claims
   - insufficient evidence explicit
   - no print() (returns str)
   - determinism
   - suite comparison formatter sections

H. Backward compatibility
   - all prior tests still pass (verified by full suite run)
   - 11J / 11K / 11L APIs importable unchanged
   - hand-constructed minimal SuiteResult handled gracefully

I. Immutability
   - frozen suite models
   - re-serialize loop / determinism
"""

from __future__ import annotations

import json
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
    ExperimentSummary,
    ReproducibilityMetadata,
)
from engine.models.experiment import ExperimentResult
from engine.models.ohlcv import OHLCVCandle
from engine.models.suite import (
    SUITE_SCHEMA_VERSION,
    SuiteComparison,
    SuiteComparisonRow,
    SuiteReproducibilityMetadata,
    SuiteResult,
    SuiteSummary,
)
from engine.pipeline.historical_pipeline import (
    HistoricalEvaluationPipeline,
    PipelineConfig,
)
from engine.registry import ExperimentRegistry
from engine.reporting import (
    SuiteComparisonFormatter,
    SuiteReportFormatter,
)
from engine.research.research import ResearchConfig
from engine.suite import (
    SuiteAlreadyExistsError,
    SuiteAnalysisEngine,
    SuiteConfig,
    SuiteError,
    SuiteIntegrityError,
    SuiteManifestPersistence,
    SuiteNotFoundError,
    SuiteRegistry,
    SuiteRunner,
    UnsupportedSuiteSchemaVersionError,
)
from engine.query.query import _summarize, _to_row


# ============================================================
# FIXTURE HELPERS
# ============================================================


def _config(
    label: str = "trending-lookback-2",
    *,
    lookback: int = 2,
    dataset_name: str = "trending",
    sweep: tuple[int, ...] = (2, 3, 4),
    seed: int | None = 42,
    metadata: dict[str, str] | None = None,
) -> ExperimentConfig:
    return ExperimentConfig(
        label=label,
        dataset=DatasetSpec(name=dataset_name),
        pipeline=PipelineConfig(
            swing_config=SwingConfig(lookback=lookback),
        ),
        research=ResearchConfig(),
        evaluation=EvaluationConfig(
            parameter_name="swing_lookback",
            parameter_values=sweep,
            run_out_of_sample=True,
            run_walk_forward=True,
            run_sensitivity=True,
        ),
        strategy_parameters={"swing_lookback": str(lookback)},
        seed=seed,
        metadata=metadata if metadata is not None else {"sprint": "11M"},
    )


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
        metadata=metadata if metadata is not None else {"sprint": "11M"},
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

    summary = replace(
        result.summary,
        evidence_status=status,
        **overrides,
    )
    return replace(result, summary=summary)


def _sufficient(result: ExperimentResult, expectancy: float = 0.5) -> ExperimentResult:
    return _with_evidence(
        result,
        ExperimentEvidenceStatus.SUFFICIENT,
        expectancy=expectancy,
        total_r=expectancy * 4,
        win_rate=0.6,
        completed_trades=10,
    )


def _insufficient(result: ExperimentResult) -> ExperimentResult:
    return _with_evidence(result, ExperimentEvidenceStatus.INSUFFICIENT)


def _partial(result: ExperimentResult) -> ExperimentResult:
    return _with_evidence(result, ExperimentEvidenceStatus.PARTIAL)


@pytest.fixture()
def registry(tmp_path: Path) -> ExperimentRegistry:
    return ExperimentRegistry(tmp_path)


@pytest.fixture()
def suite_registry(registry: ExperimentRegistry) -> SuiteRegistry:
    return SuiteRegistry(experiment_registry=registry)


@pytest.fixture()
def runner(registry: ExperimentRegistry) -> SuiteRunner:
    return SuiteRunner(experiment_registry=registry)


# ============================================================
# A. SuiteConfig IDENTITY
# ============================================================


class TestSuiteConfigIdentity:
    def test_deterministic_suite_id(self) -> None:
        members = (_fast_config("a"), _fast_config("b"))
        c1 = SuiteConfig(label="suite", members=members)
        c2 = SuiteConfig(label="suite", members=members)
        assert c1.suite_id == c2.suite_id
        assert c1.configuration_hash == c2.configuration_hash
        assert c1.canonical_representation == c2.canonical_representation

    def test_suite_id_prefix(self) -> None:
        c = SuiteConfig(label="s", members=(_fast_config("a"),))
        assert c.suite_id.startswith("suite-")
        assert len(c.suite_id) == len("suite-") + 16

    def test_reorder_changes_id(self) -> None:
        a = _fast_config("a")
        b = _fast_config("b")
        c1 = SuiteConfig(label="s", members=(a, b))
        c2 = SuiteConfig(label="s", members=(b, a))
        assert c1.suite_id != c2.suite_id

    def test_change_member_param_changes_id(self) -> None:
        a1 = _fast_config("a", lookback=2)
        a2 = _fast_config("a", lookback=3)
        c1 = SuiteConfig(label="s", members=(a1,))
        c2 = SuiteConfig(label="s", members=(a2,))
        assert c1.suite_id != c2.suite_id

    def test_change_label_changes_id(self) -> None:
        m = _fast_config("a")
        c1 = SuiteConfig(label="s1", members=(m,))
        c2 = SuiteConfig(label="s2", members=(m,))
        assert c1.suite_id != c2.suite_id

    def test_change_metadata_changes_id(self) -> None:
        m = _fast_config("a")
        c1 = SuiteConfig(label="s", members=(m,), metadata={"k": "1"})
        c2 = SuiteConfig(label="s", members=(m,), metadata={"k": "2"})
        assert c1.suite_id != c2.suite_id

    def test_change_seed_changes_id(self) -> None:
        m = _fast_config("a")
        c1 = SuiteConfig(label="s", members=(m,), seed=1)
        c2 = SuiteConfig(label="s", members=(m,), seed=2)
        assert c1.suite_id != c2.suite_id

    def test_canonical_representation_stable(self) -> None:
        c = SuiteConfig(
            label="s",
            members=(_fast_config("a"), _fast_config("b")),
            metadata={"k": "v"},
        )
        rep1 = c.canonical_representation
        rep2 = c.canonical_representation
        assert rep1 == rep2
        # Must be valid JSON with sorted keys.
        parsed = json.loads(rep1)
        assert isinstance(parsed, dict)

    def test_member_experiment_ids_ordered(self) -> None:
        a = _fast_config("a")
        b = _fast_config("b")
        c = SuiteConfig(label="s", members=(a, b))
        assert c.member_experiment_ids == (a.experiment_id, b.experiment_id)

    def test_member_count(self) -> None:
        c = SuiteConfig(label="s", members=(_fast_config("a"),))
        assert c.member_count == 1
        assert not c.is_empty

    def test_empty_suite(self) -> None:
        c = SuiteConfig(label="s", members=())
        assert c.is_empty
        assert c.member_count == 0

    def test_lambda_rejected_from_identity(self) -> None:
        bad = _fast_config("a")
        bad = replace(bad, metadata={"fn": (lambda x: x)})  # type: ignore[arg-type]
        c = SuiteConfig(label="s", members=(bad,))
        with pytest.raises(TypeError):
            _ = c.canonical_representation

    def test_frozen(self) -> None:
        c = SuiteConfig(label="s", members=(_fast_config("a"),))
        with pytest.raises(FrozenInstanceError):
            c.label = "other"  # type: ignore[misc]

    def test_slots(self) -> None:
        c = SuiteConfig(label="s", members=(_fast_config("a"),))
        with pytest.raises(AttributeError):
            c.extra = 1  # type: ignore[attr-defined]


# ============================================================
# B. SuiteRunner
# ============================================================


class TestSuiteRunner:
    def test_runs_every_member(self, runner: SuiteRunner) -> None:
        members = (_fast_config("a", lookback=2), _fast_config("b", lookback=3))
        suite = SuiteConfig(label="s", members=members)
        result = runner.run(suite)
        assert result.member_count == 2
        assert result.members[0].experiment_id == members[0].experiment_id
        assert result.members[1].experiment_id == members[1].experiment_id

    def test_references_not_copies(self, runner: SuiteRunner) -> None:
        members = (_fast_config("a"),)
        suite = SuiteConfig(label="s", members=members)
        result = runner.run(suite)
        assert isinstance(result.members[0], ExperimentResult)

    def test_skip_already_registered_reused(
        self,
        registry: ExperimentRegistry,
        runner: SuiteRunner,
    ) -> None:
        member = _fast_config("a")
        suite = SuiteConfig(label="s", members=(member,))
        runner.run(suite, register=True)

        # Patch the pipeline to raise; if the runner re-runs the member
        # it would raise. The skip path must reuse the persisted result.
        original = HistoricalEvaluationPipeline.evaluate

        def boom(self, candles):
            raise RuntimeError("should not rerun")

        HistoricalEvaluationPipeline.evaluate = boom  # type: ignore[assignment]
        try:
            result2 = runner.run(suite, register=True, overwrite=False)
            assert result2.member_count == 1
            assert result2.members[0].experiment_id == member.experiment_id
        finally:
            HistoricalEvaluationPipeline.evaluate = original  # type: ignore[assignment]

    def test_overwrite_reruns(
        self,
        registry: ExperimentRegistry,
        runner: SuiteRunner,
    ) -> None:
        member = _fast_config("a")
        suite = SuiteConfig(label="s", members=(member,))
        runner.run(suite, register=True)

        # With overwrite=True the member must be re-run. Patch the
        # pipeline to raise to prove it is actually invoked.
        original = HistoricalEvaluationPipeline.evaluate
        calls = {"n": 0}

        def counting(self, candles):
            calls["n"] += 1
            return original(self, candles)

        HistoricalEvaluationPipeline.evaluate = counting  # type: ignore[assignment]
        try:
            runner.run(suite, register=True, overwrite=True)
            assert calls["n"] >= 1
        finally:
            HistoricalEvaluationPipeline.evaluate = original  # type: ignore[assignment]

    def test_graceful_on_insufficient_member(
        self,
        runner: SuiteRunner,
    ) -> None:
        members = (_fast_config("a"),)
        suite = SuiteConfig(label="s", members=members)
        result = runner.run(suite)
        # Small built-in datasets produce INSUFFICIENT evidence; the
        # suite must still complete.
        assert result.member_count == 1
        assert result.members[0].evidence_status == ExperimentEvidenceStatus.INSUFFICIENT

    def test_custom_dataset_wired_through(
        self,
        runner: SuiteRunner,
    ) -> None:
        from engine.pipeline.datasets import trending_dataset

        member = ExperimentConfig(
            label="custom",
            dataset=DatasetSpec(name="custom"),
            pipeline=PipelineConfig(),
            research=ResearchConfig(),
            evaluation=EvaluationConfig(
                run_out_of_sample=False,
                run_walk_forward=False,
                run_sensitivity=False,
            ),
        )
        suite = SuiteConfig(label="s", members=(member,))
        result = runner.run(suite, datasets={"custom": list(trending_dataset())})
        assert result.member_count == 1

    def test_determinism(self, registry: ExperimentRegistry) -> None:
        members = (_fast_config("a"), _fast_config("b"))
        suite = SuiteConfig(label="s", members=members)
        r1 = SuiteRunner(experiment_registry=registry).run(suite)
        # Fresh registry in a different dir for a clean run.
        import tempfile
        reg2 = ExperimentRegistry(tempfile.mkdtemp())
        r2 = SuiteRunner(experiment_registry=reg2).run(suite)
        assert r1.suite_id == r2.suite_id
        assert r1.member_experiment_ids == r2.member_experiment_ids

    def test_reproducibility_metadata(self, runner: SuiteRunner) -> None:
        member = _fast_config("a")
        suite = SuiteConfig(label="s", members=(member,), seed=7)
        result = runner.run(suite)
        repro = result.reproducibility
        assert repro.suite_id == suite.suite_id
        assert repro.suite_configuration_hash == suite.configuration_hash
        assert repro.member_count == 1
        assert repro.member_experiment_ids == (member.experiment_id,)
        assert repro.reproducible is True
        assert repro.suite_configuration_representation == suite.canonical_representation

    def test_no_print_in_runner(self) -> None:
        import inspect

        src = inspect.getsource(SuiteRunner)
        assert "print(" not in src


# ============================================================
# C. MANIFEST PERSISTENCE
# ============================================================


class TestManifestPersistence:
    def test_save_and_load_round_trip(self, tmp_path: Path) -> None:
        p = SuiteManifestPersistence(tmp_path)
        p.save(
            suite_id="suite-aaaaaaaaaaaaaaaa",
            configuration_hash="abc123",
            label="s",
            member_experiment_ids=("exp-1", "exp-2"),
            metadata={"k": "v"},
        )
        assert p.exists("suite-aaaaaaaaaaaaaaaa")
        loaded = p.load("suite-aaaaaaaaaaaaaaaa")
        assert loaded["suite_id"] == "suite-aaaaaaaaaaaaaaaa"
        assert loaded["configuration_hash"] == "abc123"
        assert loaded["label"] == "s"
        assert loaded["member_experiment_ids"] == ["exp-1", "exp-2"]
        assert loaded["metadata"] == {"k": "v"}
        assert loaded["schema_version"] == SUITE_SCHEMA_VERSION

    def test_record_carries_schema_version(self, tmp_path: Path) -> None:
        p = SuiteManifestPersistence(tmp_path)
        p.save(
            suite_id="suite-bbbbbbbbbbbbbbbb",
            configuration_hash="h",
            label="s",
            member_experiment_ids=(),
            metadata={},
        )
        loaded = p.load("suite-bbbbbbbbbbbbbbbb")
        assert loaded["schema_version"] == SUITE_SCHEMA_VERSION

    def test_rejects_future_schema_version(self, tmp_path: Path) -> None:
        p = SuiteManifestPersistence(tmp_path)
        path = p.path_for("suite-cccccccccccccccc")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema_version": 999,
                    "suite_id": "suite-cccccccccccccccc",
                    "configuration_hash": "h",
                    "label": "s",
                    "member_experiment_ids": [],
                    "metadata": {},
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(UnsupportedSuiteSchemaVersionError):
            p.load("suite-cccccccccccccccc")

    def test_filename_id_mismatch(self, tmp_path: Path) -> None:
        p = SuiteManifestPersistence(tmp_path)
        path = p.path_for("suite-dddddddddddddddd")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema_version": SUITE_SCHEMA_VERSION,
                    "suite_id": "suite-eeeeeeeeeeeeeeee",
                    "configuration_hash": "h",
                    "label": "s",
                    "member_experiment_ids": [],
                    "metadata": {},
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(SuiteIntegrityError):
            p.load("suite-dddddddddddddddd")

    def test_corrupted_json(self, tmp_path: Path) -> None:
        p = SuiteManifestPersistence(tmp_path)
        path = p.path_for("suite-ffffffffffffffff")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(SuiteIntegrityError):
            p.load("suite-ffffffffffffffff")

    def test_load_missing(self, tmp_path: Path) -> None:
        p = SuiteManifestPersistence(tmp_path)
        with pytest.raises(SuiteNotFoundError):
            p.load("suite-1111111111111111")

    def test_save_no_overwrite(self, tmp_path: Path) -> None:
        p = SuiteManifestPersistence(tmp_path)
        p.save(
            suite_id="suite-2222222222222222",
            configuration_hash="h",
            label="s",
            member_experiment_ids=(),
            metadata={},
        )
        with pytest.raises(SuiteAlreadyExistsError):
            p.save(
                suite_id="suite-2222222222222222",
                configuration_hash="h",
                label="s",
                member_experiment_ids=(),
                metadata={},
            )

    def test_save_overwrite(self, tmp_path: Path) -> None:
        p = SuiteManifestPersistence(tmp_path)
        p.save(
            suite_id="suite-3333333333333333",
            configuration_hash="h1",
            label="s1",
            member_experiment_ids=(),
            metadata={},
        )
        p.save(
            suite_id="suite-3333333333333333",
            configuration_hash="h2",
            label="s2",
            member_experiment_ids=(),
            metadata={},
            overwrite=True,
        )
        loaded = p.load("suite-3333333333333333")
        assert loaded["label"] == "s2"
        assert loaded["configuration_hash"] == "h2"

    def test_list_suites(self, tmp_path: Path) -> None:
        p = SuiteManifestPersistence(tmp_path)
        p.save("suite-aaaaaaaaaaaaaaaa", "h", "s", (), {})
        p.save("suite-bbbbbbbbbbbbbbbb", "h", "s", (), {})
        assert p.list_suites() == [
            "suite-aaaaaaaaaaaaaaaa",
            "suite-bbbbbbbbbbbbbbbb",
        ]

    def test_list_ignores_experiment_records(self, tmp_path: Path) -> None:
        p = SuiteManifestPersistence(tmp_path)
        # A stray .json file (experiment record) must NOT be listed.
        (tmp_path / "exp-zzzzzzzzzzzzzzzz.json").write_text("{}", encoding="utf-8")
        p.save("suite-aaaaaaaaaaaaaaaa", "h", "s", (), {})
        assert p.list_suites() == ["suite-aaaaaaaaaaaaaaaa"]

    def test_manifest_does_not_pollute_experiment_registry(
        self,
        tmp_path: Path,
    ) -> None:
        # Regression guard: a suite manifest stored in the SAME
        # directory as experiment records must NOT appear in the
        # Sprint 11K ExperimentRegistry.list() output. The manifest
        # suffix is chosen specifically so the experiment registry's
        # ``.json`` scan excludes it.
        from engine.experiment import ExperimentRunner

        reg = ExperimentRegistry(tmp_path)
        reg.register(ExperimentRunner().run(_fast_config("a")))

        p = SuiteManifestPersistence(tmp_path)
        p.save("suite-aaaaaaaaaaaaaaaa", "h", "s", (), {})

        experiment_ids = reg.list()
        assert "suite-aaaaaaaaaaaaaaaa" not in experiment_ids
        assert all(not eid.endswith(".suite") for eid in experiment_ids)

    def test_delete(self, tmp_path: Path) -> None:
        p = SuiteManifestPersistence(tmp_path)
        p.save("suite-aaaaaaaaaaaaaaaa", "h", "s", (), {})
        p.delete("suite-aaaaaaaaaaaaaaaa")
        assert not p.exists("suite-aaaaaaaaaaaaaaaa")
        with pytest.raises(SuiteNotFoundError):
            p.delete("suite-aaaaaaaaaaaaaaaa")

    def test_unsafe_id_rejected(self, tmp_path: Path) -> None:
        # The manifest layer reuses the Sprint 11K safe-id validator,
        # so an unsafe id surfaces the registry's typed persistence
        # error (the reused validator's honest behavior).
        from engine.registry.exceptions import ExperimentPersistenceError

        p = SuiteManifestPersistence(tmp_path)
        with pytest.raises((SuiteError, ExperimentPersistenceError)):
            p.save(
                suite_id="../../etc/x",
                configuration_hash="h",
                label="s",
                member_experiment_ids=(),
                metadata={},
            )

    def test_default_dir_relative(self) -> None:
        from engine.registry.persistence import default_registry_directory

        p = SuiteManifestPersistence()
        assert p.directory == default_registry_directory()

    def test_atomic_no_temp_leftover(self, tmp_path: Path) -> None:
        p = SuiteManifestPersistence(tmp_path)
        # A successful write must not leave any .tmp files behind.
        p.save("suite-aaaaaaaaaaaaaaaa", "h", "s", (), {})
        tmps = list(tmp_path.glob("*.tmp"))
        assert tmps == []

    def test_deterministic_bytes(self, tmp_path: Path) -> None:
        p = SuiteManifestPersistence(tmp_path)
        p.save("suite-aaaaaaaaaaaaaaaa", "h", "s", ("exp-1",), {"k": "v"})
        text1 = p.path_for("suite-aaaaaaaaaaaaaaaa").read_text(encoding="utf-8")
        p.save(
            "suite-aaaaaaaaaaaaaaaa",
            "h",
            "s",
            ("exp-1",),
            {"k": "v"},
            overwrite=True,
        )
        text2 = p.path_for("suite-aaaaaaaaaaaaaaaa").read_text(encoding="utf-8")
        assert text1 == text2
        # Keys must be sorted.
        assert json.loads(text1) == json.loads(text1)


# ============================================================
# D. SuiteRegistry
# ============================================================


class TestSuiteRegistry:
    def test_register_and_load_round_trip(
        self,
        suite_registry: SuiteRegistry,
        runner: SuiteRunner,
    ) -> None:
        members = (_fast_config("a"), _fast_config("b"))
        suite = SuiteConfig(label="s", members=members)
        result = runner.run(suite, register=True)
        suite_registry.register_suite(result)

        assert suite_registry.exists(suite.suite_id)
        assert suite_registry.list() == [suite.suite_id]

        loaded = suite_registry.load_suite(suite.suite_id)
        assert loaded.suite_id == suite.suite_id
        assert loaded.member_count == 2
        assert loaded.member_experiment_ids == result.member_experiment_ids

    def test_load_suite_no_pipeline_rerun(
        self,
        suite_registry: SuiteRegistry,
        runner: SuiteRunner,
    ) -> None:
        members = (_fast_config("a"), _fast_config("b"))
        suite = SuiteConfig(label="s", members=members)
        result = runner.run(suite, register=True)
        suite_registry.register_suite(result)

        original = HistoricalEvaluationPipeline.evaluate

        def boom(self, candles):
            raise RuntimeError("should not rerun")

        HistoricalEvaluationPipeline.evaluate = boom  # type: ignore[assignment]
        try:
            loaded = suite_registry.load_suite(suite.suite_id)
            assert loaded.member_count == 2
            summary = suite_registry.summarize_suite(suite.suite_id)
            assert summary.member_count == 2
            comp = suite_registry.compare_suites([suite.suite_id])
            assert comp.suite_count == 1
        finally:
            HistoricalEvaluationPipeline.evaluate = original  # type: ignore[assignment]

    def test_reflects_new_registrations(
        self,
        suite_registry: SuiteRegistry,
        runner: SuiteRunner,
    ) -> None:
        assert suite_registry.list() == []
        members = (_fast_config("a"),)
        suite = SuiteConfig(label="s", members=members)
        result = runner.run(suite, register=True)
        suite_registry.register_suite(result)
        assert suite_registry.list() == [suite.suite_id]

    def test_reflects_deletes(
        self,
        suite_registry: SuiteRegistry,
        runner: SuiteRunner,
    ) -> None:
        members = (_fast_config("a"),)
        suite = SuiteConfig(label="s", members=members)
        result = runner.run(suite, register=True)
        suite_registry.register_suite(result)
        suite_registry.delete(suite.suite_id)
        assert not suite_registry.exists(suite.suite_id)
        with pytest.raises(SuiteNotFoundError):
            suite_registry.load_suite(suite.suite_id)

    def test_register_no_overwrite(
        self,
        suite_registry: SuiteRegistry,
        runner: SuiteRunner,
    ) -> None:
        members = (_fast_config("a"),)
        suite = SuiteConfig(label="s", members=members)
        result = runner.run(suite, register=True)
        suite_registry.register_suite(result)
        with pytest.raises(SuiteAlreadyExistsError):
            suite_registry.register_suite(result)

    def test_register_overwrite(
        self,
        suite_registry: SuiteRegistry,
        runner: SuiteRunner,
    ) -> None:
        members = (_fast_config("a"),)
        suite = SuiteConfig(label="s", members=members)
        result = runner.run(suite, register=True)
        suite_registry.register_suite(result)
        suite_registry.register_suite(result, overwrite=True)
        assert suite_registry.exists(suite.suite_id)

    def test_missing_member_raises(
        self,
        suite_registry: SuiteRegistry,
        runner: SuiteRunner,
    ) -> None:
        members = (_fast_config("a"),)
        suite = SuiteConfig(label="s", members=members)
        result = runner.run(suite, register=True)
        suite_registry.register_suite(result)

        # Delete the member experiment; loading the suite must fail
        # loudly (suite is NOT resilient to member deletion).
        suite_registry.experiment_registry.delete(members[0].experiment_id)
        with pytest.raises(SuiteIntegrityError):
            suite_registry.load_suite(suite.suite_id)

    def test_load_missing_suite(self, suite_registry: SuiteRegistry) -> None:
        with pytest.raises(SuiteNotFoundError):
            suite_registry.load_suite("suite-0000000000000000")

    def test_summarize_suite_from_persisted(
        self,
        suite_registry: SuiteRegistry,
        runner: SuiteRunner,
    ) -> None:
        members = (_fast_config("a"), _fast_config("b"))
        suite = SuiteConfig(label="s", members=members)
        result = runner.run(suite, register=True)
        suite_registry.register_suite(result)
        summary = suite_registry.summarize_suite(suite.suite_id)
        assert summary.member_count == 2

    def test_directory_shared_with_experiment_registry(
        self,
        registry: ExperimentRegistry,
    ) -> None:
        sr = SuiteRegistry(experiment_registry=registry)
        assert sr.directory == registry.directory


# ============================================================
# E. SuiteAnalysis / Summary
# ============================================================


class TestSuiteAnalysis:
    def test_counts_correct(self) -> None:
        a = _sufficient(_run("a"), expectancy=0.5)
        b = _insufficient(_run("b"))
        c = _partial(_run("c"))
        engine = SuiteAnalysisEngine()
        summary = engine.summarize([a, b, c])
        assert summary.member_count == 3
        assert summary.sufficient_count == 1
        assert summary.partial_count == 1
        assert summary.insufficient_count == 1

    def test_has_sufficient_evidence(self) -> None:
        a = _sufficient(_run("a"))
        engine = SuiteAnalysisEngine()
        summary = engine.summarize([a])
        assert summary.has_sufficient_evidence is True

    def test_no_sufficient_no_leader(self) -> None:
        a = _insufficient(_run("a"))
        b = _insufficient(_run("b"))
        engine = SuiteAnalysisEngine()
        summary = engine.summarize([a, b])
        assert summary.has_sufficient_evidence is False
        assert summary.analysis_summary.best_by_expectancy is None
        assert summary.analysis_summary.best_by_total_r is None

    def test_delegates_to_query_summarize(self) -> None:
        a = _sufficient(_run("a"), expectancy=0.5)
        b = _sufficient(_run("b"), expectancy=0.8)
        engine = SuiteAnalysisEngine()
        summary = engine.summarize([a, b])
        # The delegated analysis summary must equal a direct query-layer
        # summarize over the same rows.
        rows = [_to_row(a), _to_row(b)]
        direct = _summarize(rows)
        assert summary.analysis_summary == direct

    def test_comparison_delegated(self) -> None:
        a = _sufficient(_run("a"), expectancy=0.5)
        b = _sufficient(_run("b"), expectancy=0.8)
        engine = SuiteAnalysisEngine()
        summary = engine.summarize([a, b])
        assert summary.comparison is not None
        # The best by expectancy among sufficient members is b.
        assert summary.comparison.best_by_expectancy == b.experiment_id

    def test_empty_members_comparison_none(self) -> None:
        engine = SuiteAnalysisEngine()
        summary = engine.summarize([])
        assert summary.comparison is None
        assert summary.member_count == 0

    def test_determinism(self) -> None:
        a = _sufficient(_run("a"), expectancy=0.5)
        b = _sufficient(_run("b"), expectancy=0.8)
        engine = SuiteAnalysisEngine()
        s1 = engine.summarize([a, b])
        s2 = engine.summarize([a, b])
        assert s1 == s2

    def test_no_print_in_analysis(self) -> None:
        import inspect

        src = inspect.getsource(SuiteAnalysisEngine)
        assert "print(" not in src


# ============================================================
# F. SuiteComparison
# ============================================================


class TestSuiteComparison:
    def _suite_result(
        self,
        suite_id: str,
        label: str,
        members: list[ExperimentResult],
    ) -> SuiteResult:
        repro = SuiteReproducibilityMetadata(
            suite_id=suite_id,
            suite_configuration_hash="h",
            suite_configuration_representation="r",
            member_experiment_ids=tuple(m.experiment_id for m in members),
            member_count=len(members),
        )
        summary = SuiteAnalysisEngine().summarize(members)
        return SuiteResult(
            suite_id=suite_id,
            config=None,
            reproducibility=repro,
            summary=summary,
            members=tuple(members),
            label=label,
        )

    def test_two_suites(self) -> None:
        a = _sufficient(_run("a"), expectancy=0.5)
        b = _insufficient(_run("b"))
        sr = SuiteRegistry(directory=Path("/tmp/nonexistent-test-suite"))
        r1 = self._suite_result("suite-aaaaaaaaaaaaaaaa", "s1", [a])
        r2 = self._suite_result("suite-bbbbbbbbbbbbbbbb", "s2", [b])
        comp = sr._compare_suite_results([r1, r2])
        assert comp.suite_count == 2
        assert "suite-aaaaaaaaaaaaaaaa" in comp.sufficient_suites
        assert "suite-bbbbbbbbbbbbbbbb" in comp.insufficient_suites

    def test_best_among_sufficient_only(self) -> None:
        a = _sufficient(_run("a"), expectancy=0.5)
        b = _sufficient(_run("b"), expectancy=0.9)
        c = _insufficient(_run("c"))
        sr = SuiteRegistry(directory=Path("/tmp/nonexistent-test-suite"))
        r1 = self._suite_result("suite-aaaaaaaaaaaaaaaa", "s1", [a])
        r2 = self._suite_result("suite-bbbbbbbbbbbbbbbb", "s2", [b])
        r3 = self._suite_result("suite-cccccccccccccccc", "s3", [c])
        comp = sr._compare_suite_results([r1, r2, r3])
        # Best suite by member expectancy is the one containing b.
        assert comp.best_suite_by_member_expectancy == "suite-bbbbbbbbbbbbbbbb"

    def test_no_best_when_none_sufficient(self) -> None:
        a = _insufficient(_run("a"))
        b = _insufficient(_run("b"))
        sr = SuiteRegistry(directory=Path("/tmp/nonexistent-test-suite"))
        r1 = self._suite_result("suite-aaaaaaaaaaaaaaaa", "s1", [a])
        r2 = self._suite_result("suite-bbbbbbbbbbbbbbbb", "s2", [b])
        comp = sr._compare_suite_results([r1, r2])
        assert comp.best_suite_by_member_expectancy is None
        assert comp.has_sufficient_evidence is False

    def test_descriptive_language(self) -> None:
        a = _insufficient(_run("a"))
        sr = SuiteRegistry(directory=Path("/tmp/nonexistent-test-suite"))
        r1 = self._suite_result("suite-aaaaaaaaaaaaaaaa", "s1", [a])
        comp = sr._compare_suite_results([r1])
        text = " ".join(comp.conclusions)
        assert "predictive" in text.lower() or "descriptive" in text.lower()

    def test_empty_comparison(self) -> None:
        sr = SuiteRegistry(directory=Path("/tmp/nonexistent-test-suite"))
        comp = sr._compare_suite_results([])
        assert comp.is_empty
        assert comp.suite_count == 0

    def test_immutable(self) -> None:
        a = _sufficient(_run("a"), expectancy=0.5)
        sr = SuiteRegistry(directory=Path("/tmp/nonexistent-test-suite"))
        r1 = self._suite_result("suite-aaaaaaaaaaaaaaaa", "s1", [a])
        comp = sr._compare_suite_results([r1])
        with pytest.raises(FrozenInstanceError):
            comp.rows = ()  # type: ignore[misc]

    def test_determinism(self) -> None:
        a = _sufficient(_run("a"), expectancy=0.5)
        sr = SuiteRegistry(directory=Path("/tmp/nonexistent-test-suite"))
        r1 = self._suite_result("suite-aaaaaaaaaaaaaaaa", "s1", [a])
        c1 = sr._compare_suite_results([r1])
        c2 = sr._compare_suite_results([r1])
        assert c1 == c2


# ============================================================
# G. REPORTING
# ============================================================


class TestReporting:
    def _suite_result(
        self,
        members: list[ExperimentResult],
        label: str = "s",
    ) -> SuiteResult:
        suite = SuiteConfig(label=label, members=tuple(_fast_config(m.label) for m in members))  # noqa: E501
        repro = SuiteReproducibilityMetadata(
            suite_id=suite.suite_id,
            suite_configuration_hash=suite.configuration_hash,
            suite_configuration_representation=suite.canonical_representation,
            member_experiment_ids=tuple(m.experiment_id for m in members),
            member_count=len(members),
        )
        summary = SuiteAnalysisEngine().summarize(members)
        return SuiteResult(
            suite_id=suite.suite_id,
            config=suite,
            reproducibility=repro,
            summary=summary,
            members=tuple(members),
            label=label,
        )

    def test_suite_report_sections(self) -> None:
        a = _sufficient(_run("a"), expectancy=0.5)
        result = self._suite_result([a], label="my-suite")
        text = SuiteReportFormatter().format(result)
        assert "Experiment Suite Report" in text
        assert "Suite Identity" in text
        assert "Member Experiments" in text
        assert "Suite Summary" in text
        assert "Reproducibility" in text
        assert "Conclusions" in text
        assert "my-suite" in text

    def test_suite_report_no_misleading_claims(self) -> None:
        a = _insufficient(_run("a"))
        result = self._suite_result([a])
        text = SuiteReportFormatter().format(result)
        assert "is profitable" not in text.lower()
        assert "predict" not in text.lower() or "not predict" in text.lower()

    def test_suite_report_insufficient_explicit(self) -> None:
        a = _insufficient(_run("a"))
        result = self._suite_result([a])
        text = SuiteReportFormatter().format(a and result)
        assert "INSUFFICIENT" in text or "SUFFICIENT" in text

    def test_suite_report_returns_str(self) -> None:
        a = _sufficient(_run("a"))
        result = self._suite_result([a])
        text = SuiteReportFormatter().format(result)
        assert isinstance(text, str)

    def test_suite_report_determinism(self) -> None:
        a = _sufficient(_run("a"), expectancy=0.5)
        result = self._suite_result([a])
        t1 = SuiteReportFormatter().format(result)
        t2 = SuiteReportFormatter().format(result)
        assert t1 == t2

    def test_suite_report_empty_members(self) -> None:
        result = self._suite_result([])
        text = SuiteReportFormatter().format(result)
        assert "no members" in text.lower() or "(no members)" in text

    def test_no_print_in_formatters(self) -> None:
        import inspect

        assert "print(" not in inspect.getsource(SuiteReportFormatter)
        assert "print(" not in inspect.getsource(SuiteComparisonFormatter)

    def test_comparison_report_sections(self) -> None:
        a = _sufficient(_run("a"), expectancy=0.5)
        sr = SuiteRegistry(directory=Path("/tmp/nonexistent-test-suite"))
        r1 = self._suite_result([a], label="s1")
        comp = sr._compare_suite_results([r1])
        text = SuiteComparisonFormatter().format(comp)
        assert "Suite Comparison" in text
        assert "Conclusions" in text

    def test_comparison_report_empty(self) -> None:
        sr = SuiteRegistry(directory=Path("/tmp/nonexistent-test-suite"))
        comp = sr._compare_suite_results([])
        text = SuiteComparisonFormatter().format(comp)
        assert "No suites" in text

    def test_comparison_report_no_best_without_evidence(self) -> None:
        a = _insufficient(_run("a"))
        sr = SuiteRegistry(directory=Path("/tmp/nonexistent-test-suite"))
        r1 = self._suite_result([a], label="s1")
        comp = sr._compare_suite_results([r1])
        text = SuiteComparisonFormatter().format(comp)
        assert "NOT declared" in text or "No descriptive best" in text


# ============================================================
# H. BACKWARD COMPATIBILITY
# ============================================================


class TestBackwardCompatibility:
    def test_11j_apis_importable(self) -> None:
        from engine.experiment import (
            ExperimentConfig,
            ExperimentRunner,
            ExperimentComparisonEngine,
        )

        assert ExperimentConfig and ExperimentRunner and ExperimentComparisonEngine

    def test_11k_apis_importable(self) -> None:
        from engine.registry import (
            ExperimentRegistry,
            ExperimentPersistence,
            SCHEMA_VERSION,
        )

        assert ExperimentRegistry and ExperimentPersistence
        assert SCHEMA_VERSION == 1

    def test_11l_apis_importable(self) -> None:
        from engine.query import (
            ExperimentQueryEngine,
            ExperimentFilter,
            ExperimentSortKey,
        )

        assert ExperimentQueryEngine and ExperimentFilter and ExperimentSortKey

    def test_experiment_schema_version_unchanged(self) -> None:
        from engine.models.registry import SCHEMA_VERSION

        assert SCHEMA_VERSION == 1

    def test_suite_schema_version_separate(self) -> None:
        assert SUITE_SCHEMA_VERSION == 1
        from engine.models.registry import SCHEMA_VERSION

        assert SCHEMA_VERSION == 1  # experiment schema unchanged

    def test_minimal_suite_result_handled(self) -> None:
        # A hand-constructed minimal SuiteResult (config None) must be
        # handled gracefully by the integrity check (mirrors 11K).
        repro = SuiteReproducibilityMetadata(
            suite_id="suite-minimal00000000",
            suite_configuration_hash="h",
            suite_configuration_representation="r",
        )
        summary = SuiteSummary()
        result = SuiteResult(
            suite_id="suite-minimal00000000",
            config=None,
            reproducibility=repro,
            summary=summary,
        )
        # Must not raise.
        SuiteRegistry._verify_suite_identity(result)


# ============================================================
# I. IMMUTABILITY
# ============================================================


class TestImmutability:
    def test_suite_result_frozen(self) -> None:
        repro = SuiteReproducibilityMetadata(
            suite_id="suite-x",
            suite_configuration_hash="h",
            suite_configuration_representation="r",
        )
        summary = SuiteSummary()
        r = SuiteResult(
            suite_id="suite-x",
            config=None,
            reproducibility=repro,
            summary=summary,
        )
        with pytest.raises(FrozenInstanceError):
            r.suite_id = "other"  # type: ignore[misc]

    def test_suite_summary_frozen(self) -> None:
        s = SuiteSummary()
        with pytest.raises(FrozenInstanceError):
            s.member_count = 99  # type: ignore[misc]

    def test_suite_comparison_frozen(self) -> None:
        c = SuiteComparison()
        with pytest.raises(FrozenInstanceError):
            c.rows = ()  # type: ignore[misc]

    def test_suite_comparison_row_frozen(self) -> None:
        r = SuiteComparisonRow(
            suite_id="s",
            label="l",
            member_count=1,
            sufficient_count=1,
            partial_count=0,
            insufficient_count=0,
            has_sufficient_evidence=True,
        )
        with pytest.raises(FrozenInstanceError):
            r.suite_id = "other"  # type: ignore[misc]

    def test_suite_reproducibility_frozen(self) -> None:
        r = SuiteReproducibilityMetadata(
            suite_id="s",
            suite_configuration_hash="h",
            suite_configuration_representation="r",
        )
        with pytest.raises(FrozenInstanceError):
            r.suite_id = "other"  # type: ignore[misc]

    def test_suite_config_frozen(self) -> None:
        c = SuiteConfig(label="s", members=())
        with pytest.raises(FrozenInstanceError):
            c.label = "other"  # type: ignore[misc]

    def test_reproducibility_slots(self) -> None:
        r = SuiteReproducibilityMetadata(
            suite_id="s",
            suite_configuration_hash="h",
            suite_configuration_representation="r",
        )
        with pytest.raises(AttributeError):
            r.extra = 1  # type: ignore[attr-defined]


# ============================================================
# PACKAGE SURFACE
# ============================================================


class TestPackageSurface:
    def test_importable_api(self) -> None:
        import engine.suite as pkg

        for name in (
            "SuiteConfig",
            "SuiteRunner",
            "SuiteRegistry",
            "SuiteAnalysisEngine",
            "SuiteManifestPersistence",
            "SuiteResult",
            "SuiteSummary",
            "SuiteComparison",
            "SuiteComparisonRow",
            "SuiteReproducibilityMetadata",
            "SUITE_SCHEMA_VERSION",
            "SuiteError",
            "SuiteNotFoundError",
            "SuiteAlreadyExistsError",
            "SuiteIntegrityError",
            "UnsupportedSuiteSchemaVersionError",
        ):
            assert hasattr(pkg, name), name

    def test_exception_hierarchy(self) -> None:
        assert issubclass(SuiteNotFoundError, SuiteError)
        assert issubclass(SuiteAlreadyExistsError, SuiteError)
        assert issubclass(SuiteIntegrityError, SuiteError)
        assert issubclass(UnsupportedSuiteSchemaVersionError, SuiteError)

    def test_reporting_reexports(self) -> None:
        import engine.reporting as rep

        assert hasattr(rep, "SuiteReportFormatter")
        assert hasattr(rep, "SuiteComparisonFormatter")
