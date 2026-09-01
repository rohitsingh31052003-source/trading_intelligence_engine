"""
Operational Trade Intent model (Checkpoint 14.2).

An ``OperationalTradeIntent`` is an immutable operational snapshot/reference
of a specific :class:`~engine.models.trade_plan.TradePlan`. It is NOT
authorization, NOT an execution permission, NOT an execution command, NOT
an order, NOT a broker request, NOT a broker order, NOT an execution
result, NOT a fill, NOT a position, NOT a portfolio, and NOT a paper
trade.

It is a read-only projection of a VALID ``TradePlan`` that may later be
presented to a future authorization layer. The intent copies authoritative
values from ``TradePlan`` verbatim. It does NOT recalculate entry, stop,
target, quantity, planned risk, maximum risk, reward, R:R, direction, or
any planning geometry.

Design rules:

* Frozen + slots dataclass (matches the rest of the model layer).
* Numeric money values preserve ``Decimal`` semantics from ``Decimal``.
* Optional fields use ``None`` so "unobserved" / "unavailable" is never
  silently reported as a real value.
* ``__post_init__`` validates internal consistency: a structurally valid
  intent must carry a non-empty ``intent_id``, ``plan_id``, ``instrument``,
  a valid direction, valid timestamp relationships, a valid fingerprint
  format, and a valid version.
* Deterministic ``intent_id`` (``"intent-" + sha256[:16]``) and
  ``content_fingerprint`` (``"fp-" + sha256[:16]``) derived from canonical
  content.
* No business logic lives here; the model is a data carrier.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

from engine.models.trade_plan import RiskPlanStatus


# ============================================================
# CONSTANTS
# ============================================================


#: Schema/model version for OperationalTradeIntent.
OPERATIONAL_TRADE_INTENT_VERSION = 1

#: Prefix for intent_id.
INTENT_ID_PREFIX = "intent-"

#: Prefix for content_fingerprint.
FINGERPRINT_PREFIX = "fp-"

#: Length of SHA-256 hex digest prefix used in identity strings.
_ID_DIGEST_LENGTH = 16


# ============================================================
# CANONICALIZATION
# ============================================================


def _canonical_value(value: Any) -> str:
    """Canonical string representation of a value for identity hashing.

    Normalizes ``Decimal`` values so that ``Decimal("1.0")`` and
    ``Decimal("1")`` produce the same canonical string. ``None`` becomes
    ``"null"``. Enums use their stable ``.name``. ``datetime`` uses ISO
    format. Booleans, ints, floats, and strings are tagged by type to
    prevent accidental type confusion.
    """

    if value is None:
        return "null"
    if isinstance(value, bool):
        return f"bool:{value}"
    if isinstance(value, Decimal):
        return f"dec:{value.normalize()!s}"
    if isinstance(value, Enum):
        return f"enum:{type(value).__name__}.{value.name}"
    if isinstance(value, datetime):
        return f"dt:{value.isoformat()}"
    if isinstance(value, int):
        return f"int:{value!s}"
    if isinstance(value, float):
        return f"num:{value!s}"
    return f"str:{value!s}"


def _canonical_identity_payload(
    *,
    instrument: str,
    timeframe: str,
    direction: str,
    entry: Decimal | None,
    stop: Decimal | None,
    target_1: Decimal | None,
    engine_risk_distance: Decimal | None,
    engine_reward_distance: Decimal | None,
    engine_risk_reward_ratio: Decimal | None,
    quantity: Decimal | None,
    planned_risk: Decimal | None,
    maximum_risk: Decimal | None,
    risk_plan_status: RiskPlanStatus,
    existing_decision: str,
    actionability: str,
    created_at: datetime,
    evaluation_timestamp: datetime | None,
    label: str,
    metadata: tuple[tuple[str, str], ...],
) -> dict[str, Any]:
    """Build the canonical identity payload for intent_id generation.

    The identity captures the full operational content PLUS the instance
    discriminator (created_at / evaluation_timestamp / label / metadata)
    so two intents derived from the same TradePlan at different times or
    with different labels do not collapse into one identity.
    """

    return {
        "instrument": _canonical_value(instrument),
        "timeframe": _canonical_value(timeframe),
        "direction": _canonical_value(direction),
        "entry": _canonical_value(entry),
        "stop": _canonical_value(stop),
        "target_1": _canonical_value(target_1),
        "engine_risk_distance": _canonical_value(engine_risk_distance),
        "engine_reward_distance": _canonical_value(engine_reward_distance),
        "engine_risk_reward_ratio": _canonical_value(engine_risk_reward_ratio),
        "quantity": _canonical_value(quantity),
        "planned_risk": _canonical_value(planned_risk),
        "maximum_risk": _canonical_value(maximum_risk),
        "risk_plan_status": _canonical_value(risk_plan_status),
        "existing_decision": _canonical_value(existing_decision),
        "actionability": _canonical_value(actionability),
        "created_at": _canonical_value(created_at),
        "evaluation_timestamp": _canonical_value(evaluation_timestamp),
        "label": _canonical_value(label),
        "metadata": sorted(
            _canonical_value(k) + "=" + _canonical_value(v)
            for k, v in metadata
        ),
    }


def _canonical_fingerprint_payload(
    *,
    instrument: str,
    direction: str,
    entry: Decimal | None,
    stop: Decimal | None,
    target_1: Decimal | None,
    engine_risk_distance: Decimal | None,
    engine_reward_distance: Decimal | None,
    engine_risk_reward_ratio: Decimal | None,
    quantity: Decimal | None,
    planned_risk: Decimal | None,
    maximum_risk: Decimal | None,
    risk_plan_status: RiskPlanStatus,
) -> dict[str, Any]:
    """Build the canonical payload for content_fingerprint generation.

    The fingerprint represents the authoritative economic content only.
    It EXCLUDES operational metadata (timestamps, labels, warnings,
    rationale, existing_decision, actionability, metadata) so that the
    fingerprint remains stable across operational context changes that do
    not alter the economic intent.
    """

    return {
        "instrument": _canonical_value(instrument),
        "direction": _canonical_value(direction),
        "entry": _canonical_value(entry),
        "stop": _canonical_value(stop),
        "target_1": _canonical_value(target_1),
        "engine_risk_distance": _canonical_value(engine_risk_distance),
        "engine_reward_distance": _canonical_value(engine_reward_distance),
        "engine_risk_reward_ratio": _canonical_value(engine_risk_reward_ratio),
        "quantity": _canonical_value(quantity),
        "planned_risk": _canonical_value(planned_risk),
        "maximum_risk": _canonical_value(maximum_risk),
        "risk_plan_status": _canonical_value(risk_plan_status),
    }


def _sha256_prefix(payload: dict[str, Any], prefix: str) -> str:
    """Compute a deterministic ``"<prefix>-" + sha256[:16]`` identity."""

    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{prefix}{digest[:_ID_DIGEST_LENGTH]}"


# ============================================================
# MODEL
# ============================================================


@dataclass(frozen=True, slots=True)
class OperationalTradeIntent:
    """
    An immutable operational snapshot/reference of a specific TradePlan.

    This is NOT authorization, NOT execution permission, NOT an execution
    command, NOT an order, NOT a broker request, NOT a position, NOT a
    portfolio, and NOT a paper trade. It is a read-only projection of a
    VALID TradePlan that may later be presented to a future authorization
    layer.

    Attributes:

    intent_id
        Deterministic operational identity (``"intent-" + sha256[:16]``)
        derived from canonical operational content + instance
        discriminator. Distinct from ``plan_id``.

    plan_id
        Provenance reference to the source ``TradePlan``. The intent
        holds this by value (string), not by reference.

    instrument
        Canonical instrument name (copied verbatim from TradePlan).

    timeframe
        Setup timeframe label (copied verbatim from TradePlan).

    direction
        Trade direction (``"LONG"`` / ``"SHORT"`` / ``"NONE"`` / ``""``),
        copied verbatim from TradePlan.

    entry / stop / target_1
        Engine geometry levels copied VERBATIM from TradePlan (``Decimal``
        or ``None``). The intent NEVER recomputes these.

    engine_risk_distance
        The candidate's existing ``risk_distance`` copied verbatim
        (``Decimal`` or ``None``). ENGINE risk, distinct from ACCOUNT
        risk.

    engine_reward_distance
        The candidate's existing ``reward_distance`` copied verbatim.

    engine_risk_reward_ratio
        The candidate's existing ``risk_reward_ratio`` copied verbatim.
        The intent NEVER recomputes R:R.

    quantity
        Position quantity (``Decimal`` or ``None``). Copied verbatim from
        TradePlan.

    planned_risk
        Maximum planned loss (``Decimal`` or ``None``). Copied verbatim.

    maximum_risk
        Risk limit (``Decimal`` or ``None``). Copied verbatim.

    risk_plan_status
        :class:`~engine.models.trade_plan.RiskPlanStatus` of the overall
        risk calculation. Describes the RISK PLAN, NOT the market
        decision.

    existing_decision
        The existing Sprint 11S decision classification name, copied
        verbatim. AUTHORITATIVE; never renamed to BUY/SELL.

    actionability
        The existing ActionabilityState name (copied) when a dashboard
        view was the source, else ``""``.

    created_at
        Timezone-aware creation timestamp. Explicit, deterministic.

    evaluation_timestamp
        Timezone-aware timestamp of when market data was evaluated, or
        ``None`` if not applicable.

    valid_until
        Timezone-aware policy-derived expiry, or ``None`` if no expiry.

    content_fingerprint
        Cryptographic proof of economic intent content
        (``"fp-" + sha256[:16]``). Used to verify that the intent being
        authorized is the same intent later presented for execution.

    version
        Schema/model version (default 1). NOT a strategy version, NOT a
        broker version, NOT an execution version.

    warnings
        Tuple of human-readable validation / honesty warnings. Descriptive
        only. Copied verbatim from TradePlan.

    rationale
        Human-readable summary of how the plan status was reached.
        Copied verbatim from TradePlan.

    label / metadata
        Optional caller-supplied identity / metadata (audit trail).
    """

    # Identity
    intent_id: str
    plan_id: str

    # Instrument / direction
    instrument: str
    timeframe: str
    direction: str

    # Geometry (copied verbatim from TradePlan)
    entry: Decimal | None
    stop: Decimal | None
    target_1: Decimal | None
    engine_risk_distance: Decimal | None
    engine_reward_distance: Decimal | None
    engine_risk_reward_ratio: Decimal | None

    # Position / risk (copied verbatim from TradePlan)
    quantity: Decimal | None
    planned_risk: Decimal | None
    maximum_risk: Decimal | None
    risk_plan_status: RiskPlanStatus

    # Operational context
    existing_decision: str
    actionability: str

    # Timestamps
    created_at: datetime
    evaluation_timestamp: datetime | None
    valid_until: datetime | None

    # Integrity
    content_fingerprint: str
    version: int = OPERATIONAL_TRADE_INTENT_VERSION

    # Audit trail
    warnings: tuple[str, ...] = ()
    rationale: str = ""
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = ()

    @property
    def is_valid(self) -> bool:
        """Whether the intent references a VALID risk plan."""

        return self.risk_plan_status.is_valid

    def __post_init__(self) -> None:
        """Validate internal consistency.

        The factory never produces inconsistent states; these checks guard
        against hand-construction bugs.
        """

        # Identity fields must be non-empty.
        if not self.intent_id:
            raise ValueError("intent_id must be non-empty.")
        if not self.plan_id:
            raise ValueError("plan_id must be non-empty.")

        # Instrument must be non-empty.
        if not self.instrument or not self.instrument.strip():
            raise ValueError("instrument must be non-empty.")

        # Direction must be a recognized value.
        if self.direction not in ("LONG", "SHORT", "NONE", ""):
            raise ValueError(
                f"direction must be LONG, SHORT, NONE, or ''; got {self.direction!r}.",
            )

        # Fingerprint format validation.
        if not self.content_fingerprint.startswith(FINGERPRINT_PREFIX):
            raise ValueError(
                f"content_fingerprint must start with {FINGERPRINT_PREFIX!r}.",
            )

        # Version validation.
        if self.version < 1:
            raise ValueError("version must be >= 1.")

        # Timestamps must be timezone-aware.
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware.")
        if self.evaluation_timestamp is not None:
            if self.evaluation_timestamp.tzinfo is None:
                raise ValueError("evaluation_timestamp must be timezone-aware.")
        if self.valid_until is not None:
            if self.valid_until.tzinfo is None:
                raise ValueError("valid_until must be timezone-aware.")

        # Timestamp relationship: valid_until >= created_at.
        if self.valid_until is not None and self.valid_until < self.created_at:
            raise ValueError("valid_until must be >= created_at.")


# ============================================================
# FACTORY
# ============================================================


def create_intent_from_plan(
    *,
    plan_id: str,
    instrument: str,
    timeframe: str,
    direction: str,
    entry: Decimal | None,
    stop: Decimal | None,
    target_1: Decimal | None,
    engine_risk_distance: Decimal | None,
    engine_reward_distance: Decimal | None,
    engine_risk_reward_ratio: Decimal | None,
    quantity: Decimal | None,
    planned_risk: Decimal | None,
    maximum_risk: Decimal | None,
    risk_plan_status: RiskPlanStatus,
    existing_decision: str,
    actionability: str,
    created_at: datetime,
    evaluation_timestamp: datetime | None = None,
    valid_until: datetime | None = None,
    warnings: tuple[str, ...] = (),
    rationale: str = "",
    label: str = "",
    metadata: tuple[tuple[str, str], ...] = (),
) -> OperationalTradeIntent:
    """Create an OperationalTradeIntent from TradePlan field values.

    This is a pure factory: it copies values verbatim, computes the
    deterministic identity and fingerprint, and performs structural
    validation. It does NOT recalculate any planning values, does NOT
    access candles, and does NOT invoke any market analysis.

    Raises ``ValueError`` if the risk plan status is not VALID, if the
    direction is not LONG/SHORT, or if required fields are missing.
    """

    # Failure contract: only VALID plans produce intents.
    if not risk_plan_status.is_valid:
        raise ValueError(
            "Cannot create intent from non-VALID TradePlan "
            f"(risk_plan_status={risk_plan_status.value}).",
        )

    # Failure contract: intent requires directional bias.
    if direction not in ("LONG", "SHORT"):
        raise ValueError(
            f"Intent requires directional bias (LONG/SHORT); got {direction!r}.",
        )

    # Compute deterministic identity.
    identity_payload = _canonical_identity_payload(
        instrument=instrument,
        timeframe=timeframe,
        direction=direction,
        entry=entry,
        stop=stop,
        target_1=target_1,
        engine_risk_distance=engine_risk_distance,
        engine_reward_distance=engine_reward_distance,
        engine_risk_reward_ratio=engine_risk_reward_ratio,
        quantity=quantity,
        planned_risk=planned_risk,
        maximum_risk=maximum_risk,
        risk_plan_status=risk_plan_status,
        existing_decision=existing_decision,
        actionability=actionability,
        created_at=created_at,
        evaluation_timestamp=evaluation_timestamp,
        label=label,
        metadata=metadata,
    )
    intent_id = _sha256_prefix(identity_payload, INTENT_ID_PREFIX)

    # Compute deterministic content fingerprint.
    fingerprint_payload = _canonical_fingerprint_payload(
        instrument=instrument,
        direction=direction,
        entry=entry,
        stop=stop,
        target_1=target_1,
        engine_risk_distance=engine_risk_distance,
        engine_reward_distance=engine_reward_distance,
        engine_risk_reward_ratio=engine_risk_reward_ratio,
        quantity=quantity,
        planned_risk=planned_risk,
        maximum_risk=maximum_risk,
        risk_plan_status=risk_plan_status,
    )
    content_fingerprint = _sha256_prefix(fingerprint_payload, FINGERPRINT_PREFIX)

    return OperationalTradeIntent(
        intent_id=intent_id,
        plan_id=plan_id,
        instrument=instrument,
        timeframe=timeframe,
        direction=direction,
        entry=entry,
        stop=stop,
        target_1=target_1,
        engine_risk_distance=engine_risk_distance,
        engine_reward_distance=engine_reward_distance,
        engine_risk_reward_ratio=engine_risk_reward_ratio,
        quantity=quantity,
        planned_risk=planned_risk,
        maximum_risk=maximum_risk,
        risk_plan_status=risk_plan_status,
        existing_decision=existing_decision,
        actionability=actionability,
        created_at=created_at,
        evaluation_timestamp=evaluation_timestamp,
        valid_until=valid_until,
        content_fingerprint=content_fingerprint,
        version=OPERATIONAL_TRADE_INTENT_VERSION,
        warnings=warnings,
        rationale=rationale,
        label=label,
        metadata=metadata,
    )


__all__ = [
    "OPERATIONAL_TRADE_INTENT_VERSION",
    "OperationalTradeIntent",
    "create_intent_from_plan",
]
