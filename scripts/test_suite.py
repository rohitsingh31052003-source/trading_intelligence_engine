"""
Demo script for the Experiment Suite / Batch Orchestration &
Analysis Layer (Sprint 11M).

Demonstrates the full suite workflow:

1. define a 2x2 experiment suite (2 datasets x 2 lookbacks) as an
   ordered collection of ExperimentConfig members,
2. run the suite via SuiteRunner (which reuses ExperimentRunner to run
   each member and ExperimentRegistry to persist each member),
3. register the suite manifest (a thin artifact over existing
   experiment persistence),
4. list / load the suite back from persistence,
5. summarize and compare suites from persisted data,
6. produce formatted suite + suite comparison reports,
7. PROVE the analysis uses PERSISTED results rather than rerunning the
   pipeline (the pipeline is patched to raise on any call).

The suite layer never reruns the trading pipeline merely to load or
analyse a suite. This is made explicit in the output.
"""

import os
import sys
import tempfile
from pathlib import Path

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
    SuiteConfig,
    SuiteRegistry,
    SuiteRunner,
)


SEPARATOR = "=" * 60
SUB_SEPARATOR = "-" * 60


def _member(label: str, lookback: int, dataset_name: str) -> ExperimentConfig:
    """A fast member config with optional research analyses OFF."""

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
        seed=42,
        metadata={"sprint": "11M"},
    )


def main() -> None:
    tmp_dir = tempfile.mkdtemp(prefix="suite-demo-")
    registry = ExperimentRegistry(tmp_dir)
    suite_registry = SuiteRegistry(experiment_registry=registry)
    runner = SuiteRunner(experiment_registry=registry)

    print(SEPARATOR)
    print("Sprint 11M — Experiment Suite / Batch Orchestration Demo")
    print(SEPARATOR)

    # ----------------------------------------------------------
    # 1. Define a 2x2 suite (2 datasets x 2 lookbacks).
    # ----------------------------------------------------------
    members = (
        _member("trending-lookback-2", 2, "trending"),
        _member("trending-lookback-3", 3, "trending"),
        _member("flat-lookback-2", 2, "flat"),
        _member("flat-lookback-3", 3, "flat"),
    )

    suite = SuiteConfig(
        label="grid-2x2",
        members=members,
        metadata={"sprint": "11M", "kind": "grid"},
    )

    print()
    print(SUB_SEPARATOR)
    print("1. Suite definition")
    print(SUB_SEPARATOR)
    print(f"Suite ID            : {suite.suite_id}")
    print(f"Label               : {suite.label}")
    print(f"Member count        : {suite.member_count}")
    print(f"Configuration hash  : {suite.configuration_hash[:16]}...")
    for index, member in enumerate(members):
        print(
            f"  [{index}] {member.label} "
            f"(dataset={member.dataset.name}, "
            f"experiment_id={member.experiment_id})"
        )

    # ----------------------------------------------------------
    # 2. Run the suite (members run + persisted via reused engines).
    # ----------------------------------------------------------
    print()
    print(SUB_SEPARATOR)
    print("2. Run the suite (reuses ExperimentRunner + ExperimentRegistry)")
    print(SUB_SEPARATOR)
    result = runner.run(suite, register=True)

    print(f"Members run         : {result.member_count}")
    print(f"Has SUFFICIENT      : {result.has_sufficient_evidence}")
    print(f"Member experiment IDs:")
    for member in result.members:
        print(
            f"  - {member.experiment_id} [{member.label}] "
            f"evidence={member.evidence_status.value} "
            f"trades={member.summary.completed_trades}"
        )

    # ----------------------------------------------------------
    # 3. Register the suite manifest (thin artifact over experiment
    #    persistence).
    # ----------------------------------------------------------
    print()
    print(SUB_SEPARATOR)
    print("3. Register the suite manifest")
    print(SUB_SEPARATOR)
    suite_registry.register_suite(result)
    print(f"Registered suite    : {suite.suite_id}")
    print(f"Stored suites       : {suite_registry.list()}")
    print(
        f"Experiment records  : {registry.list()} "
        f"(member data stays in the Sprint 11K experiment registry)"
    )

    # ----------------------------------------------------------
    # 4. Load the suite back from persistence.
    # ----------------------------------------------------------
    print()
    print(SUB_SEPARATOR)
    print("4. Load the suite back from persistence")
    print(SUB_SEPARATOR)
    loaded = suite_registry.load_suite(suite.suite_id)
    print(f"Loaded suite ID     : {loaded.suite_id}")
    print(f"Loaded member count : {loaded.member_count}")
    print(f"Loaded label        : {loaded.label}")

    # ----------------------------------------------------------
    # 5. Summarize and compare suites from persisted data.
    # ----------------------------------------------------------
    print()
    print(SUB_SEPARATOR)
    print("5. Summarize suite (from persisted data)")
    print(SUB_SEPARATOR)
    summary = suite_registry.summarize_suite(suite.suite_id)
    print(f"Member count        : {summary.member_count}")
    print(f"Sufficient          : {summary.sufficient_count}")
    print(f"Partial             : {summary.partial_count}")
    print(f"Insufficient        : {summary.insufficient_count}")
    print(f"Has SUFFICIENT      : {summary.has_sufficient_evidence}")

    # Define a second (1x1) suite for comparison.
    members_b = (_member("flat-lookback-2", 2, "flat"),)
    suite_b = SuiteConfig(
        label="single-flat",
        members=members_b,
        metadata={"sprint": "11M", "kind": "single"},
    )
    result_b = runner.run(suite_b, register=True)
    suite_registry.register_suite(result_b)

    print()
    print(SUB_SEPARATOR)
    print("6. Compare suites (from persisted data)")
    print(SUB_SEPARATOR)
    comparison = suite_registry.compare_suites([suite.suite_id, suite_b.suite_id])
    print(f"Compared suites     : {comparison.suite_count}")
    print(f"Sufficient suites   : {len(comparison.sufficient_suites)}")
    print(
        f"Best suite by member expectancy: "
        f"{comparison.best_suite_by_member_expectancy}"
    )

    # ----------------------------------------------------------
    # 7. Formatted reports.
    # ----------------------------------------------------------
    print()
    print(SUB_SEPARATOR)
    print("7. Formatted suite report")
    print(SUB_SEPARATOR)
    print(SuiteReportFormatter().format(result))

    print()
    print(SUB_SEPARATOR)
    print("8. Formatted suite comparison report")
    print(SUB_SEPARATOR)
    print(SuiteComparisonFormatter().format(comparison))

    # ----------------------------------------------------------
    # 9. PROOF: persisted-data-only (no pipeline rerun).
    # ----------------------------------------------------------
    print()
    print(SUB_SEPARATOR)
    print("9. PROOF: load/summarize/compare use PERSISTED data only")
    print(SUB_SEPARATOR)
    print("Patching HistoricalEvaluationPipeline.evaluate to raise...")

    original_evaluate = HistoricalEvaluationPipeline.evaluate

    def _boom(self, candles):  # type: ignore[no-redef]
        raise RuntimeError(
            "Pipeline must not be rerun to load/analyse a suite."
        )

    HistoricalEvaluationPipeline.evaluate = _boom  # type: ignore[assignment]
    try:
        loaded2 = suite_registry.load_suite(suite.suite_id)
        summary2 = suite_registry.summarize_suite(suite.suite_id)
        comparison2 = suite_registry.compare_suites(
            [suite.suite_id, suite_b.suite_id]
        )
        print(
            f"load_suite OK (member_count={loaded2.member_count}) "
            f"-- pipeline was NOT rerun."
        )
        print(
            f"summarize_suite OK (member_count={summary2.member_count}) "
            f"-- pipeline was NOT rerun."
        )
        print(
            f"compare_suites OK (suite_count={comparison2.suite_count}) "
            f"-- pipeline was NOT rerun."
        )
    finally:
        HistoricalEvaluationPipeline.evaluate = original_evaluate  # type: ignore[assignment]

    print()
    print(SEPARATOR)
    print("Sprint 11M demo complete.")
    print("Suite layer reuses ExperimentRunner, ExperimentRegistry,")
    print("ExperimentQueryEngine and ExperimentComparisonEngine; no")
    print("trading/persistence/query/comparison logic is duplicated.")
    print(SEPARATOR)


if __name__ == "__main__":
    main()
