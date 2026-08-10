"""
Tests for the experiment registry / persistence layer (Sprint 11K).

Covers:

A. Serialization
   - save/load round trip
   - deterministic serialization (stable bytes)
   - experiment ID preservation
   - dataset hash preservation
   - configuration hash preservation
   - parameter preservation
   - evidence status preservation
   - research conclusion preservation
   - robustness preservation
   - walk-forward preservation
   - OOS preservation
   - leakage audit preservation
   - pipeline/performance summary preservation

B. Registry operations
   - list operation
   - exists operation
   - missing experiment
   - duplicate experiment (no silent overwrite)
   - overwrite behavior
   - delete

C. Error handling
   - corrupted JSON
   - invalid schema version
   - integrity mismatch
   - unsafe experiment id

D. Persistence behaviour
   - atomic persistence (no partial files / temp files left)
   - Windows-compatible paths (pathlib, no separators)

E. Comparison integration
   - loading multiple experiments
   - comparison of persisted experiments (no rerun)

F. Evidence variants
   - persistence of insufficient-evidence experiments
   - persistence of sufficient-evidence experiments
   - persistence of partial-evidence experiments

G. Schema / versioning
   - SCHEMA_VERSION constant
   - header parse
   - load_many ordering

H. Immutability / determinism
   - registry models frozen
   - re-serialization determinism
"""

from __future__ import annotations

import json
import os
from dataclasses import FrozenInstanceError, is_dataclass
from pathlib import Path

import pytest

from engine.config.swing_config import SwingConfig
from engine.experiment import (
    DatasetSpec,
    EvaluationConfig,
    ExperimentComparisonEngine,
    ExperimentConfig,
    ExperimentEvidenceStatus,
    ExperimentRunner,
)
from engine.models.experiment import (
    ExperimentResult,
    ExperimentSummary,
    ReproducibilityMetadata,
)
from engine.models.registry import (
    SCHEMA_VERSION,
    ExperimentRecordHeader,
    PersistedExperimentRecord,
)
from engine.pipeline.historical_pipeline import PipelineConfig
from engine.registry import (
    ExperimentAlreadyExistsError,
    ExperimentIntegrityError,
    ExperimentNotFoundError,
    ExperimentPersistence,
    ExperimentPersistenceError,
    ExperimentRegistry,
    UnsupportedSchemaVersionError,
    canonical_json,
    deserialize_experiment,
    parse_record,
    serialize_experiment,
)
from engine.registry.persistence import default_registry_directory
from engine.research.research import ResearchConfig
from engine.reporting import (
    ExperimentComparisonFormatter,
    ExperimentReportFormatter,
)


# ============================================================
# SHARED HELPERS
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
        metadata=metadata if metadata is not None else {"sprint": "11K"},
    )


def _run(label: str = "trending-lookback-2", **kwargs) -> ExperimentResult:
    return ExperimentRunner().run(_config(label, **kwargs))


@pytest.fixture()
def registry(tmp_path: Path) -> ExperimentRegistry:
    return ExperimentRegistry(tmp_path)


@pytest.fixture()
def two_results() -> tuple[ExperimentResult, ExperimentResult]:
    return _run("trending-lookback-2", lookback=2), _run(
        "trending-lookback-3", lookback=3
    )


# ============================================================
# A. SERIALIZATION
# ============================================================


class TestSerialization:
    """Deterministic serialization and round-trip fidelity."""

    def test_save_load_round_trip(self, registry: ExperimentRegistry) -> None:
        original = _run()
        registry.register(original)
        loaded = registry.get(original.experiment_id)

        assert loaded.experiment_id == original.experiment_id
        assert loaded.summary == original.summary
        assert loaded.reproducibility == original.reproducibility
        assert loaded.dataset == original.dataset
        assert loaded.label == original.label
        assert loaded.metadata == original.metadata

    def test_deterministic_serialization(self) -> None:
        result = _run()
        first = serialize_experiment(result)
        second = serialize_experiment(result)
        assert first == second

    def test_deterministic_serialization_stable_across_objects(self) -> None:
        # Two runs of the same deterministic config produce the same
        # bytes -- the core reproducibility guarantee.
        a = serialize_experiment(_run())
        b = serialize_experiment(_run())
        assert a == b

    def test_re_serialize_after_round_trip(self, registry: ExperimentRegistry) -> None:
        original = _run()
        registry.register(original)
        loaded = registry.get(original.experiment_id)

        assert serialize_experiment(loaded) == serialize_experiment(original)

    def test_experiment_id_preserved(self, registry: ExperimentRegistry) -> None:
        original = _run()
        registry.register(original)
        assert registry.get(original.experiment_id).experiment_id == original.experiment_id

    def test_dataset_hash_preserved(self, registry: ExperimentRegistry) -> None:
        original = _run()
        registry.register(original)
        loaded = registry.get(original.experiment_id)
        assert (
            loaded.reproducibility.dataset_content_hash
            == original.reproducibility.dataset_content_hash
        )
        assert loaded.dataset.content_hash == original.dataset.content_hash

    def test_configuration_hash_preserved(
        self, registry: ExperimentRegistry
    ) -> None:
        original = _run()
        registry.register(original)
        loaded = registry.get(original.experiment_id)
        assert (
            loaded.config.configuration_hash
            == original.config.configuration_hash
        )
        assert (
            loaded.reproducibility.configuration_hash
            == original.reproducibility.configuration_hash
        )

    def test_parameter_preserved(self, registry: ExperimentRegistry) -> None:
        original = _run()
        registry.register(original)
        loaded = registry.get(original.experiment_id)
        assert (
            loaded.reproducibility.parameter_values
            == original.reproducibility.parameter_values
        )
        assert loaded.config.strategy_parameters == original.config.strategy_parameters
        assert (
            loaded.config.pipeline.swing_config.lookback
            == original.config.pipeline.swing_config.lookback
        )

    def test_evidence_status_preserved(
        self, registry: ExperimentRegistry
    ) -> None:
        original = _run()
        registry.register(original)
        loaded = registry.get(original.experiment_id)
        assert loaded.summary.evidence_status == original.summary.evidence_status
        assert loaded.evidence_status == original.evidence_status

    def test_research_conclusions_preserved(
        self, registry: ExperimentRegistry
    ) -> None:
        original = _run()
        registry.register(original)
        loaded = registry.get(original.experiment_id)
        assert (
            loaded.research_report.conclusions
            == original.research_report.conclusions
        )

    def test_robustness_preserved(self, registry: ExperimentRegistry) -> None:
        original = _run()
        registry.register(original)
        loaded = registry.get(original.experiment_id)
        assert (
            loaded.research_report.parameter_robustness
            == original.research_report.parameter_robustness
        )

    def test_walk_forward_preserved(self, registry: ExperimentRegistry) -> None:
        original = _run()
        registry.register(original)
        loaded = registry.get(original.experiment_id)
        wf_orig = original.research_report.walk_forward_selection
        wf_load = loaded.research_report.walk_forward_selection
        assert wf_orig is not None
        assert wf_load is not None
        # Structural fields are preserved; the raw out_of_sample_result
        # (PipelineResult) is intentionally not persisted.
        assert wf_load.selected == wf_orig.selected
        assert wf_load.candidates == wf_orig.candidates
        assert (
            wf_load.out_of_sample_completed_trades
            == wf_orig.out_of_sample_completed_trades
        )
        assert (
            wf_load.selection_isolated_from_evaluation
            == wf_orig.selection_isolated_from_evaluation
        )
        assert wf_load.selection_verified == wf_orig.selection_verified
        assert wf_load.development_window == wf_orig.development_window
        assert wf_load.evaluation_window == wf_orig.evaluation_window

    def test_oos_preserved(self, registry: ExperimentRegistry) -> None:
        original = _run()
        registry.register(original)
        loaded = registry.get(original.experiment_id)
        oos_orig = original.research_report.out_of_sample
        oos_load = loaded.research_report.out_of_sample
        assert oos_orig is not None
        assert oos_load is not None
        assert oos_load.split_ratio == oos_orig.split_ratio
        assert oos_load.in_sample_count == oos_orig.in_sample_count
        assert oos_load.out_of_sample_count == oos_orig.out_of_sample_count
        assert (
            oos_load.expectancy_degradation
            == oos_orig.expectancy_degradation
        )
        assert oos_load.sufficient_data == oos_orig.sufficient_data
        # Performance analytics (aggregate) are preserved.
        assert (
            oos_load.in_sample_performance
            == oos_orig.in_sample_performance
        )
        assert (
            oos_load.out_of_sample_performance
            == oos_orig.out_of_sample_performance
        )

    def test_leakage_audit_preserved(
        self, registry: ExperimentRegistry
    ) -> None:
        original = _run()
        registry.register(original)
        loaded = registry.get(original.experiment_id)
        assert loaded.research_report.leakage == original.research_report.leakage

    def test_pipeline_summary_preserved(
        self, registry: ExperimentRegistry
    ) -> None:
        original = _run()
        registry.register(original)
        loaded = registry.get(original.experiment_id)
        assert (
            loaded.evaluation_report.pipeline
            == original.evaluation_report.pipeline
        )
        assert (
            loaded.evaluation_report.signals
            == original.evaluation_report.signals
        )
        assert (
            loaded.evaluation_report.trades
            == original.evaluation_report.trades
        )

    def test_raw_result_dropped_on_load(
        self, registry: ExperimentRegistry
    ) -> None:
        original = _run()
        registry.register(original)
        loaded = registry.get(original.experiment_id)
        # The raw PipelineResult is intentionally NOT persisted.
        assert loaded.evaluation_report.result is None
        assert loaded.research_report.result is None
        assert (
            loaded.research_report.walk_forward_selection.out_of_sample_result
            is None
        )

    def test_enum_round_trips(self) -> None:
        result = _run()
        text = serialize_experiment(result)
        loaded = deserialize_experiment(text)
        # Enums deserialize to the exact enum members, not strings.
        assert (
            loaded.summary.evidence_status
            is result.summary.evidence_status
        )
        assert (
            loaded.config.evaluation.run_out_of_sample
            is result.config.evaluation.run_out_of_sample
        )


# ============================================================
# B. REGISTRY OPERATIONS
# ============================================================


class TestRegistryOperations:
    """Registry-level operations above persistence."""

    def test_list_empty(self, registry: ExperimentRegistry) -> None:
        assert registry.list() == []

    def test_list_after_register(
        self,
        registry: ExperimentRegistry,
        two_results: tuple[ExperimentResult, ExperimentResult],
    ) -> None:
        a, b = two_results
        registry.register(a)
        registry.register(b)
        assert registry.list() == sorted([a.experiment_id, b.experiment_id])

    def test_exists(
        self,
        registry: ExperimentRegistry,
        two_results: tuple[ExperimentResult, ExperimentResult],
    ) -> None:
        a, _ = two_results
        assert not registry.exists(a.experiment_id)
        registry.register(a)
        assert registry.exists(a.experiment_id)
        assert not registry.exists("exp-doesnotexist0000")

    def test_missing_experiment_raises(self, registry: ExperimentRegistry) -> None:
        with pytest.raises(ExperimentNotFoundError):
            registry.get("exp-missing00000000")

    def test_duplicate_experiment_raises_no_silent_overwrite(
        self,
        registry: ExperimentRegistry,
    ) -> None:
        original = _run()
        registry.register(original)

        with pytest.raises(ExperimentAlreadyExistsError) as exc:
            registry.register(original)
        assert exc.value.experiment_id == original.experiment_id

        # The stored content is unchanged.
        loaded = registry.get(original.experiment_id)
        assert loaded.summary == original.summary

    def test_overwrite_behavior(self, registry: ExperimentRegistry) -> None:
        original = _run()
        registry.register(original)

        # overwrite=True must not raise.
        registry.register(original, overwrite=True)

        loaded = registry.get(original.experiment_id)
        assert loaded.summary == original.summary

    def test_delete(
        self,
        registry: ExperimentRegistry,
        two_results: tuple[ExperimentResult, ExperimentResult],
    ) -> None:
        a, b = two_results
        registry.register(a)
        registry.register(b)
        registry.delete(a.experiment_id)
        assert not registry.exists(a.experiment_id)
        assert registry.list() == [b.experiment_id]

    def test_delete_missing_raises(self, registry: ExperimentRegistry) -> None:
        with pytest.raises(ExperimentNotFoundError):
            registry.delete("exp-missing00000000")

    def test_register_returns_result(
        self, registry: ExperimentRegistry
    ) -> None:
        original = _run()
        returned = registry.register(original)
        assert returned is original


# ============================================================
# C. ERROR HANDLING
# ============================================================


class TestErrorHandling:
    """Explicit, typed errors -- no silent swallowing."""

    def test_corrupted_json(
        self,
        registry: ExperimentRegistry,
        tmp_path: Path,
    ) -> None:
        original = _run()
        registry.register(original)
        path = registry.persistence.path_for(original.experiment_id)
        path.write_text("{ this is not valid json", encoding="utf-8")

        with pytest.raises(ExperimentPersistenceError):
            registry.get(original.experiment_id)

    def test_invalid_schema_version_rejected(
        self,
        registry: ExperimentRegistry,
        tmp_path: Path,
    ) -> None:
        original = _run()
        registry.register(original)
        path = registry.persistence.path_for(original.experiment_id)
        path.write_text(
            json.dumps(
                {
                    "schema_version": 999,
                    "experiment_id": original.experiment_id,
                    "result": {},
                }
            ),
            encoding="utf-8",
        )

        with pytest.raises(UnsupportedSchemaVersionError) as exc:
            registry.get(original.experiment_id)
        assert exc.value.found == 999
        assert exc.value.supported == SCHEMA_VERSION

    def test_future_schema_version_rejected_at_load(
        self,
        registry: ExperimentRegistry,
        tmp_path: Path,
    ) -> None:
        original = _run()
        path = registry.persistence.path_for(original.experiment_id)
        # Write a future-version header but a real result body.
        body = json.loads(serialize_experiment(original))
        body["schema_version"] = SCHEMA_VERSION + 1
        path.write_text(json.dumps(body), encoding="utf-8")

        with pytest.raises(UnsupportedSchemaVersionError):
            registry.get(original.experiment_id)

    def test_integrity_mismatch_experiment_id(
        self,
        registry: ExperimentRegistry,
        tmp_path: Path,
    ) -> None:
        original = _run()
        registry.register(original)
        path = registry.persistence.path_for(original.experiment_id)

        body = json.loads(path.read_text(encoding="utf-8"))
        # Tamper with the top-level experiment id (file name stays).
        body["experiment_id"] = "exp-tampered0000000"
        path.write_text(json.dumps(body), encoding="utf-8")

        with pytest.raises(ExperimentIntegrityError):
            registry.get(original.experiment_id)

    def test_integrity_mismatch_config_hash(
        self,
        registry: ExperimentRegistry,
        tmp_path: Path,
    ) -> None:
        original = _run()
        registry.register(original)
        path = registry.persistence.path_for(original.experiment_id)

        body = json.loads(path.read_text(encoding="utf-8"))
        # Tamper with the reproducibility configuration hash so it no
        # longer matches the config's hash.
        result = body["result"]
        repro = result["reproducibility"]
        repro["configuration_hash"] = "0" * 64
        path.write_text(json.dumps(body), encoding="utf-8")

        with pytest.raises(ExperimentIntegrityError):
            registry.get(original.experiment_id)

    def test_integrity_mismatch_dataset_hash(
        self,
        registry: ExperimentRegistry,
        tmp_path: Path,
    ) -> None:
        original = _run()
        registry.register(original)
        path = registry.persistence.path_for(original.experiment_id)

        body = json.loads(path.read_text(encoding="utf-8"))
        result = body["result"]
        repro = result["reproducibility"]
        repro["dataset_content_hash"] = "f" * 64
        path.write_text(json.dumps(body), encoding="utf-8")

        with pytest.raises(ExperimentIntegrityError):
            registry.get(original.experiment_id)

    def test_filename_id_mismatch_rejected(
        self,
        registry: ExperimentRegistry,
        tmp_path: Path,
    ) -> None:
        original = _run()
        path = registry.persistence.path_for(original.experiment_id)
        # Write a valid record but with a different stored experiment id.
        body = json.loads(serialize_experiment(original))
        body["experiment_id"] = "exp-other00000000000"
        # Also tamper the inner result id + repro id to keep them
        # internally consistent so the filename check fires first.
        body["result"]["experiment_id"] = "exp-other00000000000"
        body["result"]["reproducibility"]["experiment_id"] = (
            "exp-other00000000000"
        )
        path.write_text(json.dumps(body), encoding="utf-8")

        with pytest.raises(ExperimentIntegrityError):
            registry.get(original.experiment_id)

    def test_unsafe_experiment_id_rejected(self, tmp_path: Path) -> None:
        persistence = ExperimentPersistence(tmp_path)
        from engine.models.experiment import (
            ExperimentResult,
            ExperimentSummary,
            ReproducibilityMetadata,
        )

        # Build a minimal result with a path-traversal id.
        repro = ReproducibilityMetadata(
            experiment_id="../escape",
            configuration_hash="x",
            configuration_representation="x",
            dataset_identity="trending",
            dataset_content_hash=None,
            dataset_size=0,
        )
        summary = ExperimentSummary(
            completed_trades=0,
            win_rate=0.0,
            expectancy=0.0,
            total_r=0.0,
            profit_factor=0.0,
            max_drawdown_r=0.0,
            robust=None,
            descriptive_best=None,
            oos_expectancy=None,
            oos_trades=0,
            data_sufficient=False,
            leakage_passed=None,
            leakage_not_verified=False,
            evidence_status=ExperimentEvidenceStatus.INSUFFICIENT,
        )
        result = ExperimentResult(
            experiment_id="../escape",
            config=None,
            dataset=DatasetSpec(name="trending"),
            dataset_size=0,
            evaluation_report=None,
            research_report=None,
            reproducibility=repro,
            summary=summary,
            label="bad",
        )

        with pytest.raises(ExperimentPersistenceError):
            persistence.save(result)


# ============================================================
# D. PERSISTENCE BEHAVIOUR
# ============================================================


class TestPersistenceBehaviour:
    """Atomic writes and cross-platform path handling."""

    def test_atomic_persistence_no_temp_files_left(
        self,
        registry: ExperimentRegistry,
        tmp_path: Path,
    ) -> None:
        original = _run()
        registry.register(original)

        # Only the final record file should exist; no .tmp leftovers.
        files = [p.name for p in tmp_path.iterdir()]
        assert files == [f"{original.experiment_id}.json"]

    def test_atomic_persistence_temp_file_cleaned_on_failure(
        self,
        registry: ExperimentRegistry,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        original = _run()

        # Force os.replace to fail so the atomic-write cleanup path
        # runs; the temp file must be removed and no target written.
        import engine.registry.persistence as persistence_mod

        def boom(src: Path, dst: Path) -> None:
            raise OSError("simulated rename failure")

        monkeypatch.setattr(persistence_mod.os, "replace", boom)

        with pytest.raises(ExperimentPersistenceError):
            registry.register(original)

        # No target file and no leftover temp files.
        files = [p.name for p in tmp_path.iterdir()]
        assert files == []

    def test_atomic_write_replaces_existing(
        self,
        registry: ExperimentRegistry,
        tmp_path: Path,
    ) -> None:
        original = _run()
        registry.register(original)
        path = registry.persistence.path_for(original.experiment_id)

        # Overwrite; the file should be replaced atomically.
        registry.register(original, overwrite=True)
        assert path.exists()
        # Still only one file.
        files = [p.name for p in tmp_path.iterdir()]
        assert files == [f"{original.experiment_id}.json"]

    def test_windows_compatible_paths(
        self,
        registry: ExperimentRegistry,
    ) -> None:
        original = _run()
        registry.register(original)
        path = registry.persistence.path_for(original.experiment_id)

        # All path construction uses pathlib (no hard-coded separators).
        assert isinstance(path, Path)
        assert path.parent == registry.directory
        # The file name is the id + suffix, with no OS separators.
        assert path.name == f"{original.experiment_id}.json"
        assert "/" not in path.name
        assert "\\" not in path.name

    def test_default_registry_directory_is_relative(self) -> None:
        # The default directory must NOT be a hard-coded absolute path;
        # it is relative to the cwd so it works from any project root.
        default = default_registry_directory()
        assert not default.is_absolute() or default == Path.cwd() / "experiments"

    def test_persistence_load_record(
        self,
        registry: ExperimentRegistry,
    ) -> None:
        original = _run()
        registry.register(original)

        record = registry.persistence.load_record(original.experiment_id)
        assert isinstance(record, PersistedExperimentRecord)
        assert record.header.schema_version == SCHEMA_VERSION
        assert record.header.experiment_id == original.experiment_id
        assert record.result.experiment_id == original.experiment_id
        assert isinstance(record.canonical_json, str)
        assert record.raw["schema_version"] == SCHEMA_VERSION

    def test_load_missing_record_raises(self, registry: ExperimentRegistry) -> None:
        with pytest.raises(ExperimentNotFoundError):
            registry.persistence.load("exp-missing00000000")


# ============================================================
# E. COMPARISON INTEGRATION
# ============================================================


class TestComparisonIntegration:
    """Comparing loaded experiments without rerunning the pipeline."""

    def test_load_multiple_experiments(
        self,
        registry: ExperimentRegistry,
        two_results: tuple[ExperimentResult, ExperimentResult],
    ) -> None:
        a, b = two_results
        registry.register(a)
        registry.register(b)

        loaded = registry.load_many([a.experiment_id, b.experiment_id])
        assert len(loaded) == 2
        assert loaded[0].experiment_id == a.experiment_id
        assert loaded[1].experiment_id == b.experiment_id
        assert loaded[0].summary == a.summary
        assert loaded[1].summary == b.summary

    def test_load_many_preserves_order(
        self,
        registry: ExperimentRegistry,
        two_results: tuple[ExperimentResult, ExperimentResult],
    ) -> None:
        a, b = two_results
        registry.register(a)
        registry.register(b)

        loaded = registry.load_many([b.experiment_id, a.experiment_id])
        assert [r.experiment_id for r in loaded] == [
            b.experiment_id,
            a.experiment_id,
        ]

    def test_load_many_missing_raises(
        self,
        registry: ExperimentRegistry,
        two_results: tuple[ExperimentResult, ExperimentResult],
    ) -> None:
        a, _ = two_results
        registry.register(a)
        with pytest.raises(ExperimentNotFoundError):
            registry.load_many([a.experiment_id, "exp-missing00000000"])

    def test_compare_persisted_experiments(
        self,
        registry: ExperimentRegistry,
        two_results: tuple[ExperimentResult, ExperimentResult],
    ) -> None:
        a, b = two_results
        registry.register(a)
        registry.register(b)

        comparison = registry.compare([a.experiment_id, b.experiment_id])
        assert comparison.experiment_count == 2
        assert {r.experiment_id for r in comparison.rows} == {
            a.experiment_id,
            b.experiment_id,
        }

    def test_compare_loaded_matches_compare_live(
        self,
        registry: ExperimentRegistry,
        two_results: tuple[ExperimentResult, ExperimentResult],
    ) -> None:
        a, b = two_results
        registry.register(a)
        registry.register(b)

        from_disk = registry.compare([a.experiment_id, b.experiment_id])
        live = ExperimentComparisonEngine().compare([a, b])

        assert from_disk.rows == live.rows
        assert from_disk.conclusions == live.conclusions
        assert from_disk.best_by_expectancy == live.best_by_expectancy
        assert from_disk.best_by_total_r == live.best_by_total_r

    def test_compare_uses_persisted_results_no_rerun(
        self,
        registry: ExperimentRegistry,
        two_results: tuple[ExperimentResult, ExperimentResult],
        monkeypatch,
    ) -> None:
        a, b = two_results
        registry.register(a)
        registry.register(b)

        # Patch the pipeline so any accidental rerun would explode.
        import engine.pipeline.historical_pipeline as pipeline_mod

        def explode(*args, **kwargs):
            raise AssertionError(
                "Comparison must use persisted results, not rerun the pipeline."
            )

        monkeypatch.setattr(
            pipeline_mod.HistoricalEvaluationPipeline,
            "evaluate",
            explode,
        )

        # Comparison must still succeed using only persisted data.
        comparison = registry.compare([a.experiment_id, b.experiment_id])
        assert comparison.experiment_count == 2

    def test_report_formatter_works_on_loaded(
        self,
        registry: ExperimentRegistry,
        two_results: tuple[ExperimentResult, ExperimentResult],
    ) -> None:
        a, _ = two_results
        registry.register(a)
        loaded = registry.get(a.experiment_id)

        report = ExperimentReportFormatter().format(loaded)
        assert a.experiment_id in report
        assert "Experiment Identity" in report
        assert "Reproducibility" in report

        comparison = registry.compare([a.experiment_id, a.experiment_id])
        comp_report = ExperimentComparisonFormatter().format(comparison)
        assert "Comparison Summary" in comp_report


# ============================================================
# F. EVIDENCE VARIANTS
# ============================================================


class TestEvidenceVariants:
    """Persistence across evidence-sufficiency states."""

    def test_persist_insufficient_evidence(
        self, registry: ExperimentRegistry
    ) -> None:
        # The trending dataset produces INSUFFICIENT evidence (few
        # completed trades). This must round-trip faithfully.
        original = _run("insufficient")
        assert original.evidence_status == ExperimentEvidenceStatus.INSUFFICIENT

        registry.register(original)
        loaded = registry.get(original.experiment_id)
        assert loaded.evidence_status == ExperimentEvidenceStatus.INSUFFICIENT
        assert loaded.summary == original.summary

    def test_persist_partial_evidence(self, registry: ExperimentRegistry) -> None:
        # A config with research disabled yields no data-sufficiency
        # gate and (with enough trades) PARTIAL evidence. Build a
        # synthetic PARTIAL result so the persisted form is exercised
        # regardless of dataset-driven evidence status.
        base = _run("base")
        partial_summary = ExperimentSummary(
            completed_trades=base.summary.completed_trades,
            win_rate=base.summary.win_rate,
            expectancy=base.summary.expectancy,
            total_r=base.summary.total_r,
            profit_factor=base.summary.profit_factor,
            max_drawdown_r=base.summary.max_drawdown_r,
            robust=base.summary.robust,
            descriptive_best=base.summary.descriptive_best,
            oos_expectancy=base.summary.oos_expectancy,
            oos_trades=base.summary.oos_trades,
            data_sufficient=base.summary.data_sufficient,
            leakage_passed=base.summary.leakage_passed,
            leakage_not_verified=base.summary.leakage_not_verified,
            evidence_status=ExperimentEvidenceStatus.PARTIAL,
        )
        from dataclasses import replace

        partial = replace(base, summary=partial_summary)
        registry.register(partial)
        loaded = registry.get(partial.experiment_id)
        assert loaded.evidence_status == ExperimentEvidenceStatus.PARTIAL

    def test_persist_sufficient_evidence(self, registry: ExperimentRegistry) -> None:
        # Synthesize a SUFFICIENT result to exercise that evidence
        # status round-trips even when the dataset alone would be
        # INSUFFICIENT (the model permits all three statuses).
        base = _run("base")
        sufficient_summary = ExperimentSummary(
            completed_trades=base.summary.completed_trades,
            win_rate=base.summary.win_rate,
            expectancy=base.summary.expectancy,
            total_r=base.summary.total_r,
            profit_factor=base.summary.profit_factor,
            max_drawdown_r=base.summary.max_drawdown_r,
            robust=base.summary.robust,
            descriptive_best=base.summary.descriptive_best,
            oos_expectancy=base.summary.oos_expectancy,
            oos_trades=base.summary.oos_trades,
            data_sufficient=base.summary.data_sufficient,
            leakage_passed=base.summary.leakage_passed,
            leakage_not_verified=base.summary.leakage_not_verified,
            evidence_status=ExperimentEvidenceStatus.SUFFICIENT,
        )
        from dataclasses import replace

        sufficient = replace(base, summary=sufficient_summary)
        registry.register(sufficient)
        loaded = registry.get(sufficient.experiment_id)
        assert loaded.evidence_status == ExperimentEvidenceStatus.SUFFICIENT


# ============================================================
# G. SCHEMA / VERSIONING
# ============================================================


class TestSchemaVersioning:
    """Schema version identity and forward-compatibility guard."""

    def test_schema_version_constant(self) -> None:
        assert SCHEMA_VERSION == 1
        assert isinstance(SCHEMA_VERSION, int)

    def test_persisted_record_carries_schema_version(
        self, registry: ExperimentRegistry
    ) -> None:
        original = _run()
        registry.register(original)
        path = registry.persistence.path_for(original.experiment_id)
        body = json.loads(path.read_text(encoding="utf-8"))
        assert body["schema_version"] == SCHEMA_VERSION
        assert body["experiment_id"] == original.experiment_id

    def test_parse_record_header(self, registry: ExperimentRegistry) -> None:
        original = _run()
        registry.register(original)
        path = registry.persistence.path_for(original.experiment_id)
        header = parse_record(path.read_text(encoding="utf-8"))
        assert isinstance(header, ExperimentRecordHeader)
        assert header.schema_version == SCHEMA_VERSION
        assert header.experiment_id == original.experiment_id

    def test_parse_record_rejects_future_version(
        self, registry: ExperimentRegistry, tmp_path: Path
    ) -> None:
        text = json.dumps(
            {"schema_version": 2, "experiment_id": "exp-x"}
        )
        with pytest.raises(UnsupportedSchemaVersionError):
            parse_record(text)

    def test_canonical_json_is_sorted(
        self, registry: ExperimentRegistry
    ) -> None:
        original = _run()
        registry.register(original)
        path = registry.persistence.path_for(original.experiment_id)
        raw = path.read_text(encoding="utf-8")
        canonical = canonical_json(raw)
        # The canonical form has sorted keys.
        assert canonical == json.dumps(
            json.loads(raw), sort_keys=True, ensure_ascii=False
        )

    def test_schema_version_in_module_and_registry_match(self) -> None:
        import engine.registry.persistence as persistence_mod

        assert persistence_mod.SCHEMA_VERSION == SCHEMA_VERSION


# ============================================================
# H. IMMUTABILITY / DETERMINISM
# ============================================================


class TestImmutabilityAndDeterminism:
    """Frozen models and deterministic behaviour."""

    def test_registry_models_frozen(self) -> None:
        header = ExperimentRecordHeader(
            schema_version=1, experiment_id="exp-x"
        )
        record = PersistedExperimentRecord(
            header=header,
            result=None,
            canonical_json="{}",
            raw={},
        )
        assert is_dataclass(header)
        assert is_dataclass(record)
        with pytest.raises(FrozenInstanceError):
            header.experiment_id = "exp-y"  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            record.canonical_json = "x"  # type: ignore[misc]

    def test_serialize_deserialize_deterministic_loop(self) -> None:
        result = _run()
        a = serialize_experiment(result)
        b = serialize_experiment(deserialize_experiment(a))
        assert a == b

    def test_different_configs_different_ids(
        self, registry: ExperimentRegistry
    ) -> None:
        a = _run("lb2", lookback=2)
        b = _run("lb3", lookback=3)
        assert a.experiment_id != b.experiment_id
        registry.register(a)
        registry.register(b)
        assert set(registry.list()) == {a.experiment_id, b.experiment_id}

    def test_registry_no_global_mutable_state(self, tmp_path: Path) -> None:
        # Two independent registries in different directories do not
        # share state.
        d1 = tmp_path / "a"
        d2 = tmp_path / "b"
        r1 = ExperimentRegistry(d1)
        r2 = ExperimentRegistry(d2)

        original = _run()
        r1.register(original)
        assert r1.exists(original.experiment_id)
        assert not r2.exists(original.experiment_id)
        assert r1.list() == [original.experiment_id]
        assert r2.list() == []


# ============================================================
# I. PACKAGE SURFACE
# ============================================================


class TestPackageSurface:
    """The registry package re-exports its public API."""

    def test_public_api_importable(self) -> None:
        import engine.registry as registry_pkg

        for name in (
            "SCHEMA_VERSION",
            "ExperimentRegistry",
            "ExperimentPersistence",
            "ExperimentPersistenceError",
            "ExperimentNotFoundError",
            "ExperimentAlreadyExistsError",
            "ExperimentIntegrityError",
            "UnsupportedSchemaVersionError",
            "ExperimentRecordHeader",
            "PersistedExperimentRecord",
            "serialize_experiment",
            "deserialize_experiment",
            "parse_record",
            "canonical_json",
            "default_registry_directory",
        ):
            assert hasattr(registry_pkg, name), name

    def test_exception_hierarchy(self) -> None:
        assert issubclass(
            ExperimentNotFoundError, ExperimentPersistenceError
        )
        assert issubclass(
            ExperimentAlreadyExistsError, ExperimentPersistenceError
        )
        assert issubclass(
            ExperimentIntegrityError, ExperimentPersistenceError
        )
        assert issubclass(
            UnsupportedSchemaVersionError, ExperimentPersistenceError
        )
