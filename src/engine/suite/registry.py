"""
Experiment suite registry (Sprint 11M).

The :class:`SuiteRegistry` sits ABOVE the Sprint 11K experiment registry
and the Sprint 11M suite manifest persistence. It supports the
operations required to persist completed suites and later retrieve /
analyse them WITHOUT rerunning the underlying trading pipeline:

* ``register_suite(result, overwrite=False)`` -- persist a suite
  manifest (member experiments are assumed already registered).
* ``load_suite(suite_id)`` -- reconstruct a ``SuiteResult`` from the
  persisted manifest + persisted member experiments (no pipeline rerun).
* ``exists(suite_id)`` / ``list()`` / ``delete(suite_id)``.
* ``summarize_suite(suite_id)`` -- suite summary from persisted data.
* ``compare_suites(ids)`` -- compare multiple suites from persisted
  data.

Design rules:

* Never silently overwrite. ``register_suite`` refuses to replace an
  existing manifest unless ``overwrite=True``.

* Integrity verification on every load. The reconstructed suite's
  identity metadata (suite id, configuration hash) is checked for
  internal consistency, and every referenced member experiment must
  exist in the experiment registry. A suite is NOT resilient to member
  deletion by design -- honest failure beats silent partial data.

* No duplication of experiment persistence. Member experiments are
  loaded through the existing Sprint 11K :class:`ExperimentRegistry`.
  The manifest stores only the suite identity + ordered member ids.

* No global mutable state. Cross-platform paths via :mod:`pathlib`.

* ``load_suite`` / ``summarize_suite`` / ``compare_suites`` operate
  ENTIRELY from persisted data; the trading pipeline is never rerun.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

from engine.experiment.comparison import ExperimentComparisonEngine
from engine.models.experiment import ExperimentResult
from engine.models.suite import (
    SuiteComparison,
    SuiteComparisonRow,
    SuiteReproducibilityMetadata,
    SuiteResult,
    SuiteSummary,
)
from engine.registry.registry import ExperimentRegistry
from engine.suite.analysis import SuiteAnalysisEngine
from engine.suite.exceptions import (
    SuiteIntegrityError,
    SuiteNotFoundError,
)
from engine.suite.manifest import SuiteManifestPersistence


# ============================================================
# SUITE REGISTRY
# ============================================================


class SuiteRegistry:
    """
    A registry of completed, reproducible experiment suites.

    The registry wraps an :class:`ExperimentRegistry` (Sprint 11K) and
    a :class:`SuiteManifestPersistence` (Sprint 11M). Suite manifests
    are persisted as thin artifacts; member experiments are loaded from
    the experiment registry.

    Public API:

        register_suite(result, overwrite=False) -> SuiteResult
        load_suite(suite_id) -> SuiteResult
        exists(suite_id) -> bool
        list() -> list[str]
        delete(suite_id) -> None
        summarize_suite(suite_id) -> SuiteSummary
        compare_suites(ids) -> SuiteComparison

    Parameters:

    directory
        Directory the suite manifests are stored in. When ``None`` the
        Sprint 11K default registry directory is reused so suite
        manifests live alongside their member experiment records.

    experiment_registry
        Optional pre-constructed :class:`ExperimentRegistry`. When
        supplied it takes precedence over ``directory`` so suite and
        experiment registries share storage.

    manifest_persistence
        Optional pre-constructed :class:`SuiteManifestPersistence`.
        When supplied it takes precedence over ``directory``.
    """

    def __init__(
        self,
        directory: Path | str | None = None,
        experiment_registry: ExperimentRegistry | None = None,
        manifest_persistence: SuiteManifestPersistence | None = None,
    ) -> None:
        if experiment_registry is not None:
            self._experiment_registry = experiment_registry
        else:
            self._experiment_registry = ExperimentRegistry(directory)

        if manifest_persistence is not None:
            self._manifests = manifest_persistence
        else:
            self._manifests = SuiteManifestPersistence(
                self._experiment_registry.directory,
            )

        self._analysis_engine = SuiteAnalysisEngine()
        self._comparison_engine = ExperimentComparisonEngine()

    # -----------------------------------------------------
    # PROPERTIES
    # -----------------------------------------------------

    @property
    def experiment_registry(self) -> ExperimentRegistry:
        """The underlying Sprint 11K experiment registry."""

        return self._experiment_registry

    @property
    def manifest_persistence(self) -> SuiteManifestPersistence:
        """The underlying suite manifest persistence layer."""

        return self._manifests

    @property
    def directory(self) -> Path:
        """The directory this registry stores suite manifests in."""

        return self._manifests.directory

    # -----------------------------------------------------
    # WRITE
    # -----------------------------------------------------

    def register_suite(
        self,
        result: SuiteResult,
        overwrite: bool = False,
    ) -> SuiteResult:
        """
        Persist a suite manifest.

        Member experiments are assumed to already be registered in the
        experiment registry (the ``SuiteRunner`` registers them when
        ``register=True``). This method persists ONLY the thin suite
        manifest (suite identity + ordered member ids + metadata).

        The suite identity is verified before writing. When
        ``overwrite`` is False and a manifest with the same id is
        already stored, raises :class:`SuiteAlreadyExistsError`.

        Returns the registered result unchanged.
        """

        repro = result.reproducibility

        self._verify_suite_identity(result)

        self._manifests.save(
            suite_id=result.suite_id,
            configuration_hash=repro.suite_configuration_hash,
            label=result.label,
            member_experiment_ids=tuple(repro.member_experiment_ids),
            metadata=dict(result.metadata),
            overwrite=overwrite,
        )

        return result

    # -----------------------------------------------------
    # READ
    # -----------------------------------------------------

    def load_suite(self, suite_id: str) -> SuiteResult:
        """
        Reconstruct a :class:`SuiteResult` from persisted data.

        The suite manifest is loaded, then the member experiments are
        loaded from the experiment registry (persisted data only; the
        trading pipeline is never rerun). The reconstructed suite's
        identity is verified for internal consistency before it is
        returned.

        Raises :class:`SuiteNotFoundError` when no manifest exists,
        :class:`SuiteIntegrityError` when the manifest identity is
        inconsistent or a referenced member experiment is missing.
        """

        manifest = self._manifests.load(suite_id)

        member_ids = tuple(manifest.get("member_experiment_ids", []))

        # Verify every referenced member exists in the experiment
        # registry. A suite is NOT resilient to member deletion by
        # design; honest failure beats silent partial data.
        for member_id in member_ids:
            if not self._experiment_registry.exists(member_id):
                raise SuiteIntegrityError(
                    f"Suite {suite_id!r} references member experiment "
                    f"{member_id!r} which is not stored in the "
                    f"experiment registry."
                )

        members = self._experiment_registry.load_many(member_ids)

        repro = SuiteReproducibilityMetadata(
            suite_id=suite_id,
            suite_configuration_hash=manifest.get("configuration_hash", ""),
            suite_configuration_representation="",
            member_experiment_ids=member_ids,
            member_count=len(member_ids),
            reproducible=bool(member_ids),
        )

        summary = self._analysis_engine.summarize(members)
        summary = self._augment_loaded_summary(summary, suite_id, members)

        label = manifest.get("label", "")
        metadata = dict(manifest.get("metadata", {}))

        config = None  # Config is not part of the thin manifest.

        result = SuiteResult(
            suite_id=suite_id,
            config=config,
            members=tuple(members),
            reproducibility=repro,
            summary=summary,
            label=label,
            metadata=metadata,
        )

        self._verify_suite_identity(result)

        return result

    def exists(self, suite_id: str) -> bool:
        """Whether a manifest with the given id is stored."""

        return self._manifests.exists(suite_id)

    def list(self) -> list[str]:
        """Sorted list of stored suite ids."""

        return self._manifests.list_suites()

    # -----------------------------------------------------
    # DELETE
    # -----------------------------------------------------

    def delete(self, suite_id: str) -> None:
        """
        Delete a stored suite manifest.

        Raises :class:`SuiteNotFoundError` when absent. Deleting a
        suite manifest does NOT delete its member experiment records
        (those remain in the experiment registry).
        """

        self._manifests.delete(suite_id)

    # -----------------------------------------------------
    # ANALYSIS
    # -----------------------------------------------------

    def summarize_suite(self, suite_id: str) -> SuiteSummary:
        """
        Build a suite summary from persisted data (no pipeline rerun).

        Loads the suite, then delegates to the
        :class:`SuiteAnalysisEngine`.
        """

        result = self.load_suite(suite_id)
        return result.summary

    def compare_suites(
        self,
        ids: Sequence[str],
    ) -> SuiteComparison:
        """
        Compare multiple suites from persisted data (no pipeline rerun).

        Descriptive suite-level ranking is computed ONLY among suites
        that each have at least one SUFFICIENT member; ``None``
        otherwise. Insufficient evidence is always made explicit.

        Raises :class:`SuiteNotFoundError` for any missing suite.
        """

        results = [self.load_suite(suite_id) for suite_id in ids]
        return self._compare_suite_results(results)

    # -----------------------------------------------------
    # SUITE COMPARISON
    # -----------------------------------------------------

    def _compare_suite_results(
        self,
        results: Sequence[SuiteResult],
    ) -> SuiteComparison:
        """
        Build an immutable comparison across suite results.

        Each suite is projected to a ``SuiteComparisonRow``. A suite is
        "sufficient" for descriptive ranking when at least one member
        has SUFFICIENT evidence. The best suite by member expectancy is
        computed ONLY among sufficient suites; ``None`` otherwise.
        """

        rows = [self._suite_row(result) for result in results]

        sufficient_rows = [r for r in rows if r.has_sufficient_evidence]
        insufficient_rows = [r for r in rows if not r.has_sufficient_evidence]

        best_suite_by_member_expectancy = self._best_suite_by_expectancy(
            results,
            sufficient_rows,
        )

        conclusions = self._suite_comparison_conclusions(
            rows=rows,
            sufficient_rows=sufficient_rows,
            best_suite_by_member_expectancy=best_suite_by_member_expectancy,
        )

        return SuiteComparison(
            rows=tuple(rows),
            sufficient_suites=tuple(r.suite_id for r in sufficient_rows),
            insufficient_suites=tuple(r.suite_id for r in insufficient_rows),
            best_suite_by_member_expectancy=best_suite_by_member_expectancy,
            has_sufficient_evidence=bool(sufficient_rows),
            conclusions=tuple(conclusions),
        )

    @staticmethod
    def _suite_row(result: SuiteResult) -> SuiteComparisonRow:
        """Project a suite result into a comparison row (no recomputation)."""

        summary = result.summary

        best_member_by_expectancy: str | None = None
        best_member_by_total_r: str | None = None

        if summary.has_sufficient_evidence and summary.comparison is not None:
            comp = summary.comparison
            if comp.best_by_expectancy is not None:
                best_member_by_expectancy = comp.best_by_expectancy
            if comp.best_by_total_r is not None:
                best_member_by_total_r = comp.best_by_total_r

        return SuiteComparisonRow(
            suite_id=result.suite_id,
            label=result.label,
            member_count=summary.member_count,
            sufficient_count=summary.sufficient_count,
            partial_count=summary.partial_count,
            insufficient_count=summary.insufficient_count,
            has_sufficient_evidence=summary.has_sufficient_evidence,
            best_member_by_expectancy=best_member_by_expectancy,
            best_member_by_total_r=best_member_by_total_r,
        )

    @staticmethod
    def _best_suite_by_expectancy(
        results: Sequence[SuiteResult],
        sufficient_rows: Sequence[SuiteComparisonRow],
    ) -> str | None:
        """
        The suite id containing the highest-expectancy SUFFICIENT member
        AMONG suites that have at least one SUFFICIENT member.

        ``None`` when no suite has a SUFFICIENT member. Always
        descriptive. Ties are broken by ascending suite id.
        """

        if not sufficient_rows:
            return None

        # Map suite_id -> best member expectancy (among sufficient
        # members of that suite), via the delegated analysis summary's
        # descriptive leader. When a suite has sufficient evidence but
        # no leader surfaced (shouldn't normally happen), skip it.
        candidates: list[tuple[str, float]] = []
        for row in sufficient_rows:
            best_member_id = row.best_member_by_expectancy
            if best_member_id is None:
                continue
            # Find the expectancy of that member via the suite result's
            # analysis summary leader.
            value = _member_expectancy_for(results, row.suite_id, best_member_id)
            if value is None:
                continue
            candidates.append((row.suite_id, value))

        if not candidates:
            return None

        # Stable: ties broken by ascending suite id.
        candidates.sort(key=lambda c: (-c[1], c[0]))
        return candidates[0][0]

    @staticmethod
    def _suite_comparison_conclusions(
        rows: Sequence[SuiteComparisonRow],
        sufficient_rows: Sequence[SuiteComparisonRow],
        best_suite_by_member_expectancy: str | None,
    ) -> list[str]:
        """Descriptive, non-predictive suite comparison conclusions."""

        conclusions: list[str] = []

        if not rows:
            conclusions.append("No suites supplied for comparison.")
            return conclusions

        conclusions.append(f"Compared {len(rows)} suite(s).")

        insufficient_count = len(rows) - len(sufficient_rows)
        if insufficient_count:
            conclusions.append(
                f"{insufficient_count} suite(s) have no SUFFICIENT "
                f"member; no reliable inference is possible for them."
            )

        if not sufficient_rows:
            conclusions.append(
                "No suite has a SUFFICIENT member; a descriptive best "
                "suite is NOT declared."
            )
        else:
            conclusions.append(
                f"{len(sufficient_rows)} suite(s) have at least one "
                f"SUFFICIENT member for descriptive comparison."
            )
            if best_suite_by_member_expectancy is not None:
                conclusions.append(
                    f"Suite with highest member expectancy "
                    f"(descriptive, among sufficient): "
                    f"{best_suite_by_member_expectancy}."
                )

        conclusions.append(
            "Suite comparison findings are descriptive, not predictive; "
            "historical experiment results do not predict future market "
            "performance."
        )

        return conclusions

    # -----------------------------------------------------
    # INTEGRITY
    # -----------------------------------------------------

    @staticmethod
    def _verify_suite_identity(result: SuiteResult) -> None:
        """
        Verify a suite result's identity metadata is internally
        consistent.

        Raises :class:`SuiteIntegrityError` on mismatch. The config may
        be ``None`` for a suite reconstructed from a manifest (the
        manifest does not store the full config); in that case only the
        reproducibility metadata is cross-checked.
        """

        if not result.suite_id:
            raise SuiteIntegrityError(
                "Suite result has no suite id."
            )

        repro = result.reproducibility
        if repro is None:
            return

        if repro.suite_id != result.suite_id:
            raise SuiteIntegrityError(
                f"Suite id mismatch: result {result.suite_id!r} vs "
                f"reproducibility {repro.suite_id!r}."
            )

        config = result.config
        if config is not None:
            config_id = getattr(config, "suite_id", None)
            config_hash = getattr(config, "configuration_hash", None)
            if config_id is not None and config_id != result.suite_id:
                raise SuiteIntegrityError(
                    f"Suite id mismatch: result {result.suite_id!r} "
                    f"vs config {config_id!r}."
                )
            if (
                config_hash is not None
                and repro.suite_configuration_hash
                and config_hash != repro.suite_configuration_hash
            ):
                raise SuiteIntegrityError(
                    f"Suite configuration hash mismatch: config "
                    f"{config_hash!r} vs reproducibility "
                    f"{repro.suite_configuration_hash!r}."
                )

    # -----------------------------------------------------
    # SUMMARY AUGMENTATION (loaded suites)
    # -----------------------------------------------------

    @staticmethod
    def _augment_loaded_summary(
        summary: SuiteSummary,
        suite_id: str,
        members: Sequence[ExperimentResult],
    ) -> SuiteSummary:
        """
        Add suite-level descriptive conclusions to a loaded summary.

        The counts / analysis / comparison are produced by the reused
        ``SuiteAnalysisEngine``; only descriptive conclusions are
        augmented (no recomputation).
        """

        conclusions = list(summary.conclusions)

        if not members:
            conclusions.append(f"Suite {suite_id} has no members.")
        else:
            conclusions.append(
                f"Suite {suite_id} contains {len(members)} member "
                f"experiment(s), reconstructed from persisted data "
                f"(no pipeline rerun)."
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


# ============================================================
# MODULE HELPERS
# ============================================================


def _member_expectancy_for(
    results: Sequence[SuiteResult],
    suite_id: str,
    member_id: str,
) -> float | None:
    """
    Look up the expectancy of ``member_id`` within the suite
    ``suite_id`` via the delegated analysis summary's leader, falling
    back to scanning the suite's members.

    Returns ``None`` when not resolvable. Reads authoritative values
    only; nothing is recomputed.
    """

    target: SuiteResult | None = None
    for result in results:
        if result.suite_id == suite_id:
            target = result
            break

    if target is None:
        return None

    # Prefer the analysis summary's descriptive leader value.
    analysis = target.summary.analysis_summary
    if analysis is not None and analysis.best_by_expectancy is not None:
        leader = analysis.best_by_expectancy
        if leader.experiment_id == member_id:
            return float(leader.value)

    # Fallback: scan member summaries.
    for member in target.members:
        if member.experiment_id == member_id:
            return float(member.summary.expectancy)

    return None


__all__ = ["SuiteRegistry"]
