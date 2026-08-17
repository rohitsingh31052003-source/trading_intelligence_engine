"""
Configuration for the risk & trade planning layer (Product Phase 4).

All thresholds live here; no magic numbers are embedded in the engine.
The defaults are deliberately conservative and deterministic. They are
NOT calibrated to any market; they express interpretable, rule-based
risk-planning constraints.

This config governs ONLY the account-risk / position-sizing calculation.
It MUST NOT make the following configurable (those are AUTHORITATIVE
existing semantics and are never re-configurable by Phase 4):

* the existing decision semantics (REJECTED / WATCH / QUALIFIED /
  PREFERRED)
* the existing actionability semantics
* the existing trade geometry (entry / stop / target / R:R)
* Target 2 support (always unsupported)
* evidence semantics

The core calculation remains deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(slots=True, frozen=True)
class TradePlanConfig:
    """
    Configuration for ``TradePlanningEngine``.

    Threshold semantics (documented in the engine):

    max_risk_percent
        Maximum allowed risk percentage per trade. A ``risk_percent``
        above this is rejected as ``INVALID_INPUT``. Default ``10``
        (10%). This is a SAFETY GUARDRAIL, not a recommendation. It
        prevents a fat-finger risk% from silently sizing an enormous
        position. Must be positive and >= ``min_risk_percent``.

    min_risk_percent
        Minimum allowed risk percentage per trade (exclusive lower
        bound; ``risk_percent`` must be strictly greater than ``0`` and
        ``>= min_risk_percent`` when ``min_risk_percent`` is positive).
        Default ``0`` (no positive lower bound beyond the strict ``> 0``
        requirement). Must be non-negative and <= ``max_risk_percent``.

    allow_fractional_quantity
        Global default for whether fractional quantities are permitted
        when an instrument :class:`~engine.models.trade_plan.QuantitySpec`
        does not override it. When ``False`` the planner floors the
        quantity to the largest integer whose ``planned_risk`` does NOT
        exceed ``maximum_risk``. Default ``True`` (the safe generic model
        can always size a position when geometry is complete).

    quantity_rounding_mode
        Rounding mode for the quantity when fractional quantities are
        disallowed. Only ``"floor"`` is supported — floor is the ONLY
        mode that guarantees ``planned_risk`` never exceeds
        ``maximum_risk``. ``"round"`` / ``"ceil"`` are REJECTED at
        construction because they could over-risk the account.

    monetary_precision
        Number of decimal places retained for monetary ``Decimal`` values
        (capital, maximum_risk, planned_risk, planned_reward). Applied
        only at the presentation / rounding boundary in the engine; the
        underlying calculations use full ``Decimal`` precision. Must be
        non-negative. Default ``2``.

    label / metadata
        Optional identity / metadata carried onto the plan for audit.
    """

    max_risk_percent: Decimal = Decimal("10")
    min_risk_percent: Decimal = Decimal("0")
    allow_fractional_quantity: bool = True
    quantity_rounding_mode: str = "floor"
    monetary_precision: int = 2
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.max_risk_percent, Decimal):
            object.__setattr__(
                self, "max_risk_percent", Decimal(str(self.max_risk_percent)),
            )
        if not isinstance(self.min_risk_percent, Decimal):
            object.__setattr__(
                self, "min_risk_percent", Decimal(str(self.min_risk_percent)),
            )
        if self.max_risk_percent <= 0:
            raise ValueError("max_risk_percent must be positive.")
        if self.min_risk_percent < 0:
            raise ValueError("min_risk_percent must be non-negative.")
        if self.min_risk_percent > self.max_risk_percent:
            raise ValueError(
                "min_risk_percent must not exceed max_risk_percent.",
            )
        if self.quantity_rounding_mode not in ("floor",):
            raise ValueError(
                "quantity_rounding_mode must be 'floor' "
                "(only floor guarantees planned_risk <= maximum_risk).",
            )
        if self.monetary_precision < 0:
            raise ValueError("monetary_precision must be non-negative.")
        if not isinstance(self.label, str):
            raise ValueError("label must be a string.")
        if not isinstance(self.metadata, tuple):
            raise ValueError("metadata must be a tuple of (str, str) pairs.")
        for pair in self.metadata:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise ValueError("metadata entries must be (str, str) pairs.")
            if not isinstance(pair[0], str) or not isinstance(pair[1], str):
                raise ValueError("metadata keys and values must be strings.")


__all__ = ["TradePlanConfig"]
