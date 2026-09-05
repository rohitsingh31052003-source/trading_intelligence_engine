"""
Checkpoint 19.1 demo — NIFTY Top 200 universe correctness.

Proves (deterministically, offline):
  1. the official NSE NIFTY 200 manifest is embedded (200 constituents);
  2. the manifest is complete, duplicate-free, deterministic and auditable
     (source URL + version + CSV SHA-256);
  3. every constituent resolves to a Yahoo symbol through the existing
     provider abstraction (dashboard + engine historical);
  4. the vintage NIFTY 50 ∪ SENSEX universe is preserved and fully
     contained in the Top 200;
  5. the UniverseBuilder boundary separates construction from scanning
     and rejects invalid/invented universes;
  6. no broker/execution/look-ahead semantics are introduced.

Run:  python scripts/test_nifty200_universe.py
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from engine.config.nifty200_manifest import (  # noqa: E402
    NIFTY200_CSV_SHA256,
    NIFTY200_MANIFEST_VERSION,
    NIFTY200_METADATA,
    NIFTY200_SOURCE_URL,
    NIFTY200_SYMBOLS,
)
from engine.config.universe import (  # noqa: E402
    COMBINED_UNIVERSE,
    MARKET_UNIVERSE,
    MARKET_UNIVERSE_TOP200,
)
from engine.config.universe_boundary import (  # noqa: E402
    UniverseBuilder,
    UniverseDefinition,
    UniverseKind,
)

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))


def _rejects_invented_symbol() -> bool:
    try:
        UniverseDefinition(
            kind=UniverseKind.NIFTY200,
            symbols=NIFTY200_SYMBOLS + ("INVENTED",),
        )
        return False
    except ValueError:
        return True


def _rejects_empty_custom() -> bool:
    try:
        UniverseBuilder.custom([""])
        return False
    except ValueError:
        return True


def _boundary_is_clean() -> bool:
    text = (
        Path(__file__).resolve().parent.parent
        / "src" / "engine" / "config" / "universe_boundary.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(text)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    forbidden = {
        "market_scanner", "services", "data_provider", "broker", "execution",
    }
    return not (imported & forbidden)


def main() -> int:
    # 1. Manifest embedded + complete.
    check("manifest has exactly 200 constituents", len(NIFTY200_SYMBOLS) == 200)
    check(
        "manifest has no duplicate symbols",
        len(set(NIFTY200_SYMBOLS)) == 200,
    )
    check(
        "metadata covers every symbol",
        set(NIFTY200_METADATA) == set(NIFTY200_SYMBOLS),
    )
    check(
        "symbols are sorted",
        NIFTY200_SYMBOLS == tuple(sorted(NIFTY200_SYMBOLS)),
    )

    # 2. Auditable provenance.
    check(
        "source URL is official NSE",
        "nsearchives.nseindia.com" in NIFTY200_SOURCE_URL,
    )
    check("manifest version is set", bool(NIFTY200_MANIFEST_VERSION))
    check(
        "CSV SHA-256 is 64 hex",
        len(NIFTY200_CSV_SHA256) == 64
        and all(c in "0123456789abcdef" for c in NIFTY200_CSV_SHA256),
    )

    # 3. Symbol resolution through the existing provider abstraction.
    from dashboard.data_provider import YahooDataProvider  # noqa: E402
    from engine.data.historical_provider import (  # noqa: E402
        YahooHistoricalDataProvider,
        _default_yahoo_symbol_map,
    )

    dash = YahooDataProvider(provider=object())
    hist = YahooHistoricalDataProvider(provider=object())
    check(
        "dashboard Yahoo map resolves all 200",
        all(dash.resolve_symbol(s) == f"{s}.NS" for s in NIFTY200_SYMBOLS),
    )
    check(
        "engine historical Yahoo map resolves all 200",
        all(hist.resolve_symbol(s) == f"{s}.NS" for s in NIFTY200_SYMBOLS),
    )
    check(
        "engine historical Yahoo map covers all 200",
        set(NIFTY200_SYMBOLS) <= set(_default_yahoo_symbol_map()),
    )
    check(
        "benchmark NIFTY resolves to ^NSEI",
        dash.resolve_symbol("NIFTY") == "^NSEI",
    )

    # 4. Vintage universe preserved + contained.
    check("vintage MARKET_UNIVERSE still 51", len(MARKET_UNIVERSE) == 51)
    check(
        "vintage universe fully contained in Top 200",
        set(COMBINED_UNIVERSE) <= set(NIFTY200_SYMBOLS),
    )
    check("TOP200 market universe is 201", len(MARKET_UNIVERSE_TOP200) == 201)

    # 5. Construction boundary.
    d = UniverseBuilder.nifty200()
    check("builder nifty200 kind", d.kind is UniverseKind.NIFTY200)
    check("builder nifty200 count 201", d.instrument_count == 201)
    check("builder rejects invented symbol", _rejects_invented_symbol())
    check("builder rejects empty custom name", _rejects_empty_custom())

    # 6. No trading / execution semantics.
    check("no broker/execution imports in boundary", _boundary_is_clean())

    print("=" * 72)
    print("CHECKPOINT 19.1 — NIFTY TOP 200 UNIVERSE CORRECTNESS")
    print("=" * 72)
    print(f"manifest version : {NIFTY200_MANIFEST_VERSION}")
    print(f"source           : {NIFTY200_SOURCE_URL}")
    print(f"csv sha256       : {NIFTY200_CSV_SHA256}")
    print(f"constituents     : {len(NIFTY200_SYMBOLS)}")
    print()
    failed = 0
    for name, ok, detail in CHECKS:
        status = "PASS" if ok else "FAIL"
        if not ok:
            failed += 1
        suffix = f"  ({detail})" if detail else ""
        print(f"  [{status}] {name}{suffix}")
    print()
    print(f"{len(CHECKS) - failed} PASS / {failed} FAIL")
    if failed:
        print("Checkpoint 19.1 demo FAILED.")
        return 1
    print("Checkpoint 19.1 demo completed successfully.")
    print("NOTE: this is universe CONSTRUCTION correctness only. It does NOT")
    print("prove scanning quality, does NOT predict performance, and does NOT")
    print("authorize any broker execution.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())