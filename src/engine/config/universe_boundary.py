"""
Universe construction boundary (Checkpoint 19.1).

This module is the CLEAN BOUNDARY between universe CONSTRUCTION
(definition + validation) and universe CONSUMPTION (market scanning,
symbol resolution, dashboards). It owns:

* the canonical, deterministic construction of a validated
  :class:`UniverseDefinition` from the single manifest source
  (:data:`NIFTY200_SYMBOLS` in :mod:`engine.config.nifty200_manifest`),
* the preserved "vintage" universe (NIFTY 50 ∪ SENSEX, de-duplicated)
  that the EXISTING scanner / workstation / watchlist already consume,
* the explicit distinction between universe MEMBERSHIP and any notion of
  setup quality — membership is configuration data ONLY,
* strict validation: no duplicates, no empty/whitespace names, no
  invented symbols, no silently unsupported universe rows.

DESIGN RULES:

* The builder is STATELESS and PURE — it reads configuration tuples and
  returns immutable :class:`UniverseDefinition` objects. It never touches
  the network, never reads candles, never accesses providers.
* A NIFTY Top 200 definition is accepted ONLY when it matches the versioned
  manifest exactly (``NIFTY200_MANIFEST_VERSION`` / the official NSE file).
  An unrecognized extension is NOT silently allowed — a CUSTOM universe
  must be declared explicitly with its own label.
* Universe membership is NEVER mixed with setup quality: this module
  contains zero trading, scoring, prediction, decision or ranking logic.
* No look-ahead: construction does not ingest market data and carries no
  evaluation-time concept.

The boundary intentionally does NOT create a new orchestration package:
it is a configuration/validation helper consumed by the existing scanner
and (in Checkpoint 19.3) by the future continuous scanning loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from engine.config.nifty200_manifest import (
    NIFTY200_CSV_SHA256,
    NIFTY200_MANIFEST_VERSION,
    NIFTY200_METADATA,
    NIFTY200_SOURCE_URL,
    NIFTY200_SYMBOLS,
)
from engine.config.universe import (
    BENCHMARK_INDEX,
    COMBINED_UNIVERSE,
    MARKET_UNIVERSE_TOP200,
)

#: The canonical, documented meaning of a universe KIND. The distinction
#: is informational (provenance + validation contract), NOT a ranking or
#: a quality signal.
class UniverseKind(Enum):
    #: Legacy NIFTY 50 ∪ SENSEX universe (de-duplicated) + the NIFTY
    #: benchmark index instrument (the historical default watchlist).
    VINTAGE = "vintage"
    #: The NIFTY Top 200 index constituents (official NSE snapshot,
    #: versioned) + the SEPARATE NIFTY benchmark index instrument.
    NIFTY200 = "nifty200"
    #: An explicit caller-supplied instrument list (validated for
    #: duplicates / empty names; membership is configuration only).
    CUSTOM = "custom"


def _canonical_name(name: str) -> str:
    """Canonicalize + validate a single instrument name.

    Strips surrounding whitespace, upper-cases, and rejects empty names.
    The ONLY transformation applied to non-manifest symbols; manifest
    symbols are already canonical.
    """

    if not isinstance(name, str):
        raise TypeError(f"instrument name must be a str, got {type(name).__name__}")
    canonical = name.strip().upper()
    if not canonical:
        raise ValueError("instrument name cannot be empty")
    return canonical


def _validate_unique_symbols(names: Sequence[str]) -> tuple[str, ...]:
    """Canonicalize a sequence and guarantee uniqueness (sorted result)."""

    seen: set[str] = set()
    cleaned: list[str] = []
    for name in names:
        canon = _canonical_name(name)
        if canon in seen:
            continue
        seen.add(canon)
        cleaned.append(canon)
    return tuple(sorted(cleaned))


@dataclass(frozen=True, slots=True)
class UniverseDefinition:
    """
    An immutable, validated universe definition.

    Attributes:

    kind
        The :class:`UniverseKind` that established the provenance
        contract for this definition.

    symbols
        Canonical instrument names, deterministically sorted and
        de-duplicated. For ``NIFTY200`` this is EXACTLY the 200
        constituents of the versioned manifest (the NIFTY benchmark
        index instrument is carried separately in ``benchmark_index``).

    benchmark_index
        The index instrument(s) carried alongside the stock universe
        (a benchmark is NOT a "constituent" of the top-200 universe).
        For ``CUSTOM`` this may be empty.

    label
        Human-readable label (presentation/provenance only).

    manifest_version / source_url / csv_sha256
        Embedded provenance of the constituent snapshot (populated for
        ``NIFTY200``; ``""`` otherwise).
    """

    kind: UniverseKind
    symbols: tuple[str, ...] = field(default_factory=tuple)
    benchmark_index: tuple[str, ...] = field(default_factory=tuple)
    label: str = ""
    manifest_version: str = ""
    source_url: str = ""
    csv_sha256: str = ""

    def __post_init__(self) -> None:
        # Guarantee internal invariants even for hand-constructed values.
        cleaned = _validate_unique_symbols(self.symbols)
        object.__setattr__(self, "symbols", cleaned)
        bench = _validate_unique_symbols(self.benchmark_index)
        object.__setattr__(self, "benchmark_index", bench)
        if not isinstance(self.label, str):
            raise TypeError("label must be a str")
        if self.kind is UniverseKind.NIFTY200:
            if len(cleaned) != len(NIFTY200_SYMBOLS):
                raise ValueError(
                    "NIFTY200 universe must contain exactly "
                    f"{len(NIFTY200_SYMBOLS)} constituents, got {len(cleaned)}",
                )
            missing = set(NIFTY200_SYMBOLS) - set(cleaned)
            if missing:
                raise ValueError(
                    "NIFTY200 universe missing manifest constituents: "
                    f"{sorted(missing)!r}",
                )
            # A strict NIFTY200 definition must never carry invented
            # extra symbols.
            extras = set(cleaned) - set(NIFTY200_SYMBOLS)
            if extras:
                raise ValueError(
                    "NIFTY200 universe contains non-manifest symbols: "
                    f"{sorted(extras)!r}",
                )
            if not self.manifest_version:
                object.__setattr__(
                    self, "manifest_version", NIFTY200_MANIFEST_VERSION,
                )
            if not self.source_url:
                object.__setattr__(self, "source_url", NIFTY200_SOURCE_URL)
            if not self.csv_sha256:
                object.__setattr__(self, "csv_sha256", NIFTY200_CSV_SHA256)

    @property
    def instrument_count(self) -> int:
        """Total instruments = stock universe + benchmark index."""

        return len(self.symbols) + len(self.benchmark_index)

    def contains(self, instrument: str) -> bool:
        """Case-insensitive membership test (configuration data only)."""

        try:
            canon = _canonical_name(instrument)
        except (ValueError, TypeError):
            return False
        return canon in self.symbols


class UniverseBuilder:
    """
    Stateless, deterministic universe construction boundary.

    ``vintage()`` preserves the pre-existing default watchlist universe;
    ``nifty200()`` builds the versioned NIFTY Top 200 universe from the
    single manifest source; ``custom()`` validates an explicit caller
    list. All methods return immutable :class:`UniverseDefinition`
    objects; all throw ``ValueError``/``TypeError`` on invalid input
    (never silently repair a wrong universe).
    """

    @staticmethod
    def vintage(label: str = "vintage nifty50+sensex") -> UniverseDefinition:
        """The preserved NIFTY 50 ∪ SENSEX universe (+ NIFTY benchmark)."""

        return UniverseDefinition(
            kind=UniverseKind.VINTAGE,
            symbols=COMBINED_UNIVERSE,
            benchmark_index=BENCHMARK_INDEX,
            label=label,
        )

    @staticmethod
    def nifty200(label: str = "nifty top 200") -> UniverseDefinition:
        """The versioned NIFTY Top 200 universe from the official manifest.

        The NIFTY benchmark index instrument remains SEPARATE
        (:data:`engine.config.universe.MARKET_UNIVERSE_TOP200` carries
        benchmark + 200). Construction strictly validates the manifest
        membership (exactly 200 unique symbols, all metadata present).
        """

        missing_meta = [
            s for s in NIFTY200_SYMBOLS if s not in NIFTY200_METADATA
        ]
        if missing_meta:
            raise ValueError(
                "manifest is inconsistent: missing metadata for "
                f"{sorted(missing_meta)!r}",
            )
        return UniverseDefinition(
            kind=UniverseKind.NIFTY200,
            symbols=NIFTY200_SYMBOLS,
            benchmark_index=BENCHMARK_INDEX,
            label=label,
            manifest_version=NIFTY200_MANIFEST_VERSION,
            source_url=NIFTY200_SOURCE_URL,
            csv_sha256=NIFTY200_CSV_SHA256,
        )

    @staticmethod
    def custom(
        symbols: Sequence[str],
        label: str = "custom universe",
        benchmark_index: Sequence[str] = (),
    ) -> UniverseDefinition:
        """A validated caller-supplied universe (membership config only).

        Raises ``TypeError`` for non-string entries and ``ValueError``
        for an empty/whitespace-only name. Duplicates are removed and the
        result sorted so the definition is deterministic.
        """

        cleaned = _validate_unique_symbols(symbols)
        if not cleaned:
            raise ValueError("custom universe must contain at least one symbol")
        return UniverseDefinition(
            kind=UniverseKind.CUSTOM,
            symbols=cleaned,
            benchmark_index=_validate_unique_symbols(benchmark_index),
            label=label,
        )


#: Default (pre-built) NIFTY Top 200 definition incl. benchmark index.
DEFAULT_NIFTY200_UNIVERSE: UniverseDefinition = UniverseBuilder.nifty200()

#: Combined market tuple re-export (benchmark + Top 200), single source.
TOP200_MARKET_UNIVERSE: tuple[str, ...] = MARKET_UNIVERSE_TOP200


__all__ = [
    "DEFAULT_NIFTY200_UNIVERSE",
    "TOP200_MARKET_UNIVERSE",
    "UniverseBuilder",
    "UniverseDefinition",
    "UniverseKind",
]