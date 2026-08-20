"""
Tests for the operator-facing paper-trading cycle CLI
(``scripts/run_paper_trading_cycle.py``).

The CLI is a THIN interface over the EXISTING Product Phase 5 operations
workflow. These tests cover CLI behavior only: argument parsing/defaults,
provider-env defaulting, report formatting, exit behavior, determinism and
no-look-ahead wiring. All trading intelligence, decision, geometry,
lifecycle and duplicate-prevention semantics remain covered by
``tests/test_paper_trading_operations.py``.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, "src")
sys.path.insert(0, ".")
sys.path.insert(0, "scripts")

import run_paper_trading_cycle as cli

from dashboard.views import InstrumentOperationRowView, OperationsCycleView


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------


def _row(**overrides) -> InstrumentOperationRowView:
    base = dict(
        instrument="NIFTY",
        analysed=True,
        actionability="READY_FOR_REVIEW",
        eligible_for_paper_trade=True,
        decision_classification="QUALIFIED",
        direction="LONG",
        evaluation_timestamp=datetime(2025, 1, 25, 5, 0, tzinfo=timezone.utc),
        provider_status="OK",
        freshness_state="STALE",
    )
    base.update(overrides)
    return InstrumentOperationRowView(**base)


def _view(**overrides) -> OperationsCycleView:
    base = dict(
        cycle_id="opcycle-0123456789abcdef",
        status="READY",
        reference_now=datetime(2025, 1, 25, 5, 0, tzinfo=timezone.utc),
        provider="fixture",
        freshness="STALE",
        instruments_scanned=1,
        instruments_analysed=1,
        results=(_row(),),
        rationale="cycle rationale",
    )
    base.update(overrides)
    return OperationsCycleView(**base)


def _format(view: OperationsCycleView) -> str:
    return cli.format_cycle_report(
        view, timeframe="15m", capital="100000", risk_percent="1",
    )


# ---------------------------------------------------------------------------
# A. ARGUMENT PARSING
# ---------------------------------------------------------------------------


class TestArgumentParsing:
    def test_defaults(self):
        args = cli.parse_args([])
        assert args.instruments == "NIFTY,RELIANCE,TCS,HDFCBANK,ICICIBANK"
        assert args.timeframe == "15m"
        assert args.capital == "100000"
        assert args.risk_percent == "1"

    def test_custom_arguments(self):
        args = cli.parse_args([
            "--instruments", "NIFTY,RELIANCE",
            "--timeframe", "5m",
            "--capital", "50000",
            "--risk-percent", "0.5",
        ])
        assert args.instruments == "NIFTY,RELIANCE"
        assert args.timeframe == "5m"
        assert args.capital == "50000"
        assert args.risk_percent == "0.5"

    @pytest.mark.parametrize("bad", ["abc", "0", "-5", "nan", "inf"])
    def test_invalid_capital_rejected(self, bad):
        with pytest.raises(SystemExit) as exc:
            cli.parse_args(["--capital", bad])
        assert exc.value.code == 2

    @pytest.mark.parametrize("bad", ["abc", "0", "-1", "nan"])
    def test_invalid_risk_percent_rejected(self, bad):
        with pytest.raises(SystemExit) as exc:
            cli.parse_args(["--risk-percent", bad])
        assert exc.value.code == 2


class TestParseInstruments:
    def test_parses_and_canonicalizes(self):
        assert cli.parse_instruments(" nifty , reliance,") == (
            "NIFTY", "RELIANCE",
        )

    def test_deduplicates(self):
        assert cli.parse_instruments("NIFTY,NIFTY") == ("NIFTY",)

    def test_empty_rejected(self):
        with pytest.raises(ValueError):
            cli.parse_instruments(" , ,")


# ---------------------------------------------------------------------------
# B. PROVIDER ENV DEFAULTING
# ---------------------------------------------------------------------------


class TestProviderEnv:
    def test_defaults_to_yahoo_when_unset(self, monkeypatch):
        monkeypatch.delenv("DASHBOARD_PROVIDER", raising=False)
        assert cli._resolve_provider_name() == "yahoo"
        assert os.environ["DASHBOARD_PROVIDER"] == "yahoo"

    def test_explicit_value_never_overwritten(self, monkeypatch):
        monkeypatch.setenv("DASHBOARD_PROVIDER", "fixture")
        assert cli._resolve_provider_name() == "fixture"
        assert os.environ["DASHBOARD_PROVIDER"] == "fixture"


# ---------------------------------------------------------------------------
# C. REPORT FORMATTING
# ---------------------------------------------------------------------------


class TestFormatCycleReport:
    def test_required_header_fields(self):
        report = _format(_view())
        assert "PAPER TRADING OPERATIONS CYCLE" in report
        assert "Provider:                  fixture" in report
        assert "Timeframe (setup):         15m" in report
        assert "Account capital:           100,000" in report
        assert "Risk percent:              1%" in report
        assert "Cycle status:              READY" in report
        assert "Freshness:                 STALE" in report
        assert "Reference / evaluation at: 2025-01-25T05:00:00+00:00" in report
        assert "Instruments scanned:       1" in report
        assert "Instruments analyzed:      1" in report
        assert "Cycle id:                  opcycle-0123456789abcdef" in report

    def test_paper_trading_banner_shown_twice(self):
        report = _format(_view())
        assert report.count(cli.PAPER_TRADING_BANNER) == 2

    def test_per_instrument_fields(self):
        report = _format(_view())
        assert "NIFTY" in report
        assert "Decision classification: QUALIFIED" in report
        assert "Actionability/opportunity: READY_FOR_REVIEW" in report
        assert "Direction:               LONG" in report
        assert "Geometry:                complete" in report
        assert "Paper trade created:     no" in report

    def test_created_trade_and_duplicate_shown(self):
        row = _row(
            created=("pt-aaa",),
            duplicate=True,
            duplicate_paper_trade_id="pt-bbb",
        )
        report = _format(_view(results=(row,)))
        assert "Paper trade created:     YES (pt-aaa)" in report
        assert "Duplicate:               YES — skipped (existing pt-bbb)" in report

    def test_updated_and_closed_shown(self):
        row = _row(updated=("pt-upd",), closed=("pt-cls",))
        report = _format(_view(
            results=(row,), trades_updated=1, trades_closed=1,
        ))
        assert "Trades updated:          pt-upd" in report
        assert "Trades closed:           pt-cls" in report
        assert "Trades updated:            1" in report
        assert "Trades closed:             1" in report

    def test_geometry_unavailable_honest(self):
        row = _row(
            actionability="TRADE_GEOMETRY_UNAVAILABLE",
            eligible_for_paper_trade=False,
        )
        report = _format(_view(results=(row,)))
        assert "Geometry:                unavailable" in report

    def test_geometry_not_applicable_when_no_opportunity(self):
        row = _row(
            actionability="NO_OPPORTUNITY", eligible_for_paper_trade=False,
        )
        report = _format(_view(results=(row,)))
        assert "Geometry:                not applicable (no eligible opportunity)" in report

    def test_error_row_shown_honestly(self):
        row = _row(analysed=False, error=True, reason="NIFTY: provider boom")
        report = _format(_view(results=(row,), errors=("NIFTY: provider boom",)))
        assert "Instrument error:        YES (failure isolated)" in report
        assert "Geometry:                not assessed (instrument error)" in report
        assert "- NIFTY: provider boom" in report

    def test_empty_results_honest(self):
        report = _format(_view(
            results=(), instruments_scanned=0, instruments_analysed=0,
        ))
        assert "(no instruments produced an outcome this cycle)" in report

    def test_totals_errors_warnings(self):
        report = _format(_view(
            trades_created=2, duplicates_skipped=3, active_trades=4,
            errors=("e1",), warnings=("w1", "w2"),
        ))
        assert "Trades created:            2" in report
        assert "Duplicates skipped:        3" in report
        assert "Active trades:             4" in report
        assert "Errors (1):" in report and "  - e1" in report
        assert "Warnings (2):" in report and "  - w1" in report and "  - w2" in report

    def test_no_errors_warnings_honest(self):
        report = _format(_view())
        assert "Errors (0):\n  (none)" in report
        assert "Warnings (0):\n  (none)" in report

    def test_missing_reference_timestamp_unavailable(self):
        report = _format(_view(reference_now=None))
        assert "Reference / evaluation at: unavailable" in report

    def test_deterministic(self):
        assert _format(_view()) == _format(_view())

    def test_no_recommendation_language(self):
        report = _format(_view())
        for token in ("BUY", "SELL", "ENTER", "EXIT", "HOLD"):
            assert token not in report
        assert "guarantee" not in report.lower()


# ---------------------------------------------------------------------------
# D. EXIT BEHAVIOR
# ---------------------------------------------------------------------------


class TestExitBehavior:
    def test_empty_instruments_is_config_error(self, capsys):
        assert cli.main(["--instruments", ""]) == 2
        assert "empty watchlist" in capsys.readouterr().err

    def test_runtime_failure_returns_nonzero(self, monkeypatch, capsys):
        import dashboard.services as services

        def _boom(**kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(services, "default_service", _boom)
        monkeypatch.setenv("DASHBOARD_PROVIDER", "fixture")
        assert cli.main([]) == 1
        assert "could not be executed" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# E. END-TO-END (fixture provider, default paper-trade directory)
# ---------------------------------------------------------------------------


@pytest.fixture()
def fixture_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_PROVIDER", "fixture")
    return tmp_path


class TestEndToEnd:
    def test_successful_cycle_exit_zero(self, fixture_env, capsys):
        assert cli.main([]) == 0
        out = capsys.readouterr().out
        assert cli.PAPER_TRADING_BANNER in out
        assert "Provider:                  fixture" in out
        assert "Cycle id:                  opcycle-" in out
        for instrument in cli.DEFAULT_INSTRUMENTS:
            assert f"  {instrument}\n" in out
        assert "Instruments scanned:       5" in out
        assert "Trades created:" in out
        assert "Trades updated:" in out
        assert "Trades closed:" in out
        assert "Duplicates skipped:" in out
        assert "Active trades:" in out
        assert "Errors (" in out
        assert "Warnings (" in out

    def test_zero_opportunities_not_an_error(self, fixture_env, capsys):
        # The deterministic fixture cycle surfaces no READY_FOR_REVIEW
        # opportunity (geometry unavailable) — zero trades is exit 0.
        assert cli.main([]) == 0
        assert "Trades created:            0" in capsys.readouterr().out

    def test_uses_default_paper_trade_directory(self, fixture_env, monkeypatch):
        import dashboard.paper_trade_store as store_mod

        captured = {}

        real_store = store_mod.PaperTradeStore

        def _spy(directory=None):
            captured["directory"] = directory
            return real_store(directory=directory)

        monkeypatch.setattr(store_mod, "PaperTradeStore", _spy)
        assert cli.main([]) == 0
        assert captured["directory"] == fixture_env / "paper_trades"

    def test_deterministic_cycle_id(self, fixture_env, capsys):
        assert cli.main([]) == 0
        first = capsys.readouterr().out
        assert cli.main([]) == 0
        second = capsys.readouterr().out

        def _cycle_id(out: str) -> str:
            return next(
                line.split("opcycle-", 1)[1] for line in out.splitlines()
                if "opcycle-" in line
            )

        assert _cycle_id(first) == _cycle_id(second)

    def test_custom_arguments_flow_through(self, fixture_env, capsys):
        assert cli.main([
            "--instruments", "NIFTY",
            "--timeframe", "15m",
            "--capital", "50000",
            "--risk-percent", "2",
        ]) == 0
        out = capsys.readouterr().out
        assert "Account capital:           50,000" in out
        assert "Risk percent:              2%" in out
        assert "Instruments scanned:       1" in out


# ---------------------------------------------------------------------------
# F. NO LOOK-AHEAD WIRING (the CLI adds no future-candle path)
# ---------------------------------------------------------------------------


class TestNoLookAhead:
    def test_outcome_evaluator_not_called(self, fixture_env, monkeypatch):
        from engine.intelligence.historical_outcome import OutcomeEvaluator

        def _boom(self, *args, **kwargs):
            raise AssertionError("OutcomeEvaluator must not be called")

        monkeypatch.setattr(OutcomeEvaluator, "evaluate", _boom)
        assert cli.main([]) == 0

    def test_historical_pipeline_not_called(self, fixture_env, monkeypatch):
        from engine.pipeline.historical_pipeline import (
            HistoricalEvaluationPipeline,
        )

        def _boom(self, *args, **kwargs):
            raise AssertionError("pipeline must not be re-run")

        monkeypatch.setattr(HistoricalEvaluationPipeline, "evaluate", _boom)
        assert cli.main([]) == 0
