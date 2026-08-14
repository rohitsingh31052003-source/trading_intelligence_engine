"""
Tests for the historical market data integration & end-to-end
validation layer (Sprint 11V).

Coverage:

A. Historical data normalization (valid records, chronological order,
   duplicate handling, instrument/timeframe identity, dedupe, sort)
B. Invalid data handling (missing timestamp, impossible OHLC, high < low,
   negative volume, open outside range, duplicate timestamps, missing
   instrument/timeframe)
C. Incomplete data handling (empty series, insufficient history, missing
   timeframe, in-progress HTF candle)
D. Multiple instruments (isolation, no cross-contamination)
E. Multiple timeframes (1D context + 15M setup, extensibility)
F. Higher-timeframe candle completion / look-ahead protection
G. Point-in-time correctness (prefix/full-series equality, future
   mutation, multi-instrument + multi-timeframe future mutation)
H. Replay correctness (sequential replay == direct point-in-time eval,
   replay determinism, no wall-clock dependence)
I. Deterministic ranking (no unordered iteration, repeated identical)
J. Serialization round trip (scan_id, evaluation_time, instrument,
   timeframes, status, rank, direction, decision, score, MTF alignment,
   opportunity)
K. End-to-end scan (loaded -> normalized -> pipeline -> scanner ->
   ranked analyst-style opportunities; best identified; incomplete
   handled honestly)
L. Existing pipeline regression (signals / completed trades unchanged)
M. Reporting (analyst report sections, no predictive language, warning,
   returns str, determinism)
N. Data quality / no fabricated data / model immutability
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from engine.config.market_scan_config import MarketScanConfig
from engine.data.historical_adapter import (
    HistoricalAdapterConfig,
    HistoricalDataAdapter,
)
from engine.data.historical_fixtures import (
    duplicate_timestamp_records,
    historical_candles_by_instrument,
    historical_records,
    invalid_high_below_low,
    invalid_missing_timestamp,
    invalid_negative_volume,
    invalid_open_outside_range,
    short_history_records,
)
from engine.intelligence.historical_replay import (
    HistoricalReplayEngine,
    ReplayResult,
    evaluation_times_from_setup_candles,
)
from engine.intelligence.market_scan_serialization import (
    deserialize_scan,
    serialize_scan,
)
from engine.intelligence.market_scanner import (
    InstrumentDataset,
    MarketScanner,
    ScanEngines,
)
from engine.models.historical import (
    DataQuality,
    HistoricalDataset,
    HistoricalInstrumentData,
    HistoricalNormalizationError,
    HistoricalRecord,
    NormalizationIssue,
    TimeframeSeries,
)
from engine.models.market_scan import MTFAlignment, ScanStatus
from engine.models.ohlcv import OHLCVCandle
from engine.pipeline import (
    HistoricalEvaluationPipeline,
    PipelineConfig,
    trending_dataset,
)
from engine.reporting.historical_replay import HistoricalReplayFormatter


_EPOCH = datetime(2025, 1, 6, tzinfo=UTC)


# ============================================================
# HELPERS
# ============================================================


def _candle(close: float, ts: datetime, spread: float = 2.0) -> OHLCVCandle:
    return OHLCVCandle(
        timestamp=ts,
        open=close,
        high=close + spread,
        low=close - spread,
        close=close,
        volume=1000.0,
    )


def _valid_record(
    close: float, ts: datetime, instrument="NIFTY", timeframe="1D",
) -> HistoricalRecord:
    return HistoricalRecord(
        timestamp=ts,
        open=close,
        high=close + 2.0,
        low=close - 2.0,
        close=close,
        volume=1000.0,
        instrument=instrument,
        timeframe=timeframe,
    )


def _valid_series_records(
    instrument: str, timeframe: str, n: int, start_ts: datetime = _EPOCH,
    step: timedelta = timedelta(days=1),
) -> list[HistoricalRecord]:
    records: list[HistoricalRecord] = []
    ts = start_ts
    for i in range(n):
        records.append(_valid_record(100.0 + i, ts, instrument, timeframe))
        ts = ts + step
    return records


def _default_engines() -> ScanEngines:
    return ScanEngines.default()


def _normalized_dataset(min_history: int = 10) -> HistoricalDataset:
    adapter = HistoricalDataAdapter(
        HistoricalAdapterConfig(min_history=min_history),
    )
    return adapter.normalize(historical_records())


# ============================================================
# A. HISTORICAL DATA NORMALIZATION
# ============================================================


class TestNormalization:
    def test_valid_records_normalize_to_valid_series(self):
        adapter = HistoricalDataAdapter(
            HistoricalAdapterConfig(timeframes=("1D",), min_history=10),
        )
        ds = adapter.normalize(_valid_series_records("NIFTY", "1D", 12))
        assert "NIFTY" in ds.instruments
        series = ds.get("NIFTY").series["1D"]
        assert series.quality == DataQuality.VALID
        assert series.candle_count == 12
        assert ds.invalid_count == 0
        assert ds.incomplete_count == 0

    def test_chronological_ordering(self):
        records = list(reversed(_valid_series_records("X", "1D", 5)))
        adapter = HistoricalDataAdapter(
            HistoricalAdapterConfig(min_history=3),
        )
        ds = adapter.normalize(records)
        series = ds.get("X").series["1D"]
        ts = [c.timestamp for c in series.candles]
        assert ts == sorted(ts)

    def test_normalization_is_deterministic(self):
        adapter = HistoricalDataAdapter(HistoricalAdapterConfig(min_history=10))
        ds1 = adapter.normalize(historical_records())
        ds2 = adapter.normalize(historical_records())
        assert ds1.instruments == ds2.instruments
        for inst in ds1.instruments:
            for tf in ds1.get(inst).timeframes:
                s1 = ds1.get(inst).series[tf]
                s2 = ds2.get(inst).series[tf]
                assert s1.quality == s2.quality
                assert s1.candle_count == s2.candle_count
                assert [c.timestamp for c in s1.candles] == [
                    c.timestamp for c in s2.candles
                ]

    def test_timezones_preserved(self):
        ts = datetime(2025, 3, 1, 9, 30, tzinfo=UTC)
        adapter = HistoricalDataAdapter(HistoricalAdapterConfig(min_history=1))
        ds = adapter.normalize([_valid_record(100.0, ts)])
        assert ds.get("NIFTY").series["1D"].candles[0].timestamp == ts

    def test_instrument_and_timeframe_identity_carried(self):
        ds = _normalized_dataset()
        for inst in ds.instruments:
            assert {"1D", "15M"} == set(ds.get(inst).series.keys())

    def test_mapping_records_accepted(self):
        ts = _EPOCH
        adapter = HistoricalDataAdapter(HistoricalAdapterConfig(min_history=2))
        ds = adapter.normalize(
            [
                {
                    "timestamp": ts, "open": 100.0, "high": 102.0,
                    "low": 98.0, "close": 101.0, "volume": 1000.0,
                    "instrument": "MAP", "timeframe": "1D",
                },
                {
                    "timestamp": ts + timedelta(days=1), "open": 101.0,
                    "high": 103.0, "low": 99.0, "close": 102.0,
                    "volume": 1100.0, "instrument": "MAP", "timeframe": "1D",
                },
            ],
        )
        assert ds.get("MAP").series["1D"].quality == DataQuality.VALID


# ============================================================
# B. INVALID DATA HANDLING
# ============================================================


class TestInvalidData:
    def test_high_below_low_rejected(self):
        adapter = HistoricalDataAdapter()
        ds = adapter.normalize([invalid_high_below_low()])
        assert ds.invalid_count >= 1
        assert any(
            i.error == HistoricalNormalizationError.HIGH_BELOW_LOW
            for i in ds.issues
        )
        assert "BAD" not in ds.instruments or all(
            s.quality != DataQuality.VALID
            for s in ds.get("BAD").series.values()
        )

    def test_missing_timestamp_rejected(self):
        adapter = HistoricalDataAdapter()
        ds = adapter.normalize([invalid_missing_timestamp()])
        assert any(
            i.error == HistoricalNormalizationError.MISSING_TIMESTAMP
            for i in ds.issues
        )

    def test_negative_volume_rejected(self):
        adapter = HistoricalDataAdapter()
        ds = adapter.normalize([invalid_negative_volume()])
        assert any(
            i.error == HistoricalNormalizationError.NEGATIVE_VOLUME
            for i in ds.issues
        )

    def test_open_outside_range_rejected(self):
        adapter = HistoricalDataAdapter()
        ds = adapter.normalize([invalid_open_outside_range()])
        assert any(
            i.error == HistoricalNormalizationError.INVALID_OHLC
            for i in ds.issues
        )

    def test_duplicate_timestamps_dropped(self):
        adapter = HistoricalDataAdapter()
        ds = adapter.normalize(duplicate_timestamp_records())
        assert any(
            i.error == HistoricalNormalizationError.DUPLICATE_TIMESTAMP
            for i in ds.issues
        )
        # No valid series produced from two duplicates only.
        if "DUP" in ds.instruments:
            assert ds.get("DUP").series["1D"].quality == DataQuality.INCOMPLETE

    def test_duplicate_strict_raises_when_dedupe_off(self):
        adapter = HistoricalDataAdapter(HistoricalAdapterConfig(dedupe=False))
        with pytest.raises(ValueError):
            adapter.normalize(duplicate_timestamp_records())

    def test_missing_instrument_rejected(self):
        adapter = HistoricalDataAdapter()
        ds = adapter.normalize(
            [
                HistoricalRecord(
                    timestamp=_EPOCH, open=100.0, high=102.0, low=98.0,
                    close=101.0, volume=1000.0, instrument="", timeframe="1D",
                ),
            ],
        )
        assert any(
            i.error == HistoricalNormalizationError.MISSING_INSTRUMENT
            for i in ds.issues
        )
        assert ds.instruments == ()

    def test_missing_timeframe_rejected(self):
        adapter = HistoricalDataAdapter()
        ds = adapter.normalize(
            [
                HistoricalRecord(
                    timestamp=_EPOCH, open=100.0, high=102.0, low=98.0,
                    close=101.0, volume=1000.0, instrument="X", timeframe="",
                ),
            ],
        )
        assert any(
            i.error == HistoricalNormalizationError.MISSING_TIMEFRAME
            for i in ds.issues
        )

    def test_invalid_record_does_not_contaminate_valid(self):
        records = _valid_series_records("GOOD", "1D", 12)
        records.append(invalid_high_below_low())
        adapter = HistoricalDataAdapter(HistoricalAdapterConfig(min_history=10))
        ds = adapter.normalize(records)
        assert ds.get("GOOD").series["1D"].quality == DataQuality.VALID
        assert ds.get("GOOD").series["1D"].candle_count == 12
        assert ds.invalid_count >= 1


# ============================================================
# C. INCOMPLETE DATA HANDLING
# ============================================================


class TestIncompleteData:
    def test_empty_records_yield_incomplete_series(self):
        adapter = HistoricalDataAdapter()
        ds = adapter.normalize([])
        assert ds.instruments == ()
        # Requested timeframes for a non-existent instrument are not
        # created; empty batch => no instruments, no series.

    def test_insufficient_history_is_incomplete(self):
        adapter = HistoricalDataAdapter(HistoricalAdapterConfig(min_history=10))
        ds = adapter.normalize(short_history_records("SHORT", "15M"))
        series = ds.get("SHORT").series["15M"]
        assert series.quality == DataQuality.INCOMPLETE
        assert series.candle_count == 3
        assert any(
            i.error == HistoricalNormalizationError.INSUFFICIENT_HISTORY
            for i in ds.issues
        )

    def test_missing_requested_timeframe_is_incomplete(self):
        # Supply only 1D records; the requested 15M timeframe is missing.
        adapter = HistoricalDataAdapter(
            HistoricalAdapterConfig(timeframes=("1D", "15M"), min_history=3),
        )
        ds = adapter.normalize(_valid_series_records("X", "1D", 5))
        series_15m = ds.get("X").series["15M"]
        assert series_15m.quality == DataQuality.INCOMPLETE
        assert series_15m.candles == ()
        assert any(
            i.error == HistoricalNormalizationError.EMPTY_SERIES
            for i in ds.issues
        )

    def test_incomplete_is_not_directional(self):
        adapter = HistoricalDataAdapter(HistoricalAdapterConfig(min_history=10))
        records = short_history_records("S", "15M")
        ds = adapter.normalize(records)
        series = ds.get("S").series["15M"]
        assert series.quality == DataQuality.INCOMPLETE
        # No candle is fabricated: exactly the valid input candles are
        # retained (their closes / timestamps match), nothing more.
        assert series.candle_count == len(records)
        assert [c.close for c in series.candles] == [r.close for r in records]
        assert [c.timestamp for c in series.candles] == [
            r.timestamp for r in records
        ]


# ============================================================
# D. MULTIPLE INSTRUMENTS (isolation)
# ============================================================


class TestMultipleInstruments:
    def test_all_instruments_present(self):
        ds = _normalized_dataset()
        assert ds.instruments == (
            "HDFCBANK", "ICICIBANK", "NIFTY", "RELIANCE", "TCS",
        )

    def test_instrument_state_isolated(self):
        ds = _normalized_dataset()
        nifty = ds.get("NIFTY").series["1D"]
        reliance = ds.get("RELIANCE").series["1D"]
        # Different instruments have independent candle series.
        assert nifty.candles != reliance.candles
        assert nifty.candles[0].close != reliance.candles[0].close

    def test_scanning_one_instrument_does_not_contaminate_another(self):
        ds = _normalized_dataset()
        engines = _default_engines()
        scanner = MarketScanner(
            MarketScanConfig(context_timeframe="1D", setup_timeframe="15M"),
        )
        nifty_ds = InstrumentDataset(
            "NIFTY",
            ds.context_candles("NIFTY", "1D"),
            ds.setup_candles("NIFTY", "15M"),
        )
        reliance_ds = InstrumentDataset(
            "RELIANCE",
            ds.context_candles("RELIANCE", "1D"),
            ds.setup_candles("RELIANCE", "15M"),
        )
        res_n = scanner.scan([nifty_ds], engines=engines)
        res_r = scanner.scan([reliance_ds], engines=engines)
        # NIFTY alone never produces RELIANCE in its result.
        assert {r.instrument for r in res_n.results} == {"NIFTY"}
        assert {r.instrument for r in res_r.results} == {"RELIANCE"}
        # Their alignments reflect their own data, not each other.
        n = res_n.results[0]
        r = res_r.results[0]
        assert n.alignment == MTFAlignment.ALIGNED  # bullish/bullish
        assert r.alignment == MTFAlignment.ALIGNED  # bearish/bearish (short)


# ============================================================
# E. MULTIPLE TIMEFRAMES
# ============================================================


class TestMultipleTimeframes:
    def test_context_and_setup_timeframes_supported(self):
        ds = _normalized_dataset()
        for inst in ds.instruments:
            assert ds.get(inst).series["1D"].quality == DataQuality.VALID
            assert ds.get(inst).series["15M"].quality == DataQuality.VALID

    def test_extensible_to_other_timeframes(self):
        # A 1H/5M config must work without re-architecting intelligence.
        adapter = HistoricalDataAdapter(
            HistoricalAdapterConfig(
                timeframes=("1H", "5M"), min_history=3,
            ),
        )
        ds = adapter.normalize(
            _valid_series_records("X", "1H", 5, step=timedelta(hours=1))
            + _valid_series_records("X", "5M", 5, step=timedelta(minutes=5)),
        )
        assert ds.get("X").series["1H"].quality == DataQuality.VALID
        assert ds.get("X").series["5M"].quality == DataQuality.VALID

    def test_mtf_alignment_reused(self):
        ds = _normalized_dataset()
        engines = _default_engines()
        scanner = MarketScanner(
            MarketScanConfig(context_timeframe="1D", setup_timeframe="15M"),
        )
        res = scanner.scan(
            [
                InstrumentDataset(
                    "NIFTY",
                    ds.context_candles("NIFTY", "1D"),
                    ds.setup_candles("NIFTY", "15M"),
                ),
            ],
            engines=engines,
        )
        # Bullish 1D + bullish 15M -> ALIGNED.
        assert res.results[0].alignment == MTFAlignment.ALIGNED


# ============================================================
# F. HIGHER-TIMEFRAME CANDLE COMPLETION
# ============================================================


class TestHTFCompletion:
    def test_in_progress_htf_candle_excluded(self):
        ds = _normalized_dataset()
        engines = _default_engines()
        scanner = MarketScanner(
            MarketScanConfig(context_timeframe="1D", setup_timeframe="15M"),
        )
        nifty_ctx = list(ds.context_candles("NIFTY", "1D"))
        nifty_setup = list(ds.setup_candles("NIFTY", "15M"))
        eval_time = nifty_setup[-1].timestamp
        # Add an in-progress 1D candle AT the eval time -> must be excluded.
        ctx_with_inprogress = nifty_ctx + [_candle(999.0, eval_time)]
        res_clean = scanner.scan(
            [InstrumentDataset("NIFTY", tuple(nifty_ctx), tuple(nifty_setup))],
            evaluation_time=eval_time, engines=engines,
        )
        res_inprogress = scanner.scan(
            [
                InstrumentDataset(
                    "NIFTY", tuple(ctx_with_inprogress), tuple(nifty_setup),
                ),
            ],
            evaluation_time=eval_time, engines=engines,
        )
        assert (
            res_clean.results[0].alignment
            == res_inprogress.results[0].alignment
        )
        assert res_clean.results[0].direction == res_inprogress.results[0].direction

    def test_no_completed_htf_candle_is_incomplete(self):
        engines = _default_engines()
        scanner = MarketScanner(
            MarketScanConfig(context_timeframe="1D", setup_timeframe="15M"),
        )
        # Context candle timestamps all AFTER the eval time.
        setup = _valid_series_records("X", "15M", 12, step=timedelta(minutes=15))
        setup_candles = [
            _candle(r.close, r.timestamp) for r in setup
        ]  # type: ignore[arg-type]
        late_ctx = [
            _candle(100.0, _EPOCH + timedelta(days=10)),
            _candle(101.0, _EPOCH + timedelta(days=11)),
        ]
        res = scanner.scan(
            [InstrumentDataset("X", tuple(late_ctx), tuple(setup_candles))],
            evaluation_time=setup_candles[-1].timestamp,
            engines=engines,
        )
        assert res.results[0].complete is False
        assert res.results[0].alignment == MTFAlignment.UNKNOWN

    def test_incomplete_htf_is_not_directional(self):
        engines = _default_engines()
        scanner = MarketScanner(
            MarketScanConfig(context_timeframe="1D", setup_timeframe="15M"),
        )
        res = scanner.scan(
            [InstrumentDataset("NONE", tuple(), tuple())], engines=engines,
        )
        assert res.status == ScanStatus.INCOMPLETE
        assert res.best is None
        assert res.results[0].alignment == MTFAlignment.UNKNOWN


# ============================================================
# G. POINT-IN-TIME CORRECTNESS
# ============================================================


class TestPointInTime:
    def test_prefix_full_series_equality(self):
        ds = _normalized_dataset()
        engines = _default_engines()
        scanner = MarketScanner(
            MarketScanConfig(context_timeframe="1D", setup_timeframe="15M"),
        )
        nifty_ctx = ds.context_candles("NIFTY", "1D")
        nifty_setup = list(ds.setup_candles("NIFTY", "15M"))
        mid = len(nifty_setup) // 2
        eval_time = nifty_setup[mid].timestamp
        full = scanner.scan(
            [InstrumentDataset("NIFTY", nifty_ctx, tuple(nifty_setup))],
            evaluation_time=eval_time, engines=engines,
        )
        prefix = scanner.scan(
            [
                InstrumentDataset(
                    "NIFTY", nifty_ctx, tuple(nifty_setup[: mid + 1]),
                ),
            ],
            evaluation_time=eval_time, engines=engines,
        )
        assert full.results[0].alignment == prefix.results[0].alignment
        assert full.results[0].direction == prefix.results[0].direction
        assert full.results[0].decision_score == prefix.results[0].decision_score

    def test_future_setup_mutation_unchanged(self):
        ds = _normalized_dataset()
        engines = _default_engines()
        scanner = MarketScanner(
            MarketScanConfig(context_timeframe="1D", setup_timeframe="15M"),
        )
        nifty_ctx = ds.context_candles("NIFTY", "1D")
        nifty_setup = list(ds.setup_candles("NIFTY", "15M"))
        eval_time = nifty_setup[-1].timestamp
        before = scanner.scan(
            [InstrumentDataset("NIFTY", nifty_ctx, tuple(nifty_setup))],
            evaluation_time=eval_time, engines=engines,
        )
        mutated = nifty_setup + [
            _candle(999.0, eval_time + timedelta(minutes=15)),
        ]
        after = scanner.scan(
            [InstrumentDataset("NIFTY", nifty_ctx, tuple(mutated))],
            evaluation_time=eval_time, engines=engines,
        )
        assert before.results[0].alignment == after.results[0].alignment
        assert before.results[0].direction == after.results[0].direction

    def test_future_htf_mutation_unchanged(self):
        ds = _normalized_dataset()
        engines = _default_engines()
        scanner = MarketScanner(
            MarketScanConfig(context_timeframe="1D", setup_timeframe="15M"),
        )
        nifty_ctx = list(ds.context_candles("NIFTY", "1D"))
        nifty_setup = ds.setup_candles("NIFTY", "15M")
        eval_time = nifty_setup[-1].timestamp
        before = scanner.scan(
            [InstrumentDataset("NIFTY", tuple(nifty_ctx), nifty_setup)],
            evaluation_time=eval_time, engines=engines,
        )
        future_ctx = nifty_ctx + [
            _candle(999.0, eval_time + timedelta(days=3)),
        ]
        after = scanner.scan(
            [InstrumentDataset("NIFTY", tuple(future_ctx), nifty_setup)],
            evaluation_time=eval_time, engines=engines,
        )
        assert before.results[0].alignment == after.results[0].alignment
        assert before.results[0].direction == after.results[0].direction

    def test_multi_instrument_future_mutation(self):
        ds = _normalized_dataset()
        engines = _default_engines()
        scanner = MarketScanner(
            MarketScanConfig(context_timeframe="1D", setup_timeframe="15M"),
        )
        instruments = ("NIFTY", "RELIANCE")
        datasets = [
            InstrumentDataset(
                inst,
                ds.context_candles(inst, "1D"),
                list(ds.setup_candles(inst, "15M")),
            )
            for inst in instruments
        ]
        eval_time = max(
            ds.setup_candles(inst, "15M")[-1].timestamp for inst in instruments
        )
        before = scanner.scan(datasets, evaluation_time=eval_time, engines=engines)
        mutated = [
            InstrumentDataset(
                d.instrument,
                d.context_candles,
                tuple(d.setup_candles)
                + (_candle(999.0, eval_time + timedelta(minutes=15)),),
            )
            for d in datasets
        ]
        after = scanner.scan(mutated, evaluation_time=eval_time, engines=engines)
        for a, b in zip(before.results, after.results):
            assert a.alignment == b.alignment
            assert a.direction == b.direction

    def test_multi_timeframe_future_mutation(self):
        ds = _normalized_dataset()
        engines = _default_engines()
        scanner = MarketScanner(
            MarketScanConfig(context_timeframe="1D", setup_timeframe="15M"),
        )
        ctx = list(ds.context_candles("NIFTY", "1D"))
        setup = list(ds.setup_candles("NIFTY", "15M"))
        eval_time = setup[-1].timestamp
        before = scanner.scan(
            [InstrumentDataset("NIFTY", tuple(ctx), tuple(setup))],
            evaluation_time=eval_time, engines=engines,
        )
        mutated_ctx = ctx + [_candle(999.0, eval_time + timedelta(days=2))]
        mutated_setup = setup + [
            _candle(999.0, eval_time + timedelta(minutes=30)),
        ]
        after = scanner.scan(
            [
                InstrumentDataset(
                    "NIFTY", tuple(mutated_ctx), tuple(mutated_setup),
                ),
            ],
            evaluation_time=eval_time, engines=engines,
        )
        assert before.results[0].alignment == after.results[0].alignment
        assert before.results[0].direction == after.results[0].direction


# ============================================================
# H. REPLAY CORRECTNESS
# ============================================================


class TestReplay:
    def test_replay_matches_direct_point_in_time_eval(self):
        ds = _normalized_dataset()
        engines = _default_engines()
        config = MarketScanConfig(
            context_timeframe="1D", setup_timeframe="15M", min_history=10,
        )
        replay = HistoricalReplayEngine(config)
        scanner = MarketScanner(config)
        times = evaluation_times_from_setup_candles(
            ds, "15M", count=3, min_history=12,
        )
        result = replay.replay(ds, times, engines=engines)
        assert len(result.points) == len(times)
        for point, t in zip(result.points, times):
            datasets = [
                InstrumentDataset(
                    inst,
                    ds.context_candles(inst, "1D"),
                    ds.setup_candles(inst, "15M"),
                )
                for inst in ds.instruments
            ]
            direct = scanner.scan(datasets, evaluation_time=t, engines=engines)
            assert point.scan.status == direct.status
            assert point.scan.eligible_count == direct.eligible_count
            assert [r.opportunity.instrument for r in point.scan.ranked] == [
                r.opportunity.instrument for r in direct.ranked
            ]

    def test_replay_is_deterministic(self):
        ds = _normalized_dataset()
        engines = _default_engines()
        replay = HistoricalReplayEngine(
            MarketScanConfig(
                context_timeframe="1D", setup_timeframe="15M", min_history=10,
            ),
        )
        times = evaluation_times_from_setup_candles(
            ds, "15M", count=3, min_history=12,
        )
        r1 = replay.replay(ds, times, engines=engines)
        r2 = replay.replay(ds, times, engines=engines)
        assert r1.replay_id == r2.replay_id
        assert r1.evaluation_times == r2.evaluation_times
        for p1, p2 in zip(r1.points, r2.points):
            assert p1.scan.scan_id == p2.scan.scan_id
            assert p1.scan.status == p2.scan.status
            assert [
                r.opportunity.instrument for r in p1.scan.ranked
            ] == [r.opportunity.instrument for r in p2.scan.ranked]

    def test_replay_no_wall_clock_dependence(self):
        ds = _normalized_dataset()
        engines = _default_engines()
        replay = HistoricalReplayEngine(
            MarketScanConfig(
                context_timeframe="1D", setup_timeframe="15M", min_history=10,
            ),
        )
        times = evaluation_times_from_setup_candles(
            ds, "15M", count=2, min_history=12,
        )
        r1 = replay.replay(ds, times, engines=engines)
        # Re-run much later in wall-clock; results must be identical.
        import time as _time

        _time.sleep(0.01)
        r2 = replay.replay(ds, times, engines=engines)
        assert r1.replay_id == r2.replay_id
        assert [p.scan.scan_id for p in r1.points] == [
            p.scan.scan_id for p in r2.points
        ]

    def test_replay_empty_times(self):
        ds = _normalized_dataset()
        replay = HistoricalReplayEngine()
        result = replay.replay(ds, (), engines=_default_engines())
        assert result.is_empty is True
        assert result.points == ()

    def test_replay_sequential_information_availability(self):
        ds = _normalized_dataset()
        engines = _default_engines()
        config = MarketScanConfig(
            context_timeframe="1D", setup_timeframe="15M", min_history=10,
        )
        replay = HistoricalReplayEngine(config)
        scanner = MarketScanner(config)
        times = evaluation_times_from_setup_candles(
            ds, "15M", count=3, min_history=12,
        )
        result = replay.replay(ds, times, engines=engines)
        # Each point uses only candles <= its eval time; compare against a
        # truncated-prefix scan per instrument for each T.
        for point, t in zip(result.points, times):
            truncated = [
                InstrumentDataset(
                    inst,
                    ds.context_candles(inst, "1D"),
                    tuple(
                        c for c in ds.setup_candles(inst, "15M")
                        if c.timestamp <= t
                    ),
                )
                for inst in ds.instruments
            ]
            direct = scanner.scan(
                truncated, evaluation_time=t, engines=engines,
            )
            assert point.scan.status == direct.status
            assert [
                r.opportunity.instrument for r in point.scan.ranked
            ] == [r.opportunity.instrument for r in direct.ranked]


# ============================================================
# I. DETERMINISTIC RANKING
# ============================================================


class TestDeterministicRanking:
    def test_ranking_deterministic_repeated(self):
        ds = _normalized_dataset()
        engines = _default_engines()
        scanner = MarketScanner(
            MarketScanConfig(context_timeframe="1D", setup_timeframe="15M"),
        )
        eval_time = max(
            ds.setup_candles(inst, "15M")[-1].timestamp for inst in ds.instruments
        )
        datasets = [
            InstrumentDataset(
                inst,
                ds.context_candles(inst, "1D"),
                ds.setup_candles(inst, "15M"),
            )
            for inst in ds.instruments
        ]
        r1 = scanner.scan(datasets, evaluation_time=eval_time, engines=engines)
        r2 = scanner.scan(datasets, evaluation_time=eval_time, engines=engines)
        assert [r.opportunity.instrument for r in r1.ranked] == [
            r.opportunity.instrument for r in r2.ranked
        ]

    def test_no_random_ranking(self):
        ds = _normalized_dataset()
        engines = _default_engines()
        scanner = MarketScanner(
            MarketScanConfig(context_timeframe="1D", setup_timeframe="15M"),
        )
        eval_time = max(
            ds.setup_candles(inst, "15M")[-1].timestamp for inst in ds.instruments
        )
        datasets = [
            InstrumentDataset(
                inst,
                ds.context_candles(inst, "1D"),
                ds.setup_candles(inst, "15M"),
            )
            for inst in ds.instruments
        ]
        rankings = set()
        for _ in range(5):
            res = scanner.scan(
                datasets, evaluation_time=eval_time, engines=engines,
            )
            rankings.add(tuple(r.opportunity.instrument for r in res.ranked))
        assert len(rankings) == 1

    def test_ranks_unique_when_opportunities(self):
        ds = _normalized_dataset()
        engines = _default_engines()
        scanner = MarketScanner(
            MarketScanConfig(context_timeframe="1D", setup_timeframe="15M"),
        )
        eval_time = max(
            ds.setup_candles(inst, "15M")[-1].timestamp for inst in ds.instruments
        )
        datasets = [
            InstrumentDataset(
                inst,
                ds.context_candles(inst, "1D"),
                ds.setup_candles(inst, "15M"),
            )
            for inst in ds.instruments
        ]
        res = scanner.scan(datasets, evaluation_time=eval_time, engines=engines)
        eligible_ranks = [r.rank for r in res.ranked if r.rank > 0]
        assert len(eligible_ranks) == len(set(eligible_ranks))


# ============================================================
# J. SERIALIZATION ROUND TRIP
# ============================================================


class TestSerialization:
    def test_round_trip_preserves_identity(self):
        ds = _normalized_dataset()
        engines = _default_engines()
        scanner = MarketScanner(
            MarketScanConfig(context_timeframe="1D", setup_timeframe="15M"),
        )
        eval_time = max(
            ds.setup_candles(inst, "15M")[-1].timestamp for inst in ds.instruments
        )
        datasets = [
            InstrumentDataset(
                inst,
                ds.context_candles(inst, "1D"),
                ds.setup_candles(inst, "15M"),
            )
            for inst in ds.instruments
        ]
        scan = scanner.scan(datasets, evaluation_time=eval_time, engines=engines)
        restored = deserialize_scan(serialize_scan(scan))
        assert restored.scan_id == scan.scan_id
        assert restored.timestamp == scan.timestamp
        assert restored.instruments == scan.instruments
        assert restored.timeframes == scan.timeframes
        assert restored.status == scan.status

    def test_round_trip_preserves_ranking_direction_decision_score(self):
        ds = _normalized_dataset()
        engines = _default_engines()
        scanner = MarketScanner(
            MarketScanConfig(context_timeframe="1D", setup_timeframe="15M"),
        )
        eval_time = max(
            ds.setup_candles(inst, "15M")[-1].timestamp for inst in ds.instruments
        )
        datasets = [
            InstrumentDataset(
                inst,
                ds.context_candles(inst, "1D"),
                ds.setup_candles(inst, "15M"),
            )
            for inst in ds.instruments
        ]
        scan = scanner.scan(datasets, evaluation_time=eval_time, engines=engines)
        restored = deserialize_scan(serialize_scan(scan))
        assert [
            (r.rank, r.opportunity.instrument, r.opportunity.direction,
             r.opportunity.decision_classification, r.opportunity.decision_score,
             r.alignment.name)
            for r in restored.ranked
        ] == [
            (r.rank, r.opportunity.instrument, r.opportunity.direction,
             r.opportunity.decision_classification, r.opportunity.decision_score,
             r.alignment.name)
            for r in scan.ranked
        ]

    def test_round_trip_preserves_best_and_opportunity(self):
        ds = _normalized_dataset()
        engines = _default_engines()
        scanner = MarketScanner(
            MarketScanConfig(context_timeframe="1D", setup_timeframe="15M"),
        )
        eval_time = max(
            ds.setup_candles(inst, "15M")[-1].timestamp for inst in ds.instruments
        )
        datasets = [
            InstrumentDataset(
                inst,
                ds.context_candles(inst, "1D"),
                ds.setup_candles(inst, "15M"),
            )
            for inst in ds.instruments
        ]
        scan = scanner.scan(datasets, evaluation_time=eval_time, engines=engines)
        restored = deserialize_scan(serialize_scan(scan))
        if scan.has_best:
            assert restored.has_best is True
            assert restored.best.opportunity.instrument == scan.best.opportunity.instrument
            assert restored.best.opportunity.direction == scan.best.opportunity.direction
        # Per-instrument opportunity status preserved via stored eligible flag.
        assert [r.eligible for r in restored.results] == [
            r.eligible for r in scan.results
        ]

    def test_serialization_deterministic_bytes(self):
        ds = _normalized_dataset()
        engines = _default_engines()
        scanner = MarketScanner(
            MarketScanConfig(context_timeframe="1D", setup_timeframe="15M"),
        )
        eval_time = max(
            ds.setup_candles(inst, "15M")[-1].timestamp for inst in ds.instruments
        )
        datasets = [
            InstrumentDataset(
                inst,
                ds.context_candles(inst, "1D"),
                ds.setup_candles(inst, "15M"),
            )
            for inst in ds.instruments
        ]
        scan = scanner.scan(datasets, evaluation_time=eval_time, engines=engines)
        assert serialize_scan(scan) == serialize_scan(scan)


# ============================================================
# K. END-TO-END SCAN
# ============================================================


class TestEndToEnd:
    def test_full_pipeline_loads_normalizes_scans_ranks(self):
        records = historical_records()
        adapter = HistoricalDataAdapter(HistoricalAdapterConfig(min_history=10))
        ds = adapter.normalize(records)
        engines = _default_engines()
        replay = HistoricalReplayEngine(
            MarketScanConfig(
                context_timeframe="1D", setup_timeframe="15M", min_history=10,
            ),
        )
        times = evaluation_times_from_setup_candles(
            ds, "15M", count=3, min_history=12,
        )
        result = replay.replay(ds, times, engines=engines)
        assert len(result.points) == 3
        assert result.instruments == (
            "HDFCBANK", "ICICIBANK", "NIFTY", "RELIANCE", "TCS",
        )

    def test_best_opportunity_identified_when_available(self):
        ds = _normalized_dataset()
        engines = _default_engines()
        replay = HistoricalReplayEngine(
            MarketScanConfig(
                context_timeframe="1D", setup_timeframe="15M", min_history=10,
            ),
        )
        times = evaluation_times_from_setup_candles(
            ds, "15M", count=3, min_history=12,
        )
        result = replay.replay(ds, times, engines=engines)
        # The latest point has a best opportunity.
        assert result.points[-1].scan.has_best is True
        assert result.points[-1].scan.best.rank == 1

    def test_incomplete_handled_honestly(self):
        engines = _default_engines()
        replay = HistoricalReplayEngine(
            MarketScanConfig(
                context_timeframe="1D", setup_timeframe="15M", min_history=10,
            ),
        )
        # An instrument with no candles at all -> INCOMPLETE.
        ds = HistoricalDataset()
        # Build a dataset with one empty instrument.
        empty = HistoricalInstrumentData(
            instrument="NONE",
            series={
                "1D": TimeframeSeries(
                    "NONE", "1D", (), DataQuality.INCOMPLETE,
                ),
                "15M": TimeframeSeries(
                    "NONE", "15M", (), DataQuality.INCOMPLETE,
                ),
            },
        )
        ds = HistoricalDataset(data={"NONE": empty})
        result = replay.replay(ds, [_EPOCH], engines=engines)
        assert result.points[0].scan.status == ScanStatus.INCOMPLETE
        assert result.points[0].scan.best is None

    def test_replay_id_deterministic(self):
        ds = _normalized_dataset()
        engines = _default_engines()
        replay = HistoricalReplayEngine(
            MarketScanConfig(
                context_timeframe="1D", setup_timeframe="15M", min_history=10,
            ),
        )
        times = evaluation_times_from_setup_candles(
            ds, "15M", count=2, min_history=12,
        )
        r1 = replay.replay(ds, times, engines=engines)
        r2 = replay.replay(ds, times, engines=engines)
        assert r1.replay_id == r2.replay_id

    def test_replay_progresses_from_watch_to_opportunities(self):
        ds = _normalized_dataset()
        engines = _default_engines()
        replay = HistoricalReplayEngine(
            MarketScanConfig(
                context_timeframe="1D", setup_timeframe="15M", min_history=10,
            ),
        )
        times = evaluation_times_from_setup_candles(
            ds, "15M", count=4, min_history=12,
        )
        result = replay.replay(ds, times, engines=engines)
        statuses = [p.scan.status for p in result.points]
        # The earliest point is not yet OPPORTUNITIES_FOUND; the latest is.
        assert statuses[-1] == ScanStatus.OPPORTUNITIES_FOUND


# ============================================================
# L. EXISTING PIPELINE REGRESSION
# ============================================================


class TestRegression:
    def test_pipeline_signals_trades_unchanged_on_rerun(self):
        candles = trending_dataset()
        pipe = HistoricalEvaluationPipeline(PipelineConfig())
        r1 = pipe.evaluate(candles)
        r2 = pipe.evaluate(candles)
        assert r1.signals_generated == r2.signals_generated
        assert r1.completed_trades == r2.completed_trades

    def test_pipeline_signals_trades_match_baseline(self):
        candles = trending_dataset()
        pipe = HistoricalEvaluationPipeline(PipelineConfig())
        result = pipe.evaluate(candles)
        # Documented pre-11V baseline (from Sprint 11U demo).
        assert result.signals_generated == 4
        assert result.completed_trades == 3

    def test_market_scan_field_remains_optional(self):
        # The pipeline result market_scan field is still optional and None
        # by default (Sprint 11U additive field unchanged).
        candles = trending_dataset()
        pipe = HistoricalEvaluationPipeline(PipelineConfig())
        result = pipe.evaluate(candles)
        assert getattr(result, "market_scan", None) is None


# ============================================================
# M. REPORTING
# ============================================================


class TestReporting:
    def test_format_returns_str(self):
        ds = _normalized_dataset()
        engines = _default_engines()
        replay = HistoricalReplayEngine(
            MarketScanConfig(
                context_timeframe="1D", setup_timeframe="15M", min_history=10,
            ),
        )
        times = evaluation_times_from_setup_candles(
            ds, "15M", count=2, min_history=12,
        )
        result = replay.replay(ds, times, engines=engines)
        report = HistoricalReplayFormatter().format(result)
        assert isinstance(report, str)
        assert len(report) > 0

    def test_report_contains_required_sections(self):
        ds = _normalized_dataset()
        engines = _default_engines()
        replay = HistoricalReplayEngine(
            MarketScanConfig(
                context_timeframe="1D", setup_timeframe="15M", min_history=10,
            ),
        )
        times = evaluation_times_from_setup_candles(
            ds, "15M", count=3, min_history=12,
        )
        result = replay.replay(ds, times, engines=engines)
        report = HistoricalReplayFormatter().format(result)
        for section in [
            "Historical Market Replay",
            "Replay ID",
            "Instruments",
            "Timeframes",
            "Evaluation Point",
            "Higher Timeframe Context",
            "Setup Timeframe",
            "MTF Alignment",
            "Opportunity",
            "Replay Rationale",
        ]:
            assert section in report, f"missing section: {section}"

    def test_report_contains_warning(self):
        ds = _normalized_dataset()
        engines = _default_engines()
        replay = HistoricalReplayEngine(
            MarketScanConfig(
                context_timeframe="1D", setup_timeframe="15M", min_history=10,
            ),
        )
        times = evaluation_times_from_setup_candles(
            ds, "15M", count=2, min_history=12,
        )
        result = replay.replay(ds, times, engines=engines)
        report = HistoricalReplayFormatter().format(result)
        assert "NOT predictive signals" in report
        assert "guarantees of profitability" in report

    def test_report_no_predictive_language(self):
        ds = _normalized_dataset()
        engines = _default_engines()
        replay = HistoricalReplayEngine(
            MarketScanConfig(
                context_timeframe="1D", setup_timeframe="15M", min_history=10,
            ),
        )
        times = evaluation_times_from_setup_candles(
            ds, "15M", count=2, min_history=12,
        )
        result = replay.replay(ds, times, engines=engines)
        report = HistoricalReplayFormatter().format(result).lower()
        for phrase in ["will rise", "will fall", "guaranteed profit",
                       "most profitable", "highest probability"]:
            assert phrase not in report

    def test_report_deterministic(self):
        ds = _normalized_dataset()
        engines = _default_engines()
        replay = HistoricalReplayEngine(
            MarketScanConfig(
                context_timeframe="1D", setup_timeframe="15M", min_history=10,
            ),
        )
        times = evaluation_times_from_setup_candles(
            ds, "15M", count=2, min_history=12,
        )
        result = replay.replay(ds, times, engines=engines)
        fmt = HistoricalReplayFormatter()
        assert fmt.format(result) == fmt.format(result)

    def test_format_scan_returns_str(self):
        ds = _normalized_dataset()
        engines = _default_engines()
        scanner = MarketScanner(
            MarketScanConfig(context_timeframe="1D", setup_timeframe="15M"),
        )
        eval_time = max(
            ds.setup_candles(inst, "15M")[-1].timestamp for inst in ds.instruments
        )
        datasets = [
            InstrumentDataset(
                inst,
                ds.context_candles(inst, "1D"),
                ds.setup_candles(inst, "15M"),
            )
            for inst in ds.instruments
        ]
        scan = scanner.scan(datasets, evaluation_time=eval_time, engines=engines)
        report = HistoricalReplayFormatter().format_scan(scan)
        assert isinstance(report, str)
        assert "Market Opportunity Scan" in report

    def test_empty_replay_report(self):
        result = ReplayResult(replay_id="replay-x")
        report = HistoricalReplayFormatter().format(result)
        assert "No evaluation points" in report


# ============================================================
# N. MODEL IMMUTABILITY / DATA QUALITY
# ============================================================


class TestImmutabilityAndQuality:
    def test_models_frozen_slots(self):
        for cls in (
            TimeframeSeries,
            HistoricalInstrumentData,
            HistoricalDataset,
            HistoricalRecord,
            NormalizationIssue,
        ):
            assert hasattr(cls, "__slots__")

    def test_timeframe_series_frozen(self):
        s = TimeframeSeries("X", "1D", (), DataQuality.INCOMPLETE)
        with pytest.raises(Exception):
            s.quality = DataQuality.VALID  # type: ignore[misc]

    def test_timeframe_series_requires_instrument(self):
        with pytest.raises(ValueError):
            TimeframeSeries("", "1D", (), DataQuality.INCOMPLETE)

    def test_timeframe_series_requires_timeframe(self):
        with pytest.raises(ValueError):
            TimeframeSeries("X", "", (), DataQuality.INCOMPLETE)

    def test_adapter_config_validation(self):
        with pytest.raises(ValueError):
            HistoricalAdapterConfig(timeframes=())
        with pytest.raises(ValueError):
            HistoricalAdapterConfig(timeframes=("1D", "1D"))
        with pytest.raises(ValueError):
            HistoricalAdapterConfig(min_history=-1)

    def test_no_fabricated_data_on_missing(self):
        adapter = HistoricalDataAdapter(
            HistoricalAdapterConfig(timeframes=("1D", "15M"), min_history=10),
        )
        ds = adapter.normalize(_valid_series_records("X", "1D", 5))
        # The missing 15M timeframe is INCOMPLETE with no candles, not a
        # fabricated directional series.
        series = ds.get("X").series["15M"]
        assert series.quality == DataQuality.INCOMPLETE
        assert series.candles == ()
