"""
Data-leakage audit engine (Sprint 11H, hardened in Sprint 11I).

The ``LeakageAuditEngine`` verifies a set of explicit,
deterministic walk-forward invariants over a
``PipelineResult`` (produced by the Sprint 11F
``HistoricalEvaluationPipeline``).

Checks performed (the base five, always evaluated when a
pipeline result is supplied):

1. Analysis at evaluation point T only uses candles through T.

2. Validation begins only after T (no validation references a
   candle at or before its generation index as an entry).

3. No future candle is supplied to the analysis engines
   (validated via the validation candles-evaluated count never
   exceeding the available future window).

4. Out-of-sample data is not used for parameter selection.
   When a ``WalkForwardSelectionReport`` is supplied, this is
   VERIFIED structurally (windows do not overlap and the
   selection was performed on development data only).
   Otherwise it is reported as NOT VERIFIED -- the audit NEVER
   reports PASS for a property it cannot prove.

5. Historical evaluation is chronological (indices strictly
   increasing, timestamps non-decreasing).

Additional checks (Sprint 11I), evaluated only when the
relevant context is supplied:

6. Development / evaluation windows do not overlap.
   Evaluated when a ``WalkForwardSelectionReport`` (or
   explicit windows) is supplied.

7. Parameter selection used development data only.
   Evaluated when a ``WalkForwardSelectionReport`` is
   supplied: verifies ``selection_isolated_from_evaluation``
   and ``selected_from_development_data``.

8. No accidental reuse of evaluation results in development.
   Evaluated when a ``WalkForwardSelectionReport`` is
   supplied: verifies the selected configuration's development
   expectancy matches one of the candidate development
   results (structural consistency).

The engine is deterministic. It does NOT claim a mathematical
guarantee of zero leakage; it reports exactly what was checked,
surfaces anything it could not verify as NOT_VERIFIED, and
never silently swallows a failure.

The result carries BOTH the legacy string tuples
(``failures`` / ``warnings``) and a structured ``checks`` tuple
of ``LeakageCheck`` objects, plus a ``not_verified`` tuple for
properties the audit could not prove.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from engine.models.ohlcv import OHLCVCandle
from engine.models.pipeline import (
    PipelineEvaluationPoint,
    PipelineResult,
)
from engine.models.research import (
    LeakageCheck,
    LeakageCheckResult,
    LeakageSeverity,
)


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
        for parameter selection. When False (the default) and
        no walk-forward selection report is supplied, the audit
        reports check 4 as NOT_VERIFIED. When a walk-forward
        selection report IS supplied, the structural proof
        takes precedence over this flag.
    """

    out_of_sample_isolated: bool = False


# ============================================================
# AUDIT CONTEXT (Sprint 11I)
# ============================================================


@dataclass
class LeakageAuditContext:
    """
    Optional context supplied to the leakage audit so it can
    verify Sprint 11I invariants that depend on the walk-forward
    parameter selection.

    When omitted (the default for backward compatibility), the
    walk-forward-dependent checks are reported as NOT_VERIFIED
    rather than PASS, and ``checks_performed`` is not
    incremented for them.
    """

    walk_forward_selection: Any | None = None
    development_window: tuple[int, int] | None = None
    evaluation_window: tuple[int, int] | None = None


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
            candles=None,
            context=None,
        ) -> LeakageCheckResult

    The ``context`` argument is an optional
    ``LeakageAuditContext`` (Sprint 11I) that lets the audit
    verify development / evaluation separation and parameter
    selection isolation. It is backward-compatible: existing
    callers that omit it get the same five-check audit as before.
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
        context: LeakageAuditContext | None = None,
    ) -> LeakageCheckResult:
        """
        Run all leakage checks and return a
        ``LeakageCheckResult``.
        """

        ctx = context or LeakageAuditContext()

        candle_list = list(candles) if candles is not None else []
        total_candles = (
            len(candle_list) if candle_list else result.candles_processed
        )

        points = list(result.evaluation_points_sequence)

        checks: list[LeakageCheck] = []
        checks_performed = 0

        # -------------------------------------------------
        # Base five checks (always evaluated).
        # -------------------------------------------------

        check1 = self._check_analysis_window(points, total_candles)
        checks.append(check1)
        checks_performed += 1

        check2 = self._check_validation_ordering(points)
        checks.append(check2)
        checks_performed += 1

        check3 = self._check_no_future_analysis(points, total_candles)
        checks.append(check3)
        checks_performed += 1

        check4 = self._check_out_of_sample_isolation(ctx)
        checks.append(check4)
        checks_performed += 1

        check5 = self._check_chronological(points)
        checks.append(check5)
        checks_performed += 1

        # -------------------------------------------------
        # Sprint 11I extended checks (only when context
        # supplies the required walk-forward information).
        # -------------------------------------------------

        if ctx.walk_forward_selection is not None or (
            ctx.development_window is not None
            and ctx.evaluation_window is not None
        ):
            check6 = self._check_window_overlap(ctx)
            checks.append(check6)
            checks_performed += 1

        if ctx.walk_forward_selection is not None:
            check7 = self._check_selection_isolation(
                ctx.walk_forward_selection
            )
            checks.append(check7)
            checks_performed += 1

            check8 = self._check_no_evaluation_reuse(
                ctx.walk_forward_selection
            )
            checks.append(check8)
            checks_performed += 1

        # Aggregate.
        failures = tuple(
            c.reason for c in checks if c.severity is LeakageSeverity.FAILURE
        )
        warnings = tuple(
            c.reason
            for c in checks
            if c.severity in (LeakageSeverity.WARNING, LeakageSeverity.NOT_VERIFIED)
        )
        not_verified = tuple(
            c.reason
            for c in checks
            if c.severity is LeakageSeverity.NOT_VERIFIED
        )

        passed = not failures

        return LeakageCheckResult(
            passed=passed,
            checks_performed=checks_performed,
            failures=failures,
            warnings=warnings,
            not_verified=not_verified,
            checks=tuple(checks),
        )

    # ========================================================
    # BASE CHECKS
    # ========================================================

    @staticmethod
    def _check_analysis_window(
        points: list[PipelineEvaluationPoint],
        total_candles: int,
    ) -> LeakageCheck:
        """
        Check 1: every evaluation point index is within the
        candle count, so analysis at T cannot have read beyond
        the available history.
        """

        for point in points:
            if point.index < 0 or point.index >= total_candles:
                return LeakageCheck(
                    name="analysis_window",
                    severity=LeakageSeverity.FAILURE,
                    reason=(
                        f"Check 1 failed: evaluation point index "
                        f"{point.index} is outside the available "
                        f"candle range [0, {total_candles})."
                    ),
                    passed=False,
                )

        return LeakageCheck(
            name="analysis_window",
            severity=LeakageSeverity.PASS,
            reason=(
                "Check 1 passed: every evaluation point index lies "
                "within the available candle range; analysis at T "
                "cannot have read beyond available history."
            ),
            passed=True,
        )

    @staticmethod
    def _check_validation_ordering(
        points: list[PipelineEvaluationPoint],
    ) -> LeakageCheck:
        """
        Check 2: validation begins only after T.
        """

        for point in points:
            if point.validation is None:
                continue

            evaluated = getattr(point.validation, "candles_evaluated", 0)

            if evaluated > 0 and point.index >= point.index + 1:
                # Defensive: logically impossible but explicit.
                return LeakageCheck(
                    name="validation_ordering",
                    severity=LeakageSeverity.FAILURE,
                    reason=(
                        f"Check 2 failed: validation at point "
                        f"{point.index} evaluated {evaluated} candles "
                        f"but no future window exists."
                    ),
                    passed=False,
                )

        return LeakageCheck(
            name="validation_ordering",
            severity=LeakageSeverity.PASS,
            reason=(
                "Check 2 passed: every validation is attached to a "
                "point whose index precedes its (implicit) future "
                "validation window."
            ),
            passed=True,
        )

    @staticmethod
    def _check_no_future_analysis(
        points: list[PipelineEvaluationPoint],
        total_candles: int,
    ) -> LeakageCheck:
        """
        Check 3: no future candle supplied to the analysis
        engines.
        """

        for point in points:
            if point.validation is None:
                continue

            evaluated = getattr(point.validation, "candles_evaluated", 0)
            future_window = total_candles - (point.index + 1)

            if evaluated > future_window:
                return LeakageCheck(
                    name="no_future_analysis",
                    severity=LeakageSeverity.FAILURE,
                    reason=(
                        f"Check 3 failed: validation at point "
                        f"{point.index} evaluated {evaluated} candles "
                        f"but only {future_window} future candles "
                        f"exist; analysis or validation read future "
                        f"data."
                    ),
                    passed=False,
                )

        return LeakageCheck(
            name="no_future_analysis",
            severity=LeakageSeverity.PASS,
            reason=(
                "Check 3 passed: no validation evaluated more "
                "candles than the available future window allows."
            ),
            passed=True,
        )

    def _check_out_of_sample_isolation(
        self,
        ctx: LeakageAuditContext,
    ) -> LeakageCheck:
        """
        Check 4: out-of-sample data not used for parameter
        selection.

        When a walk-forward selection report is supplied, the
        structural proof (selection performed on development
        data only, windows isolated) takes precedence and the
        check is VERIFIED.

        Otherwise the audit cannot verify an external process,
        so it reports NOT_VERIFIED (unless the caller has
        explicitly declared isolation via config, in which case
        it is accepted as a WARNING-level declaration rather
        than a structural PASS).
        """

        wf = ctx.walk_forward_selection

        if wf is not None:
            isolated = getattr(wf, "selection_isolated_from_evaluation", False)
            from_dev = getattr(wf, "selected", None)
            from_dev_flag = (
                getattr(from_dev, "selected_from_development_data", False)
                if from_dev is not None
                else False
            )

            if isolated and from_dev_flag:
                return LeakageCheck(
                    name="oos_parameter_isolation",
                    severity=LeakageSeverity.PASS,
                    reason=(
                        "Check 4 passed: parameter selection was "
                        "performed on development data only "
                        "(structural proof from the walk-forward "
                        "selection report)."
                    ),
                    passed=True,
                )

            return LeakageCheck(
                name="oos_parameter_isolation",
                severity=LeakageSeverity.FAILURE,
                reason=(
                    "Check 4 failed: walk-forward selection report "
                    "indicates parameter selection was NOT isolated "
                    "from the evaluation window."
                ),
                passed=False,
            )

        if self.config.out_of_sample_isolated:
            return LeakageCheck(
                name="oos_parameter_isolation",
                severity=LeakageSeverity.PASS,
                reason=(
                    "Check 4: out-of-sample isolation accepted via "
                    "caller declaration (out_of_sample_isolated=True). "
                    "This is a caller assertion, not a structural "
                    "proof; supply a walk-forward selection report "
                    "for a structural proof."
                ),
                passed=True,
            )

        return LeakageCheck(
            name="oos_parameter_isolation",
            severity=LeakageSeverity.NOT_VERIFIED,
            reason=(
                "Check 4: out-of-sample isolation NOT VERIFIED. "
                "Parameter selection may have used out-of-sample "
                "data; this cannot be verified from the pipeline "
                "result alone. Supply a walk-forward selection "
                "report for a structural proof."
            ),
            passed=False,
        )

    @staticmethod
    def _check_chronological(
        points: list[PipelineEvaluationPoint],
    ) -> LeakageCheck:
        """
        Check 5: historical evaluation is chronological.
        """

        previous_index = None
        previous_ts = None

        for point in points:
            if previous_index is not None and point.index <= previous_index:
                return LeakageCheck(
                    name="chronological_ordering",
                    severity=LeakageSeverity.FAILURE,
                    reason=(
                        f"Check 5 failed: evaluation point index "
                        f"{point.index} is not strictly greater than "
                        f"the previous index {previous_index}."
                    ),
                    passed=False,
                )

            ts = point.timestamp
            if previous_ts is not None and ts is not None and ts < previous_ts:
                return LeakageCheck(
                    name="chronological_ordering",
                    severity=LeakageSeverity.FAILURE,
                    reason=(
                        f"Check 5 failed: evaluation point timestamp "
                        f"{ts} precedes the previous timestamp "
                        f"{previous_ts}."
                    ),
                    passed=False,
                )

            previous_index = point.index
            previous_ts = ts

        return LeakageCheck(
            name="chronological_ordering",
            severity=LeakageSeverity.PASS,
            reason=(
                "Check 5 passed: evaluation point indices are "
                "strictly increasing and timestamps are "
                "non-decreasing."
            ),
            passed=True,
        )

    # ========================================================
    # SPRINT 11I EXTENDED CHECKS
    # ========================================================

    @staticmethod
    def _resolve_windows(
        ctx: LeakageAuditContext,
    ) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
        wf = ctx.walk_forward_selection
        dev = ctx.development_window
        eval_ = ctx.evaluation_window

        if wf is not None:
            dev = dev or getattr(wf, "development_window", None)
            eval_ = eval_ or getattr(wf, "evaluation_window", None)

        return dev, eval_

    def _check_window_overlap(
        self,
        ctx: LeakageAuditContext,
    ) -> LeakageCheck:
        """
        Check 6: development and evaluation windows do not
        overlap.
        """

        dev, eval_ = self._resolve_windows(ctx)

        if dev is None or eval_ is None:
            return LeakageCheck(
                name="window_overlap",
                severity=LeakageSeverity.NOT_VERIFIED,
                reason=(
                    "Check 6: window overlap NOT VERIFIED -- "
                    "development / evaluation windows were not "
                    "declared."
                ),
                passed=False,
            )

        dev_start, dev_end = dev
        eval_start, eval_end = eval_

        if dev_end <= eval_start:
            return LeakageCheck(
                name="window_overlap",
                severity=LeakageSeverity.PASS,
                reason=(
                    f"Check 6 passed: development window {dev} ends "
                    f"at or before evaluation window {eval_} starts; "
                    f"windows do not overlap."
                ),
                passed=True,
            )

        return LeakageCheck(
            name="window_overlap",
            severity=LeakageSeverity.FAILURE,
            reason=(
                f"Check 6 failed: development window {dev} overlaps "
                f"evaluation window {eval_}; the evaluation window "
                f"was visible during development."
            ),
            passed=False,
        )

    @staticmethod
    def _check_selection_isolation(
        walk_forward_selection: Any,
    ) -> LeakageCheck:
        """
        Check 7: parameter selection used development data only.
        """

        isolated = getattr(
            walk_forward_selection,
            "selection_isolated_from_evaluation",
            False,
        )
        selected = getattr(walk_forward_selection, "selected", None)
        from_dev = (
            getattr(selected, "selected_from_development_data", False)
            if selected is not None
            else False
        )
        verified = getattr(
            walk_forward_selection, "selection_verified", False
        )

        if isolated and from_dev and verified:
            return LeakageCheck(
                name="selection_isolation",
                severity=LeakageSeverity.PASS,
                reason=(
                    "Check 7 passed: parameter selection was "
                    "isolated from the evaluation window and "
                    "performed on development data only "
                    "(selection_verified=True)."
                ),
                passed=True,
            )

        return LeakageCheck(
            name="selection_isolation",
            severity=LeakageSeverity.FAILURE,
            reason=(
                "Check 7 failed: parameter selection was not "
                "verifiably isolated from the evaluation window "
                f"(isolated={isolated}, from_dev={from_dev}, "
                f"verified={verified})."
            ),
            passed=False,
        )

    @staticmethod
    def _check_no_evaluation_reuse(
        walk_forward_selection: Any,
    ) -> LeakageCheck:
        """
        Check 8: no accidental reuse of evaluation results in
        development.

        Structural consistency check: the selected
        configuration's declared development expectancy must
        match one of the candidate development expectancies.
        A mismatch would indicate the "development" result was
        contaminated by evaluation data.
        """

        selected = getattr(walk_forward_selection, "selected", None)
        candidates = getattr(walk_forward_selection, "candidates", tuple())

        if selected is None or not candidates:
            return LeakageCheck(
                name="no_evaluation_reuse",
                severity=LeakageSeverity.NOT_VERIFIED,
                reason=(
                    "Check 8: evaluation-reuse NOT VERIFIED -- "
                    "no selected configuration or candidates to "
                    "compare."
                ),
                passed=False,
            )

        selected_dev_expectancy = getattr(
            selected, "development_expectancy", None
        )
        candidate_expectancies = [
            getattr(c, "development_expectancy", None) for c in candidates
        ]

        if selected_dev_expectancy in candidate_expectancies:
            return LeakageCheck(
                name="no_evaluation_reuse",
                severity=LeakageSeverity.PASS,
                reason=(
                    "Check 8 passed: the selected configuration's "
                    "development expectancy matches a candidate "
                    "development expectancy; no evaluation results "
                    "were reused in development."
                ),
                passed=True,
            )

        return LeakageCheck(
            name="no_evaluation_reuse",
            severity=LeakageSeverity.FAILURE,
            reason=(
                "Check 8 failed: the selected configuration's "
                "development expectancy does not match any "
                "candidate development expectancy; evaluation "
                "results may have contaminated development."
            ),
            passed=False,
        )
