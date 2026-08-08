from types import SimpleNamespace

from engine.intelligence.decision import DecisionEngine
from engine.models.confluence import (
    ConfluenceDirection,
    ConfluenceEvidence,
    ConfluenceResult,
    EvidenceStrength,
)
from engine.models.decision import (
    DecisionDirection,
    DecisionStatus,
    SetupQuality,
)


# ============================================================
# HELPERS
# ============================================================

def make_evidence(
    name,
    direction,
    score,
    strength=EvidenceStrength.STRONG,
    reason="test evidence",
):
    return ConfluenceEvidence(
        name=name,
        direction=direction,
        score=score,
        strength=strength,
        reason=reason,
    )


def make_confluence(
    *,
    direction="BULLISH",
    confidence=80.0,
    bullish_score=80.0,
    bearish_score=0.0,
    net_score=None,
    conflict="LOW",
    evidence=None,
):

    if net_score is None:
        net_score = (
            bullish_score
            - bearish_score
        )

    return ConfluenceResult(
        direction=ConfluenceDirection[
            direction
        ],
        confidence=confidence,
        score=max(
            bullish_score,
            bearish_score,
        ),
        bullish_score=bullish_score,
        bearish_score=bearish_score,
        net_score=net_score,
        conflict=conflict,
        liquidity_score=0.0,
        evidence=tuple(
            evidence or []
        ),
        reasons=tuple(
            item.reason
            for item in (
                evidence or []
            )
        ),
    )


# ============================================================
# BASIC
# ============================================================

def test_none_confluence_is_not_ready():

    engine = DecisionEngine()

    result = engine.analyze(None)

    assert result.direction == (
        DecisionDirection.UNKNOWN
    )

    assert result.status == (
        DecisionStatus.NOT_READY
    )

    assert result.trade_eligible is False


def test_neutral_confluence_is_not_ready():

    engine = DecisionEngine()

    result = engine.analyze(
        make_confluence(
            direction="NEUTRAL",
            confidence=0.0,
            bullish_score=40.0,
            bearish_score=40.0,
            conflict="HIGH",
        )
    )

    assert result.direction == (
        DecisionDirection.NEUTRAL
    )

    assert result.status == (
        DecisionStatus.NOT_READY
    )

    assert result.trade_eligible is False

    assert result.setup_quality == (
        SetupQuality.INVALID
    )


def test_unknown_confluence_is_not_ready():

    engine = DecisionEngine()

    result = engine.analyze(
        make_confluence(
            direction="UNKNOWN",
            confidence=0.0,
            bullish_score=0.0,
            bearish_score=0.0,
            conflict="NONE",
        )
    )

    assert result.direction == (
        DecisionDirection.UNKNOWN
    )

    assert result.trade_eligible is False


# ============================================================
# BULLISH
# ============================================================

def test_strong_bullish_setup_is_ready():

    engine = DecisionEngine()

    evidence = [
        make_evidence(
            "Market Bias",
            ConfluenceDirection.BULLISH,
            30.0,
        ),
        make_evidence(
            "Trend",
            ConfluenceDirection.BULLISH,
            30.0,
        ),
        make_evidence(
            "BOS",
            ConfluenceDirection.BULLISH,
            10.0,
        ),
        make_evidence(
            "Liquidity",
            ConfluenceDirection.BULLISH,
            12.0,
        ),
    ]

    result = engine.analyze(
        make_confluence(
            direction="BULLISH",
            confidence=82.0,
            bullish_score=82.0,
            bearish_score=0.0,
            conflict="NONE",
            evidence=evidence,
        )
    )

    assert result.direction == (
        DecisionDirection.BULLISH
    )

    assert result.status == (
        DecisionStatus.READY
    )

    assert result.trade_eligible is True

    assert result.setup_quality in (
        SetupQuality.STRONG,
        SetupQuality.EXCELLENT,
    )


def test_strong_bearish_setup_is_ready():

    engine = DecisionEngine()

    evidence = [
        make_evidence(
            "Market Bias",
            ConfluenceDirection.BEARISH,
            30.0,
        ),
        make_evidence(
            "Trend",
            ConfluenceDirection.BEARISH,
            30.0,
        ),
        make_evidence(
            "BOS",
            ConfluenceDirection.BEARISH,
            10.0,
        ),
    ]

    result = engine.analyze(
        make_confluence(
            direction="BEARISH",
            confidence=75.0,
            bullish_score=0.0,
            bearish_score=70.0,
            conflict="LOW",
            evidence=evidence,
        )
    )

    assert result.direction == (
        DecisionDirection.BEARISH
    )

    assert result.status == (
        DecisionStatus.READY
    )

    assert result.trade_eligible is True


# ============================================================
# CONFIDENCE
# ============================================================

def test_low_confidence_is_not_ready():

    engine = DecisionEngine()

    result = engine.analyze(
        make_confluence(
            direction="BULLISH",
            confidence=40.0,
            bullish_score=55.0,
            bearish_score=45.0,
            conflict="MEDIUM",
        )
    )

    assert result.status == (
        DecisionStatus.NOT_READY
    )

    assert result.trade_eligible is False


def test_confidence_at_threshold_can_be_ready():

    engine = DecisionEngine()

    evidence = [
        make_evidence(
            "Trend",
            ConfluenceDirection.BULLISH,
            30.0,
        ),
        make_evidence(
            "BOS",
            ConfluenceDirection.BULLISH,
            10.0,
        ),
    ]

    result = engine.analyze(
        make_confluence(
            direction="BULLISH",
            confidence=55.0,
            bullish_score=55.0,
            bearish_score=0.0,
            conflict="LOW",
            evidence=evidence,
        )
    )

    assert result.direction == (
        DecisionDirection.BULLISH
    )

    assert result.status == (
        DecisionStatus.READY
    )


# ============================================================
# CONFLICT
# ============================================================

def test_high_conflict_blocks_setup():

    engine = DecisionEngine()

    result = engine.analyze(
        make_confluence(
            direction="BULLISH",
            confidence=80.0,
            bullish_score=60.0,
            bearish_score=40.0,
            conflict="HIGH",
        )
    )

    assert result.status == (
        DecisionStatus.CONFLICTED
    )

    assert result.trade_eligible is False

    assert result.setup_quality == (
        SetupQuality.INVALID
    )


def test_medium_conflict_is_not_ready():

    engine = DecisionEngine()

    result = engine.analyze(
        make_confluence(
            direction="BULLISH",
            confidence=75.0,
            bullish_score=75.0,
            bearish_score=25.0,
            conflict="MEDIUM",
        )
    )

    assert result.status == (
        DecisionStatus.NOT_READY
    )

    assert result.trade_eligible is False


def test_low_conflict_can_be_ready():

    engine = DecisionEngine()

    result = engine.analyze(
        make_confluence(
            direction="BULLISH",
            confidence=75.0,
            bullish_score=75.0,
            bearish_score=10.0,
            conflict="LOW",
            evidence=[
                make_evidence(
                    "Trend",
                    ConfluenceDirection.BULLISH,
                    30.0,
                ),
                make_evidence(
                    "BOS",
                    ConfluenceDirection.BULLISH,
                    10.0,
                ),
            ],
        )
    )

    assert result.status == (
        DecisionStatus.READY
    )


# ============================================================
# EVIDENCE QUALITY
# ============================================================

def test_no_evidence_has_zero_quality():

    engine = DecisionEngine()

    result = engine.analyze(
        make_confluence(
            direction="BULLISH",
            confidence=0.0,
            bullish_score=0.0,
            bearish_score=0.0,
            conflict="NONE",
            evidence=[],
        )
    )

    assert result.evidence_quality == 0.0


def test_more_aligned_evidence_improves_quality():

    engine = DecisionEngine()

    weak = engine.analyze(
        make_confluence(
            direction="BULLISH",
            confidence=70.0,
            bullish_score=70.0,
            bearish_score=0.0,
            conflict="LOW",
            evidence=[
                make_evidence(
                    "BOS",
                    ConfluenceDirection.BULLISH,
                    10.0,
                ),
            ],
        )
    )

    strong = engine.analyze(
        make_confluence(
            direction="BULLISH",
            confidence=70.0,
            bullish_score=70.0,
            bearish_score=0.0,
            conflict="NONE",
            evidence=[
                make_evidence(
                    "Market Bias",
                    ConfluenceDirection.BULLISH,
                    30.0,
                ),
                make_evidence(
                    "Trend",
                    ConfluenceDirection.BULLISH,
                    30.0,
                ),
                make_evidence(
                    "BOS",
                    ConfluenceDirection.BULLISH,
                    10.0,
                ),
                make_evidence(
                    "Liquidity",
                    ConfluenceDirection.BULLISH,
                    10.0,
                ),
            ],
        )
    )

    assert (
        strong.evidence_quality
        > weak.evidence_quality
    )


# ============================================================
# DIRECTION PRESERVATION
# ============================================================

def test_bullish_direction_is_preserved():

    engine = DecisionEngine()

    result = engine.analyze(
        make_confluence(
            direction="BULLISH",
            confidence=70.0,
            bullish_score=70.0,
            bearish_score=0.0,
            conflict="NONE",
            evidence=[
                make_evidence(
                    "Trend",
                    ConfluenceDirection.BULLISH,
                    30.0,
                ),
            ],
        )
    )

    assert result.direction == (
        DecisionDirection.BULLISH
    )


def test_bearish_direction_is_preserved():

    engine = DecisionEngine()

    result = engine.analyze(
        make_confluence(
            direction="BEARISH",
            confidence=70.0,
            bullish_score=0.0,
            bearish_score=70.0,
            conflict="NONE",
            evidence=[
                make_evidence(
                    "Trend",
                    ConfluenceDirection.BEARISH,
                    30.0,
                ),
            ],
        )
    )

    assert result.direction == (
        DecisionDirection.BEARISH
    )


# ============================================================
# SCORE PRESERVATION
# ============================================================

def test_scores_are_preserved():

    engine = DecisionEngine()

    result = engine.analyze(
        make_confluence(
            direction="BULLISH",
            confidence=80.0,
            bullish_score=80.0,
            bearish_score=20.0,
            conflict="LOW",
        )
    )

    assert result.bullish_score == 80.0
    assert result.bearish_score == 20.0
    assert result.net_score == 60.0


# ============================================================
# EXPLAINABILITY
# ============================================================

def test_reasons_are_generated():

    engine = DecisionEngine()

    result = engine.analyze(
        make_confluence(
            direction="BULLISH",
            confidence=80.0,
            bullish_score=80.0,
            bearish_score=0.0,
            conflict="NONE",
            evidence=[
                make_evidence(
                    "Trend",
                    ConfluenceDirection.BULLISH,
                    30.0,
                ),
            ],
        )
    )

    assert len(result.reasons) > 0

    assert any(
        "bullish" in reason.lower()
        for reason in result.reasons
    )


def test_high_conflict_reason_is_explained():

    engine = DecisionEngine()

    result = engine.analyze(
        make_confluence(
            direction="BULLISH",
            confidence=70.0,
            bullish_score=60.0,
            bearish_score=40.0,
            conflict="HIGH",
        )
    )

    assert any(
        "conflicting" in reason.lower()
        for reason in result.reasons
    )


# ============================================================
# EVIDENCE MAPPING
# ============================================================

def test_confluence_evidence_is_preserved():

    engine = DecisionEngine()

    evidence = [
        make_evidence(
            "Trend",
            ConfluenceDirection.BULLISH,
            30.0,
            reason="Trend is bullish.",
        )
    ]

    result = engine.analyze(
        make_confluence(
            direction="BULLISH",
            confidence=70.0,
            bullish_score=70.0,
            bearish_score=0.0,
            conflict="NONE",
            evidence=evidence,
        )
    )

    assert len(result.evidence) == 1

    assert result.evidence[0].name == "Trend"

    assert result.evidence[0].direction == (
        DecisionDirection.BULLISH
    )

    assert result.evidence[0].score == 30.0


# ============================================================
# SAFETY
# ============================================================

def test_negative_confidence_is_clamped():

    engine = DecisionEngine()

    result = engine.analyze(
        make_confluence(
            direction="BULLISH",
            confidence=-20.0,
            bullish_score=50.0,
            bearish_score=0.0,
            conflict="NONE",
        )
    )

    assert result.confidence == 0.0


def test_missing_evidence_attribute_is_safe():

    engine = DecisionEngine()

    confluence = SimpleNamespace(
        direction="BULLISH",
        confidence=60.0,
        bullish_score=60.0,
        bearish_score=0.0,
        net_score=60.0,
        conflict="NONE",
    )

    result = engine.analyze(
        confluence
    )

    assert result.direction == (
        DecisionDirection.BULLISH
    )

    assert result.evidence == ()