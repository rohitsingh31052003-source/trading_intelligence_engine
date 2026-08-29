"""
READ-ONLY Historical Corpus Integrity Audit (Checkpoint 3B).

The audit inspects the EXISTING persisted corpus (Product Phase 6A /
Checkpoint 3B historical store) and reports its condition WITHOUT
modifying anything. It is strictly READ -> CHECK -> REPORT:

* NEVER calls a provider and NEVER requires ``UPSTOX_ANALYTICS_TOKEN``.
* NEVER ingests data, NEVER writes / repairs / rewrites candle files.
* NEVER silently discards invalid candles and NEVER "fixes" bad OHLC.
* Leaves the planner, the provider, and every prediction / evidence /
  trading / replay component untouched.
* Reuses the existing domain logic: the Phase 6B store loaders, the
  canonical ``OHLCVCandle`` / ``DataValidator`` contract, the Phase 6A
  gap-detection logic and the Checkpoint 3B planner coverage semantics.

The audit is deterministic: identical corpus + identical inputs always
produce identical results (the audit id derives from the canonical
request identity — no wall-clock, no randomness).

``data/__init__.py`` stays intentionally empty — import via full paths:

    from engine.data.corpus_audit import CorpusAuditEngine
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from typing import Iterable, Sequence

from engine.config.corpus_plan_config import (
    CorpusPlanConfig,
    validate_plan_window,
)
from engine.data.corpus_plan import (
    CorpusPreparationPlanner,
    monthly_chunks_for_window,
)
from engine.data.historical_gaps import (
    GapDetectionConfig,
    detect_gaps,
)
from engine.data.historical_store import HistoricalDataStore
from engine.models.corpus_audit import (
    AuditCheckStatus,
    CorpusAuditReport,
    DatasetAuditResult,
)
from engine.models.corpus_plan import (
    DatasetCoverage,
    DatasetCoverageStatus,
)
from engine.models.historical_data import (
    GapKind,
    HistoricalGap,
    ResearchUniverse,
)
from engine.models.ohlcv import OHLCVCandle

DEFAULT_RESEARCH_UNIVERSE = ResearchUniverse()

#: Maximum number of offending timestamps surfaced per failed check
#: (report brevity; the full count is always reported).
_MAX_ISSUE_TIMESTAMPS = 5

#: The well-known provider anomaly observed during corpus ingestion
#: (HDFCBANK 15m June 2024). The audit reports it explicitly and then
#: independently inspects the PERSISTED data — a previous provider
#: anomaly does NOT automatically classify the dataset as failed.
KNOWN_CORPUS_ANOMALY = {
    "instrument": "HDFCBANK",
    "timeframe": "15m",
    "window_start": (2024, 6, 1),
    "window_end": (2024, 7, 1),
    "message": (
        "The corpus ingestion runner previously encountered: 'Open price "
        "must lie between low and high.'"
    ),
}


def _is_finite(value: object) -> bool:
    """True when ``value`` is a finite real number (int/float, not NaN).

    The canonical ``OHLCVCandle`` contract accepts ints/floats; a row
    carrying ``NaN`` / infinity is flagged by the OHLC / volume checks
    before any comparison (a ``float('nan')`` could otherwise slip
    through ``low <= open <= high``).
    """

    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        try:
            return math.isfinite(float(value))
        except (TypeError, ValueError, OverflowError):
            return False
    return False


class CorpusAuditEngine:
    """
    Read-only integrity auditor over the existing historical store.

    ``planner`` is the EXISTING :class:`CorpusPreparationPlanner` (its
    coverage semantics are reused for the coverage check; the planner
    itself never fetches data and is not modified). ``store`` / ``config``
    may be supplied explicitly or derived from the planner.
    """

    def __init__(
        self,
        planner: CorpusPreparationPlanner | None = None,
        config: CorpusPlanConfig | None = None,
        *,
        store: HistoricalDataStore | None = None,
        universe: ResearchUniverse | None = None,
        instruments: Sequence[str] | None = None,
        gap_config: GapDetectionConfig | None = None,
    ) -> None:
        cfg = config or CorpusPlanConfig()
        if planner is None:
            planner = CorpusPreparationPlanner(
                cfg,
                store=store,
                universe=universe or DEFAULT_RESEARCH_UNIVERSE,
                instruments=instruments,
            )
        self.planner = planner
        self.config = cfg
        # The audit reads through the planner's store (the single store
        # reference); the planner is never mutated by the audit.
        self.store: HistoricalDataStore | None = (
            store if store is not None else planner.store
        )
        self.gap_config = gap_config
        self._instrument_override = tuple(instruments) if instruments else None

    # ------------------------------------------------------------
    # COVERAGE (reused planner semantics)
    # ------------------------------------------------------------

    def _coverage(
        self,
        instrument: str,
        timeframe: str,
        stored_count: int,
        first_ts: datetime | None,
        last_ts: datetime | None,
        chunks: tuple[tuple[datetime, datetime], ...],
        *,
        dataset_exists: bool,
    ) -> DatasetCoverage | None:
        """
        Planner coverage semantics for one dataset (store-derived).

        Mirrors ``CorpusPreparationPlanner._coverage_for`` so the audit
        uses the SAME chunk-coverage rule rather than inventing a new
        one: a monthly chunk is covered when the stored series intersects
        it (``first < chunk_end`` and ``last >= chunk_start``).

        The instrument span for the coverage intersection is computed
        from stored candles only, so a dataset that is COMPLETE for the
        requested window but contains dates OUTSIDE that window is not
        misjudged. Returns ``None`` when no store is configured (the
        honest UNAVAILABLE case).
        """

        if self.store is None:
            return None
        required_chunks = len(chunks)
        if not dataset_exists:
            status = DatasetCoverageStatus.MISSING
            note = "dataset not stored; every planned chunk is missing."
        elif stored_count <= 0:
            status = DatasetCoverageStatus.EMPTY
            note = "dataset stored but empty; every planned chunk is missing."
        elif first_ts is None or last_ts is None:
            # Defensive: a non-zero count without timestamps cannot confirm
            # coverage — never fabricated as COMPLETE.
            status = DatasetCoverageStatus.PARTIAL
            note = "stored candles present but timestamps unavailable."
        else:
            covered = sum(
                1
                for c_start, c_end in chunks
                if first_ts < c_end and last_ts >= c_start
            )
            if covered == required_chunks:
                status = DatasetCoverageStatus.COMPLETE
                note = "full planned window is covered by the stored dataset."
            else:
                status = DatasetCoverageStatus.PARTIAL
                note = (
                    f"{covered}/{required_chunks} planned chunks covered; "
                    f"{required_chunks - covered} chunk(s) still missing."
                )
        missing = sorted(
            self._chunk_key(timeframe, c_start, c_end)
            for c_start, c_end in chunks
        ) if status is not DatasetCoverageStatus.COMPLETE else ()
        return DatasetCoverage(
            instrument=instrument,
            timeframe=timeframe,
            status=status,
            stored_count=stored_count,
            stored_first=first_ts.isoformat() if first_ts else None,
            stored_last=last_ts.isoformat() if last_ts else None,
            required_chunks=required_chunks,
            covered_chunks=(
                0
                if status is not DatasetCoverageStatus.COMPLETE
                else required_chunks
            ),
            missing_chunk_keys=missing,
            note=note,
        )

    @staticmethod
    def _chunk_key(
        timeframe: str,
        chunk_start: datetime,
        chunk_end: datetime,
    ) -> str:
        """Deterministic chunk key (matches the planner's key format)."""

        return (
            f"{timeframe}:{chunk_start.isoformat()}:{chunk_end.isoformat()}"
        )

    # ------------------------------------------------------------
    # DATA-QUALITY CHECKS (read-only; invalid rows reported, never repaired)
    # ------------------------------------------------------------

    @staticmethod
    def _ts_key(timestamp: datetime) -> datetime:
        """
        Deterministic ordering/dedupe key for a parsed timestamp.

        Aware timestamps are converted to UTC. A NAIVE timestamp keeps
        the instant viewed as UTC — strictly a compare-key so the audit
        can still run chronology / duplicate detection on a persisted
        file that (illegally) contains naive rows; the naive rows are
        independently flagged by the timezone check and the file is
        NEVER rewritten.
        """

        if timestamp.tzinfo is None:
            return timestamp.replace(tzinfo=UTC)
        return timestamp.astimezone(UTC)

    @staticmethod
    def _row_timestamp(row: dict) -> datetime | None:
        """Parse a raw row's timestamp (None when missing/unparseable)."""

        raw = row.get("timestamp")
        if not isinstance(raw, str):
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None

    @staticmethod
    def _row_numeric(row: dict, name: str) -> float | None:
        """
        The numeric value of one OHLC/volume field (safely parsed).

        ``None`` when the field is absent / a bool / NaN / infinite —
        so a non-finite persisted number is caught without ever relying
        on comparisons that silently pass on NaN.
        """

        value = row.get(name)
        if isinstance(value, bool) or value is None:
            return None
        try:
            numeric = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return numeric if math.isfinite(numeric) else None

    @classmethod
    def _analyze_rows(
        cls,
        rows: Sequence[dict],
    ) -> dict:
        """
        Single-pass data-quality analysis of one persisted dataset.

        Runs every per-row check on the RAW persisted rows so an invalid
        row that could never load into an ``OHLCVCandle`` (NaN / open
        outside range / negative volume) is still detected and reported
        with its timestamp. The rows are REJECTED by this audit only in
        the sense of being counted — never dropped, never repaired.

        Returns a deterministic dict with the check verdicts and counts.
        """

        timestamps: list[datetime] = []
        aware = True
        duplicate_count = 0
        ohlc_invalid = 0
        volume_invalid = 0
        timezone_issues: list[str] = []
        ohlc_issues: list[str] = []
        volume_issues: list[str] = []

        seen: set[datetime] = set()
        for row in rows:
            timestamp = cls._row_timestamp(row)
            if timestamp is not None:
                key = cls._ts_key(timestamp)
                if key in seen:
                    duplicate_count += 1
                else:
                    seen.add(key)
                timestamps.append(timestamp)
            if timestamp is None or timestamp.tzinfo is None:
                aware = False
                label = (
                    timestamp.isoformat()
                    if timestamp is not None
                    else "unparseable timestamp"
                )
                if len(timezone_issues) < _MAX_ISSUE_TIMESTAMPS:
                    timezone_issues.append(label)

            o = cls._row_numeric(row, "open")
            h = cls._row_numeric(row, "high")
            l = cls._row_numeric(row, "low")
            c = cls._row_numeric(row, "close")
            v = cls._row_numeric(row, "volume")
            label = (
                timestamp.isoformat() if timestamp is not None else "unavailable"
            )

            if any(value is None for value in (o, h, l, c)):
                ohlc_invalid += 1
                if len(ohlc_issues) < _MAX_ISSUE_TIMESTAMPS:
                    ohlc_issues.append(f"{label} (non-finite OHLC value)")
            elif h < l:
                ohlc_invalid += 1
                if len(ohlc_issues) < _MAX_ISSUE_TIMESTAMPS:
                    ohlc_issues.append(f"{label} (high below low)")
            elif not l <= o <= h:
                ohlc_invalid += 1
                if len(ohlc_issues) < _MAX_ISSUE_TIMESTAMPS:
                    ohlc_issues.append(f"{label} (open outside range)")
            elif not l <= c <= h:
                ohlc_invalid += 1
                if len(ohlc_issues) < _MAX_ISSUE_TIMESTAMPS:
                    ohlc_issues.append(f"{label} (close outside range)")

            if v is None or v < 0:
                volume_invalid += 1
                if len(volume_issues) < _MAX_ISSUE_TIMESTAMPS:
                    volume_issues.append(label)

        chronology = (
            AuditCheckStatus.PASS
            if (
                not timestamps
                or [cls._ts_key(ts) for ts in timestamps]
                == sorted(cls._ts_key(ts) for ts in timestamps)
            )
            else AuditCheckStatus.FAIL
        )
        duplicates = (
            AuditCheckStatus.PASS
            if duplicate_count == 0
            else AuditCheckStatus.FAIL
        )
        timezone = (
            AuditCheckStatus.PASS if aware else AuditCheckStatus.FAIL
        )
        ohlc = (
            AuditCheckStatus.PASS if ohlc_invalid == 0 else AuditCheckStatus.FAIL
        )
        volume = (
            AuditCheckStatus.PASS if volume_invalid == 0 else AuditCheckStatus.FAIL
        )
        return {
            "chronology": chronology,
            "duplicates": duplicates,
            "duplicate_count": duplicate_count,
            "timezone": timezone,
            "timezone_issues": tuple(timezone_issues),
            "ohlc": ohlc,
            "ohlc_invalid": ohlc_invalid,
            "ohlc_issues": tuple(ohlc_issues),
            "volume": volume,
            "volume_invalid": volume_invalid,
            "volume_issues": tuple(volume_issues),
            "timestamps": timestamps,
        }

    # ------------------------------------------------------------
    # PER-DATASET AUDIT
    # ------------------------------------------------------------

    def _read_rows(
        self,
        instrument: str,
        timeframe: str,
    ) -> tuple[list[dict], str]:
        """
        Read the persisted rows of ONE dataset (read-only, tolerant).

        Returns ``(rows, load_error)``. A corrupt file / schema mismatch
        is reported as ``load_error`` (with whatever rows could be
        parsed) — the audit never silently skips a corrupted dataset and
        never rewrites it.
        """

        if self.store is None:
            return [], "no historical store configured; dataset unreadable."
        path = self.store.path_for(instrument, timeframe)
        if not path.exists():
            return [], ""
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            return [], f"could not read {path.name}: {exc}"
        except json.JSONDecodeError as exc:
            return [], f"corrupted JSON in {path.name}: {exc}"
        if not isinstance(payload, dict):
            return [], f"malformed payload in {path.name}: expected an object."
        candles = payload.get("candles")
        if not isinstance(candles, list):
            return [], f"malformed payload in {path.name}: 'candles' must be a list."
        rows = [row for row in candles if isinstance(row, dict)]
        if len(rows) != len(candles):
            return rows, (
                f"malformed payload in {path.name}: "
                f"{len(candles) - len(rows)} row(s) are not objects."
            )
        return rows, ""

    def _valid_candles(
        self,
        rows: Sequence[dict],
    ) -> tuple[OHLCVCandle, ...]:
        """
        The valid subset of raw rows as canonical candles (gap input
        only). Invalid rows are EXCLUDED from the gap scan — an invalid
        row is reported (never repaired) and never silently passed over.
        """

        candles: list[OHLCVCandle] = []
        for row in rows:
            timestamp = self._row_timestamp(row)
            o = self._row_numeric(row, "open")
            h = self._row_numeric(row, "high")
            l = self._row_numeric(row, "low")
            c = self._row_numeric(row, "close")
            v = self._row_numeric(row, "volume")
            if timestamp is None or timestamp.tzinfo is None:
                continue
            if any(value is None for value in (o, h, l, c, v)):
                continue
            if not (l <= o <= h and l <= c <= h and v >= 0):
                continue
            try:
                candles.append(
                    OHLCVCandle(
                        timestamp=timestamp, open=o, high=h, low=l,
                        close=c, volume=v,
                    ),
                )
            except ValueError:
                continue
        return tuple(candles)

    def _audit_dataset(
        self,
        instrument: str,
        timeframe: str,
        chunks: tuple[tuple[datetime, datetime], ...],
    ) -> DatasetAuditResult:
        """
        Read + check ONE (instrument, timeframe) dataset.

        The dataset is read read-only through the existing store; every
        per-row check runs on the RAW persisted rows so an invalid row
        that could never load into an ``OHLCVCandle`` (NaN / open outside
        range / negative volume) is still detected and reported with its
        timestamp — never repaired, never silently discarded.
        """

        rows, load_error = self._read_rows(instrument, timeframe)
        dataset_exists = (
            self.store is not None and self.store.exists(instrument, timeframe)
        )

        if rows:
            analysis = self._analyze_rows(rows)
            chronological = analysis["chronology"]
            duplicates = analysis["duplicates"]
            duplicate_count = analysis["duplicate_count"]
            timezone_aware = analysis["timezone"]
            timezone_issues = analysis["timezone_issues"]
            ohlc = analysis["ohlc"]
            ohlc_invalid = analysis["ohlc_invalid"]
            ohlc_issues = analysis["ohlc_issues"]
            volume = analysis["volume"]
            volume_invalid = analysis["volume_invalid"]
            volume_issues = analysis["volume_issues"]
            first_ts = (
                analysis["timestamps"][0]
                if analysis["timestamps"]
                else None
            )
            last_ts = (
                analysis["timestamps"][-1]
                if analysis["timestamps"]
                else None
            )
            # Normalize for coverage intersection: a NAIVE stored
            # timestamp is compared as its UTC instant (a compare-key
            # only) so coverage never crashes on a naive row; the naive
            # rows are independently flagged by the timezone check and
            # the file is NEVER rewritten.
            first_ts = (
                self._ts_key(first_ts) if first_ts is not None else None
            )
            last_ts = (
                self._ts_key(last_ts) if last_ts is not None else None
            )
            valid_candles = self._valid_candles(rows)
            gaps = self._gaps_for(valid_candles, timeframe)
            gap_closure = sum(
                1 for g in gaps if g.kind is GapKind.POSSIBLE_MARKET_CLOSURE
            )
            gap_unexpected = sum(
                1 for g in gaps if g.kind is GapKind.UNEXPECTED_GAP
            )
        else:
            chronological = (
                AuditCheckStatus.N_A
                if not load_error
                else AuditCheckStatus.FAIL
            )
            duplicates = (
                AuditCheckStatus.N_A
                if not load_error
                else AuditCheckStatus.FAIL
            )
            duplicate_count = 0
            timezone_aware = (
                AuditCheckStatus.N_A
                if not load_error
                else AuditCheckStatus.FAIL
            )
            timezone_issues = ()
            ohlc = (
                AuditCheckStatus.N_A
                if not load_error
                else AuditCheckStatus.FAIL
            )
            ohlc_invalid = 0
            ohlc_issues = ()
            volume = (
                AuditCheckStatus.N_A
                if not load_error
                else AuditCheckStatus.FAIL
            )
            volume_invalid = 0
            volume_issues = ()
            gaps = ()
            gap_closure = 0
            gap_unexpected = 0
            first_ts = None
            last_ts = None

        coverage = None
        if not load_error:
            coverage = self._coverage(
                instrument,
                timeframe,
                len(rows),
                first_ts,
                last_ts,
                chunks,
                dataset_exists=dataset_exists,
            )

        return DatasetAuditResult(
            instrument=instrument,
            timeframe=timeframe,
            coverage=coverage,
            candle_count=len(rows),
            first_timestamp=first_ts,
            last_timestamp=last_ts,
            dataset_exists=dataset_exists,
            chronological=chronological,
            duplicates=duplicates,
            duplicate_count=duplicate_count,
            timezone_aware=timezone_aware,
            timezone_issues=timezone_issues,
            ohlc=ohlc,
            ohlc_invalid_count=ohlc_invalid,
            ohlc_issue_timestamps=ohlc_issues,
            volume=volume,
            volume_invalid_count=volume_invalid,
            volume_issue_timestamps=volume_issues,
            gaps=gaps,
            gap_closure_count=gap_closure,
            gap_unexpected_count=gap_unexpected,
            load_error=load_error,
        )

    def _gaps_for(
        self,
        candles: Sequence[OHLCVCandle],
        timeframe: str,
    ) -> tuple[HistoricalGap, ...]:
        """Gap detection via the existing Phase 6A logic (never rebuilt)."""

        return detect_gaps(candles, timeframe, self.gap_config)

    # ------------------------------------------------------------
    # UNIVERSE / WINDOW RESOLUTION
    # ------------------------------------------------------------

    def _resolve_instruments(
        self,
        instruments: Sequence[str] | None,
    ) -> tuple[str, ...]:
        if instruments is not None:
            normalized = tuple(
                sorted({str(i).strip().upper() for i in instruments if str(i).strip()}),
            )
            if not normalized:
                raise ValueError("instruments must not be empty.")
            return normalized
        if self._instrument_override:
            return tuple(
                sorted(str(i).strip().upper() for i in self._instrument_override),
            )
        if self.planner._instruments_override:
            return tuple(
                sorted(str(i).strip().upper() for i in self.planner._instruments_override),
            )
        return tuple(sorted(self.planner.universe))

    # ------------------------------------------------------------
    # AUDIT
    # ------------------------------------------------------------

    def audit(
        self,
        instruments: Sequence[str] | None = None,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        label: str = "",
        metadata: Iterable[tuple[str, str]] | None = None,
    ) -> CorpusAuditReport:
        """
        Audit every (instrument, timeframe) dataset over the window.

        The audit performs NO writes and NEVER invokes a provider. It
        reads each dataset through the existing store, runs the
        data-quality checks, and computes the planner-derived coverage
        classification. Deterministic for identical inputs.
        """

        cfg = self.config
        plan_start = start if start is not None else cfg.start
        plan_end = end if end is not None else cfg.end
        if plan_start is None or plan_end is None:
            raise ValueError(
                "an audit window is required: pass start/end or set them on "
                "the config.",
            )
        plan_start, plan_end = validate_plan_window(plan_start, plan_end)
        resolved = self._resolve_instruments(instruments)

        # Same deterministic monthly chunk grid the planner uses — the
        # coverage check compares the same chunk boundaries.
        chunks = monthly_chunks_for_window(plan_start, plan_end)

        results = tuple(
            self._audit_dataset(instrument, timeframe, chunks)
            for timeframe in cfg.timeframes
            for instrument in resolved
        )

        identity = (
            repr(resolved)
            + "|"
            + repr(cfg.timeframes)
            + "|"
            + plan_start.isoformat()
            + "|"
            + plan_end.isoformat()
            + "|"
            + str(label)
            + "|"
            + repr(tuple(sorted((str(k), str(v)) for k, v in (metadata or ()))))
        )
        audit_id = "audit-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        return CorpusAuditReport(
            audit_id=audit_id,
            instruments=resolved,
            timeframes=cfg.timeframes,
            start=plan_start,
            end=plan_end,
            results=results,
            label=label,
            metadata=tuple((str(k), str(v)) for k, v in (metadata or ())),
        )

    # ------------------------------------------------------------
    # PROJECTION
    # ------------------------------------------------------------

    def audit_to_jsonable(self, report: CorpusAuditReport) -> dict:
        """Deterministic JSON-serializable projection of an audit report."""

        rows = []
        for result in report.results:
            coverage = None
            if result.coverage is not None:
                coverage = {
                    "status": result.coverage.status.value,
                    "stored_count": result.coverage.stored_count,
                    "stored_first": result.coverage.stored_first,
                    "stored_last": result.coverage.stored_last,
                    "required_chunks": result.coverage.required_chunks,
                    "covered_chunks": result.coverage.covered_chunks,
                    "missing_chunk_keys": list(
                        result.coverage.missing_chunk_keys,
                    ),
                    "note": result.coverage.note,
                }
            rows.append(
                {
                    "instrument": result.instrument,
                    "timeframe": result.timeframe,
                    "exists": result.exists,
                    "dataset_exists": result.dataset_exists,
                    "status": result.status.value,
                    "candle_count": result.candle_count,
                    "first_timestamp": (
                        result.first_timestamp.isoformat()
                        if result.first_timestamp
                        else None
                    ),
                    "last_timestamp": (
                        result.last_timestamp.isoformat()
                        if result.last_timestamp
                        else None
                    ),
                    "chronological": result.chronological.value,
                    "duplicates": result.duplicates.value,
                    "duplicate_count": result.duplicate_count,
                    "timezone_aware": result.timezone_aware.value,
                    "timezone_issues": list(result.timezone_issues),
                    "ohlc": result.ohlc.value,
                    "ohlc_invalid_count": result.ohlc_invalid_count,
                    "ohlc_issue_timestamps": list(
                        result.ohlc_issue_timestamps,
                    ),
                    "volume": result.volume.value,
                    "volume_invalid_count": result.volume_invalid_count,
                    "volume_issue_timestamps": list(
                        result.volume_issue_timestamps,
                    ),
                    "gap_closure_count": result.gap_closure_count,
                    "gap_unexpected_count": result.gap_unexpected_count,
                    "gaps": [g.kind.value for g in result.gaps],
                    "load_error": result.load_error,
                    "coverage": coverage,
                },
            )
        return {
            "audit_id": report.audit_id,
            "instruments": list(report.instruments),
            "timeframes": list(report.timeframes),
            "start": report.start.isoformat(),
            "end": report.end.isoformat(),
            "label": report.label,
            "metadata": [list(pair) for pair in report.metadata],
            "rows": rows,
            "dataset_count": report.dataset_count,
            "complete": report.complete_count,
            "partial": report.partial_count,
            "missing": report.missing_count,
            "empty": report.empty_count,
            "unavailable": report.unavailable_count,
            "integrity_failures": report.integrity_failures,
            "verdict": report.verdict,
        }


__all__ = [
    "KNOWN_CORPUS_ANOMALY",
    "CorpusAuditEngine",
]