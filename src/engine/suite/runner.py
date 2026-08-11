"""
Experiment suite runner (Sprint 11M).

The ``SuiteRunner`` orchestrates the existing Sprint 11J experiment
runner, the Sprint 11K experiment registry and the Sprint 11M suite
analysis engine into a single reproducible ``SuiteResult``.

The runner performs orchestration ONLY. It implements no trading,
validation, performance, pipeline or research logic. Every engine is
reused as-is:

* ``ExperimentRunner``        (Sprint 11J)  -- runs each member.
* ``ExperimentRegistry``      (Sprint 11K)  -- persists each member.
* ``SuiteAnalysisEngine``    (Sprint 11M)  -- builds the suite summary.

Design rules:

* No duplication of any existing logic.
* Deterministic: identical config + datasets -> identical result.
* Skip-already-registered members: when ``register=True`` and a member
  experiment is already present in the registry (and ``overwrite`` is
  False), the existing persisted result is REUSED (loaded) instead of
  being re-run. This makes re-running a suite cheap and idempotent.
* Graceful on edge cases (empty suite, INSUFFICIENT members) -- never
  raises merely because a member produced insufficient evidence; the
  suite still completes with that member flagged.
* No print() inside the runner.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any, Mapping, Sequence

from engine.experiment.runner import ExperimentRunner
from engine.models.experiment import ExperimentResult
from engine.models.ohlcv import OHLCVCandle
from engine.models.suite import (
    SuiteReproducibilityMetadata,
    SuiteResult,
    SuiteSummary,
)
from engine.registry.registry import ExperimentRegistry
from engine.suite.analysis import SuiteAnalysisEngine
from engine.suite.config import SuiteConfig


_PACKAGE_NAME = "trading-intelligence-engine"


def _code_version() -> str:
    """Safely resolve the installed package version; UNKNOWN if absent."""

    try:
        return version(_PACKAGE_NAME)
    except PackageNotFoundError:
        return "UNKNOWN"
    except Exception:
        return "UNKNOWN"


# ============================================================
# RUNNER
# ============================================================


class SuiteRunner:
    """
    Orchestrate a complete, reproducible experiment suite.

    Public API:

        run(config, datasets=None, register=True, overwrite=False)
            -> SuiteResult

    The runner is stateless across calls: identical inputs always
    produce identical outputs.

    Parameters:

    experiment_registry
        Optional pre-constructed :class:`ExperimentRegistry`. When
        ``None`` the default registry directory is used. When supplied
        it takes precedence so callers can reuse a shared registry.
    """

    def __init__(
        self,
        experiment_registry: ExperimentRegistry | None = None,
    ) -> None:
        self._experiment_runner = ExperimentRunner()
        self._registry = experiment_registry
        self._analysis_engine = SuiteAnalysisEngine()

    # ========================================================
    # PUBLIC API
    # ========================================================

    def run(
        self,
        config: SuiteConfig,
        datasets: Mapping[str, Sequence[OHLCVCandle]] | None = None,
        register: bool = True,
        overwrite: bool = False,
    ) -> SuiteResult:
        """
        Execute a complete experiment suite.

        Each member ``ExperimentConfig`` is run via the existing
        ``ExperimentRunner``. When ``register=True`` each member result
        is persisted via the existing ``ExperimentRegistry``; a member
        already present in the registry is REUSED (loaded) instead of
        being re-run, unless ``overwrite=True``.

        The suite never raises merely because a member produced
        INSUFFICIENT evidence; the member is included and flagged.

        The suite manifest itself is NOT persisted here (the caller
        persists it via :class:`SuiteRegistry`). This runner is
        concerned with running members and assembling the result.
        """

        members = self._run_members(
            config=config,
            datasets=datasets,
            register=register,
            overwrite=overwrite,
        )

        reproducibility = self._build_reproducibility(config, members)
        summary = self._analysis_engine.summarize(members)
        summary = self._augment_summary(summary, config, members)

        return SuiteResult(
            suite_id=config.suite_id,
            config=config,
            members=tuple(members),
            reproducibility=reproducibility,
            summary=summary,
            label=config.label,
            metadata=dict(config.metadata),
        )

    # ========================================================
    # MEMBER EXECUTION
    # ========================================================

    def _run_members(
        self,
        config: SuiteConfig,
        datasets: Mapping[str, Sequence[OHLCVCandle]] | None,
        register: bool,
        overwrite: bool,
    ) -> list[ExperimentResult]:
        """
        Run (or reuse) each member experiment.

        Skip-already-registered semantics: when ``register`` is True and
        a member experiment id already exists in the registry (and
        ``overwrite`` is False), the existing persisted result is loaded
        instead of re-running the pipeline. This makes re-running a
        suite cheap and idempotent.
        """

        members: list[ExperimentResult] = []

        for member_config in config.members:
            result = self._run_single_member(
                member_config=member_config,
                datasets=datasets,
                register=register,
                overwrite=overwrite,
            )
            members.append(result)

        return members

    def _run_single_member(
        self,
        member_config: Any,
        datasets: Mapping[str, Sequence[OHLCVCandle]] | None,
        register: bool,
        overwrite: bool,
    ) -> ExperimentResult:
        """
        Run or reuse a single member experiment.

        Custom datasets are supplied by name via the ``datasets``
        mapping (mirroring ``ExperimentRunner.run``'s custom-dataset
        contract). Built-in datasets (``trending`` / ``flat`` /
        ``minimal``) need no candles.
        """

        registry = self._registry
        member_id = member_config.experiment_id

        # Skip-already-registered: reuse the persisted result.
        if (
            register
            and registry is not None
            and not overwrite
            and registry.exists(member_id)
        ):
            return registry.get(member_id)

        candles = None
        dataset_name = member_config.dataset.name
        if datasets is not None and dataset_name in datasets:
            candles = list(datasets[dataset_name])

        result = self._experiment_runner.run(member_config, candles=candles)

        if register and registry is not None:
            registry.register(result, overwrite=overwrite)

        return result

    # ========================================================
    # REPRODUCIBILITY METADATA
    # ========================================================

    def _build_reproducibility(
        self,
        config: SuiteConfig,
        members: Sequence[ExperimentResult],
    ) -> SuiteReproducibilityMetadata:
        """
        Build explicit, honest suite reproducibility metadata.

        The suite id / configuration hash are derived from the suite
        config (deterministic, no timestamps). The code version is
        resolved via ``importlib.metadata`` and is ``"UNKNOWN"`` when
        unavailable (never fabricated).
        """

        member_ids = tuple(m.experiment_id for m in members)

        reproducible = bool(
            config.canonical_representation
            and all(mid for mid in member_ids)
        )

        return SuiteReproducibilityMetadata(
            suite_id=config.suite_id,
            suite_configuration_hash=config.configuration_hash,
            suite_configuration_representation=config.canonical_representation,
            member_experiment_ids=member_ids,
            member_count=len(member_ids),
            code_version=_code_version(),
            reproducible=reproducible,
        )

    # ========================================================
    # SUMMARY AUGMENTATION
    # ========================================================

    @staticmethod
    def _augment_summary(
        summary: SuiteSummary,
        config: SuiteConfig,
        members: Sequence[ExperimentResult],
    ) -> SuiteSummary:
        """
        Add suite-level descriptive conclusions to the delegated summary.

        The summary's counts / analysis / comparison are produced by
        the reused ``SuiteAnalysisEngine``; only the descriptive
        conclusions are augmented here (no recomputation).
        """

        conclusions = list(summary.conclusions)

        if not members:
            conclusions.append("Suite has no members; no analysis possible.")
        else:
            conclusions.append(
                f"Suite contains {len(members)} member experiment(s)."
            )

        if summary.has_sufficient_evidence:
            conclusions.append(
                "At least one member has SUFFICIENT evidence; "
                "descriptive suite-level comparison is available."
            )
        else:
            conclusions.append(
                "No member has SUFFICIENT evidence; a descriptive "
                "suite-level best is NOT declared."
            )

        conclusions.append(
            "Suite findings are descriptive, not predictive; "
            "historical experiment results do not predict future "
            "market performance."
        )

        return SuiteSummary(
            member_count=summary.member_count,
            sufficient_count=summary.sufficient_count,
            partial_count=summary.partial_count,
            insufficient_count=summary.insufficient_count,
            has_sufficient_evidence=summary.has_sufficient_evidence,
            analysis_summary=summary.analysis_summary,
            comparison=summary.comparison,
            conclusions=tuple(conclusions),
        )


__all__ = ["SuiteRunner"]
