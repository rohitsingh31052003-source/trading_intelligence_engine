"""
Domain models for paper trading & real-world validation (Product Phase 5).

A :class:`PaperTrade` is a structured, DESCRIPTIVE record of what an
EXISTING trade opportunity / trade plan WOULD have done if it had been
followed in real / near-live market conditions. It is NOT a trading
signal, NOT a prediction, NOT a probability, NOT a guarantee of
profitability, NOT a BUY/SELL/ENTER/EXIT/HOLD recommendation, and NOT a
broker order. It records observational validation only.

DESIGN PRINCIPLE — every upstream output remains AUTHORITATIVE:

* The existing Sprint 11S decision classification (REJECTED / WATCH /
  QUALIFIED / PREFERRED) is reused VERBATIM (``existing_decision``) and
  is never renamed / upgraded / downgraded. A paper-trade result is a
  SEPARATE concern from the system decision: a ``QUALIFIED`` decision
  that resulted in a ``LOSS`` does NOT become a ``REJECTED`` decision,
  and a ``REJECTED`` decision is never re-classified because a paper
  trade happened to win.
* The existing Sprint 11R ``TradeCandidate`` geometry (entry / stop /
  target / risk_distance / reward_distance / risk_reward_ratio) is
  reused VERBATIM. Target 2 is NOT supported by the architecture and is
  therefore always ``None`` with ``target_2_supported = False`` — never
  invented.
* The existing Product Phase 4 :class:`~engine.models.trade_plan.TradePlan`
  (account capital, risk %, maximum risk, quantity, planned risk /
  reward, contract multiplier) is reused VERBATIM. The paper-trade layer
  performs NO new position sizing and NEVER recomputes quantity / risk
  using different rules.

DESIGN PRINCIPLE — no fabricated values:

Missing values remain ``None``. A paper trade whose geometry is
incomplete receives :attr:`PaperTradeStatus.INVALIDATED` — never a
fabricated entry / exit / P&L / R. A same-candle ambiguity (a single
completed candle touching BOTH stop and target with no intrabar
ordering) is represented explicitly as
:attr:`PaperExitReason.BOTH_TOUCHED` with ``realized_r = None`` and
``realized_pnl = None`` — a winner or loser is NEVER manufactured. This
mirrors the established Sprint 11W ``BOTH_TOUCHED`` / ``NO_GEOMETRY`` /
``INSUFFICIENT_DATA`` semantics; the paper-trade layer never contradicts
those semantics.

Design rules:

* Frozen + slots dataclasses (matches the rest of the model layer).
* Numeric money / R values are stored as ``Decimal`` so monetary
  precision is preserved across serialization; floats are accepted on
  construction but are normalized to ``Decimal``.
* Optional fields use ``None`` so "unobserved" / "unavailable" /
  "ambiguous" is never silently reported as a real value.
* ``__post_init__`` validates internal consistency so the engine never
  produces contradictory states and hand-construction bugs surface
  early.
* No business logic lives here; the models are data carriers. The
  lifecycle / accounting lives in the engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum


def _to_decimal(value) -> Decimal | None:
    """Coerce a value to ``Decimal`` (``None`` stays ``None``).

    Booleans are rejected (they are not money). NaN / infinity are
    rejected. Mirrors the Product Phase 4 ``_to_decimal`` convention so
    monetary precision is preserved.
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


class PaperTradeStatus(Enum):
    """
    Lifecycle status of a paper trade.

    This is DELIBERATELY DISTINCT from the Sprint 11S
    :class:`~engine.models.trade_decision.DecisionClassification`
    (REJECTED / WATCH / QUALIFIED / PREFERRED) and from the Sprint 11T
    :class:`~engine.models.opportunity.OpportunityStatus`. It describes
    the PAPER-TRADE lifecycle, NOT the market decision. A paper trade's
    status never rewrites the original system decision.

    WAITING_FOR_ENTRY
        The paper trade has been created from an opportunity + plan but
        the entry condition has not yet been confirmed by a completed
        market candle after the creation timestamp. No entry has been
        fabricated. The trade remains monitorable.

    OPEN
        The entry condition was confirmed by a completed candle (the
        ``entry_timestamp`` / ``actual_entry_price`` are populated). The
        position is being monitored for a stop / target / manual /
        expired exit.

    CLOSED
        The paper trade has been resolved with a determinate exit
        (``STOP_HIT`` / ``TARGET_HIT`` / ``MANUAL_CLOSE`` / ``EXPIRED`` /
        ``BOTH_TOUCHED``). The ``exit_timestamp`` / ``actual_exit_price``
        / ``exit_reason`` are populated (except ``BOTH_TOUCHED`` which
        carries no fabricated exit price / R).

    CANCELLED
        The human cancelled the paper trade before any entry was
        confirmed. No entry / exit / P&L / R is fabricated.

    INVALIDATED
        The paper trade could not be evaluated: incomplete / invalid
        geometry (``NO_GEOMETRY``) or a non-directional intent. No entry
        / exit / P&L / R is fabricated. The original system decision is
        unchanged.
    """

    WAITING_FOR_ENTRY = "WAITING_FOR_ENTRY"
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"
    INVALIDATED = "INVALIDATED"

    @property
    def is_terminal(self) -> bool:
        """Whether the status is a terminal lifecycle state."""

        return self in (
            PaperTradeStatus.CLOSED,
            PaperTradeStatus.CANCELLED,
            PaperTradeStatus.INVALIDATED,
        )


class PaperExitReason(Enum):
    """
    The descriptive reason a paper trade was resolved.

    DELIBERATELY DISTINCT from the Sprint 11W
    :class:`~engine.models.historical_outcome.OutcomeStatus` (the
    paper-trade layer reuses the SAME ambiguity semantics but exposes
    its own lifecycle vocabulary so a paper trade is never confused with
    a historical outcome evaluation).

    TARGET_HIT
        A completed candle reached the target level (favorable) BEFORE
        the stop was touched. Determinate, favorable. ``actual_exit_price``
        is the target level; ``realized_r`` is the favorable R-multiple.

    STOP_HIT
        A completed candle reached the stop level (adverse) BEFORE the
        target was touched. Determinate, adverse. ``actual_exit_price``
        is the stop level; ``realized_r`` is the adverse R-multiple.

    BOTH_TOUCHED
        A SINGLE completed candle touched BOTH the stop and the target
        and intrabar ordering is unavailable, so the first touch cannot
        be determined deterministically. The result is represented
        explicitly as ambiguous — a winner or loser is NEVER
        manufactured. ``actual_exit_price`` / ``realized_r`` /
        ``realized_pnl`` are ``None``. Reuses the Sprint 11W
        ``BOTH_TOUCHED`` semantics exactly.

    MANUAL_CLOSE
        The human closed the paper trade at an observed price
        (``actual_exit_price``) at ``exit_timestamp``. ``realized_r`` is
        computed from entry / exit / risk. This is a HUMAN action, NOT an
        automatic execution.

    EXPIRED
        Neither the target nor the stop was reached within the
        configured ``max_holding_bars`` horizon after entry.
        ``actual_exit_price`` is the close of the last evaluated candle
        (mark-to-close); ``realized_r`` is the mark-to-close R-multiple.

    NO_GEOMETRY
        The paper trade's geometry was incomplete / invalid (a missing
        level or non-positive risk), so no determinate outcome could be
        evaluated. No entry / exit / P&L / R is fabricated. Reuses the
        Sprint 11W ``NO_GEOMETRY`` semantics.

    CANCELLED
        The human cancelled the paper trade before entry. No exit price
        / P&L / R is fabricated.
    """

    TARGET_HIT = "TARGET_HIT"
    STOP_HIT = "STOP_HIT"
    BOTH_TOUCHED = "BOTH_TOUCHED"
    MANUAL_CLOSE = "MANUAL_CLOSE"
    EXPIRED = "EXPIRED"
    NO_GEOMETRY = "NO_GEOMETRY"
    CANCELLED = "CANCELLED"

    @property
    def is_win(self) -> bool:
        """Whether the exit reason is a determinate favorable outcome."""

        return self is PaperExitReason.TARGET_HIT

    @property
    def is_loss(self) -> bool:
        """Whether the exit reason is a determinate adverse outcome."""

        return self is PaperExitReason.STOP_HIT

    @property
    def is_ambiguous(self) -> bool:
        """Whether the exit reason is ambiguous (never win/loss)."""

        return self is PaperExitReason.BOTH_TOUCHED


@dataclass(frozen=True, slots=True)
class PaperTrade:
    """
    An immutable record of one paper trade.

    The model retains the engine geometry + the Product Phase 4 trade
    plan BY VALUE (copied verbatim) so a serialized paper trade is fully
    self-contained for audit. It does NOT duplicate engine semantics —
    the geometry / plan fields are explicitly the engine's values,
    flagged as such.

    Attributes:

    paper_trade_id
        Deterministic identity (``"pt-" + sha256[:16]``). Distinct from
        the opportunity identity (``plan_id``) so two paper trades
        created from the same opportunity do not collapse into one
        record. The id canonicalizes the opportunity (plan_id /
        instrument / timeframe / direction / decision / setup_type /
        geometry) PLUS the instance discriminator (creation timestamp +
        sequence + label + metadata).

    instrument / timeframe / direction
        Reused verbatim from the opportunity / plan.

    existing_decision
        Sprint 11S decision classification name (``REJECTED`` /
        ``WATCH`` / ``QUALIFIED`` / ``PREFERRED``) reused VERBATIM. The
        paper-trade layer NEVER renames / upgrades / downgrades it.

    setup_type
        Sprint 11R setup type name reused verbatim, or ``""``.

    plan_id
        The Product Phase 4 trade plan id this paper trade was created
        from (opportunity identity). ``""`` when no plan was supplied.

    created_at
        The paper-trade creation timestamp (the human action time, or the
        evaluation timestamp at creation). Explicit caller-supplied so
        tests are deterministic (no wall-clock).

    evaluation_timestamp
        The market evaluation timestamp the opportunity was generated at
        (the close of the latest completed setup candle at creation).

    entry / stop / target_1
        Engine geometry levels reused VERBATIM (``Decimal`` or ``None``).
        The paper-trade layer NEVER recomputes a second entry / stop /
        target.

    target_2 / target_2_supported
        Always ``None`` / ``False`` — the architecture produces a single
        structural target. Surfaced honestly; never invented.

    engine_risk_distance / engine_reward_distance / engine_risk_reward_ratio
        Reused verbatim from the Sprint 11R candidate / Phase 4 plan.

    planned_quantity / planned_risk / maximum_risk / account_capital /
    risk_percent
        Reused VERBATIM from the Product Phase 4 trade plan. The
        paper-trade layer performs NO new position sizing.

    status
        :class:`PaperTradeStatus` lifecycle status.

    entry_timestamp / actual_entry_price
        Populated when the entry condition was confirmed by a completed
        candle (``OPEN`` / ``CLOSED``). ``None`` while
        ``WAITING_FOR_ENTRY`` / ``CANCELLED`` / ``INVALIDATED``.

    exit_timestamp / actual_exit_price / exit_reason
        Populated when the trade was resolved (``CLOSED``). ``None``
        while not closed. ``actual_exit_price`` / ``realized_r`` /
        ``realized_pnl`` are ``None`` for ``BOTH_TOUCHED`` (ambiguous).

    realized_r / realized_pnl
        Realized R-multiple and realized P&L (``Decimal`` or ``None``).
        Computed from entry / exit / risk / quantity. ``None`` for
        ``BOTH_TOUCHED``, ``NO_GEOMETRY``, ``CANCELLED``, and any
        unresolved state. NEVER fabricated.

    label / metadata
        Optional caller-supplied identity / metadata (audit trail).

    sequence
        Caller-supplied instance discriminator (default ``0``) so two
        paper trades created from the same opportunity at the same
        ``created_at`` do not collapse into one record.
    """

    paper_trade_id: str
    instrument: str
    timeframe: str
    direction: str
    existing_decision: str
    setup_type: str
    plan_id: str
    created_at: datetime
    evaluation_timestamp: datetime | None

    entry: Decimal | None
    stop: Decimal | None
    target_1: Decimal | None
    target_2: Decimal | None = None
    target_2_supported: bool = False
    engine_risk_distance: Decimal | None = None
    engine_reward_distance: Decimal | None = None
    engine_risk_reward_ratio: Decimal | None = None

    planned_quantity: Decimal | None = None
    planned_risk: Decimal | None = None
    maximum_risk: Decimal | None = None
    account_capital: Decimal | None = None
    risk_percent: Decimal | None = None

    status: PaperTradeStatus = PaperTradeStatus.WAITING_FOR_ENTRY
    entry_timestamp: datetime | None = None
    actual_entry_price: Decimal | None = None
    exit_timestamp: datetime | None = None
    actual_exit_price: Decimal | None = None
    exit_reason: PaperExitReason | None = None
    realized_r: Decimal | None = None
    realized_pnl: Decimal | None = None

    label: str = ""
    metadata: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    sequence: int = 0

    @property
    def is_open(self) -> bool:
        return self.status is PaperTradeStatus.OPEN

    @property
    def is_terminal(self) -> bool:
        return self.status.is_terminal

    @property
    def has_geometry(self) -> bool:
        """Whether the entry/stop geometry is usable (positive risk)."""

        return (
            self.entry is not None
            and self.stop is not None
            and self.engine_risk_distance is not None
            and self.engine_risk_distance > 0
        )

    @property
    def has_target(self) -> bool:
        return self.target_1 is not None

    def __post_init__(self) -> None:
        """Validate internal consistency.

        The engine never produces inconsistent states; these checks guard
        against hand-construction bugs and enforce the no-fabrication
        invariants.
        """

        # Target 2 is never supported, regardless of status.
        if self.target_2 is not None:
            raise ValueError("target_2 must be None (unsupported).")
        if self.target_2_supported:
            raise ValueError("target_2_supported must be False.")

        # Direction must be a directional intent when the trade carries
        # usable geometry AND is not already INVALIDATED (an INVALIDATED
        # trade may carry partial geometry with a non-directional intent).
        if (
            self.status is not PaperTradeStatus.INVALIDATED
            and self.has_geometry
            and self.direction not in ("LONG", "SHORT")
        ):
            raise ValueError(
                "A paper trade with geometry requires a LONG/SHORT direction.",
            )

        # Entry state consistency.
        if self.status in (PaperTradeStatus.OPEN, PaperTradeStatus.CLOSED):
            if self.actual_entry_price is None or self.entry_timestamp is None:
                raise ValueError(
                    "An OPEN/CLOSED paper trade requires an entry price + timestamp.",
                )
        else:
            if self.actual_entry_price is not None or self.entry_timestamp is not None:
                raise ValueError(
                    "A non-OPEN/CLOSED paper trade must not carry an entry.",
                )

        # Exit state consistency.
        # CLOSED: full exit state (exit_reason + exit_timestamp + price/R/P&L
        # per determinate/ambiguous rules).
        # CANCELLED: terminal-before-entry; exit_reason=CANCELLED explains why;
        # no exit price / R / P&L fabricated (exit_timestamp optional = cancel time).
        # INVALIDATED: terminal; exit_reason=NO_GEOMETRY explains why; no exit
        # price / R / P&L / timestamp fabricated.
        # WAITING_FOR_ENTRY / OPEN: no exit state at all.
        if self.status is PaperTradeStatus.CLOSED:
            if self.exit_reason is None:
                raise ValueError("A CLOSED paper trade requires an exit_reason.")
            if self.exit_timestamp is None:
                raise ValueError("A CLOSED paper trade requires an exit_timestamp.")
            # BOTH_TOUCHED / NO_GEOMETRY carry no fabricated exit price / R / P&L.
            # TARGET_HIT / STOP_HIT / EXPIRED / MANUAL_CLOSE carry a determinate
            # exit price + realized R.
            ambiguous = self.exit_reason in (
                PaperExitReason.BOTH_TOUCHED,
                PaperExitReason.NO_GEOMETRY,
            )
            if ambiguous:
                if self.actual_exit_price is not None:
                    raise ValueError(
                        "An ambiguous/NO_GEOMETRY exit must not carry an exit price.",
                    )
                if self.realized_r is not None or self.realized_pnl is not None:
                    raise ValueError(
                        "An ambiguous/NO_GEOMETRY exit must not carry realized R/P&L.",
                    )
            else:
                if self.actual_exit_price is None:
                    raise ValueError(
                        "A determinate CLOSED exit requires an exit price.",
                    )
                if self.realized_r is None:
                    raise ValueError(
                        "A determinate CLOSED exit requires a realized_r.",
                    )
        elif self.status is PaperTradeStatus.CANCELLED:
            if self.exit_reason is not PaperExitReason.CANCELLED:
                raise ValueError(
                    "A CANCELLED paper trade must carry exit_reason CANCELLED.",
                )
            if self.actual_exit_price is not None or self.realized_r is not None:
                raise ValueError(
                    "A CANCELLED paper trade must not carry an exit price / R.",
                )
            if self.realized_pnl is not None:
                raise ValueError(
                    "A CANCELLED paper trade must not carry realized P&L.",
                )
        elif self.status is PaperTradeStatus.INVALIDATED:
            if self.exit_reason is not PaperExitReason.NO_GEOMETRY:
                raise ValueError(
                    "An INVALIDATED paper trade must carry exit_reason NO_GEOMETRY.",
                )
            if self.exit_timestamp is not None or self.actual_exit_price is not None:
                raise ValueError(
                    "An INVALIDATED paper trade must not carry exit state.",
                )
            if self.realized_r is not None or self.realized_pnl is not None:
                raise ValueError(
                    "An INVALIDATED paper trade must not carry realized R/P&L.",
                )
        else:
            # WAITING_FOR_ENTRY / OPEN: no exit state.
            if self.exit_reason is not None or self.exit_timestamp is not None:
                raise ValueError(
                    "A non-terminal paper trade must not carry exit state.",
                )
            if self.realized_r is not None or self.realized_pnl is not None:
                raise ValueError(
                    "A non-terminal paper trade must not carry realized R/P&L.",
                )

        # CANCELLED is terminal-before-entry: must not carry entry.
        if self.status is PaperTradeStatus.CANCELLED:
            if self.actual_entry_price is not None:
                raise ValueError("A CANCELLED paper trade must not carry an entry.")


__all__ = [
    "PaperExitReason",
    "PaperTrade",
    "PaperTradeStatus",
]
