"""
Watchlist abstraction for the multi-instrument scanner (Product Phase 2).

A :class:`Watchlist` is a small, deterministic, validated collection of
instrument names. It is the input to the multi-instrument scanner and
implements NO trading, scoring, prediction or market-data logic: it only
holds a sorted, de-duplicated, validated set of canonical instrument names
and exposes a tiny CRUD surface (add / remove / contains) plus
deterministic serialization.

Design choices (all required by Product Phase 2):

* **Deterministic ordering**: instruments are stored in a stable
  lexicographic order. The order in which a caller adds instruments NEVER
  affects the stored order, so two watchlists built from the same set of
  instruments (in any order) are equal. This is what makes the scanner's
  output ordering a function of the *analysis* (existing decision /
  actionability / evidence / freshness), never of accidental input order.
* **Duplicate prevention**: adding an instrument already present is a
  no-op (idempotent), never raises.
* **Validation**: instrument names are stripped of surrounding whitespace
  and must be non-empty; an empty / whitespace-only name raises
  ``ValueError``. Names are stored upper-cased and canonicalized so
  ``nifty`` and ``NIFTY`` are the same instrument.
* **No persistence**: this is a local research / trading workstation. No
  user accounts, no database, no filesystem. Serialization to a plain
  tuple / JSON-able form is provided for the API and tests only.

The watchlist is DESCRIPTIVE infrastructure only. It does NOT guarantee
future performance and does NOT constitute a trading recommendation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Iterator

#: Default watchlist matching the local deterministic fixture set
#: (Sprint 11V). Chosen so the scanner has something useful to scan out
#: of the box on the fixture provider, without fabricating instruments.
DEFAULT_WATCHLIST: tuple[str, ...] = (
    "NIFTY", "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK",
)


def _validate_name(name: str) -> str:
    """Canonicalize + validate a single instrument name.

    Strips surrounding whitespace, upper-cases, and rejects empty names.
    Returns the canonical name. Never invents or alters a name beyond
    this normalization.
    """

    if not isinstance(name, str):
        raise TypeError(
            f"instrument name must be a str, got {type(name).__name__}",
        )
    canonical = name.strip().upper()
    if not canonical:
        raise ValueError("instrument name must be a non-empty string")
    return canonical


@dataclass(frozen=True)
class WatchlistSpec:
    """
    Immutable, serializable snapshot of a watchlist.

    Attributes:

    instruments
        Tuple of canonical instrument names, sorted lexicographically,
        de-duplicated. Never contains empty strings.

    label
        Optional human-readable label (presentation only).
    """

    instruments: tuple[str, ...] = field(default_factory=tuple)
    label: str = ""

    def __post_init__(self) -> None:
        # Defensive: guarantee the invariants even when constructed by
        # hand / deserialized. Reject empty entries and duplicates; the
        # canonical order is lexicographic.
        seen: set[str] = set()
        cleaned: list[str] = []
        for name in self.instruments:
            canon = _validate_name(name)
            if canon in seen:
                continue
            seen.add(canon)
            cleaned.append(canon)
        object.__setattr__(self, "instruments", tuple(sorted(cleaned)))
        if not isinstance(self.label, str):
            raise TypeError("label must be a str")

    def to_jsonable(self) -> dict[str, object]:
        """Return a JSON-serializable dict (deterministic, sorted keys)."""

        return {
            "instruments": list(self.instruments),
            "label": self.label,
        }


class Watchlist:
    """
    A mutable, deterministic, validated watchlist of instrument names.

    Instruments are kept canonical (stripped, upper-cased), de-duplicated
    and sorted lexicographically. ``add`` / ``remove`` mutate the list in
    place and are idempotent. Equality and ordering depend ONLY on the set
    of instruments, never on the insertion order, so the scanner's
    deterministic output is independent of how the watchlist was built.

    This holds NO market data and NO analysis results. It is the input to
    the scanner; the scanner reuses the existing intelligence pipeline to
    produce per-instrument views.
    """

    __slots__ = ("_instruments", "label")

    def __init__(
        self,
        instruments: Iterable[str] | None = None,
        *,
        label: str = "",
    ) -> None:
        self._instruments: set[str] = set()
        self.label = label
        if instruments is not None:
            for name in instruments:
                self.add(name)

    # -- construction helpers -----------------------------------

    @classmethod
    def default(cls, *, label: str = "default") -> "Watchlist":
        """Build the default watchlist (the local fixture instruments)."""

        return cls(DEFAULT_WATCHLIST, label=label)

    @classmethod
    def from_spec(cls, spec: WatchlistSpec) -> "Watchlist":
        """Build a :class:`Watchlist` from a :class:`WatchlistSpec`."""

        return cls(spec.instruments, label=spec.label)

    # -- CRUD ---------------------------------------------------

    def add(self, instrument: str) -> bool:
        """
        Add an instrument. Idempotent: returns ``True`` if the
        instrument was newly added, ``False`` if it was already present.
        Raises ``ValueError`` for an empty / whitespace-only name.
        """

        canonical = _validate_name(instrument)
        if canonical in self._instruments:
            return False
        self._instruments.add(canonical)
        return True

    def remove(self, instrument: str) -> bool:
        """
        Remove an instrument. Idempotent: returns ``True`` if the
        instrument was present and removed, ``False`` if it was absent.
        Names are canonicalized before lookup, so ``"nifty"`` removes
        ``"NIFTY"``.
        """

        canonical = _validate_name(instrument)
        if canonical not in self._instruments:
            return False
        self._instruments.discard(canonical)
        return True

    def __contains__(self, instrument: object) -> bool:
        if not isinstance(instrument, str):
            return False
        try:
            canonical = _validate_name(instrument)
        except (ValueError, TypeError):
            return False
        return canonical in self._instruments

    def __iter__(self) -> Iterator[str]:
        return iter(self.instruments)

    def __len__(self) -> int:
        return len(self._instruments)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Watchlist):
            return self._instruments == other._instruments
        return NotImplemented

    def __hash__(self) -> int:  # pragma: no cover - sets are unhashable
        # A mutable container is not meaningfully hashable; expose the
        # error rather than silently hashing a stale snapshot.
        raise TypeError("Watchlist is mutable and not hashable")

    def __repr__(self) -> str:
        return f"Watchlist({list(self.instruments)!r}, label={self.label!r})"

    # -- deterministic, sorted view ------------------------------

    @property
    def instruments(self) -> tuple[str, ...]:
        """Instruments as a deterministically sorted tuple."""

        return tuple(sorted(self._instruments))

    def is_empty(self) -> bool:
        return len(self._instruments) == 0

    def to_spec(self) -> WatchlistSpec:
        """Return an immutable :class:`WatchlistSpec` snapshot."""

        return WatchlistSpec(instruments=self.instruments, label=self.label)

    def to_jsonable(self) -> dict[str, object]:
        """Return a JSON-serializable dict (delegates to the spec)."""

        return self.to_spec().to_jsonable()


__all__ = [
    "DEFAULT_WATCHLIST",
    "Watchlist",
    "WatchlistSpec",
]
