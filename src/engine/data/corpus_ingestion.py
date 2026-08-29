"""
Safe, resumable historical CORPUS INGESTION runner (Checkpoint 3B, step 2).

The runner is an ORCHESTRATION LAYER ONLY. It derives the current
``missing`` work set from the EXISTING :class:`CorpusPreparationPlanner`
(whose coverage is computed from the EXISTING Phase 6B
:class:`HistoricalDataStore`), then executes each missing monthly chunk
through the EXISTING :class:`HistoricalMarketDataService` ingestion
pipeline (the same fetch -> validate -> gap-detect -> persist path used
by ``scripts/ingest_historical_data.py``). It implements NO trading /
scoring / prediction / decision / evidence logic, NO provider HTTP
client, and NO second persistence database — resumability is derived
entirely from the current persisted corpus.

Guarantees:

* SEQUENTIAL execution (one chunk at a time) — no concurrency, no API
  pressure beyond one request per missing chunk.
* PERSIST AFTER EVERY SUCCESSFUL CHUNK through the existing store; a
  later run derives the missing set from the planner/store and does NOT
  re-fetch chunks that are already covered.
* PER-CHUNK FAILURE ISOLATION — an empty response, an API/network error,
  a validation failure or an unexpected exception FAILS only that chunk
  and the runner continues (a failed chunk is never silently marked
  covered).
* CREDENTIAL PRECHECK — before ANY ingestion step the runner verifies
  the ``UPSTOX_ANALYTICS_TOKEN`` environment variable (the same
  mechanism the existing :class:`UpstoxHistoricalDataProvider` uses);
  when it is missing the run fails cleanly with zero API requests and
  zero persistence mutations. ``UPSTOX_ACCESS_TOKEN`` is NEVER used as a
  fallback. No credential value is ever printed.
* DETERMINISM — the same planner/store inputs produce the same job list
  (the planner's row order: timeframe-outer, instrument-inner), and the
  progress lines are fixed format ``[i/N] INSTR TF START -> END ...
  PASS/FAIL/SKIPPED``.

POINT-IN-TIME / NO-LOOK-AHEAD: each chunk request is bounded to the
planner's monthly chunk window; the deterministic reference time defaults
to the plan window end (or the latest stored candle timestamp for the
backlog datasets when it is newer) so a re-ingestion of a chunk whose
data is already partially stored is never rejected as future-dated. No
future information is introduced.

``data/__init__.py`` stays intentionally empty — import via full paths:

    from engine.data.corpus_ingestion import CorpusIngestionEngine
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable, Iterable, Sequence

from engine.data.corpus_plan import (
    CorpusPreparationPlanner,
    monthly_chunks_for_window,
)
from engine.data.historical_provider import UPSTOX_TOKEN_ENV
from engine.data.historical_service import HistoricalMarketDataService
from engine.data.historical_store import (
    HistoricalDataStore,
    HistoricalDatasetNotFoundError,
)
from engine.models.historical_data import (
    HistoricalDataRequest,
    HistoricalIngestionStatus,
)

#: Unix date rendering for chunk labels and messages (deterministic,
#: no locale / timezone surprises).
_DATE_FMT = "%Y-%m-%d"

#: Defensive redaction patterns — a credential value must never surface
#: in a progress/failure line (something that could appear if an
#: exception message ever quoted the Authorization header).
_BEARER_RE = re.compile(r"Bearer\s+\S+")
_SENSITIVE_VARS = (
    UPSTOX_TOKEN_ENV,
    "UPSTOX_ACCESS_TOKEN",
    "UPSTOX_ANALYTICS_TOKEN",
)

COMPLETED = "COMPLETED"
FAILED = "FAILED"
SKIPPED = "SKIPPED"

#: Statuses that count as effectively covered by the corpus.
_SUCCESS_STATUSES = (COMPLETED, SKIPPED)


class CorpusIngestionRunError(Exception):
    """A single corpus chunk failed to ingest (recorded, never fatal)."""


class CorpusIngestionError(Exception):
    """The corpus ingestion run could not be started (no work executed)."""


# ============================================================
# CREDENTIAL PRECHECK
# ============================================================


def check_upstox_analytics_token() -> str | None:
    """
    Resolve the Upstox analytics token (the runner's credential source).

    Uses the SAME mechanism as
    :class:`~engine.data.historical_provider.UpstoxHistoricalDataProvider`
    (the ``UPSTOX_ANALYTICS_TOKEN`` environment variable). Returns
    ``None`` when unset/empty. ``UPSTOX_ACCESS_TOKEN`` is NEVER used as a
    fallback and is never copied into ``UPSTOX_ANALYTICS_TOKEN``.
    """

    value = os.environ.get(UPSTOX_TOKEN_ENV)
    return value if value else None


def require_upstox_token() -> str:
    """
    Credential precheck guard. Raises :class:`CorpusIngestionError` when
    the analytics token is missing — NO API/ingestion work may start.
    """

    if check_upstox_analytics_token() is None:
        raise CorpusIngestionError(
            "UPSTOX_ANALYTICS_TOKEN is not set. Configure the Upstox "
            "Analytics Token before starting corpus ingestion.",
        )
    # Return the sentinel only — the caller never receives the token value.
    return UPSTOX_TOKEN_ENV


# ============================================================
# CONFIGURATION
# ============================================================


@dataclass(frozen=True, slots=True)
class CorpusIngestionConfig:
    """
    Runner configuration (MINIMAL — NO strategy / scoring parameters).

    The TIMEFRAMES are NOT configured here: the runner reuses the
    EXISTING planner (whose ``CorpusPlanConfig`` carries the timeframes)
    as the single source of the work matrix.

    provider
        The provider name used to build every
        :class:`HistoricalDataRequest` (default ``"upstox-historical"``).

    require_upstox_token
        When True (default) the runner performs the credential precheck
        on ``UPSTOX_ANALYTICS_TOKEN`` BEFORE any ingestion step and fails
        cleanly when it is missing. Set False ONLY for offline /
        non-Upstox testing (e.g. a deterministic local provider).

    reference_now
        Optional deterministic reference time. When None the runner
        derives it per run from the plan window end and the store's
        latest stored candle timestamps.

    label / metadata
        Descriptive run identity carried onto the plan / provenance.
    """

    provider: str = "upstox-historical"
    require_upstox_token: bool = True
    reference_now: datetime | None = None
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.provider, str) or not self.provider.strip():
            raise ValueError("provider must be a non-empty string.")
        if not isinstance(self.label, str):
            raise ValueError("label must be a string.")
        if self.reference_now is not None and self.reference_now.tzinfo is None:
            raise ValueError("reference_now must be timezone-aware.")
        object.__setattr__(
            self,
            "metadata",
            tuple(sorted((str(k), str(v)) for k, v in self.metadata)),
        )


# ============================================================
# WORK MODELS
# ============================================================


@dataclass(frozen=True, slots=True)
class CorpusJob:
    """One missing monthly chunk to ingest (a single store operation)."""

    index: int  # 1-based backlog position (deterministic, for reporting)
    instrument: str
    timeframe: str
    start: datetime
    end: datetime

    @property
    def key(self) -> str:
        """Deterministic chunk key (matches the planner's chunk key)."""

        return f"{self.timeframe}:{self.start.isoformat()}:{self.end.isoformat()}"

    @property
    def label(self) -> str:
        """Short deterministic human label ``INSTRUMENT TF START -> END``."""

        return (
            f"{self.instrument} {self.timeframe} "
            f"{self.start.strftime(_DATE_FMT)} -> {self.end.strftime(_DATE_FMT)}"
        )


@dataclass(frozen=True, slots=True)
class CorpusBacklog:
    """The current missing work set, derived from the planner + store."""

    jobs: tuple[CorpusJob, ...]
    provider: str
    window_start: datetime
    window_end: datetime
    instruments: tuple[str, ...] = ()
    plan_id: str = ""

    @property
    def missing_count(self) -> int:
        return len(self.jobs)

    @property
    def is_empty(self) -> bool:
        return not self.jobs


@dataclass(frozen=True, slots=True)
class CorpusChunkOutcome:
    """The result of ONE chunk attempt (independent failure isolation)."""

    job: CorpusJob
    status: str  # COMPLETED / FAILED / SKIPPED
    records_added: int = 0
    records_accepted: int = 0
    records_rejected: int = 0
    stored_total: int = 0
    covered_now: bool = False
    detail: str = ""  # reason (safe) or context
    message: str = ""  # the deterministic progress-line tail (no secrets)

    @property
    def succeeded(self) -> bool:
        return self.status in _SUCCESS_STATUSES


@dataclass(frozen=True, slots=True)
class CorpusIngestionSummary:
    """
    Final accounting of one corpus-ingestion run.

    ``remaining`` is RE-DERIVED from the current persisted corpus (a
    fresh planner plan over the same window after the run), so a partial /
    failed run reports exactly what still has to be fetched on the next
    run — no separate completion database.
    """

    label: str
    metadata: tuple[tuple[str, str], ...]
    backlog_count: int
    completed: int
    skipped: int
    failed: int
    candles_added: int
    remaining: int
    failed_chunks: tuple[str, ...]
    skip_reasons: tuple[tuple[str, int], ...]

    @property
    def attempted(self) -> int:
        return self.completed + self.failed


@dataclass(frozen=True, slots=True)
class CorpusIngestionSession:
    """One executed runner invocation (input backlog + per-chunk outcomes)."""

    backlog: CorpusBacklog
    results: tuple[CorpusChunkOutcome, ...]
    summary: CorpusIngestionSummary
    reference_now: datetime


# ============================================================
# ENGINE
# ============================================================


class CorpusIngestionEngine:
    """
    Orchestrates the resumable historical corpus ingestion.

    ``planner`` is the EXISTING ``CorpusPreparationPlanner`` (the single
    source of missing-chunk truth). ``service`` is the EXISTING
    ``HistoricalMarketDataService`` (the single ingestion pipeline).
    ``reporter`` is an optional ``Callable[[str], None]`` receiving one
    deterministic progress line per chunk.

    Stateless across calls (nothing is persisted outside the existing
    historical store).
    """

    def __init__(
        self,
        planner: CorpusPreparationPlanner,
        service: HistoricalMarketDataService,
        config: CorpusIngestionConfig | None = None,
        *,
        reporter: Callable[[str], None] | None = None,
    ) -> None:
        if planner is None or service is None:
            raise ValueError("planner and service are required.")
        self.planner = planner
        self.service = service
        self.config = config or CorpusIngestionConfig()
        self.reporter = reporter

    # ------------------------------------------------------------
    # WORK DERIVATION (from the existing planner + store)
    # ------------------------------------------------------------

    def _plan_to_jobs(self, plan) -> tuple[CorpusJob, ...]:
        """
        The missing monthly chunks of an existing plan as jobs.

        The planner's chunk key is ``"<timeframe>:<start-iso>:<end-iso>"``
        where the ISO timestamps themselves CONTAIN colons (``+00:00``),
        so keys are matched against the DETERMINISTIC monthly chunk grid
        (:func:`monthly_chunks_for_window`) rather than split on ``":"``.
        The job order mirrors the planner (timeframe-outer,
        instrument-inner, chunk-key-inner).
        """

        grid = monthly_chunks_for_window(plan.start, plan.end)
        ranges_by_timeframe: dict[str, dict[str, tuple[datetime, datetime]]] = {}
        for c_start, c_end in grid:
            for timeframe in plan.timeframes:
                key = f"{timeframe}:{c_start.isoformat()}:{c_end.isoformat()}"
                ranges_by_timeframe.setdefault(timeframe, {})[key] = (
                    c_start, c_end,
                )

        jobs: list[CorpusJob] = []
        for row in plan.rows:
            if not row.provider_supported:
                continue
            coverage = row.coverage
            if coverage is None:
                continue
            ranges = ranges_by_timeframe.get(row.timeframe, {})
            for key in coverage.missing_chunk_keys:
                chunk_range = ranges.get(key)
                if chunk_range is None:
                    continue
                chunk_start, chunk_end = chunk_range
                jobs.append(
                    CorpusJob(
                        index=len(jobs) + 1,
                        instrument=row.instrument,
                        timeframe=row.timeframe,
                        start=chunk_start,
                        end=chunk_end,
                    ),
                )
        return tuple(jobs)

    def build_backlog(
        self,
        start: datetime,
        end: datetime,
        instruments: Sequence[str] | None = None,
    ) -> CorpusBacklog:
        """
        Derive the CURRENT missing work set from the planner + store.

        The planner (not this runner) computes the missing chunks from the
        persisted corpus; the runner only converts them into jobs.
        """

        plan = self.planner.plan(
            instruments,
            start=start,
            end=end,
            label=self.config.label,
            metadata=self.config.metadata,
        )
        jobs = self._plan_to_jobs(plan)
        return CorpusBacklog(
            jobs=jobs,
            provider=self.config.provider,
            window_start=start,
            window_end=end,
            instruments=(
                tuple(sorted({str(i).strip().upper() for i in instruments}))
                if instruments
                else tuple(plan.instruments)
            ),
            plan_id=plan.plan_id,
        )

    # ------------------------------------------------------------
    # REFERENCE TIME (deterministic, look-ahead-safe)
    # ------------------------------------------------------------

    def _reference_now(self, backlog: CorpusBacklog) -> datetime:
        if self.config.reference_now is not None:
            return self.config.reference_now
        latest = backlog.window_end
        if self.planner.store is not None:
            backlog_pairs = {(job.instrument, job.timeframe) for job in backlog.jobs}
            try:
                for info in self.planner.store.list_datasets():
                    if (info.instrument, info.timeframe) not in backlog_pairs:
                        continue
                    if not info.last_timestamp:
                        continue
                    try:
                        ts = datetime.fromisoformat(info.last_timestamp)
                    except ValueError:
                        continue
                    if ts > latest:
                        latest = ts
            except Exception:  # noqa: BLE001 - store listing never aborts
                pass
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=UTC)
        return latest

    # ------------------------------------------------------------
    # COVERAGE PRECHECK (resumability within a run)
    # ------------------------------------------------------------

    def _chunk_covered(self, job: CorpusJob) -> bool:
        """
        Is this monthly chunk already covered by the persisted corpus?

        Mirrors the planner's chunk-coverage semantics (a chunk is covered
        when the stored series intersects it: ``first_ts < chunk_end`` and
        ``last_ts >= chunk_start``) so a job is skipped when the store
        already contains its data — idempotent reruns without a second
        completion database.
        """

        store: HistoricalDataStore | None = self.planner.store
        if store is None:
            return False
        try:
            candles = store.load_candles(job.instrument, job.timeframe)
        except (HistoricalDatasetNotFoundError, ValueError, OSError):
            return False
        except Exception:  # noqa: BLE001 - a corrupt dataset is not "covered"
            return False
        if not candles:
            return False
        return candles[0].timestamp < job.end and candles[-1].timestamp >= job.start

    # ------------------------------------------------------------
    # SINGLE-CHUNK INGESTION (through the EXISTING service)
    # ------------------------------------------------------------

    def _ingest_one(
        self,
        job: CorpusJob,
        reference_now: datetime,
    ) -> tuple[int, int, int, int]:
        """
        Ingest exactly one chunk through the EXISTING ingestion service.

        Returns ``(records_added, accepted, rejected, stored_total)``.
        Raises :class:`CorpusIngestionRunError` on any non-persistable
        outcome (empty / invalid / error / exception) — the chunk is NOT
        marked covered.
        """

        request = HistoricalDataRequest(
            instrument=job.instrument,
            timeframe=job.timeframe,
            start=job.start,
            end=job.end,
            provider=self.config.provider,
        )
        try:
            result = self.service.ingest(request, reference_now=reference_now)
        except Exception as exc:  # noqa: BLE001 - per-chunk boundary
            raise CorpusIngestionRunError(self._safe_reason(exc)) from exc
        status = result.fetch.status
        if status not in (
            HistoricalIngestionStatus.AVAILABLE,
            HistoricalIngestionStatus.PARTIAL,
        ):
            reason = result.fetch.provenance.reason or status.value
            raise CorpusIngestionRunError(self._safe_reason(reason))
        if result.store is not None:
            return (
                result.store.records_added,
                result.fetch.accepted_count,
                result.fetch.rejected_count,
                result.store.total_candles,
            )
        return (
            result.fetch.accepted_count,
            result.fetch.accepted_count,
            result.fetch.rejected_count,
            result.fetch.accepted_count,
        )

    def _attempt_job(
        self,
        job: CorpusJob,
        reference_now: datetime,
    ) -> CorpusChunkOutcome:
        if self._chunk_covered(job):
            return CorpusChunkOutcome(
                job=job,
                status=SKIPPED,
                covered_now=True,
                detail="already covered by the persisted corpus",
                message=f"{job.label} ... SKIPPED (already covered)",
            )
        try:
            added, accepted, rejected, stored_total = self._ingest_one(
                job, reference_now,
            )
        except CorpusIngestionRunError as exc:
            safe = self._safe_reason(str(exc))
            return CorpusChunkOutcome(
                job=job,
                status=FAILED,
                detail=safe,
                message=f"{job.label} ... FAIL: {safe}",
            )
        except Exception as exc:  # noqa: BLE001 - unexpected per-chunk failures
            safe = self._safe_reason(exc)
            return CorpusChunkOutcome(
                job=job,
                status=FAILED,
                detail=safe,
                message=f"{job.label} ... FAIL: {safe}",
            )
        return CorpusChunkOutcome(
            job=job,
            status=COMPLETED,
            records_added=added,
            records_accepted=accepted,
            records_rejected=rejected,
            stored_total=stored_total,
            covered_now=self._chunk_covered(job),
            detail="",
            message=f"{job.label} ... PASS ({added} candles)",
        )

    # ------------------------------------------------------------
    # REDACTION (no credential / token leakage)
    # ------------------------------------------------------------

    @staticmethod
    def _safe_reason(text: str) -> str:
        redacted = _BEARER_RE.sub("Bearer <redacted>", str(text))
        for name in _SENSITIVE_VARS:
            redacted = redacted.replace(f"{name}=", f"{name}=<redacted>")
            redacted = redacted.replace(f"{name}:", f"{name}:<redacted>")
        return redacted

    # ------------------------------------------------------------
    # REMAINING WORK (re-derived from the planner + store)
    # ------------------------------------------------------------

    def _remaining_after(self, backlog: CorpusBacklog, completed: int) -> int:
        if self.planner.store is None:
            return max(0, backlog.missing_count - completed)
        try:
            plan = self.planner.plan(
                backlog.instruments,
                start=backlog.window_start,
                end=backlog.window_end,
                label=self.config.label,
                metadata=self.config.metadata,
            )
            return plan.missing_request_count
        except Exception:  # noqa: BLE001 - fall back to input math
            return max(0, backlog.missing_count - completed)

    # ------------------------------------------------------------
    # RUN
    # ------------------------------------------------------------

    def run(
        self,
        backlog: CorpusBacklog | None = None,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        instruments: Sequence[str] | None = None,
        reference_now: datetime | None = None,
        label: str = "",
        metadata: Iterable[tuple[str, str]] | None = None,
    ) -> CorpusIngestionSession:
        """
        Execute the missing chunks sequentially.

        ``backlog`` may be supplied directly (derived from
        ``self.build_backlog``); otherwise ``start``/``end`` (aware,
        ``start < end``) build one. Every successful chunk is persisted
        immediately through the existing service; failures are isolated
        per chunk and the runner continues. Progress lines (``[i/N] ...``)
        are delivered via the reporter callback, if any.
        """

        # CREDENTIAL PRECHECK (before ANY ingestion step): when the config
        # requires the Upstox analytics token, verify it is present BEFORE
        # the loop starts. A missing token raises
        # :class:`CorpusIngestionError` with ZERO API requests and ZERO
        # persistence mutations.
        if self.config.require_upstox_token:
            require_upstox_token()

        if backlog is None:
            if start is None or end is None or not start < end:
                raise ValueError(
                    "a plan window is required: pass start/end (or a backlog).",
                )
            backlog = self.build_backlog(start, end, instruments)
        if reference_now is not None:
            if reference_now.tzinfo is None:
                raise ValueError("reference_now must be timezone-aware.")
            now = reference_now
        else:
            now = self._reference_now(backlog)

        results: list[CorpusChunkOutcome] = []
        if not backlog.is_empty:
            total = len(backlog.jobs)
            for position, job in enumerate(backlog.jobs, start=1):
                outcome = self._attempt_job(job, now)
                results.append(outcome)
                if self.reporter is not None:
                    self.reporter(
                        f"[{position}/{total}] {outcome.message}",
                    )

        summary = self._build_summary(backlog, results, label, metadata)
        return CorpusIngestionSession(
            backlog=backlog,
            results=tuple(results),
            summary=summary,
            reference_now=now,
        )

    def _build_summary(
        self,
        backlog: CorpusBacklog,
        results: list[CorpusChunkOutcome],
        label: str,
        metadata: Iterable[tuple[str, str]] | None,
    ) -> CorpusIngestionSummary:
        completed = sum(1 for o in results if o.status == COMPLETED)
        skipped = sum(1 for o in results if o.status == SKIPPED)
        failed = sum(1 for o in results if o.status == FAILED)
        candles_added = sum(o.records_added for o in results)
        failed_chunks = tuple(
            f"{o.job.label}: {o.detail}" for o in results if o.status == FAILED
        )
        skip_counts: dict[str, int] = {}
        for o in results:
            if o.status == SKIPPED:
                reason = o.detail or "already covered"
                skip_counts[reason] = skip_counts.get(reason, 0) + 1
        return CorpusIngestionSummary(
            label=label or self.config.label,
            metadata=tuple(
                sorted((str(k), str(v)) for k, v in (metadata or ())),
            ),
            backlog_count=backlog.missing_count,
            completed=completed,
            skipped=skipped,
            failed=failed,
            candles_added=candles_added,
            remaining=self._remaining_after(backlog, completed),
            failed_chunks=failed_chunks,
            skip_reasons=tuple(sorted(skip_counts.items())),
        )


__all__ = [
    "COMPLETED",
    "CorpusBacklog",
    "CorpusChunkOutcome",
    "CorpusIngestionConfig",
    "CorpusIngestionEngine",
    "CorpusIngestionError",
    "CorpusIngestionRunError",
    "CorpusIngestionSession",
    "CorpusIngestionSummary",
    "CorpusJob",
    "FAILED",
    "SKIPPED",
    "check_upstox_analytics_token",
    "require_upstox_token",
]