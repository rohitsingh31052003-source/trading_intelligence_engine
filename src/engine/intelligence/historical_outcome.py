"""
Historical opportunity outcome evaluator (Sprint 11W).

:class:`OutcomeEvaluator` evaluates what price did AFTER an opportunity
was identified at a point in time ``T``, using ONLY candles that closed
strictly after ``T`` and within the configured evaluation horizon. The
opportunity / decision generated at ``T`` is NEVER recalculated using
future data and is NEVER mutated.

This is the forward-only outcome layer of the separated concern
pipeline:

    1. MARKET OBSERVATION          (Sprint 11O / 11P)
    2. SETUP IDENTIFICATION         (Sprint 11Q)
    3. TRADE CANDIDATE GENERATION   (Sprint 11R)
    4. CANDIDATE RANKING / DECISION (Sprint 11S)
    5. TRADE OPPORTUNITY FILTER     (Sprint 11T)
    6. MULTI-TIMEFRAME CONTEXT      (Sprint 11U)
    7. MARKET SCANNER               (Sprint 11U)
    8. HISTORICAL REPLAY            (Sprint 11V)
    9. OUTCOME EVALUATION           (Sprint 11W)  <- this layer

DESIGN PRINCIPLE — forward-only, no leakage (HARD REQUIREMENT):

The evaluator inspects ONLY future candles (``timestamp > evaluation
timestamp``). The :class:`~engine.models.historical_outcome.OutcomeSubject`
(opportunity projection) is fixed before outcome evaluation begins. No
component of the outcome re-reads the candles used to generate the
decision at ``T``, and no candle beyond the configured
``max_holding_bars`` horizon is ever inspected.

DESIGN PRINCIPLE — LONG / SHORT symmetry:

The favorable and adverse directions are derived from the candidate
direction. For LONG, favorable = upward (target above, stop below);
for SHORT, favorable = downward (target below, stop above). The exact
same logic is applied symmetrically — no direction-specific hacks.

DESIGN PRINCIPLE — same-candle ambiguity (mandatory):

If a SINGLE candle touches BOTH the stop and the target and intrabar
ordering is unavailable, the outcome is represented explicitly as
:attr:`~engine.models.historical_outcome.OutcomeStatus.BOTH_TOUCHED`.
A winner or loser is NEVER manufactured. The first-touch comparison
determines this: when the first candle that touches the target is the
SAME candle that first touches the stop, the outcome is ambiguous.

DESIGN PRINCIPLE — no fabricated values:

Incomplete / invalid geometry (a missing level or non-positive risk)
yields :attr:`NO_GEOMETRY
<engine.models.historical_outcome.OutcomeStatus.NO_GEOMETRY>` — never a
fabricated target hit, stop hit, MFE, MAE or R-multiple. Missing future
candles yield :attr:`INSUFFICIENT_DATA
<engine.models.historical_outcome.OutcomeStatus.INSUFFICIENT_DATA>`.

DESIGN PRINCIPLE — deterministic:

Identical inputs always produce identical outputs. No wall-clock time,
no randomness, no unordered iteration.

This is intelligence / analysis, NOT a new orchestration package.
``intelligence/__init__.py`` stays intentionally empty; import via full
paths, e.g. ``from engine.intelligence.historical_outcome import
OutcomeEvaluator``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Sequence

from engine.config.historical_outcome_config import OutcomeConfig
from engine.models.historical import HistoricalDataset
from engine.models.historical_outcome import (
    HistoricalOutcome,
    OutcomeStatus,
    OutcomeSubject,
    ReplayOutcomePoint,
    ReplayOutcomeReport,
)
from engine.models.market_scan import InstrumentScanResult, MarketScanResult
from engine.models.ohlcv import OHLCVCandle


# ============================================================
# OUTCOME EVALUATOR
# ============================================================


class OutcomeEvaluator:
    """
    Evaluate the forward-only historical outcome of one opportunity.

    Public API:

        evaluate(subject, future_candles) -> HistoricalOutcome

    The evaluator is stateless across calls: identical inputs always
    produce identical outputs. The input ``subject`` is NEVER mutated.
    """

    def __init__(self, config: OutcomeConfig | None = None) -> None:
        self.config = config or OutcomeConfig()

    # ------------------------------------------------------------
    # PUBLIC API
    # ------------------------------------------------------------

    def evaluate(
        self,
        subject: OutcomeSubject,
        future_candles: Sequence[OHLCVCandle],
    ) -> HistoricalOutcome:
        """
        Evaluate the historical outcome of ``subject`` using only the
        supplied ``future_candles`` (candles that closed strictly after
        the evaluation timestamp).

        The caller is responsible for supplying only candles with a
        timestamp strictly greater than the subject's evaluation
        timestamp. The evaluator further truncates this sequence to the
        configured ``max_holding_bars`` horizon, so mutating candles
        beyond the horizon never changes the outcome.

        The result is deterministic and descriptive. It makes no
        profitability / probability / directional-prediction claim.
        """

        # 1. Geometry validation.
        if not subject.is_directional or not subject.has_geometry:
            return self._no_geometry(subject)

        entry = float(subject.entry)  # type: ignore[arg-type]
        stop = float(subject.stop)  # type: ignore[arg-type]
        target = float(subject.target)  # type: ignore[arg-type]
        direction = subject.direction

        risk = abs(entry - stop)
        if risk <= 0:
            # Degenerate geometry: stop == entry (zero risk) is invalid.
            return self._no_geometry(subject)

        # 2. Forward window: enforce strictly-after-the-anchor + horizon
        #    cap. The strictly-greater filter is structural (the caller
        #    may supply the full future series; only candles that closed
        #    strictly after T, within the horizon, are inspected).
        window = self._forward_window(subject, future_candles)
        if not window:
            return self._insufficient_data(subject)

        # 3. Walk the forward window, tracking first touches + MFE/MAE.
        first_target_bar: int | None = None
        first_stop_bar: int | None = None
        mfe = 0.0
        mae = 0.0
        for i, candle in enumerate(window):
            target_touched, stop_touched = self._touches(
                candle, direction, target, stop,
            )
            if target_touched and first_target_bar is None:
                first_target_bar = i
            if stop_touched and first_stop_bar is None:
                first_stop_bar = i
            fav, adv = self._excursion(candle, direction, entry)
            if fav > mfe:
                mfe = fav
            if adv > mae:
                mae = adv

        # 4. Resolve the outcome from the first-touch comparison.
        return self._resolve(
            subject=subject,
            window=window,
            first_target_bar=first_target_bar,
            first_stop_bar=first_stop_bar,
            entry=entry,
            stop=stop,
            target=target,
            risk=risk,
            mfe=mfe,
            mae=mae,
        )

    # ------------------------------------------------------------
    # GEOMETRY / DATA GUARDS
    # ------------------------------------------------------------

    def _no_geometry(self, subject: OutcomeSubject) -> HistoricalOutcome:
        return HistoricalOutcome(
            subject=subject,
            outcome_status=OutcomeStatus.NO_GEOMETRY,
            reason=(
                "Opportunity geometry is incomplete or invalid "
                "(missing entry / stop / target, non-directional, or "
                "non-positive risk). No outcome is fabricated."
            ),
        )

    def _insufficient_data(
        self, subject: OutcomeSubject,
    ) -> HistoricalOutcome:
        return HistoricalOutcome(
            subject=subject,
            outcome_status=OutcomeStatus.INSUFFICIENT_DATA,
            reason=(
                "No future candles are available after the evaluation "
                "timestamp within the evaluation horizon. The outcome "
                "cannot be evaluated."
            ),
        )

    # ------------------------------------------------------------
    # FORWARD WINDOW
    # ------------------------------------------------------------

    def _forward_window(
        self,
        subject: OutcomeSubject,
        future_candles: Sequence[OHLCVCandle],
    ) -> list[OHLCVCandle]:
        """
        Return the forward evaluation window: candles with a timestamp
        STRICTLY greater than the subject's evaluation timestamp,
        truncated to the first ``max_holding_bars``.

        The strictly-greater filter is the STRUCTURAL forward-only
        guarantee: even if the caller accidentally supplies the
        evaluation candle (or earlier candles), the evaluator never
        inspects it. The horizon cap ensures candles beyond
        ``max_holding_bars`` are never inspected either. When the
        evaluation timestamp is ``None`` (no anchor), no forward window
        can be defined and an empty list is returned.
        """

        anchor = subject.evaluation_timestamp
        if anchor is None:
            return []
        cap = self.config.max_holding_bars
        forward = [c for c in future_candles if c.timestamp > anchor]
        return forward[:cap]

    # ------------------------------------------------------------
    # TOUCH + EXCURSION (direction-symmetric)
    # ------------------------------------------------------------

    @staticmethod
    def _touches(
        candle: OHLCVCandle,
        direction: str,
        target: float,
        stop: float,
    ) -> tuple[bool, bool]:
        """
        Determine whether a candle touched the target and/or the stop,
        using OHLC (not close-only).

        For LONG: target touched when ``high >= target``; stop touched
        when ``low <= stop``.
        For SHORT: target touched when ``low <= target``; stop touched
        when ``high >= stop``.
        """

        if direction == "LONG":
            target_touched = candle.high >= target
            stop_touched = candle.low <= stop
        else:  # SHORT
            target_touched = candle.low <= target
            stop_touched = candle.high >= stop
        return target_touched, stop_touched

    @staticmethod
    def _excursion(
        candle: OHLCVCandle, direction: str, entry: float,
    ) -> tuple[float, float]:
        """
        Return ``(favorable, adverse)`` absolute excursion for one
        candle relative to ``entry``.

        For LONG: favorable = ``high - entry``; adverse = ``entry - low``.
        For SHORT: favorable = ``entry - low``; adverse = ``high - entry``.

        Values are clamped to ``>= 0`` (an excursion cannot be negative
        by definition; the favorable/adverse directions are mutually
        exclusive per candle).
        """

        if direction == "LONG":
            fav = candle.high - entry
            adv = entry - candle.low
        else:  # SHORT
            fav = entry - candle.low
            adv = candle.high - entry
        return (fav if fav > 0 else 0.0, adv if adv > 0 else 0.0)

    # ------------------------------------------------------------
    # OUTCOME RESOLUTION
    # ------------------------------------------------------------

    def _resolve(
        self,
        subject: OutcomeSubject,
        window: list[OHLCVCandle],
        first_target_bar: int | None,
        first_stop_bar: int | None,
        entry: float,
        stop: float,
        target: float,
        risk: float,
        mfe: float,
        mae: float,
    ) -> HistoricalOutcome:
        direction = subject.direction
        mfe_r = mfe / risk if risk > 0 else None
        mae_r = mae / risk if risk > 0 else None

        # Neither touched -> EXPIRED (mark-to-close at horizon end).
        if first_target_bar is None and first_stop_bar is None:
            last = window[-1]
            exit_price = last.close
            realized_r = self._realized_r(direction, entry, exit_price, risk)
            bars_held = len(window)
            return HistoricalOutcome(
                subject=subject,
                outcome_status=OutcomeStatus.EXPIRED,
                outcome_timestamp=None,
                exit_price=exit_price,
                bars_held=bars_held,
                mfe=mfe,
                mae=mae,
                mfe_r=mfe_r,
                mae_r=mae_r,
                realized_r=realized_r,
                risk=risk,
                reason=(
                    f"Neither target ({target:.4f}) nor stop ({stop:.4f}) "
                    f"was reached within the {len(window)}-candle horizon. "
                    f"Mark-to-close at {exit_price:.4f}."
                ),
            )

        # Same-candle ambiguity: the first target touch and first stop
        # touch occur on the SAME candle -> BOTH_TOUCHED (no winner).
        if (
            first_target_bar is not None
            and first_stop_bar is not None
            and first_target_bar == first_stop_bar
        ):
            bar = first_target_bar
            candle = window[bar]
            return HistoricalOutcome(
                subject=subject,
                outcome_status=OutcomeStatus.BOTH_TOUCHED,
                outcome_timestamp=candle.timestamp,
                exit_price=None,
                bars_held=bar + 1,
                mfe=mfe,
                mae=mae,
                mfe_r=mfe_r,
                mae_r=mae_r,
                realized_r=None,
                risk=risk,
                reason=(
                    f"A single candle (bar {bar + 1}) touched BOTH the "
                    f"target ({target:.4f}) and the stop ({stop:.4f}); "
                    "intrabar ordering is unavailable so the first touch "
                    "cannot be determined. Outcome is explicitly ambiguous; "
                    "no realized R is fabricated."
                ),
            )

        # Target touched first (strictly earlier than stop, or stop
        # never touched) -> TARGET_HIT.
        if first_target_bar is not None and (
            first_stop_bar is None or first_target_bar < first_stop_bar
        ):
            bar = first_target_bar
            candle = window[bar]
            realized_r = self._realized_r(direction, entry, target, risk)
            return HistoricalOutcome(
                subject=subject,
                outcome_status=OutcomeStatus.TARGET_HIT,
                outcome_timestamp=candle.timestamp,
                exit_price=target,
                bars_held=bar + 1,
                mfe=mfe,
                mae=mae,
                mfe_r=mfe_r,
                mae_r=mae_r,
                realized_r=realized_r,
                risk=risk,
                reason=(
                    f"Target ({target:.4f}) reached at bar {bar + 1} "
                    f"before the stop ({stop:.4f}). Favorable outcome."
                ),
            )

        # Stop touched first -> STOP_HIT.
        # (first_stop_bar is not None and (first_target_bar is None or
        #  first_stop_bar < first_target_bar))
        bar = first_stop_bar  # type: ignore[assignment]
        candle = window[bar]
        realized_r = self._realized_r(direction, entry, stop, risk)
        return HistoricalOutcome(
            subject=subject,
            outcome_status=OutcomeStatus.STOP_HIT,
            outcome_timestamp=candle.timestamp,
            exit_price=stop,
            bars_held=bar + 1,
            mfe=mfe,
            mae=mae,
            mfe_r=mfe_r,
            mae_r=mae_r,
            realized_r=realized_r,
            risk=risk,
            reason=(
                f"Stop ({stop:.4f}) reached at bar {bar + 1} before the "
                f"target ({target:.4f}). Adverse outcome."
            ),
        )

    @staticmethod
    def _realized_r(
        direction: str, entry: float, exit_price: float, risk: float,
    ) -> float:
        """
        Realized R-multiple for a determinate exit.

        For LONG: ``R = (exit - entry) / risk``.
        For SHORT: ``R = (entry - exit) / risk``.
        """

        if risk <= 0:
            return 0.0
        if direction == "LONG":
            return (exit_price - entry) / risk
        return (entry - exit_price) / risk


# ============================================================
# SUBJECT BUILDERS (decouple the evaluator from heavy reference objects)
# ============================================================


def build_outcome_subject(
    scan_result: InstrumentScanResult,
    scan_id: str = "",
    setup_timeframe: str = "",
) -> OutcomeSubject | None:
    """
    Build an :class:`OutcomeSubject` projection from a live
    :class:`InstrumentScanResult`.

    Extracts the entry / stop / target / direction / identity fields
    from the Sprint 11R ``TradeCandidate`` (reached via the Sprint 11S
    ``TradeDecision`` retained by reference on the scan result). Returns
    ``None`` when the scan result carries no decision / candidate (no
    opportunity to evaluate).

    The projection is deliberately lightweight and serializable: the
    heavy ``TradeDecision`` / ``TradeOpportunity`` / ``MarketContext``
    reference objects are NOT retained. This decouples the outcome
    evaluator from objects that are dropped on scan serialization.
    """

    direction = scan_result.direction or ""
    decision = scan_result.decision
    candidate = getattr(decision, "candidate", None) if decision is not None else None
    if candidate is None:
        return None

    entry = getattr(candidate, "entry_reference", None)
    stop = getattr(candidate, "stop_reference", None)
    target = getattr(candidate, "target_reference", None)

    opportunity = scan_result.opportunity
    opportunity_status = ""
    rank = 0
    if opportunity is not None:
        opp_status = getattr(opportunity, "status", None)
        opportunity_status = getattr(opp_status, "name", "") or ""
        rank = int(getattr(opportunity, "rank", 0) or 0)

    decision_classification = scan_result.decision_classification or ""
    decision_score = int(scan_result.decision_score or 0)

    # Additive projections of existing fields (Sprint 11X analytics needs
    # these as breakdown dimensions; they survive scan serialization
    # because they are captured here, before the heavy reference objects
    # are dropped). They never influence outcome evaluation semantics.
    setup_type = ""
    setup_type_attr = getattr(candidate, "setup_type", None)
    setup_type_name = getattr(setup_type_attr, "name", None)
    if setup_type_name:
        setup_type = str(setup_type_name)
    mtf_alignment = ""
    alignment_attr = getattr(scan_result, "alignment", None)
    alignment_name = getattr(alignment_attr, "name", None)
    if alignment_name:
        mtf_alignment = str(alignment_name)

    return OutcomeSubject(
        instrument=scan_result.instrument,
        direction=direction,
        evaluation_timestamp=scan_result.timestamp,
        entry=entry,
        stop=stop,
        target=target,
        decision_classification=decision_classification,
        decision_score=decision_score,
        opportunity_status=opportunity_status,
        rank=rank,
        scan_id=scan_id,
        setup_timeframe=setup_timeframe,
        setup_type=setup_type,
        mtf_alignment=mtf_alignment,
    )


def build_outcome_subjects(
    scan: MarketScanResult,
    setup_timeframe: str = "",
    eligible_only: bool = True,
) -> tuple[OutcomeSubject, ...]:
    """
    Build the :class:`OutcomeSubject` projections for every (eligible)
    opportunity in a :class:`MarketScanResult`.

    When ``eligible_only`` is ``True`` (the default), only instruments
    that surfaced an eligible opportunity are projected — these are the
    opportunities the outcome layer evaluates. When ``False``, every
    instrument carrying a candidate is projected (useful for audit).
    """

    subjects: list[OutcomeSubject] = []
    for result in scan.results:
        if eligible_only and not result.eligible:
            continue
        subject = build_outcome_subject(
            result, scan_id=scan.scan_id, setup_timeframe=setup_timeframe,
        )
        if subject is not None:
            subjects.append(subject)
    return tuple(subjects)


def future_candles_after(
    candles: Sequence[OHLCVCandle],
    evaluation_timestamp: datetime | None,
) -> tuple[OHLCVCandle, ...]:
    """
    Return the candles with a timestamp strictly greater than
    ``evaluation_timestamp``, in chronological order.

    This is the forward-only slice the outcome evaluator consumes.
    When ``evaluation_timestamp`` is ``None``, an empty tuple is
    returned (no forward window can be defined without an anchor).
    """

    if evaluation_timestamp is None:
        return ()
    return tuple(c for c in candles if c.timestamp > evaluation_timestamp)


# ============================================================
# REPLAY OUTCOME ENGINE (integration with Sprint 11V replay)
# ============================================================


class HistoricalOutcomeEngine:
    """
    Evaluate historical outcomes across a full Sprint 11V replay.

    Pairs each opportunity the existing intelligence pipeline
    identified at each evaluation point (Sprint 11V) with what price
    did afterwards (Sprint 11W), WITHOUT rerunning the pipeline and
    WITHOUT using future information to influence the decisions made
    at ``T``.

    Public API:

        evaluate_replay(replay_result, dataset) -> ReplayOutcomeReport

    The decision / opportunity at each point is taken VERBATIM from the
    replay result (already computed by Sprint 11V using only candles up
    to ``T``). The outcome evaluator then consumes ONLY the setup
    timeframe candles that closed strictly after ``T``. The two
    concerns are kept strictly separate.

    The engine is stateless across calls: identical inputs always
    produce identical outputs.
    """

    def __init__(
        self,
        config: OutcomeConfig | None = None,
        evaluator: OutcomeEvaluator | None = None,
    ) -> None:
        self.config = config or OutcomeConfig()
        self._evaluator = evaluator or OutcomeEvaluator(self.config)

    # ------------------------------------------------------------
    # PUBLIC API
    # ------------------------------------------------------------

    def evaluate_replay(
        self,
        replay_result: object,
        dataset: HistoricalDataset,
    ) -> ReplayOutcomeReport:
        """
        Evaluate historical outcomes for every eligible opportunity at
        every point in a Sprint 11V :class:`ReplayResult`.

        ``replay_result`` is a Sprint 11V ``ReplayResult`` (imported
        lazily to avoid a circular import). The decisions / opportunities
        are taken verbatim from the replay; only the setup-timeframe
        candles after each ``T`` are read for the outcome.

        The result is deterministic and descriptive. It makes no
        profitability / probability / directional-prediction claim.
        """

        points = tuple(getattr(replay_result, "points", ()))
        instruments = tuple(getattr(replay_result, "instruments", ()))
        timeframes = tuple(getattr(replay_result, "timeframes", ("", "")))
        setup_tf = timeframes[1] if len(timeframes) > 1 else ""

        outcome_points: list[ReplayOutcomePoint] = []
        for point in points:
            evaluation_time = getattr(point, "evaluation_time", None)
            scan = getattr(point, "scan", None)
            if scan is None or evaluation_time is None:
                continue
            outcomes = self._evaluate_scan(
                scan, dataset, setup_tf, evaluation_time,
            )
            outcome_points.append(
                ReplayOutcomePoint(
                    evaluation_time=evaluation_time, outcomes=outcomes,
                ),
            )

        return ReplayOutcomeReport(
            report_id=self._report_id(instruments, timeframes, outcome_points),
            instruments=instruments,
            timeframes=timeframes,
            points=tuple(outcome_points),
            rationale=self._rationale(outcome_points),
        )

    def evaluate_scan(
        self,
        scan: MarketScanResult,
        dataset: HistoricalDataset,
        evaluation_time: datetime | None = None,
    ) -> tuple[HistoricalOutcome, ...]:
        """
        Evaluate historical outcomes for every eligible opportunity in
        a single :class:`MarketScanResult`.

        ``evaluation_time`` defaults to the scan's timestamp. The
        setup-timeframe is read from the scan's ``timeframes`` pair.
        """

        timeframes = tuple(getattr(scan, "timeframes", ("", "")))
        setup_tf = timeframes[1] if len(timeframes) > 1 else ""
        if evaluation_time is None:
            evaluation_time = scan.timestamp
        return self._evaluate_scan(scan, dataset, setup_tf, evaluation_time)

    # ------------------------------------------------------------
    # INTERNALS
    # ------------------------------------------------------------

    def _evaluate_scan(
        self,
        scan: MarketScanResult,
        dataset: HistoricalDataset,
        setup_tf: str,
        evaluation_time: datetime,
    ) -> tuple[HistoricalOutcome, ...]:
        subjects = build_outcome_subjects(scan, setup_timeframe=setup_tf)
        outcomes: list[HistoricalOutcome] = []
        for subject in subjects:
            future = self._future_for(
                dataset, subject.instrument, setup_tf, evaluation_time,
            )
            outcomes.append(self._evaluator.evaluate(subject, future))
        return tuple(outcomes)

    def _future_for(
        self,
        dataset: HistoricalDataset,
        instrument: str,
        setup_tf: str,
        evaluation_time: datetime,
    ) -> tuple[OHLCVCandle, ...]:
        """Return the setup-timeframe candles strictly after ``T``."""

        candles = dataset.setup_candles(instrument, setup_tf)
        return future_candles_after(candles, evaluation_time)

    def _report_id(
        self,
        instruments: tuple[str, ...],
        timeframes: tuple[str, ...],
        points: list[ReplayOutcomePoint],
    ) -> str:
        payload = {
            "instruments": list(instruments),
            "timeframes": list(timeframes),
            "max_holding_bars": self.config.max_holding_bars,
            "label": self.config.label,
            "metadata": [list(p) for p in self.config.metadata],
            "points": [
                {
                    "evaluation_time": p.evaluation_time.isoformat(),
                    "instruments": [o.instrument for o in p.outcomes],
                }
                for p in points
            ],
        }
        try:
            canon = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            canon = str(payload)
        digest = hashlib.sha256(canon.encode("utf-8")).hexdigest()
        return f"outcomes-{digest[:16]}"

    def _rationale(
        self, points: list[ReplayOutcomePoint],
    ) -> str:
        if not points:
            return (
                "Historical outcome report evaluated no points. "
                "Descriptive only; not predictive and not a guarantee of "
                "profitability."
            )
        total = sum(len(p.outcomes) for p in points)
        statuses: list[str] = []
        for p in points:
            for o in p.outcomes:
                statuses.append(o.outcome_status.name)
        return (
            f"Historical outcome report evaluated {total} outcome(s) across "
            f"{len(points)} point(s). Outcome statuses: {statuses}. "
            "Outcomes are evaluated forward-only (no future information "
            "influenced the decision at T). Descriptive only; not "
            "predictive and not a guarantee of profitability."
        )


__all__ = [
    "HistoricalOutcomeEngine",
    "OutcomeEvaluator",
    "build_outcome_subject",
    "build_outcome_subjects",
    "future_candles_after",
]
