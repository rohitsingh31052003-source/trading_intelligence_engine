"""
Historical performance analytics engine (Sprint 11X).

:class:`PerformanceAnalyticsEngine` aggregates a collection of Sprint
11W :class:`~engine.models.historical_outcome.HistoricalOutcome` objects
into descriptive performance statistics. It is the downstream
analytics layer of the separated concern pipeline:

    1. MARKET OBSERVATION          (Sprint 11O / 11P)
    2. SETUP IDENTIFICATION         (Sprint 11Q)
    3. TRADE CANDIDATE GENERATION   (Sprint 11R)
    4. CANDIDATE RANKING / DECISION (Sprint 11S)
    5. TRADE OPPORTUNITY FILTER     (Sprint 11T)
    6. MULTI-TIMEFRAME CONTEXT      (Sprint 11U)
    7. MARKET SCANNER               (Sprint 11U)
    8. HISTORICAL REPLAY            (Sprint 11V)
    9. OUTCOME EVALUATION           (Sprint 11W)
   10. PERFORMANCE ANALYTICS        (Sprint 11X)  <- this layer

DESIGN PRINCIPLE — downstream only, no leakage (HARD REQUIREMENT):

The engine consumes ALREADY-COMPUTED historical outcomes. It NEVER
re-evaluates outcomes, NEVER re-runs the pipeline, NEVER re-reads
candles, and NEVER introduces future information into performance
aggregation. The point-in-time correctness established in Sprint 11V
and Sprint 11W is preserved unchanged: the outcomes were evaluated
forward-only (candles strictly after ``T``), and the analytics layer
merely counts / sums / averages their already-resolved values.

DESIGN PRINCIPLE — preserve Sprint 11W semantics exactly:

* ``TARGET_HIT`` / ``STOP_HIT`` are resolved favorable / adverse
  outcomes (valid ``realized_r``).
* ``EXPIRED`` is a mark-to-close result (valid ``realized_r``).
* ``BOTH_TOUCHED`` is ambiguous — it is NEVER treated as a target hit
  or a stop hit, and its ``realized_r`` is ``None`` so it never
  contributes to R aggregates.
* ``NO_GEOMETRY`` / ``INSUFFICIENT_DATA`` carry no fabricated values
  (``realized_r`` is ``None``); they never contribute to R aggregates.

DESIGN PRINCIPLE — no fabricated values:

Unavailable metrics remain ``None``. Win / loss rates use the resolved
target-vs-stop denominator; when zero, the rate is ``None``. Profit
factor is ``None`` when there is no valid negative R. MFE / MAE
averages are ``None`` when no observations are available. Observed
zero is NEVER confused with not-available.

DESIGN PRINCIPLE — deterministic:

Identical inputs always produce identical outputs. No wall-clock time,
no randomness, no unordered iteration. Group ordering is canonical
(defined per dimension) then lexicographic for any remaining keys.

This is intelligence / analysis, NOT a new orchestration package.
``intelligence/__init__.py`` stays intentionally empty; import via full
paths, e.g. ``from engine.intelligence.performance_analytics import
PerformanceAnalyticsEngine``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from statistics import median
from typing import Mapping

from engine.config.performance_config import PerformanceAnalyticsConfig
from engine.models.historical_outcome import (
    HistoricalOutcome,
    OutcomeStatus,
)
from engine.models.historical_performance import (
    BreakdownDimension,
    HistoricalPerformanceAnalytics,
    HistoricalPerformanceBreakdown,
    HistoricalPerformanceGroup,
    HistoricalPerformanceStatistics,
)


# ============================================================
# DETERMINISTIC ORDERING
# ============================================================

#: Canonical order for the DIRECTION dimension (LONG before SHORT).
_DIRECTION_ORDER = ("LONG", "SHORT")

#: Canonical order for the DECISION dimension (strongest first).
_DECISION_ORDER = (
    "PREFERRED", "QUALIFIED", "WATCH", "REJECTED",
)

#: Canonical order for the OPPORTUNITY_STATUS dimension (best first).
_OPPORTUNITY_STATUS_ORDER = (
    "BEST_OPPORTUNITY", "ALTERNATIVE_OPPORTUNITY", "WATCH", "NO_OPPORTUNITY",
)

#: Canonical order for the MTF_ALIGNMENT dimension.
_MTF_ALIGNMENT_ORDER = ("ALIGNED", "NEUTRAL", "CONFLICTING", "UNKNOWN")

#: Canonical order for the SETUP_TYPE dimension.
_SETUP_TYPE_ORDER = (
    "TREND_CONTINUATION", "BREAKOUT", "STRUCTURE_CONTINUATION",
    "RANGE_REJECTION", "SETUP_CANDIDATE",
)

#: Canonical order for the supported breakdown dimensions (overall
#: report ordering).
_DIMENSION_ORDER = (
    BreakdownDimension.INSTRUMENT,
    BreakdownDimension.DIRECTION,
    BreakdownDimension.MTF_ALIGNMENT,
    BreakdownDimension.SETUP_TYPE,
    BreakdownDimension.DECISION,
    BreakdownDimension.OPPORTUNITY_STATUS,
    BreakdownDimension.OPPORTUNITY_RANK,
)


def _ordered_keys(keys: Iterable[str], canonical: Sequence[str]) -> list[str]:
    """
    Return ``keys`` in canonical order first (preserving the canonical
    sequence), then any remaining keys sorted lexicographically. The
    empty-string "unavailable" sentinel sorts LAST among the remaining
    keys (it is not in any canonical list; unavailable metadata is
    reported after observed values).
    """

    remaining = set(keys)
    ordered: list[str] = []
    for name in canonical:
        if name in remaining:
            ordered.append(name)
            remaining.discard(name)
    # Remaining (non-canonical) keys: observed non-canonical values first
    # (sorted lexicographically), then the "" unavailable sentinel last.
    remaining_list = sorted(remaining)
    # Move "" to the end if present.
    if "" in remaining_list:
        remaining_list.remove("")
        remaining_list.append("")
    ordered.extend(remaining_list)
    return ordered


# ============================================================
# CORE STATISTICS
# ============================================================


def _compute_statistics(
    outcomes: Sequence[HistoricalOutcome],
) -> HistoricalPerformanceStatistics:
    """
    Compute the core descriptive statistics for a collection of
    outcomes.

    Pure and deterministic. Never fabricates a value: unavailable
    metrics remain ``None``.
    """

    total = len(outcomes)
    if total == 0:
        return HistoricalPerformanceStatistics()

    target_hits = 0
    stop_hits = 0
    expired = 0
    both_touched = 0
    no_geometry = 0
    insufficient_data = 0

    realized_r_values: list[float] = []
    mfe_values: list[float] = []
    mae_values: list[float] = []
    mfe_r_values: list[float] = []
    mae_r_values: list[float] = []

    for outcome in outcomes:
        status = outcome.outcome_status
        if status == OutcomeStatus.TARGET_HIT:
            target_hits += 1
        elif status == OutcomeStatus.STOP_HIT:
            stop_hits += 1
        elif status == OutcomeStatus.EXPIRED:
            expired += 1
        elif status == OutcomeStatus.BOTH_TOUCHED:
            both_touched += 1
        elif status == OutcomeStatus.NO_GEOMETRY:
            no_geometry += 1
        elif status == OutcomeStatus.INSUFFICIENT_DATA:
            insufficient_data += 1

        # R aggregates: only valid realized_r (TARGET_HIT / STOP_HIT /
        # EXPIRED). BOTH_TOUCHED / NO_GEOMETRY / INSUFFICIENT_DATA have
        # realized_r == None and are excluded.
        if outcome.realized_r is not None:
            realized_r_values.append(float(outcome.realized_r))

        # Excursion aggregates: only available values.
        if outcome.mfe is not None:
            mfe_values.append(float(outcome.mfe))
        if outcome.mae is not None:
            mae_values.append(float(outcome.mae))
        if outcome.mfe_r is not None:
            mfe_r_values.append(float(outcome.mfe_r))
        if outcome.mae_r is not None:
            mae_r_values.append(float(outcome.mae_r))

    resolved = target_hits + stop_hits + both_touched + expired

    # Rates. None when the denominator is zero.
    resolved_denominator = target_hits + stop_hits
    win_rate = (
        target_hits / resolved_denominator
        if resolved_denominator > 0
        else None
    )
    loss_rate = (
        stop_hits / resolved_denominator
        if resolved_denominator > 0
        else None
    )
    expiration_rate = expired / total if total > 0 else None
    ambiguous_rate = both_touched / total if total > 0 else None

    # R-multiple metrics.
    valid_r_count = len(realized_r_values)
    total_realized_r = (
        sum(realized_r_values) if valid_r_count > 0 else None
    )
    average_realized_r = (
        sum(realized_r_values) / valid_r_count if valid_r_count > 0 else None
    )
    median_realized_r = (
        median(realized_r_values) if valid_r_count > 0 else None
    )
    gross_positive_r = (
        sum(r for r in realized_r_values if r > 0)
        if any(r > 0 for r in realized_r_values)
        else None
    )
    gross_negative_r = (
        sum(r for r in realized_r_values if r < 0)
        if any(r < 0 for r in realized_r_values)
        else None
    )
    # Profit factor: gross_positive / abs(gross_negative). None when
    # there is no valid negative R (never fabricated). When there is
    # positive R but no negative R, profit factor is conventionally
    # undefined here -> None (do not fabricate +inf).
    profit_factor: float | None = None
    if gross_positive_r is not None and gross_negative_r is not None:
        neg_abs = abs(gross_negative_r)
        if neg_abs > 0:
            profit_factor = gross_positive_r / neg_abs

    # Excursion averages.
    average_mfe = (
        sum(mfe_values) / len(mfe_values) if mfe_values else None
    )
    average_mae = (
        sum(mae_values) / len(mae_values) if mae_values else None
    )
    average_mfe_r = (
        sum(mfe_r_values) / len(mfe_r_values) if mfe_r_values else None
    )
    average_mae_r = (
        sum(mae_r_values) / len(mae_r_values) if mae_r_values else None
    )

    return HistoricalPerformanceStatistics(
        total=total,
        resolved=resolved,
        target_hits=target_hits,
        stop_hits=stop_hits,
        expired=expired,
        both_touched=both_touched,
        no_geometry=no_geometry,
        insufficient_data=insufficient_data,
        win_rate=win_rate,
        loss_rate=loss_rate,
        expiration_rate=expiration_rate,
        ambiguous_rate=ambiguous_rate,
        total_realized_r=total_realized_r,
        average_realized_r=average_realized_r,
        median_realized_r=median_realized_r,
        gross_positive_r=gross_positive_r,
        gross_negative_r=gross_negative_r,
        profit_factor=profit_factor,
        valid_r_count=valid_r_count,
        average_mfe=average_mfe,
        average_mae=average_mae,
        average_mfe_r=average_mfe_r,
        average_mae_r=average_mae_r,
    )


# ============================================================
# BREAKDOWNS
# ============================================================


def _dimension_key(outcome: HistoricalOutcome, dimension: BreakdownDimension) -> str:
    """Return the grouping key for one outcome under one dimension."""

    subject = outcome.subject
    if dimension == BreakdownDimension.INSTRUMENT:
        return subject.instrument or ""
    if dimension == BreakdownDimension.DIRECTION:
        return subject.direction or ""
    if dimension == BreakdownDimension.DECISION:
        return subject.decision_classification or ""
    if dimension == BreakdownDimension.OPPORTUNITY_STATUS:
        return subject.opportunity_status or ""
    if dimension == BreakdownDimension.MTF_ALIGNMENT:
        return subject.mtf_alignment or ""
    if dimension == BreakdownDimension.SETUP_TYPE:
        return subject.setup_type or ""
    if dimension == BreakdownDimension.OPPORTUNITY_RANK:
        return str(subject.rank) if subject.rank != 0 else ""
    return ""


def _canonical_for(dimension: BreakdownDimension) -> Sequence[str]:
    if dimension == BreakdownDimension.DIRECTION:
        return _DIRECTION_ORDER
    if dimension == BreakdownDimension.DECISION:
        return _DECISION_ORDER
    if dimension == BreakdownDimension.OPPORTUNITY_STATUS:
        return _OPPORTUNITY_STATUS_ORDER
    if dimension == BreakdownDimension.MTF_ALIGNMENT:
        return _MTF_ALIGNMENT_ORDER
    if dimension == BreakdownDimension.SETUP_TYPE:
        return _SETUP_TYPE_ORDER
    return ()


def _build_breakdown(
    outcomes: Sequence[HistoricalOutcome],
    dimension: BreakdownDimension,
) -> HistoricalPerformanceBreakdown:
    """
    Build the grouped performance breakdown for one dimension.

    Groups are ordered canonically (defined per dimension) then
    lexicographically for any remaining keys. OPPORTUNITY_RANK groups
    are ordered numerically ascending (rank 1 first).
    """

    buckets: dict[str, list[HistoricalOutcome]] = {}
    for outcome in outcomes:
        key = _dimension_key(outcome, dimension)
        buckets.setdefault(key, []).append(outcome)

    if dimension == BreakdownDimension.OPPORTUNITY_RANK:
        # Numeric ascending order; the "" (unavailable) sentinel LAST
        # (unavailable metadata sorts after all observed ranks).
        keys = sorted(
            buckets.keys(),
            key=lambda k: (
                1 if k == "" else 0,
                int(k) if k != "" else 0,
            ),
        )
    else:
        keys = _ordered_keys(buckets.keys(), _canonical_for(dimension))

    groups = tuple(
        HistoricalPerformanceGroup(
            key=k, statistics=_compute_statistics(buckets[k]),
        )
        for k in keys
    )
    return HistoricalPerformanceBreakdown(dimension=dimension, groups=groups)


# ============================================================
# ENGINE
# ============================================================


class PerformanceAnalyticsEngine:
    """
    Aggregate a collection of Sprint 11W historical outcomes into
    descriptive performance analytics.

    Public API:

        analyze(outcomes, label="", metadata=None) -> HistoricalPerformanceAnalytics

    The engine is stateless across calls: identical inputs always
    produce identical outputs. The input outcomes are NEVER mutated.

    The result is DESCRIPTIVE. It makes no profitability, probability,
    or directional prediction claim.
    """

    def __init__(self, config: PerformanceAnalyticsConfig | None = None) -> None:
        self.config = config or PerformanceAnalyticsConfig()

    # ------------------------------------------------------------
    # PUBLIC API
    # ------------------------------------------------------------

    def analyze(
        self,
        outcomes: Iterable[HistoricalOutcome],
        label: str = "",
        metadata: Mapping[str, str] | None = None,
    ) -> HistoricalPerformanceAnalytics:
        """
        Aggregate ``outcomes`` into a
        :class:`HistoricalPerformanceAnalytics` result.

        ``label`` and ``metadata`` override the config's label /
        metadata when supplied (mirroring the Sprint 11G / 11W
        convention). The result is deterministic and descriptive.
        """

        outcome_list = list(outcomes)
        label = label if label else self.config.label
        meta = self._normalize_metadata(metadata, self.config.metadata)

        overall = _compute_statistics(outcome_list)
        breakdowns = tuple(
            _build_breakdown(outcome_list, dim) for dim in _DIMENSION_ORDER
        )

        analytics_id = self._analytics_id(outcome_list, label, meta)
        rationale = self._rationale(outcome_list, overall)

        return HistoricalPerformanceAnalytics(
            analytics_id=analytics_id,
            overall=overall,
            breakdowns=breakdowns,
            outcome_count=len(outcome_list),
            label=label,
            metadata=meta,
            rationale=rationale,
        )

    # ------------------------------------------------------------
    # INTERNALS
    # ------------------------------------------------------------

    @staticmethod
    def _normalize_metadata(
        override: Mapping[str, str] | None,
        fallback: tuple[tuple[str, str], ...],
    ) -> tuple[tuple[str, str], ...]:
        if override is None:
            return tuple(sorted(fallback))
        return tuple(sorted((str(k), str(v)) for k, v in override.items()))

    def _analytics_id(
        self,
        outcomes: Sequence[HistoricalOutcome],
        label: str,
        metadata: tuple[tuple[str, str], ...],
    ) -> str:
        """
        Deterministic analytics identifier.

        Computed from the canonical representation of the outcomes'
        identities (instrument / direction / evaluation timestamp /
        outcome status / realized R) plus label + metadata. The
        outcome identities are SORTED before hashing so the id is
        independent of input ordering (a shuffled input yields the same
        analytics). No wall-clock time, no nondeterminism.
        """

        identities = sorted(
            (
                {
                    "instrument": o.subject.instrument,
                    "direction": o.subject.direction,
                    "evaluation_timestamp": (
                        o.subject.evaluation_timestamp.isoformat()
                        if o.subject.evaluation_timestamp is not None
                        else None
                    ),
                    "outcome_status": o.outcome_status.name,
                    "realized_r": o.realized_r,
                }
                for o in outcomes
            ),
            key=lambda d: json.dumps(d, sort_keys=True, ensure_ascii=False),
        )
        payload = {
            "label": label,
            "metadata": [list(p) for p in metadata],
            "outcomes": identities,
        }
        try:
            canon = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            canon = str(payload)
        digest = hashlib.sha256(canon.encode("utf-8")).hexdigest()
        return f"perf-{digest[:16]}"

    def _rationale(
        self,
        outcomes: Sequence[HistoricalOutcome],
        stats: HistoricalPerformanceStatistics,
    ) -> str:
        if not outcomes:
            return (
                "Historical performance analytics aggregated no outcomes. "
                "Descriptive only; not predictive and not a guarantee of "
                "profitability."
            )
        return (
            f"Historical performance analytics aggregated {stats.total} "
            f"outcome(s): {stats.target_hits} target hit(s), "
            f"{stats.stop_hits} stop hit(s), {stats.expired} expired, "
            f"{stats.both_touched} ambiguous (both touched), "
            f"{stats.no_geometry} no-geometry, "
            f"{stats.insufficient_data} insufficient-data. "
            f"Valid R observations: {stats.valid_r_count}. "
            "Outcomes were evaluated forward-only by Sprint 11W (no future "
            "information influenced the decision at T); this analytics layer "
            "aggregates already-computed outcomes only. Descriptive only; not "
            "predictive and not a guarantee of profitability."
        )


__all__ = ["PerformanceAnalyticsEngine"]
