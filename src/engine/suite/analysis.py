"""
Experiment suite analysis engine (Sprint 11M).

The ``SuiteAnalysisEngine`` builds a :class:`SuiteSummary` over a
suite's member experiments by DELEGATING to the existing Sprint 11L
query summary and Sprint 11J comparison engines. It implements NO
analysis logic of its own; every value is read from the reused engines.

Design rules:

* No duplication of any existing logic. The suite summary's counts,
  descriptive leaders and coverage maps are produced by reusing the
  Sprint 11L ``_to_row`` / ``_summarize`` query-layer helpers over the
  suite's members; the member comparison is produced by reusing the
  Sprint 11J ``ExperimentComparisonEngine``.

* Evidence safety is structural. Descriptive suite-level leaders are
  populated ONLY when at least one member has SUFFICIENT evidence and
  are ``None`` otherwise. INSUFFICIENT is unobserved, NOT zero
  performance. The engine never turns a descriptive historical result
  into a predictive claim.

* Deterministic. Identical members always produce identical summaries.

* No print() inside the engine.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from engine.experiment.comparison import ExperimentComparisonEngine
from engine.models.experiment import ExperimentResult
from engine.models.suite import SuiteSummary
from engine.query.query import _sort_by_id, _summarize, _to_row


# ============================================================
# SUITE ANALYSIS ENGINE
# ============================================================


class SuiteAnalysisEngine:
    """
    Build a suite summary by delegating to the existing query and
    comparison engines.

    Public API:

        summarize(members) -> SuiteSummary
        compare(members) -> ExperimentComparison

    The engine is stateless across calls: identical inputs always
    produce identical outputs.
    """

    def __init__(self) -> None:
        self._comparison_engine = ExperimentComparisonEngine()

    # ========================================================
    # PUBLIC API
    # ========================================================

    def summarize(
        self,
        members: Iterable[ExperimentResult],
    ) -> SuiteSummary:
        """
        Build a :class:`SuiteSummary` over the suite's members.

        The analysis summary (counts, descriptive leaders, coverage
        maps, conclusions) is produced by reusing the Sprint 11L
        ``_summarize`` query-layer helper over rows projected from the
        members. The member comparison is produced by reusing the
        Sprint 11J ``ExperimentComparisonEngine``. Nothing is
        recomputed.
        """

        member_list = list(members)

        rows = _sort_by_id([_to_row(m) for m in member_list])
        analysis_summary = _summarize(rows)

        comparison = self.compare(member_list)

        sufficient_count = sum(1 for r in rows if r.is_sufficient)
        partial_count = sum(1 for r in rows if r.is_partial)
        insufficient_count = sum(1 for r in rows if r.is_insufficient)

        return SuiteSummary(
            member_count=len(member_list),
            sufficient_count=sufficient_count,
            partial_count=partial_count,
            insufficient_count=insufficient_count,
            has_sufficient_evidence=sufficient_count > 0,
            analysis_summary=analysis_summary,
            comparison=comparison,
            conclusions=analysis_summary.conclusions,
        )

    def compare(
        self,
        members: Iterable[ExperimentResult],
    ):
        """
        Build a member comparison by delegating to the Sprint 11J
        :class:`ExperimentComparisonEngine`.

        Returns ``None`` when the suite has no members (the comparison
        engine would otherwise produce an empty comparison, which is
        valid but uninformative for an empty suite).
        """

        member_list = list(members)
        if not member_list:
            return None

        return self._comparison_engine.compare(member_list)


__all__ = ["SuiteAnalysisEngine"]
