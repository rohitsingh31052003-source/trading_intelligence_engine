"""
Configuration for the historical research corpus (Product Phase 6C).

Frozen + slots, validated at construction, MINIMAL — NO strategy /
optimization / scoring parameters. The corpus configuration carries ONLY
research-infrastructure settings: which timeframes to reconstruct, the
minimum-history requirements, the sampling cadence, the optional data
window, and whether gapped evaluation windows are skipped. The existing
decision / actionability / trade-geometry / evidence semantics are
intentionally NOT part of this configuration.

The 51-instrument universe is NOT configured here — the corpus consumes
the EXISTING configurable :class:`ResearchUniverse` from the Phase 6B
foundation; instruments are never hard-coded into the research layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from engine.data.historical_times import canonical_timeframe


@dataclass(frozen=True, slots=True)
class ResearchCorpusConfig:
    """
    Research corpus configuration.

    Attributes:

    setup_timeframe
        The lower / setup timeframe label (canonicalized via the reused
        Phase 6B :func:`canonical_timeframe`). Default ``"15m"``.

    context_timeframe
        The higher / context timeframe label, or ``""`` to disable the
        context timeframe entirely. Must DIFFER from the setup
        timeframe when set. Default ``"1D"``.

    min_setup_history
        Minimum number of usable setup-timeframe candles required at an
        evaluation boundary for the point to be VALID. Default 10.

    min_context_history
        Minimum number of completed context-timeframe candles required
        at an evaluation boundary for the point to be VALID when a
        context timeframe is configured. Default 1.

    sample_every
        Evaluation cadence: one evaluation point is generated every
        ``sample_every`` setup-timeframe candles along the canonical
        candle grid. Default 1 (every completed setup candle). The
        corpus never scans arbitrary wall-clock timestamps — the
        existing candle boundaries ARE the canonical evaluation grid.

    start / end
        Optional data window bounds (timezone-aware, ``start < end``).
        ``None`` = unbounded on that side.

    skip_gapped_points
        When ``True`` (default), an evaluation window containing an
        UNEXPECTED data gap is classified ``DATA_GAP`` and skipped
        honestly. When ``False`` the point is evaluated and the gap is
        reported via the data-quality summary only.

    label / metadata
        Descriptive run identity carried onto the corpus.
    """

    setup_timeframe: str = "15m"
    context_timeframe: str = "1D"
    min_setup_history: int = 10
    min_context_history: int = 1
    sample_every: int = 1
    start: datetime | None = None
    end: datetime | None = None
    skip_gapped_points: bool = True
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        setup = canonical_timeframe(self.setup_timeframe)
        if setup is None:
            raise ValueError(
                f"setup_timeframe {self.setup_timeframe!r} is not supported.",
            )
        object.__setattr__(self, "setup_timeframe", setup)

        if self.context_timeframe:
            context = canonical_timeframe(self.context_timeframe)
            if context is None:
                raise ValueError(
                    f"context_timeframe {self.context_timeframe!r} is not "
                    "supported.",
                )
            if context == setup:
                raise ValueError(
                    "context_timeframe must differ from setup_timeframe.",
                )
            object.__setattr__(self, "context_timeframe", context)
        else:
            object.__setattr__(self, "context_timeframe", "")

        if self.min_setup_history < 1:
            raise ValueError("min_setup_history must be >= 1.")
        if self.min_context_history < 0:
            raise ValueError("min_context_history must be non-negative.")
        if self.sample_every < 1:
            raise ValueError("sample_every must be >= 1.")

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

        if not isinstance(self.label, str):
            raise ValueError("label must be a string.")
        object.__setattr__(
            self,
            "metadata",
            tuple(sorted((str(k), str(v)) for k, v in self.metadata)),
        )

    @property
    def has_context_timeframe(self) -> bool:
        return bool(self.context_timeframe)

    def snapshot(self) -> tuple[tuple[str, str], ...]:
        """Sorted, auditable configuration snapshot for reporting."""

        return tuple(
            sorted(
                (
                    ("setup_timeframe", self.setup_timeframe),
                    ("context_timeframe", self.context_timeframe or "(disabled)"),
                    ("min_setup_history", str(self.min_setup_history)),
                    ("min_context_history", str(self.min_context_history)),
                    ("sample_every", str(self.sample_every)),
                    ("start", self.start.isoformat() if self.start else ""),
                    ("end", self.end.isoformat() if self.end else ""),
                    ("skip_gapped_points", str(self.skip_gapped_points)),
                    ("label", self.label),
                ),
            ),
        )


__all__ = ["ResearchCorpusConfig"]
