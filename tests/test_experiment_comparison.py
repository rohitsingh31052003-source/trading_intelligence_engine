"""
Tests for the experiment comparison and comparison reporting
(Sprint 11J).

Covers:

D. Comparison
   - two experiment comparison
   - equal results
   - differing results
   - insufficient-data handling
   - OOS handling
   - robustness handling
   - leakage status handling
   - best-by-expectancy / best-by-total-r among SUFFICIENT only
   - no best declared without sufficient evidence

E. Comparison Reporting
   - required sections exist
   - no misleading claims
   - insufficient evidence is explicitly reported
   - empty comparison
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from engine.config.swing_config import SwingConfig
from engine.experiment import (
    DatasetSpec,
    EvaluationConfig,
    ExperimentComparisonEngine,
    ExperimentConfig,
    ExperimentEvidenceStatus,
    ExperimentRunner,
)
from engine.models.experiment import (
    ExperimentComparison,
    ExperimentComparisonRow,
    ExperimentResult,
    ExperimentSummary,
)
from engine.pipeline.historical_pipeline import PipelineConfig
from engine.research.research import ResearchConfig
from engine.reporting import ExperimentComparisonFormatter


# ============================================================
# SHARED HELPERS
# ============================================================


def _base_config(label: str = "test") -> ExperimentConfig:
    return ExperimentConfig(
        label=label,
        dataset=DatasetSpec(name="trending"),
        pipeline=PipelineConfig(
            swing_config=SwingConfig(lookback=2),
        ),
        research=ResearchConfig(),
        evaluation=EvaluationConfig(
            parameter_name="swing_lookback",
            parameter_values=(2, 3),
            run_out_of_sample=False,
            run_walk_forward=False,
            run_sensitivity=False,
        ),
        seed=1,
    )


def _real_result(label: str = "test") -> ExperimentResult:
    """Run a real experiment (insufficient evidence on the demo data)."""

    runner = ExperimentRunner()
    return runner.run(_base_config(label))


def _sufficient_result(
    label: str,
    *,
    expectancy: float = 1.0,
    total_r: float = 5.0,
    completed_trades: int = 10,
    win_rate: float = 60.0,
    profit_factor: float = 2.0,
    max_drawdown_r: float = 1.0,
    robust: bool | None = True,
    oos_expectancy: float | None = 0.5,
    oos_trades: int = 5,
    data_sufficient: bool = True,
    leakage_passed: bool | None = True,
    leakage_not_verified: bool = False,
) -> ExperimentResult:
    """
    Build an ExperimentResult with a SUFFICIENT evidence status.

    The underlying experiment is run for real; only the summary
    (which the comparison reads) is replaced with a controlled
    SUFFICIENT summary so the comparison's gating logic is
    exercised honestly without faking the engine.
    """

    base = _real_result(label)
    summary = replace(
        base.summary,
        completed_trades=completed_trades,
        win_rate=win_rate,
        expectancy=expectancy,
        total_r=total_r,
        profit_factor=profit_factor,
        max_drawdown_r=max_drawdown_r,
        robust=robust,
        oos_expectancy=oos_expectancy,
        oos_trades=oos_trades,
        data_sufficient=data_sufficient,
        leakage_passed=leakage_passed,
        leakage_not_verified=leakage_not_verified,
        evidence_status=ExperimentEvidenceStatus.SUFFICIENT,
    )
    return replace(base, summary=summary)


def _insufficient_result(
    label: str,
    *,
    expectancy: float = 0.0,
    total_r: float = 0.0,
) -> ExperimentResult:
    """Build a real experiment with INSUFFICIENT evidence."""

    base = _real_result(label)
    summary = replace(
        base.summary,
        expectancy=expectancy,
        total_r=total_r,
        evidence_status=ExperimentEvidenceStatus.INSUFFICIENT,
    )
    return replace(base, summary=summary)


# ============================================================
# D. COMPARISON
# ============================================================


class TestComparison:
    """Experiment comparison logic."""

    def test_two_experiment_comparison(self) -> None:
        r1 = _real_result("a")
        r2 = _real_result("b")
        comp = ExperimentComparisonEngine().compare([r1, r2])
        assert isinstance(comp, ExperimentComparison)
        assert comp.experiment_count == 2
        assert len(comp.rows) == 2
        assert all(
            isinstance(row, ExperimentComparisonRow) for row in comp.rows
        )

    def test_equal_results(self) -> None:
        r1 = _real_result("a")
        r2 = _real_result("a")  # same config -> same id
        comp = ExperimentComparisonEngine().compare([r1, r2])
        assert comp.rows[0].experiment_id == comp.rows[1].experiment_id
        assert comp.rows[0].expectancy == comp.rows[1].expectancy

    def test_differing_results(self) -> None:
        r1 = _sufficient_result("a", expectancy=1.0, total_r=5.0)
        r2 = _sufficient_result("b", expectancy=2.0, total_r=8.0)
        comp = ExperimentComparisonEngine().compare([r1, r2])
        assert comp.rows[0].expectancy != comp.rows[1].expectancy

    def test_best_by_expectancy_among_sufficient_only(self) -> None:
        r_low = _sufficient_result("low", expectancy=1.0, total_r=5.0)
        r_high = _sufficient_result("high", expectancy=3.0, total_r=12.0)
        comp = ExperimentComparisonEngine().compare([r_low, r_high])
        assert comp.best_by_expectancy == r_high.experiment_id

    def test_best_by_total_r_among_sufficient_only(self) -> None:
        r_low = _sufficient_result("low", expectancy=1.0, total_r=5.0)
        r_high = _sufficient_result("high", expectancy=3.0, total_r=12.0)
        comp = ExperimentComparisonEngine().compare([r_low, r_high])
        assert comp.best_by_total_r == r_high.experiment_id

    def test_no_best_declared_without_sufficient_evidence(self) -> None:
        r1 = _insufficient_result("a", expectancy=1.0, total_r=5.0)
        r2 = _insufficient_result("b", expectancy=9.0, total_r=99.0)
        comp = ExperimentComparisonEngine().compare([r1, r2])
        # Even though r2 has higher descriptive expectancy, it is
        # insufficient, so no best is declared.
        assert comp.best_by_expectancy is None
        assert comp.best_by_total_r is None
        assert comp.has_sufficient_evidence is False

    def test_best_excludes_insufficient(self) -> None:
        r_insuf = _insufficient_result("insuf", expectancy=100.0, total_r=999.0)
        r_suf = _sufficient_result("suf", expectancy=1.0, total_r=5.0)
        comp = ExperimentComparisonEngine().compare([r_insuf, r_suf])
        # The sufficient (lower) experiment wins; the high but
        # insufficient experiment must never be "best".
        assert comp.best_by_expectancy == r_suf.experiment_id
        assert comp.best_by_total_r == r_suf.experiment_id

    def test_insufficient_data_handling(self) -> None:
        r1 = _insufficient_result("a")
        r2 = _insufficient_result("b")
        comp = ExperimentComparisonEngine().compare([r1, r2])
        assert len(comp.insufficient_experiments) == 2
        assert len(comp.sufficient_experiments) == 0
        assert any(
            "INSUFFICIENT" in c for c in comp.conclusions
        )

    def test_partial_evidence_handling(self) -> None:
        base = _real_result("partial")
        summary = replace(
            base.summary,
            completed_trades=10,
            evidence_status=ExperimentEvidenceStatus.PARTIAL,
            data_sufficient=False,
        )
        r = replace(base, summary=summary)
        comp = ExperimentComparisonEngine().compare([r])
        assert len(comp.partial_experiments) == 1
        assert any("PARTIAL" in c for c in comp.conclusions)

    def test_oos_handling(self) -> None:
        r1 = _sufficient_result("a", oos_expectancy=0.5, oos_trades=5)
        r2 = _sufficient_result("b", oos_expectancy=None, oos_trades=0)
        comp = ExperimentComparisonEngine().compare([r1, r2])
        rows_by_id = {row.experiment_id: row for row in comp.rows}
        assert rows_by_id[r1.experiment_id].oos_trades == 5
        assert rows_by_id[r2.experiment_id].oos_trades == 0
        assert rows_by_id[r1.experiment_id].oos_expectancy == 0.5
        assert rows_by_id[r2.experiment_id].oos_expectancy is None

    def test_robustness_handling(self) -> None:
        r1 = _sufficient_result("robust", robust=True)
        r2 = _sufficient_result("not-robust", robust=False)
        comp = ExperimentComparisonEngine().compare([r1, r2])
        rows_by_id = {row.experiment_id: row for row in comp.rows}
        assert rows_by_id[r1.experiment_id].robust is True
        assert rows_by_id[r2.experiment_id].robust is False
        # Conclusion mentions robust configuration.
        assert any(
            "robust" in c.lower() for c in comp.conclusions
        )

    def test_robust_none_handling(self) -> None:
        r1 = _sufficient_result("a", robust=None)
        comp = ExperimentComparisonEngine().compare([r1])
        assert comp.rows[0].robust is None

    def test_leakage_status_handling_passed(self) -> None:
        r1 = _sufficient_result("clean", leakage_passed=True)
        comp = ExperimentComparisonEngine().compare([r1])
        assert comp.rows[0].leakage_passed is True
        assert comp.rows[0].leakage_not_verified is False
        assert not any(
            "Leakage violations" in c for c in comp.conclusions
        )

    def test_leakage_status_handling_failed(self) -> None:
        r1 = _sufficient_result("leaky", leakage_passed=False)
        comp = ExperimentComparisonEngine().compare([r1])
        assert comp.rows[0].leakage_passed is False
        assert any(
            "Leakage violations" in c for c in comp.conclusions
        )

    def test_leakage_not_verified_handling(self) -> None:
        r1 = _sufficient_result(
            "unverified",
            leakage_passed=True,
            leakage_not_verified=True,
        )
        comp = ExperimentComparisonEngine().compare([r1])
        assert comp.rows[0].leakage_not_verified is True

    def test_conclusions_descriptive_not_predictive(self) -> None:
        r1 = _sufficient_result("a", expectancy=1.0)
        r2 = _sufficient_result("b", expectancy=2.0)
        comp = ExperimentComparisonEngine().compare([r1, r2])
        combined = " ".join(comp.conclusions).lower()
        assert "descriptive" in combined
        assert "predictive" in combined

    def test_comparison_immutable(self) -> None:
        from dataclasses import FrozenInstanceError

        comp = ExperimentComparisonEngine().compare([_real_result("a")])
        with pytest.raises(FrozenInstanceError):
            comp.best_by_expectancy = "x"  # type: ignore[misc]

    def test_comparison_deterministic(self) -> None:
        r1 = _sufficient_result("a", expectancy=1.0)
        r2 = _sufficient_result("b", expectancy=2.0)
        c1 = ExperimentComparisonEngine().compare([r1, r2])
        c2 = ExperimentComparisonEngine().compare([r1, r2])
        assert c1.best_by_expectancy == c2.best_by_expectancy
        assert c1.conclusions == c2.conclusions


# ============================================================
# E. COMPARISON REPORTING
# ============================================================


class TestComparisonReporting:
    """Comparison report formatter."""

    def test_required_sections_exist(self) -> None:
        r1 = _sufficient_result("a", expectancy=1.0)
        r2 = _sufficient_result("b", expectancy=2.0)
        comp = ExperimentComparisonEngine().compare([r1, r2])
        report = ExperimentComparisonFormatter().format(comp)
        for section in (
            "Comparison Summary",
            "Side-by-Side Metrics",
            "Descriptive Ranking",
            "Comparison Conclusions",
        ):
            assert section in report

    def test_report_contains_metrics(self) -> None:
        r1 = _sufficient_result("a", expectancy=1.0)
        r2 = _sufficient_result("b", expectancy=2.0)
        comp = ExperimentComparisonEngine().compare([r1, r2])
        report = ExperimentComparisonFormatter().format(comp)
        for metric in (
            "Completed Trades",
            "Win Rate",
            "Expectancy",
            "Total R",
            "Profit Factor",
            "Max Drawdown",
            "Robust",
            "OOS Expectancy",
            "OOS Trades",
            "Data Sufficient",
            "Leakage Passed",
        ):
            assert metric in report

    def test_no_misleading_claims(self) -> None:
        r1 = _sufficient_result("a", expectancy=1.0)
        r2 = _sufficient_result("b", expectancy=2.0)
        comp = ExperimentComparisonEngine().compare([r1, r2])
        report = ExperimentComparisonFormatter().format(comp).lower()
        for forbidden in (
            "is profitable",
            "is best",
            "guaranteed",
            "will profit",
        ):
            assert forbidden not in report

    def test_insufficient_evidence_explicitly_reported(self) -> None:
        r1 = _insufficient_result("a")
        r2 = _insufficient_result("b")
        comp = ExperimentComparisonEngine().compare([r1, r2])
        report = ExperimentComparisonFormatter().format(comp)
        assert "INSUFFICIENT" in report
        assert "NOT declared" in report or "No descriptive best" in report

    def test_best_declared_when_sufficient(self) -> None:
        r1 = _sufficient_result("a", expectancy=1.0)
        r2 = _sufficient_result("b", expectancy=3.0)
        comp = ExperimentComparisonEngine().compare([r1, r2])
        report = ExperimentComparisonFormatter().format(comp)
        assert comp.best_by_expectancy is not None
        assert "descriptive" in report.lower()

    def test_empty_comparison_report(self) -> None:
        comp = ExperimentComparisonEngine().compare([])
        report = ExperimentComparisonFormatter().format(comp)
        assert "No experiments" in report

    def test_report_deterministic(self) -> None:
        r1 = _sufficient_result("a", expectancy=1.0)
        r2 = _sufficient_result("b", expectancy=2.0)
        comp = ExperimentComparisonEngine().compare([r1, r2])
        formatter = ExperimentComparisonFormatter()
        assert formatter.format(comp) == formatter.format(comp)
