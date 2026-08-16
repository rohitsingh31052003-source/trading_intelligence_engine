"""
Production INTEGRATION + FINAL HARDENING engine (Sprint 12E).

:class:`ProductionIntelligenceEngine` bundles the ALREADY-COMPUTED
outputs of the completed architecture (Sprint 11V through 12D) into ONE
coherent, inspectable, production-facing artifact WITHOUT altering the
meaning of any previous layer.

It is the FINAL planned sprint of the current architecture. It is NOT
another intelligence / scoring layer and NOT a new orchestration
package. It is the smallest clean PRODUCTION INTEGRATION BOUNDARY.

    11V -> 11W -> 11X -> 11Y -> 11Z -> 12A -> 12B -> 12C -> 12D -> 12E

DESIGN PRINCIPLE — reuse, do not re-invent:

The engine REUSES the Sprint 12B
:class:`IntegratedDecisionContext` (which itself embeds the reused 12A
decision-intelligence context, the reused 11Z strategy assessment, the
reused 11Y evidence strength and the reused 11X statistics). It
OPTIONALLY attaches the ALREADY-COMPUTED Sprint 12C
:class:`BacktestValidationReport` and / or Sprint 12D
:class:`RobustnessValidationReport` as OFFLINE / HISTORICAL validation
state. It does NOT recompute statistics, re-classify evidence,
re-interpret strategy, rebuild cohort matching, re-evaluate outcomes,
re-read candles, re-run the pipeline, re-run validation or use future
information. It only BUNDLES.

DESIGN PRINCIPLE — the existing decision is AUTHORITATIVE:

The engine retains the 12B integrated context BY REFERENCE and NEVER
modifies it (and therefore never modifies the original existing
decision it carries). The production integration status is MIRRORED from
the reused 12B :class:`IntegrationStatus` (never recomputed, never
overridden). There is NO BUY / SELL / ENTER / EXIT / HOLD
recommendation, NO probability, NO score adjustment, NO hidden weight,
NO re-ranking and NO re-selection.

DESIGN PRINCIPLE — historical vs live boundary:

The production runtime NEVER runs historical replay against future
candles. The validation reports attached here are PRE-COMPUTED OFFLINE
artifacts (produced by the 12C / 12D engines over historical outcome
corpora); 12E merely REFERENCES them. The engine's public API takes NO
candle / future-market-data argument. The point-in-time correctness
established in 11V / 11W is preserved unchanged.

DESIGN PRINCIPLE — honest fallbacks:

* No integrated context supplied (``None``) -> ``INVALID``; nothing
  fabricated.
* Integrated context present but 12B status ``UNAVAILABLE`` /
  ``CONTEXT_ONLY`` -> surfaced unchanged; no evidence fabricated.
* No validation reports supplied -> ``ProductionValidationState.NONE``;
  the production result notes that no offline validation state is
  attached (never a fake PASS).
* Validation reports carry their own honest statuses (inherited from
  12C / 12D); 12E never converts a SKIPPED / UNAVAILABLE into a PASS.

DESIGN PRINCIPLE — deterministic:

Identical inputs always produce identical outputs. No wall-clock time,
no randomness, no unordered iteration. The production id hashes the
canonical identity (12B integration id + validation ids + validation
state + label + metadata); because the embedded 12B integration id is
already shuffle-invariant (via 11Y / 11Z), the production id is
shuffle-invariant for equivalent evidence.

DESIGN PRINCIPLE — no leakage:

The engine consumes ALREADY-COMPUTED artifacts. Its public API takes NO
candle / future-market-data argument. It never inspects future market
candles, never re-evaluates outcomes, never calls the outcome evaluator,
never re-runs the pipeline, and never modifies the historical replay
semantics established in 11V / 11W.

This is intelligence / integration, NOT a new orchestration package.
``intelligence/__init__.py`` stays intentionally empty; import via full
paths, e.g. ``from engine.intelligence.production_intelligence import
ProductionIntelligenceEngine``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from engine.config.production_intelligence_config import (
    ProductionIntelligenceConfig,
)
from engine.models.backtest_validation import BacktestValidationReport
from engine.models.decision_intelligence_integration import (
    IntegratedDecisionContext,
    IntegrationStatus,
)
from engine.models.production_intelligence import (
    PRODUCTION_INTELLIGENCE_LIMITATIONS,
    ProductionIntegrationStatus,
    ProductionIntelligenceContext,
    ProductionValidationState,
)
from engine.models.robustness_validation import RobustnessValidationReport


# ============================================================
# STATUS MIRROR
# ============================================================

#: Fixed one-to-one mirror from the reused Sprint 12B
#: :class:`IntegrationStatus` to the Sprint 12E
#: :class:`ProductionIntegrationStatus`. The production status is
#: MIRRORED (never recomputed) so the authority contract established in
#: 12B cannot be weakened or overridden. This mapping is intentionally
#: NOT configurable.
_INTEGRATION_STATUS_MIRROR: dict[IntegrationStatus, ProductionIntegrationStatus] = {
    IntegrationStatus.INTEGRATED: ProductionIntegrationStatus.INTEGRATED,
    IntegrationStatus.CONTEXT_ONLY: ProductionIntegrationStatus.CONTEXT_ONLY,
    IntegrationStatus.UNAVAILABLE: ProductionIntegrationStatus.UNAVAILABLE,
    IntegrationStatus.INVALID: ProductionIntegrationStatus.INVALID,
}


def _mirror_status(integrated: IntegratedDecisionContext | None) -> ProductionIntegrationStatus:
    """Mirror the reused 12B integration status; never recompute."""

    if integrated is None:
        return ProductionIntegrationStatus.INVALID
    return _INTEGRATION_STATUS_MIRROR[integrated.integration_status]


def _validation_state(
    backtest: BacktestValidationReport | None,
    robustness: RobustnessValidationReport | None,
) -> ProductionValidationState:
    """Determine which offline validation reports are attached."""

    has_backtest = backtest is not None
    has_robustness = robustness is not None
    if has_backtest and has_robustness:
        return ProductionValidationState.FULL_VALIDATION
    if has_backtest:
        return ProductionValidationState.BACKTEST_VALIDATION
    if has_robustness:
        return ProductionValidationState.ROBUSTNESS_VALIDATION
    return ProductionValidationState.NONE


# ============================================================
# DETERMINISTIC ID
# ============================================================


def _production_id(
    integrated: IntegratedDecisionContext | None,
    backtest: BacktestValidationReport | None,
    robustness: RobustnessValidationReport | None,
    validation_state: ProductionValidationState,
    status: ProductionIntegrationStatus,
    label: str,
    metadata: tuple[tuple[str, str], ...],
) -> str:
    """Deterministic production identifier (``"prod-"`` + sha256[:16])."""

    # The 12B integration id is already deterministic and shuffle-
    # invariant (via Sprint 11Y / 11Z), so embedding it makes the
    # production id shuffle-invariant for equivalent evidence. The 12C /
    # 12D validation ids are likewise deterministic.
    payload = {
        "integration_id": (
            integrated.integration_id if integrated is not None else None
        ),
        "backtest_validation_id": (
            backtest.validation_id if backtest is not None else None
        ),
        "robustness_validation_id": (
            robustness.validation_id if robustness is not None else None
        ),
        "validation_state": validation_state.name,
        "production_integration_status": status.name,
        "label": label,
        "metadata": [list(p) for p in metadata],
    }
    try:
        canon = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        canon = str(payload)
    digest = hashlib.sha256(canon.encode("utf-8")).hexdigest()
    return f"prod-{digest[:16]}"


# ============================================================
# RATIONALE + LIMITATIONS
# ============================================================


_STATUS_RATIONALE: dict[ProductionIntegrationStatus, str] = {
    ProductionIntegrationStatus.INTEGRATED: (
        "A 12B integrated decision context with available historical "
        "evidence was bundled into a coherent production-facing artifact. "
        "The existing decision is preserved without alteration; the "
        "bundled context is descriptive and does not upgrade, downgrade, "
        "replace or otherwise modify the existing decision."
    ),
    ProductionIntegrationStatus.CONTEXT_ONLY: (
        "A 12B integrated decision context was bundled, but it carries no "
        "available historical evidence (no matching cohort). The "
        "intelligence is informational context only; it does not "
        "constitute supportive evidence and does not alter the existing "
        "decision."
    ),
    ProductionIntegrationStatus.UNAVAILABLE: (
        "A 12B context was supplied but no decision intelligence was "
        "attached. No evidence is fabricated; the existing decision "
        "stands unchanged and the production artifact records that no "
        "decision intelligence is available."
    ),
    ProductionIntegrationStatus.INVALID: (
        "No 12B integrated decision context was supplied, so a coherent "
        "production-facing artifact could not be assembled. The "
        "production result carries an explicit invalid state; no "
        "decision intelligence context is fabricated."
    ),
}


def _rationale(
    integrated: IntegratedDecisionContext | None,
    backtest: BacktestValidationReport | None,
    robustness: RobustnessValidationReport | None,
    validation_state: ProductionValidationState,
    status: ProductionIntegrationStatus,
    label: str,
) -> str:
    """Build the deterministic, descriptive production rationale."""

    parts: list[str] = []

    if label:
        parts.append(f"Production run: {label}.")

    if integrated is not None:
        summary = integrated.existing_decision_summary
        if summary.has_decision:
            parts.append(
                f"Existing decision: classification "
                f"{summary.decision_classification or 'none'}, score "
                f"{summary.decision_score}, direction "
                f"{summary.direction or 'none'}. The existing decision is "
                f"preserved WITHOUT alteration (retained by reference via "
                f"the reused 12B integration context).",
            )
        else:
            parts.append(
                "Existing decision: none carried by the 12B context.",
            )
        parts.append(
            f"Reused 12B integration: id {integrated.integration_id}, "
            f"integration status {integrated.integration_status.name}.",
        )
        if integrated.has_evidence and integrated.evidence_strength is not None:
            parts.append(
                f"Historical evidence: strength "
                f"{integrated.evidence_strength.name}, strategy "
                f"interpretation "
                f"{(integrated.strategy_interpretation.name if integrated.strategy_interpretation else 'unavailable')}.",
            )
        elif integrated.decision_intelligence is not None:
            parts.append(
                "Decision intelligence attached but no available evidence "
                "(no matching historical cohort).",
            )
        else:
            parts.append(
                "No decision intelligence was attached to the 12B context.",
            )
    else:
        parts.append(
            "No 12B integrated decision context was supplied.",
        )

    parts.append(f"Production integration status: {status.name}.")
    parts.append(_STATUS_RATIONALE[status])

    if validation_state.has_validation:
        attached: list[str] = []
        if backtest is not None:
            attached.append(
                f"12C backtest validation (id {backtest.validation_id}, "
                f"overall {backtest.overall_status.name})",
            )
        if robustness is not None:
            attached.append(
                f"12D robustness validation (id {robustness.validation_id}, "
                f"overall {robustness.overall_status.name})",
            )
        parts.append(
            f"Offline validation state: {validation_state.name} — "
            + "; ".join(attached)
            + ". These are PRE-COMPUTED OFFLINE artifacts, referenced "
            "by the production artifact; the production runtime did not "
            "re-run any validation."
        )
    else:
        parts.append(
            "Offline validation state: NONE — no pre-computed validation "
            "report was attached. This is reported honestly; it is never a "
            "fake PASS.",
        )

    parts.append(
        "Production intelligence is a coherent bundle of already-computed "
        "descriptive artifacts; it is NOT a trading signal, NOT a "
        "predictive guarantee, and does NOT modify the existing decision."
    )
    return " ".join(parts)


def _limitations(
    integrated: IntegratedDecisionContext | None,
    validation_state: ProductionValidationState,
) -> str:
    """Build the deterministic, descriptive production limitations."""

    parts: list[str] = [
        PRODUCTION_INTELLIGENCE_LIMITATIONS,
        "The production integration boundary is a CONTEXT / AUDIT bundle; "
        "it does NOT replace the existing scoring, signal generation, "
        "decision classification, ranking, opportunity selection, risk "
        "geometry or execution logic. The existing decision is retained by "
        "reference (via the reused 12B context) and is NEVER modified by "
        "the production integration.",
        "The production integration status is MIRRORED from the reused "
        "Sprint 12B IntegrationStatus; it is never recomputed, never "
        "overridden and never made configurable, so the established "
        "authority contract cannot be weakened.",
        "Historical evidence is OFFLINE / DESCRIPTIVE: the production "
        "runtime never runs historical replay against future candles. Any "
        "attached validation reports are pre-computed offline artifacts "
        "referenced by the production artifact.",
        "No statistical hypothesis test was performed.",
    ]
    if integrated is None:
        parts.append(
            "No 12B integrated decision context was supplied; the "
            "production result carries no integrated decision.",
        )
    else:
        if not integrated.has_evidence:
            parts.append(
                "The bundled 12B context carries no available historical "
                "evidence; observed performance, evidence strength and "
                "strategy interpretation are unavailable (never "
                "fabricated).",
            )
        else:
            di = integrated.decision_intelligence
            if di is not None and di.observed_performance is not None:
                stats = di.observed_performance
                if stats.both_touched > 0:
                    parts.append(
                        f"{stats.both_touched} ambiguous BOTH_TOUCHED "
                        "outcome(s) are excluded from win/loss and R "
                        "aggregates (never a fabricated win/loss).",
                    )
                if stats.no_geometry > 0:
                    parts.append(
                        f"{stats.no_geometry} NO_GEOMETRY outcome(s) "
                        "carry no fabricated R values.",
                    )
                if stats.insufficient_data > 0:
                    parts.append(
                        f"{stats.insufficient_data} INSUFFICIENT_DATA "
                        "outcome(s) carry no directional conclusion.",
                    )
    if not validation_state.has_validation:
        parts.append(
            "No offline validation report was attached; the production "
            "artifact notes the absence of validation state honestly.",
        )
    return " ".join(parts)


# ============================================================
# ENGINE
# ============================================================


class ProductionIntelligenceEngine:
    """
    Bundle the ALREADY-COMPUTED outputs of the completed architecture
    (Sprint 11V through 12D) into ONE coherent, production-facing
    artifact.

    Public API:

        assemble(integrated_context, backtest_validation,
                 robustness_validation, label, metadata)
            -> ProductionIntelligenceContext

    The engine is stateless across calls: identical inputs always
    produce identical outputs. The integrated context / validation
    reports are NEVER mutated. The result is DESCRIPTIVE. It makes no
    profitability, probability, directional prediction, statistical-
    significance, live-trading-readiness, or trading-recommendation
    claim, and it does NOT modify the existing decision / scoring
    logic. Its public API takes NO candle / future-market-data argument.
    """

    def __init__(
        self,
        config: ProductionIntelligenceConfig | None = None,
    ) -> None:
        self.config = config or ProductionIntelligenceConfig()

    # ------------------------------------------------------------
    # PUBLIC API
    # ------------------------------------------------------------

    def assemble(
        self,
        integrated_context: IntegratedDecisionContext | None,
        backtest_validation: BacktestValidationReport | None = None,
        robustness_validation: RobustnessValidationReport | None = None,
        label: str = "",
        metadata: Mapping[str, str] | None = None,
    ) -> ProductionIntelligenceContext:
        """
        Assemble a coherent production-facing artifact from
        ALREADY-COMPUTED artifacts.

        Reuses the 12B integrated context verbatim (retained by
        reference) and optionally attaches the 12C / 12D validation
        reports by reference. Never recomputes statistics, re-classifies
        evidence, re-interprets strategy, re-evaluates outcomes,
        re-reads candles, re-runs the pipeline, re-runs validation or
        modifies the existing decision. Mirrors the 12B integration
        status into the production status and produces explicit
        rationale / limitations / audit information.
        """

        self._validate_inputs(
            integrated_context, backtest_validation, robustness_validation,
        )
        lbl = label if label else self.config.label
        meta = self._normalize_metadata(metadata, self.config.metadata)

        status = _mirror_status(integrated_context)
        validation_state = _validation_state(
            backtest_validation, robustness_validation,
        )
        rationale = _rationale(
            integrated_context,
            backtest_validation,
            robustness_validation,
            validation_state,
            status,
            lbl,
        )
        limitations = _limitations(integrated_context, validation_state)
        production_id = _production_id(
            integrated_context,
            backtest_validation,
            robustness_validation,
            validation_state,
            status,
            lbl,
            meta,
        )
        return ProductionIntelligenceContext(
            production_id=production_id,
            integrated_context=integrated_context,
            backtest_validation=backtest_validation,
            robustness_validation=robustness_validation,
            integration_status=status,
            validation_state=validation_state,
            rationale=rationale,
            limitations=limitations,
            label=lbl,
            metadata=meta,
        )

    # ------------------------------------------------------------
    # INTERNALS
    # ------------------------------------------------------------

    @staticmethod
    def _validate_inputs(
        integrated_context: Any,
        backtest_validation: Any,
        robustness_validation: Any,
    ) -> None:
        """Type-check the supplied artifacts; fail safely and explicitly."""

        if integrated_context is not None and not isinstance(
            integrated_context, IntegratedDecisionContext,
        ):
            raise TypeError(
                "integrated_context must be a Sprint 12B "
                "IntegratedDecisionContext (or None), got "
                f"{type(integrated_context).__name__}.",
            )
        if backtest_validation is not None and not isinstance(
            backtest_validation, BacktestValidationReport,
        ):
            raise TypeError(
                "backtest_validation must be a Sprint 12C "
                "BacktestValidationReport (or None), got "
                f"{type(backtest_validation).__name__}.",
            )
        if robustness_validation is not None and not isinstance(
            robustness_validation, RobustnessValidationReport,
        ):
            raise TypeError(
                "robustness_validation must be a Sprint 12D "
                "RobustnessValidationReport (or None), got "
                f"{type(robustness_validation).__name__}.",
            )

    @staticmethod
    def _normalize_metadata(
        override: Mapping[str, str] | None,
        fallback: tuple[tuple[str, str], ...],
    ) -> tuple[tuple[str, str], ...]:
        if override is None:
            return fallback
        return tuple(sorted((str(k), str(v)) for k, v in override.items()))


__all__ = ["ProductionIntelligenceEngine"]
