"""
Tests for the Experiment Query, Filtering & Analysis Layer
(Sprint 11L).

Covers:

A. Empty registry
   - empty query / group / summarize

B. Single experiment
   - single query row / group / summary

C. Multiple experiments
   - all loaded from persistence
   - deterministic default ordering

D. Filters (every important criterion)
   - experiment id
   - label
   - dataset name
   - evidence status
   - configuration hash
   - dataset content hash
   - reproducible
   - min / max completed trades
   - min / max expectancy
   - parameter values (subset / containment match)
   - combined (AND) criteria
   - no-match filter

E. Sorting
   - every sort key ascending + descending
   - stable tie-breaking by experiment id
   - default ordering (no sort key) by experiment id

F. Grouping
   - by dataset / evidence status / label / configuration
   - group counts + evidence breakdown
   - deterministic group ordering
   - empty grouping

G. Evidence-status handling
   - SUFFICIENT / PARTIAL / INSUFFICIENT counts
   - descriptive leaders computed only among SUFFICIENT
   - insufficient experiments NEVER declared best

H. Determinism
   - identical inputs -> identical outputs
   - stable across repeated calls

I. Persisted-data-only analysis
   - query / group / summarize with the pipeline patched to raise

J. Missing / optional fields
   - hand-constructed minimal results (config/reports None)
   - optional oos / leakage / robust fields handled

K. Invalid query parameters
   - unknown sort key / order / dimension (string + bad type)
   - experiment query error types

L. Registry integration
   - the engine uses the provided registry
   - registered-then-queried round trip

M. Reporting
   - formatters produce required sections
   - no misleading claims
   - empty-input handling
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from engine.config.swing_config import SwingConfig
from engine.experiment import (
    DatasetSpec,
    EvaluationConfig,
    ExperimentConfig,
    ExperimentEvidenceStatus,
    ExperimentRunner,
)
from engine.models.experiment import (
    ExperimentResult,
    ExperimentSummary,
    ReproducibilityMetadata,
)
from engine.pipeline.historical_pipeline import PipelineConfig
from engine.query import (
    ExperimentAnalysisSummary,
    ExperimentFilter,
    ExperimentGroup,
    ExperimentGroupDimension,
    ExperimentGrouping,
    ExperimentQueryEngine,
    ExperimentQueryError,
    ExperimentQueryRow,
    ExperimentSortKey,
    MetricLeader,
    SortOrder,
)
from engine.registry import ExperimentRegistry
from engine.research.research import ResearchConfig
from engine.reporting import (
    ExperimentAnalysisFormatter,
    ExperimentGroupingFormatter,
    ExperimentQueryFormatter,
)


# ============================================================
# SHARED HELPERS
# ============================================================


def _config(
    label: str = "trending-lookback-2",
    *,
    lookback: int = 2,
    dataset_name: str = "trending",
    sweep: tuple[int, ...] = (2, 3, 4),
    seed: int | None = 42,
    metadata: dict[str, str] | None = None,
) -> ExperimentConfig:
    return ExperimentConfig(
        label=label,
        dataset=DatasetSpec(name=dataset_name),
        pipeline=PipelineConfig(
            swing_config=SwingConfig(lookback=lookback),
        ),
        research=ResearchConfig(),
        evaluation=EvaluationConfig(
            parameter_name="swing_lookback",
            parameter_values=sweep,
            run_out_of_sample=True,
            run_walk_forward=True,
            run_sensitivity=True,
        ),
        strategy_parameters={"swing_lookback": str(lookback)},
        seed=seed,
        metadata=metadata if metadata is not None else {"sprint": "11L"},
    )


def _run(label: str = "trending-lookback-2", **kwargs) -> ExperimentResult:
    return ExperimentRunner().run(_config(label, **kwargs))


def _with_evidence(
    result: ExperimentResult,
    status: ExperimentEvidenceStatus,
    **metrics,
) -> ExperimentResult:
    """
    Synthetically override the evidence status (and optional metrics).

    Enforces the SAME invariant the real ``ExperimentRunner`` produces:
    ``evidence_status == SUFFICIENT`` iff ``data_sufficient == True`` (both
    are projections of ``DataSufficiencyReport.sufficient_for_inference``
    in Sprint 11J). The real runner can never emit ``SUFFICIENT`` with
    ``data_sufficient`` False, so a synthetic fixture must not either.
    ``data_sufficient`` is derived from ``status`` here unless the caller
    passes it explicitly in ``metrics``.
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


@pytest.fixture()
def registry(tmp_path: Path) -> ExperimentRegistry:
    return ExperimentRegistry(tmp_path)


@pytest.fixture()
def engine(registry: ExperimentRegistry) -> ExperimentQueryEngine:
    return ExperimentQueryEngine(registry)


@pytest.fixture()
def populated_engine(
    registry: ExperimentRegistry,
) -> ExperimentQueryEngine:
    """Register a known spread of experiments and return the engine."""

    a = _run("trending-lookback-2", lookback=2, dataset_name="trending")
    b = _run("trending-lookback-3", lookback=3, dataset_name="trending")
    c = _run("flat-lookback-2", lookback=2, dataset_name="flat")
    d = _run("flat-lookback-3", lookback=3, dataset_name="flat")

    # Evidence spread: one SUFFICIENT, one PARTIAL, two INSUFFICIENT.
    a = _with_evidence(
        a, ExperimentEvidenceStatus.PARTIAL, expectancy=0.1
    )
    b = _with_evidence(
        b,
        ExperimentEvidenceStatus.SUFFICIENT,
        expectancy=0.62,
        total_r=3.1,
        max_drawdown_r=0.9,
    )
    c = _with_evidence(c, ExperimentEvidenceStatus.INSUFFICIENT)
    d = _with_evidence(d, ExperimentEvidenceStatus.INSUFFICIENT)

    for result in (a, b, c, d):
        registry.register(result)

    return ExperimentQueryEngine(registry)


# ============================================================
# A. EMPTY REGISTRY
# ============================================================


class TestEmptyRegistry:
    def test_empty_query_returns_empty_tuple(self, engine) -> None:
        rows = engine.query()
        assert rows == ()

    def test_empty_group_returns_empty_grouping(self, engine) -> None:
        grouping = engine.group()
        assert grouping.total_experiments == 0
        assert grouping.groups == ()
        assert grouping.is_empty

    def test_empty_summary_has_no_experiments(self, engine) -> None:
        summary = engine.summarize()
        assert summary.total_experiments == 0
        assert summary.has_sufficient_evidence is False
        assert summary.best_by_expectancy is None
        assert summary.best_by_total_r is None
        assert summary.lowest_drawdown is None
        assert summary.most_reproducible_experiment_ids == ()
        assert summary.dataset_coverage == {}

    def test_empty_summary_explicit_no_best(self, engine) -> None:
        summary = engine.summarize()
        assert summary.best_by_expectancy is None
        assert summary.best_by_total_r is None
        assert summary.lowest_drawdown is None
        # The empty case makes the lack of a best explicit.
        assert any(
            "No persisted experiments matched the query." in c
            for c in summary.conclusions
        )

    def test_empty_query_with_filter_still_empty(self, engine) -> None:
        rows = engine.query(ExperimentFilter(dataset_name="trending"))
        assert rows == ()


# ============================================================
# B. SINGLE EXPERIMENT
# ============================================================


class TestSingleExperiment:
    def test_single_query_row(
        self, registry, engine
    ) -> None:
        result = _run()
        registry.register(result)

        rows = engine.query()
        assert len(rows) == 1
        row = rows[0]
        assert row.experiment_id == result.experiment_id
        assert row.label == result.label
        assert row.dataset_name == result.dataset.name
        assert row.evidence_status == result.summary.evidence_status
        assert row.completed_trades == result.summary.completed_trades
        assert row.expectancy == result.summary.expectancy

    def test_single_group(
        self, registry, engine
    ) -> None:
        result = _run()
        registry.register(result)

        grouping = engine.group(dimension=ExperimentGroupDimension.DATASET)
        assert grouping.total_experiments == 1
        assert grouping.total_groups == 1
        group = grouping.groups[0]
        assert group.name == result.dataset.name
        assert group.experiment_count == 1

    def test_single_summary(
        self, registry, engine
    ) -> None:
        result = _run()
        registry.register(result)

        summary = engine.summarize()
        assert summary.total_experiments == 1
        # The trending dataset produces INSUFFICIENT evidence.
        assert summary.insufficient_count == 1
        assert summary.has_sufficient_evidence is False


# ============================================================
# C. MULTIPLE EXPERIMENTS + DEFAULT ORDERING
# ============================================================


class TestMultipleExperiments:
    def test_all_loaded_from_persistence(
        self, populated_engine
    ) -> None:
        rows = populated_engine.query()
        assert len(rows) == 4

    def test_default_ordering_by_experiment_id(
        self, populated_engine
    ) -> None:
        rows = populated_engine.query()
        ids = [r.experiment_id for r in rows]
        assert ids == sorted(ids)

    def test_query_row_fields_are_projections(
        self, populated_engine
    ) -> None:
        rows = populated_engine.query()
        for row in rows:
            assert isinstance(row, ExperimentQueryRow)
            # configuration_hash populated from reproducibility
            assert row.configuration_hash
            # parameter_values is a persisted snapshot mapping
            assert isinstance(row.parameter_values, dict)

    def test_registry_property_exposed(
        self, registry, engine
    ) -> None:
        assert engine.registry is registry


# ============================================================
# D. FILTERS
# ============================================================


class TestFilters:
    def test_filter_by_experiment_id(self, populated_engine) -> None:
        all_rows = populated_engine.query()
        target = all_rows[0].experiment_id
        rows = populated_engine.query(
            ExperimentFilter(experiment_id=target)
        )
        assert len(rows) == 1
        assert rows[0].experiment_id == target

    def test_filter_by_label(self, populated_engine) -> None:
        rows = populated_engine.query(
            ExperimentFilter(label="flat-lookback-2")
        )
        assert len(rows) == 1
        assert rows[0].label == "flat-lookback-2"

    def test_filter_by_dataset_name(self, populated_engine) -> None:
        rows = populated_engine.query(
            ExperimentFilter(dataset_name="trending")
        )
        assert len(rows) == 2
        assert all(r.dataset_name == "trending" for r in rows)

    def test_filter_by_evidence_status(self, populated_engine) -> None:
        rows = populated_engine.query(
            ExperimentFilter(
                evidence_status=ExperimentEvidenceStatus.SUFFICIENT
            )
        )
        assert len(rows) == 1
        assert rows[0].evidence_status == ExperimentEvidenceStatus.SUFFICIENT

    def test_filter_by_configuration_hash(
        self, populated_engine
    ) -> None:
        all_rows = populated_engine.query()
        target = all_rows[0].configuration_hash
        rows = populated_engine.query(
            ExperimentFilter(configuration_hash=target)
        )
        # Two experiments share the trending-lookback-2
        # configuration only if identical; here each lookback is
        # distinct so exactly one matches.
        assert len(rows) == 1
        assert rows[0].configuration_hash == target

    def test_filter_by_dataset_content_hash(
        self, populated_engine
    ) -> None:
        all_rows = populated_engine.query()
        target = all_rows[0].dataset_content_hash
        rows = populated_engine.query(
            ExperimentFilter(dataset_content_hash=target)
        )
        # Two experiments share the trending dataset content hash.
        assert len(rows) >= 1
        assert all(r.dataset_content_hash == target for r in rows)

    def test_filter_reproducible_true(self, populated_engine) -> None:
        rows = populated_engine.query(ExperimentFilter(reproducible=True))
        assert len(rows) == 4
        assert all(r.reproducible for r in rows)

    def test_filter_reproducible_false(self, populated_engine) -> None:
        # All built-in experiments are reproducible, so this is empty.
        rows = populated_engine.query(ExperimentFilter(reproducible=False))
        assert rows == ()

    def test_filter_min_completed_trades(
        self, populated_engine
    ) -> None:
        all_rows = populated_engine.query()
        threshold = min(r.completed_trades for r in all_rows)
        rows = populated_engine.query(
            ExperimentFilter(min_completed_trades=threshold)
        )
        assert all(r.completed_trades >= threshold for r in rows)

    def test_filter_max_completed_trades(
        self, populated_engine
    ) -> None:
        all_rows = populated_engine.query()
        ceiling = max(r.completed_trades for r in all_rows)
        rows = populated_engine.query(
            ExperimentFilter(max_completed_trades=ceiling)
        )
        assert all(r.completed_trades <= ceiling for r in rows)

    def test_filter_min_expectancy(self, populated_engine) -> None:
        rows = populated_engine.query(
            ExperimentFilter(min_expectancy=0.5)
        )
        assert all(r.expectancy >= 0.5 for r in rows)

    def test_filter_max_expectancy(self, populated_engine) -> None:
        rows = populated_engine.query(
            ExperimentFilter(max_expectancy=0.0)
        )
        assert all(r.expectancy <= 0.0 for r in rows)

    def test_filter_parameter_values_subset_match(
        self, populated_engine
    ) -> None:
        rows = populated_engine.query(
            ExperimentFilter(
                parameter_values={"strategy.swing_lookback": "3"}
            )
        )
        # Two experiments used lookback 3 (trending + flat).
        assert len(rows) == 2
        assert all(
            r.parameter_values.get("strategy.swing_lookback") == "3"
            for r in rows
        )

    def test_filter_parameter_values_no_match(
        self, populated_engine
    ) -> None:
        rows = populated_engine.query(
            ExperimentFilter(
                parameter_values={"strategy.swing_lookback": "99"}
            )
        )
        assert rows == ()

    def test_filter_parameter_values_multiple_keys_and(
        self, populated_engine
    ) -> None:
        rows = populated_engine.query(
            ExperimentFilter(
                parameter_values={
                    "strategy.swing_lookback": "3",
                    "seed": "42",
                }
            )
        )
        assert len(rows) == 2

    def test_filter_combined_and_criteria(
        self, populated_engine
    ) -> None:
        rows = populated_engine.query(
            ExperimentFilter(
                dataset_name="trending",
                evidence_status=ExperimentEvidenceStatus.SUFFICIENT,
            )
        )
        assert len(rows) == 1
        assert rows[0].dataset_name == "trending"
        assert rows[0].evidence_status == ExperimentEvidenceStatus.SUFFICIENT

    def test_filter_no_match_returns_empty(
        self, populated_engine
    ) -> None:
        rows = populated_engine.query(
            ExperimentFilter(dataset_name="nonexistent")
        )
        assert rows == ()

    def test_filter_bounds_are_inclusive(self, populated_engine) -> None:
        all_rows = populated_engine.query()
        lo = min(r.expectancy for r in all_rows)
        hi = max(r.expectancy for r in all_rows)
        rows = populated_engine.query(
            ExperimentFilter(min_expectancy=lo, max_expectancy=hi)
        )
        assert len(rows) == len(all_rows)


# ============================================================
# E. SORTING
# ============================================================


class TestSorting:
    @pytest.mark.parametrize(
        "key",
        [
            ExperimentSortKey.EXPECTANCY,
            ExperimentSortKey.TOTAL_R,
            ExperimentSortKey.WIN_RATE,
            ExperimentSortKey.PROFIT_FACTOR,
            ExperimentSortKey.MAX_DRAWDOWN,
            ExperimentSortKey.COMPLETED_TRADES,
            ExperimentSortKey.EXPERIMENT_ID,
            ExperimentSortKey.LABEL,
        ],
    )
    def test_sort_ascending(self, populated_engine, key) -> None:
        rows = populated_engine.query(
            sort_key=key, sort_order=SortOrder.ASCENDING
        )
        accessor = {
            ExperimentSortKey.EXPECTANCY: lambda r: r.expectancy,
            ExperimentSortKey.TOTAL_R: lambda r: r.total_r,
            ExperimentSortKey.WIN_RATE: lambda r: r.win_rate,
            ExperimentSortKey.PROFIT_FACTOR: lambda r: r.profit_factor,
            ExperimentSortKey.MAX_DRAWDOWN: lambda r: r.max_drawdown_r,
            ExperimentSortKey.COMPLETED_TRADES: lambda r: r.completed_trades,
            ExperimentSortKey.EXPERIMENT_ID: lambda r: r.experiment_id,
            ExperimentSortKey.LABEL: lambda r: r.label,
        }[key]
        values = [accessor(r) for r in rows]
        assert values == sorted(values)

    @pytest.mark.parametrize(
        "key",
        [
            ExperimentSortKey.EXPECTANCY,
            ExperimentSortKey.TOTAL_R,
            ExperimentSortKey.WIN_RATE,
            ExperimentSortKey.PROFIT_FACTOR,
            ExperimentSortKey.MAX_DRAWDOWN,
            ExperimentSortKey.COMPLETED_TRADES,
            ExperimentSortKey.EXPERIMENT_ID,
            ExperimentSortKey.LABEL,
        ],
    )
    def test_sort_descending(self, populated_engine, key) -> None:
        rows = populated_engine.query(
            sort_key=key, sort_order=SortOrder.DESCENDING
        )
        accessor = {
            ExperimentSortKey.EXPECTANCY: lambda r: r.expectancy,
            ExperimentSortKey.TOTAL_R: lambda r: r.total_r,
            ExperimentSortKey.WIN_RATE: lambda r: r.win_rate,
            ExperimentSortKey.PROFIT_FACTOR: lambda r: r.profit_factor,
            ExperimentSortKey.MAX_DRAWDOWN: lambda r: r.max_drawdown_r,
            ExperimentSortKey.COMPLETED_TRADES: lambda r: r.completed_trades,
            ExperimentSortKey.EXPERIMENT_ID: lambda r: r.experiment_id,
            ExperimentSortKey.LABEL: lambda r: r.label,
        }[key]
        values = [accessor(r) for r in rows]
        assert values == sorted(values, reverse=True)

    def test_sort_string_key_coercion(
        self, populated_engine
    ) -> None:
        rows_enum = populated_engine.query(
            sort_key=ExperimentSortKey.EXPECTANCY,
            sort_order=SortOrder.DESCENDING,
        )
        rows_str = populated_engine.query(
            sort_key="expectancy",
            sort_order="descending",
        )
        assert [r.experiment_id for r in rows_enum] == [
            r.experiment_id for r in rows_str
        ]

    def test_sort_tie_break_by_experiment_id(
        self, registry, engine
    ) -> None:
        # Two experiments with IDENTICAL expectancy but different ids.
        a = _with_evidence(
            _run("trending-lookback-2", lookback=2),
            ExperimentEvidenceStatus.SUFFICIENT,
            expectancy=1.0,
        )
        b = _with_evidence(
            _run("flat-lookback-2", lookback=2, dataset_name="flat"),
            ExperimentEvidenceStatus.SUFFICIENT,
            expectancy=1.0,
        )
        registry.register(a)
        registry.register(b)

        rows = engine.query(
            sort_key=ExperimentSortKey.EXPECTANCY,
            sort_order=SortOrder.ASCENDING,
        )
        # Ties broken by ascending experiment id regardless of order.
        ids = [r.experiment_id for r in rows]
        assert ids == sorted(ids)

        rows_desc = engine.query(
            sort_key=ExperimentSortKey.EXPECTANCY,
            sort_order=SortOrder.DESCENDING,
        )
        ids_desc = [r.experiment_id for r in rows_desc]
        # Even descending, ties keep ascending-id order (stable).
        assert ids_desc == sorted(ids_desc)

    def test_default_no_sort_key_is_by_id(
        self, populated_engine
    ) -> None:
        rows = populated_engine.query()
        ids = [r.experiment_id for r in rows]
        assert ids == sorted(ids)


# ============================================================
# F. GROUPING
# ============================================================


class TestGrouping:
    def test_group_by_dataset(self, populated_engine) -> None:
        grouping = populated_engine.group(
            dimension=ExperimentGroupDimension.DATASET
        )
        assert grouping.dimension == ExperimentGroupDimension.DATASET
        assert grouping.total_experiments == 4
        names = [g.name for g in grouping.groups]
        assert names == sorted(names)
        assert "trending" in names
        assert "flat" in names
        trending = next(g for g in grouping.groups if g.name == "trending")
        assert trending.experiment_count == 2

    def test_group_by_evidence_status(
        self, populated_engine
    ) -> None:
        grouping = populated_engine.group(
            dimension=ExperimentGroupDimension.EVIDENCE_STATUS
        )
        names = [g.name for g in grouping.groups]
        assert "SUFFICIENT" in names
        assert "PARTIAL" in names
        assert "INSUFFICIENT" in names
        sufficient = next(
            g for g in grouping.groups if g.name == "SUFFICIENT"
        )
        assert sufficient.sufficient_count == 1
        assert sufficient.insufficient_count == 0

    def test_group_by_label(self, populated_engine) -> None:
        grouping = populated_engine.group(
            dimension=ExperimentGroupDimension.LABEL
        )
        # Each label is unique here -> 4 groups.
        assert grouping.total_groups == 4
        assert grouping.total_experiments == 4
        for group in grouping.groups:
            assert group.experiment_count == 1

    def test_group_by_configuration(self, populated_engine) -> None:
        grouping = populated_engine.group(
            dimension=ExperimentGroupDimension.CONFIGURATION
        )
        # Four distinct configurations -> four groups.
        assert grouping.total_groups == 4
        assert sum(g.experiment_count for g in grouping.groups) == 4

    def test_group_evidence_breakdown(
        self, populated_engine
    ) -> None:
        grouping = populated_engine.group(
            dimension=ExperimentGroupDimension.DATASET
        )
        trending = next(
            g for g in grouping.groups if g.name == "trending"
        )
        assert trending.sufficient_count == 1
        assert trending.partial_count == 1
        assert trending.insufficient_count == 0

    def test_group_deterministic_order(
        self, populated_engine
    ) -> None:
        g1 = populated_engine.group(
            dimension=ExperimentGroupDimension.DATASET
        )
        g2 = populated_engine.group(
            dimension=ExperimentGroupDimension.DATASET
        )
        assert [grp.name for grp in g1.groups] == [
            grp.name for grp in g2.groups
        ]

    def test_group_with_filter(
        self, populated_engine
    ) -> None:
        grouping = populated_engine.group(
            filter=ExperimentFilter(dataset_name="trending"),
            dimension=ExperimentGroupDimension.EVIDENCE_STATUS,
        )
        assert grouping.total_experiments == 2
        names = [g.name for g in grouping.groups]
        assert set(names) == {"SUFFICIENT", "PARTIAL"}

    def test_group_empty(self, engine) -> None:
        grouping = engine.group(
            dimension=ExperimentGroupDimension.DATASET
        )
        assert grouping.is_empty
        assert grouping.total_experiments == 0

    def test_group_dimension_string_coercion(
        self, populated_engine
    ) -> None:
        grouping = populated_engine.group(dimension="dataset")
        assert grouping.dimension == ExperimentGroupDimension.DATASET


# ============================================================
# G. EVIDENCE-STATUS HANDLING
# ============================================================


class TestEvidenceHandling:
    def test_counts_match_spread(self, populated_engine) -> None:
        summary = populated_engine.summarize()
        assert summary.sufficient_count == 1
        assert summary.partial_count == 1
        assert summary.insufficient_count == 2
        assert summary.total_experiments == 4

    def test_leaders_only_among_sufficient(
        self, populated_engine
    ) -> None:
        summary = populated_engine.summarize()
        # The SUFFICIENT experiment is the lookback-3 trending one.
        assert summary.best_by_expectancy is not None
        assert summary.best_by_expectancy.value == 0.62
        assert summary.best_by_total_r is not None
        assert summary.best_by_total_r.value == 3.1
        assert summary.lowest_drawdown is not None
        assert summary.lowest_drawdown.value == 0.9

    def test_insufficient_never_declared_best(
        self, registry, engine
    ) -> None:
        # Only INSUFFICIENT experiments -> no best declared.
        a = _with_evidence(
            _run("a", lookback=2),
            ExperimentEvidenceStatus.INSUFFICIENT,
            expectancy=10.0,  # huge but INSUFFICIENT
            total_r=100.0,
        )
        b = _with_evidence(
            _run("b", lookback=3),
            ExperimentEvidenceStatus.INSUFFICIENT,
            expectancy=5.0,
        )
        registry.register(a)
        registry.register(b)

        summary = engine.summarize()
        assert summary.has_sufficient_evidence is False
        assert summary.best_by_expectancy is None
        assert summary.best_by_total_r is None
        assert summary.lowest_drawdown is None
        assert any(
            "descriptive best is NOT declared" in c
            for c in summary.conclusions
        )

    def test_partial_experiments_excluded_from_leaders(
        self, registry, engine
    ) -> None:
        # A PARTIAL experiment with the highest expectancy must NOT be
        # the descriptive leader.
        a = _with_evidence(
            _run("a", lookback=2),
            ExperimentEvidenceStatus.PARTIAL,
            expectancy=2.0,
        )
        b = _with_evidence(
            _run("b", lookback=3),
            ExperimentEvidenceStatus.SUFFICIENT,
            expectancy=0.5,
        )
        registry.register(a)
        registry.register(b)

        summary = engine.summarize()
        assert summary.best_by_expectancy is not None
        assert summary.best_by_expectancy.experiment_id == b.experiment_id
        assert summary.best_by_expectancy.value == 0.5

    def test_lowest_drawdown_among_sufficient_only(
        self, registry, engine
    ) -> None:
        a = _with_evidence(
            _run("a", lookback=2),
            ExperimentEvidenceStatus.SUFFICIENT,
            expectancy=0.3,
            max_drawdown_r=2.0,
        )
        b = _with_evidence(
            _run("b", lookback=3),
            ExperimentEvidenceStatus.INSUFFICIENT,
            max_drawdown_r=0.1,  # lower but INSUFFICIENT
        )
        registry.register(a)
        registry.register(b)

        summary = engine.summarize()
        assert summary.lowest_drawdown is not None
        # The INSUFFICIENT 0.1 must be ignored; leader is a (2.0).
        assert summary.lowest_drawdown.experiment_id == a.experiment_id
        assert summary.lowest_drawdown.value == 2.0


# ============================================================
# G2. EVIDENCE-STATUS / DATA-SUFFICIENT INVARIANT
# ============================================================


class TestEvidenceDataSufficientInvariant:
    """
    The real ``ExperimentRunner`` (Sprint 11J) structurally guarantees:

        evidence_status == SUFFICIENT  <=>  data_sufficient == True

    because both are projections of the SAME
    ``DataSufficiencyReport.sufficient_for_inference`` flag
    (``_evidence_status`` returns SUFFICIENT only when that flag is True,
    and ``data_sufficient`` is literally ``bool(sufficient_for_inference)``).

    A naturally-produced result can therefore NEVER be
    ``SUFFICIENT + data_sufficient=False``. These tests guard that
    invariant for both the real runner and the synthetic fixture helper,
    so an impossible combination can never silently enter a demo/report.
    """

    def test_runner_natural_results_respect_invariant(self) -> None:
        # Every built-in dataset/lookback combo the runner can produce
        # naturally must satisfy the invariant.
        for dataset_name in ("trending", "flat", "minimal"):
            for lookback in (2, 3, 4):
                result = _run(
                    f"{dataset_name}-{lookback}",
                    lookback=lookback,
                    dataset_name=dataset_name,
                )
                summary = result.summary
                if summary.evidence_status == ExperimentEvidenceStatus.SUFFICIENT:
                    assert summary.data_sufficient is True
                else:
                    assert summary.data_sufficient is False

    def test_fixture_sufficient_implies_data_sufficient(
        self, registry, engine
    ) -> None:
        # The synthetic SUFFICIENT fixture must not produce the
        # impossible SUFFICIENT + data_sufficient=False combination.
        result = _with_evidence(
            _run("sufficient", lookback=3),
            ExperimentEvidenceStatus.SUFFICIENT,
            expectancy=0.62,
        )
        assert result.summary.evidence_status == ExperimentEvidenceStatus.SUFFICIENT
        assert result.summary.data_sufficient is True

    def test_fixture_non_sufficient_implies_not_data_sufficient(
        self, registry, engine
    ) -> None:
        for status in (
            ExperimentEvidenceStatus.PARTIAL,
            ExperimentEvidenceStatus.INSUFFICIENT,
        ):
            result = _with_evidence(
                _run(f"{status.value}", lookback=2),
                status,
            )
            assert result.summary.evidence_status == status
            assert result.summary.data_sufficient is False

    def test_populated_fixture_respects_invariant(
        self, populated_engine
    ) -> None:
        rows = populated_engine.query()
        for row in rows:
            if row.evidence_status == ExperimentEvidenceStatus.SUFFICIENT:
                assert row.data_sufficient is True
            else:
                assert row.data_sufficient is False


# ============================================================
# H. DETERMINISM
# ============================================================


class TestDeterminism:
    def test_query_deterministic(self, populated_engine) -> None:
        first = populated_engine.query(
            sort_key=ExperimentSortKey.EXPECTANCY,
            sort_order=SortOrder.DESCENDING,
        )
        second = populated_engine.query(
            sort_key=ExperimentSortKey.EXPECTANCY,
            sort_order=SortOrder.DESCENDING,
        )
        assert first == second

    def test_group_deterministic(self, populated_engine) -> None:
        first = populated_engine.group(
            dimension=ExperimentGroupDimension.DATASET
        )
        second = populated_engine.group(
            dimension=ExperimentGroupDimension.DATASET
        )
        assert first == second

    def test_summarize_deterministic(self, populated_engine) -> None:
        first = populated_engine.summarize()
        second = populated_engine.summarize()
        assert first == second

    def test_query_stable_across_repeated_calls(
        self, populated_engine
    ) -> None:
        for _ in range(3):
            rows = populated_engine.query()
            assert len(rows) == 4


# ============================================================
# I. PERSISTED-DATA-ONLY ANALYSIS
# ============================================================


class TestPersistedDataOnly:
    def test_query_uses_persisted_results_no_rerun(
        self, populated_engine, monkeypatch
    ) -> None:
        import engine.pipeline.historical_pipeline as pipeline_mod

        def explode(*args, **kwargs):
            raise AssertionError(
                "Query must use persisted results, not rerun."
            )

        monkeypatch.setattr(
            pipeline_mod.HistoricalEvaluationPipeline,
            "evaluate",
            explode,
        )

        rows = populated_engine.query()
        assert len(rows) == 4

    def test_group_uses_persisted_results_no_rerun(
        self, populated_engine, monkeypatch
    ) -> None:
        import engine.pipeline.historical_pipeline as pipeline_mod

        def explode(*args, **kwargs):
            raise AssertionError(
                "Group must use persisted results, not rerun."
            )

        monkeypatch.setattr(
            pipeline_mod.HistoricalEvaluationPipeline,
            "evaluate",
            explode,
        )

        grouping = populated_engine.group(
            dimension=ExperimentGroupDimension.DATASET
        )
        assert grouping.total_experiments == 4

    def test_summarize_uses_persisted_results_no_rerun(
        self, populated_engine, monkeypatch
    ) -> None:
        import engine.pipeline.historical_pipeline as pipeline_mod

        def explode(*args, **kwargs):
            raise AssertionError(
                "Summarize must use persisted results, not rerun."
            )

        monkeypatch.setattr(
            pipeline_mod.HistoricalEvaluationPipeline,
            "evaluate",
            explode,
        )

        summary = populated_engine.summarize()
        assert summary.total_experiments == 4


# ============================================================
# J. MISSING / OPTIONAL FIELDS
# ============================================================


class TestMissingOptionalFields:
    def _minimal_result(
        self,
        experiment_id: str = "exp-minimal00000000",
        evidence_status: ExperimentEvidenceStatus = ExperimentEvidenceStatus.INSUFFICIENT,
        reproducible: bool = True,
    ) -> ExperimentResult:
        repro = ReproducibilityMetadata(
            experiment_id=experiment_id,
            configuration_hash="hash",
            configuration_representation="repr",
            dataset_identity="trending",
            dataset_content_hash=None,
            dataset_size=0,
            reproducible=reproducible,
        )
        summary = ExperimentSummary(
            completed_trades=0,
            win_rate=0.0,
            expectancy=0.0,
            total_r=0.0,
            profit_factor=0.0,
            max_drawdown_r=0.0,
            robust=None,
            descriptive_best=None,
            oos_expectancy=None,
            oos_trades=0,
            data_sufficient=False,
            leakage_passed=None,
            leakage_not_verified=False,
            evidence_status=evidence_status,
        )
        return ExperimentResult(
            experiment_id=experiment_id,
            config=None,
            dataset=DatasetSpec(name="trending"),
            dataset_size=0,
            evaluation_report=None,
            research_report=None,
            reproducibility=repro,
            summary=summary,
            label="minimal",
        )

    def test_minimal_result_queryable(
        self, registry, engine
    ) -> None:
        result = self._minimal_result()
        registry.register(result)

        rows = engine.query()
        assert len(rows) == 1
        row = rows[0]
        assert row.experiment_id == "exp-minimal00000000"
        assert row.dataset_name == "trending"
        assert row.dataset_content_hash is None
        assert row.robust is None
        assert row.oos_expectancy is None
        assert row.leakage_passed is None
        assert row.parameter_values == {}

    def test_minimal_result_summary(
        self, registry, engine
    ) -> None:
        registry.register(self._minimal_result())
        summary = engine.summarize()
        assert summary.total_experiments == 1
        assert summary.insufficient_count == 1
        assert summary.best_by_expectancy is None
        assert summary.most_reproducible_experiment_ids == (
            "exp-minimal00000000",
        )

    def test_non_reproducible_filterable(
        self, registry, engine
    ) -> None:
        registry.register(
            self._minimal_result(reproducible=False)
        )
        rows = engine.query(ExperimentFilter(reproducible=False))
        assert len(rows) == 1
        assert rows[0].reproducible is False

        repro_rows = engine.query(ExperimentFilter(reproducible=True))
        assert repro_rows == ()


# ============================================================
# K. INVALID QUERY PARAMETERS
# ============================================================


class TestInvalidQueryParameters:
    def test_unknown_sort_key_string(self, engine) -> None:
        with pytest.raises(ExperimentQueryError):
            engine.query(sort_key="nonsense")

    def test_unknown_sort_order_string(self, engine) -> None:
        with pytest.raises(ExperimentQueryError):
            engine.query(
                sort_key=ExperimentSortKey.EXPECTANCY,
                sort_order="sideways",
            )

    def test_unknown_group_dimension_string(self, engine) -> None:
        with pytest.raises(ExperimentQueryError):
            engine.group(dimension="nope")

    def test_bad_sort_key_type(self, engine) -> None:
        with pytest.raises(ExperimentQueryError):
            engine.query(sort_key=123)

    def test_bad_sort_order_type(self, engine) -> None:
        with pytest.raises(ExperimentQueryError):
            engine.query(sort_order=123)

    def test_bad_group_dimension_type(self, engine) -> None:
        with pytest.raises(ExperimentQueryError):
            engine.group(dimension=123)

    def test_valid_sort_key_case_insensitive(
        self, populated_engine
    ) -> None:
        rows = populated_engine.query(sort_key="Expectancy")
        assert len(rows) == 4


# ============================================================
# L. REGISTRY INTEGRATION
# ============================================================


class TestRegistryIntegration:
    def test_registered_then_queried_round_trip(
        self, registry, engine
    ) -> None:
        result = _run()
        registry.register(result)

        rows = engine.query()
        assert len(rows) == 1
        assert rows[0].experiment_id == result.experiment_id
        assert rows[0].configuration_hash == (
            result.reproducibility.configuration_hash
        )

    def test_engine_reflects_new_registrations(
        self, registry, engine
    ) -> None:
        assert engine.query() == ()
        registry.register(_run("first", lookback=2))
        assert len(engine.query()) == 1
        registry.register(_run("second", lookback=3))
        assert len(engine.query()) == 2

    def test_engine_reflects_deletes(
        self, registry, engine
    ) -> None:
        result = _run()
        registry.register(result)
        assert len(engine.query()) == 1
        registry.delete(result.experiment_id)
        assert engine.query() == ()

    def test_query_id_matches_registry_id(
        self, registry, engine
    ) -> None:
        result = _run()
        registry.register(result)
        row = engine.query()[0]
        assert row.experiment_id == registry.list()[0]


# ============================================================
# M. REPORTING
# ============================================================


class TestReporting:
    def test_query_formatter_has_sections(
        self, populated_engine
    ) -> None:
        rows = populated_engine.query()
        report = ExperimentQueryFormatter().format(rows)
        assert "Experiment Query Results" in report
        assert "Matched experiments: 4" in report
        assert "descriptive, not predictive" in report

    def test_query_formatter_empty(self, engine) -> None:
        report = ExperimentQueryFormatter().format(engine.query())
        assert "No persisted experiments matched the query." in report

    def test_grouping_formatter_has_sections(
        self, populated_engine
    ) -> None:
        grouping = populated_engine.group(
            dimension=ExperimentGroupDimension.DATASET
        )
        report = ExperimentGroupingFormatter().format(grouping)
        assert "Experiment Grouping Report" in report
        assert "Grouping dimension : DATASET" in report
        assert "Group: trending" in report

    def test_grouping_formatter_empty(self, engine) -> None:
        grouping = engine.group()
        report = ExperimentGroupingFormatter().format(grouping)
        assert "No groups" in report

    def test_analysis_formatter_has_sections(
        self, populated_engine
    ) -> None:
        summary = populated_engine.summarize()
        report = ExperimentAnalysisFormatter().format(summary)
        assert "Experiment Analysis Summary" in report
        assert "Overview" in report
        assert "Descriptive Leaders" in report
        assert "SUFFICIENT only" in report
        assert "Reproducibility" in report
        assert "Coverage" in report
        assert "Conclusions" in report
        assert "descriptive, not predictive" in report

    def test_analysis_formatter_no_best_without_evidence(
        self, registry, engine
    ) -> None:
        registry.register(
            _with_evidence(
                _run(),
                ExperimentEvidenceStatus.INSUFFICIENT,
            )
        )
        summary = engine.summarize()
        report = ExperimentAnalysisFormatter().format(summary)
        assert "N/A" in report
        assert "no experiment has SUFFICIENT evidence" in report

    def test_analysis_formatter_leaders_shown(
        self, populated_engine
    ) -> None:
        summary = populated_engine.summarize()
        report = ExperimentAnalysisFormatter().format(summary)
        assert "Highest expectancy" in report
        assert "Highest total R" in report
        assert "Lowest max drawdown" in report


# ============================================================
# N. IMMODELABILITY / FROZEN MODELS
# ============================================================


class TestImmutability:
    def test_query_row_frozen(self, populated_engine) -> None:
        row = populated_engine.query()[0]
        with pytest.raises(FrozenInstanceError):
            row.expectancy = 99.0  # type: ignore[misc]

    def test_filter_frozen(self) -> None:
        f = ExperimentFilter(dataset_name="trending")
        with pytest.raises(FrozenInstanceError):
            f.dataset_name = "flat"  # type: ignore[misc]

    def test_group_frozen(self, populated_engine) -> None:
        group = populated_engine.group().groups[0]
        with pytest.raises(FrozenInstanceError):
            group.name = "other"  # type: ignore[misc]

    def test_summary_frozen(self, populated_engine) -> None:
        summary = populated_engine.summarize()
        with pytest.raises(FrozenInstanceError):
            summary.total_experiments = 99  # type: ignore[misc]

    def test_metric_leader_frozen(self) -> None:
        leader = MetricLeader(experiment_id="exp-x", value=1.0)
        with pytest.raises(FrozenInstanceError):
            leader.value = 2.0  # type: ignore[misc]
