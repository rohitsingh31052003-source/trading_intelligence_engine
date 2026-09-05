"""
Continuous market scanner orchestration (Checkpoint 19.3).

This module is the scanner-orchestration half of Checkpoint 19.3. It
consumes the FROZEN 19.1 validated NIFTY Top 200 universe and the FROZEN
19.2 canonical intraday market-data layer
(:class:`dashboard.intraday_coverage.IntradayCoverageEngine`) and turns
them into repeated, deterministic SCAN CYCLES that answer:

    "At each scan cycle, what is the current market-data state of the
     NIFTY Top 200 universe?"

The scanner implements NO trading intelligence:

* NO setup detection, NO technical signals, NO scoring, NO ranking,
  NO trade plans, NO entry/stop/target, NO setup lifecycle, NO alerts,
  NO multi-timeframe confluence, NO broker execution.
* A scan cycle result describes MARKET STATE / DATA AVAILABILITY only.

Design (the smallest architecture compatible with the project):

* :class:`ContinuousScannerEngine` is STATELESS and PURE: it runs one
  deterministic scan cycle via the EXISTING 19.2
  :meth:`IntradayCoverageEngine.assess_universe`
  (failure isolation + explicit per-symbol statuses already live there)
  and projects the coverage report into a scanner-level
  :class:`engine.models.continuous_scan.MarketScanCycleResult`.
  The provider / universe / time are dependency-injected; nothing is
  duplicated from 19.1 / 19.2.

* :class:`ContinuousScanner` is the SCHEDULING LOOP: start() / stop() /
  state() / run() with an injected clock + waiter so tests never sleep
  in real time. It enforces the NO-OVERLAPPING-SCANS policy: a cycle is
  skipped (SKIPPED result, honest bookkeeping) when the previous cycle
  is still running at the next scheduled start.

* The scan interval is configurable (:class:`ContinuousScanConfig`).
  The default of 900s (15 minutes) is documented and intentionally
  conservative — it respects provider rate limits rather than polling
  aggressively.

Session awareness is REUSED from 19.2 (market_session module) and
attached to every cycle result; the scanner does NOT redesign market-
session handling and does NOT add exchange-holiday infrastructure.
"""

from __future__ import annotations

import threading
import time as _time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Sequence

from dashboard.intraday_coverage import (
    DEFAULT_INTRADAY_TIMEFRAME,
    IntradayCoverageEngine,
)
from engine.config.universe_boundary import (
    DEFAULT_NIFTY200_UNIVERSE,
    UniverseDefinition,
)
from engine.data.market_session import (
    market_session_state,
    seconds_until_next_open,
)
from engine.models.continuous_scan import (
    ContinuousScanConfig,
    MarketScanCycleResult,
    MarketScanStatus,
    PerSymbolScanResult,
    ScanCycleMetadata,
    ScanOnceRequest,
    ScanSourceKind,
    ScannerState,
    cycle_id as build_cycle_id,
)
from engine.models.intraday_coverage import (
    IntradayCoverageReport,
    IntradayCoverageStatus,
)


def _canonical_name(name: str) -> str:
    if not isinstance(name, str):
        raise TypeError(f"instrument name must be a str, got {type(name).__name__}")
    canonical = name.strip().upper()
    if not canonical:
        raise ValueError("instrument name cannot be empty")
    return canonical


def _resolve_universe_names(
    universe: UniverseDefinition | Sequence[str] | None,
) -> tuple[str, ...]:
    """Canonical, sorted, de-duplicated instrument tuple from any input."""

    if universe is None:
        universe = DEFAULT_NIFTY200_UNIVERSE
    if isinstance(universe, UniverseDefinition):
        names = list(universe.symbols)
    else:
        names = [_canonical_name(n) for n in universe]
    return tuple(sorted(set(names)))


def _resolve_timeframe(timeframe: str) -> str:
    from engine.data.historical_times import canonical_timeframe

    canonical = canonical_timeframe(timeframe)
    if canonical is None or canonical == "1D":
        raise ValueError(
            f"timeframe must be a canonical INTRADAY timeframe "
            f"(got {timeframe!r}).",
        )
    return canonical


def _require_aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime.")
    if value.tzinfo is None:
        raise ValueError(
            f"{name} must be timezone-aware (naive datetimes are never "
            "silently accepted).",
        )
    return value


# ------------------------------------------------------------
# PRICE SOURCE (deterministic, optional)
# ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PriceSource:
    """
    Optional deterministic latest-close lookup for per-symbol results.

    The 19.2 coverage layer classifies candle series but does NOT
    expose the latest close price (its aggregate model intentionally
    carries counts/statuses, not candles). A caller may supply a price
    source so a scan cycle can record the latest CLOSED price as market
    STATE. The scanner never requires one — when the source is absent,
    ``PerSymbolScanResult.latest_price`` stays ``None`` (unavailable is
    never fabricated).
    """

    lookup: Callable[[str, str], float | None]

    def latest_closed_price(
        self, instrument: str, timeframe: str,
    ) -> float | None:
        try:
            value = self.lookup(instrument, timeframe)
        except Exception:  # pragma: no cover - defensive
            return None
        if value is None:
            return None
        try:
            value = float(value)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            return None
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return value


def price_source_from_provider(provider: Any, lookback_bars: int = 300) -> PriceSource | None:
    """
    Build a deterministic price source from a provider exposing
    ``fetch`` returning an :class:`dashboard.data_provider.InstrumentSeries`
    with ``setup_candles``. Returns ``None`` when the provider has no
    fetch — a missing price is reported as unavailable, never invented.
    """

    fetch = getattr(provider, "fetch", None)
    if fetch is None:
        return None

    def _lookup(instrument: str, timeframe: str) -> float | None:
        series = fetch(instrument, timeframe, lookback_bars=lookback_bars)
        candles = getattr(series, "setup_candles", ()) or ()
        if not candles:
            return None
        last = candles[-1]
        close = getattr(last, "close", None)
        return float(close) if close is not None else None

    return PriceSource(lookup=_lookup)


# ------------------------------------------------------------
# SINGLE CYCLE (STATELESS ENGINE)
# ------------------------------------------------------------


class ContinuousScannerEngine:
    """
    Stateless, deterministic scan-cycle engine (Checkpoint 19.3).

    ``run_cycle`` executes ONE scan cycle through the EXISTING 19.2
    intraday coverage layer and projects the result into a scanner-level
    :class:`MarketScanCycleResult`. It implements NO trading intelligence.
    """

    def __init__(
        self,
        coverage_engine: IntradayCoverageEngine | None = None,
        price_source: PriceSource | None = None,
        timeframe: str = DEFAULT_INTRADAY_TIMEFRAME,
    ) -> None:
        self.coverage = coverage_engine or IntradayCoverageEngine()
        self.price_source = price_source
        self.timeframe = _resolve_timeframe(timeframe)

    @classmethod
    def build(
        cls,
        provider_name: str = "fixture",
        *,
        timeframe: str = DEFAULT_INTRADAY_TIMEFRAME,
        price_source: PriceSource | None = None,
    ) -> "ContinuousScannerEngine":
        """
        Stateless factory over the EXISTING ``make_provider`` + 19.2
        engine. ``"fixture"`` (default) is deterministic and offline;
        ``"yahoo"`` is the OPT-IN live provider (never silently
        substituted). No broker provider is ever constructed.
        """

        return cls(
            coverage_engine=IntradayCoverageEngine.build(
                provider_name=provider_name,
                timeframe=timeframe,
            ),
            price_source=price_source,
            timeframe=timeframe,
        )

    def run_cycle(
        self,
        request: ScanOnceRequest | None = None,
        *,
        universe: UniverseDefinition | Sequence[str] | None = None,
        timeframe: str | None = None,
        reference_now: datetime | None = None,
        source: ScanSourceKind = ScanSourceKind.AUTO,
        manual_run_id: str = "",
    ) -> MarketScanCycleResult:
        """
        Execute exactly ONE deterministic scan cycle.

        The cycle consumes the EXISTING 19.2 intraday coverage layer.
        Failures inside the cycle are captured in the result (never
        raised); a single symbol failure never terminates the cycle.

        ``reference_now`` is explicit for determinism. When ``None`` the
        wall-clock UTC instant is used (LIVE operator mode only).
        """

        req = request or ScanOnceRequest()
        tf = _resolve_timeframe(timeframe or req.timeframe or self.timeframe)
        names = _resolve_universe_names(
            universe if universe is not None else req.universe,
        )
        now = _require_aware(
            reference_now
            if reference_now is not None
            else (
                req.reference_now
                if req.reference_now is not None
                else datetime.now(UTC)
            ),
            "reference_now",
        )
        manual = manual_run_id or req.manual_run_id or ""

        if not names:
            return MarketScanCycleResult(
                cycle_id=build_cycle_id(tf, names, now, source, manual),
                reference_now=now,
                timeframe=tf,
                universe=(),
                results=(),
                status=MarketScanStatus.UNAVAILABLE,
                metadata=ScanCycleMetadata(
                    cycle_started_at=now,
                    cycle_ended_at=now,
                    requested_universe_size=0,
                    attempted_instrument_count=0,
                    successful_instrument_count=0,
                    unavailable_instrument_count=0,
                    failed_instrument_count=0,
                    duration_seconds=0.0,
                ),
                source=source,
                manual_run_id=manual,
                error="empty universe — nothing to scan",
                market_session=market_session_state(now),
                seconds_until_next_open=(
                    seconds_until_next_open(now).total_seconds()
                    if seconds_until_next_open(now) is not None
                    else None
                ),
            )

        try:
            report: IntradayCoverageReport = self.coverage.assess_universe(
                names, tf, reference_now=now,
            )
        except Exception as exc:  # pragma: no cover - defensive
            error = f"{type(exc).__name__}: {exc}".strip()
            return MarketScanCycleResult(
                cycle_id=build_cycle_id(tf, names, now, source, manual),
                reference_now=now,
                timeframe=tf,
                universe=names,
                results=(),
                status=MarketScanStatus.COMPLETE_FAILURE,
                metadata=ScanCycleMetadata(
                    cycle_started_at=now,
                    cycle_ended_at=now,
                    requested_universe_size=len(names),
                    attempted_instrument_count=0,
                    successful_instrument_count=0,
                    unavailable_instrument_count=0,
                    failed_instrument_count=0,
                    duration_seconds=0.0,
                ),
                source=source,
                manual_run_id=manual,
                error=error,
                market_session=market_session_state(now),
                seconds_until_next_open=(
                    seconds_until_next_open(now).total_seconds()
                    if seconds_until_next_open(now) is not None
                    else None
                ),
            )

        # ---- Project the coverage report into scanner results ----
        report_by_symbol = {r.instrument: r for r in report.results}
        per_symbol: list[PerSymbolScanResult] = []
        successful = 0
        unavailable = 0
        failed = 0
        needs_attention = 0
        for name in names:
            coverage_result = report_by_symbol.get(name)
            price: float | None = None
            if coverage_result is not None and self.price_source is not None:
                price = self.price_source.latest_closed_price(name, tf)
            if coverage_result is None:
                symbol = PerSymbolScanResult(instrument=name, coverage=None)
                unavailable += 1
                needs_attention += 1
            else:
                symbol = PerSymbolScanResult(
                    instrument=name, coverage=coverage_result,
                )
                if coverage_result.status.is_valid_data:
                    successful += 1
                else:
                    unavailable += 1
                if coverage_result.status in (
                    IntradayCoverageStatus.PROVIDER_ERROR,
                    IntradayCoverageStatus.INVALID_RESPONSE,
                    IntradayCoverageStatus.TEMPORARILY_UNAVAILABLE,
                ):
                    failed += 1
                if coverage_result.status.needs_attention:
                    needs_attention += 1
            if price is not None:
                object.__setattr__(symbol, "latest_price", price)
            per_symbol.append(symbol)

        # Deterministic ordering: canonical universe order (sorted).
        per_symbol.sort(key=lambda s: s.instrument)
        results = tuple(per_symbol)

        status = (
            MarketScanStatus.FULL_SUCCESS
            if needs_attention == 0
            else MarketScanStatus.PARTIAL_SUCCESS
        )

        metadata = ScanCycleMetadata(
            cycle_started_at=now,
            cycle_ended_at=now,
            requested_universe_size=len(names),
            attempted_instrument_count=len(names),
            successful_instrument_count=successful,
            unavailable_instrument_count=unavailable,
            failed_instrument_count=failed,
            duration_seconds=0.0,
        )

        return MarketScanCycleResult(
            cycle_id=build_cycle_id(tf, names, now, source, manual),
            reference_now=now,
            timeframe=tf,
            universe=names,
            results=results,
            status=status,
            metadata=metadata,
            source=source,
            manual_run_id=manual,
            market_session=market_session_state(now),
            seconds_until_next_open=(
                seconds_until_next_open(now).total_seconds()
                if seconds_until_next_open(now) is not None
                else None
            ),
        )


# ------------------------------------------------------------
# SCHEDULING LOOP (CONTINUOUS SCANNER)
# ------------------------------------------------------------


class ContinuousScanner:
    """
    Continuous scanning loop (Checkpoint 19.3).

    ``start()`` launches a deterministic worker thread; ``stop()``
    requests a clean stop (the in-flight cycle, if any, is allowed to
    finish); ``run(cycles=...)`` is the blocking in-process loop used
    by tests and the CLI; ``state()`` exposes the lifecycle state.

    CLOCK + WAITER are dependency-injected (never hard-coded real
    sleeps) so tests are fully deterministic. The default waiter is the
    real :func:`time.sleep` (used only by CLI / operator runs).

    NO-OVERLAPPING-SCANS POLICY: a new cycle is never started while a
    previous cycle is still running. On the schedule the loop detects
    that the previous cycle has not finished, produces a SKIPPED cycle
    result (honest bookkeeping) and re-computes the schedule so the
    next attempt starts at the configured interval after the previous
    cycle finished.
    """

    def __init__(
        self,
        engine: ContinuousScannerEngine | None = None,
        config: ContinuousScanConfig | None = None,
        universe: UniverseDefinition | Sequence[str] | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        waiter: Callable[[float], None] | None = None,
    ) -> None:
        self.engine = engine or ContinuousScannerEngine()
        self.config = config or ContinuousScanConfig()
        self.universe_names = _resolve_universe_names(universe)
        self._clock = clock
        self._waiter = waiter or _time.sleep
        self._lock = threading.Lock()
        self._state = ScannerState.STOPPED
        self._stop_requested = False
        self._busy = False
        self._cycles: list[MarketScanCycleResult] = []
        self._thread: threading.Thread | None = None
        self._last_cycle_end: datetime | None = None
        self._manual_run_counter = 0

    # ------------------------------------------------------------
    # LIFECYCLE
    # ------------------------------------------------------------

    def state(self) -> ScannerState:
        """Current lifecycle state (thread-safe)."""

        with self._lock:
            return self._state

    def start(self) -> None:
        """Start the continuous scanning loop in a background worker.

        Idempotent: starting an already-running scanner is a no-op.
        Calling start() again after a clean stop resets the schedule
        (the first cycle of the new run begins immediately).
        """

        with self._lock:
            if self._state in (
                ScannerState.RUNNING,
                ScannerState.STOPPING,
                ScannerState.SCANNING,
                ScannerState.WAITING,
            ):
                return
            self._stop_requested = False
            self._cycles = []
            self._last_cycle_end = None
            self._state = ScannerState.RUNNING
            self._thread = threading.Thread(
                target=self._loop,
                name="continuous-scanner",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        """Request a clean stop. The in-flight cycle (if any) finishes;
        no new cycle is started. Idempotent."""

        with self._lock:
            if self._state is ScannerState.STOPPED:
                return
            self._stop_requested = True
            self._state = ScannerState.STOPPING

    def join(self, timeout: float | None = None) -> None:
        """Block until the worker thread exits (if started)."""

        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def run(self, cycles: int = 1) -> list[MarketScanCycleResult]:
        """
        Blocking, deterministic in-process loop (CLI / tests).

        Runs up to ``cycles`` AUTO cycles (or ``config.max_cycles`` when
        set), enforcing the no-overlap policy. Returns the completed
        cycle results. Does NOT sleep in real time when a deterministic
        ``waiter`` was injected. Each cycle uses an explicit reference
        time: the injected clock when present, else ``datetime.now(UTC)``.
        """

        with self._lock:
            self._stop_requested = False
            self._state = ScannerState.RUNNING
        try:
            self._loop(cycles=cycles)
        except Exception:
            with self._lock:
                self._state = ScannerState.FAILED
            raise
        finally:
            with self._lock:
                if self._state is not ScannerState.FAILED:
                    self._state = ScannerState.STOPPED
        with self._lock:
            return list(self._cycles)

    # ------------------------------------------------------------
    # CYCLE EXECUTION
    # ------------------------------------------------------------

    def scan_once(
        self,
        *,
        reference_now: datetime | None = None,
        manual_run_id: str | None = None,
    ) -> MarketScanCycleResult:
        """
        Execute ONE cycle immediately (ON-DEMAND / manual).

        Honors the no-overlap policy: when a cycle is currently running,
        returns a SKIPPED result instead of starting a second scan.
        ``reference_now`` defaults to the injected clock or wall-clock.
        """

        with self._lock:
            if self._busy:
                now = self._now()
                names = self.universe_names or _resolve_universe_names(None)
                run_id = manual_run_id or ""
                return self._skipped_result(
                    now, names, ScanSourceKind.MANUAL, run_id,
                    "previous scan cycle still running — this manual "
                    "cycle was skipped (no-overlap policy)",
                )
            self._busy = True
        try:
            if manual_run_id is None:
                self._manual_run_counter += 1
                run_id = f"m{self._manual_run_counter:04d}"
            else:
                run_id = manual_run_id
            now = (
                self._now()
                if reference_now is None
                else _require_aware(reference_now, "reference_now")
            )
            result = self.engine.run_cycle(
                universe=self.universe_names,
                timeframe=self.engine.timeframe,
                reference_now=now,
                source=ScanSourceKind.MANUAL,
                manual_run_id=run_id,
            )
            with self._lock:
                self._cycles.append(result)
                self._last_cycle_end = now
            return result
        finally:
            with self._lock:
                self._busy = False

    def _now(self) -> datetime:
        if self._clock is not None:
            return _require_aware(self._clock(), "clock result")
        return datetime.now(UTC)

    def _loop(self, cycles: int | None = None) -> None:
        """Worker / blocking loop body (internal)."""

        self._loop_body(cycles=cycles)
        with self._lock:
            if self._state is not ScannerState.FAILED:
                self._state = ScannerState.STOPPED
                self._thread = None

    def _loop_body(self, cycles: int | None = None) -> None:

        budget = cycles
        if budget is None:
            cfg = self.config
            if cfg.max_cycles is not None:
                budget = cfg.max_cycles
            elif cfg.run_forever:
                budget = None
            else:
                budget = 1

        interval = self.config.scan_interval_seconds
        while True:
            with self._lock:
                stop_requested = self._stop_requested
            if stop_requested:
                break
            if budget is not None and budget <= 0:
                break
            with self._lock:
                if self._busy:
                    now = self._now()
                    names = self.universe_names or _resolve_universe_names(None)
                    self._cycles.append(
                        self._skipped_result(
                            now, names, ScanSourceKind.AUTO, "",
                            "previous scan cycle still running — this "
                            "scheduled cycle was skipped (no-overlap policy)",
                        ),
                    )
                    self._last_cycle_end = now
                    continue
                self._busy = True
                self._state = ScannerState.SCANNING

            started = self._now()
            try:
                result = self.engine.run_cycle(
                    universe=self.universe_names,
                    timeframe=self.engine.timeframe,
                    reference_now=started,
                    source=ScanSourceKind.AUTO,
                    manual_run_id="",
                )
            finally:
                self._busy = False
                with self._lock:
                    self._state = ScannerState.RUNNING
            with self._lock:
                self._cycles.append(result)
                self._last_cycle_end = self._now()

            if budget is not None:
                budget -= 1
            if budget is not None and budget <= 0:
                break

            # Schedule the next cycle at least one full interval after
            # the previous cycle END (back-pressure on overrun).
            next_start = self._last_cycle_end + timedelta(seconds=interval)
            sleep_for = (next_start - self._now()).total_seconds()
            if sleep_for > 0:
                with self._lock:
                    self._state = ScannerState.WAITING
                self._interruptible_sleep(sleep_for)
            # else: overran — start immediately (no-overlap guards stacking).

    def _interruptible_sleep(self, seconds: float) -> None:
        """Sleep in small slices so a stop request is honored promptly."""

        deadline = self._now() + timedelta(seconds=seconds)
        while True:
            with self._lock:
                if self._stop_requested:
                    return
            remaining = (deadline - self._now()).total_seconds()
            if remaining <= 0:
                return
            self._waiter(min(remaining, 0.25))

    def _skipped_result(
        self,
        now: datetime,
        names: tuple[str, ...],
        source: ScanSourceKind,
        manual_run_id: str,
        error: str,
    ) -> MarketScanCycleResult:
        return MarketScanCycleResult(
            cycle_id=build_cycle_id(
                self.engine.timeframe, names, now, source, manual_run_id,
            ),
            reference_now=now,
            timeframe=self.engine.timeframe,
            universe=names,
            results=(),
            status=MarketScanStatus.SKIPPED,
            metadata=ScanCycleMetadata(
                cycle_started_at=now,
                cycle_ended_at=None,
                requested_universe_size=len(names),
                attempted_instrument_count=0,
                successful_instrument_count=0,
                unavailable_instrument_count=0,
                failed_instrument_count=0,
                duration_seconds=None,
            ),
            source=source,
            manual_run_id=manual_run_id,
            error=error,
            market_session=market_session_state(now),
            seconds_until_next_open=(
                seconds_until_next_open(now).total_seconds()
                if seconds_until_next_open(now) is not None
                else None
            ),
        )

    # ------------------------------------------------------------
    # OBSERVABILITY
    # ------------------------------------------------------------

    @property
    def results(self) -> tuple[MarketScanCycleResult, ...]:
        """All cycle results so far (deterministic order)."""

        with self._lock:
            return tuple(self._cycles)

    @property
    def last_result(self) -> MarketScanCycleResult | None:
        with self._lock:
            return self._cycles[-1] if self._cycles else None

    @property
    def cycle_count(self) -> int:
        with self._lock:
            return len(self._cycles)


__all__ = [
    "ContinuousScanner",
    "ContinuousScannerEngine",
    "PriceSource",
    "price_source_from_provider",
]