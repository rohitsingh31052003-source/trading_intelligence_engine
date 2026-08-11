"""
Experiment query / filtering / analysis engine (Sprint 11L).

The :class:`ExperimentQueryEngine` sits ABOVE the Sprint 11K
experiment registry. It discovers, filters, sorts, groups and
summarizes PERSISTED experiment results WITHOUT rerunning the
underlying trading pipeline.

Public API:

    query(filter=None, sort_key=None, sort_order=ASCENDING)
        -> tuple[ExperimentQueryRow, ...]

    group(filter=None, dimension=ExperimentGroupDimension.DATASET)
        -> ExperimentGrouping

    summarize(filter=None) -> ExperimentAnalysisSummary

Design rules:

* No duplication of experiment logic (Sprint 11J). Every value is
  read from the persisted ``ExperimentResult`` (its summary,
  dataset and reproducibility metadata). No statistic is
  recomputed and no pipeline is rerun.

* Persistence is separate from query/analysis. The engine holds a
  reference to an :class:`ExperimentRegistry` (Sprint 11K) and
  loads persisted results through it. It never touches the
  filesystem or the trading pipeline directly.

* Evidence safety is structural. Descriptive "best"/"leader"
  results are computed ONLY among experiments with SUFFICIENT
  evidence and are ``None`` otherwise. The engine never ranks
  insufficient experiments as reliable winners and never claims
  historical results predict future performance.

* Determinism. Ordering, grouping and summaries are pure functions
  of the persisted data. Ties are broken by ascending experiment
  id so identical inputs always produce identical outputs.

* No print() inside the engine.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from engine.models.experiment import (
    ExperimentEvidenceStatus,
    ExperimentResult,
)
from engine.models.query import (
    ExperimentAnalysisSummary,
    ExperimentFilter,
    ExperimentGroup,
    ExperimentGroupDimension,
    ExperimentGrouping,
    ExperimentQueryError,
    ExperimentQueryRow,
    ExperimentSortKey,
    MetricLeader,
    SortOrder,
)
from engine.registry.registry import ExperimentRegistry


# ============================================================
# QUERY ENGINE
# ============================================================


class ExperimentQueryEngine:
    """
    Query, filter, sort, group and summarize persisted experiments.

    The engine is constructed with an :class:`ExperimentRegistry`
    (Sprint 11K). All data is loaded from persisted records; the
    trading pipeline is never rerun to answer a query.

    The engine is stateless across calls: identical inputs always
    produce identical outputs.
    """

    def __init__(self, registry: ExperimentRegistry) -> None:
        self._registry = registry

    # -----------------------------------------------------
    # PROPERTIES
    # -----------------------------------------------------

    @property
    def registry(self) -> ExperimentRegistry:
        """The underlying Sprint 11K registry."""

        return self._registry

    # ========================================================
    # PUBLIC API
    # ========================================================

    def query(
        self,
        filter: ExperimentFilter | None = None,
        sort_key: ExperimentSortKey | str | None = None,
        sort_order: SortOrder | str = SortOrder.ASCENDING,
    ) -> tuple[ExperimentQueryRow, ...]:
        """
        Return the filtered, sorted query rows for all persisted
        experiments matching ``filter``.

        When ``sort_key`` is ``None`` the rows are ordered by
        ascending experiment id (the deterministic default). The
        sort is stable: ties on the primary key are broken by
        ascending experiment id so the result is always
        deterministic regardless of sort direction.

        ``sort_key`` and ``sort_order`` accept either the enum
        members or their stable string names (case-insensitive) so
        the API is convenient for CLI / dashboard callers. Unknown
        values raise :class:`ExperimentQueryError`.
        """

        key = _coerce_sort_key(sort_key)
        order = _coerce_sort_order(sort_order)

        rows = self._filtered_rows(filter)

        if key is not None:
            rows = _sort_rows(rows, key, order)
        else:
            rows = _sort_by_id(rows)

        return tuple(rows)

    def group(
        self,
        filter: ExperimentFilter | None = None,
        dimension: ExperimentGroupDimension | str = ExperimentGroupDimension.DATASET,
    ) -> ExperimentGrouping:
        """
        Group the filtered persisted experiments by ``dimension``.

        Groups are sorted by name ascending for determinism. Each
        group carries an evidence-sufficiency breakdown.
        """

        dim = _coerce_group_dimension(dimension)
        rows = self._filtered_rows(filter)

        groups = _group_rows(rows, dim)
        return ExperimentGrouping(
            dimension=dim,
            groups=tuple(groups),
            total_experiments=len(rows),
        )

    def summarize(
        self,
        filter: ExperimentFilter | None = None,
    ) -> ExperimentAnalysisSummary:
        """
        Build a structured analysis summary over the filtered
        persisted experiments.

        Descriptive leaders (best expectancy, best total R, lowest
        drawdown) are computed ONLY among experiments with
        SUFFICIENT evidence and are ``None`` when none is
        sufficient. The summary never declares a "best" experiment
        when evidence is insufficient and never claims historical
        results predict future performance.
        """

        rows = self._filtered_rows(filter)
        return _summarize(rows)

    # ========================================================
    # INTERNAL: LOADING + FILTERING
    # ========================================================

    def _filtered_rows(
        self,
        filter: ExperimentFilter | None,
    ) -> list[ExperimentQueryRow]:
        """
        Load every persisted experiment from the registry and
        return the rows matching ``filter`` (in ascending-id order
        as a stable baseline for downstream sorting).

        Loading goes through the registry (persisted data only);
        the trading pipeline is never rerun.
        """

        ids = self._registry.list()
        if not ids:
            return []

        results = self._registry.load_many(ids)
        rows = [_to_row(result) for result in results]

        if filter is None:
            return _sort_by_id(rows)

        matched = [row for row in rows if _matches(row, filter)]
        return _sort_by_id(matched)


# ============================================================
# ROW PROJECTION
# ============================================================


def _to_row(result: ExperimentResult) -> ExperimentQueryRow:
    """
    Project a persisted ``ExperimentResult`` into a query row.

    Reads authoritative values only; nothing is recomputed.
    """

    summary = result.summary
    repro = result.reproducibility
    dataset = result.dataset

    configuration_hash = ""
    if repro is not None:
        configuration_hash = repro.configuration_hash or ""

    dataset_name = dataset.name if dataset is not None else ""
    dataset_content_hash = (
        dataset.content_hash if dataset is not None else None
    )

    reproducible = bool(repro.reproducible) if repro is not None else False

    parameter_values: dict[str, str] = {}
    if repro is not None and repro.parameter_values:
        parameter_values = dict(repro.parameter_values)

    return ExperimentQueryRow(
        experiment_id=result.experiment_id,
        label=result.label,
        dataset_name=dataset_name,
        dataset_content_hash=dataset_content_hash,
        configuration_hash=configuration_hash,
        evidence_status=summary.evidence_status,
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
        reproducible=reproducible,
        parameter_values=parameter_values,
    )


# ============================================================
# FILTER MATCHING
# ============================================================


def _matches(row: ExperimentQueryRow, filter: ExperimentFilter) -> bool:
    """
    Return whether ``row`` satisfies ALL active filter criteria.
    """

    if filter.experiment_id is not None:
        if row.experiment_id != filter.experiment_id:
            return False

    if filter.label is not None:
        if row.label != filter.label:
            return False

    if filter.dataset_name is not None:
        if row.dataset_name != filter.dataset_name:
            return False

    if filter.evidence_status is not None:
        if row.evidence_status != filter.evidence_status:
            return False

    if filter.configuration_hash is not None:
        if row.configuration_hash != filter.configuration_hash:
            return False

    if filter.dataset_content_hash is not None:
        if row.dataset_content_hash != filter.dataset_content_hash:
            return False

    if filter.reproducible is not None:
        if row.reproducible != filter.reproducible:
            return False

    if filter.min_completed_trades is not None:
        if row.completed_trades < filter.min_completed_trades:
            return False

    if filter.max_completed_trades is not None:
        if row.completed_trades > filter.max_completed_trades:
            return False

    if filter.min_expectancy is not None:
        if row.expectancy < filter.min_expectancy:
            return False

    if filter.max_expectancy is not None:
        if row.expectancy > filter.max_expectancy:
            return False

    if filter.parameter_values:
        for key, value in filter.parameter_values.items():
            if row.parameter_values.get(key) != value:
                return False

    return True


# ============================================================
# SORTING
# ============================================================


def _sort_rows(
    rows: Sequence[ExperimentQueryRow],
    key: ExperimentSortKey,
    order: SortOrder,
) -> list[ExperimentQueryRow]:
    """
    Deterministic stable sort.

    Two-pass: first by ascending experiment id (the deterministic
    tiebreaker baseline), then by the requested metric with
    ``reverse`` set for DESCENDING. Python's stable sort preserves
    the ascending-id order for ties regardless of direction.
    """

    baseline = _sort_by_id(rows)

    accessor = _SORT_ACCESSORS[key]
    reverse = order == SortOrder.DESCENDING

    return sorted(baseline, key=accessor, reverse=reverse)


def _sort_by_id(
    rows: Iterable[ExperimentQueryRow],
) -> list[ExperimentQueryRow]:
    return sorted(rows, key=lambda r: r.experiment_id)


def _expectancy(r: ExperimentQueryRow) -> float:
    return float(r.expectancy)


def _total_r(r: ExperimentQueryRow) -> float:
    return float(r.total_r)


def _win_rate(r: ExperimentQueryRow) -> float:
    return float(r.win_rate)


def _profit_factor(r: ExperimentQueryRow) -> float:
    return float(r.profit_factor)


def _max_drawdown(r: ExperimentQueryRow) -> float:
    return float(r.max_drawdown_r)


def _completed_trades(r: ExperimentQueryRow) -> float:
    return float(r.completed_trades)


def _experiment_id(r: ExperimentQueryRow) -> str:
    return r.experiment_id


def _label(r: ExperimentQueryRow) -> str:
    return r.label


_SORT_ACCESSORS: dict[
    ExperimentSortKey, object
] = {
    ExperimentSortKey.EXPECTANCY: _expectancy,
    ExperimentSortKey.TOTAL_R: _total_r,
    ExperimentSortKey.WIN_RATE: _win_rate,
    ExperimentSortKey.PROFIT_FACTOR: _profit_factor,
    ExperimentSortKey.MAX_DRAWDOWN: _max_drawdown,
    ExperimentSortKey.COMPLETED_TRADES: _completed_trades,
    ExperimentSortKey.EXPERIMENT_ID: _experiment_id,
    ExperimentSortKey.LABEL: _label,
}


# ============================================================
# GROUPING
# ============================================================


def _group_rows(
    rows: Sequence[ExperimentQueryRow],
    dimension: ExperimentGroupDimension,
) -> list[ExperimentGroup]:
    """
    Group rows by ``dimension``; groups sorted by name ascending.
    """

    buckets: dict[str, list[ExperimentQueryRow]] = {}

    for row in rows:
        name = _group_name(row, dimension)
        buckets.setdefault(name, []).append(row)

    groups: list[ExperimentGroup] = []
    for name in sorted(buckets.keys()):
        members = buckets[name]
        ids = tuple(sorted(r.experiment_id for r in members))
        sufficient = sum(
            1 for r in members if r.is_sufficient
        )
        partial = sum(1 for r in members if r.is_partial)
        insufficient = sum(
            1 for r in members if r.is_insufficient
        )
        groups.append(
            ExperimentGroup(
                name=name,
                dimension=dimension,
                experiment_ids=ids,
                experiment_count=len(members),
                sufficient_count=sufficient,
                partial_count=partial,
                insufficient_count=insufficient,
            )
        )

    return groups


def _group_name(
    row: ExperimentQueryRow,
    dimension: ExperimentGroupDimension,
) -> str:
    if dimension == ExperimentGroupDimension.DATASET:
        return row.dataset_name
    if dimension == ExperimentGroupDimension.EVIDENCE_STATUS:
        return row.evidence_status.value
    if dimension == ExperimentGroupDimension.LABEL:
        return row.label
    if dimension == ExperimentGroupDimension.CONFIGURATION:
        return row.configuration_hash

    raise ExperimentQueryError(
        f"Unknown grouping dimension: {dimension!r}."
    )


# ============================================================
# ANALYSIS SUMMARY
# ============================================================


def _summarize(
    rows: Sequence[ExperimentQueryRow],
) -> ExperimentAnalysisSummary:
    """
    Build the analysis summary.

    Descriptive leaders are computed ONLY among SUFFICIENT rows and
    are ``None`` when none is sufficient.
    """

    total = len(rows)
    sufficient = [r for r in rows if r.is_sufficient]
    partial = [r for r in rows if r.is_partial]
    insufficient = [r for r in rows if r.is_insufficient]

    sufficient_count = len(sufficient)
    partial_count = len(partial)
    insufficient_count = len(insufficient)
    has_sufficient = sufficient_count > 0

    best_by_expectancy = _leader(
        sufficient,
        key=lambda r: r.expectancy,
        highest=True,
    )
    best_by_total_r = _leader(
        sufficient,
        key=lambda r: r.total_r,
        highest=True,
    )
    lowest_drawdown = _leader(
        sufficient,
        key=lambda r: r.max_drawdown_r,
        highest=False,
    )

    reproducible_ids = tuple(
        sorted(r.experiment_id for r in rows if r.reproducible)
    )

    dataset_coverage = _coverage(rows, key=lambda r: r.dataset_name)
    configuration_coverage = _coverage(
        rows,
        key=lambda r: r.configuration_hash,
    )
    parameter_coverage = _parameter_coverage(rows)

    conclusions = _summary_conclusions(
        total=total,
        sufficient_count=sufficient_count,
        partial_count=partial_count,
        insufficient_count=insufficient_count,
        has_sufficient=has_sufficient,
        best_by_expectancy=best_by_expectancy,
        best_by_total_r=best_by_total_r,
        lowest_drawdown=lowest_drawdown,
        reproducible_ids=reproducible_ids,
        dataset_coverage=dataset_coverage,
        configuration_coverage=configuration_coverage,
    )

    return ExperimentAnalysisSummary(
        total_experiments=total,
        sufficient_count=sufficient_count,
        partial_count=partial_count,
        insufficient_count=insufficient_count,
        has_sufficient_evidence=has_sufficient,
        best_by_expectancy=best_by_expectancy,
        best_by_total_r=best_by_total_r,
        lowest_drawdown=lowest_drawdown,
        most_reproducible_experiment_ids=reproducible_ids,
        dataset_coverage=dataset_coverage,
        configuration_coverage=configuration_coverage,
        parameter_coverage=parameter_coverage,
        conclusions=tuple(conclusions),
    )


def _leader(
    rows: Sequence[ExperimentQueryRow],
    key,
    highest: bool,
) -> MetricLeader | None:
    """
    Return the descriptive leader on ``key`` among ``rows``.

    ``highest=True`` selects the maximum; ``highest=False`` the
    minimum. Ties are broken by ascending experiment id (via the
    stable two-pass sort). Returns ``None`` when ``rows`` is empty.
    """

    if not rows:
        return None

    baseline = sorted(rows, key=lambda r: r.experiment_id)
    reverse = highest
    best = sorted(baseline, key=key, reverse=reverse)[0]

    return MetricLeader(
        experiment_id=best.experiment_id,
        value=float(key(best)),
    )


def _coverage(
    rows: Sequence[ExperimentQueryRow],
    key,
) -> dict[str, int]:
    """
    Build a {value: count} coverage map, sorted by key ascending.
    """

    counts: dict[str, int] = {}
    for row in rows:
        name = key(row)
        counts[name] = counts.get(name, 0) + 1
    return dict(sorted(counts.items()))


def _parameter_coverage(
    rows: Sequence[ExperimentQueryRow],
) -> dict[str, int]:
    """
    Coverage by distinct persisted parameter-values snapshots.

    The snapshot is a deterministic canonical string
    (``k=v;...`` sorted by key) so identical parameter sets
    collapse to one bucket. Empty parameter sets collapse to the
    empty-string bucket.
    """

    counts: dict[str, int] = {}
    for row in rows:
        snapshot = _parameter_snapshot(row.parameter_values)
        counts[snapshot] = counts.get(snapshot, 0) + 1
    return dict(sorted(counts.items()))


def _parameter_snapshot(
    parameter_values: object,
) -> str:
    """
    Deterministic canonical string for a parameter-values mapping.
    """

    if not parameter_values:
        return ""

    try:
        items = sorted(parameter_values.items())
    except AttributeError:
        return ""

    return ";".join(f"{k}={v}" for k, v in items)


# ============================================================
# CONCLUSIONS
# ============================================================


def _summary_conclusions(
    total: int,
    sufficient_count: int,
    partial_count: int,
    insufficient_count: int,
    has_sufficient: bool,
    best_by_expectancy: MetricLeader | None,
    best_by_total_r: MetricLeader | None,
    lowest_drawdown: MetricLeader | None,
    reproducible_ids: tuple[str, ...],
    dataset_coverage: dict[str, int],
    configuration_coverage: dict[str, int],
) -> list[str]:
    conclusions: list[str] = []

    if total == 0:
        conclusions.append("No persisted experiments matched the query.")
        return conclusions

    conclusions.append(f"Analysed {total} persisted experiment(s).")

    if insufficient_count:
        conclusions.append(
            f"{insufficient_count} experiment(s) have INSUFFICIENT "
            f"evidence; no reliable inference is possible for them."
        )

    if partial_count:
        conclusions.append(
            f"{partial_count} experiment(s) have PARTIAL evidence; "
            f"conclusions for them are provisional."
        )

    if not has_sufficient:
        conclusions.append(
            "No experiment has SUFFICIENT evidence; a descriptive "
            "best is NOT declared."
        )
    else:
        conclusions.append(
            f"{sufficient_count} experiment(s) have SUFFICIENT "
            f"evidence for descriptive comparison."
        )

        if best_by_expectancy is not None:
            conclusions.append(
                f"Highest expectancy (descriptive, among sufficient): "
                f"{best_by_expectancy.experiment_id} "
                f"({best_by_expectancy.value:.4f} R)."
            )

        if best_by_total_r is not None:
            conclusions.append(
                f"Highest total R (descriptive, among sufficient): "
                f"{best_by_total_r.experiment_id} "
                f"({best_by_total_r.value:.4f} R)."
            )

        if lowest_drawdown is not None:
            conclusions.append(
                f"Lowest max drawdown (descriptive, among sufficient): "
                f"{lowest_drawdown.experiment_id} "
                f"({lowest_drawdown.value:.4f} R)."
            )

    if reproducible_ids:
        conclusions.append(
            f"{len(reproducible_ids)} experiment(s) are marked fully "
            f"reproducible from recorded metadata."
        )
    else:
        conclusions.append(
            "No experiment is marked fully reproducible from "
            "recorded metadata."
        )

    conclusions.append(
        f"Dataset coverage: "
        f"{', '.join(f'{k}={v}' for k, v in dataset_coverage.items()) or 'none'}."
    )
    conclusions.append(
        f"Configuration coverage: {len(configuration_coverage)} distinct "
        f"configuration(s)."
    )

    conclusions.append(
        "All findings are descriptive, not predictive; historical "
        "experiment results do not predict future market performance."
    )

    return conclusions


# ============================================================
# ENUM COERCION
# ============================================================


def _coerce_sort_key(
    value: ExperimentSortKey | str | None,
) -> ExperimentSortKey | None:
    if value is None:
        return None

    if isinstance(value, ExperimentSortKey):
        return value

    if isinstance(value, str):
        normalized = value.strip().upper()
        try:
            return ExperimentSortKey[normalized]
        except KeyError:
            valid = ", ".join(k.name for k in ExperimentSortKey)
            raise ExperimentQueryError(
                f"Unknown sort key {value!r}. Valid keys: {valid}."
            ) from None

    raise ExperimentQueryError(
        f"Sort key must be a ExperimentSortKey or str, got "
        f"{type(value).__name__}."
    )


def _coerce_sort_order(
    value: SortOrder | str,
) -> SortOrder:
    if isinstance(value, SortOrder):
        return value

    if isinstance(value, str):
        normalized = value.strip().upper()
        try:
            return SortOrder[normalized]
        except KeyError:
            valid = ", ".join(k.name for k in SortOrder)
            raise ExperimentQueryError(
                f"Unknown sort order {value!r}. Valid orders: {valid}."
            ) from None

    raise ExperimentQueryError(
        f"Sort order must be a SortOrder or str, got "
        f"{type(value).__name__}."
    )


def _coerce_group_dimension(
    value: ExperimentGroupDimension | str,
) -> ExperimentGroupDimension:
    if isinstance(value, ExperimentGroupDimension):
        return value

    if isinstance(value, str):
        normalized = value.strip().upper()
        try:
            return ExperimentGroupDimension[normalized]
        except KeyError:
            valid = ", ".join(k.name for k in ExperimentGroupDimension)
            raise ExperimentQueryError(
                f"Unknown grouping dimension {value!r}. Valid "
                f"dimensions: {valid}."
            ) from None

    raise ExperimentQueryError(
        f"Grouping dimension must be a ExperimentGroupDimension or "
        f"str, got {type(value).__name__}."
    )


__all__ = ["ExperimentQueryEngine"]
