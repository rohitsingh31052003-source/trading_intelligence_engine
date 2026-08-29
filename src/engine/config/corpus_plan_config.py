"""
Configuration for the corpus-preparation plan (Checkpoint 3B).

Frozen + slots, validated at construction, MINIMAL — NO strategy /
optimization / scoring parameters. The planner configuration carries
ONLY research-infrastructure settings: the planned data window, the
timeframes to cover, the provider whose capability gates the request
matrix, and descriptive identity. The existing decision / actionability
/ trade-geometry / evidence semantics are intentionally NOT part of
this configuration.

The instrument universe is NOT configured here — the planner consumes
the EXISTING configurable :class:`ResearchUniverse` (or an explicit
instrument sequence) and NEVER hard-codes the universe.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from engine.data.historical_times import (
    canonical_timeframe,
    timeframe_seconds,
)


@dataclass(frozen=True, slots=True)
class CorpusPlanConfig:
    """
    Corpus-preparation plan configuration.

    Attributes:

    timeframes
        The timeframe labels to cover (canonicalized via the reused
        Phase 6B :func:`canonical_timeframe`; unknown labels are
        rejected). Every planned dataset is one (instrument, timeframe)
        pair. Default ``("15m", "1D")`` mirroring the Phase 6C setup /
        context pair.

    start / end
        The planned data window bounds (timezone-aware, ``start < end``).
        REQUIRED — the plan always has an explicit window; there is no
        unbounded planning mode.

    provider
        The provider name whose capability gates the request matrix.
        Default ``"upstox-historical"`` — the research-universe
        historical provider introduced in Checkpoint 3A. Planning is
        pure (no fetch); a row is ``provider_supported`` only when the
        selected provider's ``supports()`` says so.

    label / metadata
        Descriptive run identity carried onto the plan.
    """

    timeframes: tuple[str, ...] = ("15m", "1D")
    start: datetime | None = None
    end: datetime | None = None
    provider: str = "upstox-historical"
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.timeframes:
            raise ValueError("timeframes must not be empty.")
        normalized: list[str] = []
        for frame in self.timeframes:
            canonical = canonical_timeframe(frame)
            if canonical is None:
                raise ValueError(f"timeframe {frame!r} is not supported.")
            if canonical in normalized:
                raise ValueError(f"duplicate timeframe {canonical!r}.")
            normalized.append(canonical)
        object.__setattr__(self, "timeframes", tuple(normalized))
        for name in ("start", "end"):
            value = getattr(self, name)
            if value is not None and value.tzinfo is None:
                raise ValueError(f"{name} must be timezone-aware.")
        if (
            self.start is not None
            and self.end is not None
            and not self.start < self.end
        ):
            raise ValueError("start must be strictly before end.")
        if not isinstance(self.provider, str) or not self.provider.strip():
            raise ValueError("provider must be a non-empty string.")
        if not isinstance(self.label, str):
            raise ValueError("label must be a string.")
        object.__setattr__(
            self,
            "metadata",
            tuple(sorted((str(k), str(v)) for k, v in self.metadata)),
        )

    def snapshot(self) -> tuple[tuple[str, str], ...]:
        """Sorted, auditable configuration snapshot for reporting."""

        return tuple(
            sorted(
                (
                    ("timeframes", ",".join(self.timeframes)),
                    ("start", self.start.isoformat() if self.start else ""),
                    ("end", self.end.isoformat() if self.end else ""),
                    ("provider", self.provider),
                    ("label", self.label),
                ),
            ),
        )


def validate_plan_window(
    start: datetime,
    end: datetime,
) -> tuple[datetime, datetime]:
    """Validate an explicit plan window; returns ``(start, end)``.

    Raised ``ValueError`` for naive timestamps, non-datetime types and
    reversed/repeated bounds — the corpus preparation layer never
    silently invents a window.
    """

    for name, value in (("start", start), ("end", end)):
        if not isinstance(value, datetime):
            raise ValueError(f"{name} must be a datetime.")
        if value.tzinfo is None:
            raise ValueError(
                f"{name} must be timezone-aware (naive timestamps are "
                "never silently accepted).",
            )
    if not start < end:
        raise ValueError("start must be strictly before end.")
    return start, end


__all__ = [
    "CorpusPlanConfig",
    "validate_plan_window",
]