"""
Continuous market-scan models (Checkpoint 19.3).

These models describe the DETERMINISTIC continuous market scanning
layer that answers, at each scan cycle, "what is the current
market-data / data-availability state of every constituent of the
NIFTY Top 200 universe?".

They are DATA / PRODUCT-STATE models ONLY:

* They carry NO trading, scoring, prediction, decision, ranking,
  setup-detection, multi-timeframe-intelligence, lifecycle or
  execution logic.
* Every universe constituent has an EXPLICIT per-symbol result
  (never silently dropped).
* The per-symbol vocabulary REUSES the Checkpoint 19.2
  :class:`engine.models.intraday_coverage.IntradayCoverageStatus`
  taxonomy rather than inventing a parallel scanner status set.
* A scan cycle result describes MARKET STATE / DATA AVAILABILITY —
  it NEVER says whether a trade should be taken.

Design rules (match the rest of the model layer):

* Frozen + slots dataclasses.
* Optional fields use ``None`` so "unobserved" / "unavailable" is
  never silently a real value.
* ``__post_init__`` performs structural validation only; no business
  logic lives here.
* No wall-clock dependence: timestamps are explicit, caller-supplied,
  timezone-aware values.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from engine.models.intraday_coverage import (
    IntradayCoverageCounts,
    IntradayCoverageStatus,
    IntradayInstrumentCoverage,
)


def _sha256_prefix(payload: str, prefix: str) -> str:
    """Deterministic ``"<prefix>" + sha256[:16]`` over a canonical string."""

    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{prefix}{digest[:16]}"


def _canonical_name(name: str) -> str:
    """Canonicalize + validate a single instrument name."""

    if not isinstance(name, str):
        raise TypeError(f"instrument name must be a str, got {type(name).__name__}")
    canonical = name.strip().upper()
    if not canonical:
        raise ValueError("instrument name cannot be empty")
    return canonical


def _require_aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime.")
    if value.tzinfo is None:
        raise ValueError(
            f"{name} must be timezone-aware (naive datetimes are never "
            "silently accepted).",
        )


class MarketScanStatus(Enum):
    """
    Deterministic classification of ONE scan cycle's overall outcome.

    The vocabulary answers "did the cycle observe the universe?" — it
    is a DATA-AVAILABILITY classification, NEVER a market or trade
    verdict.

    FULL_SUCCESS
        Every requested instrument produced a per-symbol result and
        NO per-symbol result is marked as needing attention (all data
        was current/valid — provider error, unsupported, empty,
        NO_DATA, stale and invalid are all zero).

    PARTIAL_SUCCESS
        Every requested instrument produced a per-symbol result, but
        at least one result requires attention (a failed / unsupported
        / stale / empty symbol was reported honestly rather than
        silently discarded). PARTIAL SUCCESS IS NEVER CONFUSED WITH
        FULL SUCCESS.

    COMPLETE_FAILURE
        NO per-symbol result could be produced at all (e.g. the whole
        universe assessment raised before producing any per-symbol
        result, or zero instruments were attempted). A cycle that
        produced at least one per-symbol result is never COMPLETE_FAILURE.

    UNAVAILABLE
        The cycle could not run because the underlying data layer was
        unavailable (no provider configured / provider not ready) or
        the universe was empty and nothing meaningful could be scanned.

    SKIPPED
        The cycle was NOT executed because it overlaps a still-running
        previous cycle (the chosen no-overlap policy). It is a distinct,
        honest bookkeeping state — a skipped cycle is never reported as
        a success or failure.
    """

    FULL_SUCCESS = "FULL_SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    COMPLETE_FAILURE = "COMPLETE_FAILURE"
    UNAVAILABLE = "UNAVAILABLE"
    SKIPPED = "SKIPPED"

    @property
    def is_observational_success(self) -> bool:
        """True for FULL_SUCCESS / PARTIAL_SUCCESS (a cycle produced
        complete per-symbol coverage)."""

        return self in (
            MarketScanStatus.FULL_SUCCESS,
            MarketScanStatus.PARTIAL_SUCCESS,
        )


class ScannerState(Enum):
    """
    Lifecycle state of a continuous scanner instance.

    This is the SCANNER lifecycle (Checkpoint 19.3) — NOT the trade
    setup lifecycle (Checkpoint 19.6). It answers "is the scanning
    loop running?" — it never describes market setups.

    STOPPED
        The scanner has not been started, or finished / was stopped.
        No worker is active and no state is retained.

    RUNNING
        The scanner has been started and is looping. It may currently
        be WAITING between cycles or executing a cycle.

    SCANNING
        A cycle is currently being executed (subset of RUNNING).

    WAITING
        The scanner is RUNNING and sleeping until the next scheduled
        cycle start (subset of RUNNING).

    STOPPING
        A stop was requested; the current cycle (if any) is allowed to
        finish and no new cycle will be started. Determines a clean
        stop.

    FAILED
        The scan loop terminated because an unexpected, non-cycle-level
        error escaped the cycle wrapper (defensive — cycle-level
        failures are captured inside the per-cycle result instead).
    """

    STOPPED = "STOPPED"
    RUNNING = "RUNNING"
    SCANNING = "SCANNING"
    WAITING = "WAITING"
    STOPPING = "STOPPING"
    FAILED = "FAILED"


class ScanSourceKind(Enum):
    """Source of an explicit scan-cycle trigger (deterministic).

    AUTO
        A scheduled / automatic cycle (triggered by the continuous
        loop's interval timer).

    MANUAL
        An explicit on-demand run-one-cycle request (CLI / operator /
        tests). Carries an optional deterministic ``manual_run_id``.
    """

    AUTO = "AUTO"
    MANUAL = "MANUAL"


def _canonical_scalar(value: Any) -> str:
    """Stable canonical string of a scalar involved in an identity."""

    if value is None:
        return "~"
    if isinstance(value, bool):
        return "T" if value else "F"
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return str(value)


def _cycle_identity(
    timeframe: str,
    universe_symbols: tuple[str, ...],
    reference_now: datetime,
    source: ScanSourceKind,
    manual_run_id: str,
) -> str:
    """Deterministic cycle identity over canonical inputs.

    The identity deliberately EXCLUDES cycle start/end wall-clock
    bookkeeping (those are per-cycle session metadata, not identity):
    the same scheduled observation instant always yields the same cycle
    id, which keeps repeated / idempotent observation reproducible.
    """

    parts = [
        _canonical_scalar(timeframe),
        "|".join(sorted(universe_symbols)),
        _canonical_scalar(reference_now),
        _canonical_scalar(source),
        _canonical_scalar(manual_run_id),
    ]
    return _sha256_prefix("|".join(parts), "")[:16]


@dataclass(frozen=True, slots=True)
class PerSymbolScanResult:
    """
    Explicit per-symbol scan-cycle result for ONE universe constituent.

    Every requested constituent appears EXACTLY ONCE in a cycle's
    per-symbol results — a symbol can never be silently dropped. The
    result is a thin projection AROUND the Checkpoint 19.2
    :class:`IntradayInstrumentCoverage` object (retained BY
    REFERENCE — no data is duplicated or recomputed) plus cycle
    bookkeeping.

    Attributes:

    instrument
        Canonical instrument name.

    coverage
        The reused 19.2 per-instrument coverage classification
        (BY REFERENCE). ``None`` only when the coverage layer produced
        no result for this instrument (defensive).

    status
        Short per-symbol status — the 19.2
        :class:`IntradayCoverageStatus` when ``coverage`` is present,
        else ``UNSUPPORTED_INSTRUMENT`` (honest bookkeeping for a
        symbol the coverage layer did not return).

    available
        ``True`` when the symbol carries usable current market data
        (19.2 ``is_valid_data`` semantics: VALID / VALID_WITH_GAPS /
        STALE). ``STALE`` counts as "available data with a quality
        caveat" — it is NEVER silently promoted to fresh.

    fresh
        ``True`` ONLY when the 19.2 status is exactly VALID or
        VALID_WITH_GAPS (current, session-aware freshness). STALE
        data is available but NOT fresh.

    latest_price
        Latest CLOSED candle close (the most recent observation for
        this symbol). ``None`` when no completed candle exists. This is
        market STATE, not a signal.

    latest_bar_timestamp
        Timestamp of the latest completed candle (``None`` when none).

    data_age_seconds
        Age of the latest completed bar at the cycle reference time
        (from the 19.2 result), or ``None``.

    needs_attention
        Mirrors the 19.2 classification (a symbol that would block /
        limit scanner coverage). Used for the cycle-level
        FULL vs PARTIAL success distinction.
    """

    instrument: str
    coverage: IntradayInstrumentCoverage | None
    status: IntradayCoverageStatus = IntradayCoverageStatus.UNSUPPORTED_INSTRUMENT
    available: bool = False
    fresh: bool = False
    latest_price: float | None = None
    latest_bar_timestamp: datetime | None = None
    data_age_seconds: float | None = None
    needs_attention: bool = False

    def __post_init__(self) -> None:
        canon = _canonical_name(self.instrument)
        object.__setattr__(self, "instrument", canon)
        if self.coverage is None:
            object.__setattr__(
                self,
                "status",
                IntradayCoverageStatus.UNSUPPORTED_INSTRUMENT,
            )
            object.__setattr__(self, "available", False)
            object.__setattr__(self, "fresh", False)
            object.__setattr__(self, "needs_attention", True)
            return
        object.__setattr__(self, "status", self.coverage.status)
        object.__setattr__(
            self,
            "available",
            bool(self.coverage.status.is_valid_data),
        )
        object.__setattr__(
            self,
            "fresh",
            self.coverage.status
            in (
                IntradayCoverageStatus.VALID,
                IntradayCoverageStatus.VALID_WITH_GAPS,
            ),
        )
        object.__setattr__(
            self,
            "needs_attention",
            bool(self.coverage.status.needs_attention),
        )
        object.__setattr__(
            self,
            "latest_bar_timestamp",
            self.coverage.last_timestamp,
        )
        object.__setattr__(
            self,
            "data_age_seconds",
            self.coverage.data_age_seconds,
        )


@dataclass(frozen=True, slots=True)
class ScanCycleMetadata:
    """
    Cycle bookkeeping metadata (Checkpoint 19.3).

    Attributes:

    cycle_started_at
        Wall-clock instant the cycle began executing (explicit,
        timezone-aware).

    cycle_ended_at
        Wall-clock instant the cycle finished executing, or ``None``
        for SKIPPED / not-executed cycles.

    requested_universe_size
        Number of instruments REQUESTED (from the universe definition:
        stock constituents + benchmark where included).

    attempted_instrument_count
        Number of instruments the data layer actually attempted (==
        requested for a healthy cycle; may be 0 for UNAVAILABLE /
        COMPLETE_FAILURE cycles).

    successful_instrument_count
        Number of per-symbol results that produced usable current data
        (19.2 with_valid_data semantics: VALID / VALID_WITH_GAPS /
        STALE).

    unavailable_instrument_count
        Number of per-symbol results with NO usable data (empty /
        unsupported / provider-error / invalid / not-ready / no-data).

    failed_instrument_count
        Number of per-symbol results explicitly classified as a
        failure (provider error / invalid response / not-ready).
        Stale / unsupported / empty are grouped under
        ``unavailable_instrument_count``.

    duration_seconds
        Cycle wall-clock duration in seconds: ``cycle_ended_at -
        cycle_started_at`` when both are present, else ``None``
        (SKIPPED / not-executed / start-only).
    """

    cycle_started_at: datetime
    cycle_ended_at: datetime | None = None
    requested_universe_size: int = 0
    attempted_instrument_count: int = 0
    successful_instrument_count: int = 0
    unavailable_instrument_count: int = 0
    failed_instrument_count: int = 0
    duration_seconds: float | None = None

    def __post_init__(self) -> None:
        _require_aware(self.cycle_started_at, "cycle_started_at")
        if self.cycle_ended_at is not None:
            _require_aware(self.cycle_ended_at, "cycle_ended_at")
        if (
            self.cycle_ended_at is not None
            and self.cycle_ended_at < self.cycle_started_at
        ):
            raise ValueError("cycle_ended_at must not be before cycle_started_at")
        for name in (
            "requested_universe_size",
            "attempted_instrument_count",
            "successful_instrument_count",
            "unavailable_instrument_count",
            "failed_instrument_count",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an int.")
        inv = (
            self.successful_instrument_count
            + self.unavailable_instrument_count
        )
        if self.attempted_instrument_count and inv != self.attempted_instrument_count:
            raise ValueError(
                "successful + unavailable must equal attempted",
            )
        if self.failed_instrument_count > self.unavailable_instrument_count:
            raise ValueError(
                "failed must be a subset of unavailable (failed <= unavailable)",
            )
        if self.cycle_ended_at is not None and self.duration_seconds is None:
            object.__setattr__(
                self,
                "duration_seconds",
                (self.cycle_ended_at - self.cycle_started_at).total_seconds(),
            )
        if self.duration_seconds is not None and self.duration_seconds < 0:
            raise ValueError("duration_seconds must be >= 0.")


@dataclass(frozen=True, slots=True)
class MarketScanCycleResult:
    """
    One complete, deterministic scan-cycle result (Checkpoint 19.3).

    A cycle result describes MARKET STATE / DATA AVAILABILITY at one
    observation instant for every requested universe constituent. It
    NEVER says whether a trade should be taken.

    Attributes:

    cycle_id
        Deterministic cycle identity (``"cycle-" + sha256[:16]`` over
        canonical universe + timeframe + reference instant + source).
        Repeated observation of the same instant yields the SAME id
        (idempotency / reproducibility).

    reference_now
        The deterministic observation instant (explicit; never
        wall-clock by default).

    timeframe
        Canonical intraday timeframe scanned.

    universe
        Instruments requested (sorted, de-duplicated canonical names).

    results
        Per-symbol results, deterministically ordered by instrument
        (canonical ascending). EXACTLY ONE result per requested
        constituent.

    status
        :class:`MarketScanStatus` cycle classification.

    metadata
        :class:`ScanCycleMetadata` bookkeeping.

    source / manual_run_id
        Source of the cycle trigger (AUTO / MANUAL) + optional
        manual-run identifier (``""`` for AUTO).

    error
        Cycle-level failure detail (``""`` when none; never a
        credential / sensitive value).

    market_session
        :class:`engine.data.market_session.MarketSessionState` at the
        reference instant, or ``None`` when not evaluated.

    seconds_until_next_open
        Deterministic estimate from 19.2, or ``None``.
    """

    cycle_id: str
    reference_now: datetime
    timeframe: str
    universe: tuple[str, ...] = field(default_factory=tuple)
    results: tuple[PerSymbolScanResult, ...] = field(default_factory=tuple)
    status: MarketScanStatus = MarketScanStatus.UNAVAILABLE
    metadata: ScanCycleMetadata | None = None
    source: ScanSourceKind = ScanSourceKind.AUTO
    manual_run_id: str = ""
    error: str = ""
    market_session: Any | None = None
    seconds_until_next_open: float | None = None

    def __post_init__(self) -> None:
        _require_aware(self.reference_now, "reference_now")
        if not self.cycle_id.strip():
            raise ValueError("cycle_id must not be empty")
        canon_universe = tuple(sorted(set(_canonical_name(n) for n in self.universe)))
        object.__setattr__(self, "universe", canon_universe)
        expected = len(canon_universe)
        if self.results and len(self.results) != expected:
            raise ValueError(
                f"expected exactly {expected} per-symbol results, "
                f"got {len(self.results)}",
            )
        if self.results:
            seen: set[str] = set()
            for result in self.results:
                if not isinstance(result, PerSymbolScanResult):
                    raise TypeError("results must be PerSymbolScanResult objects")
                if result.instrument in seen:
                    raise ValueError(
                        f"duplicate per-symbol result for {result.instrument!r}",
                    )
                seen.add(result.instrument)
            missing = set(canon_universe) - seen
            if missing:
                raise ValueError(
                    f"per-symbol results missing constituents: {sorted(missing)!r}",
                )
            extras = seen - set(canon_universe)
            if extras:
                raise ValueError(
                    f"per-symbol results contain non-universe symbols: "
                    f"{sorted(extras)!r}",
                )

    # ----------------------------------------------------------
    # CONVENIENCE VIEWS (read-only, deterministic)
    # ----------------------------------------------------------

    @property
    def instrument_count(self) -> int:
        """Number of per-symbol results (== requested universe size)."""
        return len(self.results)

    def result_for(self, instrument: str) -> PerSymbolScanResult | None:
        """Per-symbol lookup (``None`` when not present)."""
        canon = _canonical_name(instrument)
        for result in self.results:
            if result.instrument == canon:
                return result
        return None

    @property
    def successful_count(self) -> int:
        """Symbols with usable current market data (metadata count)."""
        meta = self.metadata
        return meta.successful_instrument_count if meta else 0

    @property
    def unavailable_count(self) -> int:
        meta = self.metadata
        return meta.unavailable_instrument_count if meta else 0

    @property
    def failed_count(self) -> int:
        meta = self.metadata
        return meta.failed_instrument_count if meta else 0

    def instrument_statuses(self) -> dict[str, IntradayCoverageStatus]:
        """Deterministic instrument -> per-symbol status map."""
        return {r.instrument: r.status for r in self.results}

    @property
    def is_full_success(self) -> bool:
        return self.status is MarketScanStatus.FULL_SUCCESS

    @property
    def is_partial_success(self) -> bool:
        return self.status is MarketScanStatus.PARTIAL_SUCCESS

    @property
    def is_complete_failure(self) -> bool:
        return self.status is MarketScanStatus.COMPLETE_FAILURE

    @property
    def is_observational(self) -> bool:
        return self.status in (
            MarketScanStatus.FULL_SUCCESS,
            MarketScanStatus.PARTIAL_SUCCESS,
        )


#: Cycle id prefix (documented, deterministic).
CYCLE_ID_PREFIX = "cycle-"


def cycle_id(
    timeframe: str,
    universe: tuple[str, ...],
    reference_now: datetime,
    source: ScanSourceKind = ScanSourceKind.AUTO,
    manual_run_id: str = "",
) -> str:
    """Deterministic cycle identity (documented)."""

    digest = _cycle_identity(
        timeframe, universe, reference_now, source, manual_run_id,
    )
    return f"{CYCLE_ID_PREFIX}{digest}"


@dataclass(frozen=True, slots=True)
class MarketScanCounts:
    """
    Determinitive aggregate counts over a cycle's per-symbol results.

    Reuses the 19.2 :class:`IntradayCoverageCounts` vocabulary
    verbatim (no parallel taxonomy). ``tested`` / ``with_valid_data``
    / ``needs_attention`` are the SAME derived properties as 19.2 and
    are provided here as a scanner-level convenience.
    """

    counts: IntradayCoverageCounts = field(default_factory=IntradayCoverageCounts)

    @property
    def tested(self) -> int:
        return self.counts.tested

    @property
    def with_valid_data(self) -> int:
        return self.counts.with_valid_data

    @property
    def needs_attention(self) -> int:
        return self.counts.needs_attention


@dataclass(frozen=True, slots=True)
class ScanOnceRequest:
    """
    Deterministic request for ONE scan cycle (run-once mode).

    Attributes:

    universe
        Validated universe (or sequence of canonical names).

    timeframe
        Canonical intraday timeframe (default ``"15m"``).

    reference_now
        Deterministic observation instant (explicit). ``None`` allows
        the engine to resolve wall-clock only in LIVE operator mode;
        the deterministic fixture path always supplies one.

    manual_run_id
        Optional operator-supplied run identifier (default ``""``).
    """

    universe: Any = None
    timeframe: str = "15m"
    reference_now: datetime | None = None
    manual_run_id: str = ""

    def __post_init__(self) -> None:
        if self.reference_now is not None:
            _require_aware(self.reference_now, "reference_now")
        if not isinstance(self.timeframe, str) or not self.timeframe.strip():
            raise ValueError("timeframe must be a non-empty str")


@dataclass(frozen=True, slots=True)
class ContinuousScanConfig:
    """
    Configuration for the continuous scanner (Checkpoint 19.3).

    Attributes:

    scan_interval_seconds
        Minimum wall-clock spacing between cycle STARTS (the
        scheduling interval). Positive; default 900 (15 minutes). A
        cycle that takes longer than the interval simply delays the
        next cycle (see ``skip_intervals_on_overrun``). The default is
        documented and intentionally conservative — the production
        operator may override via CLI / constructor.

    skip_intervals_on_overrun
        When True (default), if a cycle finishes AFTER the next
        scheduled start (i.e. the cycle overran), the loop waits one
        full ``scan_interval_seconds`` from the PREVIOUS cycle's END
        before starting the next cycle (back-pressure — the scanner
        never stack-runs missed intervals). When False, the loop waits
        until ``previous_start + interval`` even if that is in the
        past (immediate back-to-back start).

    max_cycles
        Optional hard cap of AUTO cycles the loop runs before
        stopping on its own (``None`` = run until stopped). Used for
        bounded / test runs; the loop stops cleanly once reached.

    run_forever
        If True, ``run()`` loops until ``stop()`` is called (start /
        stop model). If False, ``run()`` executes at most
        ``max_cycles`` (or one cycle when ``max_cycles`` is None) then
        stops. Default False.
    """

    scan_interval_seconds: float = 900.0
    skip_intervals_on_overrun: bool = True
    max_cycles: int | None = None
    run_forever: bool = False

    def __post_init__(self) -> None:
        interval = float(self.scan_interval_seconds)
        if interval <= 0:
            raise ValueError("scan_interval_seconds must be positive.")
        object.__setattr__(self, "scan_interval_seconds", interval)
        if self.max_cycles is not None:
            if isinstance(self.max_cycles, bool):
                raise TypeError("max_cycles must be an int, not bool.")
            if int(self.max_cycles) <= 0:
                raise ValueError("max_cycles must be positive when set.")
            object.__setattr__(self, "max_cycles", int(self.max_cycles))


__all__ = [
    "CYCLE_ID_PREFIX",
    "ContinuousScanConfig",
    "MarketScanCounts",
    "MarketScanCycleResult",
    "MarketScanStatus",
    "PerSymbolScanResult",
    "ScanCycleMetadata",
    "ScanOnceRequest",
    "ScanSourceKind",
    "ScannerState",
    "cycle_id",
]