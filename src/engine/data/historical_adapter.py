"""
Historical market data adapter (Sprint 11V).

:class:`HistoricalDataAdapter` normalizes realistic historical OHLCV
records into the engine's canonical :class:`~engine.models.ohlcv.OHLCVCandle`
form, keyed by instrument and timeframe, and produces a
:class:`~engine.models.historical.HistoricalDataset` that explicitly
distinguishes VALID, INVALID and INCOMPLETE data.

The adapter is the data-ingestion layer of Sprint 11V. It implements NO
intelligence: no candle-pattern, structure, setup, candidate, decision,
opportunity or scanner logic. It REUSES the existing
:class:`~engine.models.ohlcv.OHLCVCandle` (whose ``__post_init__`` rejects
impossible OHLC relationships) and the existing
:class:`~engine.data.validator.DataValidator` semantics.

DESIGN PRINCIPLE — no fabricated data:

Invalid records (missing timestamp, impossible OHLC, high < low, negative
volume) are REJECTED per the existing repository convention — they are
recorded as :class:`NormalizationIssue` entries with
``severity == INVALID`` and never produce a candle. Incomplete series (a
missing required timeframe, an empty series, or insufficient history below
a configurable minimum) are represented EXPLICITLY as
:class:`TimeframeSeries` with ``quality == INCOMPLETE`` (empty candles
tuple) so missing data is surfaced honestly downstream — never silently
manufactured into a price, trend, structure, setup, stop, target or
risk/reward figure.

DESIGN PRINCIPLE — independence from any specific vendor:

The adapter consumes plain :class:`HistoricalRecord` objects (or
equivalent mappings). It has no dependency on any external data vendor
and no network dependency. Deterministic local fixtures drive automated
tests.

Normalization steps (deterministic):

1. Group records by (instrument, timeframe); records with a missing
   instrument or timeframe are recorded as INVALID issues and dropped.
2. Within each group, reject records with a missing timestamp, then
   construct the canonical ``OHLCVCandle`` (which rejects impossible
   OHLC / negative volume). Rejected records become INVALID issues.
3. Drop exact-timestamp duplicates (keeping the first occurrence is NOT
   deterministic enough across vendors; duplicates are reported as
   INVALID issues and the record is dropped to avoid ambiguity).
4. Sort chronologically. Detect non-increasing timestamps after sort
   (already handled by dedupe + sort, but reported if encountered).
5. Mark the series VALID when it has >= ``min_history`` candles,
   otherwise INCOMPLETE.
6. For each requested timeframe that produced NO records, emit an
   explicit INCOMPLETE series (empty candles) plus a MISSING_TIMEFRAME
   issue, so a missing timeframe is never silently absent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Mapping, Sequence

from engine.data.validator import DataValidator
from engine.models.historical import (
    DataQuality,
    HistoricalDataset,
    HistoricalInstrumentData,
    HistoricalNormalizationError,
    HistoricalRecord,
    NormalizationIssue,
    TimeframeSeries,
)
from engine.models.ohlcv import OHLCVCandle


def _to_record(value: HistoricalRecord | Mapping) -> HistoricalRecord:
    """Coerce a mapping into a :class:`HistoricalRecord`."""

    if isinstance(value, HistoricalRecord):
        return value
    if isinstance(value, Mapping):
        return HistoricalRecord(
            timestamp=value.get("timestamp"),
            open=value.get("open"),
            high=value.get("high"),
            low=value.get("low"),
            close=value.get("close"),
            volume=value.get("volume"),
            instrument=str(value.get("instrument", "") or ""),
            timeframe=str(value.get("timeframe", "") or ""),
        )
    raise TypeError(
        f"Unsupported historical record type: {type(value).__name__}",
    )


@dataclass(frozen=True, slots=True)
class HistoricalAdapterConfig:
    """
    Configuration for :class:`HistoricalDataAdapter`.

    Attributes:

    timeframes
        The canonical timeframe labels the adapter is expected to
        populate per instrument (e.g. ``("1D", "15M")``). A requested
        timeframe that produces no records becomes an explicit
        INCOMPLETE series rather than being silently omitted.

    min_history
        Minimum number of valid candles a series must carry to be VALID.
        Series with fewer candles are INCOMPLETE (insufficient history
        is never a directional conclusion).

    dedupe
        When ``True`` (default), exact-timestamp duplicate records within
        an (instrument, timeframe) group are rejected as INVALID (the
        duplicate is dropped, never silently merged). When ``False``, a
        duplicate raises a :class:`ValueError` (matching the strict
        ``DataValidator`` behaviour).
    """

    timeframes: tuple[str, ...] = ("1D", "15M")
    min_history: int = 10
    dedupe: bool = True

    def __post_init__(self) -> None:
        if not self.timeframes:
            raise ValueError("timeframes must be non-empty.")
        if len(set(self.timeframes)) != len(self.timeframes):
            raise ValueError("timeframes must be unique.")
        if self.min_history < 0:
            raise ValueError("min_history must be non-negative.")


class HistoricalDataAdapter:
    """
    Normalize realistic historical OHLCV records into a canonical
    :class:`HistoricalDataset`.

    Public API:

        normalize(records) -> HistoricalDataset

    The adapter is stateless across calls: identical inputs always
    produce identical outputs (deterministic ordering, no reliance on
    unordered iteration for the result).
    """

    def __init__(self, config: HistoricalAdapterConfig | None = None) -> None:
        self.config = config or HistoricalAdapterConfig()

    # ============================================================
    # PUBLIC API
    # ============================================================

    def normalize(
        self,
        records: Iterable[HistoricalRecord | Mapping],
    ) -> HistoricalDataset:
        """
        Normalize a batch of historical records into a
        :class:`HistoricalDataset`.

        Invalid records are rejected (recorded as INVALID issues, never
        turned into candles). Missing requested timeframes are
        represented as explicit INCOMPLETE series. The result is
        deterministic: identical inputs produce identical datasets.
        """

        issues: list[NormalizationIssue] = []
        # group_key -> list[HistoricalRecord], insertion-stable order.
        groups: dict[tuple[str, str], list[HistoricalRecord]] = {}
        # Track the set of instruments seen (even via invalid records so
        # that a fully-invalid instrument still surfaces its issues).
        instruments_seen: set[str] = set()

        for raw in records:
            record = _to_record(raw)
            if not record.instrument:
                issues.append(
                    NormalizationIssue(
                        severity=DataQuality.INVALID,
                        instrument="",
                        timeframe=record.timeframe,
                        error=HistoricalNormalizationError.MISSING_INSTRUMENT,
                        reason="Historical record is missing an instrument "
                        "identifier; rejected.",
                    ),
                )
                continue
            if not record.timeframe:
                issues.append(
                    NormalizationIssue(
                        severity=DataQuality.INVALID,
                        instrument=record.instrument,
                        timeframe="",
                        error=HistoricalNormalizationError.MISSING_TIMEFRAME,
                        reason=f"{record.instrument}: historical record is "
                        "missing a timeframe identifier; rejected.",
                    ),
                )
                instruments_seen.add(record.instrument)
                continue
            instruments_seen.add(record.instrument)
            groups.setdefault(
                (record.instrument, record.timeframe), [],
            ).append(record)

        # Build the per-instrument data, deterministic by sorted key.
        data: dict[str, HistoricalInstrumentData] = {}
        for instrument in sorted(instruments_seen):
            series_map: dict[str, TimeframeSeries] = {}
            for timeframe in self.config.timeframes:
                key = (instrument, timeframe)
                group = groups.get(key, [])
                series, group_issues = self._normalize_group(
                    instrument, timeframe, group,
                )
                issues.extend(group_issues)
                series_map[timeframe] = series
            # Also carry any non-requested timeframe groups that were
            # supplied (extensibility) — they are normalized too.
            for (inst, tf), group in groups.items():
                if inst != instrument or tf in self.config.timeframes:
                    continue
                series, group_issues = self._normalize_group(inst, tf, group)
                issues.extend(group_issues)
                series_map.setdefault(tf, series)
            data[instrument] = HistoricalInstrumentData(
                instrument=instrument, series=series_map,
            )

        return HistoricalDataset(
            data=data, issues=tuple(issues),
        )

    # ============================================================
    # PER-GROUP NORMALIZATION
    # ============================================================

    def _normalize_group(
        self,
        instrument: str,
        timeframe: str,
        group: Sequence[HistoricalRecord],
    ) -> tuple[TimeframeSeries, list[NormalizationIssue]]:
        """Normalize one (instrument, timeframe) record group."""

        issues: list[NormalizationIssue] = []
        candles: list[OHLCVCandle] = []
        seen_ts: set[datetime] = set()

        for record in group:
            if record.timestamp is None:
                issues.append(
                    NormalizationIssue(
                        severity=DataQuality.INVALID,
                        instrument=instrument,
                        timeframe=timeframe,
                        error=HistoricalNormalizationError.MISSING_TIMESTAMP,
                        reason=f"{instrument}/{timeframe}: record is missing "
                        "a timestamp; rejected.",
                    ),
                )
                continue

            candle, issue = self._build_candle(instrument, timeframe, record)
            if issue is not None:
                issues.append(issue)
                continue
            assert candle is not None

            if candle.timestamp in seen_ts:
                if self.config.dedupe:
                    issues.append(
                        NormalizationIssue(
                            severity=DataQuality.INVALID,
                            instrument=instrument,
                            timeframe=timeframe,
                            error=HistoricalNormalizationError.DUPLICATE_TIMESTAMP,
                            reason=f"{instrument}/{timeframe}: duplicate "
                            f"timestamp {candle.timestamp.isoformat()} "
                            "dropped (ambiguous; never silently merged).",
                        ),
                    )
                    continue
                raise ValueError(
                    f"{instrument}/{timeframe}: duplicate timestamp "
                    f"{candle.timestamp.isoformat()} encountered.",
                )
            seen_ts.add(candle.timestamp)
            candles.append(candle)

        # Chronological ordering. Non-increasing timestamps after dedupe
        # are reported (defensive; sort still produces a stable order).
        candles.sort(key=lambda c: c.timestamp)
        unsorted = [
            c.timestamp for i, c in enumerate(candles)
            if i > 0 and c.timestamp <= candles[i - 1].timestamp
        ]
        if unsorted:
            issues.append(
                NormalizationIssue(
                    severity=DataQuality.INVALID,
                    instrument=instrument,
                    timeframe=timeframe,
                    error=HistoricalNormalizationError.UNORDERED,
                    reason=f"{instrument}/{timeframe}: non-increasing "
                    "timestamps detected after normalization; series kept "
                    "in sorted order.",
                ),
            )

        if not candles:
            issues.append(
                NormalizationIssue(
                    severity=DataQuality.INCOMPLETE,
                    instrument=instrument,
                    timeframe=timeframe,
                    error=HistoricalNormalizationError.EMPTY_SERIES,
                    reason=f"{instrument}/{timeframe}: no usable candles "
                    "after normalization; series INCOMPLETE.",
                ),
            )
            return (
                TimeframeSeries(
                    instrument=instrument, timeframe=timeframe,
                    candles=(), quality=DataQuality.INCOMPLETE,
                ),
                issues,
            )

        if len(candles) < self.config.min_history:
            issues.append(
                NormalizationIssue(
                    severity=DataQuality.INCOMPLETE,
                    instrument=instrument,
                    timeframe=timeframe,
                    error=HistoricalNormalizationError.INSUFFICIENT_HISTORY,
                    reason=f"{instrument}/{timeframe}: only "
                    f"{len(candles)} candle(s) available (min "
                    f"{self.config.min_history}); series INCOMPLETE.",
                ),
            )
            return (
                TimeframeSeries(
                    instrument=instrument, timeframe=timeframe,
                    candles=tuple(candles), quality=DataQuality.INCOMPLETE,
                ),
                issues,
            )

        # Final structural validation (defensive; matches DataValidator).
        try:
            DataValidator.validate_dataset(list(candles))
        except ValueError as exc:
            issues.append(
                NormalizationIssue(
                    severity=DataQuality.INVALID,
                    instrument=instrument,
                    timeframe=timeframe,
                    error=HistoricalNormalizationError.INVALID_OHLC,
                    reason=f"{instrument}/{timeframe}: dataset validation "
                    f"failed: {exc}; series rejected.",
                ),
            )
            return (
                TimeframeSeries(
                    instrument=instrument, timeframe=timeframe,
                    candles=(), quality=DataQuality.INVALID,
                ),
                issues,
            )

        return (
            TimeframeSeries(
                instrument=instrument, timeframe=timeframe,
                candles=tuple(candles), quality=DataQuality.VALID,
            ),
            issues,
        )

    @staticmethod
    def _build_candle(
        instrument: str,
        timeframe: str,
        record: HistoricalRecord,
    ) -> tuple[OHLCVCandle | None, NormalizationIssue | None]:
        """Construct a canonical candle from one record, or an issue."""

        ohlc = (record.open, record.high, record.low, record.close, record.volume)
        if any(v is None for v in ohlc):
            return None, NormalizationIssue(
                severity=DataQuality.INVALID,
                instrument=instrument,
                timeframe=timeframe,
                error=HistoricalNormalizationError.INVALID_OHLC,
                reason=f"{instrument}/{timeframe}: record is missing an "
                "OHLCV value; rejected.",
            )

        try:
            candle = OHLCVCandle(
                timestamp=record.timestamp,  # type: ignore[arg-type]
                open=record.open,  # type: ignore[arg-type]
                high=record.high,  # type: ignore[arg-type]
                low=record.low,  # type: ignore[arg-type]
                close=record.close,  # type: ignore[arg-type]
                volume=record.volume,  # type: ignore[arg-type]
            )
        except ValueError as exc:
            msg = str(exc)
            if "Volume" in msg:
                error = HistoricalNormalizationError.NEGATIVE_VOLUME
            elif "cannot be lower than low" in msg:
                error = HistoricalNormalizationError.HIGH_BELOW_LOW
            elif "Open" in msg:
                error = HistoricalNormalizationError.INVALID_OHLC
            elif "Close" in msg:
                error = HistoricalNormalizationError.INVALID_OHLC
            else:
                error = HistoricalNormalizationError.INVALID_OHLC
            return None, NormalizationIssue(
                severity=DataQuality.INVALID,
                instrument=instrument,
                timeframe=timeframe,
                error=error,
                reason=f"{instrument}/{timeframe}: {exc}; rejected.",
            )
        return candle, None


__all__ = [
    "HistoricalAdapterConfig",
    "HistoricalDataAdapter",
]
