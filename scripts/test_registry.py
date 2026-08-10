"""
Demo script for the experiment registry / persistence layer
(Sprint 11K).

Demonstrates the full persisted-research workflow:

1. create / run at least two experiments using the existing
   experiment framework (Sprint 11J),
2. save them to the registry,
3. list stored experiments,
4. load them back,
5. verify their identities,
6. compare the loaded experiments,
7. print a concise report.

The comparison uses PERSISTED results rather than rerunning the
underlying trading pipeline. This is made explicit in the output.
"""

import os
import sys
import tempfile

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
    ExperimentRunner,
)
from engine.pipeline.historical_pipeline import PipelineConfig
from engine.registry import (
    SCHEMA_VERSION,
    ExperimentRegistry,
)
from engine.research.research import ResearchConfig
from engine.reporting import (
    ExperimentComparisonFormatter,
    ExperimentReportFormatter,
)


def _config(label: str, lookback: int) -> ExperimentConfig:
    """Build a deterministic experiment configuration."""

    return ExperimentConfig(
        label=label,
        dataset=DatasetSpec(name="trending"),
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
        metadata={"dataset": "trending", "sprint": "11K"},
    )


def main() -> None:

    runner = ExperimentRunner()

    config_a = _config("trending-lookback-2", lookback=2)
    config_b = _config("trending-lookback-3", lookback=3)

    # -----------------------------------------------------
    # 1. RUN the experiments (once).
    # -----------------------------------------------------

    print("=" * 60)
    print("Sprint 11K - Experiment Registry Demo")
    print(f"Persistence schema version: {SCHEMA_VERSION}")
    print("=" * 60)
    print()
    print("Step 1: Running two experiments through the existing")
    print("        experiment framework (Sprint 11J)...")

    result_a = runner.run(config_a)
    result_b = runner.run(config_b)

    print(f"  - {result_a.experiment_id} ({result_a.label})")
    print(
        f"    evidence={result_a.summary.evidence_status.value} "
        f"trades={result_a.summary.completed_trades}"
    )
    print(f"  - {result_b.experiment_id} ({result_b.label})")
    print(
        f"    evidence={result_b.summary.evidence_status.value} "
        f"trades={result_b.summary.completed_trades}"
    )
    print()

    # -----------------------------------------------------
    # 2. SAVE them to the registry (atomic, versioned JSON).
    # -----------------------------------------------------

    with tempfile.TemporaryDirectory() as storage:
        registry = ExperimentRegistry(storage)

        print("Step 2: Saving experiments to the registry at:")
        print(f"        {registry.directory}")
        registry.register(result_a)
        registry.register(result_b)

        # -------------------------------------------------
        # 3. LIST stored experiments.
        # -------------------------------------------------

        print()
        print("Step 3: Listing stored experiments...")
        for experiment_id in registry.list():
            print(f"  - {experiment_id}")

        # -------------------------------------------------
        # 4 & 5. LOAD them back and verify identities.
        # -------------------------------------------------

        print()
        print("Step 4: Loading experiments back from the registry")
        print("        (no trading pipeline rerun)...")

        loaded_a = registry.get(result_a.experiment_id)
        loaded_b = registry.get(result_b.experiment_id)

        print(f"  - loaded {loaded_a.experiment_id}")
        print(f"  - loaded {loaded_b.experiment_id}")

        print()
        print("Step 5: Verifying loaded identities...")
        for original, loaded in (
            (result_a, loaded_a),
            (result_b, loaded_b),
        ):
            registry.verify_identity(loaded)
            id_ok = loaded.experiment_id == original.experiment_id
            hash_ok = (
                loaded.config.configuration_hash
                == original.config.configuration_hash
            )
            dataset_ok = (
                loaded.reproducibility.dataset_content_hash
                == original.reproducibility.dataset_content_hash
            )
            print(
                f"  - {loaded.experiment_id}: "
                f"id={'OK' if id_ok else 'BAD'} "
                f"config_hash={'OK' if hash_ok else 'BAD'} "
                f"dataset_hash={'OK' if dataset_ok else 'BAD'}"
            )

        # -------------------------------------------------
        # 6. COMPARE the loaded experiments.
        # -------------------------------------------------

        print()
        print("Step 6: Comparing the LOADED experiments.")
        print("        (uses persisted results, NOT a rerun)")

        comparison = registry.compare(
            [result_a.experiment_id, result_b.experiment_id]
        )

        print()
        print(ExperimentComparisonFormatter().format(comparison))

        # -------------------------------------------------
        # 7. A concise per-experiment report (from disk).
        # -------------------------------------------------

        print()
        print("Step 7: Per-experiment report from persisted data:")
        formatter = ExperimentReportFormatter()
        print(formatter.format(loaded_a))
        print()
        print(formatter.format(loaded_b))

        print()
        print("=" * 60)
        print("Demo complete. Comparison used persisted results.")
        print(f"Registry stored {len(registry.list())} experiment(s).")
        print("=" * 60)


if __name__ == "__main__":
    main()
