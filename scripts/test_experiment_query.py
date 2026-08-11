"""
Demo script for the experiment query / filtering / analysis layer
(Sprint 11L).

Demonstrates the full persisted-query workflow:

1. create / run several experiments using the existing experiment
   framework (Sprint 11J),
2. save them to the registry (Sprint 11K),
3. query them back from persistence (no trading pipeline rerun),
4. filter them (by dataset, evidence status, parameters, bounds),
5. sort them (ascending / descending, multiple metrics),
6. group them (by dataset / evidence status / configuration),
7. produce a structured analysis summary,
8. prove the analysis uses PERSISTED results rather than rerunning
   the pipeline (the pipeline is patched to raise on any call).

The query layer never reruns the trading pipeline merely to answer
a query. This is made explicit in the output.
"""

import os
import sys
import tempfile
from dataclasses import replace

sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "src",
    ),
)

from engine.config.swing_config import SwingConfig
from engine.experiment import (
    DatasetSpec,
    EvaluationConfig,
    ExperimentConfig,
    ExperimentEvidenceStatus,
    ExperimentRunner,
)
from engine.pipeline.historical_pipeline import PipelineConfig
from engine.query import (
    ExperimentFilter,
    ExperimentGroupDimension,
    ExperimentQueryEngine,
    ExperimentSortKey,
    SortOrder,
)
from engine.registry import ExperimentRegistry
from engine.reporting import (
    ExperimentAnalysisFormatter,
    ExperimentGroupingFormatter,
    ExperimentQueryFormatter,
)
from engine.research.research import ResearchConfig


def _config(
    label: str,
    lookback: int,
    dataset_name: str = "trending",
) -> ExperimentConfig:
    """Build a deterministic experiment configuration."""

    return ExperimentConfig(
        label=label,
        dataset=DatasetSpec(name=dataset_name),
        pipeline=PipelineConfig(
            swing_config=SwingConfig(lookback=lookback),
        ),
        research=ResearchConfig(),
        evaluation=EvaluationConfig(
            parameter_name="swing_lookback",
            parameter_values=(2, 3, 4),
        ),
        strategy_parameters={"swing_lookback": str(lookback)},
        seed=42,
        metadata={"sprint": "11L"},
    )


def _with_evidence(result, status, **metrics):
    """
    Synthetically set an evidence status (and optional metrics).

    Enforces the SAME invariant the real ``ExperimentRunner`` produces:
    ``evidence_status == SUFFICIENT`` iff ``data_sufficient == True`` (both
    are projections of ``DataSufficiencyReport.sufficient_for_inference``).
    The real runner can never emit ``SUFFICIENT`` with ``data_sufficient``
    False, so a synthetic fixture must not either. ``data_sufficient`` is
    derived from ``status`` here unless the caller passes it explicitly.
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


def main() -> None:

    runner = ExperimentRunner()

    # Run a handful of experiments across two datasets and two
    # lookbacks so the query layer has something meaningful to
    # filter / sort / group.
    a = runner.run(_config("trending-lookback-2", 2, "trending"))
    b = runner.run(_config("trending-lookback-3", 3, "trending"))
    c = runner.run(_config("flat-lookback-2", 2, "flat"))
    d = runner.run(_config("flat-lookback-3", 3, "flat"))

    # Synthesize a spread of evidence statuses so the
    # "no-best-without-sufficient" guarantee is visible. One
    # SUFFICIENT, one PARTIAL, two INSUFFICIENT.
    a = _with_evidence(a, ExperimentEvidenceStatus.PARTIAL)
    b = _with_evidence(
        b,
        ExperimentEvidenceStatus.SUFFICIENT,
        expectancy=0.62,
        total_r=3.1,
        max_drawdown_r=0.9,
    )
    c = _with_evidence(c, ExperimentEvidenceStatus.INSUFFICIENT)
    d = _with_evidence(d, ExperimentEvidenceStatus.INSUFFICIENT)

    with tempfile.TemporaryDirectory() as storage:
        registry = ExperimentRegistry(storage)
        engine = ExperimentQueryEngine(registry)

        print("=" * 60)
        print("Sprint 11L - Experiment Query & Analysis Demo")
        print("=" * 60)
        print()
        print("Step 1: Running four experiments through the existing")
        print("        experiment framework (Sprint 11J) and saving")
        print("        them to the registry (Sprint 11K)...")

        for result in (a, b, c, d):
            registry.register(result)
            print(
                f"  - {result.experiment_id} ({result.label}) "
                f"evidence={result.evidence_status.value}"
            )
        print()

        # -------------------------------------------------
        # 2. QUERY everything back (no pipeline rerun).
        # -------------------------------------------------

        print("Step 2: Querying all persisted experiments back")
        print("        (uses persisted results, NOT a rerun)...")
        rows = engine.query()
        print(f"  - matched {len(rows)} experiment(s)")
        print()

        # -------------------------------------------------
        # 3. FILTER.
        # -------------------------------------------------

        print("Step 3: Filtering persisted experiments...")

        trending_filter = ExperimentFilter(dataset_name="trending")
        trending_rows = engine.query(trending_filter)
        print(
            f"  - dataset=trending -> {len(trending_rows)} experiment(s)"
        )

        sufficient_filter = ExperimentFilter(
            evidence_status=ExperimentEvidenceStatus.SUFFICIENT,
        )
        sufficient_rows = engine.query(sufficient_filter)
        print(
            f"  - evidence=SUFFICIENT -> "
            f"{len(sufficient_rows)} experiment(s)"
        )

        param_filter = ExperimentFilter(
            parameter_values={"strategy.swing_lookback": "3"},
        )
        param_rows = engine.query(param_filter)
        print(
            f"  - parameter swing_lookback=3 -> "
            f"{len(param_rows)} experiment(s)"
        )

        bounds_filter = ExperimentFilter(min_expectancy=0.5)
        bounds_rows = engine.query(bounds_filter)
        print(
            f"  - expectancy>=0.5 -> "
            f"{len(bounds_rows)} experiment(s)"
        )
        print()

        # -------------------------------------------------
        # 4. SORT (ascending / descending, multiple metrics).
        # -------------------------------------------------

        print("Step 4: Sorting persisted experiments...")
        by_exp_desc = engine.query(
            sort_key=ExperimentSortKey.EXPECTANCY,
            sort_order=SortOrder.DESCENDING,
        )
        print("  - by expectancy (descending):")
        for row in by_exp_desc:
            print(
                f"      {row.experiment_id}  exp={row.expectancy:.4f}  "
                f"({row.evidence_status.value})"
            )

        by_trades_asc = engine.query(
            sort_key=ExperimentSortKey.COMPLETED_TRADES,
            sort_order=SortOrder.ASCENDING,
        )
        print("  - by completed trades (ascending):")
        for row in by_trades_asc:
            print(
                f"      {row.experiment_id}  trades={row.completed_trades}"
            )
        print()

        # -------------------------------------------------
        # 5. GROUP.
        # -------------------------------------------------

        print("Step 5: Grouping persisted experiments...")
        by_dataset = engine.group(
            dimension=ExperimentGroupDimension.DATASET,
        )
        print("  - by dataset:")
        for group in by_dataset.groups:
            print(
                f"      {group.name}: {group.experiment_count} "
                f"(S={group.sufficient_count}, "
                f"P={group.partial_count}, "
                f"I={group.insufficient_count})"
            )

        by_evidence = engine.group(
            dimension=ExperimentGroupDimension.EVIDENCE_STATUS,
        )
        print("  - by evidence status:")
        for group in by_evidence.groups:
            print(f"      {group.name}: {group.experiment_count}")
        print()

        # -------------------------------------------------
        # 6. ANALYSIS SUMMARY.
        # -------------------------------------------------

        print("Step 6: Producing an analysis summary...")
        summary = engine.summarize()
        print(
            f"  - total={summary.total_experiments} "
            f"sufficient={summary.sufficient_count} "
            f"partial={summary.partial_count} "
            f"insufficient={summary.insufficient_count}"
        )
        if summary.best_by_expectancy is not None:
            print(
                f"  - best by expectancy (SUFFICIENT only): "
                f"{summary.best_by_expectancy.experiment_id} "
                f"({summary.best_by_expectancy.value:.4f} R)"
            )
        else:
            print("  - no descriptive best (no SUFFICIENT evidence)")
        print()

        # -------------------------------------------------
        # 7. FORMATTED REPORTS.
        # -------------------------------------------------

        print("Step 7: Formatted reports (from persisted data):")
        print()
        print(ExperimentQueryFormatter().format(by_exp_desc))
        print(ExperimentGroupingFormatter().format(by_dataset))
        print(ExperimentAnalysisFormatter().format(summary))

        # -------------------------------------------------
        # 8. PROVE the analysis uses persisted data only.
        # -------------------------------------------------

        print("Step 8: Proving the query layer does NOT rerun the")
        print("        trading pipeline (patched to raise)...")
        import engine.pipeline.historical_pipeline as pipeline_mod

        def explode(*args, **kwargs):
            raise AssertionError(
                "Query must use persisted results, not rerun the pipeline."
            )

        # Patch the pipeline so any accidental rerun would explode.
        original = pipeline_mod.HistoricalEvaluationPipeline.evaluate
        pipeline_mod.HistoricalEvaluationPipeline.evaluate = explode  # type: ignore

        try:
            rows = engine.query()
            grouping = engine.group(
                dimension=ExperimentGroupDimension.EVIDENCE_STATUS,
            )
            summary = engine.summarize()
            print(
                f"  - query/group/summarize succeeded with the pipeline"
                f" disabled: {len(rows)} rows, {grouping.total_groups} "
                f"groups, best_by_expectancy="
                f"{summary.best_by_expectancy}"
            )
            print("  - CONFIRMED: analysis uses persisted results only.")
        finally:
            pipeline_mod.HistoricalEvaluationPipeline.evaluate = original  # type: ignore

        print()
        print("=" * 60)
        print("Demo complete. All queries used persisted results.")
        print("=" * 60)


if __name__ == "__main__":
    main()
