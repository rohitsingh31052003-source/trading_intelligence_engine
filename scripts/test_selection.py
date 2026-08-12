"""
Demo script for the Experiment Selection & Promotion Layer (Sprint 11N).

Demonstrates the full selection workflow:

1. create / run representative experiments using the EXISTING experiment
   framework (Sprint 11J),
2. persist them through the EXISTING registry (Sprint 11K),
   including an INSUFFICIENT experiment, a PARTIAL experiment, a
   SUFFICIENT experiment and MULTIPLE sufficient candidates where
   ranking matters,
3. run selection using persisted data,
4. show candidates,
5. show rejected experiments and reasons,
6. show the selected experiment,
7. demonstrate suite-level selection (Sprint 11M suites),
8. PROVE selection still works after patching
   HistoricalEvaluationPipeline.evaluate() to raise,
9. PROVE ExperimentRunner is not invoked during persisted selection,
10. print a clear descriptive-only warning.

The selection layer never reruns the trading pipeline, the experiment
runner or the suite runner merely to make or load a selection. This is
made explicit in the output. The demo exits successfully.
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
from engine.experiment.runner import ExperimentRunner as _Runner
from engine.models.experiment import ExperimentResult
from engine.pipeline.historical_pipeline import (
    HistoricalEvaluationPipeline,
    PipelineConfig,
)
from engine.registry import ExperimentRegistry
from engine.reporting import SelectionReportFormatter
from engine.research.research import ResearchConfig
from engine.selection import (
    SelectionCriteria,
    SelectionEngine,
    SelectionRegistry,
    SuiteSelectionEngine,
)
from engine.suite import (
    SuiteConfig,
    SuiteRegistry,
    SuiteRunner,
)


SEPARATOR = "=" * 60
SUB_SEPARATOR = "-" * 60


def _config(label: str, lookback: int, dataset_name: str = "trending") -> ExperimentConfig:
    """A fast config with optional research analyses OFF for speed."""

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
        metadata={"sprint": "11N"},
    )


def _with_evidence(
    result: ExperimentResult,
    status: ExperimentEvidenceStatus,
    **metrics,
) -> ExperimentResult:
    """Override the evidence status (and optional metrics) of a result."""

    implied = status == ExperimentEvidenceStatus.SUFFICIENT
    overrides = {"data_sufficient": implied}
    overrides.update(metrics)
    summary = replace(result.summary, evidence_status=status, **overrides)
    return replace(result, summary=summary)


def _register(registry: ExperimentRegistry, result: ExperimentResult) -> None:
    registry.register(result, overwrite=True)


def main() -> None:
    tmp_dir = tempfile.mkdtemp(prefix="selection-demo-")
    registry = ExperimentRegistry(tmp_dir)
    suite_registry = SuiteRegistry(experiment_registry=registry)
    suite_runner = SuiteRunner(experiment_registry=registry)
    selection_registry = SelectionRegistry(tmp_dir)

    print(SEPARATOR)
    print("Sprint 11N — Experiment Selection & Promotion Demo")
    print(SEPARATOR)

    # ----------------------------------------------------------
    # 1. Run representative experiments via the EXISTING framework.
    # ----------------------------------------------------------
    print()
    print(SUB_SEPARATOR)
    print("1. Run representative experiments (existing framework)")
    print(SUB_SEPARATOR)

    runner = ExperimentRunner()
    r_insuff = runner.run(_config("insufficient-low-trades", lookback=2))
    r_partial = runner.run(_config("partial-evidence", lookback=3))
    r_suff_a = runner.run(_config("sufficient-a", lookback=2))
    r_suff_b = runner.run(_config("sufficient-b", lookback=3))
    r_suff_c = runner.run(_config("sufficient-c", lookback=2))

    # Synthesize a spread of evidence statuses (mirrors test helpers).
    r_insuff = _with_evidence(
        r_insuff, ExperimentEvidenceStatus.INSUFFICIENT, completed_trades=0
    )
    r_partial = _with_evidence(
        r_partial,
        ExperimentEvidenceStatus.PARTIAL,
        expectancy=0.4,
        completed_trades=5,
    )
    r_suff_a = _with_evidence(
        r_suff_a,
        ExperimentEvidenceStatus.SUFFICIENT,
        expectancy=0.3,
        total_r=1.2,
        max_drawdown_r=0.8,
        completed_trades=10,
        robust=False,
        oos_expectancy=None,
        oos_trades=0,
    )
    r_suff_b = _with_evidence(
        r_suff_b,
        ExperimentEvidenceStatus.SUFFICIENT,
        expectancy=0.6,
        total_r=2.4,
        max_drawdown_r=0.5,
        completed_trades=12,
        robust=True,
        oos_expectancy=0.35,
        oos_trades=4,
    )
    r_suff_c = _with_evidence(
        r_suff_c,
        ExperimentEvidenceStatus.SUFFICIENT,
        expectancy=0.55,
        total_r=2.2,
        max_drawdown_r=0.3,
        completed_trades=11,
        robust=True,
        oos_expectancy=0.30,
        oos_trades=3,
    )

    for r in (r_insuff, r_partial, r_suff_a, r_suff_b, r_suff_c):
        _register(registry, r)
        print(
            f"  {r.experiment_id} ({r.label}) "
            f"evidence={r.summary.evidence_status.value} "
            f"expectancy={r.summary.expectancy:.4f}"
        )

    # ----------------------------------------------------------
    # 2. Run selection using persisted data.
    # ----------------------------------------------------------
    print()
    print(SUB_SEPARATOR)
    print("2. Run experiment selection from persisted data")
    print(SUB_SEPARATOR)

    engine = SelectionEngine(registry)
    criteria = SelectionCriteria(
        require_evidence_status=ExperimentEvidenceStatus.SUFFICIENT,
        require_robust=True,
        require_oos_evidence=True,
        min_completed_trades=5,
    )
    result = engine.select(criteria, label="11N-demo", metadata={"kind": "demo"})
    print(f"Selection ID : {result.selection_id}")
    print(f"Selection type : {result.selection_type.value}")
    print(f"Evaluated    : {result.all_evaluated}")

    # ----------------------------------------------------------
    # 3. Show candidates.
    # ----------------------------------------------------------
    print()
    print(SUB_SEPARATOR)
    print("3. Candidates")
    print(SUB_SEPARATOR)
    for c in result.candidates:
        print(
            f"  {c.entity_id:<20} {c.evidence_status.value:<12} "
            f"exp={c.expectancy:.4f} robust={c.robust} "
            f"oos={c.has_oos_evidence} elig={c.eligible} "
            f"status={c.status.value}"
        )

    # ----------------------------------------------------------
    # 4. Show rejected experiments and reasons.
    # ----------------------------------------------------------
    print()
    print(SUB_SEPARATOR)
    print("4. Rejected / Ineligible")
    print(SUB_SEPARATOR)
    for r in result.rejected:
        print(f"  {r.entity_id:<20} {r.status.value:<12}")
        print(f"    reason: {r.reason}")

    # ----------------------------------------------------------
    # 5. Show the selected experiment.
    # ----------------------------------------------------------
    print()
    print(SUB_SEPARATOR)
    print("5. Selected experiment")
    print(SUB_SEPARATOR)
    if result.selected is None:
        print("  selected: NONE (no eligible SUFFICIENT candidate)")
    else:
        print(f"  selected: {result.selected.entity_id} ({result.selected.label})")
        print(f"  rationale: {result.selected.rationale}")
    print(f"  promoted: {result.promotion.promoted}")

    # Persist the selection decision.
    selection_registry.register_selection(result, overwrite=True)
    print(f"  persisted selection id: {result.selection_id}")

    # ----------------------------------------------------------
    # 6. Demonstrate suite-level selection.
    # ----------------------------------------------------------
    print()
    print(SUB_SEPARATOR)
    print("6. Suite-level selection")
    print(SUB_SEPARATOR)

    # Suite 1: all insufficient members -> NOT_ELIGIBLE.
    suite1 = SuiteConfig(
        label="all-insufficient",
        members=(_config("s1-ins", 2), _config("s1-ins2", 3)),
    )
    res1 = suite_runner.run(suite1, register=True)
    for m in res1.members:
        _register(registry, _with_evidence(m, ExperimentEvidenceStatus.INSUFFICIENT))
    suite_registry.register_suite(res1, overwrite=True)

    # Suite 2: contains a SUFFICIENT member -> eligible.
    suite2 = SuiteConfig(
        label="has-sufficient",
        members=(_config("s2-m1", 2), _config("s2-m2", 3)),
    )
    res2 = suite_runner.run(suite2, register=True)
    suff_member = _with_evidence(
        res2.members[0],
        ExperimentEvidenceStatus.SUFFICIENT,
        expectancy=0.5,
        total_r=2.0,
        completed_trades=10,
    )
    _register(registry, suff_member)
    suite_registry.register_suite(res2, overwrite=True)

    suite_engine = SuiteSelectionEngine(suite_registry)
    suite_selection = suite_engine.select(
        SelectionCriteria(), label="11N-suite-demo"
    )
    print(f"  suites evaluated: {suite_selection.all_evaluated}")
    for c in suite_selection.candidates:
        print(
            f"  {c.entity_id:<20} evidence={c.evidence_status.value:<12} "
            f"elig={c.eligible} status={c.status.value}"
        )
    if suite_selection.selected is None:
        print("  suite selected: NONE")
    else:
        print(
            f"  suite selected: {suite_selection.selected.entity_id} "
            f"({suite_selection.selected.label})"
        )

    # ----------------------------------------------------------
    # 7. PROVE selection uses persisted data only: patch the
    #    trading pipeline AND the experiment runner to raise.
    # ----------------------------------------------------------
    print()
    print(SUB_SEPARATOR)
    print("7. Persisted-only proof (pipeline + runner patched to raise)")
    print(SUB_SEPARATOR)

    original_eval = HistoricalEvaluationPipeline.evaluate
    original_run = _Runner.run

    def boom_evaluate(self, candles):
        raise RuntimeError("pipeline should not be rerun for selection")

    def boom_run(self, config, candles=None):
        raise RuntimeError("runner should not be rerun for selection")

    HistoricalEvaluationPipeline.evaluate = boom_evaluate  # type: ignore[assignment]
    _Runner.run = boom_run  # type: ignore[assignment]
    try:
        # Re-run selection: must work using persisted data only.
        persisted_result = engine.select(criteria, label="11N-demo", metadata={"kind": "demo"})
        print(
            f"  selection from persisted data OK: "
            f"selected={persisted_result.selected_entity_id}"
        )
        assert persisted_result.selected_entity_id == result.selected_entity_id

        # Re-run suite selection from persisted data only.
        persisted_suite = suite_engine.select(
            SelectionCriteria(), label="11N-suite-demo"
        )
        print(
            f"  suite selection from persisted data OK: "
            f"selected={persisted_suite.selected_entity_id}"
        )
        assert persisted_suite.selected_entity_id == suite_selection.selected_entity_id

        # Load the persisted selection decision back WITHOUT any rerun.
        loaded = selection_registry.load_selection(result.selection_id)
        print(
            f"  loaded selection decision OK: "
            f"selected={loaded.selected_entity_id}"
        )
        assert loaded.selected_entity_id == result.selected_entity_id
    finally:
        HistoricalEvaluationPipeline.evaluate = original_eval  # type: ignore[assignment]
        _Runner.run = original_run  # type: ignore[assignment]
    print("  PROOF: selection used persisted data only (no pipeline / runner rerun).")

    # ----------------------------------------------------------
    # 8. Print the full selection report.
    # ----------------------------------------------------------
    print()
    print(SUB_SEPARATOR)
    print("8. Selection report")
    print(SUB_SEPARATOR)
    print(SelectionReportFormatter().format(result))

    # ----------------------------------------------------------
    # 9. Clear descriptive-only warning.
    # ----------------------------------------------------------
    print()
    print(SEPARATOR)
    print("DESCRIPTIVE-ONLY WARNING")
    print(SEPARATOR)
    print(
        "This selection is DESCRIPTIVE ONLY. Historical experiment "
        "selection is NOT predictive of future market performance."
    )
    print(
        "Selecting an experiment does NOT imply live-trading readiness, "
        "nor that the selected configuration will remain profitable."
    )
    print(
        "An experiment with insufficient evidence was NEVER promoted; a "
        "winner was never manufactured."
    )
    print(SEPARATOR)
    print("Sprint 11N demo completed successfully.")


if __name__ == "__main__":
    main()
