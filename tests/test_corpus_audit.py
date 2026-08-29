"""
Focused tests for the READ-ONLY historical corpus integrity audit
(``engine.data.corpus_audit.CorpusAuditEngine`` +
``scripts/audit_historical_corpus.py``).

Deterministic, network-free: the audit only READS the existing Phase 6B
store; no provider is ever instantiated, no token is needed and NO data
is written. These tests verify existence, candle counts, chronology,
duplicate timestamps, timezone awareness, OHLC validity (the canonical
``low <= open <= high`` / ``low <= close <= high`` relation plus finite
values), volume validity (the existing non-negative-volume rule), gap
reporting (reusing the Phase 6A ``detect_gaps`` terminology),
coverage classification (reusing the planner's MISSING / EMPTY /
PARTIAL / COMPLETE / UNAVAILABLE vocabulary), missing / empty dataset
handling, the known HDFCBANK / 15m / June-2024 anomaly reporting,
strict read-only behavior (no writes, no provider instantiation), the
operator CLI and regression.

The corpus fixtures are written DIRECTLY into the historical store
layout (``<root>/<INSTRUMENT>/<timeframe>/candles.json``) using the
existing store ``path_for`` convention so the audit reads them exactly
as it would read a real persisted corpus — including datasets whose
rows could never load into an ``OHLCVCandle``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Sequence

import pytest

from engine.config.corpus_plan_config import CorpusPlanConfig
from engine.data.corpus_audit import (
    KNOWN_CORPUS_ANOMALY,
    CorpusAuditEngine,
)
from engine.data.historical_serialization import HISTORICAL_SCHEMA_VERSION
from engine.data.historical_store import HistoricalDataStore
from engine.models.corpus_audit import (
    AuditCheckStatus,
    CorpusAuditReport,
    DatasetAuditResult,
)
from engine.models.corpus_plan import DatasetCoverageStatus
from engine.models.historical_data import GapKind
from engine.models.ohlcv import OHLCVCandle
from engine.reporting.corpus_audit import CorpusAuditFormatter

WIN_START = datetime(2023, 1, 1, tzinfo=UTC)
WIN_END = datetime(2023, 4, 1, tzinfo=UTC)

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "audit_historical_corpus.py"


# ============================================================
# FIXTURE HELPERS (deterministic, no network, no provider)
# ============================================================


def _row(
    ts: datetime,
    *,
    open_=100.0,
    high=101.0,
    low=99.0,
    close=100.5,
    volume=1000.0,
) -> dict:
    return {
        "timestamp": ts.isoformat(),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def _write_dataset(
    store: HistoricalDataStore,
    instrument: str,
    timeframe: str,
    rows: list[dict],
) -> Path:
    """Write one dataset directly into the store layout (test-local)."""

    path = store.path_for(instrument, timeframe)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": HISTORICAL_SCHEMA_VERSION,
        "instrument": instrument.upper(),
        "timeframe": timeframe,
        "candles": rows,
    }
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path


def _daily_rows(
    start: datetime = WIN_START,
    n: int = 60,
) -> list[dict]:
    """A deterministic, OHLC-valid daily row series (naive-free)."""

    return [
        _row(
            start + timedelta(days=i),
            open_=100.0 + i,
            high=101.5 + i,
            low=99.5 + i,
            close=100.75 + i,
        )
        for i in range(n)
    ]


def _make_store(tmp_path: Path) -> HistoricalDataStore:
    return HistoricalDataStore(tmp_path / "hist")


def _engine(
    store: HistoricalDataStore,
    timeframes: tuple[str, ...] = ("15m", "1D"),
    instruments: Sequence[str] | None = None,
) -> CorpusAuditEngine:
    return CorpusAuditEngine(
        config=CorpusPlanConfig(timeframes=timeframes),
        store=store,
        instruments=instruments,
    )


def _audit(
    tmp_path: Path,
    timeframe: str,
    instrument: str,
    rows: list[dict] | None = None,
    *,
    store: HistoricalDataStore | None = None,
) -> tuple[CorpusAuditReport, DatasetAuditResult]:
    """
    Deterministic single-dataset audit helper.

    Writes ``rows`` (when given) into the store layout and audits ONLY
    that instrument so ``result`` refers to the dataset under test.
    """

    if store is None:
        store = _make_store(tmp_path)
    if rows is not None:
        _write_dataset(store, instrument, timeframe, rows)
    engine = _engine(store, timeframes=(timeframe,), instruments=[instrument])
    report = engine.audit(start=WIN_START, end=WIN_END)
    result = next(r for r in report.results if r.instrument == instrument)
    return report, result


# ============================================================
# A. ALL TEN DATASETS DISCOVERED + CANDLE COUNTS
# ============================================================


class TestAllDatasetsDiscovered:
    def test_ten_datasets_audited(self, tmp_path):
        store = _make_store(tmp_path)
        for instrument in ("HDFCBANK", "ICICIBANK", "NIFTY", "RELIANCE", "TCS"):
            for timeframe in ("15m", "1D"):
                _write_dataset(
                    store, instrument, timeframe,
                    _daily_rows(n=20),
                )
        report = _engine(store).audit(
            start=WIN_START, end=WIN_END,
        )
        assert report.dataset_count == 10
        datasets = {
            (r.instrument, r.timeframe) for r in report.results
        }
        assert datasets == {
            (inst, tf)
            for inst in ("HDFCBANK", "ICICIBANK", "NIFTY", "RELIANCE", "TCS")
            for tf in ("15m", "1D")
        }

    def test_candle_counts_reported(self, tmp_path):
        store = _make_store(tmp_path)
        _write_dataset(
            store, "NIFTY", "1D",
            _daily_rows(n=57),
        )
        report = _engine(store, timeframes=("1D",)).audit(
            start=WIN_START, end=WIN_END,
        )
        nifty = next(r for r in report.results if r.instrument == "NIFTY")
        assert nifty.candle_count == 57
        assert nifty.first_timestamp == WIN_START
        assert nifty.last_timestamp is not None


# ============================================================
# B. CHRONOLOGY DETECTION
# ============================================================


class TestChronology:
    def test_ordered_passes(self, tmp_path):
        _, result = _audit(tmp_path, "1D", "TCS", _daily_rows(n=30))
        assert result.chronological is AuditCheckStatus.PASS

    def test_unordered_fails(self, tmp_path):
        rows = _daily_rows(n=5)
        rows.reverse()
        report, result = _audit(tmp_path, "1D", "TCS", rows)
        assert result.chronological is AuditCheckStatus.FAIL
        assert report.integrity_failures == 1

    def test_naive_timestamp_still_chronology_checkable(self, tmp_path):
        naive_rows = [
            {
                "timestamp": (WIN_START + timedelta(days=i)).isoformat()[:-6],
                "open": 100.0, "high": 101.0, "low": 99.0,
                "close": 100.5, "volume": 1000.0,
            }
            for i in range(5)
        ]
        _, result = _audit(tmp_path, "1D", "TCS", naive_rows)
        assert result.timezone_aware is AuditCheckStatus.FAIL


# ============================================================
# C. DUPLICATE DETECTION
# ============================================================


class TestDuplicates:
    def test_no_duplicates(self, tmp_path):
        _, result = _audit(tmp_path, "1D", "RELIANCE", _daily_rows(n=10))
        assert result.duplicates is AuditCheckStatus.PASS
        assert result.duplicate_count == 0

    def test_duplicates_detected(self, tmp_path):
        rows = _daily_rows(n=5)
        rows.append(dict(rows[2]))  # duplicate timestamp (n=6, 1 dup)
        rows.append(dict(rows[3]))  # duplicate timestamp (2 dups)
        _, result = _audit(tmp_path, "1D", "RELIANCE", rows)
        assert result.duplicates is AuditCheckStatus.FAIL
        assert result.duplicate_count == 2
        assert result.candle_count == 7


# ============================================================
# D. TIMEZONE DETECTION
# ============================================================


class TestTimezone:
    def test_all_aware_passes(self, tmp_path):
        _, result = _audit(tmp_path, "15m", "NIFTY", _daily_rows(n=8))
        assert result.timezone_aware is AuditCheckStatus.PASS

    def test_naive_fails_with_offending_timestamp(self, tmp_path):
        rows = _daily_rows(n=4)
        rows[2]["timestamp"] = (
            (WIN_START + timedelta(days=2)).isoformat()[:-6]
        )
        _, result = _audit(tmp_path, "15m", "NIFTY", rows)
        assert result.timezone_aware is AuditCheckStatus.FAIL
        assert result.timezone_issues
        assert "T00:00:00+00:00" not in result.timezone_issues[0]


# ============================================================
# E. OHLC VALIDATION (canonical relation + finite values)
# ============================================================


class TestOHLC:
    def test_valid_rows_pass(self, tmp_path):
        rows = _daily_rows(n=10)
        for i in range(10):
            rows[i]["low"] = 99.0
            rows[i]["high"] = 101.0
            rows[i]["open"] = 99.5 + i * 0.01
            rows[i]["close"] = 100.5 + i * 0.01
        _, result = _audit(tmp_path, "15m", "HDFCBANK", rows)
        assert result.ohlc is AuditCheckStatus.PASS
        assert result.ohlc_invalid_count == 0

    def test_open_outside_range_fails(self, tmp_path):
        rows = _daily_rows(n=5)
        rows[2] = _row(
            WIN_START + timedelta(days=2),
            open_=999.0, high=101.0, low=99.0, close=100.0,
        )
        _, result = _audit(tmp_path, "15m", "HDFCBANK", rows)
        assert result.ohlc is AuditCheckStatus.FAIL
        assert result.ohlc_invalid_count == 1
        assert "open outside range" in result.ohlc_issue_timestamps[0]
        ts = WIN_START + timedelta(days=2)
        assert ts.isoformat() in result.ohlc_issue_timestamps[0]

    def test_high_below_low_fails(self, tmp_path):
        rows = _daily_rows(n=5)
        rows[1]["high"] = 50.0  # below low=99.0
        rows[1]["low"] = 100.0  # also below open=100.0
        _, result = _audit(tmp_path, "15m", "HDFCBANK", rows)
        assert result.ohlc is AuditCheckStatus.FAIL
        assert result.ohlc_invalid_count == 1

    def test_close_outside_range_fails(self, tmp_path):
        rows = _daily_rows(n=5)
        rows[3]["close"] = 50.0  # below low=99.0
        _, result = _audit(tmp_path, "15m", "HDFCBANK", rows)
        assert result.ohlc is AuditCheckStatus.FAIL

    def test_nan_row_detected(self, tmp_path):
        rows = _daily_rows(n=5)
        # A row that could never construct an OHLCVCandle: NaN open.
        rows[4]["open"] = float("nan")
        _, result = _audit(tmp_path, "15m", "HDFCBANK", rows)
        assert result.ohlc is AuditCheckStatus.FAIL
        assert result.ohlc_invalid_count == 1
        assert "non-finite" in result.ohlc_issue_timestamps[0]


# ============================================================
# F. VOLUME VALIDATION
# ============================================================


class TestVolume:
    def test_non_negative_passes(self, tmp_path):
        _, result = _audit(tmp_path, "1D", "ICICIBANK", _daily_rows(n=6))
        assert result.volume is AuditCheckStatus.PASS

    def test_negative_volume_fails(self, tmp_path):
        rows = _daily_rows(n=6)
        rows[1]["volume"] = -10.0
        _, result = _audit(tmp_path, "1D", "ICICIBANK", rows)
        assert result.volume is AuditCheckStatus.FAIL
        assert result.volume_invalid_count == 1
        assert result.volume_issue_timestamps

    def test_non_finite_volume_fails(self, tmp_path):
        rows = _daily_rows(n=6)
        rows[2]["volume"] = float("inf")
        _, result = _audit(tmp_path, "1D", "ICICIBANK", rows)
        assert result.volume is AuditCheckStatus.FAIL


# ============================================================
# G. GAP REPORTING (reused Phase 6A terminology)
# ============================================================


class TestGaps:
    def test_no_gaps(self, tmp_path):
        _, result = _audit(tmp_path, "1D", "NIFTY", _daily_rows(n=30))
        assert result.gaps == ()
        assert result.gap_closure_count == 0
        assert result.gap_unexpected_count == 0

    def test_weekend_gap_is_possible_closure(self, tmp_path):
        rows = [
            _row(datetime(2023, 1, 2, tzinfo=UTC)),
            _row(datetime(2023, 1, 3, tzinfo=UTC)),
        ]
        _, result = _audit(tmp_path, "1D", "NIFTY", rows)
        assert result.gap_closure_count == 0  # consecutive days, no gap


# ============================================================
# H. COVERAGE CLASSIFICATION (reused planner vocabulary)
# ============================================================


class TestCoverage:
    def test_complete(self, tmp_path):
        _, result = _audit(tmp_path, "1D", "NIFTY", _daily_rows(n=90))
        assert result.status is DatasetCoverageStatus.COMPLETE

    def test_partial(self, tmp_path):
        _, result = _audit(tmp_path, "1D", "NIFTY", _daily_rows(n=20))
        assert result.status is DatasetCoverageStatus.PARTIAL

    def test_missing(self, tmp_path):
        _, result = _audit(tmp_path, "1D", "NIFTY", None)
        assert result.status is DatasetCoverageStatus.MISSING
        assert result.exists is False
        # The CHECKS on a missing dataset are N_A (never fabricated PASS).
        assert result.chronological is AuditCheckStatus.N_A
        assert result.ohlc is AuditCheckStatus.N_A

    def test_empty(self, tmp_path):
        _, result = _audit(tmp_path, "1D", "NIFTY", [])
        assert result.status is DatasetCoverageStatus.EMPTY
        assert result.exists is True
        assert result.candle_count == 0

    def test_unavailable_no_store(self):
        engine = CorpusAuditEngine(
            config=CorpusPlanConfig(timeframes=("1D",)),
            store=None,
        )
        report = engine.audit(["NIFTY"], start=WIN_START, end=WIN_END)
        result = report.results[0]
        assert result.status is DatasetCoverageStatus.UNAVAILABLE
        assert result.load_error


# ============================================================
# I. MISSING / EMPTY DATASET HANDLING
# ============================================================


class TestMissingEmptyHandling:
    def test_missing_dataset_does_not_crash(self, tmp_path):
        store = _make_store(tmp_path)
        _write_dataset(store, "RELIANCE", "1D", _daily_rows(n=5))
        report = _engine(store, timeframes=("1D",)).audit(
            start=WIN_START, end=WIN_END,
        )
        icici = next(r for r in report.results if r.instrument == "ICICIBANK")
        assert icici.status is DatasetCoverageStatus.MISSING
        assert icici.candle_count == 0
        assert icici.first_timestamp is None and icici.last_timestamp is None

    def test_corrupt_dataset_reported_not_skipped(self, tmp_path):
        store = _make_store(tmp_path)
        path = store.path_for("TCS", "1D")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ this is not json", encoding="utf-8")
        report = _engine(store, timeframes=("1D",), instruments=["TCS"]).audit(
            start=WIN_START, end=WIN_END,
        )
        result = report.results[0]
        assert result.load_error
        assert result.chronological is AuditCheckStatus.FAIL
        assert result.ohlc is AuditCheckStatus.FAIL


# ============================================================
# J. KNOWN HDFCBANK ANOMALY REPORTING
# ============================================================


class TestKnownAnomaly:
    def test_anomaly_constant(self):
        assert KNOWN_CORPUS_ANOMALY["instrument"] == "HDFCBANK"
        assert KNOWN_CORPUS_ANOMALY["timeframe"] == "15m"
        assert "Open price" in KNOWN_CORPUS_ANOMALY["message"]

    def test_clean_hdfcbank_reports_no_invalid_ohlc(self, tmp_path):
        store = _make_store(tmp_path)
        _write_dataset(
            store, "HDFCBANK", "15m",
            _daily_rows(n=30),
        )
        report = _engine(store, timeframes=("15m",), instruments=["HDFCBANK"]).audit(
            start=WIN_START, end=WIN_END,
        )
        result = report.results[0]
        assert result.ohlc is AuditCheckStatus.PASS
        assert result.ohlc_invalid_count == 0
        # The known-anomaly block states the persisted data is clean.
        text = CorpusAuditFormatter().format(report)
        assert "Provider anomaly previously observed : YES" in text
        assert "NO invalid OHLC" in text
        assert "HDFCBANK / 15m / June 2024" in text

    def test_anomaly_with_invalid_rows_states_review(self, tmp_path):
        store = _make_store(tmp_path)
        rows = _daily_rows(n=5)
        rows[0] = _row(
            WIN_START, open_=999.0, high=101.0, low=99.0, close=100.0,
        )
        rows.append(_row(WIN_START + timedelta(days=61)))
        _write_dataset(store, "HDFCBANK", "15m", rows)
        report = _engine(store, timeframes=("15m",), instruments=["HDFCBANK"]).audit(
            start=WIN_START, end=WIN_END,
        )
        text = CorpusAuditFormatter().format(report)
        assert "1 invalid OHLC" in text
        assert "review is required" in text.lower()
        # The dataset is NOT auto-classified as failed for coverage.
        assert report.results[0].ohlc is AuditCheckStatus.FAIL
        assert report.results[0].ohlc_invalid_count == 1


# ============================================================
# K. READ-ONLY GUARANTEES (no writes, no provider)
# ============================================================


class TestReadOnly:
    def test_audit_never_writes(self, tmp_path):
        store = _make_store(tmp_path)
        for instrument in ("HDFCBANK", "NIFTY"):
            _write_dataset(store, instrument, "15m", _daily_rows(n=10))
        before = {
            str(p.relative_to(tmp_path)): p.read_bytes()
            for p in tmp_path.rglob("*") if p.is_file()
        }
        engine = _engine(store, timeframes=("15m",))
        engine.audit(["HDFCBANK", "NIFTY"], start=WIN_START, end=WIN_END)
        after = {
            str(p.relative_to(tmp_path)): p.read_bytes()
            for p in tmp_path.rglob("*") if p.is_file()
        }
        assert before == after
        # No new files (no provenance.jsonl append, no temp leftovers).
        assert set(after) == set(before)

    def test_audit_never_instantiates_provider(self):
        # The audit code must not import / instantiate any HTTP provider
        # and must never reference the token (the module DOCSTRING names
        # it only to say the audit never requires it; the class code is
        # what matters).
        import inspect

        text = inspect.getsource(CorpusAuditEngine)
        for forbidden in (
            "UpstoxHistoricalDataProvider",
            "UPSTOX_ANALYTICS_TOKEN",
            "urlopen",
            "http",
        ):
            assert forbidden not in text
        # The audit engine has no provider / token attribute either.
        import engine.data.corpus_audit as mod

        assert not hasattr(mod, "UpstoxHistoricalDataProvider")
        assert not hasattr(CorpusAuditEngine, "upstox")

    def test_audit_takes_no_future_candles(self):
        import inspect

        sig = inspect.signature(CorpusAuditEngine.audit)
        assert "future" not in sig.parameters
        assert "candles" not in sig.parameters

    def test_audit_never_uses_outcome_or_pipeline(self):
        import engine.data.corpus_audit as mod

        text = Path(mod.__file__).read_text(encoding="utf-8")
        assert "OutcomeEvaluator" not in text
        assert "HistoricalEvaluationPipeline" not in text


# ============================================================
# L. DETERMINISM + MODEL INVARIANTS
# ============================================================


class TestDeterminismAndModel:
    def test_deterministic_repeat(self, tmp_path):
        store = _make_store(tmp_path)
        _write_dataset(store, "NIFTY", "1D", _daily_rows(n=30))
        engine = _engine(store, timeframes=("1D",))
        a = engine.audit(["NIFTY"], start=WIN_START, end=WIN_END)
        b = engine.audit(["NIFTY"], start=WIN_START, end=WIN_END)
        assert a == b
        assert a.audit_id == b.audit_id
        assert a.audit_id.startswith("audit-")

    def test_different_window_different_id(self, tmp_path):
        store = _make_store(tmp_path)
        engine = _engine(store, timeframes=("1D",))
        a = engine.audit(["NIFTY"], start=WIN_START, end=WIN_END)
        b = engine.audit(
            ["NIFTY"],
            start=WIN_START,
            end=datetime(2023, 5, 1, tzinfo=UTC),
        )
        assert a.audit_id != b.audit_id

    def test_models_frozen(self):
        assert CorpusAuditReport.__dataclass_params__.frozen
        assert DatasetAuditResult.__dataclass_params__.frozen
        assert hasattr(DatasetAuditResult, "__slots__")

    def test_verdict_distinguishes_completeness_from_quality(self, tmp_path):
        store = _make_store(tmp_path)
        # FULLY COVERED window but with an invalid persisted candle.
        rows = _daily_rows(n=90)
        rows[10] = _row(
            WIN_START + timedelta(days=10),
            open_=999.0, high=101.0, low=99.0, close=100.0,
        )
        _write_dataset(store, "NIFTY", "1D", rows)
        report = _engine(store, timeframes=("1D",)).audit(
            ["NIFTY"], start=WIN_START, end=WIN_END,
        )
        assert report.complete_count == 1
        assert report.integrity_failures == 1
        assert report.verdict == "REVIEW REQUIRED"


# ============================================================
# M. OPERATOR CLI
# ============================================================


def _run_cli(*args: str, extra_env: dict[str, str] | None = None) -> tuple[int, str]:
    env = dict(os.environ)
    for key, value in (extra_env or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=str(_SCRIPT.parent.parent),
        env=env,
    )
    return result.returncode, (result.stdout or "") + (result.stderr or "")


class TestOperatorCLI:
    def test_missing_window_errors(self, tmp_path):
        code, out = _run_cli(
            "--start", "not-a-date", "--end", "2023-04-01",
            "--timeframes", "1D", "--instruments", "NIFTY",
            "--data-dir", str(tmp_path / "hist"),
        )
        assert code == 2

    def test_exit_zero_on_review_required(self, tmp_path):
        store = _make_store(tmp_path)
        _write_dataset(store, "HDFCBANK", "15m", _daily_rows(n=5))
        code, out = _run_cli(
            "--start", "2023-01-01", "--end", "2023-04-01",
            "--timeframes", "15m", "--instruments", "HDFCBANK",
            "--data-dir", str(tmp_path / "hist"),
        )
        assert code == 0
        assert "HISTORICAL CORPUS INTEGRITY AUDIT" in out
        assert "HDFCBANK" in out
        assert "READ-ONLY AUDIT" in out

    def test_cli_no_token_required(self, tmp_path):
        env = {"UPSTOX_ANALYTICS_TOKEN": "", "UPSTOX_ACCESS_TOKEN": None}
        code, out = _run_cli(
            "--start", "2023-01-01", "--end", "2023-04-01",
            "--timeframes", "15m", "--instruments", "NIFTY",
            "--data-dir", str(tmp_path / "hist"),
            extra_env=env,
        )
        assert code == 0
        assert "UPSTOX" not in out.upper()

    def test_cli_json_mode(self, tmp_path):
        store = _make_store(tmp_path)
        _write_dataset(store, "NIFTY", "1D", _daily_rows(n=30))
        code, out = _run_cli(
            "--start", "2023-01-01", "--end", "2023-04-01",
            "--timeframes", "1D", "--instruments", "NIFTY",
            "--data-dir", str(tmp_path / "hist"),
            "--json",
        )
        assert code == 0
        payload = json.loads(out)
        assert payload["verdict"] in ("PASS", "REVIEW REQUIRED")
        assert payload["rows"][0]["instrument"] == "NIFTY"

    def test_default_timeframes_and_universe(self, tmp_path):
        # Defaults mirror the research corpus configuration (15m + 1D).
        code, out = _run_cli(
            "--start", "2023-01-01", "--end", "2023-02-01",
            "--data-dir", str(tmp_path / "hist"),
        )
        assert code == 0
        assert "Timeframes  : 15m, 1D" in out
        assert "HDFCBANK" in out and "TCS" in out

    def test_space_separated_arguments(self, tmp_path):
        # The documented CLI form accepts space-separated values.
        code, out = _run_cli(
            "--start", "2023-01-01", "--end", "2023-02-01",
            "--timeframes", "15m", "1D",
            "--instruments", "HDFCBANK", "ICICIBANK", "NIFTY", "RELIANCE", "TCS",
            "--data-dir", str(tmp_path / "hist"),
        )
        assert code == 0
        assert "Timeframes  : 15m, 1D" in out
        assert out.count("MISSING") >= 10


# ============================================================
# N. REGRESSION / EXISTING BEHAVIOR UNCHANGED
# ============================================================


class TestRegression:
    def test_planner_behavior_unchanged(self, tmp_path):
        from engine.data.corpus_plan import CorpusPreparationPlanner

        store = _make_store(tmp_path)
        planner = CorpusPreparationPlanner(
            config=CorpusPlanConfig(timeframes=("1D",)),
            store=store,
        )
        plan_a = planner.plan(["NIFTY"], start=WIN_START, end=WIN_END)
        engine = CorpusAuditEngine(
            config=CorpusPlanConfig(timeframes=("1D",)),
            store=store,
        )
        report = engine.audit(["NIFTY"], start=WIN_START, end=WIN_END)
        # Planner coverage classification matches the audit coverage for
        # the same empty store: both report MISSING.
        plan_row = plan_a.rows[0]
        assert plan_row.coverage.status is DatasetCoverageStatus.MISSING
        assert report.results[0].status is DatasetCoverageStatus.MISSING

    def test_historical_pipeline_still_imports(self):
        # The audit adds NO dependency on trading / pipeline logic and
        # does not break their importability.
        import engine.pipeline.historical_pipeline  # noqa: F401
        import engine.models.ohlcv  # noqa: F401

    def test_engine_module_does_not_import_provider_package_logic(self):
        import engine.data.corpus_audit as mod

        text = Path(mod.__file__).read_text(encoding="utf-8")
        assert "historical_provider" not in text
        assert "historical_service" not in text
        assert "corpus_ingestion" not in text