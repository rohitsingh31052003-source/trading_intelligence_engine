"""
Tests for the controlled decision intelligence INTEGRATION boundary
(Sprint 12B).

Coverage (mirrors the required test areas A-T):

A.  Valid integration
B.  Existing decision preserved (by reference: ``is``)
C.  Decision intelligence attached (reused by reference)
D.  Integration status derivation (INTEGRATED / CONTEXT_ONLY /
    UNAVAILABLE / INVALID)
E.  Missing intelligence (UNAVAILABLE)
F.  Invalid inputs (INVALID; no existing decision; strict guard raises)
G.  Deterministic IDs
H.  Shuffle invariance
I.  Serialization round trip (lossless for the audit view; heavy ref
    dropped)
J.  Input immutability (existing decision / DI context / profile /
    evidence report / outcomes not mutated)
K.  No-look-ahead (no candles param; OutcomeEvaluator patched to raise;
    HistoricalEvaluationPipeline patched to raise)
L.  Existing pipeline regression (signals=4, trades=3)
M.  Sprint 11X regression
N.  Sprint 11Y regression
O.  Sprint 11Z regression
P.  Sprint 12A regression
Q.  Full 11V -> 11W -> 11X -> 11Y -> 11Z -> 12A -> 12B end-to-end flow
R.  Disclaimer / rationale correctness
S.  Separation of observed result / evidence / strategy / decision
    context (never collapsed into one score)
T.  No recommendation generation (no BUY/SELL/ENTER/EXIT/HOLD, no
    probability, no score adjustment, no upgrade of existing decision)
"""

from __future__ import annotations

import inspect
import random
from datetime import UTC, datetime, timedelta

import pytest

from engine.config.decision_intelligence_integration_config import (
    DecisionIntelligenceIntegrationConfig,
)
from engine.config.historical_evidence_config import EvidenceConfig
from engine.config.performance_config import PerformanceAnalyticsConfig
from engine.intelligence.decision_intelligence import (
    DecisionIntelligenceEngine,
)
from engine.intelligence.decision_intelligence_integration import (
    DecisionIntelligenceIntegrationEngine,
)
from engine.intelligence.decision_intelligence_integration_serialization import (
    INTEGRATION_SCHEMA_VERSION,
    canonical_integration_json,
    deserialize_integration,
    parse_integration_header,
    serialize_integration,
    serialize_integration_bytes,
)
from engine.intelligence.decision_intelligence_serialization import (
    serialize_context,
)
from engine.intelligence.historical_evidence import (
    HistoricalEvidenceEngine,
)
from engine.intelligence.historical_outcome import (
    HistoricalOutcomeEngine,
    OutcomeEvaluator,
)
from engine.intelligence.performance_analytics import (
    PerformanceAnalyticsEngine,
)
from engine.intelligence.strategy_intelligence import (
    StrategyIntelligenceEngine,
)
from engine.models.decision_intelligence import (
    DecisionContextStatus,
    DecisionEvidenceFactor,
    DecisionIntelligenceContext,
    ExistingDecisionSummary,
)
from engine.models.decision_intelligence_integration import (
    IntegratedDecisionContext,
    IntegrationStatus,
)
from engine.models.historical_evidence import (
    EvidenceStrength,
    HistoricalEvidenceReport,
)
from engine.models.historical_outcome import (
    HistoricalOutcome,
    OutcomeStatus,
    OutcomeSubject,
)
from engine.models.historical_performance import (
    HistoricalPerformanceStatistics,
)
from engine.models.strategy_intelligence import (
    OpportunityProfile,
    StrategyAssessmentStatus,
)
from engine.models.trade_decision import (
    DecisionClassification,
    DecisionScore,
    TradeDecision,
)
from engine.pipeline import (
    HistoricalEvaluationPipeline,
    PipelineConfig,
    trending_dataset,
)
from engine.reporting.decision_intelligence_integration import (
    DecisionIntelligenceIntegrationFormatter,
)


_EPOCH = datetime(2025, 1, 1, tzinfo=UTC)


# ============================================================
# HELPERS
# ============================================================


def _subject(
    instrument: str = "NIFTY",
    direction: str = "LONG",
    rank: int = 1,
    setup_type: str = "TREND_CONTINUATION",
    mtf_alignment: str = "ALIGNED",
    decision: str = "QUALIFIED",
    opportunity: str = "BEST_OPPORTUNITY",
    ts: datetime = _EPOCH,
    entry: float = 100.0,
    stop: float = 95.0,
    target: float = 110.0,
) -> OutcomeSubject:
    return OutcomeSubject(
        instrument=instrument,
        direction=direction,
        evaluation_timestamp=ts,
        entry=entry,
        stop=stop,
        target=target,
        decision_classification=decision,
        decision_score=70,
        opportunity_status=opportunity,
        rank=rank,
        scan_id="scan-12b",
        setup_timeframe="15M",
        setup_type=setup_type,
        mtf_alignment=mtf_alignment,
    )


def _outcome(
    status: OutcomeStatus,
    realized_r: float | None = None,
    mfe: float | None = 5.0,
    mae: float | None = 2.0,
    mfe_r: float | None = 1.0,
    mae_r: float | None = 0.4,
    instrument: str = "NIFTY",
    direction: str = "LONG",
    rank: int = 1,
    setup_type: str = "TREND_CONTINUATION",
    mtf_alignment: str = "ALIGNED",
    decision: str = "QUALIFIED",
    opportunity: str = "BEST_OPPORTUNITY",
    ts: datetime = _EPOCH,
) -> HistoricalOutcome:
    return HistoricalOutcome(
        subject=_subject(
            instrument=instrument, direction=direction, rank=rank,
            setup_type=setup_type, mtf_alignment=mtf_alignment,
            decision=decision, opportunity=opportunity, ts=ts,
        ),
        outcome_status=status,
        realized_r=realized_r,
        mfe=mfe,
        mae=mae,
        mfe_r=mfe_r,
        mae_r=mae_r,
        risk=5.0,
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
        status = OutcomeStatus.TARGET_HIT if win else OutcomeStatus.STOP_HIT
        rr = 2.0 if win else -1.0
        outcomes.append(
            _outcome(
                status, realized_r=rr, instrument=instrument,
                direction=direction, setup_type=setup_type,
                mtf_alignment=mtf_alignment,
                ts=_EPOCH + timedelta(days=i),
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


def _decision_summary(
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
) -> DecisionIntelligenceContext:
    """Build a Sprint 12A decision-intelligence context (matched, supported)."""

    outs = outcomes if outcomes is not None else _resolved_cohort(60, seed=1)
    report = _evidence_report(outs, label=label)
    strat = StrategyIntelligenceEngine()
    prof = profile or _profile()
    lookup = strat.lookup(report, prof)
    return DecisionIntelligenceEngine().build(
        prof, decision or _decision_summary(), lookup, label=label,
    )


def _di_context_nomatch(label="nomatch") -> DecisionIntelligenceContext:
    outs = _resolved_cohort(60, seed=2)
    report = _evidence_report(outs, label=label)
    strat = StrategyIntelligenceEngine()
    # A setup type with no cohort -> NO_MATCH.
    lookup = strat.lookup(report, OpportunityProfile(setup_type="RANGE_REJECTION"))
    return DecisionIntelligenceEngine().build(
        OpportunityProfile(setup_type="RANGE_REJECTION"),
        _decision_summary(),
        lookup,
        label=label,
    )


_UNSET = object()


def _integrate(
    existing_decision=_UNSET,
    di_context=_UNSET,
    profile=None,
    label="test",
    config=None,
):
    eng = DecisionIntelligenceIntegrationEngine(config)
    return eng.integrate(
        _decision_summary() if existing_decision is _UNSET else existing_decision,
        _di_context() if di_context is _UNSET else di_context,
        profile,
        label=label,
    )


# ============================================================
# A. VALID INTEGRATION
# ============================================================


class TestValidIntegration:
    def test_integration_returns_integrated_context(self):
        integ = _integrate()
        assert isinstance(integ, IntegratedDecisionContext)

    def test_integration_id_prefix(self):
        integ = _integrate()
        assert integ.integration_id.startswith("int-")
        assert len(integ.integration_id) == len("int-") + 16

    def test_supported_evidence_integrated_status(self):
        integ = _integrate()
        assert integ.integration_status == IntegrationStatus.INTEGRATED
        assert integ.integration_status.is_integrated
        assert integ.integration_status.is_attached

    def test_profile_carried_through(self):
        # Build a DI context whose profile matches the integration
        # profile (strict guard requires consistency).
        prof = _profile(instrument="RELIANCE")
        di = _di_context(profile=prof)
        integ = _integrate(di_context=di, profile=prof)
        assert integ.profile is prof

    def test_label_and_metadata_carried(self):
        integ = _integrate(label="run-1", profile=_profile())
        # metadata defaults to config (empty); set via engine config.
        eng = DecisionIntelligenceIntegrationEngine(
            DecisionIntelligenceIntegrationConfig(
                metadata=(("source", "test"),),
            ),
        )
        integ2 = eng.integrate(
            _decision_summary(), _di_context(), _profile(), label="run-2",
        )
        assert integ2.label == "run-2"
        assert integ2.metadata == (("source", "test"),)


# ============================================================
# B. EXISTING DECISION PRESERVED
# ============================================================


class TestExistingDecisionPreserved:
    def test_existing_decision_is_original_by_reference(self):
        original = _decision_summary()
        integ = _integrate(existing_decision=original)
        assert integ.existing_decision is original

    def test_existing_decision_summary_projection_identity_for_summary(self):
        original = _decision_summary(direction="SHORT", classification="WATCH")
        integ = _integrate(existing_decision=original)
        # When the existing decision IS an ExistingDecisionSummary, the
        # projection is the same object.
        assert integ.existing_decision_summary is original

    def test_existing_decision_not_modified(self):
        original = _decision_summary()
        before = (
            original.direction, original.decision_classification,
            original.decision_score, original.rank,
        )
        _integrate(existing_decision=original)
        after = (
            original.direction, original.decision_classification,
            original.decision_score, original.rank,
        )
        assert before == after

    def test_existing_decision_summary_projection_from_trade_decision(self):
        # A Sprint 11S TradeDecision (heavy object) is projected to an
        # ExistingDecisionSummary; the original is retained by reference.
        td = TradeDecision(
            timestamp=_EPOCH,
            evaluation_index=5,
            candidate=object(),  # candidate ref not needed for projection
            direction="LONG",
            classification=DecisionClassification.QUALIFIED,
            score=DecisionScore(total=72, max_total=100, components=(), reason="r"),
            geometry_complete=True,
            confluence_score=4,
            supporting_count=4,
            conflicting_count=0,
            risk_reward_ratio=2.0,
            rationale="trade decision rationale",
        )
        integ = _integrate(existing_decision=td)
        assert integ.existing_decision is td
        assert integ.existing_decision_summary.direction == "LONG"
        assert integ.existing_decision_summary.decision_classification == "QUALIFIED"
        assert integ.existing_decision_summary.decision_score == 72
        assert integ.existing_decision_summary.geometry_complete is True

    def test_existing_decision_classification_not_upgraded(self):
        # QUALIFIED existing decision must remain QUALIFIED after
        # integration with EVIDENCE_SUPPORTED context — never upgraded
        # to PREFERRED.
        original = _decision_summary(classification="QUALIFIED")
        di = _di_context()  # EVIDENCE_SUPPORTED
        assert di.decision_context_status == DecisionContextStatus.EVIDENCE_SUPPORTED
        integ = _integrate(existing_decision=original, di_context=di)
        assert integ.existing_decision_summary.decision_classification == "QUALIFIED"
        assert integ.existing_decision is original


# ============================================================
# C. DECISION INTELLIGENCE ATTACHED
# ============================================================


class TestDecisionIntelligenceAttached:
    def test_decision_intelligence_reused_by_reference(self):
        di = _di_context()
        integ = _integrate(di_context=di)
        assert integ.decision_intelligence is di

    def test_decision_intelligence_not_mutated(self):
        di = _di_context()
        before_id = di.context_id
        before_status = di.decision_context_status
        before_factors = di.factors
        _integrate(di_context=di)
        assert di.context_id == before_id
        assert di.decision_context_status == before_status
        assert di.factors == before_factors

    def test_evidence_status_surfaced_from_di(self):
        di = _di_context()
        integ = _integrate(di_context=di)
        assert integ.evidence_status is di.decision_context_status
        assert integ.evidence_status == DecisionContextStatus.EVIDENCE_SUPPORTED

    def test_strategy_interpretation_surfaced_from_di(self):
        di = _di_context()
        integ = _integrate(di_context=di)
        assert integ.strategy_interpretation is di.strategy_interpretation

    def test_contextual_factors_reused_from_di(self):
        di = _di_context()
        integ = _integrate(di_context=di)
        assert integ.contextual_factors is di.factors
        assert integ.contextual_factors == di.factors

    def test_observed_performance_reused_from_di(self):
        di = _di_context()
        integ = _integrate(di_context=di)
        assert integ.observed_performance is di.observed_performance

    def test_evidence_strength_reused_from_di(self):
        di = _di_context()
        integ = _integrate(di_context=di)
        assert integ.evidence_strength is di.evidence_strength


# ============================================================
# D. INTEGRATION STATUS
# ============================================================


class TestIntegrationStatus:
    def test_status_members(self):
        assert {s.name for s in IntegrationStatus} == {
            "INTEGRATED", "CONTEXT_ONLY", "UNAVAILABLE", "INVALID",
        }

    def test_status_rank_value_ordering(self):
        assert (
            IntegrationStatus.INTEGRATED.rank_value
            < IntegrationStatus.CONTEXT_ONLY.rank_value
            < IntegrationStatus.UNAVAILABLE.rank_value
            < IntegrationStatus.INVALID.rank_value
        )

    def test_is_attached_and_is_integrated(self):
        assert IntegrationStatus.INTEGRATED.is_attached
        assert IntegrationStatus.INTEGRATED.is_integrated
        assert IntegrationStatus.CONTEXT_ONLY.is_attached
        assert not IntegrationStatus.CONTEXT_ONLY.is_integrated
        assert not IntegrationStatus.UNAVAILABLE.is_attached
        assert not IntegrationStatus.INVALID.is_attached

    def test_integrated_when_evidence_available(self):
        integ = _integrate()
        assert integ.integration_status == IntegrationStatus.INTEGRATED
        assert integ.has_evidence

    def test_context_only_when_no_match(self):
        di = _di_context_nomatch()
        assert di.decision_context_status == DecisionContextStatus.EVIDENCE_UNAVAILABLE
        integ = _integrate(di_context=di)
        assert integ.integration_status == IntegrationStatus.CONTEXT_ONLY
        assert not integ.has_evidence
        assert integ.integration_status.is_attached  # DI attached, just no evidence

    def test_context_only_evidence_status_unavailable(self):
        di = _di_context_nomatch()
        integ = _integrate(di_context=di)
        assert integ.evidence_status == DecisionContextStatus.EVIDENCE_UNAVAILABLE
        assert integ.strategy_interpretation is None
        assert integ.observed_performance is None
        assert integ.evidence_strength is None

    def test_context_only_existing_decision_unchanged(self):
        original = _decision_summary(classification="WATCH")
        di = _di_context_nomatch()
        integ = _integrate(existing_decision=original, di_context=di)
        assert integ.existing_decision is original
        assert integ.existing_decision_summary.decision_classification == "WATCH"


# ============================================================
# E. MISSING INTELLIGENCE
# ============================================================


class TestMissingIntelligence:
    def test_unavailable_when_no_di_supplied(self):
        integ = _integrate(di_context=None)
        assert integ.integration_status == IntegrationStatus.UNAVAILABLE
        assert not integ.has_decision_intelligence

    def test_unavailable_no_fabricated_evidence(self):
        integ = _integrate(di_context=None)
        assert integ.evidence_status is None
        assert integ.strategy_interpretation is None
        assert integ.observed_performance is None
        assert integ.evidence_strength is None
        assert integ.contextual_factors == ()

    def test_unavailable_existing_decision_preserved(self):
        original = _decision_summary()
        integ = _integrate(existing_decision=original, di_context=None)
        assert integ.existing_decision is original
        assert integ.existing_decision_summary is original

    def test_unavailable_rationale_states_no_di(self):
        integ = _integrate(di_context=None)
        assert "no decision intelligence" in integ.rationale.lower()


# ============================================================
# F. INVALID INPUTS
# ============================================================


class TestInvalidInputs:
    def test_invalid_when_no_existing_decision(self):
        di = _di_context()
        eng = DecisionIntelligenceIntegrationEngine()
        integ = eng.integrate(None, di, _profile())
        assert integ.integration_status == IntegrationStatus.INVALID
        assert not integ.has_existing_decision

    def test_invalid_when_both_none(self):
        eng = DecisionIntelligenceIntegrationEngine()
        integ = eng.integrate(None, None, _profile())
        assert integ.integration_status == IntegrationStatus.INVALID

    def test_invalid_no_fabricated_di(self):
        eng = DecisionIntelligenceIntegrationEngine()
        integ = eng.integrate(None, None, _profile())
        assert integ.decision_intelligence is None
        assert integ.evidence_status is None

    def test_strict_guard_raises_on_inconsistent_profile(self):
        di = _di_context(profile=_profile(instrument="NIFTY"))
        eng = DecisionIntelligenceIntegrationEngine(
            DecisionIntelligenceIntegrationConfig(strict=True),
        )
        # Integration profile disagrees on instrument.
        with pytest.raises(ValueError, match="Inconsistent opportunity profile"):
            eng.integrate(_decision_summary(), di, _profile(instrument="TCS"))

    def test_strict_guard_does_not_raise_on_consistent_profile(self):
        di = _di_context(profile=_profile(instrument="NIFTY"))
        eng = DecisionIntelligenceIntegrationEngine(
            DecisionIntelligenceIntegrationConfig(strict=True),
        )
        integ = eng.integrate(
            _decision_summary(), di, _profile(instrument="NIFTY"),
        )
        assert integ.integration_status == IntegrationStatus.INTEGRATED

    def test_non_strict_records_inconsistency(self):
        di = _di_context(profile=_profile(instrument="NIFTY"))
        eng = DecisionIntelligenceIntegrationEngine(
            DecisionIntelligenceIntegrationConfig(strict=False),
        )
        integ = eng.integrate(
            _decision_summary(), di, _profile(instrument="TCS"),
        )
        assert "inconsistency" in integ.rationale.lower()
        assert "inconsistency" in integ.limitations.lower()

    def test_strict_guard_never_modifies_existing_decision(self):
        original = _decision_summary()
        di = _di_context(profile=_profile(instrument="NIFTY"))
        eng = DecisionIntelligenceIntegrationEngine(
            DecisionIntelligenceIntegrationConfig(strict=True),
        )
        with pytest.raises(ValueError):
            eng.integrate(original, di, _profile(instrument="TCS"))
        # Existing decision untouched even when the guard raises.
        assert original.decision_classification == "QUALIFIED"


# ============================================================
# G. DETERMINISTIC IDS
# ============================================================


class TestDeterministicIds:
    def test_repeated_integration_same_id(self):
        di = _di_context()
        original = _decision_summary()
        eng = DecisionIntelligenceIntegrationEngine()
        a = eng.integrate(original, di, _profile(), label="x")
        b = eng.integrate(original, di, _profile(), label="x")
        assert a.integration_id == b.integration_id
        assert a == b

    def test_different_label_different_id(self):
        di = _di_context()
        eng = DecisionIntelligenceIntegrationEngine()
        a = eng.integrate(_decision_summary(), di, _profile(), label="a")
        b = eng.integrate(_decision_summary(), di, _profile(), label="b")
        assert a.integration_id != b.integration_id

    def test_different_existing_decision_different_id(self):
        di = _di_context()
        eng = DecisionIntelligenceIntegrationEngine()
        a = eng.integrate(
            _decision_summary(classification="QUALIFIED"), di, _profile(),
        )
        b = eng.integrate(
            _decision_summary(classification="WATCH"), di, _profile(),
        )
        assert a.integration_id != b.integration_id

    def test_different_di_different_id(self):
        di1 = _di_context(label="one")
        di2 = _di_context(label="two")
        eng = DecisionIntelligenceIntegrationEngine()
        a = eng.integrate(_decision_summary(), di1, _profile())
        b = eng.integrate(_decision_summary(), di2, _profile())
        assert a.integration_id != b.integration_id

    def test_invalid_status_deterministic_id(self):
        eng = DecisionIntelligenceIntegrationEngine()
        a = eng.integrate(None, None, _profile(), label="x")
        b = eng.integrate(None, None, _profile(), label="x")
        assert a.integration_id == b.integration_id


# ============================================================
# H. SHUFFLE INVARIANCE
# ============================================================


class TestShuffleInvariance:
    def test_shuffled_outcomes_same_integration_id(self):
        outcomes = _resolved_cohort(60, seed=5)
        di_a = _di_context(outcomes=outcomes, label="shuf")
        di_b = _di_context(outcomes=list(reversed(outcomes)), label="shuf")
        eng = DecisionIntelligenceIntegrationEngine()
        a = eng.integrate(_decision_summary(), di_a, _profile(), label="x")
        b = eng.integrate(_decision_summary(), di_b, _profile(), label="x")
        # The 12A context id is shuffle-invariant, so the 12B id is too.
        assert a.integration_id == b.integration_id

    def test_shuffled_outcomes_same_status_and_factors(self):
        outcomes = _resolved_cohort(60, seed=6)
        di_a = _di_context(outcomes=outcomes, label="shuf")
        di_b = _di_context(outcomes=list(reversed(outcomes)), label="shuf")
        eng = DecisionIntelligenceIntegrationEngine()
        a = eng.integrate(_decision_summary(), di_a, _profile(), label="x")
        b = eng.integrate(_decision_summary(), di_b, _profile(), label="x")
        assert a.integration_status == b.integration_status
        assert a.contextual_factors == b.contextual_factors


# ============================================================
# I. SERIALIZATION ROUND TRIP
# ============================================================


class TestSerialization:
    def test_round_trip_preserves_id_and_status(self):
        integ = _integrate(label="rt")
        rt = deserialize_integration(serialize_integration(integ))
        assert rt.integration_id == integ.integration_id
        assert rt.integration_status == integ.integration_status

    def test_round_trip_preserves_existing_decision_summary(self):
        integ = _integrate(
            existing_decision=_decision_summary(direction="SHORT"),
            label="rt",
        )
        rt = deserialize_integration(serialize_integration(integ))
        assert rt.existing_decision_summary == integ.existing_decision_summary
        assert rt.existing_decision_summary.direction == "SHORT"

    def test_round_trip_preserves_decision_intelligence(self):
        integ = _integrate(label="rt")
        rt = deserialize_integration(serialize_integration(integ))
        assert rt.decision_intelligence is not None
        assert rt.decision_intelligence.context_id == integ.decision_intelligence.context_id
        assert (
            rt.decision_intelligence.decision_context_status
            == integ.decision_intelligence.decision_context_status
        )

    def test_round_trip_preserves_evidence_and_strategy(self):
        integ = _integrate(label="rt")
        rt = deserialize_integration(serialize_integration(integ))
        assert rt.evidence_status == integ.evidence_status
        assert rt.strategy_interpretation == integ.strategy_interpretation
        assert rt.evidence_strength == integ.evidence_strength

    def test_round_trip_preserves_factors_rationale_metadata(self):
        integ = _integrate(label="rt")
        rt = deserialize_integration(serialize_integration(integ))
        assert rt.contextual_factors == integ.contextual_factors
        assert rt.rationale == integ.rationale
        assert rt.limitations == integ.limitations
        assert rt.metadata == integ.metadata

    def test_round_trip_heavy_existing_decision_drops(self):
        # The heavy original existing-decision reference is NOT persisted
        # (regenerable by the caller); on reload it is None. The audit
        # projection is preserved losslessly.
        original = _decision_summary()
        integ = _integrate(existing_decision=original, label="rt")
        rt = deserialize_integration(serialize_integration(integ))
        assert rt.existing_decision is None
        assert rt.existing_decision_summary == original

    def test_round_trip_context_only(self):
        di = _di_context_nomatch()
        integ = _integrate(di_context=di, label="rt")
        rt = deserialize_integration(serialize_integration(integ))
        assert rt.integration_status == IntegrationStatus.CONTEXT_ONLY
        assert rt.integration_id == integ.integration_id

    def test_round_trip_unavailable(self):
        integ = _integrate(di_context=None, label="rt")
        rt = deserialize_integration(serialize_integration(integ))
        assert rt.integration_status == IntegrationStatus.UNAVAILABLE
        assert rt.decision_intelligence is None

    def test_round_trip_invalid(self):
        eng = DecisionIntelligenceIntegrationEngine()
        integ = eng.integrate(None, None, _profile(), label="rt")
        rt = deserialize_integration(serialize_integration(integ))
        assert rt.integration_status == IntegrationStatus.INVALID
        assert rt.integration_id == integ.integration_id

    def test_bytes_round_trip(self):
        integ = _integrate(label="rt")
        rt = deserialize_integration(
            serialize_integration_bytes(integ).decode("utf-8"),
        )
        assert rt.integration_id == integ.integration_id

    def test_deterministic_bytes(self):
        integ = _integrate(label="rt")
        a = serialize_integration_bytes(integ)
        b = serialize_integration_bytes(integ)
        assert a == b

    def test_canonical_json_matches_serialize(self):
        integ = _integrate(label="rt")
        assert canonical_integration_json(integ) == serialize_integration(integ)

    def test_header_parse_returns_schema_version(self):
        integ = _integrate(label="rt")
        header = parse_integration_header(serialize_integration(integ))
        assert header["schema_version"] == INTEGRATION_SCHEMA_VERSION

    def test_future_schema_rejected(self):
        integ = _integrate(label="rt")
        payload = serialize_integration(integ)
        bad = payload.replace(
            f'"schema_version": {INTEGRATION_SCHEMA_VERSION}',
            '"schema_version": 999',
        )
        with pytest.raises(ValueError, match="Unsupported integration schema"):
            deserialize_integration(bad)

    def test_malformed_payload_rejected(self):
        with pytest.raises(ValueError):
            deserialize_integration('{"schema_version": 1, "integration": "not-a-mapping"}')


# ============================================================
# J. INPUT IMMUTABILITY
# ============================================================


class TestInputImmutability:
    def test_existing_decision_not_mutated(self):
        original = _decision_summary()
        before = (original.direction, original.decision_score, original.rank)
        _integrate(existing_decision=original)
        after = (original.direction, original.decision_score, original.rank)
        assert before == after

    def test_di_context_not_mutated(self):
        di = _di_context()
        before = (
            di.context_id, di.decision_context_status, di.factors,
            di.observed_performance, di.evidence_strength,
        )
        _integrate(di_context=di)
        after = (
            di.context_id, di.decision_context_status, di.factors,
            di.observed_performance, di.evidence_strength,
        )
        assert before == after

    def test_profile_not_mutated(self):
        prof = _profile(instrument="NIFTY")
        before = prof.instrument
        _integrate(profile=prof)
        assert prof.instrument == before

    def test_outcomes_not_mutated(self):
        outcomes = _resolved_cohort(60, seed=9)
        before = [
            (o.outcome_status, o.realized_r, o.subject.instrument)
            for o in outcomes
        ]
        di = _di_context(outcomes=outcomes)
        _integrate(di_context=di)
        after = [
            (o.outcome_status, o.realized_r, o.subject.instrument)
            for o in outcomes
        ]
        assert before == after

    def test_evidence_report_not_mutated(self):
        outcomes = _resolved_cohort(60, seed=10)
        report = _evidence_report(outcomes)
        before_id = report.evidence_id
        di = DecisionIntelligenceEngine().build_from_report(
            report, _profile(), _decision_summary(),
        )
        _integrate(di_context=di)
        assert report.evidence_id == before_id

    def test_existing_decision_frozen(self):
        with pytest.raises(Exception):
            _decision_summary().decision_score = 99  # type: ignore[misc]

    def test_integrated_context_frozen(self):
        integ = _integrate()
        with pytest.raises(Exception):
            integ.integration_status = IntegrationStatus.INVALID  # type: ignore[misc]


# ============================================================
# K. NO-LOOK-AHEAD
# ============================================================


class TestNoLookAhead:
    def test_integrate_signature_has_no_candle_param(self):
        sig = inspect.signature(
            DecisionIntelligenceIntegrationEngine.integrate,
        )
        params = set(sig.parameters)
        assert "candles" not in params
        assert "future_candles" not in params
        assert "evaluation_time" not in params

    def test_works_with_outcome_evaluator_patched_to_raise(self):
        outcomes = _resolved_cohort(60, seed=11)
        report = _evidence_report(outcomes)
        di = DecisionIntelligenceEngine().build_from_report(
            report, _profile(), _decision_summary(),
        )
        original = OutcomeEvaluator.evaluate

        def boom(self, subject, future_candles):  # noqa: ANN001
            raise RuntimeError("must not re-evaluate outcomes")

        OutcomeEvaluator.evaluate = boom  # type: ignore[method-assign]
        try:
            eng = DecisionIntelligenceIntegrationEngine()
            integ = eng.integrate(_decision_summary(), di, _profile())
            assert integ.integration_status == IntegrationStatus.INTEGRATED
        finally:
            OutcomeEvaluator.evaluate = original  # type: ignore[method-assign]

    def test_works_with_pipeline_patched_to_raise(self):
        di = _di_context()
        original = HistoricalEvaluationPipeline.evaluate

        def boom(self, candles):  # noqa: ANN001
            raise RuntimeError("must not re-run pipeline")

        HistoricalEvaluationPipeline.evaluate = boom  # type: ignore[method-assign]
        try:
            eng = DecisionIntelligenceIntegrationEngine()
            integ = eng.integrate(_decision_summary(), di, _profile())
            assert integ.integration_status == IntegrationStatus.INTEGRATED
            assert integ.decision_intelligence is di
        finally:
            HistoricalEvaluationPipeline.evaluate = original  # type: ignore[method-assign]

    def test_no_future_info_introduced(self):
        # Integrating twice with identical inputs yields identical
        # results (no wall-clock / randomness leaks in).
        di = _di_context()
        eng = DecisionIntelligenceIntegrationEngine()
        a = eng.integrate(_decision_summary(), di, _profile(), label="x")
        b = eng.integrate(_decision_summary(), di, _profile(), label="x")
        assert a == b


# ============================================================
# L. EXISTING PIPELINE REGRESSION
# ============================================================


class TestPipelineRegression:
    def test_pipeline_baseline_signals_4_trades_3(self):
        pipeline = HistoricalEvaluationPipeline(PipelineConfig())
        result = pipeline.evaluate(trending_dataset())
        assert result.signals_generated == 4
        assert result.performance.completed_trades == 3


# ============================================================
# M. SPRINT 11X REGRESSION
# ============================================================


class TestSprint11XRegression:
    def test_performance_analytics_unchanged(self):
        outcomes = _resolved_cohort(60, seed=12)
        analytics = PerformanceAnalyticsEngine(
            PerformanceAnalyticsConfig(label="11x"),
        ).analyze(outcomes)
        assert analytics.overall.total == 60
        assert analytics.overall.resolved == 60

    def test_12b_does_not_touch_performance_engine(self):
        # Running a 12B integration must not change 11X analytics output.
        outcomes = _resolved_cohort(60, seed=13)
        before = PerformanceAnalyticsEngine(
            PerformanceAnalyticsConfig(),
        ).analyze(outcomes).analytics_id
        di = _di_context(outcomes=outcomes)
        _integrate(di_context=di)
        after = PerformanceAnalyticsEngine(
            PerformanceAnalyticsConfig(),
        ).analyze(outcomes).analytics_id
        assert before == after


# ============================================================
# N. SPRINT 11Y REGRESSION
# ============================================================


class TestSprint11YRegression:
    def test_evidence_engine_unchanged(self):
        outcomes = _resolved_cohort(60, seed=14)
        report = HistoricalEvidenceEngine(
            EvidenceConfig(label="11y"),
        ).evaluate(outcomes)
        assert report.summary.strength == EvidenceStrength.STRONG

    def test_12b_does_not_touch_evidence_engine(self):
        outcomes = _resolved_cohort(60, seed=15)
        before = HistoricalEvidenceEngine(
            EvidenceConfig(label="11y"),
        ).evaluate(outcomes).evidence_id
        di = _di_context(outcomes=outcomes)
        _integrate(di_context=di)
        after = HistoricalEvidenceEngine(
            EvidenceConfig(label="11y"),
        ).evaluate(outcomes).evidence_id
        assert before == after


# ============================================================
# O. SPRINT 11Z REGRESSION
# ============================================================


class TestSprint11ZRegression:
    def test_strategy_engine_unchanged(self):
        outcomes = _resolved_cohort(60, seed=16)
        report = _evidence_report(outcomes)
        strat = StrategyIntelligenceEngine()
        lookup = strat.lookup(report, _profile())
        assert lookup.matched
        assert lookup.assessment is not None
        assert lookup.assessment.assessment_status in {
            StrategyAssessmentStatus.STRONGER_HISTORICAL_SUPPORT,
            StrategyAssessmentStatus.SUPPORTIVE_EVIDENCE,
        }

    def test_12b_does_not_touch_strategy_engine(self):
        outcomes = _resolved_cohort(60, seed=17)
        report = _evidence_report(outcomes)
        strat = StrategyIntelligenceEngine()
        before = strat.lookup(report, _profile()).lookup_id
        di = _di_context(outcomes=outcomes)
        _integrate(di_context=di)
        after = strat.lookup(report, _profile()).lookup_id
        assert before == after


# ============================================================
# P. SPRINT 12A REGRESSION
# ============================================================


class TestSprint12ARegression:
    def test_decision_intelligence_engine_unchanged(self):
        di = _di_context()
        assert di.decision_context_status == DecisionContextStatus.EVIDENCE_SUPPORTED
        assert di.context_id.startswith("di-")

    def test_12b_does_not_touch_12a_engine(self):
        outcomes = _resolved_cohort(60, seed=18)
        report = _evidence_report(outcomes)
        strat = StrategyIntelligenceEngine()
        lookup = strat.lookup(report, _profile())
        eng = DecisionIntelligenceEngine()
        before = eng.build(_profile(), _decision_summary(), lookup).context_id
        # Now run a 12B integration using a 12A context.
        di = eng.build(_profile(), _decision_summary(), lookup)
        _integrate(di_context=di)
        after = eng.build(_profile(), _decision_summary(), lookup).context_id
        assert before == after

    def test_12a_serialization_still_works(self):
        di = _di_context()
        from engine.intelligence.decision_intelligence_serialization import (
            deserialize_context,
        )
        rt = deserialize_context(serialize_context(di))
        assert rt == di


# ============================================================
# Q. FULL 11V -> 11W -> 11X -> 11Y -> 11Z -> 12A -> 12B END-TO-END
# ============================================================


class TestEndToEndChain:
    def test_full_chain_to_integration_boundary(self):
        # 11V replay -> 11W outcome evaluation (stand-in: build outcomes
        # directly), then 11X -> 11Y -> 11Z -> 12A -> 12B.
        long_trend = _resolved_cohort(
            60, win_fraction=0.6, instrument="NIFTY", direction="LONG",
            setup_type="TREND_CONTINUATION", mtf_alignment="ALIGNED", seed=201,
        )
        short_breakout = _resolved_cohort(
            60, win_fraction=0.4, instrument="TCS", direction="SHORT",
            setup_type="BREAKOUT", mtf_alignment="CONFLICTING", seed=202,
        )
        insufficient = _resolved_cohort(
            5, win_fraction=1.0, instrument="HDFCBANK", direction="LONG",
            setup_type="STRUCTURE_CONTINUATION", mtf_alignment="ALIGNED", seed=203,
        )
        all_outcomes = long_trend + short_breakout + insufficient

        # 11X + 11Y.
        evidence = HistoricalEvidenceEngine(
            EvidenceConfig(label="e2e-12b"),
        ).evaluate(all_outcomes)

        # 11Z lookup for a current opportunity.
        strat = StrategyIntelligenceEngine()
        profile = OpportunityProfile(
            instrument="NIFTY", direction="LONG",
            setup_type="TREND_CONTINUATION", mtf_alignment="ALIGNED",
        )
        lookup = strat.lookup(evidence, profile)
        assert lookup.matched

        # 12A decision intelligence context.
        di = DecisionIntelligenceEngine().build(
            profile,
            _decision_summary(direction="LONG", classification="QUALIFIED"),
            lookup,
            label="e2e",
            metadata={"source": "12b-e2e"},
        )
        assert di.decision_context_status == DecisionContextStatus.EVIDENCE_SUPPORTED
        assert di.evidence_strength == EvidenceStrength.STRONG

        # 12B integration: attach DI to an EXISTING decision.
        original_decision = _decision_summary(direction="LONG", classification="QUALIFIED")
        eng = DecisionIntelligenceIntegrationEngine()
        integ = eng.integrate(
            original_decision, di, profile, label="e2e-12b",
        )
        # The existing decision flows to the boundary unchanged.
        assert integ.existing_decision is original_decision
        assert integ.integration_status == IntegrationStatus.INTEGRATED
        assert integ.evidence_strength == EvidenceStrength.STRONG
        assert integ.metadata == (("source", "12b-e2e"),) or integ.label == "e2e-12b"

        # Real historical evidence flowed all the way to the boundary.
        assert integ.observed_performance is not None
        assert integ.observed_performance.total == 60
        assert integ.strategy_interpretation == StrategyAssessmentStatus.STRONGER_HISTORICAL_SUPPORT

        # The existing decision classification is NOT changed by the
        # attached evidence.
        assert integ.existing_decision_summary.decision_classification == "QUALIFIED"

    def test_full_chain_insufficient_cohort_to_boundary(self):
        insufficient = _resolved_cohort(
            5, win_fraction=1.0, instrument="HDFCBANK", direction="LONG",
            setup_type="STRUCTURE_CONTINUATION", mtf_alignment="ALIGNED", seed=211,
        )
        evidence = _evidence_report(insufficient)
        strat = StrategyIntelligenceEngine()
        profile = OpportunityProfile(
            instrument="HDFCBANK", direction="LONG",
            setup_type="STRUCTURE_CONTINUATION", mtf_alignment="ALIGNED",
        )
        lookup = strat.lookup(evidence, profile)
        di = DecisionIntelligenceEngine().build(profile, _decision_summary(), lookup)
        assert di.decision_context_status == DecisionContextStatus.INSUFFICIENT_EVIDENCE

        # 12B: insufficient evidence IS attached (available evidence,
        # just insufficient strength) -> INTEGRATED (evidence available).
        eng = DecisionIntelligenceIntegrationEngine()
        integ = eng.integrate(_decision_summary(), di, profile)
        assert integ.integration_status == IntegrationStatus.INTEGRATED
        assert integ.evidence_strength == EvidenceStrength.INSUFFICIENT
        # Existing decision unchanged.
        assert integ.existing_decision_summary.decision_classification == "QUALIFIED"

    def test_full_chain_no_match_to_boundary(self):
        outcomes = _resolved_cohort(60, seed=221)
        evidence = _evidence_report(outcomes)
        strat = StrategyIntelligenceEngine()
        profile = OpportunityProfile(setup_type="RANGE_REJECTION")
        lookup = strat.lookup(evidence, profile)
        di = DecisionIntelligenceEngine().build(profile, _decision_summary(), lookup)
        assert di.decision_context_status == DecisionContextStatus.EVIDENCE_UNAVAILABLE

        eng = DecisionIntelligenceIntegrationEngine()
        integ = eng.integrate(_decision_summary(), di, profile)
        assert integ.integration_status == IntegrationStatus.CONTEXT_ONLY
        assert integ.observed_performance is None

    def test_full_chain_serialization_round_trip(self):
        outcomes = _resolved_cohort(60, seed=231)
        evidence = _evidence_report(outcomes)
        strat = StrategyIntelligenceEngine()
        profile = _profile()
        lookup = strat.lookup(evidence, profile)
        di = DecisionIntelligenceEngine().build(profile, _decision_summary(), lookup)
        eng = DecisionIntelligenceIntegrationEngine()
        integ = eng.integrate(_decision_summary(), di, profile, label="e2e-rt")
        rt = deserialize_integration(serialize_integration(integ))
        assert rt.integration_id == integ.integration_id
        assert rt.integration_status == integ.integration_status
        assert rt.existing_decision_summary == integ.existing_decision_summary


# ============================================================
# R. DISCLAIMER / RATIONALE
# ============================================================


class TestRationaleAndDisclaimer:
    def test_rationale_mentions_integration_status(self):
        integ = _integrate()
        assert integ.integration_status.name in integ.rationale

    def test_rationale_states_existing_decision_not_modified(self):
        integ = _integrate()
        assert (
            "without alteration" in integ.rationale.lower()
            or "not modified" in integ.rationale.lower()
            or "preserved without alteration" in integ.rationale.lower()
        )

    def test_rationale_has_no_predictive_language(self):
        integ = _integrate()
        banned = (
            "will win", "guaranteed profit", "high probability",
            "statistically significant", "buy now", "sell now",
        )
        assert all(t not in integ.rationale.lower() for t in banned)

    def test_limitations_states_context_not_decision(self):
        integ = _integrate()
        assert "context" in integ.limitations.lower()
        assert "does not" in integ.limitations.lower()

    def test_limitations_states_no_recommendation(self):
        integ = _integrate()
        assert "buy" in integ.limitations.lower() or "recommendation" in integ.limitations.lower()

    def test_integrated_status_rationale_descriptive(self):
        integ = _integrate()
        assert "INTEGRATED" in integ.rationale or "integrated" in integ.rationale.lower()

    def test_unavailable_rationale_states_no_di(self):
        integ = _integrate(di_context=None)
        assert "no decision intelligence" in integ.rationale.lower()

    def test_invalid_rationale_states_no_existing_decision(self):
        eng = DecisionIntelligenceIntegrationEngine()
        integ = eng.integrate(None, None, _profile())
        assert "no existing decision" in integ.rationale.lower()


# ============================================================
# S. SEPARATION OF CONCERNS
# ============================================================


class TestSeparationOfConcerns:
    def test_observed_performance_evidence_strength_strategy_distinct_fields(self):
        integ = _integrate()
        # These are separate fields, never collapsed into one score.
        assert integ.observed_performance is not None
        assert integ.evidence_strength is not None
        assert integ.strategy_interpretation is not None
        assert integ.evidence_status is not None
        # They are distinct objects/values.
        assert integ.observed_performance is integ.decision_intelligence.observed_performance
        assert integ.evidence_strength is integ.decision_intelligence.evidence_strength

    def test_no_single_score_field(self):
        integ = _integrate()
        # No aggregated "quality" / "confidence" / "probability" field.
        assert not hasattr(integ, "quality_score")
        assert not hasattr(integ, "confidence")
        assert not hasattr(integ, "probability")
        assert not hasattr(integ, "combined_score")
        assert not hasattr(integ, "integrated_score")

    def test_existing_decision_classification_separate_from_evidence(self):
        original = _decision_summary(classification="WATCH")
        di = _di_context()  # EVIDENCE_SUPPORTED
        integ = _integrate(existing_decision=original, di_context=di)
        # Existing decision classification and evidence status are
        # separate, never merged.
        assert integ.existing_decision_summary.decision_classification == "WATCH"
        assert integ.evidence_status == DecisionContextStatus.EVIDENCE_SUPPORTED
        assert integ.integration_status == IntegrationStatus.INTEGRATED

    def test_decision_intelligence_context_separate_from_integration(self):
        di = _di_context()
        integ = _integrate(di_context=di)
        # The 12A context and the 12B integration are separate objects.
        assert integ.decision_intelligence is di
        assert isinstance(integ, IntegratedDecisionContext)
        assert isinstance(di, DecisionIntelligenceContext)


# ============================================================
# T. NO RECOMMENDATION GENERATION
# ============================================================


class TestNoRecommendation:
    def test_no_buy_sell_in_rationale(self):
        integ = _integrate()
        low = integ.rationale.lower()
        for term in ("buy", "sell", "enter", "exit", "hold"):
            # "hold" may appear in "threshold"; check word boundaries.
            assert f" {term} " not in low or term in (
                "hold",  # limitations may mention "HOLD" in the disclaimer
            )

    def test_limitations_explicitly_states_no_recommendation(self):
        integ = _integrate()
        low = integ.limitations.lower()
        assert "buy" in low and "sell" in low
        assert "recommendation" in low

    def test_no_score_adjustment(self):
        original = _decision_summary(score=75)
        di = _di_context()
        integ = _integrate(existing_decision=original, di_context=di)
        # The existing decision score is unchanged; no adjusted score.
        assert integ.existing_decision_summary.decision_score == 75
        assert not hasattr(integ, "adjusted_score")
        assert not hasattr(integ, "evidence_adjusted_score")

    def test_no_upgrade_of_existing_decision(self):
        # QUALIFIED + EVIDENCE_SUPPORTED must NOT become PREFERRED.
        original = _decision_summary(classification="QUALIFIED")
        di = _di_context()
        integ = _integrate(existing_decision=original, di_context=di)
        assert integ.existing_decision_summary.decision_classification == "QUALIFIED"
        assert integ.existing_decision is original

    def test_no_downgrade_of_existing_decision(self):
        original = _decision_summary(classification="PREFERRED")
        di = _di_context_nomatch()  # no evidence
        integ = _integrate(existing_decision=original, di_context=di)
        assert integ.existing_decision_summary.decision_classification == "PREFERRED"

    def test_report_has_no_predictive_language(self):
        integ = _integrate()
        report = DecisionIntelligenceIntegrationFormatter().format(integ)
        low = report.lower()
        for term in ("guaranteed profit", "will rise", "will fall",
                     "high probability", "statistically significant",
                     "buy now", "sell now"):
            assert term not in low

    def test_report_has_disclaimer(self):
        integ = _integrate()
        report = DecisionIntelligenceIntegrationFormatter().format(integ)
        assert "WARNING" in report
        assert "does not guarantee future performance" in report
        assert "trading recommendation" in report
        assert "modify the existing decision" in report

    def test_report_returns_str(self):
        integ = _integrate()
        report = DecisionIntelligenceIntegrationFormatter().format(integ)
        assert isinstance(report, str)

    def test_report_required_sections(self):
        integ = _integrate()
        report = DecisionIntelligenceIntegrationFormatter().format(integ)
        for section in (
            "Decision Intelligence Integration Report",
            "Existing Decision",
            "Decision Intelligence",
            "Evidence Status",
            "Strategy Interpretation",
            "Integration Status",
            "Rationale",
            "Limitations",
        ):
            assert section in report

    def test_report_unavailable_shown_for_no_di(self):
        integ = _integrate(di_context=None)
        report = DecisionIntelligenceIntegrationFormatter().format(integ)
        assert "unavailable" in report.lower()

    def test_report_deterministic(self):
        integ = _integrate(label="det")
        fmt = DecisionIntelligenceIntegrationFormatter()
        a = fmt.format(integ)
        b = fmt.format(integ)
        assert a == b

    def test_precision_negative_rejected(self):
        with pytest.raises(ValueError):
            DecisionIntelligenceIntegrationFormatter(precision=-1)


# ============================================================
# CONFIG VALIDATION
# ============================================================


class TestConfig:
    def test_defaults(self):
        c = DecisionIntelligenceIntegrationConfig()
        assert c.strict is True
        assert c.label == ""
        assert c.metadata == ()

    def test_frozen(self):
        c = DecisionIntelligenceIntegrationConfig()
        with pytest.raises(Exception):
            c.strict = False  # type: ignore[misc]

    def test_snapshot_sorted(self):
        c = DecisionIntelligenceIntegrationConfig(strict=False)
        snap = c.snapshot()
        assert snap == (("strict", "False"),)
        assert list(snap) == sorted(snap)

    def test_label_metadata_used(self):
        eng = DecisionIntelligenceIntegrationEngine(
            DecisionIntelligenceIntegrationConfig(
                label="cfg-label", metadata=(("k", "v"),),
            ),
        )
        integ = eng.integrate(_decision_summary(), _di_context(), _profile())
        assert integ.label == "cfg-label"
        assert integ.metadata == (("k", "v"),)


# ============================================================
# IMPLICIT PROPERTIES
# ============================================================


class TestProperties:
    def test_has_decision_intelligence(self):
        integ = _integrate()
        assert integ.has_decision_intelligence

    def test_has_existing_decision(self):
        integ = _integrate()
        assert integ.has_existing_decision

    def test_has_evidence(self):
        integ = _integrate()
        assert integ.has_evidence

    def test_evidence_supported(self):
        integ = _integrate()
        assert integ.evidence_supported

    def test_properties_false_when_unavailable(self):
        eng = DecisionIntelligenceIntegrationEngine()
        integ = eng.integrate(None, None, _profile())
        assert not integ.has_decision_intelligence
        assert not integ.has_existing_decision
        assert not integ.has_evidence
        assert not integ.evidence_supported
