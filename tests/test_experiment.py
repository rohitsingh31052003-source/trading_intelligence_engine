"""
Tests for the reproducible Research Experiment Framework (Sprint 11J).

Covers:

A. Configuration
   - deterministic experiment IDs
   - same configuration -> same ID
   - changed parameter -> different ID
   - canonical representation stability
   - immutable configuration

B. Runner
   - successful experiment execution
   - dataset resolution (built-in + custom)
   - pipeline invocation
   - research invocation
   - result construction

C. Reproducibility
   - deterministic metadata
   - configuration/hash consistency
   - dataset identity
   - seed handling

D. Reporting
   - required sections exist
   - no misleading claims
   - insufficient evidence is explicitly reported

E. Edge cases
   - empty dataset
   - minimal dataset
   - no completed trades
   - zero OOS trades
   - failed / invalid configuration handling
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest

from engine.config.swing_config import SwingConfig
from engine.experiment import (
    DatasetSpec,
    EvaluationConfig,
    ExperimentComparisonEngine,
    ExperimentConfig,
    ExperimentEvidenceStatus,
    ExperimentRunner,
    dataset_content_hash,
)
from engine.models.experiment import (
    ExperimentResult,
    ExperimentSummary,
    ReproducibilityMetadata,
)
from engine.models.ohlcv import OHLCVCandle
from engine.pipeline.historical_pipeline import PipelineConfig
from engine.research.research import ResearchConfig
from engine.reporting import (
    ExperimentReportFormatter,
)


# ============================================================
# SHARED HELPERS
# ============================================================

_EPOCH = datetime(2025, 1, 1, tzinfo=UTC)


def _candle(close: float, spread: float, index: int) -> OHLCVCandle:
    low = round(close - spread, 2)
    high = round(close + spread, 2)
    open_ = round((low + high) / 2, 2)
    return OHLCVCandle(
        timestamp=_EPOCH + timedelta(days=index),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=1000.0,
    )


def _trending_candles(n: int = 40) -> list[OHLCVCandle]:
    return [_candle(round(100.0 + i * 2, 2), 1.0, i) for i in range(n)]


def _base_config(
    label: str = "test",
    *,
    lookback: int = 2,
    dataset_name: str = "trending",
    sweep: tuple[int, ...] = (2, 3),
    run_research: bool = True,
    seed: int | None = 42,
) -> ExperimentConfig:
    return ExperimentConfig(
        label=label,
        dataset=DatasetSpec(name=dataset_name),
        pipeline=PipelineConfig(
            swing_config=SwingConfig(lookback=lookback),
        ),
        research=ResearchConfig(),
        evaluation=EvaluationConfig(
            parameter_name="swing_lookback" if run_research else None,
            parameter_values=sweep if run_research else (),
            run_out_of_sample=run_research,
            run_walk_forward=run_research,
            run_sensitivity=run_research,
        ),
        strategy_parameters={"swing_lookback": str(lookback)},
        seed=seed,
        metadata={"sprint": "11J"},
    )


# ============================================================
# A. CONFIGURATION
# ============================================================


class TestExperimentConfig:
    """Deterministic experiment identity and immutability."""

    def test_deterministic_experiment_id(self) -> None:
        cfg = _base_config()
        assert cfg.experiment_id.startswith("exp-")
        # Stable across repeated reads.
        assert cfg.experiment_id == cfg.experiment_id

    def test_same_configuration_same_id(self) -> None:
        a = _base_config()
        b = _base_config()
        assert a.experiment_id == b.experiment_id
        assert a.configuration_hash == b.configuration_hash

    def test_changed_pipeline_parameter_different_id(self) -> None:
        a = _base_config(lookback=2)
        b = _base_config(lookback=3)
        assert a.experiment_id != b.experiment_id

    def test_changed_label_different_id(self) -> None:
        a = _base_config(label="alpha")
        b = _base_config(label="beta")
        assert a.experiment_id != b.experiment_id

    def test_changed_dataset_different_id(self) -> None:
        a = _base_config(dataset_name="trending")
        b = _base_config(dataset_name="flat")
        assert a.experiment_id != b.experiment_id

    def test_changed_seed_different_id(self) -> None:
        a = _base_config(seed=42)
        b = _base_config(seed=7)
        assert a.experiment_id != b.experiment_id

    def test_changed_strategy_parameters_different_id(self) -> None:
        a = _base_config()
        b = ExperimentConfig(
            label="test",
            dataset=DatasetSpec(name="trending"),
            pipeline=PipelineConfig(
                swing_config=SwingConfig(lookback=2),
            ),
            research=ResearchConfig(),
            evaluation=EvaluationConfig(
                parameter_name="swing_lookback",
                parameter_values=(2, 3),
            ),
            strategy_parameters={"swing_lookback": "999"},
            seed=42,
            metadata={"sprint": "11J"},
        )
        assert a.experiment_id != b.experiment_id

    def test_changed_metadata_different_id(self) -> None:
        a = _base_config()
        b = ExperimentConfig(
            label="test",
            dataset=DatasetSpec(name="trending"),
            pipeline=PipelineConfig(
                swing_config=SwingConfig(lookback=2),
            ),
            research=ResearchConfig(),
            evaluation=EvaluationConfig(
                parameter_name="swing_lookback",
                parameter_values=(2, 3),
            ),
            strategy_parameters={"swing_lookback": "2"},
            seed=42,
            metadata={"sprint": "11J", "extra": "value"},
        )
        assert a.experiment_id != b.experiment_id

    def test_canonical_representation_stability(self) -> None:
        cfg = _base_config()
        rep = cfg.canonical_representation
        # Deterministic and stable across reads.
        assert rep == cfg.canonical_representation
        # Sorted keys: the canonical form is deterministic JSON.
        assert rep.startswith("{")
        # No nondeterministic markers in the representation.
        for marker in ("datetime.now", "uuid", "random"):
            assert marker not in rep

    def test_canonical_representation_is_json(self) -> None:
        import json

        cfg = _base_config()
        payload = json.loads(cfg.canonical_representation)
        assert isinstance(payload, dict)
        assert payload["label"] == "test"
        assert payload["seed"] == 42

    def test_configuration_hash_is_sha256_hex(self) -> None:
        cfg = _base_config()
        h = cfg.configuration_hash
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_experiment_id_is_prefix_of_hash(self) -> None:
        cfg = _base_config()
        assert cfg.experiment_id == f"exp-{cfg.configuration_hash[:16]}"

    def test_configuration_is_immutable(self) -> None:
        cfg = _base_config()
        with pytest.raises(FrozenInstanceError):
            cfg.label = "mutated"  # type: ignore[misc]

    def test_dataset_spec_is_immutable(self) -> None:
        spec = DatasetSpec(name="trending")
        with pytest.raises(FrozenInstanceError):
            spec.name = "flat"  # type: ignore[misc]

    def test_evaluation_config_is_immutable(self) -> None:
        ev = EvaluationConfig()
        with pytest.raises(FrozenInstanceError):
            ev.run_out_of_sample = False  # type: ignore[misc]

    def test_canonicalization_rejects_lambdas(self) -> None:
        cfg = ExperimentConfig(
            label="bad",
            dataset=DatasetSpec(name="trending"),
            metadata={"fn": "x"},  # safe string metadata
        )
        # Embedding a lambda directly via a dataclass field is not
        # possible for frozen slots configs here, so verify the
        # canonicalizer itself rejects bare callables.
        from engine.experiment.config import _canonicalize

        with pytest.raises(TypeError):
            _canonicalize(lambda: 1)  # noqa: E731

    def test_no_timestamps_in_identity(self) -> None:
        """The experiment ID must never depend on the current time."""

        cfg = _base_config()
        first = cfg.experiment_id
        import time

        time.sleep(0.01)
        assert cfg.experiment_id == first


# ============================================================
# B. RUNNER
# ============================================================


class TestExperimentRunner:
    """Runner orchestration and result construction."""

    def test_successful_experiment_execution(self) -> None:
        runner = ExperimentRunner()
        result = runner.run(_base_config())
        assert isinstance(result, ExperimentResult)
        assert result.experiment_id == _base_config().experiment_id

    def test_dataset_resolution_builtin(self) -> None:
        runner = ExperimentRunner()
        result = runner.run(_base_config(dataset_name="trending"))
        assert result.dataset.name == "trending"
        assert result.dataset.size > 0
        assert result.dataset.content_hash is not None
        assert len(result.dataset.content_hash) == 64

    def test_dataset_resolution_custom(self) -> None:
        runner = ExperimentRunner()
        candles = _trending_candles(n=30)
        cfg = ExperimentConfig(
            label="custom",
            dataset=DatasetSpec(name="custom"),
            pipeline=PipelineConfig(),
            research=ResearchConfig(),
            evaluation=EvaluationConfig(
                run_out_of_sample=False,
                run_walk_forward=False,
                run_sensitivity=False,
            ),
            seed=1,
        )
        result = runner.run(cfg, candles=candles)
        assert result.dataset.name == "custom"
        assert result.dataset.size == 30
        assert result.dataset.content_hash == dataset_content_hash(candles)

    def test_pipeline_invocation(self) -> None:
        runner = ExperimentRunner()
        result = runner.run(_base_config())
        # The evaluation report embeds the pipeline result.
        assert result.evaluation_report is not None
        assert result.evaluation_report.result is not None
        assert result.evaluation_report.pipeline.candles_processed > 0

    def test_research_invocation(self) -> None:
        runner = ExperimentRunner()
        result = runner.run(_base_config())
        report = result.research_report
        # Research report carries regime statistics and leakage.
        assert report is not None
        assert len(report.regime_statistics) > 0
        assert report.leakage is not None

    def test_result_construction_carries_config(self) -> None:
        cfg = _base_config()
        result = ExperimentRunner().run(cfg)
        assert result.config is cfg
        assert result.label == cfg.label
        assert result.metadata == cfg.metadata

    def test_runner_deterministic(self) -> None:
        runner = ExperimentRunner()
        a = runner.run(_base_config())
        b = runner.run(_base_config())
        assert a.experiment_id == b.experiment_id
        assert (
            a.summary.completed_trades == b.summary.completed_trades
        )
        assert (
            a.reproducibility.configuration_hash
            == b.reproducibility.configuration_hash
        )

    def test_runner_no_research_when_disabled(self) -> None:
        runner = ExperimentRunner()
        cfg = ExperimentConfig(
            label="no-research",
            dataset=DatasetSpec(name="trending"),
            pipeline=PipelineConfig(),
            research=ResearchConfig(),
            evaluation=EvaluationConfig(
                run_out_of_sample=False,
                run_walk_forward=False,
                run_sensitivity=False,
            ),
            seed=1,
        )
        result = runner.run(cfg)
        # No OOS / walk-forward / sensitivity performed.
        assert result.research_report.out_of_sample is None
        assert result.research_report.walk_forward_selection is None
        assert result.research_report.parameter_sensitivity is None


# ============================================================
# C. REPRODUCIBILITY
# ============================================================


class TestReproducibility:
    """Reproducibility metadata correctness."""

    def test_reproducibility_metadata_deterministic(self) -> None:
        runner = ExperimentRunner()
        a = runner.run(_base_config())
        b = runner.run(_base_config())
        assert (
            a.reproducibility.configuration_representation
            == b.reproducibility.configuration_representation
        )
        assert (
            a.reproducibility.configuration_hash
            == b.reproducibility.configuration_hash
        )

    def test_reproducibility_consistent_with_config(self) -> None:
        cfg = _base_config()
        result = ExperimentRunner().run(cfg)
        repro = result.reproducibility
        assert repro.experiment_id == cfg.experiment_id
        assert repro.configuration_hash == cfg.configuration_hash
        assert (
            repro.configuration_representation
            == cfg.canonical_representation
        )

    def test_dataset_identity_recorded(self) -> None:
        result = ExperimentRunner().run(_base_config())
        repro = result.reproducibility
        assert repro.dataset_identity == "trending"
        assert repro.dataset_size > 0
        assert repro.dataset_content_hash is not None
        assert len(repro.dataset_content_hash) == 64

    def test_dataset_content_hash_matches_resolved(self) -> None:
        result = ExperimentRunner().run(_base_config())
        assert (
            result.reproducibility.dataset_content_hash
            == result.dataset.content_hash
        )

    def test_seed_recorded(self) -> None:
        result = ExperimentRunner().run(_base_config(seed=42))
        assert result.reproducibility.random_seed == 42
        assert result.reproducibility.has_seed is True

    def test_seed_none_when_not_set(self) -> None:
        cfg = _base_config(seed=None)
        result = ExperimentRunner().run(cfg)
        assert result.reproducibility.random_seed is None
        assert result.reproducibility.has_seed is False

    def test_code_version_known(self) -> None:
        result = ExperimentRunner().run(_base_config())
        # The package is installed, so a version should be known.
        assert result.reproducibility.code_version != "UNKNOWN"

    def test_code_version_unknown_when_uninstalled(self) -> None:
        # The version resolver must return UNKNOWN gracefully.
        from engine.experiment.reproducibility import _code_version

        original = None
        try:
            import engine.experiment.reproducibility as repro_mod
            from importlib.metadata import PackageNotFoundError

            def fake_version(name):
                raise PackageNotFoundError(name)

            original = repro_mod.version
            repro_mod.version = fake_version
            assert _code_version() == "UNKNOWN"
        finally:
            repro_mod.version = original  # type: ignore[attr-defined]

    def test_parameter_values_captured(self) -> None:
        result = ExperimentRunner().run(_base_config())
        params = result.reproducibility.parameter_values
        assert "pipeline.swing.lookback" in params
        assert "strategy.swing_lookback" in params
        assert params["seed"] == "42"

    def test_reproducible_flag(self) -> None:
        result = ExperimentRunner().run(_base_config())
        assert result.reproducibility.reproducible is True

    def test_reproducibility_metadata_immutable(self) -> None:
        repro = ExperimentRunner().run(_base_config()).reproducibility
        assert isinstance(repro, ReproducibilityMetadata)
        with pytest.raises(FrozenInstanceError):
            repro.random_seed = 99  # type: ignore[misc]


# ============================================================
# D. REPORTING
# ============================================================


class TestReporting:
    """Experiment report formatter."""

    REQUIRED_SECTIONS = [
        "Experiment Identity",
        "Dataset",
        "Configuration",
        "Pipeline Summary",
        "Research Summary",
        "Robustness Summary",
        "Walk-Forward Summary",
        "OOS Summary",
        "Data Sufficiency",
        "Leakage Audit",
        "Reproducibility",
        "Research Conclusion",
    ]

    def test_required_sections_exist(self) -> None:
        result = ExperimentRunner().run(_base_config())
        report = ExperimentReportFormatter().format(result)
        for section in self.REQUIRED_SECTIONS:
            assert section in report, f"Missing section: {section}"

    def test_report_contains_experiment_id(self) -> None:
        result = ExperimentRunner().run(_base_config())
        report = ExperimentReportFormatter().format(result)
        assert result.experiment_id in report

    def test_report_contains_label(self) -> None:
        result = ExperimentRunner().run(_base_config(label="my-label"))
        report = ExperimentReportFormatter().format(result)
        assert "my-label" in report

    def test_no_misleading_claims(self) -> None:
        result = ExperimentRunner().run(_base_config())
        report = ExperimentReportFormatter().format(result).lower()
        for forbidden in (
            "is profitable",
            "is best",
            "guaranteed",
            "will profit",
            "risk-free",
        ):
            assert forbidden not in report

    def test_insufficient_evidence_explicitly_reported(self) -> None:
        result = ExperimentRunner().run(_base_config())
        report = ExperimentReportFormatter().format(result)
        # The trending demo yields INSUFFICIENT evidence.
        assert "INSUFFICIENT" in report

    def test_report_deterministic(self) -> None:
        a = ExperimentRunner().run(_base_config())
        b = ExperimentRunner().run(_base_config())
        formatter = ExperimentReportFormatter()
        assert formatter.format(a) == formatter.format(b)


# ============================================================
# E. EDGE CASES
# ============================================================


class TestEdgeCases:
    """Edge cases: empty / minimal / no-trades / zero-OOS."""

    def test_empty_dataset(self) -> None:
        runner = ExperimentRunner()
        cfg = ExperimentConfig(
            label="empty",
            dataset=DatasetSpec(name="custom"),
            pipeline=PipelineConfig(),
            research=ResearchConfig(),
            evaluation=EvaluationConfig(
                run_out_of_sample=False,
                run_walk_forward=False,
                run_sensitivity=False,
            ),
            seed=1,
        )
        result = runner.run(cfg, candles=[])
        assert result.dataset_size == 0
        assert result.summary.completed_trades == 0
        assert (
            result.summary.evidence_status
            == ExperimentEvidenceStatus.INSUFFICIENT
        )

    def test_minimal_dataset(self) -> None:
        runner = ExperimentRunner()
        cfg = ExperimentConfig(
            label="minimal",
            dataset=DatasetSpec(name="minimal"),
            pipeline=PipelineConfig(),
            research=ResearchConfig(),
            evaluation=EvaluationConfig(
                run_out_of_sample=False,
                run_walk_forward=False,
                run_sensitivity=False,
            ),
            seed=1,
        )
        result = runner.run(cfg)
        assert result.dataset.name == "minimal"
        assert result.summary.completed_trades == 0
        assert (
            result.summary.evidence_status
            == ExperimentEvidenceStatus.INSUFFICIENT
        )

    def test_no_completed_trades(self) -> None:
        runner = ExperimentRunner()
        # The flat dataset produces no directional signals.
        cfg = ExperimentConfig(
            label="flat",
            dataset=DatasetSpec(name="flat"),
            pipeline=PipelineConfig(),
            research=ResearchConfig(),
            evaluation=EvaluationConfig(
                run_out_of_sample=False,
                run_walk_forward=False,
                run_sensitivity=False,
            ),
            seed=1,
        )
        result = runner.run(cfg)
        assert result.summary.completed_trades == 0
        assert result.summary.evidence_status.value == "INSUFFICIENT"
        # The summary must still be well-formed.
        assert isinstance(result.summary, ExperimentSummary)

    def test_zero_oos_trades(self) -> None:
        runner = ExperimentRunner()
        result = runner.run(_base_config())
        # The trending demo's evaluation window yields no OOS trades.
        assert result.summary.oos_trades == 0
        assert result.summary.oos_expectancy is not None

    def test_failed_invalid_configuration_custom_without_candles(self) -> None:
        """A custom dataset with no candles yields an empty result,
        never an exception."""

        runner = ExperimentRunner()
        cfg = ExperimentConfig(
            label="custom-no-candles",
            dataset=DatasetSpec(name="custom"),
            pipeline=PipelineConfig(),
            research=ResearchConfig(),
            evaluation=EvaluationConfig(
                run_out_of_sample=False,
                run_walk_forward=False,
                run_sensitivity=False,
            ),
            seed=1,
        )
        result = runner.run(cfg)  # no candles supplied
        assert result.dataset_size == 0
        assert result.summary.completed_trades == 0

    def test_runner_never_raises_on_flat_dataset_with_research(self) -> None:
        runner = ExperimentRunner()
        cfg = ExperimentConfig(
            label="flat-full",
            dataset=DatasetSpec(name="flat"),
            pipeline=PipelineConfig(),
            research=ResearchConfig(),
            evaluation=EvaluationConfig(
                parameter_name="swing_lookback",
                parameter_values=(2, 3),
            ),
            seed=1,
        )
        result = runner.run(cfg)
        assert result is not None
        assert result.summary.evidence_status.value == "INSUFFICIENT"


# ============================================================
# DATASET CONTENT HASH
# ============================================================


class TestDatasetContentHash:
    """Deterministic dataset content hashing."""

    def test_same_candles_same_hash(self) -> None:
        a = _trending_candles()
        b = _trending_candles()
        assert dataset_content_hash(a) == dataset_content_hash(b)

    def test_different_candles_different_hash(self) -> None:
        a = _trending_candles(n=30)
        b = _trending_candles(n=31)
        assert dataset_content_hash(a) != dataset_content_hash(b)

    def test_hash_is_hex_64(self) -> None:
        h = dataset_content_hash(_trending_candles())
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_empty_candles_stable_hash(self) -> None:
        assert dataset_content_hash([]) == dataset_content_hash([])
