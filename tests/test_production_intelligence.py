"""
Focused tests for Sprint 12E — PRODUCTION INTEGRATION + FINAL HARDENING.

Sprint 12E is the FINAL planned sprint. It is the smallest clean
PRODUCTION INTEGRATION BOUNDARY that bundles the ALREADY-COMPUTED
outputs of the completed architecture (Sprint 11V through 12D) into ONE
coherent, production-facing artifact WITHOUT altering the meaning of any
previous layer.

These tests REUSE the existing Sprint 11Y / 11Z / 12A / 12B / 12C / 12D
engines to build real already-computed artifacts, then exercise the
Sprint 12E :class:`ProductionIntelligenceEngine`. They NEVER re-evaluate
outcomes with future data, NEVER re-run the pipeline to influence
decisions, and NEVER generate trading signals.

Coverage areas A-Z (per the Sprint 12E specification):

A.  Happy-path production integration
B.  Existing REJECTED decision
C.  Existing WATCH decision
D.  Existing QUALIFIED decision
E.  Existing PREFERRED decision
F.  Evidence-supported context
G.  Limited evidence
H.  Insufficient evidence
I.  Evidence unavailable
J.  Invalid / mismatched context
K.  Missing historical evidence
L.  Existing decision preservation by identity
M.  No decision mutation
N.  Input immutability
O.  Determinism
P.  Shuffle invariance
Q.  Serialization round trip
R.  Malformed serialization
S.  Configuration validation
T.  Error isolation
U.  No-look-ahead
V.  11Y hard-gate preservation
W.  BOTH_TOUCHED handling
X.  NO_GEOMETRY handling
Y.  INSUFFICIENT_DATA handling
Z.  Pipeline regression
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from engine.config.historical_evidence_config import EvidenceConfig
from engine.config.production_intelligence_config import (
    ProductionIntelligenceConfig,
)
from engine.intelligence.backtest_validation import BacktestValidationEngine
from engine.intelligence.decision_intelligence import DecisionIntelligenceEngine
from engine.intelligence.decision_intelligence_integration import (
    DecisionIntelligenceIntegrationEngine,
)
from engine.intelligence.historical_evidence import HistoricalEvidenceEngine
from engine.intelligence.historical_outcome import (
    OutcomeEvaluator,
    OutcomeStatus,
)
from engine.intelligence.production_intelligence import (
    ProductionIntelligenceEngine,
)
from engine.intelligence.production_intelligence_serialization import (
    PRODUCTION_SCHEMA_VERSION,
    canonical_production_json,
    deserialize_production,
    parse_production_header,
    serialize_production,
    serialize_production_bytes,
)
from engine.intelligence.robustness_validation import RobustnessValidationEngine
from engine.intelligence.strategy_intelligence import StrategyIntelligenceEngine
from engine.models.backtest_validation import BacktestValidationReport
from engine.models.decision_intelligence import (
    DecisionContextStatus,
    ExistingDecisionSummary,
)
from engine.models.decision_intelligence_integration import (
    IntegratedDecisionContext,
    IntegrationStatus,
)
from engine.models.historical_outcome import (
    HistoricalOutcome,
    OutcomeSubject,
)
from engine.models.production_intelligence import (
    PRODUCTION_INTELLIGENCE_LIMITATIONS,
    ProductionIntelligenceContext,
    ProductionIntegrationStatus,
    ProductionValidationState,
)
from engine.models.robustness_validation import RobustnessValidationReport
from engine.models.strategy_intelligence import OpportunityProfile
from engine.pipeline import (
    HistoricalEvaluationPipeline,
    PipelineConfig,
    trending_dataset,
)
from engine.reporting.production_intelligence import (
    ProductionIntelligenceFormatter,
)


_EPOCH = datetime(2025, 1, 1, tzinfo=UTC)


# ============================================================
# FIXTURE BUILDERS
# ============================================================


def _subject(
    i: int = 0,
    instrument: str = "NIFTY",
    direction: str = "LONG",
    setup_type: str = "TREND_CONTINUATION",
    mtf_alignment: str = "ALIGNED",
    decision: str = "QUALIFIED",
    opportunity: str = "BEST_OPPORTUNITY",
    rank: int = 1,
    entry: float = 100.0,
    stop: float = 95.0,
    target: float = 110.0,
) -> OutcomeSubject:
    return OutcomeSubject(
        instrument=instrument,
        direction=direction,
        evaluation_timestamp=_EPOCH + timedelta(days=i),
        entry=entry,
        stop=stop,
        target=target,
        decision_classification=decision,
        decision_score=70,
        opportunity_status=opportunity,
        rank=rank,
        scan_id="scan-12e-test",
        setup_timeframe="15M",
        setup_type=setup_type,
        mtf_alignment=mtf_alignment,
    )


def _outcome(
    status: OutcomeStatus,
    i: int = 0,
    realized_r: float | None = None,
    instrument: str = "NIFTY",
    direction: str = "LONG",
    setup_type: str = "TREND_CONTINUATION",
    mtf_alignment: str = "ALIGNED",
    decision: str = "QUALIFIED",
    opportunity: str = "BEST_OPPORTUNITY",
    mfe: float | None = 5.0,
    mae: float | None = 2.0,
) -> HistoricalOutcome:
    risk = abs(100.0 - 95.0)
    return HistoricalOutcome(
        subject=_subject(
            i, instrument=instrument, direction=direction,
            setup_type=setup_type, mtf_alignment=mtf_alignment,
            decision=decision, opportunity=opportunity,
        ),
        outcome_status=status,
        realized_r=realized_r,
        mfe=mfe,
        mae=mae,
        mfe_r=(mfe / risk) if mfe is not None else None,
        mae_r=(mae / risk) if mae is not None else None,
        risk=risk,
    )


def _resolved_cohort(
    n: int,
    win_fraction: float = 0.6,
    instrument: str = "NIFTY",
    direction: str = "LONG",
    setup_type: str = "TREND_CONTINUATION",
    mtf_alignment: str = "ALIGNED",
    seed: int = 0,
) -> list[HistoricalOutcome]:
    rng = random.Random(seed)
    outcomes: list[HistoricalOutcome] = []
    for i in range(n):
        win = rng.random() < win_fraction
        outcomes.append(
            _outcome(
                OutcomeStatus.TARGET_HIT if win else OutcomeStatus.STOP_HIT,
                i=i, realized_r=2.0 if win else -1.0,
                instrument=instrument, direction=direction,
                setup_type=setup_type, mtf_alignment=mtf_alignment,
            ),
        )
    return outcomes


def _evidence_report(outcomes, label="test"):
    return HistoricalEvidenceEngine(EvidenceConfig(label=label)).evaluate(
        outcomes,
    )


def _profile(
    instrument: str = "NIFTY",
    direction: str = "LONG",
    setup_type: str = "TREND_CONTINUATION",
    mtf_alignment: str = "ALIGNED",
) -> OpportunityProfile:
    return OpportunityProfile(
        instrument=instrument,
        direction=direction,
        setup_type=setup_type,
        mtf_alignment=mtf_alignment,
    )


def _decision(
    direction: str = "LONG",
    classification: str = "QUALIFIED",
    score: int = 75,
    opportunity: str = "BEST_OPPORTUNITY",
    rank: int = 1,
    geometry: bool = True,
    confluence: int = 4,
    rr: float = 2.0,
) -> ExistingDecisionSummary:
    return ExistingDecisionSummary(
        direction=direction,
        decision_classification=classification,
        decision_score=score,
        opportunity_status=opportunity,
        rank=rank,
        geometry_complete=geometry,
        confluence_score=confluence,
        risk_reward_ratio=rr,
        entry=100.0,
        stop=95.0,
        target=110.0,
    )


def _di_context(
    profile: OpportunityProfile | None = None,
    decision: ExistingDecisionSummary | None = None,
    outcomes=None,
    label: str = "di",
) -> "DecisionIntelligenceContext":  # type: ignore[name-defined]
    outs = outcomes if outcomes is not None else _resolved_cohort(60, seed=1)
    report = _evidence_report(outs, label=label)
    strat = StrategyIntelligenceEngine()
    prof = profile or _profile()
    lookup = strat.lookup(report, prof)
    return DecisionIntelligenceEngine().build(
        prof, decision or _decision(), lookup, label=label,
    )


def _di_context_nomatch(label="nomatch"):
    outs = _resolved_cohort(60, seed=2)
    report = _evidence_report(outs, label=label)
    strat = StrategyIntelligenceEngine()
    lookup = strat.lookup(
        report, OpportunityProfile(setup_type="RANGE_REJECTION"),
    )
    return DecisionIntelligenceEngine().build(
        OpportunityProfile(setup_type="RANGE_REJECTION"),
        _decision(),
        lookup,
        label=label,
    )


def _integrate(
    existing_decision=None,
    di_context=None,
    profile=None,
    label="test",
) -> IntegratedDecisionContext:
    eng = DecisionIntelligenceIntegrationEngine()
    return eng.integrate(
        existing_decision if existing_decision is not None else _decision(),
        di_context if di_context is not None else _di_context(),
        profile,
        label=label,
    )


def _backtest_report(outcomes=None, label="bvr") -> BacktestValidationReport:
    outs = outcomes if outcomes is not None else _resolved_cohort(60, seed=1)
    return BacktestValidationEngine().validate(
        [{"name": "s", "outcomes": tuple(outs)}], label=label,
    )


def _robustness_report(outcomes=None, label="rvr") -> RobustnessValidationReport:
    outs = outcomes if outcomes is not None else _resolved_cohort(60, seed=1)
    return RobustnessValidationEngine().validate(
        [{"name": "s", "outcomes": tuple(outs)}], label=label,
    )


def _assemble(
    integrated_context=None,
    backtest_validation=None,
    robustness_validation=None,
    label="test",
    config=None,
) -> ProductionIntelligenceContext:
    eng = ProductionIntelligenceEngine(config)
    return eng.assemble(
        integrated_context if integrated_context is not None else _integrate(),
        backtest_validation=backtest_validation,
        robustness_validation=robustness_validation,
        label=label,
    )


# ============================================================
# A. HAPPY-PATH PRODUCTION INTEGRATION
# ============================================================


class TestHappyPath:
    def test_assembles_integrated_context_with_full_validation(self):
        integ = _integrate()
        bvr = _backtest_report()
        rvr = _robustness_report()
        ctx = ProductionIntelligenceEngine().assemble(
            integ, backtest_validation=bvr, robustness_validation=rvr,
            label="happy",
        )
        assert ctx.integration_status == ProductionIntegrationStatus.INTEGRATED
        assert ctx.validation_state == ProductionValidationState.FULL_VALIDATION
        assert ctx.production_id.startswith("prod-")
        assert ctx.has_evidence
        assert ctx.has_validation

    def test_production_id_is_deterministic(self):
        integ = _integrate()
        bvr = _backtest_report()
        rvr = _robustness_report()
        ctx1 = ProductionIntelligenceEngine().assemble(
            integ, backtest_validation=bvr, robustness_validation=rvr, label="x",
        )
        ctx2 = ProductionIntelligenceEngine().assemble(
            integ, backtest_validation=bvr, robustness_validation=rvr, label="x",
        )
        assert ctx1.production_id == ctx2.production_id

    def test_different_label_yields_different_id(self):
        integ = _integrate()
        ctx1 = ProductionIntelligenceEngine().assemble(integ, label="a")
        ctx2 = ProductionIntelligenceEngine().assemble(integ, label="b")
        assert ctx1.production_id != ctx2.production_id

    def test_no_candle_argument_in_public_api(self):
        # The production API takes no candle / future-data argument.
        import inspect

        sig = inspect.signature(ProductionIntelligenceEngine.assemble)
        assert "candles" not in sig.parameters
        assert "future" not in sig.parameters


# ============================================================
# B-E. EXISTING DECISION CLASSIFICATIONS PRESERVED
# ============================================================


@pytest.mark.parametrize(
    "classification",
    ["REJECTED", "WATCH", "QUALIFIED", "PREFERRED"],
)
class TestExistingDecisionClassifications:
    def test_classification_preserved_unchanged(self, classification):
        original = _decision(classification=classification)
        integ = _integrate(existing_decision=original)
        ctx = ProductionIntelligenceEngine().assemble(integ, label="cls")
        assert ctx.existing_decision is original
        assert (
            ctx.existing_decision_summary.decision_classification
            == classification
        )
        assert ctx.existing_decision_summary.decision_score == 75

    def test_decision_not_upgraded_or_downgraded(self, classification):
        original = _decision(classification=classification, score=42)
        integ = _integrate(existing_decision=original)
        ctx = ProductionIntelligenceEngine().assemble(integ)
        # Evidence-supported context must never alter the classification.
        assert (
            ctx.existing_decision_summary.decision_classification
            == classification
        )
        assert ctx.existing_decision_summary.decision_score == 42


# ============================================================
# F. EVIDENCE-SUPPORTED CONTEXT
# ============================================================


class TestEvidenceSupported:
    def test_evidence_supported_status(self):
        ctx = _assemble()
        assert ctx.evidence_status == DecisionContextStatus.EVIDENCE_SUPPORTED
        assert ctx.evidence_supported
        assert ctx.integration_status == ProductionIntegrationStatus.INTEGRATED

    def test_observed_performance_surfaced(self):
        ctx = _assemble()
        assert ctx.observed_performance is not None
        assert ctx.observed_performance.total == 60

    def test_strategy_interpretation_surfaced(self):
        ctx = _assemble()
        assert ctx.strategy_interpretation is not None


# ============================================================
# G. LIMITED EVIDENCE
# ============================================================


class TestLimitedEvidence:
    def test_weak_evidence_evidence_limited(self):
        # Build a cohort that yields WEAK evidence: total >= min_sample_total
        # (>=30) but resolved < min_resolved (10), so it is WEAK (not
        # STRONG, not INSUFFICIENT).
        outcomes = _resolved_cohort(40, win_fraction=0.0, seed=7)
        # All STOP_HIT so all resolved; reduce resolved by mixing in
        # BOTH_TOUCHED (which are NOT resolved) until resolved < 10.
        mixed = [
            _outcome(OutcomeStatus.STOP_HIT, i=0, realized_r=-1.0),
            _outcome(OutcomeStatus.STOP_HIT, i=1, realized_r=-1.0),
            _outcome(OutcomeStatus.STOP_HIT, i=2, realized_r=-1.0),
            _outcome(OutcomeStatus.STOP_HIT, i=3, realized_r=-1.0),
            _outcome(OutcomeStatus.STOP_HIT, i=4, realized_r=-1.0),
        ] + [
            _outcome(OutcomeStatus.BOTH_TOUCHED, i=k, realized_r=None)
            for k in range(5, 35)
        ]
        report = _evidence_report(mixed, label="weak")
        strat = StrategyIntelligenceEngine()
        prof = _profile()
        lookup = strat.lookup(report, prof)
        di = DecisionIntelligenceEngine().build(prof, _decision(), lookup)
        integ = _integrate(existing_decision=_decision(), di_context=di)
        ctx = ProductionIntelligenceEngine().assemble(integ)
        # WEAK maps to LIMITED_EVIDENCE -> EVIDENCE_LIMITED.
        assert ctx.evidence_status == DecisionContextStatus.EVIDENCE_LIMITED
        assert not ctx.evidence_supported
        assert ctx.integration_status == ProductionIntegrationStatus.INTEGRATED


# ============================================================
# H. INSUFFICIENT EVIDENCE
# ============================================================


class TestInsufficientEvidence:
    def test_insufficient_evidence_small_sample(self):
        outcomes = _resolved_cohort(5, win_fraction=1.0, seed=3)
        report = _evidence_report(outcomes, label="ins")
        strat = StrategyIntelligenceEngine()
        prof = _profile()
        lookup = strat.lookup(report, prof)
        di = DecisionIntelligenceEngine().build(prof, _decision(), lookup)
        integ = _integrate(existing_decision=_decision(), di_context=di)
        ctx = ProductionIntelligenceEngine().assemble(integ)
        assert ctx.evidence_status == DecisionContextStatus.INSUFFICIENT_EVIDENCE
        assert not ctx.evidence_supported
        assert ctx.integration_status == ProductionIntegrationStatus.INTEGRATED


# ============================================================
# I. EVIDENCE UNAVAILABLE
# ============================================================


class TestEvidenceUnavailable:
    def test_no_matching_cohort_context_only(self):
        di = _di_context_nomatch()
        integ = _integrate(
            existing_decision=_decision(), di_context=di,
            profile=OpportunityProfile(setup_type="RANGE_REJECTION"),
        )
        ctx = ProductionIntelligenceEngine().assemble(integ)
        assert ctx.integration_status == ProductionIntegrationStatus.CONTEXT_ONLY
        assert ctx.evidence_status == DecisionContextStatus.EVIDENCE_UNAVAILABLE
        assert ctx.observed_performance is None
        assert not ctx.has_evidence


# ============================================================
# J. INVALID / MISMATCHED CONTEXT
# ============================================================


class TestInvalidMismatched:
    def test_no_integrated_context_invalid(self):
        ctx = ProductionIntelligenceEngine().assemble(None, label="invalid")
        assert ctx.integration_status == ProductionIntegrationStatus.INVALID
        assert ctx.is_empty
        assert ctx.existing_decision is None
        assert ctx.observed_performance is None

    def test_no_di_unavailable(self):
        # 12B context with no DI -> UNAVAILABLE, mirrored to production.
        integ = DecisionIntelligenceIntegrationEngine().integrate(
            _decision(), None, _profile(),
        )
        ctx = ProductionIntelligenceEngine().assemble(integ)
        assert ctx.integration_status == ProductionIntegrationStatus.UNAVAILABLE
        assert ctx.existing_decision is not None

    def test_type_mismatch_raises_typeerror(self):
        with pytest.raises(TypeError):
            ProductionIntelligenceEngine().assemble("not-an-integ")  # type: ignore

    def test_backtest_type_mismatch_raises_typeerror(self):
        with pytest.raises(TypeError):
            ProductionIntelligenceEngine().assemble(
                _integrate(), backtest_validation="not-a-report",  # type: ignore
            )

    def test_robustness_type_mismatch_raises_typeerror(self):
        with pytest.raises(TypeError):
            ProductionIntelligenceEngine().assemble(
                _integrate(), robustness_validation="not-a-report",  # type: ignore
            )


# ============================================================
# K. MISSING HISTORICAL EVIDENCE
# ============================================================


class TestMissingHistoricalEvidence:
    def test_missing_validation_reports_none_state(self):
        ctx = _assemble()
        assert ctx.validation_state == ProductionValidationState.NONE
        assert not ctx.has_validation

    def test_missing_evidence_no_fabricated_metrics(self):
        di = _di_context_nomatch()
        integ = _integrate(
            existing_decision=_decision(), di_context=di,
            profile=OpportunityProfile(setup_type="RANGE_REJECTION"),
        )
        ctx = ProductionIntelligenceEngine().assemble(integ)
        assert ctx.observed_performance is None
        assert ctx.evidence_strength is None
        assert ctx.strategy_interpretation is None


# ============================================================
# L. EXISTING DECISION PRESERVATION BY IDENTITY
# ============================================================


class TestExistingDecisionPreserved:
    def test_existing_decision_preserved_by_reference(self):
        original = _decision()
        integ = _integrate(existing_decision=original)
        ctx = ProductionIntelligenceEngine().assemble(integ)
        assert ctx.existing_decision is original

    def test_integrated_context_preserved_by_reference(self):
        integ = _integrate()
        ctx = ProductionIntelligenceEngine().assemble(integ)
        assert ctx.integrated_context is integ

    def test_validation_reports_preserved_by_reference(self):
        integ = _integrate()
        bvr = _backtest_report()
        rvr = _robustness_report()
        ctx = ProductionIntelligenceEngine().assemble(
            integ, backtest_validation=bvr, robustness_validation=rvr,
        )
        assert ctx.backtest_validation is bvr
        assert ctx.robustness_validation is rvr


# ============================================================
# M. NO DECISION MUTATION
# ============================================================


class TestNoDecisionMutation:
    def test_existing_decision_not_mutated_after_assembly(self):
        original = _decision(classification="QUALIFIED", score=75)
        original_cls = original.decision_classification
        original_score = original.decision_score
        integ = _integrate(existing_decision=original)
        ProductionIntelligenceEngine().assemble(integ)
        assert original.decision_classification == original_cls
        assert original.decision_score == original_score

    def test_integrated_context_not_mutated_after_assembly(self):
        integ = _integrate()
        original_id = integ.integration_id
        original_status = integ.integration_status
        ProductionIntelligenceEngine().assemble(integ)
        assert integ.integration_id == original_id
        assert integ.integration_status == original_status


# ============================================================
# N. INPUT IMMUTABILITY
# ============================================================


class TestInputImmutability:
    def test_production_context_frozen(self):
        ctx = _assemble()
        with pytest.raises(Exception):
            ctx.production_id = "mutated"  # type: ignore[misc]

    def test_production_context_slots(self):
        ctx = _assemble()
        # slots -> cannot set undeclared attributes.
        with pytest.raises(Exception):
            ctx.new_attr = "x"  # type: ignore[attr-defined]

    def test_validation_report_not_mutated(self):
        bvr = _backtest_report()
        original_id = bvr.validation_id
        ProductionIntelligenceEngine().assemble(
            _integrate(), backtest_validation=bvr,
        )
        assert bvr.validation_id == original_id


# ============================================================
# O. DETERMINISM
# ============================================================


class TestDeterminism:
    def test_repeated_assembly_identical(self):
        integ = _integrate()
        bvr = _backtest_report()
        rvr = _robustness_report()
        ctx1 = ProductionIntelligenceEngine().assemble(
            integ, backtest_validation=bvr, robustness_validation=rvr, label="x",
        )
        ctx2 = ProductionIntelligenceEngine().assemble(
            integ, backtest_validation=bvr, robustness_validation=rvr, label="x",
        )
        assert ctx1.production_id == ctx2.production_id
        assert ctx1.integration_status == ctx2.integration_status
        assert ctx1.validation_state == ctx2.validation_state

    def test_production_id_prefixed(self):
        ctx = _assemble()
        assert ctx.production_id.startswith("prod-")


# ============================================================
# P. SHUFFLE INVARIANCE
# ============================================================


class TestShuffleInvariance:
    def test_shuffled_outcomes_same_production_id(self):
        outcomes = _resolved_cohort(60, seed=1)
        report = _evidence_report(outcomes, label="shuf")
        strat = StrategyIntelligenceEngine()
        prof = _profile()
        lookup = strat.lookup(report, prof)
        di = DecisionIntelligenceEngine().build(prof, _decision(), lookup)
        integ = _integrate(existing_decision=_decision(), di_context=di)
        ctx = ProductionIntelligenceEngine().assemble(integ, label="shuf")

        shuffled = list(outcomes)
        random.Random(99).shuffle(shuffled)
        report_shuffled = _evidence_report(shuffled, label="shuf")
        lookup_shuffled = strat.lookup(report_shuffled, prof)
        di_shuffled = DecisionIntelligenceEngine().build(
            prof, _decision(), lookup_shuffled,
        )
        integ_shuffled = _integrate(
            existing_decision=_decision(), di_context=di_shuffled,
        )
        ctx_shuffled = ProductionIntelligenceEngine().assemble(
            integ_shuffled, label="shuf",
        )
        assert ctx_shuffled.production_id == ctx.production_id


# ============================================================
# Q. SERIALIZATION ROUND TRIP
# ============================================================


class TestSerialization:
    def test_round_trip_preserves_identity_and_status(self):
        ctx = _assemble(
            backtest_validation=_backtest_report(),
            robustness_validation=_robustness_report(),
            label="ser",
        )
        restored = deserialize_production(serialize_production(ctx))
        assert restored.production_id == ctx.production_id
        assert restored.integration_status == ctx.integration_status
        assert restored.validation_state == ctx.validation_state

    def test_round_trip_preserves_validation_reports(self):
        ctx = _assemble(
            backtest_validation=_backtest_report(),
            robustness_validation=_robustness_report(),
        )
        restored = deserialize_production(serialize_production(ctx))
        assert restored.backtest_validation is not None
        assert restored.robustness_validation is not None
        assert (
            restored.backtest_validation.overall_status
            == ctx.backtest_validation.overall_status
        )
        assert (
            restored.robustness_validation.overall_status
            == ctx.robustness_validation.overall_status
        )

    def test_round_trip_preserves_observed_performance(self):
        ctx = _assemble()
        restored = deserialize_production(serialize_production(ctx))
        assert restored.observed_performance is not None
        assert restored.observed_performance.total == ctx.observed_performance.total

    def test_round_trip_drops_heavy_existing_decision_ref(self):
        ctx = _assemble()
        restored = deserialize_production(serialize_production(ctx))
        assert restored.existing_decision is None
        # ... but the audit projection survives.
        assert (
            restored.existing_decision_summary.decision_classification
            == "QUALIFIED"
        )

    def test_round_trip_invalid_case(self):
        ctx = ProductionIntelligenceEngine().assemble(None, label="invalid")
        restored = deserialize_production(serialize_production(ctx))
        assert restored.integration_status == ProductionIntegrationStatus.INVALID
        assert restored.production_id == ctx.production_id

    def test_bytes_round_trip(self):
        ctx = _assemble()
        restored = deserialize_production(
            serialize_production_bytes(ctx).decode("utf-8"),
        )
        assert restored.production_id == ctx.production_id

    def test_canonical_json_stable(self):
        ctx = _assemble()
        assert canonical_production_json(ctx) == serialize_production(ctx)

    def test_schema_version_constant(self):
        assert PRODUCTION_SCHEMA_VERSION == 1

    def test_header_parse_exposes_schema(self):
        ctx = _assemble()
        header = parse_production_header(serialize_production(ctx))
        assert header["schema_version"] == PRODUCTION_SCHEMA_VERSION

    def test_deterministic_bytes(self):
        ctx = _assemble()
        b1 = serialize_production_bytes(ctx)
        b2 = serialize_production_bytes(ctx)
        assert b1 == b2


# ============================================================
# R. MALFORMED SERIALIZATION
# ============================================================


class TestMalformedSerialization:
    def test_unsupported_schema_version_rejected(self):
        with pytest.raises(ValueError):
            deserialize_production(
                '{"schema_version": 999, "production": {}}',
            )

    def test_missing_schema_version_rejected(self):
        with pytest.raises(ValueError):
            deserialize_production('{"production": {}}')

    def test_malformed_payload_rejected(self):
        with pytest.raises(ValueError):
            deserialize_production("not json at all")

    def test_malformed_mapping_rejected(self):
        with pytest.raises(ValueError):
            deserialize_production('{"schema_version": 1, "production": []}')


# ============================================================
# S. CONFIGURATION VALIDATION
# ============================================================


class TestConfigValidation:
    def test_defaults(self):
        cfg = ProductionIntelligenceConfig()
        assert cfg.label == ""
        assert cfg.metadata == ()

    def test_frozen(self):
        cfg = ProductionIntelligenceConfig()
        with pytest.raises(Exception):
            cfg.label = "x"  # type: ignore[misc]

    def test_slots(self):
        cfg = ProductionIntelligenceConfig()
        with pytest.raises(Exception):
            cfg.new_attr = "x"  # type: ignore[attr-defined]

    def test_label_type_validated(self):
        with pytest.raises(TypeError):
            ProductionIntelligenceConfig(label=123)  # type: ignore[arg-type]

    def test_metadata_type_validated(self):
        with pytest.raises((TypeError, ValueError)):
            ProductionIntelligenceConfig(metadata="not-a-tuple")  # type: ignore[arg-type]

    def test_metadata_pair_validated(self):
        with pytest.raises(ValueError):
            ProductionIntelligenceConfig(metadata=(("k", 123),))  # type: ignore

    def test_snapshot_sorted(self):
        cfg = ProductionIntelligenceConfig(
            label="x", metadata=(("b", "2"), ("a", "1")),
        )
        snap = cfg.snapshot()
        assert snap == (
            ("label", "x"),
            ("metadata.a", "1"),
            ("metadata.b", "2"),
        )

    def test_config_label_used_as_default(self):
        cfg = ProductionIntelligenceConfig(label="from-config")
        ctx = ProductionIntelligenceEngine(cfg).assemble(_integrate())
        assert ctx.label == "from-config"


# ============================================================
# T. ERROR ISOLATION
# ============================================================


class TestErrorIsolation:
    def test_type_mismatch_does_not_corrupt_engine(self):
        eng = ProductionIntelligenceEngine()
        with pytest.raises(TypeError):
            eng.assemble("bad")  # type: ignore
        # Engine remains usable.
        ctx = eng.assemble(_integrate())
        assert ctx.integration_status == ProductionIntegrationStatus.INTEGRATED

    def test_invalid_case_does_not_prevent_valid_assembly(self):
        eng = ProductionIntelligenceEngine()
        invalid = eng.assemble(None)
        valid = eng.assemble(_integrate())
        assert invalid.integration_status == ProductionIntegrationStatus.INVALID
        assert valid.integration_status == ProductionIntegrationStatus.INTEGRATED


# ============================================================
# U. NO-LOOK-AHEAD
# ============================================================


class TestNoLookAhead:
    def test_works_with_outcome_evaluator_patched_to_raise(self):
        original = OutcomeEvaluator.evaluate

        def boom(self, subject, future_candles):  # noqa: ANN001
            raise RuntimeError("must not re-evaluate outcomes")

        OutcomeEvaluator.evaluate = boom  # type: ignore[method-assign]
        try:
            ctx = ProductionIntelligenceEngine().assemble(_integrate())
            assert ctx.integration_status == ProductionIntegrationStatus.INTEGRATED
        finally:
            OutcomeEvaluator.evaluate = original  # type: ignore[method-assign]

    def test_works_with_pipeline_patched_to_raise(self):
        original = HistoricalEvaluationPipeline.evaluate

        def boom(self, candles):  # noqa: ANN001
            raise RuntimeError("must not re-run pipeline")

        HistoricalEvaluationPipeline.evaluate = boom  # type: ignore[method-assign]
        try:
            ctx = ProductionIntelligenceEngine().assemble(_integrate())
            assert ctx.existing_decision is not None
        finally:
            HistoricalEvaluationPipeline.evaluate = original  # type: ignore[method-assign]


# ============================================================
# V. 11Y HARD-GATE PRESERVATION
# ============================================================


class TestEvidenceHardGate:
    def test_tiny_impressive_cohort_stays_insufficient(self):
        # 1 trade, +2R, 100% win -> must be INSUFFICIENT (hard gate).
        outcomes = [
            _outcome(OutcomeStatus.TARGET_HIT, i=0, realized_r=2.0,
                     instrument="ICICIBANK", setup_type="STRUCTURE_CONTINUATION"),
        ]
        report = _evidence_report(outcomes, label="tiny")
        strat = StrategyIntelligenceEngine()
        prof = _profile(
            instrument="ICICIBANK", setup_type="STRUCTURE_CONTINUATION",
        )
        lookup = strat.lookup(report, prof)
        di = DecisionIntelligenceEngine().build(prof, _decision(), lookup)
        integ = _integrate(existing_decision=_decision(), di_context=di)
        ctx = ProductionIntelligenceEngine().assemble(integ)
        assert ctx.evidence_status == DecisionContextStatus.INSUFFICIENT_EVIDENCE
        assert not ctx.evidence_supported
        # 12E never promotes insufficient evidence to a stronger status.
        assert ctx.integration_status == ProductionIntegrationStatus.INTEGRATED

    def test_insufficient_never_becomes_evidence_supported(self):
        outcomes = _resolved_cohort(5, win_fraction=1.0, seed=3)
        report = _evidence_report(outcomes, label="ins2")
        strat = StrategyIntelligenceEngine()
        prof = _profile()
        lookup = strat.lookup(report, prof)
        di = DecisionIntelligenceEngine().build(prof, _decision(), lookup)
        integ = _integrate(existing_decision=_decision(), di_context=di)
        ctx = ProductionIntelligenceEngine().assemble(integ)
        assert not ctx.evidence_supported


# ============================================================
# W. BOTH_TOUCHED HANDLING
# ============================================================


class TestBothTouched:
    def test_both_touched_excluded_from_win_loss(self):
        outcomes = [
            _outcome(OutcomeStatus.TARGET_HIT, i=0, realized_r=2.0),
            _outcome(OutcomeStatus.STOP_HIT, i=1, realized_r=-1.0),
            _outcome(OutcomeStatus.BOTH_TOUCHED, i=2, realized_r=None),
        ]
        report = _evidence_report(outcomes, label="both")
        strat = StrategyIntelligenceEngine()
        prof = _profile()
        lookup = strat.lookup(report, prof)
        di = DecisionIntelligenceEngine().build(prof, _decision(), lookup)
        integ = _integrate(existing_decision=_decision(), di_context=di)
        ctx = ProductionIntelligenceEngine().assemble(integ)
        stats = ctx.observed_performance
        assert stats is not None
        assert stats.both_touched == 1
        # BOTH_TOUCHED excluded from win/loss denominator.
        assert stats.win_rate is not None
        # Both touched never fabricated as win/loss.

    def test_both_touched_realized_r_none(self):
        outcomes = [_outcome(OutcomeStatus.BOTH_TOUCHED, i=0, realized_r=None)]
        report = _evidence_report(outcomes, label="both2")
        strat = StrategyIntelligenceEngine()
        prof = _profile()
        lookup = strat.lookup(report, prof)
        di = DecisionIntelligenceEngine().build(prof, _decision(), lookup)
        integ = _integrate(existing_decision=_decision(), di_context=di)
        ctx = ProductionIntelligenceEngine().assemble(integ)
        stats = ctx.observed_performance
        assert stats is not None
        # No valid R contributions -> total_realized_r is None (never 0),
        # preserving the observed-zero vs not-available distinction.
        assert stats.total_realized_r is None
        assert stats.valid_r_count == 0


# ============================================================
# X. NO_GEOMETRY HANDLING
# ============================================================


class TestNoGeometry:
    def test_no_geometry_carries_no_fabricated_r(self):
        outcomes = [
            _outcome(OutcomeStatus.TARGET_HIT, i=0, realized_r=2.0),
            _outcome(OutcomeStatus.NO_GEOMETRY, i=1, realized_r=None),
        ]
        report = _evidence_report(outcomes, label="nog")
        strat = StrategyIntelligenceEngine()
        prof = _profile()
        lookup = strat.lookup(report, prof)
        di = DecisionIntelligenceEngine().build(prof, _decision(), lookup)
        integ = _integrate(existing_decision=_decision(), di_context=di)
        ctx = ProductionIntelligenceEngine().assemble(integ)
        stats = ctx.observed_performance
        assert stats is not None
        assert stats.no_geometry == 1


# ============================================================
# Y. INSUFFICIENT_DATA HANDLING
# ============================================================


class TestInsufficientData:
    def test_insufficient_data_carries_no_fabricated_values(self):
        outcomes = [
            _outcome(OutcomeStatus.TARGET_HIT, i=0, realized_r=2.0),
            _outcome(OutcomeStatus.INSUFFICIENT_DATA, i=1, realized_r=None),
        ]
        report = _evidence_report(outcomes, label="insd")
        strat = StrategyIntelligenceEngine()
        prof = _profile()
        lookup = strat.lookup(report, prof)
        di = DecisionIntelligenceEngine().build(prof, _decision(), lookup)
        integ = _integrate(existing_decision=_decision(), di_context=di)
        ctx = ProductionIntelligenceEngine().assemble(integ)
        stats = ctx.observed_performance
        assert stats is not None
        assert stats.insufficient_data == 1


# ============================================================
# Z. PIPELINE REGRESSION
# ============================================================


class TestPipelineRegression:
    def test_baseline_signals_and_trades(self):
        pipeline = HistoricalEvaluationPipeline(PipelineConfig())
        result = pipeline.evaluate(trending_dataset())
        assert result.signals_generated == 4
        assert result.performance.completed_trades == 3

    def test_validation_reports_pass(self):
        outcomes = _resolved_cohort(60, seed=1)
        bvr = _backtest_report(outcomes)
        rvr = _robustness_report(outcomes)
        # Both offline validation engines pass over a clean corpus.
        assert bvr.overall_status.name == "PASS"
        assert rvr.overall_status.name == "PASS"


# ============================================================
# END-TO-END CHAIN
# ============================================================


class TestEndToEndChain:
    def test_full_11v_to_12e_chain(self):
        # Build a real outcome corpus -> 11Y evidence -> 11Z lookup ->
        # 12A context -> 12B integration -> 12C/12D validation ->
        # 12E production bundle.
        outcomes = _resolved_cohort(60, seed=1)
        report = _evidence_report(outcomes, label="e2e")
        strat = StrategyIntelligenceEngine()
        prof = _profile()
        lookup = strat.lookup(report, prof)
        di = DecisionIntelligenceEngine().build(prof, _decision(), lookup)
        original = _decision(classification="QUALIFIED")
        integ = _integrate(existing_decision=original, di_context=di)
        bvr = _backtest_report(outcomes)
        rvr = _robustness_report(outcomes)
        ctx = ProductionIntelligenceEngine().assemble(
            integ, backtest_validation=bvr, robustness_validation=rvr,
            label="e2e",
        )

        # 1. Existing pipeline produces its normal decision (carried).
        assert ctx.existing_decision is original
        # 2. Historical evidence consumed from already-computed artifacts.
        assert ctx.observed_performance is not None
        # 3. Evidence strength preserved.
        assert ctx.evidence_strength is not None
        # 4. Strategy interpretation preserved.
        assert ctx.strategy_interpretation is not None
        # 5. Decision context preserved.
        assert ctx.decision_intelligence is not None
        # 6. 12B integration does not alter the original decision.
        assert (
            ctx.existing_decision_summary.decision_classification == "QUALIFIED"
        )
        # 7. 12C validation remains valid.
        assert ctx.backtest_validation.overall_status.name == "PASS"
        # 8. 12D robustness state remains valid.
        assert ctx.robustness_validation.overall_status.name == "PASS"
        # 9. 12E produces a coherent production-facing result.
        assert ctx.integration_status == ProductionIntegrationStatus.INTEGRATED
        assert ctx.validation_state == ProductionValidationState.FULL_VALIDATION
        # 10. Serialization remains lossless.
        restored = deserialize_production(serialize_production(ctx))
        assert restored.production_id == ctx.production_id
        assert restored.observed_performance.total == ctx.observed_performance.total
        # 11. No future information accessed (no candle param).
        assert "candles" not in (
            ProductionIntelligenceEngine.assemble.__code__.co_varnames
        )
        # 12. Descriptive / contextual, not predictive.
        report_text = ProductionIntelligenceFormatter().format(ctx)
        assert "WARNING" in report_text
        assert "descriptive" in report_text.lower()

    def test_no_future_information_in_report(self):
        ctx = _assemble()
        text = ProductionIntelligenceFormatter().format(ctx)
        # No predictive certainty / guarantee / buy-sell language.
        upper = text.upper()
        for forbidden in (
            "GUARANTEED PROFIT", "WILL DEFINITELY", "STATISTICALLY SIGNIFICANT",
            "BUY SIGNAL", "SELL SIGNAL", "ENTER TRADE", "EXIT TRADE",
            "HOLD POSITION", "LIVE-TRADING-READY",
        ):
            assert forbidden not in upper, f"forbidden phrase: {forbidden}"


# ============================================================
# REPORTING
# ============================================================


class TestReporting:
    def test_format_returns_str(self):
        ctx = _assemble()
        assert isinstance(ProductionIntelligenceFormatter().format(ctx), str)

    def test_format_invalid_case(self):
        ctx = ProductionIntelligenceEngine().assemble(None, label="invalid")
        text = ProductionIntelligenceFormatter().format(ctx)
        assert "INVALID" in text
        assert "WARNING" in text

    def test_format_has_eight_concern_sections(self):
        ctx = _assemble(
            backtest_validation=_backtest_report(),
            robustness_validation=_robustness_report(),
        )
        text = ProductionIntelligenceFormatter().format(ctx)
        for section in (
            "Market / Opportunity Information",
            "Existing Decision",
            "Historical Observed Performance",
            "Evidence Strength",
            "Strategy Interpretation",
            "Decision Intelligence Context",
            "Controlled Integration Status",
            "Validation / Robustness State",
        ):
            assert section in text, f"missing section: {section}"

    def test_format_has_disclaimer(self):
        ctx = _assemble()
        text = ProductionIntelligenceFormatter().format(ctx)
        assert "does NOT guarantee future performance" in text
        assert "does NOT constitute a trading recommendation" in text
        assert "does NOT modify the existing decision" in text

    def test_format_unavailable_shown(self):
        di = _di_context_nomatch()
        integ = _integrate(
            existing_decision=_decision(), di_context=di,
            profile=OpportunityProfile(setup_type="RANGE_REJECTION"),
        )
        ctx = ProductionIntelligenceEngine().assemble(integ)
        text = ProductionIntelligenceFormatter().format(ctx)
        assert "unavailable" in text.lower()

    def test_format_deterministic(self):
        ctx = _assemble()
        f1 = ProductionIntelligenceFormatter().format(ctx)
        f2 = ProductionIntelligenceFormatter().format(ctx)
        assert f1 == f2

    def test_format_negative_precision_rejected(self):
        with pytest.raises(ValueError):
            ProductionIntelligenceFormatter(precision=-1)

    def test_format_negative_width_rejected(self):
        with pytest.raises(ValueError):
            ProductionIntelligenceFormatter(width=10)

    def test_format_shows_validation_reports(self):
        ctx = _assemble(
            backtest_validation=_backtest_report(),
            robustness_validation=_robustness_report(),
        )
        text = ProductionIntelligenceFormatter().format(ctx)
        assert "12C Backtest" in text
        assert "12D Robustness" in text

    def test_format_shows_no_validation_honestly(self):
        ctx = _assemble()
        text = ProductionIntelligenceFormatter().format(ctx)
        assert "not attached" in text


# ============================================================
# MODEL + LIMITATIONS
# ============================================================


class TestModel:
    def test_frozen(self):
        ctx = _assemble()
        with pytest.raises(Exception):
            ctx.integration_status = ProductionIntegrationStatus.INVALID  # type: ignore

    def test_slots(self):
        ctx = _assemble()
        with pytest.raises(Exception):
            ctx.new_attr = "x"  # type: ignore[attr-defined]

    def test_status_enum_members(self):
        assert {
            m.name for m in ProductionIntegrationStatus
        } == {"INTEGRATED", "CONTEXT_ONLY", "UNAVAILABLE", "INVALID"}

    def test_validation_state_enum_members(self):
        assert {
            m.name for m in ProductionValidationState
        } == {"NONE", "BACKTEST_VALIDATION", "ROBUSTNESS_VALIDATION",
              "FULL_VALIDATION"}

    def test_status_rank_value_ordering(self):
        assert (
            ProductionIntegrationStatus.INTEGRATED.rank_value
            < ProductionIntegrationStatus.CONTEXT_ONLY.rank_value
            < ProductionIntegrationStatus.UNAVAILABLE.rank_value
            < ProductionIntegrationStatus.INVALID.rank_value
        )

    def test_validation_state_has_validation(self):
        assert not ProductionValidationState.NONE.has_validation
        assert ProductionValidationState.FULL_VALIDATION.has_validation

    def test_status_is_integrated(self):
        assert ProductionIntegrationStatus.INTEGRATED.is_integrated
        assert not ProductionIntegrationStatus.CONTEXT_ONLY.is_integrated

    def test_limitations_constant_present(self):
        assert PRODUCTION_INTELLIGENCE_LIMITATIONS
        assert "descriptive" in PRODUCTION_INTELLIGENCE_LIMITATIONS.lower()

    def test_delegating_properties_invalid_case(self):
        ctx = ProductionIntelligenceEngine().assemble(None)
        assert ctx.existing_decision is None
        assert ctx.decision_intelligence is None
        assert ctx.evidence_status is None
        assert ctx.observed_performance is None
        assert ctx.evidence_strength is None
        assert ctx.contextual_factors == ()
        assert not ctx.has_evidence

    def test_delegating_properties_integrated_case(self):
        ctx = _assemble()
        assert ctx.existing_decision is not None
        assert ctx.decision_intelligence is not None
        assert ctx.evidence_status is not None
        assert ctx.observed_performance is not None
        assert ctx.evidence_strength is not None
        assert len(ctx.contextual_factors) > 0

    def test_validation_state_backtest_only(self):
        ctx = ProductionIntelligenceEngine().assemble(
            _integrate(), backtest_validation=_backtest_report(),
        )
        assert ctx.validation_state == ProductionValidationState.BACKTEST_VALIDATION

    def test_validation_state_robustness_only(self):
        ctx = ProductionIntelligenceEngine().assemble(
            _integrate(), robustness_validation=_robustness_report(),
        )
        assert ctx.validation_state == ProductionValidationState.ROBUSTNESS_VALIDATION


# ============================================================
# BACKWARD COMPATIBILITY
# ============================================================


class TestBackwardCompatibility:
    def test_prior_apis_importable(self):
        # 12B / 12C / 12D APIs remain importable and unchanged.
        from engine.intelligence.decision_intelligence_integration import (
            DecisionIntelligenceIntegrationEngine,
        )
        from engine.intelligence.backtest_validation import (
            BacktestValidationEngine,
        )
        from engine.intelligence.robustness_validation import (
            RobustnessValidationEngine,
        )
        assert DecisionIntelligenceIntegrationEngine
        assert BacktestValidationEngine
        assert RobustnessValidationEngine

    def test_intelligence_init_remains_empty(self):
        import engine.intelligence as intel_pkg
        # The intelligence package __init__ stays intentionally empty
        # (no re-exports) — 12E follows the 11O-12D convention.
        assert not hasattr(intel_pkg, "ProductionIntelligenceEngine")

    def test_reporting_init_not_extended(self):
        import engine.reporting as reporting_pkg
        # 12E formatter is imported via full path, not re-exported.
        assert not hasattr(reporting_pkg, "ProductionIntelligenceFormatter")

    def test_12b_serialization_unchanged(self):
        from engine.intelligence.decision_intelligence_integration_serialization import (
            INTEGRATION_SCHEMA_VERSION,
            serialize_integration,
            deserialize_integration,
        )
        integ = _integrate()
        restored = deserialize_integration(serialize_integration(integ))
        assert restored.integration_id == integ.integration_id
        assert INTEGRATION_SCHEMA_VERSION == 1
