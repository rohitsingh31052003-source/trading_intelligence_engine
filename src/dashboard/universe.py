"""
Monitored instrument universe (index constituents).

This module is the single, maintainable definition of the universe of
stocks the EXISTING scanner / workstation / paper-trading operations
monitor. It expands the monitored STOCK universe from the original
5-instrument default to the union of the current NIFTY 50 and SENSEX
constituents, with duplicate companies removed (a company that appears
in both indices is monitored exactly once).

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

#: Current NIFTY 50 constituents (NSE trading symbols), alphabetically.
NIFTY50_CONSTITUENTS: tuple[str, ...] = (
    "ADANIENT",
    "ADANIPORTS",
    "APOLLOHOSP",
    "ASIANPAINT",
    "AXISBANK",
    "BAJAJ-AUTO",
    "BAJAJFINSV",
    "BAJFINANCE",
    "BEL",
    "BHARTIARTL",
    "CIPLA",
    "COALINDIA",
    "DRREDDY",
    "EICHERMOT",
    "ETERNAL",
    "GRASIM",
    "HCLTECH",
    "HDFCBANK",
    "HDFCLIFE",
    "HINDALCO",
    "HINDUNILVR",
    "ICICIBANK",
    "INDIGO",
    "INFY",
    "ITC",
    "JIOFIN",
    "JSWSTEEL",
    "KOTAKBANK",
    "LT",
    "M&M",
    "MARUTI",
    "MAXHEALTH",
    "NESTLEIND",
    "NTPC",
    "ONGC",
    "POWERGRID",
    "RELIANCE",
    "SBILIFE",
    "SBIN",
    "SHRIRAMFIN",
    "SUNPHARMA",
    "TATACONSUM",
    "TATASTEEL",
    "TCS",
    "TECHM",
    "TITAN",
    "TMPV",
    "TRENT",
    "ULTRACEMCO",
    "WIPRO",
)

#: Current SENSEX constituents (NSE trading symbols), alphabetically.
#: All of them are also NIFTY 50 constituents, so they are removed as
#: duplicates when the two index universes are combined.
SENSEX_CONSTITUENTS: tuple[str, ...] = (
    "ADANIPORTS",
    "ASIANPAINT",
    "AXISBANK",
    "BAJAJFINSV",
    "BAJFINANCE",
    "BEL",
    "BHARTIARTL",
    "ETERNAL",
    "HCLTECH",
    "HDFCBANK",
    "HINDUNILVR",
    "ICICIBANK",
    "INDIGO",
    "INFY",
    "ITC",
    "KOTAKBANK",
    "LT",
    "M&M",
    "MARUTI",
    "NTPC",
    "POWERGRID",
    "RELIANCE",
    "SBIN",
    "SUNPHARMA",
    "TATASTEEL",
    "TCS",
    "TECHM",
    "TITAN",
    "TRENT",
    "ULTRACEMCO",
)


def combined_universe() -> tuple[str, ...]:
    """NIFTY 50 ∪ SENSEX constituents, de-duplicated and sorted.

    A company listed in both indices appears exactly once (the scanner
    never scans the same company twice because of dual index
    membership). The result is deterministically ordered so downstream
    behaviour never depends on source-list ordering.
    """

    return tuple(sorted(set(NIFTY50_CONSTITUENTS) | set(SENSEX_CONSTITUENTS)))


#: The combined, de-duplicated stock universe (NIFTY 50 ∪ SENSEX).
COMBINED_UNIVERSE: tuple[str, ...] = combined_universe()

#: Yahoo Finance symbols for every universe constituent. NSE-listed
#: stocks use the ``<NSE symbol>.NS`` convention the existing Yahoo
#: provider expects (``RELIANCE`` -> ``RELIANCE.NS``, ``M&M`` ->
#: ``M&M.NS``, ``BAJAJ-AUTO`` -> ``BAJAJ-AUTO.NS``, ...).
UNIVERSE_YAHOO_SYMBOLS: dict[str, str] = {
    name: f"{name}.NS" for name in COMBINED_UNIVERSE
}


__all__ = [
    "COMBINED_UNIVERSE",
    "NIFTY50_CONSTITUENTS",
    "SENSEX_CONSTITUENTS",
    "UNIVERSE_YAHOO_SYMBOLS",
    "combined_universe",
]
