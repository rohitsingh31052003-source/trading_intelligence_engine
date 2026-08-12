"""
Market context report formatter (Sprint 11P).

``MarketContextFormatter`` renders a descriptive ``MarketContext`` (or
a sequence of them) as a plain-text report. It is stateless and
deterministic: identical inputs always produce identical text.

The report is DESCRIPTIVE. It never claims directional prediction or
profitability. Wording uses "descriptive market context" and never
"predicted direction" or "profitable setup".
"""

from __future__ import annotations

from typing import Iterable

from engine.models.market_context import MarketContext


class MarketContextFormatter:
    """
    Render descriptive market context as plain text.
    """

    def format(self, context: MarketContext) -> str:
        """Render a single ``MarketContext``."""

        return self._render_point(context)

    def format_sequence(
        self,
        contexts: Iterable[MarketContext],
    ) -> str:
        """Render a sequence of ``MarketContext`` snapshots."""

        lines: list[str] = []
        for ctx in contexts:
            lines.append(self._render_point(ctx))
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    # ------------------------------------------------------------
    # INTERNALS
    # ------------------------------------------------------------

    def _render_point(self, ctx: MarketContext) -> str:
        sr = ctx.support_resistance
        rng = ctx.range
        tr = ctx.trend
        recent = " -> ".join(
            s.structure.name for s in ctx.recent_structure
        ) or "none"

        lines = [
            f"index={ctx.index}",
            f"  trend         = {tr.state.name} (bias {tr.bias.name})",
            f"  range         = {rng.state.name}",
        ]

        if rng.state.name == "IN_RANGE":
            lines.append(
                f"  range bounds  = low {rng.low} / high {rng.high} "
                f"(width {rng.width}, position {rng.position:.2f})",
            )

        lines.append(f"  location      = {sr.location.name}")
        lines.append(f"  support       = {sr.support}")
        lines.append(f"  resistance    = {sr.resistance}")
        lines.append(f"  recent struct = {recent}")
        lines.append(f"  confirmed swings = {ctx.confirmed_swings}")
        lines.append(
            "  note          = descriptive market context; not a "
            "trade signal or directional prediction"
        )
        return "\n".join(lines)


__all__ = ["MarketContextFormatter"]
