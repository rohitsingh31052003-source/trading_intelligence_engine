"""
Experiment comparison (Sprint 11J).

The ``ExperimentComparisonEngine`` compares multiple
``ExperimentResult`` objects into an immutable
``ExperimentComparison``.

Design rules:

* No recomputation. Every value is read from the reused
  experiment summaries.

* No automatic "best" declaration unless the evidence supports
  it. Descriptive ranking (``best_by_expectancy``,
  ``best_by_total_r``) is computed ONLY among experiments with
  SUFFICIENT evidence. When no experiment is sufficient, both
  are ``None`` and the conclusions explicitly state that
  insufficient data prevents a comparison.

* Insufficient data is always made explicit.

* Conclusions are descriptive, never predictive. The comparison
  never claims a strategy "is best" or "is profitable".
"""

from __future__ import annotations

from typing import Iterable

from engine.models.experiment import (
    ExperimentComparison,
    ExperimentComparisonRow,
    ExperimentEvidenceStatus,
    ExperimentResult,
)


class ExperimentComparisonEngine:
    """
    Compare multiple experiment results.

    Public API:

        compare(results) -> ExperimentComparison

    The engine is stateless across calls.
    """

    # ========================================================
    # PUBLIC API
    # ========================================================

    def compare(
        self,
        results: Iterable[ExperimentResult],
    ) -> ExperimentComparison:
        """
        Build an immutable comparison across experiment results.
        """

        results_list = list(results)

        rows = tuple(
            self._row(result) for result in results_list
        )

        sufficient = tuple(
            r.experiment_id
            for r in rows
            if r.evidence_status == ExperimentEvidenceStatus.SUFFICIENT
        )
        partial = tuple(
            r.experiment_id
            for r in rows
            if r.evidence_status == ExperimentEvidenceStatus.PARTIAL
        )
        insufficient = tuple(
            r.experiment_id
            for r in rows
            if r.evidence_status == ExperimentEvidenceStatus.INSUFFICIENT
        )

        best_by_expectancy = self._best_descriptive(
            rows,
            sufficient,
            key=lambda r: r.expectancy,
        )
        best_by_total_r = self._best_descriptive(
            rows,
            sufficient,
            key=lambda r: r.total_r,
        )

        has_sufficient = bool(sufficient)

        conclusions = self._conclusions(
            rows=rows,
            sufficient=sufficient,
            partial=partial,
            insufficient=insufficient,
            best_by_expectancy=best_by_expectancy,
            best_by_total_r=best_by_total_r,
        )

        return ExperimentComparison(
            rows=rows,
            sufficient_experiments=sufficient,
            insufficient_experiments=insufficient,
            partial_experiments=partial,
            best_by_expectancy=best_by_expectancy,
            best_by_total_r=best_by_total_r,
            has_sufficient_evidence=has_sufficient,
            conclusions=tuple(conclusions),
        )

    # ========================================================
    # ROW PROJECTION
    # ========================================================

    @staticmethod
    def _row(result: ExperimentResult) -> ExperimentComparisonRow:
        summary = result.summary

        return ExperimentComparisonRow(
            experiment_id=result.experiment_id,
            label=result.label,
            completed_trades=summary.completed_trades,
            win_rate=summary.win_rate,
            expectancy=summary.expectancy,
            total_r=summary.total_r,
            profit_factor=summary.profit_factor,
            max_drawdown_r=summary.max_drawdown_r,
            robust=summary.robust,
            oos_expectancy=summary.oos_expectancy,
            oos_trades=summary.oos_trades,
            data_sufficient=summary.data_sufficient,
            leakage_passed=summary.leakage_passed,
            leakage_not_verified=summary.leakage_not_verified,
            evidence_status=summary.evidence_status,
        )

    # ========================================================
    # DESCRIPTIVE RANKING
    # ========================================================

    @staticmethod
    def _best_descriptive(
        rows: tuple[ExperimentComparisonRow, ...],
        sufficient_ids: tuple[str, ...],
        key,
    ) -> str | None:
        """
        Return the experiment ID with the highest ``key`` AMONG
        SUFFICIENT experiments only.

        Returns ``None`` when no experiment is sufficient. This
        is the core "no best without evidence" guarantee.
        """

        if not sufficient_ids:
            return None

        sufficient_set = set(sufficient_ids)

        candidates = [r for r in rows if r.experiment_id in sufficient_set]

        if not candidates:
            return None

        best = max(candidates, key=key)

        return best.experiment_id

    # ========================================================
    # CONCLUSIONS
    # ========================================================

    @staticmethod
    def _conclusions(
        rows: tuple[ExperimentComparisonRow, ...],
        sufficient: tuple[str, ...],
        partial: tuple[str, ...],
        insufficient: tuple[str, ...],
        best_by_expectancy: str | None,
        best_by_total_r: str | None,
    ) -> list[str]:
        conclusions: list[str] = []

        if not rows:
            conclusions.append("No experiments were supplied for comparison.")
            return conclusions

        conclusions.append(
            f"Compared {len(rows)} experiment(s)."
        )

        if insufficient:
            conclusions.append(
                f"{len(insufficient)} experiment(s) have INSUFFICIENT "
                f"evidence; no reliable comparison is possible for them."
            )

        if partial:
            conclusions.append(
                f"{len(partial)} experiment(s) have PARTIAL evidence; "
                f"conclusions for them are provisional."
            )

        if not sufficient:
            conclusions.append(
                "No experiment has SUFFICIENT evidence; a descriptive "
                "best is NOT declared."
            )
            return conclusions

        conclusions.append(
            f"{len(sufficient)} experiment(s) have SUFFICIENT evidence "
            f"for descriptive comparison."
        )

        if best_by_expectancy is not None:
            conclusions.append(
                f"Highest expectancy (descriptive, among sufficient): "
                f"{best_by_expectancy}."
            )

        if best_by_total_r is not None:
            conclusions.append(
                f"Highest total R (descriptive, among sufficient): "
                f"{best_by_total_r}."
            )

        # Robustness-aware note.
        robust_rows = [
            r for r in rows
            if r.experiment_id in set(sufficient) and r.robust is True
        ]
        if robust_rows:
            conclusions.append(
                f"{len(robust_rows)} sufficient experiment(s) also "
                f"identified a robust parameter configuration."
            )
        else:
            conclusions.append(
                "No sufficient experiment identified a robust parameter "
                "configuration; treat descriptive rankings cautiously."
            )

        # Leakage-aware note.
        leakage_failures = [
            r.experiment_id
            for r in rows
            if r.leakage_passed is False
        ]
        if leakage_failures:
            conclusions.append(
                f"Leakage violations were detected in: "
                f"{', '.join(leakage_failures)}."
            )

        conclusions.append(
            "All rankings are descriptive, not predictive."
        )

        return conclusions
