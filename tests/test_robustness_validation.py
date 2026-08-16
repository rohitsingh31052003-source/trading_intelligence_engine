"""
Tests for the robustness, failure-mode & edge-case hardening validation
layer (Sprint 12D).

Coverage (mirrors the required test areas A-M):

A.  Empty / minimal inputs
B.  Boundary sample-size conditions
C.  Mixed status / contamination cases
D.  Adversarial cohort combinations
E.  Lookup / matching robustness
F.  Decision-integration failure isolation
G.  Serialization adversarial cases
H.  Determinism / shuffle invariance
I.  Input immutability
J.  Failure isolation
K.  Cross-layer consistency
L.  Accounting invariants
M.  Reporting honesty
N.  Pipeline regression / no-look-ahead / serialization / model / config
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from engine.config.robustness_validation_config import RobustnessValidationConfig
from engine.intelligence.robustness_validation import RobustnessValidationEngine
from engine.intelligence.robustness_validation_serialization import (
    ROBUSTNESS_VALIDATION_SCHEMA_VERSION,
    canonical_robustness_json,
    deserialize_robustness,
    parse_robustness_header,
    serialize_robustness,
    serialize_robustness_bytes,
)
from engine.intelligence.historical_outcome import (
    OutcomeEvaluator,
)
from engine.models.decision_intelligence import (
    DecisionContextStatus,
    ExistingDecisionSummary,
)
from engine.models.decision_intelligence_integration import (
    IntegrationStatus,
)
from engine.models.historical_evidence import EvidenceStrength
from engine.models.historical_outcome import (
    HistoricalOutcome,
    OutcomeStatus,
    OutcomeSubject,
)
from engine.models.robustness_validation import (
    ROBUSTNESS_VALIDATION_LIMITATIONS,
    RobustnessCategory,
    RobustnessCategorySummary,
    RobustnessCheckResult,
    RobustnessCheckStatus,
    RobustnessScenarioResult,
    RobustnessValidationReport,
)
from engine.models.strategy_intelligence import OpportunityProfile
from engine.pipeline import (
    HistoricalEvaluationPipeline,
    PipelineConfig,
    trending_dataset,
)
from engine.reporting.robustness_validation import RobustnessValidationFormatter


_EPOCH = datetime(2025, 1, 1, tzinfo=UTC)


# ============================================================
# HELPERS
# ============================================================


def _subject(
    i: int = 0,
    instrument: str = "NIFTY",
    direction: str = "LONG",
    setup_type: str = "TREND_CONTINUATION",
    mtf_alignment: str = "ALIGNED",
    decision: str = "QUALIFIED",
    opportunity: str = "BEST_OPPORTUNITY",
    entry: float = 100.0,
    stop: float = 95.0,
    target: float = 110.0,
    ts: datetime | None = None,
) -> OutcomeSubject:
    return OutcomeSubject(
        instrument=instrument,
        direction=direction,
        evaluation_timestamp=ts or _EPOCH + timedelta(days=i),
        entry=entry,
        stop=stop,
        target=target,
        decision_classification=decision,
        decision_score=70,
        opportunity_status=opportunity,
        rank=1,
        scan_id="scan-12d",
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
    entry: float = 100.0,
    stop: float = 95.0,
    target: float = 110.0,
) -> HistoricalOutcome:
    risk = abs(entry - stop)
    return HistoricalOutcome(
        subject=_subject(
            i, instrument=instrument, direction=direction,
            setup_type=setup_type, mtf_alignment=mtf_alignment,
            decision=decision, opportunity=opportunity,
            entry=entry, stop=stop, target=target,
        ),
        outcome_status=status,
        realized_r=realized_r,
        mfe=mfe,
        mae=mae,
        mfe_r=(mfe / risk) if mfe is not None else None,
        mae_r=(mae / risk) if mae is not None else None,
        risk=risk,
    )


def _resolved(
    n: int,
    win_fraction: float = 0.6,
    instrument: str = "NIFTY",
    direction: str = "LONG",
    setup_type: str = "TREND_CONTINUATION",
    mtf_alignment: str = "ALIGNED",
    seed: int = 0,
) -> list[HistoricalOutcome]:
    rng = random.Random(seed)
    out: list[HistoricalOutcome] = []
    for i in range(n):
        win = rng.random() < win_fraction
        status = OutcomeStatus.TARGET_HIT if win else OutcomeStatus.STOP_HIT
        rr = 2.0 if win else -1.0
        out.append(
            _outcome(
                status, i=i, realized_r=rr, instrument=instrument,
                direction=direction, setup_type=setup_type,
                mtf_alignment=mtf_alignment,
            ),
        )
    return out


def _decision(
    direction: str = "LONG", classification: str = "QUALIFIED", score: int = 75,
) -> ExistingDecisionSummary:
    return ExistingDecisionSummary(
        direction=direction,
        decision_classification=classification,
        decision_score=score,
        opportunity_status="BEST_OPPORTUNITY",
        rank=1,
        geometry_complete=True,
        confluence_score=4,
        risk_reward_ratio=2.0,
        entry=100.0,
        stop=95.0,
        target=110.0,
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


def _validate(outcomes, decision=None, profile=None, edge_case=""):
    eng = RobustnessValidationEngine()
    scenario: dict = {"name": "s", "outcomes": tuple(outcomes)}
    if decision is not None:
        scenario["decision"] = decision
    if profile is not None:
        scenario["profile"] = profile
    if edge_case:
        scenario["edge_case"] = edge_case
    return eng.validate([scenario])


def _check_by_name(scenario: RobustnessScenarioResult, name: str) -> RobustnessCheckResult:
    matches = [c for c in scenario.checks if c.name == name]
    assert matches, f"check {name!r} not found"
    return matches[0]


def _checks_by_cat(scenario: RobustnessScenarioResult, cat: RobustnessCategory) -> list[RobustnessCheckResult]:
    return [c for c in scenario.checks if c.category == cat]


# ============================================================
# A. EMPTY / MINIMAL INPUTS
# ============================================================


class TestEmptyMinimalInputs:
    def test_empty_scenario_no_fabricated_stats(self):
        r = _validate([], edge_case="empty")
        sc = r.scenarios[0]
        empty = _check_by_name(sc, "empty_input_honest")
        assert empty.skipped
        assert "fabricated" in empty.detail or "unavailable" in empty.detail

    def test_empty_scenario_overall_skipped(self):
        r = _validate([], edge_case="empty")
        # All per-scenario checks (from _run_scenario_checks) are
        # not-run for an empty scenario. The pipeline-regression +
        # failure-isolation checks are global attachments and may run,
        # but the empty scenario itself must NOT auto-pass from its own
        # checks.
        sc = r.scenarios[0]
        own_checks = [
            c for c in sc.checks
            if c.name not in ("pipeline_regression", "failure_isolation")
        ]
        assert all(c.not_run for c in own_checks)
        assert sc.passed is False

    def test_single_outcome_no_div_by_zero(self):
        r = _validate([_outcome(OutcomeStatus.EXPIRED, i=0, realized_r=0.3)])
        sc = r.scenarios[0]
        empty = _check_by_name(sc, "empty_input_honest")
        assert empty.passed

    def test_single_excluded_outcome_no_fabricated_winrate(self):
        r = _validate([_outcome(OutcomeStatus.BOTH_TOUCHED, i=0)])
        sc = r.scenarios[0]
        empty = _check_by_name(sc, "empty_input_honest")
        assert empty.passed

    def test_all_both_touched_no_fabricated_r(self):
        both = [_outcome(OutcomeStatus.BOTH_TOUCHED, i=k) for k in range(8)]
        r = _validate(both, edge_case="all-both-touched")
        sc = r.scenarios[0]
        empty = _check_by_name(sc, "empty_input_honest")
        assert empty.passed

    def test_all_no_geometry_no_fabricated_r(self):
        ng = [_outcome(OutcomeStatus.NO_GEOMETRY, i=k) for k in range(6)]
        r = _validate(ng, edge_case="all-no-geometry")
        sc = r.scenarios[0]
        empty = _check_by_name(sc, "empty_input_honest")
        assert empty.passed

    def test_all_insufficient_data_no_fabricated_r(self):
        ind = [_outcome(OutcomeStatus.INSUFFICIENT_DATA, i=k) for k in range(5)]
        r = _validate(ind)
        sc = r.scenarios[0]
        empty = _check_by_name(sc, "empty_input_honest")
        assert empty.passed

    def test_non_outcome_entry_is_invalid(self):
        eng = RobustnessValidationEngine()
        r = eng.validate([
            {"name": "bad", "outcomes": ("not-an-outcome",)},
        ])
        sc = r.scenarios[0]
        cons = _check_by_name(sc, "scenario_construction")
        assert cons.status == RobustnessCheckStatus.INVALID
        assert "Non-HistoricalOutcome" in cons.detail

    def test_non_outcome_entry_does_not_crash_engine(self):
        eng = RobustnessValidationEngine()
        r = eng.validate([
            {"name": "bad", "outcomes": ("not-an-outcome",)},
            {"name": "ok", "outcomes": tuple(_resolved(10))},
        ])
        # The valid scenario must still be processed.
        names = {s.name for s in r.scenarios}
        assert names == {"bad", "ok"}

    def test_empty_outcome_distribution(self):
        r = _validate([], edge_case="empty")
        assert r.outcome_distribution == ()


# ============================================================
# B. BOUNDARY SAMPLE-SIZE CONDITIONS
# ============================================================


class TestBoundarySampleSize:
    def _boundary_check(self, outcomes):
        r = _validate(outcomes)
        return _check_by_name(r.scenarios[0], "boundary_sample_size")

    def test_below_min_is_insufficient(self):
        # min_sample_total default = 30 -> 29 below.
        outcomes = _resolved(29, 1.0, seed=10)  # 100% wins, insufficient
        c = self._boundary_check(outcomes)
        assert c.passed
        assert "INSUFFICIENT" in c.detail

    def test_exactly_at_min(self):
        outcomes = _resolved(30, 0.6, seed=11)
        c = self._boundary_check(outcomes)
        assert c.passed

    def test_just_above_min(self):
        outcomes = _resolved(31, 0.6, seed=12)
        c = self._boundary_check(outcomes)
        assert c.passed

    def test_100pct_wins_insufficient_sample(self):
        outcomes = _resolved(5, 1.0, seed=13)
        r = _validate(outcomes, decision=_decision())
        c = _check_by_name(r.scenarios[0], "boundary_sample_size")
        assert c.passed
        # Hard gate preserved through the chain.
        cross = _check_by_name(r.scenarios[0], "cross_layer_consistency")
        assert cross.passed

    def test_100pct_losses_insufficient_sample(self):
        outcomes = _resolved(5, 0.0, seed=14)
        c = self._boundary_check(outcomes)
        assert c.passed
        assert "INSUFFICIENT" in c.detail

    def test_excellent_r_insufficient_sample(self):
        outcomes = [
            _outcome(OutcomeStatus.TARGET_HIT, i=k, realized_r=10.0)
            for k in range(3)
        ]
        c = self._boundary_check(outcomes)
        assert c.passed
        assert "INSUFFICIENT" in c.detail

    def test_later_layers_do_not_promote_insufficient(self):
        outcomes = _resolved(5, 1.0, seed=15)
        r = _validate(outcomes, decision=_decision())
        cross = _check_by_name(r.scenarios[0], "cross_layer_consistency")
        assert cross.passed


# ============================================================
# C. MIXED STATUS / CONTAMINATION CASES
# ============================================================


class TestMixedContamination:
    def _mixed_check(self, outcomes):
        r = _validate(outcomes)
        return _check_by_name(r.scenarios[0], "mixed_contamination")

    def test_all_target_hit(self):
        outcomes = [_outcome(OutcomeStatus.TARGET_HIT, i=k, realized_r=2.0) for k in range(12)]
        assert self._mixed_check(outcomes).passed

    def test_target_plus_stop(self):
        outcomes = [
            _outcome(OutcomeStatus.TARGET_HIT, i=0, realized_r=2.0),
            _outcome(OutcomeStatus.STOP_HIT, i=1, realized_r=-1.0),
        ] * 6
        assert self._mixed_check(outcomes).passed

    def test_both_touched_excluded_from_winloss(self):
        outcomes = [
            _outcome(OutcomeStatus.TARGET_HIT, i=0, realized_r=2.0),
            _outcome(OutcomeStatus.BOTH_TOUCHED, i=1, realized_r=None),
        ] * 6
        c = self._mixed_check(outcomes)
        assert c.passed

    def test_no_geometry_no_fabricated_r(self):
        outcomes = [
            _outcome(OutcomeStatus.NO_GEOMETRY, i=k, realized_r=None)
            for k in range(10)
        ]
        c = self._mixed_check(outcomes)
        assert c.passed

    def test_insufficient_data_no_fabricated_r(self):
        outcomes = [
            _outcome(OutcomeStatus.INSUFFICIENT_DATA, i=k, realized_r=None)
            for k in range(10)
        ]
        c = self._mixed_check(outcomes)
        assert c.passed

    def test_expired_contributes_valid_r(self):
        outcomes = [
            _outcome(OutcomeStatus.EXPIRED, i=k, realized_r=0.3)
            for k in range(12)
        ]
        c = self._mixed_check(outcomes)
        assert c.passed

    def test_full_mix_all_statuses(self):
        outcomes = [
            _outcome(OutcomeStatus.TARGET_HIT, i=0, realized_r=2.0),
            _outcome(OutcomeStatus.STOP_HIT, i=1, realized_r=-1.0),
            _outcome(OutcomeStatus.EXPIRED, i=2, realized_r=0.1),
            _outcome(OutcomeStatus.BOTH_TOUCHED, i=3, realized_r=None),
            _outcome(OutcomeStatus.NO_GEOMETRY, i=4, realized_r=None),
            _outcome(OutcomeStatus.INSUFFICIENT_DATA, i=5, realized_r=None),
        ] * 6
        c = self._mixed_check(outcomes)
        assert c.passed


# ============================================================
# D. ADVERSARIAL COHORT COMBINATIONS
# ============================================================


class TestAdversarialCohorts:
    def _adv_check(self, outcomes):
        r = _validate(outcomes)
        return _check_by_name(r.scenarios[0], "adversarial_cohorts")

    def test_same_instrument_different_directions(self):
        outcomes = (
            _resolved(15, 0.6, instrument="NIFTY", direction="LONG", seed=1)
            + _resolved(15, 0.4, instrument="NIFTY", direction="SHORT", seed=2)
        )
        assert self._adv_check(outcomes).passed

    def test_same_direction_different_instruments(self):
        outcomes = (
            _resolved(15, 0.6, instrument="NIFTY", seed=3)
            + _resolved(15, 0.5, instrument="TCS", seed=4)
        )
        assert self._adv_check(outcomes).passed

    def test_overlapping_single_dimension_cohorts(self):
        outcomes = (
            _resolved(10, 0.6, instrument="NIFTY", setup_type="TREND_CONTINUATION", seed=5)
            + _resolved(10, 0.5, instrument="NIFTY", setup_type="BREAKOUT", seed=6)
        )
        assert self._adv_check(outcomes).passed

    def test_controlled_composite_cohort(self):
        outcomes = (
            _resolved(15, 0.6, instrument="NIFTY", direction="LONG", setup_type="TREND_CONTINUATION", seed=7)
            + _resolved(15, 0.4, instrument="TCS", direction="SHORT", setup_type="BREAKOUT", seed=8)
        )
        assert self._adv_check(outcomes).passed

    def test_composite_with_insufficient_observations(self):
        outcomes = (
            _resolved(3, 1.0, instrument="NIFTY", direction="LONG", setup_type="TREND_CONTINUATION", seed=9)
            + _resolved(3, 0.0, instrument="TCS", direction="SHORT", setup_type="BREAKOUT", seed=10)
        )
        assert self._adv_check(outcomes).passed

    def test_unavailable_metadata(self):
        # Outcomes with empty setup_type -> unavailable sentinel sorts last.
        outcomes = [
            _outcome(OutcomeStatus.TARGET_HIT, i=k, realized_r=2.0, setup_type="")
            for k in range(12)
        ]
        assert self._adv_check(outcomes).passed

    def test_unknown_alignment(self):
        outcomes = [
            _outcome(OutcomeStatus.TARGET_HIT, i=k, realized_r=2.0, mtf_alignment="UNKNOWN")
            for k in range(12)
        ]
        assert self._adv_check(outcomes).passed

    def test_no_invented_metadata(self):
        outcomes = _resolved(20, 0.6, seed=11)
        c = self._adv_check(outcomes)
        assert c.passed
        assert "invented" in c.detail


# ============================================================
# E. LOOKUP / MATCHING ROBUSTNESS
# ============================================================


class TestLookupMatching:
    def _lookup_check(self, outcomes, profile=None):
        r = _validate(outcomes, profile=profile)
        return _check_by_name(r.scenarios[0], "lookup_matching")

    def test_no_matching_cohort_no_match(self):
        # Profile whose every available dimension matches no cohort.
        outcomes = _resolved(30, 0.6, instrument="NIFTY", seed=1)
        profile = OpportunityProfile(
            instrument="DOES_NOT_EXIST", direction="NEITHER",
            setup_type="NOPE", mtf_alignment="NONE",
        )
        c = self._lookup_check(outcomes, profile=profile)
        assert c.passed
        assert "NO_MATCH" in c.detail

    def test_exact_single_dimension_match(self):
        outcomes = _resolved(30, 0.6, instrument="NIFTY", seed=2)
        profile = _profile(instrument="NIFTY")
        c = self._lookup_check(outcomes, profile=profile)
        assert c.passed

    def test_exact_composite_match(self):
        outcomes = _resolved(30, 0.6, instrument="NIFTY", direction="LONG", setup_type="TREND_CONTINUATION", seed=3)
        profile = _profile(instrument="NIFTY", direction="LONG", setup_type="TREND_CONTINUATION")
        c = self._lookup_check(outcomes, profile=profile)
        assert c.passed

    def test_more_specific_over_less_specific(self):
        outcomes = _resolved(30, 0.6, instrument="NIFTY", direction="LONG", setup_type="TREND_CONTINUATION", seed=4)
        # Profile with 3 dimensions -> most-specific composite selected.
        profile = OpportunityProfile(
            instrument="NIFTY", direction="LONG", setup_type="TREND_CONTINUATION",
        )
        c = self._lookup_check(outcomes, profile=profile)
        assert c.passed

    def test_lookup_dimension_cap(self):
        outcomes = _resolved(30, 0.6, instrument="NIFTY", direction="LONG", seed=5)
        # Even with many dimensions, lookup_max_dimensions=2 caps specificity.
        profile = OpportunityProfile(
            instrument="NIFTY", direction="LONG",
            setup_type="TREND_CONTINUATION", mtf_alignment="ALIGNED",
            decision="QUALIFIED",
        )
        c = self._lookup_check(outcomes, profile=profile)
        assert c.passed

    def test_unavailable_metadata_no_match(self):
        outcomes = _resolved(30, 0.6, instrument="NIFTY", seed=6)
        profile = OpportunityProfile(instrument="NIFTY", setup_type="NONEXISTENT_SETUP")
        c = self._lookup_check(outcomes, profile=profile)
        assert c.passed

    def test_deterministic_repeated_lookup(self):
        outcomes = _resolved(30, 0.6, instrument="NIFTY", seed=7)
        c = self._lookup_check(outcomes)
        assert c.passed
        assert "deterministic" in c.detail.lower()

    def test_no_match_fabricates_nothing(self):
        outcomes = _resolved(30, 0.6, instrument="NIFTY", seed=8)
        profile = _profile(instrument="MISSING")
        c = self._lookup_check(outcomes, profile=profile)
        assert c.passed


# ============================================================
# F. DECISION-INTEGRATION FAILURE ISOLATION
# ============================================================


class TestIntegrationIsolation:
    def _integ_check(self, outcomes, decision, profile=None):
        r = _validate(outcomes, decision=decision, profile=profile)
        return _check_by_name(r.scenarios[0], "integration_isolation")

    def test_missing_decision_unavailable(self):
        outcomes = _resolved(30, 0.6, seed=1)
        r = _validate(outcomes, decision=None)
        c = _check_by_name(r.scenarios[0], "integration_isolation")
        assert c.status == RobustnessCheckStatus.UNAVAILABLE

    def test_sufficient_evidence_preserves_decision(self):
        outcomes = _resolved(40, 0.7, instrument="NIFTY", seed=2)
        dec = _decision(classification="QUALIFIED")
        c = self._integ_check(outcomes, dec, profile=_profile(instrument="NIFTY"))
        assert c.passed
        assert "QUALIFIED" in c.detail

    def test_insufficient_evidence_preserves_decision(self):
        outcomes = _resolved(5, 1.0, instrument="NIFTY", seed=3)
        dec = _decision(classification="WATCH")
        c = self._integ_check(outcomes, dec, profile=_profile(instrument="NIFTY"))
        assert c.passed

    def test_no_match_evidence_preserves_decision(self):
        outcomes = _resolved(30, 0.6, instrument="NIFTY", seed=4)
        dec = _decision(classification="PREFERRED")
        c = self._integ_check(outcomes, dec, profile=_profile(instrument="MISSING"))
        assert c.passed

    def test_unavailable_intelligence_preserves_decision(self):
        outcomes = _resolved(30, 0.6, seed=5)
        dec = _decision(classification="QUALIFIED")
        c = self._integ_check(outcomes, dec)
        # The check tests both available + unavailable intelligence;
        # the unavailable path must produce UNAVAILABLE status internally.
        assert c.passed
        assert "UNAVAILABLE" in c.detail

    def test_existing_decision_retained_by_reference(self):
        outcomes = _resolved(30, 0.6, seed=6)
        dec = _decision()
        c = self._integ_check(outcomes, dec)
        assert c.passed
        assert "reference" in c.detail.lower()

    def test_all_classifications_preserved(self):
        outcomes = _resolved(40, 0.7, instrument="NIFTY", seed=7)
        for cls in ("REJECTED", "WATCH", "QUALIFIED", "PREFERRED"):
            dec = _decision(classification=cls)
            c = self._integ_check(outcomes, dec, profile=_profile(instrument="NIFTY"))
            assert c.passed, f"{cls} not preserved"


# ============================================================
# G. SERIALIZATION ADVERSARIAL CASES
# ============================================================


class TestSerializationAdversarial:
    def _ser_check(self, outcomes, decision=None, profile=None):
        r = _validate(outcomes, decision=decision, profile=profile)
        return _check_by_name(r.scenarios[0], "serialization_adversarial")

    def test_round_trip_pass(self):
        outcomes = _resolved(30, 0.6, seed=1)
        assert self._ser_check(outcomes).passed

    def test_empty_skipped(self):
        r = _validate([])
        c = _check_by_name(r.scenarios[0], "serialization_adversarial")
        assert c.skipped

    def test_round_trip_preserves_ids(self):
        outcomes = _resolved(30, 0.6, seed=2)
        r = _validate(outcomes)
        rt = deserialize_robustness(serialize_robustness(r))
        assert rt.validation_id == r.validation_id
        assert rt.overall_status == r.overall_status
        assert rt.scenario_count == r.scenario_count

    def test_deserialize_serialize_stable(self):
        outcomes = _resolved(30, 0.6, seed=3)
        r = _validate(outcomes)
        rt = deserialize_robustness(serialize_robustness(r))
        assert serialize_robustness(rt) == serialize_robustness(r)

    def test_canonical_json_identical(self):
        outcomes = _resolved(30, 0.6, seed=4)
        r = _validate(outcomes)
        assert canonical_robustness_json(r) == serialize_robustness(r)

    def test_malformed_schema_version_rejected(self):
        import json
        bad = json.dumps({"schema_version": 999, "report": {}})
        with pytest.raises(ValueError):
            deserialize_robustness(bad)

    def test_missing_schema_version_rejected(self):
        import json
        bad = json.dumps({"report": {}})
        with pytest.raises(ValueError):
            deserialize_robustness(bad)

    def test_parse_header_returns_schema(self):
        outcomes = _resolved(30, 0.6, seed=5)
        r = _validate(outcomes)
        header = parse_robustness_header(serialize_robustness(r))
        assert header["schema_version"] == ROBUSTNESS_VALIDATION_SCHEMA_VERSION

    def test_schema_version_is_1(self):
        assert ROBUSTNESS_VALIDATION_SCHEMA_VERSION == 1

    def test_bytes_round_trip(self):
        outcomes = _resolved(30, 0.6, seed=6)
        r = _validate(outcomes)
        b = serialize_robustness_bytes(r)
        assert isinstance(b, bytes)
        rt = deserialize_robustness(b.decode("utf-8"))
        assert rt.validation_id == r.validation_id

    def test_unavailable_values_preserved(self):
        # Empty scenario -> SKIPPED statuses must round-trip.
        r = _validate([], edge_case="empty")
        rt = deserialize_robustness(serialize_robustness(r))
        assert rt.scenarios[0].checks[0].status == r.scenarios[0].checks[0].status


# ============================================================
# H. DETERMINISM / SHUFFLE INVARIANCE
# ============================================================


class TestDeterminismShuffle:
    def _det_check(self, outcomes, profile=None):
        r = _validate(outcomes, profile=profile)
        return _check_by_name(r.scenarios[0], "determinism_shuffle")

    def test_repeated_pass(self):
        outcomes = _resolved(30, 0.6, seed=1)
        assert self._det_check(outcomes).passed

    def test_empty_skipped(self):
        r = _validate([])
        c = _check_by_name(r.scenarios[0], "determinism_shuffle")
        assert c.skipped

    def test_repeated_validation_same_id(self):
        outcomes = _resolved(30, 0.6, seed=2)
        eng = RobustnessValidationEngine()
        r1 = eng.validate([{"name": "s", "outcomes": tuple(outcomes)}])
        r2 = eng.validate([{"name": "s", "outcomes": tuple(outcomes)}])
        assert r1.validation_id == r2.validation_id

    def test_shuffled_input_same_id(self):
        outcomes = _resolved(30, 0.6, seed=3)
        shuffled = list(outcomes)
        random.Random(99).shuffle(shuffled)
        eng = RobustnessValidationEngine()
        r1 = eng.validate([{"name": "s", "outcomes": tuple(outcomes)}])
        r2 = eng.validate([{"name": "s", "outcomes": tuple(shuffled)}])
        assert r1.validation_id == r2.validation_id

    def test_determinism_status_pass(self):
        outcomes = _resolved(30, 0.6, seed=4)
        r = _validate(outcomes)
        assert r.determinism_status == RobustnessCheckStatus.PASS

    def test_shuffled_same_overall(self):
        outcomes = _resolved(30, 0.6, seed=5)
        shuffled = list(outcomes)
        random.Random(7).shuffle(shuffled)
        eng = RobustnessValidationEngine()
        r1 = eng.validate([{"name": "s", "outcomes": tuple(outcomes)}])
        r2 = eng.validate([{"name": "s", "outcomes": tuple(shuffled)}])
        assert r1.overall_status == r2.overall_status
        assert r1.passed_count == r2.passed_count


# ============================================================
# I. INPUT IMMUTABILITY
# ============================================================


class TestInputImmutability:
    def _imm_check(self, outcomes, decision=None, profile=None):
        r = _validate(outcomes, decision=decision, profile=profile)
        return _check_by_name(r.scenarios[0], "input_immutability")

    def test_outcomes_not_mutated(self):
        outcomes = _resolved(30, 0.6, seed=1)
        snap = [(o.outcome_status, o.realized_r) for o in outcomes]
        _validate(outcomes, decision=_decision())
        after = [(o.outcome_status, o.realized_r) for o in outcomes]
        assert snap == after

    def test_immutability_check_passes(self):
        outcomes = _resolved(30, 0.6, seed=2)
        assert self._imm_check(outcomes, decision=_decision()).passed

    def test_empty_skipped(self):
        r = _validate([])
        c = _check_by_name(r.scenarios[0], "input_immutability")
        assert c.skipped

    def test_reference_identity_preserved(self):
        outcomes = _resolved(30, 0.6, seed=3)
        c = self._imm_check(outcomes, decision=_decision())
        assert c.passed
        assert "reference" in c.detail.lower()

    def test_repeated_validation_does_not_mutate(self):
        outcomes = _resolved(30, 0.6, seed=4)
        eng = RobustnessValidationEngine()
        snap = [(o.outcome_status, o.realized_r) for o in outcomes]
        eng.validate([{"name": "s", "outcomes": tuple(outcomes), "decision": _decision()}])
        eng.validate([{"name": "s", "outcomes": tuple(outcomes), "decision": _decision()}])
        after = [(o.outcome_status, o.realized_r) for o in outcomes]
        assert snap == after


# ============================================================
# J. FAILURE ISOLATION
# ============================================================


class TestFailureIsolation:
    def test_two_scenarios_isolated(self):
        eng = RobustnessValidationEngine()
        r = eng.validate([
            {"name": "a", "outcomes": tuple(_resolved(30, 0.6, seed=1))},
            {"name": "b", "outcomes": tuple(_resolved(20, 0.4, seed=2))},
        ])
        # failure_isolation is a report-level check.
        iso = [c for c in r.report_checks if c.name == "failure_isolation"]
        assert len(iso) == 1
        assert iso[0].passed

    def test_single_scenario_skipped(self):
        eng = RobustnessValidationEngine()
        r = eng.validate([{"name": "only", "outcomes": tuple(_resolved(10))}])
        iso = [c for c in r.report_checks if c.name == "failure_isolation"]
        assert iso[0].skipped

    def test_invalid_scenario_does_not_corrupt_valid(self):
        eng = RobustnessValidationEngine()
        r = eng.validate([
            {"name": "bad", "outcomes": ("not-an-outcome",)},
            {"name": "ok", "outcomes": tuple(_resolved(30, 0.6, seed=3))},
        ])
        ok = next(s for s in r.scenarios if s.name == "ok")
        # The valid scenario must still have run checks (not all skipped).
        assert ok.passed

    def test_empty_plus_valid_isolated(self):
        eng = RobustnessValidationEngine()
        r = eng.validate([
            {"name": "empty", "outcomes": ()},
            {"name": "valid", "outcomes": tuple(_resolved(30, 0.6, seed=4))},
        ])
        valid = next(s for s in r.scenarios if s.name == "valid")
        assert valid.passed


# ============================================================
# K. CROSS-LAYER CONSISTENCY
# ============================================================


class TestCrossLayerConsistency:
    def _cross_check(self, outcomes, decision=None, profile=None):
        r = _validate(outcomes, decision=decision, profile=profile)
        return _check_by_name(r.scenarios[0], "cross_layer_consistency")

    def test_sufficient_passes(self):
        outcomes = _resolved(40, 0.7, instrument="NIFTY", seed=1)
        c = self._cross_check(outcomes, decision=_decision(), profile=_profile(instrument="NIFTY"))
        assert c.passed

    def test_insufficient_stays_insufficient(self):
        outcomes = _resolved(5, 1.0, instrument="NIFTY", seed=2)
        c = self._cross_check(outcomes, decision=_decision(), profile=_profile(instrument="NIFTY"))
        assert c.passed

    def test_existing_decision_authoritative(self):
        outcomes = _resolved(40, 0.7, instrument="NIFTY", seed=3)
        dec = _decision(classification="QUALIFIED")
        c = self._cross_check(outcomes, decision=dec, profile=_profile(instrument="NIFTY"))
        assert c.passed
        assert "authoritative" in c.detail.lower()

    def test_empty_skipped(self):
        r = _validate([])
        c = _check_by_name(r.scenarios[0], "cross_layer_consistency")
        assert c.skipped

    def test_integration_contextual_not_new_decision(self):
        outcomes = _resolved(40, 0.7, instrument="NIFTY", seed=4)
        r = _validate(outcomes, decision=_decision(classification="QUALIFIED"), profile=_profile(instrument="NIFTY"))
        cross = _check_by_name(r.scenarios[0], "cross_layer_consistency")
        assert cross.passed

    def test_below_min_not_promoted_at_any_layer(self):
        outcomes = _resolved(3, 1.0, instrument="NIFTY", seed=5)
        r = _validate(outcomes, decision=_decision(), profile=_profile(instrument="NIFTY"))
        cross = _check_by_name(r.scenarios[0], "cross_layer_consistency")
        assert cross.passed


# ============================================================
# L. ACCOUNTING INVARIANTS
# ============================================================


class TestAccountingInvariants:
    def _acc_check(self, outcomes):
        r = _validate(outcomes)
        return _check_by_name(r.scenarios[0], "accounting_invariants")

    def test_mixed_passes(self):
        outcomes = _resolved(30, 0.6, seed=1)
        assert self._acc_check(outcomes).passed

    def test_empty_skipped(self):
        r = _validate([])
        c = _check_by_name(r.scenarios[0], "accounting_invariants")
        assert c.skipped

    def test_status_counts_reconcile(self):
        outcomes = [
            _outcome(OutcomeStatus.TARGET_HIT, i=0, realized_r=2.0),
            _outcome(OutcomeStatus.STOP_HIT, i=1, realized_r=-1.0),
            _outcome(OutcomeStatus.EXPIRED, i=2, realized_r=0.1),
            _outcome(OutcomeStatus.BOTH_TOUCHED, i=3),
            _outcome(OutcomeStatus.NO_GEOMETRY, i=4),
        ]
        assert self._acc_check(outcomes).passed

    def test_valid_r_count_reconciles(self):
        outcomes = [
            _outcome(OutcomeStatus.TARGET_HIT, i=k, realized_r=2.0) for k in range(10)
        ] + [_outcome(OutcomeStatus.BOTH_TOUCHED, i=20) for _ in range(5)]
        assert self._acc_check(outcomes).passed

    def test_gross_positive_negative_reconcile(self):
        outcomes = (
            [_outcome(OutcomeStatus.TARGET_HIT, i=k, realized_r=2.0) for k in range(8)]
            + [_outcome(OutcomeStatus.STOP_HIT, i=k, realized_r=-1.0) for k in range(8, 16)]
        )
        assert self._acc_check(outcomes).passed

    def test_breakdown_totals_equal_overall(self):
        outcomes = (
            _resolved(15, 0.6, instrument="NIFTY", seed=2)
            + _resolved(15, 0.4, instrument="TCS", seed=3)
        )
        assert self._acc_check(outcomes).passed

    def test_ambiguous_excluded_from_winloss(self):
        outcomes = [_outcome(OutcomeStatus.BOTH_TOUCHED, i=k) for k in range(12)]
        c = self._acc_check(outcomes)
        assert c.passed

    def test_unavailable_values_remain_unavailable(self):
        outcomes = [_outcome(OutcomeStatus.NO_GEOMETRY, i=k) for k in range(10)]
        c = self._acc_check(outcomes)
        assert c.passed

    def test_accounting_status_pass(self):
        outcomes = _resolved(30, 0.6, seed=4)
        r = _validate(outcomes)
        assert r.accounting_status == RobustnessCheckStatus.PASS


# ============================================================
# M. REPORTING HONESTY
# ============================================================


class TestReportingHonesty:
    def _rep_check(self, outcomes):
        r = _validate(outcomes)
        return _check_by_name(r.scenarios[0], "reporting_honesty")

    def test_passes(self):
        outcomes = _resolved(30, 0.6, seed=1)
        assert self._rep_check(outcomes).passed

    def test_empty_skipped(self):
        r = _validate([])
        c = _check_by_name(r.scenarios[0], "reporting_honesty")
        assert c.skipped

    def test_no_predictive_language(self):
        outcomes = _resolved(30, 0.6, seed=2)
        r = _validate(outcomes)
        c = _check_by_name(r.scenarios[0], "reporting_honesty")
        assert c.passed
        assert "descriptive" in c.detail.lower()

    def test_robustness_report_has_limitations(self):
        outcomes = _resolved(30, 0.6, seed=3)
        r = _validate(outcomes)
        fmt = RobustnessValidationFormatter()
        text = fmt.format(r)
        assert "Limitations" in text
        assert "does not establish predictive validity" in text

    def test_robustness_report_no_buy_sell(self):
        outcomes = _resolved(30, 0.6, seed=4)
        r = _validate(outcomes)
        text = RobustnessValidationFormatter().format(r).lower()
        for phrase in ("buy signal", "sell signal", "guaranteed profit", "will rise"):
            assert phrase not in text

    def test_report_distinguishes_statuses(self):
        outcomes = _resolved(30, 0.6, seed=5)
        r = _validate(outcomes)
        text = RobustnessValidationFormatter().format(r)
        assert "Overall Status" in text
        assert "Determinism" in text
        assert "Look-Ahead" in text
        assert "Accounting" in text
        assert "Serialization" in text

    def test_report_shows_skipped(self):
        r = _validate([], edge_case="empty")
        text = RobustnessValidationFormatter().format(r)
        assert "SKIPPED" in text or "skipped" in text


# ============================================================
# N. PIPELINE REGRESSION / NO-LOOK-AHEAD / MODEL / CONFIG
# ============================================================


class TestPipelineRegression:
    def test_pipeline_baseline_4_3(self):
        result = HistoricalEvaluationPipeline(PipelineConfig()).evaluate(
            trending_dataset(),
        )
        assert result.signals_generated == 4
        assert result.performance.completed_trades == 3

    def test_pipeline_regression_check_pass(self):
        outcomes = _resolved(30, 0.6, seed=1)
        r = _validate(outcomes)
        assert r.pipeline_regression_status == RobustnessCheckStatus.PASS

    def test_pipeline_regression_check_attached(self):
        outcomes = _resolved(30, 0.6, seed=2)
        r = _validate(outcomes)
        # pipeline_regression is a report-level check.
        pipe = [c for c in r.report_checks if c.name == "pipeline_regression"]
        assert len(pipe) == 1
        assert pipe[0].passed


class TestNoLookAhead:
    def _la_check(self, outcomes, decision=None, profile=None):
        r = _validate(outcomes, decision=decision, profile=profile)
        return _check_by_name(r.scenarios[0], "no_look_ahead")

    def test_passes(self):
        outcomes = _resolved(30, 0.6, seed=1)
        assert self._la_check(outcomes).passed

    def test_empty_skipped(self):
        r = _validate([])
        c = _check_by_name(r.scenarios[0], "no_look_ahead")
        assert c.skipped

    def test_works_with_evaluator_patched_to_raise(self):
        outcomes = _resolved(30, 0.6, seed=2)
        c = self._la_check(outcomes)
        assert c.passed
        # The check internally patches OutcomeEvaluator + pipeline.
        assert OutcomeEvaluator.evaluate is not None

    def test_look_ahead_status_pass(self):
        outcomes = _resolved(30, 0.6, seed=3)
        r = _validate(outcomes)
        assert r.look_ahead_status == RobustnessCheckStatus.PASS

    def test_no_candle_param_in_public_api(self):
        import inspect
        sig = inspect.signature(RobustnessValidationEngine.validate)
        params = sig.parameters
        assert "candles" not in params
        assert "future" not in str(params).lower()


# ============================================================
# MODEL TESTS
# ============================================================


class TestModels:
    def test_check_status_members(self):
        assert {s.name for s in RobustnessCheckStatus} == {
            "PASS", "FAIL", "SKIPPED", "UNAVAILABLE", "INVALID",
        }

    def test_check_status_is_pass(self):
        assert RobustnessCheckStatus.PASS.is_pass
        assert not RobustnessCheckStatus.FAIL.is_pass

    def test_check_result_ran(self):
        assert RobustnessCheckResult(
            name="x", category=RobustnessCategory.EMPTY_MINIMAL_INPUTS,
            status=RobustnessCheckStatus.PASS,
        ).ran
        assert RobustnessCheckResult(
            name="x", category=RobustnessCategory.EMPTY_MINIMAL_INPUTS,
            status=RobustnessCheckStatus.FAIL,
        ).ran
        assert not RobustnessCheckResult(
            name="x", category=RobustnessCategory.EMPTY_MINIMAL_INPUTS,
            status=RobustnessCheckStatus.SKIPPED,
        ).ran

    def test_check_result_skipped_covers_unavailable_invalid(self):
        for st in (RobustnessCheckStatus.SKIPPED, RobustnessCheckStatus.UNAVAILABLE, RobustnessCheckStatus.INVALID):
            assert RobustnessCheckResult(
                name="x", category=RobustnessCategory.EMPTY_MINIMAL_INPUTS,
                status=st,
            ).skipped

    def test_category_members(self):
        cats = {c.name for c in RobustnessCategory}
        assert "EMPTY_MINIMAL_INPUTS" in cats
        assert "FAILURE_ISOLATION" in cats
        assert "CROSS_LAYER_CONSISTENCY" in cats

    def test_scenario_result_passed(self):
        sc = RobustnessScenarioResult(
            name="s", outcome_count=1,
            checks=(
                RobustnessCheckResult("a", RobustnessCategory.EMPTY_MINIMAL_INPUTS, RobustnessCheckStatus.PASS),
            ),
        )
        assert sc.passed
        assert sc.failed_checks == ()

    def test_scenario_result_not_run(self):
        sc = RobustnessScenarioResult(
            name="s", outcome_count=0,
            checks=(
                RobustnessCheckResult("a", RobustnessCategory.EMPTY_MINIMAL_INPUTS, RobustnessCheckStatus.SKIPPED),
            ),
        )
        assert not sc.passed
        assert sc.not_run_count == 1

    def test_category_summary_passed_category(self):
        cs = RobustnessCategorySummary(
            category=RobustnessCategory.ACCOUNTING_INVARIANTS,
            total=2, passed=2, failed=0, skipped=0,
        )
        assert cs.passed_category

    def test_category_summary_failed(self):
        cs = RobustnessCategorySummary(
            category=RobustnessCategory.ACCOUNTING_INVARIANTS,
            total=2, passed=1, failed=1, skipped=0,
        )
        assert not cs.passed_category

    def test_report_passed(self):
        r = RobustnessValidationReport(
            validation_id="robustness-x",
            overall_status=RobustnessCheckStatus.PASS,
        )
        assert r.passed

    def test_report_is_empty(self):
        r = RobustnessValidationReport(validation_id="robustness-x")
        assert r.is_empty

    def test_models_frozen(self):
        sc = RobustnessScenarioResult(name="s", outcome_count=0)
        with pytest.raises(Exception):
            sc.name = "other"  # type: ignore[misc]

    def test_check_result_frozen(self):
        c = RobustnessCheckResult("a", RobustnessCategory.EMPTY_MINIMAL_INPUTS, RobustnessCheckStatus.PASS)
        with pytest.raises(Exception):
            c.name = "b"  # type: ignore[misc]

    def test_limitations_constant(self):
        assert "predictive validity" in ROBUSTNESS_VALIDATION_LIMITATIONS
        assert "statistical significance" in ROBUSTNESS_VALIDATION_LIMITATIONS


# ============================================================
# CONFIG TESTS
# ============================================================


class TestConfig:
    def test_defaults(self):
        c = RobustnessValidationConfig()
        assert c.accounting_tolerance == 1e-9
        assert c.evidence_min_sample_total == 30
        assert c.evidence_strong_min_sample == 50
        assert c.lookup_max_dimensions == 2

    def test_negative_tolerance_rejected(self):
        with pytest.raises(ValueError):
            RobustnessValidationConfig(accounting_tolerance=-0.1)

    def test_min_sample_below_one_rejected(self):
        with pytest.raises(ValueError):
            RobustnessValidationConfig(evidence_min_sample_total=0)

    def test_strong_below_min_rejected(self):
        with pytest.raises(ValueError):
            RobustnessValidationConfig(evidence_strong_min_sample=20)

    def test_lookup_max_dimensions_invalid(self):
        with pytest.raises(ValueError):
            RobustnessValidationConfig(lookup_max_dimensions=3)
        with pytest.raises(ValueError):
            RobustnessValidationConfig(lookup_max_dimensions=0)

    def test_frozen(self):
        c = RobustnessValidationConfig()
        with pytest.raises(Exception):
            c.label = "x"  # type: ignore[misc]

    def test_snapshot_sorted(self):
        c = RobustnessValidationConfig()
        snap = c.snapshot()
        keys = [k for k, _ in snap]
        assert keys == sorted(keys)
        assert ("accounting_tolerance", "1e-09") in snap or ("accounting_tolerance", "1e-9") in snap


# ============================================================
# END-TO-END / DETERMINISTIC ID TESTS
# ============================================================


class TestEndToEnd:
    def test_full_matrix_passes(self):
        eng = RobustnessValidationEngine()
        scenarios = [
            {"name": "mixed", "outcomes": tuple(_resolved(40, 0.6, seed=1)), "decision": _decision(), "edge_case": "mixed"},
            {"name": "tiny", "outcomes": tuple(_resolved(5, 1.0, seed=2)), "decision": _decision(), "edge_case": "tiny"},
            {"name": "both", "outcomes": tuple([_outcome(OutcomeStatus.BOTH_TOUCHED, i=k) for k in range(8)]), "edge_case": "all-both-touched"},
            {"name": "empty", "outcomes": (), "edge_case": "empty"},
        ]
        r = eng.validate(scenarios, label="e2e")
        assert r.overall_status == RobustnessCheckStatus.PASS
        assert r.scenario_count == 4

    def test_validation_id_prefix(self):
        r = _validate(_resolved(10))
        assert r.validation_id.startswith("robustness-")

    def test_different_scenarios_different_id(self):
        eng = RobustnessValidationEngine()
        r1 = eng.validate([{"name": "a", "outcomes": tuple(_resolved(10, seed=1))}])
        r2 = eng.validate([{"name": "b", "outcomes": tuple(_resolved(10, seed=2))}])
        assert r1.validation_id != r2.validation_id

    def test_repeated_validation_same_id(self):
        outcomes = _resolved(30, 0.6, seed=5)
        eng = RobustnessValidationEngine()
        r1 = eng.validate([{"name": "s", "outcomes": tuple(outcomes)}])
        r2 = eng.validate([{"name": "s", "outcomes": tuple(outcomes)}])
        assert r1.validation_id == r2.validation_id

    def test_shuffled_scenarios_same_id(self):
        eng = RobustnessValidationEngine()
        sc = [
            {"name": "a", "outcomes": tuple(_resolved(30, seed=1))},
            {"name": "b", "outcomes": tuple(_resolved(30, seed=2))},
        ]
        r1 = eng.validate(sc)
        r2 = eng.validate(list(reversed(sc)))
        # Scenarios sorted by name internally -> same id.
        assert r1.validation_id == r2.validation_id

    def test_edge_case_coverage_populated(self):
        eng = RobustnessValidationEngine()
        r = eng.validate([
            {"name": "a", "outcomes": (), "edge_case": "empty"},
            {"name": "b", "outcomes": tuple(_resolved(30, seed=1)), "edge_case": "mixed"},
        ])
        assert "empty" in r.edge_case_coverage
        assert "mixed" in r.edge_case_coverage

    def test_outcome_distribution_populated(self):
        outcomes = [
            _outcome(OutcomeStatus.TARGET_HIT, i=0, realized_r=2.0),
            _outcome(OutcomeStatus.STOP_HIT, i=1, realized_r=-1.0),
        ]
        r = _validate(outcomes)
        dist = dict(r.outcome_distribution)
        assert dist["TARGET_HIT"] == 1
        assert dist["STOP_HIT"] == 1

    def test_rationale_descriptive(self):
        r = _validate(_resolved(10))
        assert "Descriptive only" in r.rationale
        assert "not predictive" in r.rationale

    def test_no_global_mutable_state(self):
        # Two engines do not interfere.
        e1 = RobustnessValidationEngine()
        e2 = RobustnessValidationEngine()
        outcomes = _resolved(30, 0.6, seed=1)
        r1 = e1.validate([{"name": "s", "outcomes": tuple(outcomes)}])
        r2 = e2.validate([{"name": "s", "outcomes": tuple(outcomes)}])
        assert r1.validation_id == r2.validation_id
