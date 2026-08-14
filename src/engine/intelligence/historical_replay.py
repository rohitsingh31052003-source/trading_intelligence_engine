"""
Historical replay engine (Sprint 11V).

:class:`HistoricalReplayEngine` drives the EXISTING
:class:`~engine.intelligence.market_scanner.MarketScanner` over a
normalized :class:`~engine.models.historical.HistoricalDataset` at a
sequence of evaluation timestamps, simulating sequential information
availability. It is the end-to-end integration / validation layer of
Sprint 11V: it proves the existing intelligence pipeline (Sprints
11A-11U) can consume realistic historical OHLCV data exactly as if it
were seeing the market sequentially in real time, without using future
information.

DESIGN PRINCIPLE — reuse, do not re-invent:

The replay engine implements NO candle, structure, setup, candidate,
decision, opportunity, alignment or ranking logic. It REUSES the
existing :class:`MarketScanner` (which itself reuses the Sprint 11O-11T
engines + the Sprint 11U alignment engine). At each evaluation point
``T`` the replay builds the per-instrument
:class:`~engine.intelligence.market_scanner.InstrumentDataset` objects
from the historical dataset and delegates to
``MarketScanner.scan(datasets, evaluation_time=T, engines=...)``. The
scanner is already point-in-time safe (it uses only candles that had
CLOSED strictly before ``T`` for the higher timeframe and at-or-before
``T`` for the setup timeframe); the replay layer preserves that
guarantee by feeding the SAME candle tuples and the explicit
``evaluation_time``.

DESIGN PRINCIPLE — no future leakage (HARD REQUIREMENT):

For an evaluation time ``T``, the replay feeds each instrument's full
context + setup candle tuples to the scanner together with
``evaluation_time=T``. The scanner's
:func:`~engine.intelligence.market_scanner._latest_completed_before` and
:func:`~engine.intelligence.market_scanner._latest_completed_at_or_before`
truncation ensure only candles that had closed strictly before (context)
or at-or-before (setup) ``T`` influence the result. No candle with a
timestamp after ``T`` is read. This is verified by the prefix / full
equality test and the future-mutation test in the Sprint 11V test suite
and demo.

DESIGN PRINCIPLE — deterministic replay:

Running the same data + configuration + evaluation timestamps ALWAYS
produces identical results. The replay uses no wall-clock time and no
unordered iteration for ranking (the scanner's ranking is already
deterministic; the evaluation timestamps are caller-supplied and
processed in order).

DESIGN PRINCIPLE — no fabricated data:

An instrument whose required timeframe is INCOMPLETE (missing or
insufficient) is fed to the scanner with an empty candle tuple for that
timeframe; the scanner reports it INCOMPLETE / UNKNOWN — never a
directional conclusion fabricated from absent data. Missing data is
never turned into a price, trend, structure, setup, stop, target or
risk/reward figure.

DESIGN PRINCIPLE — no profitability / backtest metrics:

Sprint 11V validates correctness and reproducibility. The replay
produces descriptive market scans ONLY — no win rate, profit factor,
Sharpe, PnL, expectancy, portfolio returns or trade outcome evaluation.
Those belong to a later sprint.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Sequence

from engine.config.market_scan_config import MarketScanConfig
from engine.intelligence.market_scanner import (
    InstrumentDataset,
    MarketScanner,
    ScanEngines,
)
from engine.models.historical import HistoricalDataset
from engine.models.market_scan import MarketScanResult
from engine.models.ohlcv import OHLCVCandle


@dataclass(frozen=True, slots=True)
class ReplayEvaluationPoint:
    """
    One point in the historical replay.

    Attributes:

    evaluation_time
        The timestamp this point was evaluated at (the close of the
        latest completed setup-timeframe candle considered available at
        this point).

    scan
        The :class:`MarketScanResult` produced by the scanner at this
        point (DESCRIPTIVE only; not a prediction).
    """

    evaluation_time: datetime
    scan: MarketScanResult


@dataclass(frozen=True, slots=True)
class ReplayResult:
    """
    The deterministic result of a complete historical replay.

    Attributes:

    replay_id
        Deterministic replay identifier (``"replay-"`` + sha256[:16] of
        the canonical replay identity — instruments, timeframes,
        evaluation timestamps, scan config label/metadata).

    instruments
        Sorted tuple of instrument names replayed.

    timeframes
        The ``(context_timeframe, setup_timeframe)`` pair used.

    evaluation_times
        The ordered tuple of evaluation timestamps replayed.

    points
        Tuple of :class:`ReplayEvaluationPoint`, one per evaluation
        timestamp, in order.

    rationale
        Human-readable, descriptive summary of the replay.
    """

    replay_id: str
    instruments: tuple[str, ...] = field(default_factory=tuple)
    timeframes: tuple[str, str] = ("", "")
    evaluation_times: tuple[datetime, ...] = field(default_factory=tuple)
    points: tuple[ReplayEvaluationPoint, ...] = field(
        default_factory=tuple,
    )
    rationale: str = ""

    @property
    def is_empty(self) -> bool:
        """Whether the replay evaluated no points."""

        return len(self.points) == 0


class HistoricalReplayEngine:
    """
    Drive the existing market scanner over a historical dataset at a
    sequence of evaluation timestamps.

    Public API:

        replay(dataset, evaluation_times, engines=None) -> ReplayResult

    The replay engine is stateless across calls: identical inputs always
    produce identical outputs.
    """

    def __init__(self, config: MarketScanConfig | None = None) -> None:
        self.config = config or MarketScanConfig()
        self._scanner = MarketScanner(self.config)

    # ============================================================
    # PUBLIC API
    # ============================================================

    def replay(
        self,
        dataset: HistoricalDataset,
        evaluation_times: Sequence[datetime],
        engines: ScanEngines | None = None,
    ) -> ReplayResult:
        """
        Replay the historical dataset at the given evaluation timestamps.

        ``evaluation_times`` are processed IN ORDER. At each ``T`` the
        per-instrument :class:`InstrumentDataset` objects are built from
        the dataset and the existing :class:`MarketScanner` is invoked
        with ``evaluation_time=T``. The result is deterministic and
        descriptive; it makes no profitability / probability /
        directional-prediction claim.
        """

        times = tuple(evaluation_times)
        bundle = engines or ScanEngines.default()
        instruments = dataset.instruments
        context_tf = self.config.context_timeframe
        setup_tf = self.config.setup_timeframe

        points: list[ReplayEvaluationPoint] = []
        for t in times:
            datasets = [
                self._build_instrument_dataset(
                    dataset, instrument, context_tf, setup_tf,
                )
                for instrument in instruments
            ]
            scan = self._scanner.scan(
                datasets, evaluation_time=t, engines=bundle,
            )
            points.append(ReplayEvaluationPoint(evaluation_time=t, scan=scan))

        return ReplayResult(
            replay_id=self._replay_id(instruments, times),
            instruments=instruments,
            timeframes=(context_tf, setup_tf),
            evaluation_times=times,
            points=tuple(points),
            rationale=self._rationale(times, points),
        )

    # ============================================================
    # HELPERS
    # ============================================================

    def _build_instrument_dataset(
        self,
        dataset: HistoricalDataset,
        instrument: str,
        context_tf: str,
        setup_tf: str,
    ) -> InstrumentDataset:
        """Build one instrument's scanner dataset from the historical data."""

        context = dataset.context_candles(instrument, context_tf)
        setup = dataset.setup_candles(instrument, setup_tf)
        return InstrumentDataset(
            instrument=instrument,
            context_candles=context,
            setup_candles=setup,
        )

    def _replay_id(
        self,
        instruments: tuple[str, ...],
        times: tuple[datetime, ...],
    ) -> str:
        import hashlib
        import json

        payload = {
            "context_timeframe": self.config.context_timeframe,
            "setup_timeframe": self.config.setup_timeframe,
            "instruments": list(instruments),
            "evaluation_times": [t.isoformat() for t in times],
            "label": self.config.label,
            "metadata": [[k, v] for k, v in self.config.metadata],
        }
        try:
            canon = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            canon = str(payload)
        digest = hashlib.sha256(canon.encode("utf-8")).hexdigest()
        return f"replay-{digest[:16]}"

    def _rationale(
        self,
        times: tuple[datetime, ...],
        points: list[ReplayEvaluationPoint],
    ) -> str:
        if not points:
            return (
                "Historical replay produced no evaluation points "
                "(no evaluation timestamps supplied). Descriptive only; "
                "not predictive and not a guarantee of profitability."
            )
        statuses = [p.scan.status.name for p in points]
        found = sum(1 for p in points if p.scan.has_best)
        return (
            f"Historical replay evaluated {len(points)} point(s) at "
            f"{len(times)} evaluation timestamp(s) across "
            f"{self.config.context_timeframe}/{self.config.setup_timeframe} "
            f"timeframes. Scan statuses: {statuses}. Best opportunity "
            f"identified at {found} point(s). Replay is deterministic and "
            "point-in-time correct (no future information is used). "
            "Descriptive only; not predictive and not a guarantee of "
            "profitability."
        )


# ============================================================
# EVALUATION TIMESTAMP SELECTION (deterministic helpers)
# ============================================================


def evaluation_times_from_setup_candles(
    dataset: HistoricalDataset,
    setup_timeframe: str,
    count: int | None = None,
    min_history: int = 10,
) -> tuple[datetime, ...]:
    """
    Build a deterministic, chronologically ordered sequence of
    evaluation timestamps from the setup-timeframe candles across
    instruments.

    The evaluation timestamps are the close timestamps of the
    setup-timeframe candles that are SHARED across instruments (the
    intersection of each instrument's setup candle timestamps), so that
    every instrument has information available at every evaluation
    point. Only candles at index >= ``min_history`` are considered (a
    point with insufficient history would be INCOMPLETE by design; the
    replay still evaluates it honestly, but the helper skips the leading
    warmup candles by default).

    When ``count`` is set, the LAST ``count`` shared timestamps are
    returned (the most recent evaluation points).
    """

    instruments = dataset.instruments
    if not instruments:
        return ()

    shared: set[datetime] | None = None
    for instrument in instruments:
        inst = dataset.get(instrument)
        if inst is None:
            continue
        series = inst.series.get(setup_timeframe)
        ts_set = (
            {c.timestamp for c in series.candles} if series is not None else set()
        )
        shared = ts_set if shared is None else (shared & ts_set)
        if not shared:
            break

    if not shared:
        return ()

    ordered = sorted(shared)
    # Skip warmup candles (insufficient history by design).
    warmup = ordered[:min_history]
    eligible = [t for t in ordered if t not in set(warmup)]
    if count is not None:
        eligible = eligible[-count:]
    return tuple(eligible)


__all__ = [
    "HistoricalReplayEngine",
    "ReplayEvaluationPoint",
    "ReplayResult",
    "evaluation_times_from_setup_candles",
]
