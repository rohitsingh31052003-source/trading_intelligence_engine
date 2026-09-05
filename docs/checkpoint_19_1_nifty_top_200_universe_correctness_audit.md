# CHECKPOINT 19.1 — NIFTY TOP 200 UNIVERSE CORRECTNESS AUDIT

**Date:** 2026-09-05
**Status:** AUDIT + MINIMAL IMPLEMENTATION COMPLETE
**Verdict:** **PASS**

**Audit type:** ARCHITECTURE / CORRECTNESS AUDIT with the minimum
implementation required to establish the NIFTY Top 200 universe foundation for
the future continuous intraday market scanner. No continuous scanning, no MTF
analysis, no setup detection/ranking, no alerts, no broker execution. No frozen
Checkpoint 10–17 file was reopened or modified.

---

## 1. Objective

Establish the **NIFTY Top 200 universe foundation** for the future continuous
intraday market scanner:

1. Determine whether a NIFTY Top 200 universe already exists.
2. Locate the current universe/watchlist definitions.
3. Identify the source used / intended for NIFTY Top 200 constituents.
4. Verify the constituent list is complete, deterministic and maintainable.
5. Verify how symbols resolve into the instrument identifiers the existing
   market-data layer requires.
6. Verify all constituents can be represented by the existing data-provider
   abstraction.
7. Verify duplicates, stale symbols, invalid symbols, missing mappings and
   silently skipped instruments are impossible.
8. Verify existing tests adequately establish universe correctness.
9. Verify the architecture already has a clean boundary between universe
   construction and market scanning.
10. Verify no existing implementation violates the requirement that broker
    execution remains deferred.

Only the minimum implementation changes required for Checkpoint 19.1 were made;
the existing architecture and abstractions were preserved unless a concrete
defect required change.

---

## 2. Scope

**In scope:** universe definition, constituent provenance, symbol/instrument
resolution, deterministic construction boundary, focused tests, audit document.

**Explicitly OUT of scope (per Checkpoint 19 instructions):** continuous
scanning (19.3), reliable intraday coverage (19.2), multi-timeframe analysis
(19.4), setup-quality intelligence (19.5), setup lifecycle (19.6), user alerts
(19.7), reliability/recovery/observability (19.8), validation/forward testing
(19.9), and ALL broker execution (Checkpoints 13–18 remain frozen).

---

## 3. Existing Architecture

The repository contains a large, mature, deterministic, point-in-time-safe
intelligence architecture (Sprints 11A–12E, Product Phases 1–6F, Checkpoints
10–18.5). The relevant universe-related architecture:

```
engine.config.universe          ← canonical universe definitions (NIFTY 50 ∪ SENSEX + NIFTY)
  └─ dashboard.universe         ← thin backward-compat shim + Yahoo symbol map
       └─ dashboard.watchlist   ← DEFAULT_WATCHLIST = ("NIFTY",) + COMBINED_UNIVERSE
            └─ dashboard.services / dashboard.data_provider / scanner
engine.data.historical_provider ← Yahoo + Upstox historical providers (symbol maps)
engine.models.historical_data   ← ResearchUniverse (configurable allow-list)
```

Key facts verified in source:

* `src/engine/config/universe.py` is the single maintainable universe source:
  `NIFTY50_CONSTITUENTS` (50), `SENSEX_CONSTITUENTS` (30), `combined_universe()`
  (50 de-duplicated), `MARKET_UNIVERSE` (51 = NIFTY + 50).
* `src/dashboard/universe.py` re-exports the engine lists and adds
  `UNIVERSE_YAHOO_SYMBOLS` (`<NSE>.NS` for the 50).
* `src/dashboard/watchlist.py` builds `DEFAULT_WATCHLIST = ("NIFTY",) +
  COMBINED_UNIVERSE` (51 instruments).
* `src/dashboard/data_provider.py` `YahooDataProvider.YAHOO_SYMBOL_MAP` resolves
  the canonical names to Yahoo symbols (`^NSEI` for NIFTY, `<NSE>.NS` for
  stocks).
* `src/engine/data/historical_provider.py` `_default_yahoo_symbol_map()` derives
  the historical Yahoo map from the same canonical universe; the Upstox
  historical provider carries a small VERIFIED instrument-key map
  (RELIANCE/TCS/HDFCBANK/ICICIBANK/NIFTY) — unverified keys are never guessed.
* `src/engine/models/historical_data.py` `ResearchUniverse` is a configurable
  allow-list (default 5 instruments) used by the historical-data layer.
* `src/dashboard/services.py` `available_instruments()` returns fixture
  instruments for the fixture provider and the default watchlist for live
  providers.

---

## 4. Current Universe Implementation (as found)

| Question | Finding | Evidence |
|----------|---------|----------|
| 1. Does a NIFTY Top 200 universe exist? | **NO.** The implemented universe is **NIFTY 50 ∪ SENSEX (50 de-duplicated stocks) + the NIFTY benchmark index (51 instruments total)**. Repo-wide grep: zero "NIFTY 200"/"Top 200" hits in source or docs. | `src/engine/config/universe.py`, `grep` |
| 2. Where do the current universe/watchlist definitions live? | `src/engine/config/universe.py` (engine), `src/dashboard/universe.py` (shim), `src/dashboard/watchlist.py` (`DEFAULT_WATCHLIST`), `src/engine/models/historical_data.py` (`ResearchUniverse`) | source scan |
| 3. What source is used/intended for NIFTY Top 200 constituents? | **None.** No Top-200 constituent source existed. The existing NIFTY 50/SENSEX list is a manually maintained point-in-time snapshot ("as of the December 2025 reconstitutions") with no auto-update and no canonical Top-200 source. | docstring + repo grep |
| 4. Is the current list complete/deterministic/maintainable? | NIFTY 50/SENSEX is deterministic (50 de-duplicated, sorted) and maintainable (single module), but it is NOT the Top 200 and is not versioned/auditable against an upstream source. | `combined_universe()` |
| 5. How are symbols resolved into provider identifiers? | Canonical name → provider-local map: Yahoo `resolve_symbol` (`<NSE>.NS` / `^NSEI`), Upstox `resolve_instrument_key` (verified `NSE_EQ|ISIN` keys). Unknown → passthrough (Yahoo) or clear `KeyError`/`UNSUPPORTED` (Upstox). | `dashboard/data_provider.py`, `engine/data/historical_provider.py` |
| 6. Can all constituents be represented by the provider abstraction? | For the EXISTING 51: yes (Yahoo). For a future Top 200: the abstraction is provider-agnostic and extensible; Yahoo `<NSE>.NS` covers all NSE-listed equities; Upstox requires per-ISIN verified keys (only 5 verified). | provider code |
| 7. Duplicates/stale/invalid/missing/silently-skipped possible? | Within the existing 51: no duplicates (set de-dup), no silently skipped (per-symbol failure isolation). Stale membership is possible (static snapshot, no reconstitution tracking). Missing provider mappings are surfaced honestly (unavailable/unsupported), never fabricated. | `combined_universe()`, `_scan_one` |
| 8. Do existing tests establish universe correctness? | PARTIAL. `tests/test_historical_data_foundation.py` verifies the 50/51 counts + Yahoo map coverage; `tests/test_upstox_historical.py` verifies the verified key map. **No test asserts the Top 200** (it did not exist). | test scan |
| 9. Clean boundary between universe construction and scanning? | PARTIAL. Universe is configuration data; the scanner consumes `Watchlist`/`ResearchUniverse`; but there was no explicit, validated "universe construction" boundary (no `UniverseDefinition`/builder) and no provenance/versioning. | source scan |
| 10. Broker execution deferred? | **YES.** Checkpoints  13–18 (intent/authorization/command/submission/broker adapter) are frozen and not wired; the execution gate is DISABLED; no path to live orders. Checkpoint 19.1 adds nothing execution-related. | Checkpoint 13–18 docs + source |

---

## 5. Data-Source / Provenance Assessment

The authoritative source for the NIFTY Top 200 index constituents is the
official NSE publication:

* Index page: `https://www.nseindia.com/static/products-services/indices-nifty200-index`
* Constituent CSV: `https://nsearchives.nseindia.com/content/indices/ind_nifty200list.csv`
* NSE "Updated on" label: **22/04/2026**
* Retrieved (this checkpoint): **2026-09-05**
* HTTP Last-Modified: `Sat, 05 Sep 2026 03:31:32 GMT`
* CSV byte length: **13081**
* CSV SHA-256: `76b8b127931953ce7e5e5511c99c3b73775140eeb83b6b293085b4a9483dce1a`
* Data rows: **200** (exactly the published membership)

The NIFTY 200 Index is defined by NSE as including every company forming part
of the NIFTY 100 and the NIFTY Full Midcap 100 (large + mid market-cap
coverage; the exact free-float coverage percentage is published in the
official NSE factsheet and is not asserted here).

The raw bytes were archived at `scripts/checkpoint19_data/ind_nifty200list.csv`
and the JSON projection at `scripts/checkpoint19_data/ind_nifty200list.json`
so the embedded snapshot is byte-comparable with the upstream source. The
embedded manifest (`src/engine/config/nifty200_manifest.py`) carries the
source URL, version label and CSV SHA-256.

**Verification performed on the embedded snapshot:**

* exactly 200 rows; no duplicate `Symbol`; no duplicate `ISIN Code`;
* every row has a non-empty `Symbol`, `Company Name`, `Industry`, `Series`
  (all `EQ`) and `ISIN Code`;
* every `Symbol` matches `[A-Z0-9&.\-]+` (NSE trading-symbol charset);
* every `ISIN` matches the INE prefix + 9 alphanumeric + 1 digit;
* the archived CSV SHA-256 matches the embedded manifest SHA-256;
* the triple consistency check (archived CSV == JSON projection == embedded
  manifest) passes.

---

## 6. Instrument Mapping Assessment

| Provider | Resolution rule | Top-200 coverage |
|----------|----------------|------------------|
| Yahoo (dashboard live) | `YAHOO_SYMBOL_MAP` — `<NSE>.NS` for equities, `^NSEI` for NIFTY | **All 200** now resolve via `TOP200_YAHOO_SYMBOLS` (added). |
| Yahoo (engine historical) | `_default_yahoo_symbol_map()` — same convention | **All 200** now resolve (added). |
| Upstox (engine historical) | `_default_upstox_instrument_key_map()` — verified `NSE_EQ|ISIN` keys | Only the 5 verified instruments (RELIANCE/TCS/HDFCBANK/ICICIBANK/NIFTY). Checkpoint 19.1 does **NOT** add unverified Upstox keys — an unverified instrument is never guessed. The Upstox provider reports `UNSUPPORTED` for the rest (honest, never fabricated). |

The Yahoo convention is the same for every NSE-listed equity, so all 200
constituents are representable by the existing provider abstraction. Upstox
historical coverage is a documented, provider-scoped limitation (a future
checkpoint owns verifying the remaining instrument keys against the Upstox
instrument master).

---

## 7. Completeness / Correctness Findings

* The embedded manifest contains **exactly the 200** official NSE NIFTY 200
  constituents (verified against the archived CSV).
* **All 50 NIFTY 50 constituents are present** in the Top 200 with identical
  symbols (verified programmatically); the vintage universe is a strict subset
  of the Top 200.
* The NIFTY benchmark index instrument (`NIFTY`) is kept **separate** from the
  Top 200 stock universe (a benchmark is not a "constituent" of itself). The
  `MARKET_UNIVERSE_TOP200` tuple = `("NIFTY",) + NIFTY200_SYMBOLS` (201).
* The existing `MARKET_UNIVERSE` (51) is **unchanged** — the existing scanner /
  workstation / watchlist behaviour is preserved (non-regression).
* `UniverseBuilder.nifty200()` strictly validates the manifest membership
  (exactly 200 unique symbols, all metadata present, no invented extras).

---

## 8. Duplicate / Stale / Unmapped-Symbol Findings

* **Duplicates:** impossible in the manifest (verified: 200 unique symbols,
  200 unique ISINs). `UniverseDefinition` de-duplicates and sorts; a NIFTY200
  definition with a duplicate/invented/missing symbol raises `ValueError`.
* **Stale symbols:** the manifest is a **point-in-time snapshot** (NSE
  "Updated on" 22/04/2026). NSE re-constitutes semi-annually; a FUTURE
  checkpoint owns re-fetch/re-validate/re-embed. This is a documented
  limitation, not a defect — the manifest is versioned so a stale snapshot is
  always traceable.
* **Invalid symbols:** impossible (charset-validated; empty/whitespace rejected).
* **Missing mappings:** Yahoo covers all 200 (added). Upstox covers the 5
  verified instruments and reports the rest as `UNSUPPORTED` (never silently
  skipped, never fabricated).
* **Silently skipped instruments:** impossible in the existing scanner
  (`_scan_one` per-symbol failure isolation → honest `INVALID` row) and in the
  provider abstraction (unavailable/unsupported statuses).

---

## 9. Determinism Assessment

* The manifest is a sorted, de-duplicated tuple; `NIFTY200_SYMBOLS ==
  tuple(sorted(...))` (tested).
* `UniverseBuilder` is stateless and pure; repeated calls return equal
  definitions (tested).
* `UniverseDefinition` canonicalizes to sorted order regardless of input order
  (tested).
* No wall-clock, no randomness, no unordered iteration, no network at import or
  run time.
* The embedded SHA-256 makes the snapshot byte-verifiable (tested).

---

## 10. Test Coverage

New focused tests (`tests/test_nifty200_universe.py`, 48 tests) cover:

* **A. Manifest completeness** — exactly 200, no dup symbols/ISINs, metadata
  coverage, sorted, known members present, symbol charset, ISIN format.
* **B. Manifest provenance** — official source URL, version label, 64-hex
  SHA-256, archived-CSV SHA match, archived-CSV 200 rows.
* **C. Vintage universe preserved** — NIFTY 50=50, SENSEX=30, combined=50,
  market=51, vintage ⊆ Top200, MARKET_UNIVERSE unchanged.
* **D. Top200 market universe** — 201 count, benchmark first, set equality.
* **E. Universe construction boundary** — nifty200/vintage/custom builders,
  empty/whitespace/non-string/empty-list rejection, invented/missing-symbol
  rejection, immutability, case-insensitive membership.
* **F. Symbol resolution** — dashboard Yahoo map covers all 200, benchmark
  `^NSEI`, engine historical Yahoo map covers all 200, Top200 Yahoo map,
  vintage Yahoo map unchanged, Upstox verified map unchanged.
* **G. No trading semantics** — no `evaluation_time`, no network/engine imports
  in the manifest, no scanner/provider/broker/execution imports in the
  boundary, no BUY/SELL/LONG/SHORT/STOP/TARGET/SIGNAL terms in manifest code.
* **H. Determinism** — stable imports, repeated builder calls identical,
  input-order independence.

Existing tests that already establish universe correctness (unchanged, all
pass): `tests/test_historical_data_foundation.py` (50/51 counts + Yahoo map),
`tests/test_upstox_historical.py` (verified key map), `tests/test_workstation.py`
(`DEFAULT_WATCHLIST`), `tests/test_watchlist_scanner.py` (watchlist size).

---

## 11. Changes Made

| File | Change | Type |
|------|--------|------|
| `src/engine/config/nifty200_manifest.py` | NEW — canonical, versioned, auditable NIFTY Top 200 manifest (200 × (symbol, company, ISIN) + source URL + version + CSV SHA-256) | ADD |
| `src/engine/config/universe.py` | Re-export the manifest symbols/ISINs/metadata/provenance; add `MARKET_UNIVERSE_TOP200` (= benchmark + 200). `MARKET_UNIVERSE` unchanged. | ADD |
| `src/dashboard/universe.py` | Re-export Top-200 constants; add `TOP200_YAHOO_SYMBOLS` (`<NSE>.NS` for all 200) | ADD |
| `src/dashboard/data_provider.py` | `YahooDataProvider.YAHOO_SYMBOL_MAP` now also merges `TOP200_YAHOO_SYMBOLS` (all 200 resolve) | ADD |
| `src/engine/data/historical_provider.py` | `_default_yahoo_symbol_map()` now also covers all NIFTY 200 symbols | ADD |
| `src/engine/config/universe_boundary.py` | NEW — `UniverseKind` / `UniverseDefinition` / `UniverseBuilder` (clean, validated construction boundary; no scanning/execution imports) | ADD |
| `tests/test_nifty200_universe.py` | NEW — 48 focused correctness/resolution/determinism/non-regression tests | ADD |
| `scripts/test_nifty200_universe.py` | NEW — deterministic demo (19 PASS) | ADD |
| `scripts/checkpoint19_data/ind_nifty200list.csv` | NEW — archived official NSE CSV (13081 bytes, SHA-256 verified) | ADD |
| `scripts/checkpoint19_data/ind_nifty200list.json` | NEW — JSON projection of the CSV (200 rows) | ADD |
| `docs/checkpoint_19_1_nifty_top_200_universe_correctness_audit.md` | NEW — this document | ADD |

The four pre-existing files (`src/engine/config/universe.py`,
`src/dashboard/universe.py`, `src/dashboard/data_provider.py`,
`src/engine/data/historical_provider.py`) received ONLY additive re-exports /
map entries — no existing constant or function was deleted or changed. No
frozen Checkpoint 10–17 file was reopened or modified.

---

## 12. Tests Executed and Results

| Command | Result |
|--------|--------|
| `python3 -m pytest tests/test_nifty200_universe.py -q` | **48 passed** |
| `python3 -m pytest tests/test_historical_data_foundation.py tests/test_upstox_historical.py tests/test_dashboard.py tests/test_watchlist_scanner.py tests/test_workstation.py tests/test_live_data_integration.py tests/test_corpus_audit.py tests/test_corpus_preparation.py tests/test_corpus_ingestion.py tests/test_historical_data_availability.py tests/test_historical_data_consumer.py tests/test_research_corpus.py tests/test_live_paper_validation.py tests/test_run_paper_trading_cycle.py tests/test_yahoo_range_fix.py tests/test_historical_setup_research_integration.py -q` | **1049 passed** (universe/provider/dashboard/watchlist regression) |
| `python3 -m pytest -q` (full suite) | **6354 passed, 12 skipped, 2 warnings** (baseline 6306 → +48 new; 12 skips = opt-in real-broker/sandbox tests; 2 pre-existing third-party deprecation warnings) |
| `python3 scripts/test_nifty200_universe.py` | **19 PASS / 0 FAIL** (demo) |

Pipeline baseline unchanged (signals=4, trades=3) — verified by the full suite.

---

## 13. Architectural Boundary Assessment

* **Universe construction vs. scanning:** Checkpoint 19.1 establishes an
  explicit, validated boundary: `UniverseBuilder`/`UniverseDefinition` construct
  and validate a universe; the existing `MarketScanner`/`scan_watchlist`
  consume a watchlist. The boundary module imports **no** scanner/provider/
  broker/execution code (AST-verified). Checkpoint 19.3 (continuous scanning)
  will consume this boundary.
* **Membership vs. setup quality:** the boundary module contains zero trading /
  scoring / prediction / decision / ranking logic; membership is configuration
  data only (tested).
* **No look-ahead:** universe construction has no evaluation-time / candle
  concept (tested); the manifest never contacts the network.
* **Broker execution deferred:** Checkpoint 19.1 adds nothing execution-related;
  the frozen Checkpoints 13–18 boundaries are intact (verified).

---

## 14. Limitations

* The manifest is a **point-in-time snapshot** (NSE "Updated on" 22/04/2026).
  NSE re-constitutes semi-annually; a FUTURE checkpoint owns re-fetch,
  re-validate and re-embed. The version + SHA-256 make the snapshot auditable.
* **Upstox historical coverage** is limited to the 5 verified instrument keys.
  Checkpoint 19.1 intentionally does NOT add unverified Upstox keys; a future
  checkpoint must verify the remaining instrument keys against the Upstox
  instrument master before Upstox can serve the full Top 200.
* The **Yahoo live/intraday** provider covers all 200 via the `.NS` convention,
  but intraday history is bounded by Yahoo's retention windows (documented in
  Product Phase 1); Checkpoint 19.2 owns reliable intraday coverage.
* `MARKET_UNIVERSE` (the scanner's current default) is intentionally **not**
  switched to the Top 200 in this checkpoint; Checkpoint 19.3 owns the
  universe-selection decision.
* No live NSE re-fetch was automated (this is a deliberate offline/deterministic
  design; re-embedding is an explicit operator action).

---

## 15. Final Verdict

**PASS.** The NIFTY Top 200 universe foundation is now established:

* complete (exactly the 200 official NSE constituents),
* deterministic (sorted, de-duplicated, stable),
* auditable (official source URL + version + CSV SHA-256, archived raw bytes),
* resolvable (all 200 via the existing Yahoo provider abstraction; Upstox
  verified-map preserved),
* bounded (clean, validated construction boundary separate from scanning),
* non-regressive (vintage universe + all existing provider maps preserved),
* broker-execution-deferred (nothing execution-related introduced).

**This is universe CONSTRUCTION correctness only.** It does NOT prove scanning
quality, does NOT predict performance, and does NOT authorize any broker
execution.
