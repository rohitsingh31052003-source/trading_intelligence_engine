"""
Experiment runner (Sprint 11J).

The ``ExperimentRunner`` orchestrates the existing historical
evaluation pipeline, evaluation reporting layer and research /
robustness layer into a single reproducible
``ExperimentResult``.

The runner performs orchestration ONLY. It implements no
trading, validation, performance, pipeline or research logic.
Every engine is reused as-is:

* ``HistoricalEvaluationPipeline``  (Sprint 11F)
* ``EvaluationReportEngine``        (Sprint 11G)
* ``ResearchEngine``                (Sprint 11H/11I)

The runner builds the evaluators required by the research
engine (pipeline evaluator, parameter evaluator, walk-forward
evaluator) from the experiment configuration, then delegates.

Design rules:

* No duplication of any existing logic.
* Deterministic: identical config + dataset -> identical result.
* Graceful on edge cases (empty / minimal dataset, no trades,
  zero OOS trades) -- never raises; produces an honest result
  with an INSUFFICIENT evidence status.
* No print() inside the runner.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Sequence

from engine.config.swing_config import SwingConfig
from engine.experiment.config import ExperimentConfig
from engine.experiment.reproducibility import (
    build_reproducibility_metadata,
    dataset_content_hash,
)
from engine.models.experiment import (
    DatasetSpec,
    ExperimentEvidenceStatus,
    ExperimentResult,
    ExperimentSummary,
)
from engine.models.ohlcv import OHLCVCandle
from engine.pipeline.datasets import (
    flat_dataset,
    minimal_dataset,
    trending_dataset,
)
from engine.pipeline.historical_pipeline import (
    HistoricalEvaluationPipeline,
    PipelineConfig,
)
from engine.reporting.evaluation import EvaluationReportEngine
from engine.research.research import ResearchConfig, ResearchEngine


# Built-in deterministic dataset resolvers keyed by name.
_BUILTIN_DATASETS: dict[
    str, Callable[[], list[OHLCVCandle]]
] = {
    "trending": trending_dataset,
    "flat": flat_dataset,
    "minimal": minimal_dataset,
}


# ============================================================
# RUNNER
# ============================================================


class ExperimentRunner:
    """
    Orchestrate a complete, reproducible research experiment.

    Public API:

        run(config, candles=None) -> ExperimentResult

    The runner is stateless across calls: identical inputs
    always produce identical outputs.
    """

    def __init__(self) -> None:
        self._evaluation_engine = EvaluationReportEngine()

    # ========================================================
    # PUBLIC API
    # ========================================================

    def run(
        self,
        config: ExperimentConfig,
        candles: Sequence[OHLCVCandle] | None = None,
    ) -> ExperimentResult:
        """
        Execute a complete experiment.

        Parameters:

        config
            The immutable ``ExperimentConfig``.

        candles
            Optional explicit candle sequence for a custom
            dataset. Required when the dataset name is not a
            built-in; ignored for built-in datasets (which are
            resolved deterministically from the name).
        """

        resolved = self._resolve_dataset(config, candles)

        dataset_candles, dataset_spec, dataset_size = resolved

        # 1. Historical evaluation pipeline (reused as-is).
        pipeline_result = self._run_pipeline(
            config.pipeline,
            dataset_candles,
        )

        # 2. Evaluation report (reused as-is).
        evaluation_report = self._evaluation_engine.analyze(
            pipeline_result,
            label=config.label,
            metadata=config.metadata,
        )

        # 3. Research analysis (reused as-is).
        research_report = self._run_research(
            config,
            pipeline_result,
            dataset_candles,
        )

        # 4. Reproducibility metadata.
        reproducibility = build_reproducibility_metadata(
            config=config,
            dataset=dataset_spec,
            dataset_size=dataset_size,
            actual_content_hash=dataset_spec.content_hash
            or dataset_content_hash(dataset_candles),
        )

        # 5. Summary + evidence status.
        summary = self._build_summary(
            evaluation_report,
            research_report,
            config,
        )

        return ExperimentResult(
            experiment_id=config.experiment_id,
            config=config,
            dataset=dataset_spec,
            dataset_size=dataset_size,
            evaluation_report=evaluation_report,
            research_report=research_report,
            reproducibility=reproducibility,
            summary=summary,
            label=config.label,
            metadata=dict(config.metadata),
        )

    # ========================================================
    # DATASET RESOLUTION
    # ========================================================

    def _resolve_dataset(
        self,
        config: ExperimentConfig,
        candles: Sequence[OHLCVCandle] | None,
    ) -> tuple[list[OHLCVCandle], DatasetSpec, int]:
        """
        Resolve the dataset from the config's ``DatasetSpec``.

        Built-in datasets are produced deterministically from
        their name. Custom datasets require an explicit candle
        sequence. The actual content hash is always computed
        and stored on the resolved spec.
        """

        spec = config.dataset
        name = spec.name

        if name in _BUILTIN_DATASETS:
            resolved_candles = list(_BUILTIN_DATASETS[name]())
        else:
            if candles is None:
                resolved_candles = []
            else:
                resolved_candles = list(candles)

        actual_hash = dataset_content_hash(resolved_candles)

        resolved_spec = DatasetSpec(
            name=name,
            content_hash=actual_hash,
            size=len(resolved_candles),
        )

        return resolved_candles, resolved_spec, len(resolved_candles)

    # ========================================================
    # PIPELINE
    # ========================================================

    def _run_pipeline(
        self,
        pipeline_config: PipelineConfig,
        candles: list[OHLCVCandle],
    ) -> Any:
        """
        Run the existing historical evaluation pipeline.
        """

        pipeline = HistoricalEvaluationPipeline(pipeline_config)
        return pipeline.evaluate(candles)

    # ========================================================
    # RESEARCH
    # ========================================================

    def _run_research(
        self,
        config: ExperimentConfig,
        pipeline_result: Any,
        candles: list[OHLCVCandle],
    ) -> Any:
        """
        Run the existing research engine with evaluators built
        from the experiment configuration.
        """

        research_config = self._research_config(config)

        engine = ResearchEngine(research_config)

        pipeline_evaluator = self._build_pipeline_evaluator(config)
        parameter_evaluator = self._build_parameter_evaluator(
            config,
            candles,
        )
        walk_forward_evaluator = self._build_walk_forward_evaluator(
            config,
            candles,
        )

        evaluation = config.evaluation

        return engine.analyze(
            pipeline_result,
            candles,
            pipeline_evaluator=(
                pipeline_evaluator
                if evaluation.run_out_of_sample
                else None
            ),
            parameter_evaluator=(
                parameter_evaluator
                if evaluation.run_sensitivity
                else None
            ),
            walk_forward_evaluator=(
                walk_forward_evaluator
                if evaluation.run_walk_forward
                else None
            ),
            label=config.label,
            metadata=config.metadata,
        )

    def _research_config(self, config: ExperimentConfig) -> ResearchConfig:
        """
        Project the experiment's evaluation config onto the
        research config's sensitivity sweep fields.

        The research engine reads
        ``sensitivity_parameter_name`` / ``sensitivity_parameter_values``
        from its own config. We mirror the experiment's
        evaluation parameter sweep so the existing engine runs
        unchanged.
        """

        research = config.research
        evaluation = config.evaluation

        if (
            evaluation.parameter_name is not None
            and evaluation.parameter_values
        ):
            return replace(
                research,
                sensitivity_parameter_name=evaluation.parameter_name,
                sensitivity_parameter_values=tuple(
                    evaluation.parameter_values
                ),
            )

        return research

    # -----------------------------------------------------
    # EVALUATOR FACTORIES
    # -----------------------------------------------------
    #
    # The research engine requires caller-supplied evaluators.
    # These build fresh pipelines from the experiment's pipeline
    # config (with the swept parameter applied) and delegate to
    # the existing engines. No trading logic is reimplemented.

    def _build_pipeline_evaluator(
        self,
        config: ExperimentConfig,
    ) -> Callable[[Sequence[OHLCVCandle]], Any] | None:
        """
        Build a pipeline evaluator that reuses the existing
        ``HistoricalEvaluationPipeline`` with the experiment's
        pipeline config.
        """

        pipeline_config = config.pipeline

        def evaluator(cs: Sequence[OHLCVCandle]) -> Any:
            return HistoricalEvaluationPipeline(
                pipeline_config,
            ).evaluate(cs)

        return evaluator

    def _build_parameter_evaluator(
        self,
        config: ExperimentConfig,
        candles: list[OHLCVCandle],
    ) -> Callable[[Any], Any] | None:
        """
        Build a parameter evaluator for the sensitivity sweep.

        The swept parameter is the swing lookback (the only
        strategy parameter currently exposed for sweeping). The
        evaluator builds a fresh pipeline with the new lookback
        and evaluates the SAME candles.
        """

        if (
            config.evaluation.parameter_name is None
            or not config.evaluation.parameter_values
        ):
            return None

        base_pipeline = config.pipeline

        def evaluator(value: Any) -> Any:
            pipeline_config = replace(
                base_pipeline,
                swing_config=SwingConfig(lookback=int(value)),
            )
            return HistoricalEvaluationPipeline(
                pipeline_config,
            ).evaluate(candles)

        return evaluator

    def _build_walk_forward_evaluator(
        self,
        config: ExperimentConfig,
        candles: list[OHLCVCandle],
    ) -> Callable[[Sequence[OHLCVCandle], Any], Any] | None:
        """
        Build a walk-forward evaluator.

        The walk-forward evaluator receives a candle slice AND
        a parameter value (unlike the parameter evaluator). It
        builds a fresh pipeline with the new lookback and
        evaluates the supplied slice only.
        """

        if (
            config.evaluation.parameter_name is None
            or not config.evaluation.parameter_values
        ):
            return None

        base_pipeline = config.pipeline

        def evaluator(
            cs: Sequence[OHLCVCandle],
            value: Any,
        ) -> Any:
            pipeline_config = replace(
                base_pipeline,
                swing_config=SwingConfig(lookback=int(value)),
            )
            return HistoricalEvaluationPipeline(
                pipeline_config,
            ).evaluate(cs)

        return evaluator

    # ========================================================
    # SUMMARY + EVIDENCE STATUS
    # ========================================================

    def _build_summary(
        self,
        evaluation_report: Any,
        research_report: Any,
        config: ExperimentConfig,
    ) -> ExperimentSummary:
        """
        Project a flat summary from the reused reports.

        Nothing is recomputed; authoritative values are read
        directly from the evaluation and research reports.
        """

        trades = evaluation_report.trades
        performance = trades.performance

        completed_trades = trades.completed_trades
        win_rate = trades.win_rate
        expectancy = trades.expectancy
        total_r = trades.total_r
        profit_factor = trades.profit_factor
        max_drawdown_r = trades.max_drawdown_r

        robustness = research_report.parameter_robustness
        robust: bool | None = None
        descriptive_best: Any = None
        if robustness is not None and not robustness.is_empty:
            robust = robustness.robust
            descriptive_best = robustness.descriptive_best

        oos = research_report.out_of_sample
        wf = research_report.walk_forward_selection

        oos_expectancy: float | None = None
        oos_trades = 0

        if wf is not None and wf.has_selected:
            oos_perf = wf.out_of_sample_performance
            oos_expectancy = _safe_float(
                getattr(oos_perf, "expectancy", None),
            )
            oos_trades = wf.out_of_sample_completed_trades
        elif oos is not None:
            oos_perf = oos.out_of_sample_performance
            oos_expectancy = _safe_float(
                getattr(oos_perf, "expectancy", None),
            )
            oos_trades = int(
                getattr(oos_perf, "completed_trades", 0),
            )

        data_sufficiency = research_report.data_sufficiency
        data_sufficient = bool(
            data_sufficiency.sufficient_for_inference
            if data_sufficiency is not None
            else False,
        )

        leakage = research_report.leakage
        leakage_passed: bool | None = None
        leakage_not_verified = False
        if leakage is not None:
            leakage_passed = leakage.passed
            leakage_not_verified = bool(leakage.has_not_verified)

        evidence_status = self._evidence_status(
            completed_trades=completed_trades,
            data_sufficiency=data_sufficiency,
            min_trades=config.research.min_trades_for_inference,
        )

        return ExperimentSummary(
            completed_trades=completed_trades,
            win_rate=win_rate,
            expectancy=expectancy,
            total_r=total_r,
            profit_factor=profit_factor,
            max_drawdown_r=max_drawdown_r,
            robust=robust,
            descriptive_best=descriptive_best,
            oos_expectancy=oos_expectancy,
            oos_trades=oos_trades,
            data_sufficient=data_sufficient,
            leakage_passed=leakage_passed,
            leakage_not_verified=leakage_not_verified,
            evidence_status=evidence_status,
        )

    def _evidence_status(
        self,
        completed_trades: int,
        data_sufficiency: Any,
        min_trades: int,
    ) -> ExperimentEvidenceStatus:
        """
        Classify the sufficiency of the experiment's evidence.

        INSUFFICIENT: fewer completed trades than the minimum
        for inference.
        SUFFICIENT: the data-sufficiency gate passed.
        PARTIAL: enough trades for a basic inference but at
        least one secondary evidence source is insufficient.
        """

        if completed_trades < min_trades:
            return ExperimentEvidenceStatus.INSUFFICIENT

        if data_sufficiency is None:
            return ExperimentEvidenceStatus.PARTIAL

        if data_sufficiency.sufficient_for_inference:
            return ExperimentEvidenceStatus.SUFFICIENT

        return ExperimentEvidenceStatus.PARTIAL


def _safe_float(value: Any) -> float | None:
    """
    Coerce a value to float, returning None when not possible.
    """

    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None
