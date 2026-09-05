"""
Checkpoint 19.1 — NIFTY Top 200 universe correctness tests.

These tests prove the NIFTY Top 200 universe foundation is:

* COMPLETE — exactly the 200 official NSE constituents (no missing
  members, no invented members, no duplicates);
* DETERMINISTIC — sorted, de-duplicated, stable across imports;
* AUDITABLE — the embedded manifest carries the official NSE source URL,
  the NSE "Updated on" version label, and the SHA-256 of the raw CSV;
* RESOLVABLE — every constituent maps to a Yahoo symbol (``<NSE>.NS``)
  through the existing provider abstraction, and the engine historical
  provider's default Yahoo map covers all 200;
* BOUNDED — universe construction (UniverseBuilder) is a clean,
  validated boundary separate from market scanning; it never mixes
  membership with setup quality and never introduces look-ahead or
  broker/execution semantics;
* NON-REGRESSIVE — the existing NIFTY 50 ∪ SENSEX universe and the
  existing provider maps remain intact.

No network access is used by these tests: the manifest is embedded, and
provider symbol resolution is exercised through the deterministic
provider constructors / default maps.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from engine.config.nifty200_manifest import (
    NIFTY200_CONSTITUENTS,
    NIFTY200_CSV_SHA256,
    NIFTY200_ISINS,
    NIFTY200_MANIFEST_VERSION,
    NIFTY200_METADATA,
    NIFTY200_SOURCE_URL,
    NIFTY200_SYMBOLS,
)
from engine.config.universe import (
    COMBINED_UNIVERSE,
    MARKET_UNIVERSE,
    MARKET_UNIVERSE_TOP200,
    NIFTY50_CONSTITUENTS,
    SENSEX_CONSTITUENTS,
)
from engine.config.universe_boundary import (
    DEFAULT_NIFTY200_UNIVERSE,
    TOP200_MARKET_UNIVERSE,
    UniverseBuilder,
    UniverseDefinition,
    UniverseKind,
)

#: Path to the archived official NSE CSV (provenance cross-check).
_ARCHIVED_CSV = Path(__file__).resolve().parent.parent / "scripts" / "checkpoint19_data" / "ind_nifty200list.csv"


# ============================================================
# A. MANIFEST COMPLETENESS
# ============================================================


class TestManifestCompleteness:
    def test_exactly_200_constituents(self):
        assert len(NIFTY200_SYMBOLS) == 200
        assert len(NIFTY200_CONSTITUENTS) == 200

    def test_no_duplicate_symbols(self):
        assert len(set(NIFTY200_SYMBOLS)) == 200

    def test_no_duplicate_isins(self):
        isins = [row[2] for row in NIFTY200_CONSTITUENTS]
        assert len(set(isins)) == 200

    def test_metadata_and_isin_maps_cover_all(self):
        assert set(NIFTY200_METADATA) == set(NIFTY200_SYMBOLS)
        assert set(NIFTY200_ISINS) == set(NIFTY200_SYMBOLS)
        assert len(NIFTY200_METADATA) == 200
        assert len(NIFTY200_ISINS) == 200

    def test_symbols_sorted(self):
        assert NIFTY200_SYMBOLS == tuple(sorted(NIFTY200_SYMBOLS))

    def test_known_members_present(self):
        # Spot-check a spread of well-known constituents.
        for sym in (
            "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "SBIN",
            "LT", "M&M", "BAJAJ-AUTO", "TITAN", "SUNPHARMA", "WIPRO",
            "TRENT", "NESTLEIND", "MARUTI", "TATASTEEL", "360ONE",
            "ZYDUSLIFE", "YESBANK", "IDEA",
        ):
            assert sym in NIFTY200_SYMBOLS

    def test_symbol_charset(self):
        # NSE trading-symbol charset (uppercase alnum + & . -).
        pattern = re.compile(r"^[A-Z0-9&.\-]+$")
        for sym in NIFTY200_SYMBOLS:
            assert pattern.match(sym), sym

    def test_isin_format(self):
        # ISIN: 2-letter country code + 9 alphanumeric + 1 check digit
        # (12 chars). Indian ISINs use the "IN" country code and the
        # "INE" prefix for equity.
        pattern = re.compile(r"^IN[A-Z0-9]{9}[0-9]$")
        for sym in NIFTY200_SYMBOLS:
            isin = NIFTY200_ISINS[sym]
            assert pattern.match(isin), (sym, isin)
            assert isin.startswith("INE"), (sym, isin)

    def test_every_row_has_company_and_series(self):
        # The manifest stores (symbol, company, isin); the archived NSE
        # file additionally carries Industry/Series(EQ). We assert the
        # company names are non-empty for every row.
        for row in NIFTY200_CONSTITUENTS:
            assert row[0] and row[1] and row[2]


# ============================================================
# B. MANIFEST PROVENANCE / AUDITABILITY
# ============================================================


class TestManifestProvenance:
    def test_source_url_is_official_nse(self):
        assert NIFTY200_SOURCE_URL == (
            "https://nsearchives.nseindia.com/content/indices/ind_nifty200list.csv"
        )

    def test_manifest_version_is_versioned(self):
        assert NIFTY200_MANIFEST_VERSION
        assert NIFTY200_MANIFEST_VERSION == "2026-04-22-nse"

    def test_sha256_is_64_hex(self):
        assert re.fullmatch(r"[0-9a-f]{64}", NIFTY200_CSV_SHA256)

    def test_archived_csv_sha_matches_manifest(self):
        # The archived raw CSV must hash to the embedded SHA-256.
        if not _ARCHIVED_CSV.exists():
            pytest.skip("archived NSE CSV not present in this checkout")
        raw = _ARCHIVED_CSV.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == NIFTY200_CSV_SHA256

    def test_archived_csv_has_200_rows(self):
        if not _ARCHIVED_CSV.exists():
            pytest.skip("archived NSE CSV not present in this checkout")
        import csv

        with _ARCHIVED_CSV.open(encoding="utf-8-sig") as f:
            rows = [r for r in csv.reader(f) if r and r[0].strip()]
        # header + 200 data rows
        assert len(rows) == 201


# ============================================================
# C. VINTAGE UNIVERSE PRESERVED (non-regression)
# ============================================================


class TestVintageUniversePreserved:
    def test_nifty50_count(self):
        assert len(NIFTY50_CONSTITUENTS) == 50

    def test_sensex_count(self):
        assert len(SENSEX_CONSTITUENTS) == 30

    def test_combined_universe_50(self):
        # Every SENSEX member is also NIFTY 50, so the union is 50.
        assert len(COMBINED_UNIVERSE) == 50

    def test_market_universe_51(self):
        assert len(MARKET_UNIVERSE) == 51
        assert MARKET_UNIVERSE[0] == "NIFTY"

    def test_vintage_fully_contained_in_top200(self):
        # The previous default universe is a strict subset of the Top 200.
        assert set(COMBINED_UNIVERSE).issubset(set(NIFTY200_SYMBOLS))

    def test_market_universe_unchanged(self):
        # MARKET_UNIVERSE keeps its established meaning (benchmark + 50).
        assert MARKET_UNIVERSE == ("NIFTY",) + COMBINED_UNIVERSE


# ============================================================
# D. TOP200 MARKET UNIVERSE (benchmark + 200)
# ============================================================


class TestTop200MarketUniverse:
    def test_market_universe_top200_count(self):
        assert len(MARKET_UNIVERSE_TOP200) == 201
        assert MARKET_UNIVERSE_TOP200[0] == "NIFTY"
        assert set(MARKET_UNIVERSE_TOP200[1:]) == set(NIFTY200_SYMBOLS)

    def test_boundary_reexport_matches(self):
        assert TOP200_MARKET_UNIVERSE == MARKET_UNIVERSE_TOP200


# ============================================================
# E. UNIVERSE CONSTRUCTION BOUNDARY
# ============================================================


class TestUniverseBuilder:
    def test_nifty200_definition(self):
        d = UniverseBuilder.nifty200()
        assert d.kind is UniverseKind.NIFTY200
        assert len(d.symbols) == 200
        assert d.instrument_count == 201
        assert d.benchmark_index == ("NIFTY",)
        assert d.manifest_version == NIFTY200_MANIFEST_VERSION
        assert d.source_url == NIFTY200_SOURCE_URL
        assert d.csv_sha256 == NIFTY200_CSV_SHA256

    def test_default_definition_equals_builder(self):
        assert DEFAULT_NIFTY200_UNIVERSE == UniverseBuilder.nifty200()

    def test_vintage_definition(self):
        d = UniverseBuilder.vintage()
        assert d.kind is UniverseKind.VINTAGE
        assert d.symbols == COMBINED_UNIVERSE
        assert d.instrument_count == 51

    def test_custom_definition(self):
        d = UniverseBuilder.custom(["x", "X", "y"], label="custom")
        assert d.kind is UniverseKind.CUSTOM
        assert d.symbols == ("X", "Y")
        assert d.instrument_count == 2

    def test_custom_rejects_empty(self):
        with pytest.raises(ValueError):
            UniverseBuilder.custom(["X", ""])

    def test_custom_rejects_whitespace(self):
        with pytest.raises(ValueError):
            UniverseBuilder.custom(["   "])

    def test_custom_rejects_non_string(self):
        with pytest.raises(TypeError):
            UniverseBuilder.custom(["X", 123])

    def test_custom_rejects_empty_list(self):
        with pytest.raises(ValueError):
            UniverseBuilder.custom([])

    def test_nifty200_rejects_extra_symbols(self):
        with pytest.raises(ValueError):
            UniverseDefinition(
                kind=UniverseKind.NIFTY200,
                symbols=NIFTY200_SYMBOLS + ("INVENTED",),
            )

    def test_nifty200_rejects_missing_symbols(self):
        with pytest.raises(ValueError):
            UniverseDefinition(
                kind=UniverseKind.NIFTY200,
                symbols=NIFTY200_SYMBOLS[:-1],
            )

    def test_definition_is_immutable(self):
        d = UniverseBuilder.nifty200()
        with pytest.raises((AttributeError, TypeError)):
            d.symbols = ("X",)  # type: ignore[misc]

    def test_contains_case_insensitive(self):
        d = UniverseBuilder.nifty200()
        assert d.contains("reliance")
        assert d.contains("RELIANCE")
        assert not d.contains("NOTASTOCK")


# ============================================================
# F. SYMBOL RESOLUTION THROUGH PROVIDER ABSTRACTION
# ============================================================


class TestSymbolResolution:
    def test_dashboard_yahoo_map_covers_all_200(self):
        from dashboard.data_provider import YahooDataProvider

        p = YahooDataProvider(provider=object())  # no backend needed for map
        for sym in NIFTY200_SYMBOLS:
            assert p.resolve_symbol(sym) == f"{sym}.NS"

    def test_dashboard_yahoo_map_benchmark(self):
        from dashboard.data_provider import YahooDataProvider

        p = YahooDataProvider(provider=object())
        assert p.resolve_symbol("NIFTY") == "^NSEI"

    def test_engine_historical_yahoo_map_covers_all_200(self):
        from engine.data.historical_provider import (
            YahooHistoricalDataProvider,
            _default_yahoo_symbol_map,
        )

        mapping = _default_yahoo_symbol_map()
        for sym in NIFTY200_SYMBOLS:
            assert mapping[sym] == f"{sym}.NS"
        p = YahooHistoricalDataProvider(provider=object())
        for sym in NIFTY200_SYMBOLS:
            assert p.resolve_symbol(sym) == f"{sym}.NS"

    def test_engine_historical_yahoo_map_benchmark(self):
        from engine.data.historical_provider import _default_yahoo_symbol_map

        assert _default_yahoo_symbol_map()["NIFTY"] == "^NSEI"

    def test_dashboard_universe_top200_yahoo_map(self):
        from dashboard.universe import TOP200_YAHOO_SYMBOLS

        assert set(TOP200_YAHOO_SYMBOLS) == set(NIFTY200_SYMBOLS)
        for sym in NIFTY200_SYMBOLS:
            assert TOP200_YAHOO_SYMBOLS[sym] == f"{sym}.NS"

    def test_vintage_yahoo_map_still_covers_50(self):
        from dashboard.universe import UNIVERSE_YAHOO_SYMBOLS

        assert set(UNIVERSE_YAHOO_SYMBOLS) == set(COMBINED_UNIVERSE)
        for sym in COMBINED_UNIVERSE:
            assert UNIVERSE_YAHOO_SYMBOLS[sym] == f"{sym}.NS"

    def test_upstox_verified_map_unchanged(self):
        # Checkpoint 19.1 does NOT add unverified Upstox keys. The
        # verified research-universe map remains exactly the 5 verified
        # instruments (regression).
        from engine.data.historical_provider import (
            _default_upstox_instrument_key_map,
        )

        mapping = _default_upstox_instrument_key_map()
        assert set(mapping) == {"NIFTY", "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK"}
        assert mapping["RELIANCE"] == "NSE_EQ|INE002A01018"


# ============================================================
# G. NO LOOK-AHEAD / NO TRADING SEMANTICS
# ============================================================


class TestNoTradingSemantics:
    def test_manifest_has_no_evaluation_time(self):
        # Universe construction is configuration data; there is no
        # evaluation-time / candle concept anywhere in the manifest.
        import ast
        from pathlib import Path

        module_path = (
            Path(__file__).resolve().parent.parent
            / "src" / "engine" / "config" / "nifty200_manifest.py"
        )
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "evaluation_time":
                pytest.fail("manifest must not reference evaluation_time")
            if isinstance(node, ast.Attribute) and node.attr == "evaluation_time":
                pytest.fail("manifest must not reference evaluation_time")

    def test_manifest_module_imports_no_network_or_engine(self):
        import ast
        from pathlib import Path

        module_path = (
            Path(__file__).resolve().parent.parent
            / "src" / "engine" / "config" / "nifty200_manifest.py"
        )
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        forbidden = {"urllib", "requests", "httpx", "socket", "engine", "dashboard"}
        assert not (imported & forbidden), imported & forbidden

    def test_boundary_has_no_scanning_imports(self):
        import ast
        from pathlib import Path

        module_path = (
            Path(__file__).resolve().parent.parent
            / "src" / "engine" / "config" / "universe_boundary.py"
        )
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        forbidden = {"market_scanner", "services", "data_provider", "broker", "execution"}
        assert not (imported & forbidden), imported & forbidden

    def test_manifest_has_no_buy_sell_terms(self):
        import ast
        from pathlib import Path

        module_path = (
            Path(__file__).resolve().parent.parent
            / "src" / "engine" / "config" / "nifty200_manifest.py"
        )
        text = module_path.read_text(encoding="utf-8")
        for term in ("BUY", "SELL", "LONG", "SHORT", "STOP", "TARGET", "SIGNAL"):
            # Only the docstring disclaimer may mention "no ... trading".
            assert term not in text.split('"""', 2)[-1]


# ============================================================
# H. DETERMINISM
# ============================================================


class TestDeterminism:
    def test_imports_are_stable(self):
        import importlib

        m1 = importlib.import_module("engine.config.nifty200_manifest")
        m2 = importlib.import_module("engine.config.nifty200_manifest")
        assert m1.NIFTY200_SYMBOLS == m2.NIFTY200_SYMBOLS

    def test_builder_repeated_calls_identical(self):
        a = UniverseBuilder.nifty200()
        b = UniverseBuilder.nifty200()
        assert a == b
        assert a.symbols == b.symbols

    def test_symbols_independent_of_input_order(self):
        # UniverseDefinition canonicalizes to sorted order regardless of
        # the order symbols are supplied in.
        d1 = UniverseDefinition(kind=UniverseKind.CUSTOM, symbols=("B", "A", "C"))
        d2 = UniverseDefinition(kind=UniverseKind.CUSTOM, symbols=("C", "A", "B"))
        assert d1.symbols == d2.symbols == ("A", "B", "C")
