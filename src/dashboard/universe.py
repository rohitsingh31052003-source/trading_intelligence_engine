"""
Monitored instrument universe (index constituents) — THIN backward-compat shim.

The single, maintainable definition of the monitored stock universe
lives in the ENGINE-level :mod:`engine.config.universe` (dependency
direction: dashboard -> engine; the engine never imports the dashboard).
This module re-exports the engine lists so existing dashboard imports
(``from dashboard.universe import ...``) keep working, and adds the
provider-local Yahoo symbol map that is dashboard-provider specific.

It contains NO trading, scoring, prediction, market-data or analysis
logic — it is configuration data only. The existing strategy / scanner /
decision / geometry / risk / paper-trading layers consume these names
unchanged.

Constituent sources (index composition as of the December 2025
reconstitutions):

* NIFTY 50 — 50 constituents (NSE trading symbols).
* SENSEX — 30 constituents. Every current SENSEX constituent is also a
  NIFTY 50 constituent, so the union is exactly the 50 NIFTY 50 stocks
  and the 30 SENSEX memberships are duplicates that are removed.

Symbol convention: the canonical instrument names used across the
dashboard are the plain NSE trading symbols (``RELIANCE``, ``TCS``,
``M&M``, ``BAJAJ-AUTO``, ``TMPV``, ...). The existing Yahoo data
provider expects ``<NSE symbol>.NS`` for NSE-listed stocks (indices use
the ``^`` prefix); :data:`UNIVERSE_YAHOO_SYMBOLS` provides exactly that
mapping for every constituent so provider-specific formatting stays
isolated inside the provider layer.

The universe is DESCRIPTIVE configuration only. It does NOT guarantee
future performance and does NOT constitute a trading recommendation.
"""

from __future__ import annotations

from engine.config.universe import (
    BENCHMARK_INDEX,
    COMBINED_UNIVERSE,
    MARKET_UNIVERSE,
    MARKET_UNIVERSE_TOP200,
    NIFTY200_CSV_SHA256,
    NIFTY200_ISINS,
    NIFTY200_MANIFEST_VERSION,
    NIFTY200_METADATA,
    NIFTY200_SOURCE_URL,
    NIFTY200_SYMBOLS,
    NIFTY50_CONSTITUENTS,
    SENSEX_CONSTITUENTS,
    combined_universe,
)

#: Yahoo Finance symbols for every universe constituent. NSE-listed
#: stocks use the ``<NSE symbol>.NS`` convention the existing Yahoo
#: provider expects (``RELIANCE`` -> ``RELIANCE.NS``, ``M&M`` ->
#: ``M&M.NS``, ``BAJAJ-AUTO`` -> ``BAJAJ-AUTO.NS``, ...).
UNIVERSE_YAHOO_SYMBOLS: dict[str, str] = {
    name: f"{name}.NS" for name in COMBINED_UNIVERSE
}

#: Yahoo Finance symbols for every NIFTY Top 200 constituent
#: (Checkpoint 19.1). All 200 constituents are NSE-listed equities, so
#: the same ``<NSE symbol>.NS`` convention resolves every one of them.
#: Derived from :data:`NIFTY200_SYMBOLS` — no second symbol universe.
TOP200_YAHOO_SYMBOLS: dict[str, str] = {
    name: f"{name}.NS" for name in NIFTY200_SYMBOLS
}


__all__ = [
    "BENCHMARK_INDEX",
    "COMBINED_UNIVERSE",
    "MARKET_UNIVERSE",
    "MARKET_UNIVERSE_TOP200",
    "NIFTY200_CSV_SHA256",
    "NIFTY200_ISINS",
    "NIFTY200_MANIFEST_VERSION",
    "NIFTY200_METADATA",
    "NIFTY200_SOURCE_URL",
    "NIFTY200_SYMBOLS",
    "NIFTY50_CONSTITUENTS",
    "SENSEX_CONSTITUENTS",
    "TOP200_YAHOO_SYMBOLS",
    "UNIVERSE_YAHOO_SYMBOLS",
    "combined_universe",
]
