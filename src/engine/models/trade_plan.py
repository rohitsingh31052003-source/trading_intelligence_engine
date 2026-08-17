"""
Domain models for risk & trade planning (Product Phase 4).

A ``TradePlan`` is a structured, DESCRIPTIVE trade plan derived from an
EXISTING trade candidate / trade geometry plus user-supplied account
risk parameters. It is NOT a BUY/SELL trading signal, NOT a prediction,
NOT a probability, and NOT a guarantee of profitability. It answers ONE
question: *if a trader chooses to review/take the existing trade
candidate, how much should they risk and what position size follows from
their risk rules?*

This layer implements NO market analysis, NO decision logic, NO
prediction and NO execution. The existing Sprint 11S decision
classification (REJECTED / WATCH / QUALIFIED / PREFERRED) is AUTHORITATIVE
and is never renamed / upgraded / downgraded. The existing Sprint 11R
``TradeCandidate`` geometry (entry / stop / target / risk_distance /
reward_distance / risk_reward_ratio) is AUTHORITATIVE and is never
recomputed; Phase 4 only performs deterministic calculations AROUND
those existing values.

Design rules:

* Frozen + slots dataclasses (matches the rest of the model layer).
* Numeric money values are stored as ``Decimal`` so monetary precision is
  preserved across serialization; floats are accepted on construction but
  are normalized to ``Decimal``. The engine performs all financial math
  in ``Decimal``.
* Optional fields use ``None`` so "unobserved" / "unavailable" / "not
  computable" is never silently reported as a real value. In particular,
  quantity / planned_risk / planned_reward are ``None`` when the position
  cannot be sized (incomplete geometry, invalid inputs, unavailable
  quantity spec, risk-limit exceeded).
* ``__post_init__`` validates internal consistency: a VALID plan must
  carry a directional intent, complete geometry, a positive risk
  distance, a non-zero quantity, and a ``planned_risk`` that does NOT
  exceed ``maximum_risk`` (so rounding never silently over-risks the
  account). The engine never produces inconsistent states; the checks
  guard against hand-construction bugs.
* No business logic lives here; the models are data carriers. The
  calculation lives in the engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


def _to_decimal(value) -> Decimal | None:
    """Coerce a value to ``Decimal`` (``None`` stays ``None``).

    Booleans are rejected (they are not money). NaN / infinity are rejected.
    """

    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("Boolean is not a valid monetary value.")
    if isinstance(value, Decimal):
        d = value
    elif isinstance(value, int):
        d = Decimal(value)
    elif isinstance(value, float):
        d = Decimal(str(value))
    elif isinstance(value, str):
        d = Decimal(value)
    else:
        d = Decimal(value)
    if not d.is_finite():
        raise ValueError("Monetary value must be finite (not NaN/infinity).")
    return d


class RiskPlanStatus(Enum):
    """
    Status of a trade plan's RISK CALCULATION (NOT the market decision).

    This is deliberately DISTINCT from the existing market
    :class:`~dashboard.views.ActionabilityState` and from the Sprint 11S
    decision classification. It describes whether the deterministic risk
    calculation produced a usable position size, and if not, why. It
    NEVER upgrades / downgrades the existing decision and NEVER produces a
    BUY/SELL/ENTER/EXIT/HOLD recommendation.

    VALID
        The plan was computed successfully: complete geometry, valid
        account-risk inputs, a positive risk distance, and a quantity
        whose ``planned_risk`` does not exceed the configured
        ``maximum_risk``. The trader may review the resulting position
        size. Descriptive only — does NOT predict success.

    INVALID_INPUT
        One or more user-supplied account-risk inputs were invalid
        (non-positive capital, non-positive / out-of-bounds risk
        percentage, NaN / infinity). No position is sized; nothing is
        fabricated.

    GEOMETRY_UNAVAILABLE
        The existing trade candidate did not produce complete geometry
        (entry / stop missing, or zero / negative risk distance). Without
        a risk distance there is no position to size. The plan surfaces
        the engine geometry verbatim (``None`` where unavailable) and no
        quantity is invented.

    RISK_LIMIT_EXCEEDED
        The smallest valid integer quantity would commit more risk than
        the configured ``maximum_risk`` (e.g. one unit costs more than the
        account is willing to lose). The plan reports this honestly
        instead of fabricating a fractional / oversized position.

    QUANTITY_UNAVAILABLE
        The instrument's quantity specification is unavailable
        (``QuantitySpec`` missing), OR fractional quantity is disallowed
        and the raw division produced a fractional quantity below the
        quantity step. No quantity is invented; the engine surfaces
        ``QUANTITY_SPEC_UNAVAILABLE`` so a downstream caller knows the
        generic model could not size this instrument safely.
    """

    VALID = "VALID"
    INVALID_INPUT = "INVALID_INPUT"
    GEOMETRY_UNAVAILABLE = "GEOMETRY_UNAVAILABLE"
    RISK_LIMIT_EXCEEDED = "RISK_LIMIT_EXCEEDED"
    QUANTITY_UNAVAILABLE = "QUANTITY_UNAVAILABLE"

    @property
    def is_valid(self) -> bool:
        """Whether the plan produced a usable position size."""

        return self is RiskPlanStatus.VALID


class QuantityStatus(Enum):
    """
    Status of the quantity computation within a trade plan.

    DETERMINED
        A concrete quantity was computed (integer when integer-only,
        fractional when allowed). ``planned_risk`` / ``planned_reward``
        reflect the sized position.

    FRACTIONAL_ALLOWED
        The raw division produced a fractional quantity AND fractional
        quantities are allowed; the quantity is a fractional ``Decimal``.

    FLOOR_ROUNDED
        The raw division produced a fractional quantity, fractional
        quantities are disallowed, and the quantity was FLOOR-rounded to
        the largest integer whose ``planned_risk`` does NOT exceed
        ``maximum_risk``. This guarantees rounding never over-risks the
        account.

    UNSIZED
        No quantity was computed (incomplete geometry, invalid inputs,
        risk-limit exceeded, or quantity spec unavailable). ``quantity``
        / ``planned_risk`` / ``planned_reward`` are ``None``.
    """

    DETERMINED = "DETERMINED"
    FRACTIONAL_ALLOWED = "FRACTIONAL_ALLOWED"
    FLOOR_ROUNDED = "FLOOR_ROUNDED"
    UNSIZED = "UNSIZED"


@dataclass(frozen=True, slots=True)
class QuantitySpec:
    """
    Optional instrument-specific quantity specification.

    The repository does NOT contain authoritative broker / exchange
    contract metadata (lot sizes, contract multipliers, tick sizes). To
    avoid fabricating NSE lot sizes or broker-specific rules, the planner
    uses a SAFE GENERIC quantity model by default: a unit quantity step of
    ``1`` with a contract multiplier of ``1`` and fractional quantities
    allowed. A caller MAY supply a real :class:`QuantitySpec` for an
    instrument whose contract semantics are known; when none is supplied
    the planner surfaces ``QUANTITY_SPEC_UNAVAILABLE`` in the warnings so a
    downstream caller knows the generic model was used.

    Attributes:

    quantity_step
        Minimum tradable quantity increment (e.g. ``1`` for shares,
        ``Decimal("0.01")`` for crypto). Must be positive.

    contract_multiplier
        How many underlying units one contract represents. ``1`` for
        simple share-based instruments; ``75`` for one NIFTY lot (when the
        caller has authoritative data). Must be positive.

    allow_fractional_quantity
        Whether fractional quantities are permitted. When ``False`` the
        planner floors the quantity to the largest integer that does NOT
        exceed the configured maximum risk.
    """

    quantity_step: Decimal = Decimal("1")
    contract_multiplier: Decimal = Decimal("1")
    allow_fractional_quantity: bool = False

    def __post_init__(self) -> None:
        step = _to_decimal(self.quantity_step)
        mult = _to_decimal(self.contract_multiplier)
        if step is None or step <= 0:
            raise ValueError("quantity_step must be positive.")
        if mult is None or mult <= 0:
            raise ValueError("contract_multiplier must be positive.")


#: A safe default :class:`QuantitySpec` used when no instrument-specific
#: spec is supplied. Unit step, unit multiplier, fractional allowed so the
#: generic model can always size a position when the geometry is complete.
DEFAULT_QUANTITY_SPEC = QuantitySpec(
    quantity_step=Decimal("1"),
    contract_multiplier=Decimal("1"),
    allow_fractional_quantity=True,
)


@dataclass(frozen=True, slots=True)
class TradePlan:
    """
    A deterministic, descriptive trade plan at one evaluation point.

    A ``TradePlan`` is NOT a trading signal. It is a structured risk /
    position-size calculation around an EXISTING trade candidate. It makes
    no profitability or predictive claim.

    Attributes:

    plan_id
        Deterministic identifier (``"plan-" + sha256[:16]``) derived from
        the canonical normalized plan inputs (instrument, timeframe,
        direction, existing decision, actionability, account capital,
        risk percent, engine geometry, quantity spec, label, metadata).
        Repeated identical inputs produce identical plan ids; changing
        account capital or risk percent changes the plan id. No random /
        wall-clock / memory-address component.

    instrument
        Canonical instrument name (reused).

    timeframe
        Setup timeframe label (reused).

    direction
        Trade direction (``"LONG"`` / ``"SHORT"`` / ``"NONE"`` / ``""``),
        reused from the existing candidate where available. Risk is based
        on the absolute distance between entry and stop for BOTH
        directions.

    existing_decision
        The existing Sprint 11S decision classification name, reused
        verbatim (``"REJECTED"`` / ``"WATCH"`` / ``"QUALIFIED"`` /
        ``"PREFERRED"`` / ``""``). AUTHORITATIVE; never renamed to BUY/SELL
        and never upgraded / downgraded by this plan.

    actionability
        The existing :class:`~dashboard.views.ActionabilityState` name
        (reused) when a dashboard view was the source, else ``""``.

    account_capital
        User-supplied account capital (``Decimal``). ``None`` only for
        invalid-input plans where the capital could not be coerced.

    risk_percent
        User-supplied risk percentage per trade (``Decimal``; e.g.
        ``Decimal("1")`` means 1%). ``None`` only for invalid-input plans.

    maximum_risk
        ``account_capital * risk_percent / 100`` (``Decimal``). The maximum
        monetary amount the plan is willing to lose on this trade. ``None``
        for invalid-input plans.

    entry / stop / target_1
        Engine geometry levels reused VERBATIM from the Sprint 11R
        candidate (``Decimal`` or ``None``). The plan NEVER recomputes a
        second entry / stop / target and NEVER invents a second target.

    engine_risk_distance
        The candidate's existing ``risk_distance`` reused verbatim
        (``Decimal`` or ``None``). This is the ENGINE risk (per-unit risk
        implied by the structural geometry), DISTINCT from the ACCOUNT
        risk (``maximum_risk``).

    engine_reward_distance
        The candidate's existing ``reward_distance`` reused verbatim.

    engine_risk_reward_ratio
        The candidate's existing ``risk_reward_ratio`` reused verbatim.
        The plan NEVER recomputes R:R.

    target_2
        Always ``None`` — the architecture produces a single structural
        target. Surfaced honestly; never invented.

    target_2_supported
        Always ``False`` — documents that a second target is not part of
        the current architecture.

    quantity
        Position quantity (``Decimal`` or ``None``). ``None`` when the
        position could not be sized (incomplete geometry, invalid inputs,
        risk-limit exceeded, quantity spec unavailable). Integer when
        integer-only, fractional when allowed.

    planned_risk
        ``quantity * engine_risk_distance`` (``Decimal`` or ``None``). The
        MAXIMUM PLANNED LOSS if the stop is hit. ``None`` when unsized.

    planned_reward
        ``quantity * engine_reward_distance`` (``Decimal`` or ``None``).
        The POTENTIAL PLANNED REWARD if the target is hit. This is
        deterministic from the engine geometry; it is NOT an expected
        return and NOT a prediction. ``None`` when unsized OR when the
        engine reward distance is unavailable.

    quantity_status
        :class:`QuantityStatus` describing how (or whether) the quantity
        was computed.

    risk_plan_status
        :class:`RiskPlanStatus` of the overall risk calculation. Describes
        the RISK PLAN, NOT the market decision.

    quantity_spec_available
        Whether an instrument-specific :class:`QuantitySpec` was supplied.
        When ``False`` the generic default model was used and the warnings
        surface ``QUANTITY_SPEC_UNAVAILABLE``.

    warnings
        Tuple of human-readable validation / honesty warnings. Descriptive
        only. Includes the ``QUANTITY_SPEC_UNAVAILABLE`` warning when no
        instrument spec was supplied, the geometry-unavailable warning
        when geometry is incomplete, and the risk-limit-exceeded warning
        when the smallest valid position exceeds the maximum risk.

    rationale
        Human-readable summary of how the plan status was reached.

    label / metadata
        Optional caller-supplied identity / metadata (audit trail).

    Notes:
        The model retains the engine geometry by VALUE (copied verbatim),
        not by reference, so a serialized plan is fully self-contained for
        audit. It does NOT duplicate engine semantics — the geometry
        fields are explicitly the engine's values, flagged as such.
    """

    plan_id: str
    instrument: str
    timeframe: str
    direction: str
    existing_decision: str
    actionability: str

    account_capital: Decimal | None
    risk_percent: Decimal | None
    maximum_risk: Decimal | None

    entry: Decimal | None
    stop: Decimal | None
    target_1: Decimal | None
    engine_risk_distance: Decimal | None
    engine_reward_distance: Decimal | None
    engine_risk_reward_ratio: Decimal | None
    target_2: Decimal | None = None
    target_2_supported: bool = False

    quantity: Decimal | None = None
    planned_risk: Decimal | None = None
    planned_reward: Decimal | None = None

    quantity_status: QuantityStatus = QuantityStatus.UNSIZED
    risk_plan_status: RiskPlanStatus = RiskPlanStatus.GEOMETRY_UNAVAILABLE
    quantity_spec_available: bool = False

    warnings: tuple[str, ...] = field(default_factory=tuple)
    rationale: str = ""
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    @property
    def is_valid(self) -> bool:
        """Whether the risk plan produced a usable, sized position."""

        return self.risk_plan_status.is_valid

    @property
    def has_geometry(self) -> bool:
        """Whether the engine geometry entry/stop (risk) is available."""

        return (
            self.entry is not None
            and self.stop is not None
            and self.engine_risk_distance is not None
            and self.engine_risk_distance > 0
        )

    def __post_init__(self) -> None:
        """Validate internal consistency.

        The engine never produces inconsistent states; these checks guard
        against hand-construction bugs and enforce the no-over-risk
        invariant.
        """

        # A VALID plan must carry directional geometry + sized quantity +
        # planned_risk within maximum_risk.
        if self.risk_plan_status is RiskPlanStatus.VALID:
            if self.direction not in ("LONG", "SHORT"):
                raise ValueError(
                    "A VALID trade plan requires a directional intent.",
                )
            if not self.has_geometry:
                raise ValueError(
                    "A VALID trade plan requires complete entry/stop geometry.",
                )
            if self.quantity is None or self.quantity <= 0:
                raise ValueError(
                    "A VALID trade plan requires a positive quantity.",
                )
            if self.planned_risk is None or self.planned_risk <= 0:
                raise ValueError(
                    "A VALID trade plan requires a positive planned_risk.",
                )
            if self.maximum_risk is not None and self.planned_risk > self.maximum_risk:
                raise ValueError(
                    "planned_risk must not exceed maximum_risk (no-over-risk).",
                )
            if self.target_2 is not None or self.target_2_supported:
                raise ValueError(
                    "Target 2 is not supported by the architecture.",
                )
        # Target 2 is never supported, regardless of status.
        if self.target_2 is not None:
            raise ValueError("target_2 must be None (unsupported).")
        if self.target_2_supported:
            raise ValueError("target_2_supported must be False.")

        # planned_risk requires a quantity + risk distance.
        if self.planned_risk is not None:
            if self.quantity is None or self.engine_risk_distance is None:
                raise ValueError(
                    "planned_risk requires a quantity and engine_risk_distance.",
                )
        # planned_reward requires quantity + reward distance.
        if self.planned_reward is not None and (
            self.quantity is None or self.engine_reward_distance is None
        ):
            raise ValueError(
                "planned_reward requires a quantity and engine_reward_distance.",
            )


__all__ = [
    "DEFAULT_QUANTITY_SPEC",
    "QuantitySpec",
    "QuantityStatus",
    "RiskPlanStatus",
    "TradePlan",
]
