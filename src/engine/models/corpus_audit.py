"""
Models for the READ-ONLY historical corpus integrity audit (Checkpoint 3B).

This layer inspects the EXISTING persisted corpus (Product Phase 6A /
Checkpoint 3B historical store) and reports its condition WITHOUT
modifying anything. It is an AUDIT vocabulary only:

* It implements NO trading / scoring / prediction / decision / evidence
  / paper-trading logic.
* It NEVER fetches market data, NEVER requires a provider / token and
  NEVER writes to the historical store.
* It reuses, rather than duplicates, the established domain vocabulary:
  ``DatasetCoverage`` / ``DatasetCoverageStatus`` (the planner's
  coverage semantics) and ``HistoricalGap`` / ``GapKind`` (the Phase 6A
  gap-detection terminology).

DESIGN PRINCIPLE — completeness vs data-quality anomalies are separate:

A dataset can be COMPLETE in *coverage* while still requiring review
because an individual persisted candle violates the canonical OHLC /
volume contract. The audit therefore reports BOTH the planner-derived
coverage classification AND the independent persisted-data checks, and
its final verdict distinguishes "corpus completeness" from
"data-quality anomalies".

Optional fields use ``None`` so "unavailable" / "not applicable" is
never silently a real value. Frozen + slots everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from engine.models.corpus_plan import (
    DatasetCoverage,
    DatasetCoverageStatus,
)
from engine.models.historical_data import HistoricalGap


class AuditCheckStatus(Enum):
    """
    Result of ONE data-quality check on a persisted dataset.

    PASS        the check found no problem.
    FAIL        the check found a concrete problem (an offending row /
                issue is reported — never repaired).
    N_A         the check is not applicable because the dataset has no
                usable candles (MISSING / EMPTY) — a missing dataset is
                reported as such, NOT as a fabricated PASS.
    NOT_MEASURED
        the check could not be measured (e.g. no coverage measurement
        without a store) — reported honestly, never converted to PASS.
    """

    PASS = "PASS"
    FAIL = "FAIL"
    N_A = "N_A"
    NOT_MEASURED = "NOT_MEASURED"

    @property
    def is_pass(self) -> bool:
        """True only for a real PASS (N_A / NOT_MEASURED never count)."""

        return self is AuditCheckStatus.PASS


@dataclass(frozen=True, slots=True)
class DatasetAuditResult:
    """
    The full read-only audit result for ONE (instrument, timeframe).

    Attributes:

    instrument / timeframe
        Audited identity (canonicalized).

    coverage
        The planner-derived coverage classification for the planned
        window (``None`` when no store is configured — the honest
        UNAVAILABLE case).

    candle_count
        Number of stored candles (0 for MISSING / EMPTY datasets).

    first_timestamp / last_timestamp
        First / last stored candle (``None`` when no usable candles).

    chronological
    duplicates
        Chronology / duplicate checks (duplicate *count* on
        ``duplicate_count`` — stored timestamps are kept as-is; the
        duplicate counter is reported, never repaired).

    duplicate_count
        Number of duplicate timestamps observed (0 == PASS).

    timezone_aware
        Timezone-awareness check (awareness is reported per its own
        check; the first offending candle listed in ``timezone_issues``).

    timezone_issues
        ISO timestamps of candles lacking timezone awareness (first few
        only; capped for report brevity).

    ohlc / ohlc_invalid_count / ohlc_issue_timestamps
        OHLC validity check (the canonical
        ``low <= open <= high`` / ``low <= close <= high`` relation plus
        finite numeric values), the count of invalid rows and the first
        few offending timestamps. Invalid rows are reported, NEVER
        repaired or discarded.

    volume / volume_invalid_count / volume_issue_timestamps
        Volume validity check (the existing non-negative-volume rule —
        the same contract ``OHLCVCandle`` / ``DataValidator`` enforce).
        Invalid rows reported, never repaired.

    gaps
        Detected chronological gaps using the existing Phase 6A
        gap-detection logic (``POSSIBLE_MARKET_CLOSURE`` vs
        ``UNEXPECTED_GAP`` terminology preserved).

    gap_closure_count / gap_unexpected_count
        Per-kind gap tallies.

    load_error
        Resolved load failure reason (``""`` when the dataset loaded
        cleanly). A corrupt / unreadable dataset is reported here — never
        silently bypassed.
    """

    instrument: str
    timeframe: str
    coverage: DatasetCoverage | None
    candle_count: int
    first_timestamp: datetime | None
    last_timestamp: datetime | None
    chronological: AuditCheckStatus
    duplicates: AuditCheckStatus
    duplicate_count: int
    timezone_aware: AuditCheckStatus
    timezone_issues: tuple[str, ...]
    ohlc: AuditCheckStatus
    ohlc_invalid_count: int
    ohlc_issue_timestamps: tuple[str, ...]
    volume: AuditCheckStatus
    volume_invalid_count: int
    volume_issue_timestamps: tuple[str, ...]
    gaps: tuple[HistoricalGap, ...]
    gap_closure_count: int
    gap_unexpected_count: int
    load_error: str = ""
    dataset_exists: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, str) or not self.instrument.strip():
            raise ValueError("instrument must be a non-empty string.")
        object.__setattr__(self, "instrument", self.instrument.strip().upper())
        if not isinstance(self.timeframe, str) or not self.timeframe.strip():
            raise ValueError("timeframe must be a non-empty string.")
        if self.candle_count < 0:
            raise ValueError("candle_count must be non-negative.")
        for count in (
            self.duplicate_count,
            self.ohlc_invalid_count,
            self.volume_invalid_count,
            self.gap_closure_count,
            self.gap_unexpected_count,
        ):
            if count < 0:
                raise ValueError("count fields must be non-negative.")
        if self.candle_count == 0:
            if self.duplicate_count != 0:
                raise ValueError(
                    "an empty dataset cannot report duplicate timestamps.",
                )
            if self.ohlc_invalid_count != 0 or self.volume_invalid_count != 0:
                raise ValueError(
                    "an empty dataset cannot report invalid OHLC/volume rows.",
                )

    @property
    def exists(self) -> bool:
        """A persisted dataset exists (with or without stored candles)."""

        return self.dataset_exists or (
            self.coverage is not None
            and self.coverage.status
            in (
                DatasetCoverageStatus.EMPTY,
                DatasetCoverageStatus.PARTIAL,
                DatasetCoverageStatus.COMPLETE,
            )
        )

    @property
    def status(self) -> DatasetCoverageStatus:
        """
        The coverage classification for counting / display.

        ``UNAVAILABLE`` when no coverage measurement exists (no store
        configured) — a dataset that could not be classified is never
        silently coerced to MISSING.
        """

        if self.coverage is None:
            return DatasetCoverageStatus.UNAVAILABLE
        return self.coverage.status

    @property
    def coverage_label(self) -> str:
        """Coverage status rendered as its vocabulary value."""

        return self.status.value

    @property
    def has_usable_candles(self) -> bool:
        return self.candle_count > 0

    @property
    def integrity_failed(self) -> bool:
        """
        True when persisted data carries a concrete data-quality issue.

        Only FAIL checks count (N_A on a missing dataset is not an
        integrity failure — a missing dataset is a completeness issue,
        surfaced via ``status``).
        """

        failures = (
            self.chronological,
            self.duplicates,
            self.timezone_aware,
            self.ohlc,
            self.volume,
        )
        return any(check is AuditCheckStatus.FAIL for check in failures)


@dataclass(frozen=True, slots=True)
class CorpusAuditReport:
    """
    The complete read-only corpus integrity audit.

    ``audit_id = "audit-"+sha256[:16]`` of the canonical identity
    (instruments + timeframes + window + label + metadata) so identical
    inputs produce identical audit ids (no wall-clock, no randomness).
    """

    audit_id: str
    instruments: tuple[str, ...]
    timeframes: tuple[str, ...]
    start: datetime
    end: datetime
    results: tuple[DatasetAuditResult, ...]
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.audit_id.startswith("audit-"):
            raise ValueError("audit_id must use the 'audit-' prefix.")
        if not isinstance(self.metadata, tuple):
            object.__setattr__(
                self,
                "metadata",
                tuple(sorted(self.metadata)),
            )

    @property
    def dataset_count(self) -> int:
        return len(self.results)

    def _count_coverage(self, statuses: set[DatasetCoverageStatus]) -> int:
        return sum(1 for r in self.results if r.status in statuses)

    @property
    def complete_count(self) -> int:
        return self._count_coverage({DatasetCoverageStatus.COMPLETE})

    @property
    def partial_count(self) -> int:
        return self._count_coverage({DatasetCoverageStatus.PARTIAL})

    @property
    def missing_count(self) -> int:
        return self._count_coverage({DatasetCoverageStatus.MISSING})

    @property
    def empty_count(self) -> int:
        return self._count_coverage({DatasetCoverageStatus.EMPTY})

    @property
    def unavailable_count(self) -> int:
        return self._count_coverage({DatasetCoverageStatus.UNAVAILABLE})

    @property
    def integrity_failures(self) -> int:
        """Datasets whose persisted data failed a data-quality check."""

        return sum(1 for r in self.results if r.integrity_failed)

    @property
    def is_pass(self) -> bool:
        """
        The corpus audit verdict.

        PASS requires every audited plan row to be COMPLETE in coverage
        AND every persisted dataset to pass every data-quality check.
        A corpus that is complete in coverage but carries a data-quality
        anomaly is REVIEW REQUIRED — never silently passed.
        """

        return (
            self.complete_count == self.dataset_count
            and self.integrity_failures == 0
            and self.dataset_count > 0
        )

    @property
    def verdict(self) -> str:
        return "PASS" if self.is_pass else "REVIEW REQUIRED"


__all__ = [
    "AuditCheckStatus",
    "CorpusAuditReport",
    "DatasetAuditResult",
]