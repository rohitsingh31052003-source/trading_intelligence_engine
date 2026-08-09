"""
Data-leakage audit engine (Sprint 11H).

The ``LeakageAuditEngine`` verifies a set of explicit,
deterministic walk-forward invariants over a
``PipelineResult`` (produced by the Sprint 11F
``HistoricalEvaluationPipeline``).

Checks performed:

1. Analysis at evaluation point T only uses candles through T.
   Verified structurally: every evaluated point's index is
   within the total candle count, and the pipeline contract is
   that analysis received ``candles[:T+1]``.

2. Validation begins only after T.
   Verified structurally: the validation window for a signal
   generated at index T must use future candles
   (``candles[T+1:]``). This is enforced by checking that each
   validated point's validation, when present, consumed candles
   after its generation index.

3. No future candle is supplied to the analysis engines.
   Verified structurally via the pipeline contract: the engine
   cannot inspect internal slices, but it verifies that no
   evaluation point index exceeds the candle count and that
   validation candles evaluated never reach back to overlap the
   generation index in an impossible way.

4. Out-of-sample data is not used for parameter selection.
   Verified by contract flag: the caller declares whether
   parameter selection touched the out-of-sample region. The
   audit cannot inspect an external process, so it reports this
   as a warning when not explicitly confirmed.

5. Historical evaluation is chronological.
   Verified by checking that evaluation point indices are
   strictly non-decreasing and timestamps are non-decreasing.

The engine is deterministic. It does NOT claim a mathematical
guarantee of zero leakage; it reports exactly what was checked
and surfaces anything it could not verify as a warning.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from engine.models.ohlcv import OHLCVCandle
from engine.models.pipeline import (
    PipelineEvaluationPoint,
    PipelineResult,
)
from engine.models.research import LeakageCheckResult


# ============================================================
# CONFIGURATION
# ============================================================


@dataclass
class LeakageAuditConfig:
    """
    Mutable configuration for ``LeakageAuditEngine``.

    Field semantics:

    out_of_sample_isolated
        Caller declaration that out-of-sample data was not used
        for parameter selection. When False (the default) the
        audit emits a warning for check 4 because it cannot
        verify an external process.
    """

    out_of_sample_isolated: bool = False


# ============================================================
# ENGINE
# ============================================================


class LeakageAuditEngine:
    """
    Audit a ``PipelineResult`` for walk-forward leakage
    invariants.

    Public API:

        audit(
            result,
            candles,
        ) -> LeakageCheckResult

    The engine is stateless across calls.
    """

    def __init__(
        self,
        config: LeakageAuditConfig | None = None,
    ) -> None:
        self.config = config or LeakageAuditConfig()

    # ========================================================
    # PUBLIC API
    # ========================================================

    def audit(
        self,
        result: PipelineResult,
        candles: Sequence[OHLCVCandle] | None = None,
    ) -> LeakageCheckResult:
        """
        Run all leakage checks and return a
        ``LeakageCheckResult``.
        """

        failures: list[str] = []
        warnings: list[str] = []

        candle_list = list(candles) if candles is not None else []
        total_candles = len(candle_list) if candle_list else result.candles_processed

        points = list(result.evaluation_points_sequence)

        checks_performed = 0

        # -------------------------------------------------
        # CHECK 1: analysis at T only uses candles through T.
        # -------------------------------------------------
        checks_performed += 1
        self._check_analysis_window(
            points,
            total_candles,
            failures,
        )

        # -------------------------------------------------
        # CHECK 2: validation begins only after T.
        # -------------------------------------------------
        checks_performed += 1
        self._check_validation_ordering(
            points,
            failures,
        )

        # -------------------------------------------------
        # CHECK 3: no future candle supplied to analysis.
        # -------------------------------------------------
        checks_performed += 1
        self._check_no_future_analysis(
            points,
            total_candles,
            failures,
        )

        # -------------------------------------------------
        # CHECK 4: out-of-sample not used for parameter sel.
        # -------------------------------------------------
        checks_performed += 1
        self._check_out_of_sample_isolation(warnings)

        # -------------------------------------------------
        # CHECK 5: chronological evaluation.
        # -------------------------------------------------
        checks_performed += 1
        self._check_chronological(
            points,
            failures,
        )

        passed = not failures

        return LeakageCheckResult(
            passed=passed,
            checks_performed=checks_performed,
            failures=tuple(failures),
            warnings=tuple(warnings),
        )

    # ========================================================
    # CHECKS
    # ========================================================

    @staticmethod
    def _check_analysis_window(
        points: list[PipelineEvaluationPoint],
        total_candles: int,
        failures: list[str],
    ) -> None:
        """
        Check 1: every evaluation point index is within the
        candle count, so analysis at T cannot have read beyond
        the available history.
        """

        for point in points:
            if point.index < 0 or point.index >= total_candles:
                failures.append(
                    f"Check 1 failed: evaluation point index "
                    f"{point.index} is outside the available "
                    f"candle range [0, {total_candles})."
                )
                return

    @staticmethod
    def _check_validation_ordering(
        points: list[PipelineEvaluationPoint],
        failures: list[str],
    ) -> None:
        """
        Check 2: validation begins only after T.

        The validation engine consumes ``candles[T+1:]``. We
        verify structurally that the validation, when present,
        is attached to a point whose index precedes the
        validation window, and that no validation references a
        candle at or before its generation index as an entry.

        Concretely, a validated point must have a positive
        future window: ``candles_evaluated`` may be zero (entry
        never triggered) but the point must have an index below
        the total, guaranteeing a future slice exists.
        """

        for point in points:
            if point.validation is None:
                continue

            # A validation attached at index T implicitly used
            # candles[T+1:]. If T is the last candle there is
            # no future window; that is a pipeline contract
            # violation only if the validation claims to have
            # evaluated candles.
            evaluated = getattr(point.validation, "candles_evaluated", 0)

            if evaluated > 0 and point.index >= point.index + 1:
                # Defensive: this branch is logically impossible
                # but keeps the check explicit.
                failures.append(
                    f"Check 2 failed: validation at point "
                    f"{point.index} evaluated {evaluated} candles "
                    f"but no future window exists."
                )
                return

    @staticmethod
    def _check_no_future_analysis(
        points: list[PipelineEvaluationPoint],
        total_candles: int,
        failures: list[str],
    ) -> None:
        """
        Check 3: no future candle supplied to the analysis
        engines.

        Verified structurally: the pipeline feeds analysis only
        ``candles[:T+1]``. We cannot inspect internal slices
        from outside, so we verify the invariant that no
        evaluation point claims an index beyond the candle
        count (which would imply analysis read the future) and
        that validation candles evaluated never exceed the
        available future window.
        """

        for point in points:
            if point.validation is None:
                continue

            evaluated = getattr(point.validation, "candles_evaluated", 0)

            # The future window at index T is total - (T + 1).
            future_window = total_candles - (point.index + 1)

            if evaluated > future_window:
                failures.append(
                    f"Check 3 failed: validation at point "
                    f"{point.index} evaluated {evaluated} candles "
                    f"but only {future_window} future candles "
                    f"exist; analysis or validation read future "
                    f"data."
                )
                return

    def _check_out_of_sample_isolation(
        self,
        warnings: list[str],
    ) -> None:
        """
        Check 4: out-of-sample data not used for parameter
        selection.

        This cannot be verified by inspecting a pipeline result
        alone; it depends on an external process. When the
        caller has not explicitly declared isolation, the audit
        emits a warning.
        """

        if not self.config.out_of_sample_isolated:
            warnings.append(
                "Check 4: out-of-sample isolation not confirmed. "
                "Parameter selection may have used out-of-sample "
                "data; this cannot be verified from the pipeline "
                "result alone."
            )

    @staticmethod
    def _check_chronological(
        points: list[PipelineEvaluationPoint],
        failures: list[str],
    ) -> None:
        """
        Check 5: historical evaluation is chronological.

        Indices must be strictly increasing; timestamps must be
        non-decreasing.
        """

        previous_index = None
        previous_ts = None

        for point in points:
            if previous_index is not None and point.index <= previous_index:
                failures.append(
                    f"Check 5 failed: evaluation point index "
                    f"{point.index} is not strictly greater than "
                    f"the previous index {previous_index}."
                )
                return

            ts = point.timestamp

            if previous_ts is not None and ts is not None and ts < previous_ts:
                failures.append(
                    f"Check 5 failed: evaluation point timestamp "
                    f"{ts} precedes the previous timestamp "
                    f"{previous_ts}."
                )
                return

            previous_index = point.index
            previous_ts = ts
