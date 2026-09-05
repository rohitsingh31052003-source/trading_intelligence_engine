# CHECKPOINT 18.5 — MARKET SCANNING & TRADE-SETUP INTELLIGENCE GAP AUDIT

**Date:** 2026-09-05
**Status:** AUDIT COMPLETE
**Verdict:** **PASS WITH LIMITATIONS**

**Audit type:** ARCHITECTURE / PRODUCT-GAP ASSESSMENT ONLY. No production code
modified. No existing test modified/deleted/weakened. No broker connected. No
order submitted. No execution gate activated. No live trading. No frozen
Checkpoint 10–17 file reopened.

---

## 1. Executive Summary

The repository contains a large, mature, deterministic, point-in-time-safe
intelligence architecture (Sprints 11A–12E, Product Phases 1–6F, Checkpoints
10–18.4). The **analytical core** required by the product goal — market
structure, multi-timeframe context, setup detection, trade geometry, decision
classification, opportunity ranking, a multi-instrument scanner, a workstation,
trade planning, paper trading and live/near-live data ingestion — is **already
built and regression-tested** (full suite: **6306 passed, 12 skipped, 2
pre-existing deprecation warnings**).

However, the **product goal is specifically the NIFTY Top 200 universe with
continuous market scanning and user alerts**. Measured against that goal the
architecture has three material gaps:

1. **Universe mismatch (GAP A):** the implemented universe is **NIFTY 50 ∪
   SENSEX (50 de-duplicated stocks) + the NIFTY benchmark index (51
   instruments total)** — NOT the NIFTY Top 200. The repository contains **zero
   references** to "NIFTY 200" / "Top 200" anywhere in source or docs. This is
   the single largest product-level gap.
2. **No continuous scanning (GAP B):** every scan is a **single, explicit,
   on-demand cycle** (`run_once`). There is a Windows Task Scheduler launcher
   (every 15 min, 09:15→16:00 IST) for the *paper-trading* cycle, but there is
   **no in-engine continuous scanning loop, no scan scheduler, no incremental
   scan, no candle-completion-driven trigger, no scan-state persistence**.
3. **No user alerting (GAP C):** there is **no notification/alert subsystem**
   (no alert model, no deduplication/cooldown, no push/CLI alert on a new
   qualified setup). The only "user-facing" surface is the pull-based dashboard
   (`/`, `/scan`, `/workstation`) and the paper-trading CLI report.

Everything else the product asks for is at least PARTIAL and often BUILT:
market structure (Swing/MarketStructure/StructureAnalysis), multi-timeframe
context (11P/11U MTF alignment), setup detection (11O/11Q/11R), entry/stop/
target + R:R (11R), decision/ranking (11S/11T/11U), trade plan (Phase 4),
paper trading (Phase 5 + operations), live/near-live data (Phase 1, Yahoo
provider with completed-candle boundary), historical data (Phase 6A–6D),
persistence, observability, and an extensive test suite.

Broker execution (Checkpoints 13–18) is correctly **separated and frozen** and
is **not** required for the product goal; it should remain a future optional
boundary.

**Recommendation:** continue evolving toward
`MARKET DATA → MARKET SCANNER → MARKET STRUCTURE → MULTI-TIMEFRAME CONTEXT →
SETUP INTELLIGENCE → TRADE PLAN → OPPORTUNITY RANKING → USER ALERT`, with
`USER → MANUAL EXECUTION` and broker execution outside the product boundary.
The next checkpoints should prioritize (1) NIFTY Top 200 universe correctness,
(2) reliable intraday coverage, (3) continuous scanning, (4) setup lifecycle +
alert deduplication, (5) user alerts. **No implementation was performed in this
checkpoint.**

---

## 2. Original Product Goal (source of truth)

> "An engine that continuously scans the NIFTY Top 200 stocks, analyzes the
> market across relevant timeframes and market levels, identifies high-quality
> intraday trade opportunities, prepares complete trade setups, ranks the
> opportunities, rejects poor-quality setups, and tells the user when a good
> setup appears."

The USER will manually execute trades. Broker execution is **out of scope** for
the current product direction.

The engine should answer: what stocks have a good setup; why; direction; entry;
invalidation/stop; targets; risk/reward; supporting market structure; confirming
timeframe; pending conditions; setup strength; rank vs other opportunities;
actionable NOW vs developing; why weak setups are rejected; and how a valid
setup is detected and reported in the live market.

---

## 3. Current Architecture (as implemented)

```
LIVE / NEAR-LIVE DATA (Yahoo provider, Phase 1) or FIXTURE (deterministic)
        ↓  completed-candle boundary (split_completed_candles)
DATA NORMALIZATION (DataValidator, OHLCVCandle)
        ↓
DashboardAnalysisService.analyze / scan_watchlist / workstation
        ↓
InstrumentDataset (context + setup candles)
        ↓
MarketScanner.scan(datasets, evaluation_time, ScanEngines.default())
        ↓  ScanEngines bundle (Sprint 11O–11U):
        CandlePatternEngine (11O) → MarketContextEngine (11P) →
        SetupConfluenceEngine (11Q) → TradeCandidateEngine (11R) →
        TradeDecisionEngine (11S) → TradeOpportunityEngine (11T) →
        MTFAlignmentEngine (11U)
        ↓
MarketScanResult (ranked opportunities, best/alternatives/rejected)
        ↓
DashboardTradeView (presentation projection) → FastAPI JSON / Jinja2 HTML
        ↓
TradePlan (Phase 4) → PaperTrade (Phase 5) → PaperTradingOperations (Phase 5 ops)
        ↓
[FROZEN, OUT OF SCOPE] OperationalTradeIntent → ExecutionAuthorization →
ExecutionCommand → SubmissionLifecycle → BrokerAdapter → (future broker)
```

Key evidence:
- `src/engine/intelligence/market_scanner.py:776` `ScanEngines.default()` builds
  the exact 7-engine bundle (lines 806–831).
- `src/engine/intelligence/market_scanner.py:189` `MarketScanner.scan`.
- `src/dashboard/services.py:1522` `_scan_one` (failure isolation),
  `:1568` `_build_view`, `:2347` `default_service`.
- `src/dashboard/data_provider.py:999` `YahooDataProvider.fetch` (completed-candle
  boundary), `:1139` `make_provider`.

---

## 4. NIFTY Top 200 Universe Assessment

| # | Question | Finding | Evidence |
|---|----------|---------|----------|
| 1 | What universe is implemented? | **NIFTY 50 ∪ SENSEX (50 de-duplicated stocks) + NIFTY benchmark index (51 total)** | `src/engine/config/universe.py` |
| 2 | Where is it defined? | `src/engine/config/universe.py` (engine-level) + thin shim `src/dashboard/universe.py` + `DEFAULT_WATCHLIST` in `src/dashboard/watchlist.py:47` | `("NIFTY",) + COMBINED_UNIVERSE` |
| 3 | Static or dynamic? | **STATIC** — a manually maintained tuple; no network/dynamic fetch (verified: no `requests`/`httpx`/`urllib` imports in universe/watchlist modules) | source scan |
| 4 | Date/version of constituents? | "index composition as of the December 2025 reconstitutions" (docstring) — a point-in-time snapshot, not auto-updated | `src/engine/config/universe.py:24-25` |
| 5 | How many instruments actually scanned? | Default watchlist = **51** (50 stocks + NIFTY). Fixture provider serves only 5 (`FIXTURE_INSTRUMENTS`); Yahoo provider can serve the 51 (all mapped to `.NS` / `^NSEI`). | `MARKET_UNIVERSE` count verified programmatically = 51; `FIXTURE_INSTRUMENTS` = 5 |
| 6 | All NIFTY Top 200 represented? | **NO** — the Top 200 is not represented at all; only NIFTY 50 (all 50 SENSEX members are also NIFTY 50). | `NIFTY50_CONSTITUENTS` = 50, `SENSEX_CONSTITUENTS` = 30, union = 50 |
| 7 | Duplicates? | **No duplicates within the implemented universe** — `combined_universe()` de-duplicates by set; verified 0 duplicates, sorted. | `src/engine/config/universe.py:144-153` |
| 8 | Symbols mapped to providers? | Yes for the implemented universe: `UNIVERSE_YAHOO_SYMBOLS` (`<NSE>.NS`), `^NSEI` for NIFTY; Upstox historical provider has a verified key map (RELIANCE/TCS/HDFCBANK/ICICIBANK/NIFTY). | `src/dashboard/universe.py`, `src/dashboard/data_provider.py:785` |
| 9 | Benchmark separate from 200? | A benchmark index instrument (`NIFTY`) exists separately from the stock universe (`BENCHMARK_INDEX`). | `src/engine/config/universe.py:46-51` |
| 10 | Extensible? | **Yes** — `ResearchUniverse` is a configurable allow-list; watchlist is a mutable collection; provider symbol maps are configurable. | `src/engine/models/historical_data.py:562`, `src/dashboard/watchlist.py:111` |
| 11 | Scanner processes every configured instrument? | Yes — `scan_watchlist` iterates the whole watchlist; `PaperTradingOperations.run_once` iterates sorted instruments. | `src/dashboard/services.py`, `src/dashboard/paper_trade_operations.py:507-531` |
| 12 | Per-symbol failure isolation? | **Yes** — `_scan_one` catches exceptions → honest `INVALID` row; one bad symbol never aborts the scan. | `src/dashboard/services.py:1522` |
| 13 | Canonical NIFTY Top 200 source? | **NO** — there is only a manually maintained NIFTY 50/SENSEX list. No Top 200 constituent source exists in the repository. | repo-wide grep: zero "NIFTY 200"/"Top 200" hits |

**GAP IDENTIFIED (explicit, per instructions):** the repository contains **NIFTY
50 / SENSEX**, not the NIFTY Top 200. This is a documented product-level gap.
No current NIFTY Top 200 constituent list was invented; the repository evidence is
reported as-is and is distinguished from any external/current constituent
information (none was fetched).

---

## 5. Data Pipeline Assessment

| Stage | Status | Evidence |
|-------|--------|----------|
| MARKET DATA | IMPLEMENTED (live/near-live Yahoo + deterministic fixture + historical Upstox/Yahoo) | `src/dashboard/data_provider.py` (YahooDataProvider), `src/engine/data/historical_provider.py` (UpstoxHistoricalDataProvider, YahooHistoricalDataProvider), `src/engine/data/historical_fixtures.py` |
| DATA NORMALIZATION | IMPLEMENTED | `DataValidator.validate_candle`, `OHLCVCandle.__post_init__`, `DataNormalizer` (`src/engine/data/yahoo_provider.py:42`) |
| INDICATORS / FEATURES | PARTIAL — candle patterns (11O) exist; **no volume/volatility/ATR/momentum/RSI factor is used in live scoring** (verified: zero such terms in trade_opportunity/decision/candidate/setup_confluence configs); `src/engine/intelligence/volume.py` is an **empty 0-byte file** with no references | source scan |
| MARKET STRUCTURE | IMPLEMENTED (swings → HH/HL/LH/LL → bias) | `SwingEngine`, `MarketStructureEngine`, `StructureAnalysisEngine` via `MarketContextEngine` |
| MULTI-TIMEFRAME ANALYSIS | IMPLEMENTED (context < T + setup ≤ T + MTFAlignment) | `MarketScanner._scan_instrument` (`src/engine/intelligence/market_scanner.py:300-540`), `MTFAlignmentEngine` |
| SETUP DETECTION | IMPLEMENTED (patterns → confluence → candidate) | `CandlePatternEngine`, `SetupConfluenceEngine`, `TradeCandidateEngine` |
| TRADE PLAN | IMPLEMENTED (Phase 4 risk/position sizing) | `TradePlanningEngine` (`src/engine/intelligence/trade_planning.py`) |
| OPPORTUNITY / SIGNAL RANKING | IMPLEMENTED (11S decision score, 11T eligibility, 11U market-level ranking) | `TradeDecisionEngine`, `TradeOpportunityEngine`, `MarketScanner._ranking_key` |
| MARKET SCAN RESULT | IMPLEMENTED (`MarketScanResult` with best/alternatives/rejected) | `src/engine/models/market_scan.py:371` |
| USER-VISIBLE OUTPUT / ALERT | PARTIAL — pull-based dashboard/CLI only; **no push alert** | `src/dashboard/app.py`, `scripts/run_paper_trading_cycle.py` |

---

## 6. What Is Already Built

| Component | What it does | What it does NOT do | Contributes to product goal |
|-----------|--------------|---------------------|----------------------------|
| Historical data (Phase 6A–6D) | Deterministic local + Upstox/Yahoo historical ingestion, validation, gaps, persistence, corpus, setup research, evidence | Does not feed live scanning automatically; no auto-acquire by default | YES (research/evidence context) |
| Market data providers (Phase 1) | Fixture + Yahoo live/near-live with completed-candle boundary | No streaming; no broker data | YES (live data) |
| Data normalization | OHLCV validation, canonical candles, timezone normalization | — | YES |
| Timeframe handling | 1m/3m/5m/15m/30m/1h/4h/1D supported; context fallback map | Only 15M+1D in fixtures; Yahoo native intervals | YES |
| Market structure | Swings, HH/HL/LH/LL, bias, structure_intact | No BOS/CHOCH in the live scan path (BOS/CHOCH exist but are only used by the historical signal pipeline `TrendEngine`, not the live `MarketTrendEngine`) | YES |
| BOS / structure breaks | `BOSEngine`, `CHOCHEngine` exist (Sprint 11A) | Not connected to the live scanner/setup path | PARTIAL |
| Trend analysis | `MarketTrendEngine` (structure-first) in live path; `TrendEngine` (BOS/CHOCH) in signal pipeline | — | YES |
| Indicators | Candle patterns (11O) | No volume/ATR/momentum/RSI in live scoring | PARTIAL |
| Setup detection | Patterns → confluence → candidate (11O/11Q/11R) | Produces WATCH/NO_CANDIDATE for weak; no explicit "good vs bad" beyond thresholds | YES |
| Entry logic | `entry_reference` = trigger close (11R) | No limit-order/level-trigger execution (out of scope) | YES |
| Stop-loss logic | `stop_reference` = structural level; invalidation == stop | — | YES |
| Target logic | Single structural target (Target 2 unsupported, `target_2_supported=False`) | No multi-target scaling | YES (single target) |
| Risk/reward | `risk_distance`, `reward_distance`, `risk_reward_ratio` (11R), R:R gates (11S/11T) | — | YES |
| TradePlan (Phase 4) | Account risk, position sizing, planned risk/reward, floor rounding | No execution | YES |
| MarketScanResult (11U) | Ranked scan across instruments/timeframes | One-shot, not continuous | YES |
| Opportunity ranking (11S/11T/11U) | Decision score, eligibility gates, deterministic market-level ranking | Presentational scan ordering is a sort, not a new score | YES |
| Signal generation | `SignalEngine` exists (Sprint 11A) | **Not connected to the live scanner** — only used by `HistoricalEvaluationPipeline` | PARTIAL/NOT CONNECTED |
| Multi-timeframe analysis | HTF context (< T) + LTF setup (≤ T) + MTF alignment | — | YES |
| Scanner | `MarketScanner`, `scan_watchlist`, `/scan`, `/api/scan` | Single-cycle; no continuous loop | YES (partial) |
| Watchlist/universe | `Watchlist`, `DEFAULT_WATCHLIST` (51), `ResearchUniverse` | NIFTY 50/SENSEX, not Top 200 | PARTIAL |
| Persistence | Historical store, paper-trade store, evidence/research stores, execution stores | No scan-state persistence | YES |
| Paper trading (Phase 5 + ops) | Full lifecycle, journal, performance, operations cycle, CLI, Windows scheduler | No real orders | YES (validation) |
| Operational trade intent (14) | Deterministic intent from TradePlan | No execution | PARTIAL (future) |
| Execution authorization (15) | Auth model/engine/store | No execution | PARTIAL (future) |
| ExecutionCommand (16) | Command model/store | No execution | PARTIAL (future) |
| Broker adapter (17) | Contract, reference adapter, Upstox adapter (mock), sandbox read-only | No real order path | PARTIAL (future) |
| Execution gate (17.9) | 20-condition fail-closed gate (DISABLED, not wired) | — | PARTIAL (future) |
| Auditability | Deterministic ids, atomic persistence, redaction, audit surfaces | — | YES |
| Reporting | Formatters for every layer; CLI reports | No alert push | YES |
| Notifications / alerts | **NONE** | — | MISSING |
| CLI / operational interface | `run_paper_trading_cycle.py`, `run_live_paper_validation.py`, `ingest_*`, `audit_*`, `prepare_corpus_data.py` | — | YES |
| Scheduling / continuous execution | Windows Task Scheduler launcher for the paper-trading cycle (every 15 min, 09:15→16:00 IST) | **No in-engine scan scheduler; no continuous scan loop** | PARTIAL |
| Market-session awareness | Scheduler window 09:15–16:00; keep-awake | **No pre/post-market analysis; no session logic in the engine** | PARTIAL |

---

## 7. What Is Partially Built

| Functionality | Evidence | Gap |
|---------------|----------|-----|
| Scanner exists but does not continuously scan | `MarketScanner.scan`, `scan_watchlist`, `run_once` are all single-cycle; only the Windows scheduler re-invokes the paper-trading CLI every 15 min | No continuous scan loop, no incremental scan, no candle-completion trigger |
| Scanner only handles a smaller universe | 51 instruments (NIFTY 50 ∪ SENSEX + NIFTY); fixtures serve 5 | Not the NIFTY Top 200 |
| Setup detection produces too many weak setups | 11Q classification NO_SETUP/WATCH/POTENTIAL_SETUP; 11R candidate gate requires confluence ≥ 3, no conflict, directional; 11T eligibility gates | No empirical calibration; no volume/momentum to separate high-quality |
| TradePlan not ranked effectively | TradePlan is a per-instrument artifact; ranking lives in 11S/11T/11U | Plan itself has no rank |
| Multi-timeframe analysis does not combine timeframes coherently | Only 2 timeframes (context + setup) with a fixed context-fallback map; no 3+ timeframe hierarchy | Fixed single HTF, not a coherent multi-TF stack |
| Market structure not connected to setup qualification | Structure feeds bias/trend/range + recent_structure; BOS/CHOCH NOT connected to live setup path | BOS/CHOCH unused in live scan |
| Signals not converted into actionable opportunities | `SignalEngine` not connected to the scanner; opportunities derive from 11R/11S/11T instead | Signal layer is legacy/disconnected |
| Opportunities not ranked (partially) | 11U market-level ranking exists and is deterministic | Presentational scanner ordering is a separate sort |
| Ranking does not distinguish high-quality from marginal | 11S decision score [0,100] + 11T gates; but no volume/momentum/relative-strength factors | Quality separation is limited to structure/geometry/R:R |
| Historical data exists but live/intraday coverage incomplete | Historical corpus partial (corpus_plan.txt: 430 chunk requests, 360 missing); live Yahoo intraday bounded to ~58 days | Intraday history limited |
| Live data exists but no continuous scanning loop | Yahoo provider works; no loop | Missing |
| Scan results exist but no user-facing notification | Dashboard/CLI pull-based | Missing |
| Alerts exist but not deduplicated | No alert subsystem at all | Missing |
| Setup detection repeated scans generate duplicate alerts | No alert dedup (paper-trade duplicate prevention exists, but that is trade creation, not alerting) | Missing |
| Persistence exists but setup lifecycle/state tracking incomplete | Paper-trade lifecycle persisted; **no setup/opportunity lifecycle state** | Missing |
| Market session logic exists but no continuous pre/post-market | Scheduler window only | Missing |

---

## 8. What Is Missing

Organized per the required categories:

### A. DATA
- No volume/ATR/volatility/momentum/relative-strength features in the live scoring path (`src/engine/intelligence/volume.py` is an empty 0-byte file).
- No streaming/tick data (polling only).
- Intraday historical coverage is incomplete (corpus plan shows 360/430 chunks missing).

### B. UNIVERSE
- **NIFTY Top 200 universe is entirely absent** (only NIFTY 50 ∪ SENSEX = 50 stocks + NIFTY benchmark).
- No canonical/auto-updated Top 200 constituent source; no reconstitution tracking.

### C. MARKET SCANNING
- No continuous scanning loop / scheduler inside the engine.
- No incremental scan (only full re-scan).
- No candle-completion-driven trigger.
- No scan-state persistence/recovery after restart.

### D. MULTI-TIMEFRAME ANALYSIS
- No 3+ timeframe hierarchy; only a fixed 2-timeframe (context+setup) pair.
- No per-timeframe independent structure roll-up into a coherent stack.

### E. MARKET STRUCTURE
- BOS/CHOCH not connected to the live setup-qualification path.
- No structural-strength/level-quality factor in live scoring (`StructuralLevelsEngine`/`StructuralStrengthEngine` exist but are not wired into the scanner).

### F. SETUP INTELLIGENCE
- No explicit "good vs bad setup" calibration; no volume/momentum/liquidity/relative-strength factor.
- No regime classification in the live path (`MarketRegimeEngine` is research-only).

### G. TRADE PLAN QUALITY
- TradePlan is complete for risk/position sizing but carries no rank/quality label and no alerting hook.

### H. OPPORTUNITY RANKING
- Ranking is built and deterministic, but there is no top-N alerting/emission of "Stock A — HIGH QUALITY" as a user-facing ranked alert feed; the scanner table is the only surface.

### I. SETUP LIFECYCLE
- **No setup lifecycle states** (DEVELOPING/QUALIFIED/ACTIONABLE/ALERTED/INVALIDATED/EXPIRED/REPLACED). The only lifecycle tracking is the paper-trade lifecycle (WAITING_FOR_ENTRY/OPEN/CLOSED/...), which is a different concern. A valid setup re-alerts every scan cycle because there is no alert state.

### J. ALERTING / NOTIFICATION
- **No alert/notification subsystem at all**: no alert model, no dedup, no cooldown, no push (webhook/telegram/email/console alert on a new qualified setup). Zero hits for notification/alert/webhook/telegram/push in source and docs.

### K. CONTINUOUS OPERATION
- No in-engine continuous operation; only external Windows Task Scheduler for paper trading. No pre-market preparation, no market-hours scanning loop, no post-market analysis.

### L. OBSERVABILITY
- Deterministic audit surfaces exist for execution/paper-trading; **no scan-run observability** (no scan-run log/history, no alert history, no per-cycle scan state).

### M. TESTING / VALIDATION
- Extensive (6306 tests) but no tests for continuous scanning, alert dedup, setup lifecycle, or Top-200 universe (because those capabilities do not exist).

### N. PERFORMANCE / SCALE
- Sequential scanning only (documented in 11.6 audit); 200 instruments × polling would be slow; no concurrency/caching of scan results.

For each missing capability: required because the product goal demands continuous
scanning + alerts + Top 200; the natural owner is the existing
`DashboardAnalysisService`/`MarketScanner` + a new alert/lifecycle module;
frozen boundaries are Checkpoints 10–17 (execution) and the analytical engines
(must remain descriptive); new market-data capabilities needed only for
volume/momentum (Yahoo provides volume already — it is simply unused); no
strategy change required (additive factors/lifecycle); each can be implemented
independently.

---

## 9. Market Scanning Requirement Audit (continuous)

| Requirement | Status | Evidence |
|-------------|--------|----------|
| Scan scheduling | MISSING (in-engine); PARTIAL (external Windows Task Scheduler for paper-trading CLI) | `scripts/windows/Install-PaperTradingTask.ps1` |
| Scan frequency | External: every 15 min; no in-engine frequency | same |
| Incremental vs full scan | FULL only; no incremental | `MarketScanner.scan` re-analyzes all candles |
| Candle completion awareness | BUILT (completed-candle boundary) | `split_completed_candles` |
| Intraday timeframe updates | BUILT (fetch returns latest completed candle) | `YahooDataProvider.fetch` |
| Stale data detection | BUILT (FreshnessState) | `classify_freshness` |
| Missing data handling | BUILT (honest INCOMPLETE/UNAVAILABLE) | `MarketScanner` |
| Per-symbol failure isolation | BUILT | `_scan_one` |
| Scan state | MISSING (no persisted scan state) | — |
| Duplicate detection | PARTIAL (paper-trade duplicate prevention only) | `PaperTradingOperations` |
| Setup lifecycle | MISSING | — |
| Re-evaluation of developing setups | MISSING (each scan is independent) | — |
| Setup invalidation | MISSING (no lifecycle) | — |
| Setup confirmation | MISSING (no lifecycle) | — |
| Setup expiry | MISSING (no lifecycle) | — |
| Alert deduplication | MISSING | — |
| Alert cooldown | MISSING | — |
| Ranking refresh | PARTIAL (recomputed each scan; no incremental) | — |
| Market-session awareness | PARTIAL (scheduler window only) | — |
| Pre-market preparation | MISSING | — |
| Market-hours scanning | PARTIAL (external scheduler) | — |
| Post-market analysis | MISSING | — |
| Persistence/recovery after restart | PARTIAL (paper trades survive; scan state does not) | — |

---

## 10. Multi-Timeframe Intelligence Audit

- **Timeframes available:** 1m/3m/5m/15m/30m/1h/4h/1D (dashboard `SUPPORTED_TIMEFRAMES`); Yahoo native 1m/2m/5m/15m/30m/60m/1h/90m/1D; fixture 15M+1D only.
- **Actually used:** context + setup pair (default 1D context / 15m setup); fixed context-fallback map (`_CONTEXT_FALLBACK`).
- **Merely supported by infrastructure:** 1m/3m/5m/30m/1h/4h are selectable but the default scanner config uses 1D/15M.
- **How HTF context influences LTF setup:** `MTFAlignmentEngine.align(higher_context, lower_direction)` → ALIGNED/CONFLICTING/NEUTRAL/UNKNOWN; alignment is a market-level ranking key and is surfaced to the user.
- **Explicit alignment:** YES (`MTFAlignment` enum).
- **Conflicting timeframes:** handled explicitly (CONFLICTING alignment; never silently bullish/bearish).
- **Market structure per timeframe:** YES — `MarketContextEngine.analyze_at` runs independently on the context slice and the setup slice.
- **Setup qualification considers HTF context:** YES — alignment is part of the scan result and ranking; the candidate/decision itself is built on the LTF slice.
- **Entries based on LTF confirmation:** YES — entry = LTF setup-candle close (11R).
- **Distinguish DEVELOPING/CONFIRMED/INVALIDATED/EXPIRED:** **NO** — no such lifecycle states exist; only per-scan classifications (WATCH/QUALIFIED/PREFERRED etc.).

---

## 11. "Good Trade" Definition Audit

Factors actually present in the live scoring path:

| Factor | Present? | Strength |
|--------|----------|----------|
| Market structure | YES (recent_structure HH/HL/LH/LL, bias, structure_intact) | STRONG |
| Trend | YES (MarketTrendState) | STRONG |
| Swing structure | YES (confirmed swings) | STRONG |
| BOS / structural confirmation | NO (BOS/CHOCH not in live path) | MISSING |
| Entry quality | PARTIAL (entry = trigger close; no limit-level quality) | PARTIAL |
| Stop placement | PARTIAL (nearest structural level) | PARTIAL |
| Target quality | PARTIAL (single structural target) | PARTIAL |
| Risk/reward | YES (ratio + gates) | STRONG |
| Higher-timeframe alignment | YES (MTF alignment) | STRONG |
| Lower-timeframe confirmation | YES (LTF candle patterns + close) | STRONG |
| Volatility | NO | MISSING |
| Liquidity | NO | MISSING |
| Momentum | NO | MISSING |
| Proximity to important levels | YES (price_location NEAR_SUPPORT/RESISTANCE) | STRONG |
| Invalidation clarity | YES (stop == invalidation level) | STRONG |
| Setup freshness | NO | MISSING |
| Setup maturity | NO | MISSING |
| Market regime | NO (regime engine research-only) | MISSING |
| Relative strength/weakness | NO | MISSING |
| Volume | NO (volume unused; `volume.py` empty) | MISSING |
| Conflicting signals | YES (conflicting_count, conflict caps) | STRONG |
| Risk conditions | YES (TradePlan risk/position sizing) | STRONG |

**Rejection mechanism:** YES — the engine has a real rejection chain: 11Q caps
(WATCH/NO_SETUP), 11R promotion gate (confluence ≥ 3, no conflict, directional,
range-blocked), 11S caps (conflict caps PREFERRED→QUALIFIED, geometry cap), 11T
eligibility gates (candidate status, decision class, min score 40, optional
geometry/R:R/conflict), and `MarketScanner` eligibility (`require_opportunity_for_eligibility`).
A mediocre setup is NOT promoted to an opportunity. However, the separation
quality is limited by the absence of volume/momentum/regime factors.

---

## 12. Opportunity Ranking Audit

- **Ranking mechanism:** `TradeOpportunityEngine._ranking_key` (per-context, 11T) and `MarketScanner._ranking_key` (market-level, 11U).
- **Scoring:** Sprint 11S `DecisionScore` [0,100] = transparent sum of 7 weighted components (trend/structure/candle/location/geometry/risk_reward/no_conflict).
- **Confidence:** no probability; score is descriptive evidence strength.
- **Setup quality:** confluence score (count of aligned sources, 0–5).
- **Tie handling:** fully deterministic multi-key tie-break ending in instrument name ascending; direction is NOT a ranking key.
- **Deterministic ranking:** YES (verified by tests).
- **Explanation of ranking:** rationale strings on decisions/opportunities/scans.
- **Stale opportunities:** not tracked (no lifecycle).
- **Duplicate opportunities:** no alert-level dedup (paper-trade dup prevention exists).
- **Conflicting opportunities:** conflict handled (caps + disqualification option).
- **Max surfaced:** `max_surfaced_opportunities` cap (best counts as 1).
- **Thresholding:** 11S preferred 80 / qualified 60 / watch 40; 11T min score 40; 11R confluence ≥ 3.
- **Rejection criteria:** full gate chain (see §11).

**Ranking status: IMPLEMENTED** — it can produce "Stock A — HIGH QUALITY … Stock D — WATCH …" via the scan ranking; what is missing is the *continuous* ranked alert feed, not the ranking itself.

---

## 13. Trade Setup Output Audit

Desired concept vs current:

| Desired | Current |
|---------|---------|
| SYMBOL | YES (`view.instrument`) |
| DIRECTION | YES (`geometry.direction`) |
| SETUP TYPE | YES (`setup_type`) |
| CURRENT STATUS | PARTIAL (actionability/decision/opportunity status; no lifecycle) |
| ENTRY | YES (`geometry.entry`) |
| STOP / INVALIDATION | YES (`geometry.stop` == `invalidation_level`) |
| TARGET 1 | YES (`geometry.target_1`) |
| TARGET 2 | NO (`None`, `target_2_supported=False` — intentionally unsupported) |
| RISK/REWARD | YES (`risk_reward_ratio`) |
| TIMEFRAME | YES (setup/context labels) |
| HIGHER-TIMEFRAME CONTEXT | YES (`market_overview.htf_trend`, `mtf_alignment`) |
| MARKET STRUCTURE | YES (`recent_structure`, `ltf_trend`) |
| WHY THIS IS A GOOD SETUP | PARTIAL (rationale + evidence; no volume/momentum reasoning) |
| WHAT WOULD INVALIDATE IT | YES (invalidation == stop) |
| QUALITY / SCORE | YES (decision_score, confluence) |
| RANK | YES (scan rank) |
| TIMESTAMP | YES (evaluation_timestamp) |
| DATA FRESHNESS | YES (DataSourceView) |

The `_trade_review.html` include renders all of the above (verified).

---

## 14. Live Market Detection Audit

- **Current market data provider capabilities:** Yahoo (polling, near-live, completed-candle boundary); fixture (deterministic).
- **Intraday data freshness:** FreshnessState CURRENT/STALE/UNAVAILABLE; staleness config.
- **Polling/streaming:** polling only; no streaming/WebSocket.
- **Candle updates:** each fetch re-derives the latest completed candle; forming candle excluded.
- **Latency:** request-scoped; no latency guarantees.
- **Scan scheduling:** external only (Windows scheduler for paper trading).
- **Market session awareness:** scheduler window only; no engine session logic.
- **Data availability:** per-instrument graceful; missing → INCOMPLETE/UNAVAILABLE.
- **Symbol coverage:** 51 (Yahoo) / 5 (fixture).
- **Failure recovery:** per-instrument isolation + provider status; no scan-state recovery.

**Distinction:**
- HISTORICAL RESEARCH CAPABILITY: **BUILT** (Phase 6A–6D, corpus, evidence).
- PAPER/SIMULATION CAPABILITY: **BUILT** (Phase 5 + operations).
- LIVE MARKET DATA CAPABILITY: **BUILT (partial)** — polling near-live Yahoo works; no streaming, no continuous loop.
- LIVE EXECUTION CAPABILITY: **NOT REQUIRED** (out of scope) and correctly absent.

The required chain `LIVE MARKET DATA → ANALYSIS → SETUP DETECTION → USER ALERT`
is implemented for the first three links; **USER ALERT is missing**.

---

## 15. Alerting / Notification Audit

- **CLI output:** `run_paper_trading_cycle.py` prints a per-cycle report (paper-trading focus, not a setup alert).
- **Console reporting:** dashboard HTML/JSON (pull-based).
- **Logs:** keep-awake/paper-trading logs only; no alert log.
- **Files:** no alert files.
- **Notification systems / alert modules / APIs / messaging / event systems:** **NONE** (verified: zero hits for notification/alert/webhook/telegram/email/push in source and docs).

**Classified:** MISSING. The desired chain
`SCAN → DETECT NEW QUALIFIED SETUP → RANK → EMIT ALERT → USER REVIEWS → USER MANUALLY EXECUTES`
is missing the EMIT ALERT step. Broker execution is correctly not part of the chain.

---

## 16. Setup Lifecycle Audit

- **Desired states:** DEVELOPING/QUALIFIED/ACTIONABLE/ALERTED/INVALIDATED/EXPIRED/REPLACED.
- **Current:** **NO setup lifecycle states exist.** The only lifecycle is the paper-trade lifecycle (`PaperTradeStatus`: WAITING_FOR_ENTRY/OPEN/CLOSED/INVALIDATED/CANCELLED) and the submission lifecycle (execution), both different concerns.
- **Duplicate alert suppression:** **NONE** for setups. A valid setup would re-appear identically every scan cycle (the paper-trade duplicate-prevention idempotency is by deterministic trade id, not an alert dedup).

---

## 17. Broker Execution — Explicitly Deprioritized

- Real order submission, broker credentials, broker SDK, broker execution, live order management, automatic trading, execution-gate activation, autonomous trading, paper-to-live transition, broker reconciliation for execution purposes: **all OUT OF SCOPE** for the current product direction.
- The Checkpoint 17 broker architecture (contract, reference adapter, Upstox adapter mock, sandbox read-only) remains **frozen** as a future optional boundary. No implementation effort was spent on it in this checkpoint.
- Verified: execution gate is DISABLED and not wired; no path to accidentally submit orders.

---

## 18. Built Capabilities (summary)

Market structure, swing detection, structure analysis, trend, range detection,
support/resistance context, MTF alignment, candle patterns, setup confluence,
trade candidate (entry/stop/target/R:R), trade decision (score + classification),
trade opportunity (eligibility + ranking), market scanner (multi-instrument
ranked scan), dashboard (trade review / scanner / workstation), trade plan
(risk/position), paper trading (lifecycle + journal + performance + operations),
live/near-live Yahoo data with completed-candle boundary, historical data +
corpus + setup research + evidence, persistence (many stores), observability
(deterministic ids, audit surfaces), CLI/operator interfaces, extensive tests.

## 19. Partial Capabilities

Continuous scanning (external scheduler only), universe (51 not 200), live
intraday coverage (bounded), multi-timeframe (2-TF only), BOS/CHOCH not
connected to live setup path, volume/momentum/regime factors absent from live
scoring, signal engine not connected to scanner, market-session awareness
(scheduler window only), ranking (built but no alert feed), observability (no
scan-run history).

## 20. Missing Capabilities

NIFTY Top 200 universe, canonical Top-200 source, continuous scan loop /
scheduler, incremental scan, candle-completion trigger, scan-state persistence,
setup lifecycle (developing/qualified/actionable/alerted/invalidated/expired/
replaced), alert dedup + cooldown, user alerting/notification (any channel),
pre/post-market analysis, 3+ timeframe hierarchy, volume/ATR/momentum/relative-
strength factors in live scoring, scan-run observability, scale (concurrency/
caching).

---

## 21. Product-Gap Matrix

| Capability | Status | Existing Component | Evidence | Gap | Importance | Recommended Future Checkpoint |
|------------|--------|--------------------|----------|-----|------------|-------------------------------|
| NIFTY Top 200 universe | MISSING | `engine/config/universe.py` (NIFTY 50 ∪ SENSEX) | universe.py | Top 200 absent | HIGH | 19.1 Universe correctness |
| Data ingestion | BUILT | `HistoricalMarketDataService`, providers | historical_service.py | — | — | — |
| Intraday data | PARTIAL | `YahooDataProvider` (polling, bounded) | data_provider.py:999 | No streaming; bounded history | HIGH | 19.2 Intraday coverage |
| Historical data | BUILT | Phase 6A–6D | historical_*.py | corpus partial | — | — |
| Market structure | BUILT | Swing/MarketStructure/StructureAnalysis | market_context_engine.py | — | — | — |
| BOS | PARTIAL | `BOSEngine`/`CHOCHEngine` | bos.py, choch.py | Not in live path | MEDIUM | 19.4 MTF/structure |
| Multi-timeframe analysis | PARTIAL | `MarketScanner` + `MTFAlignmentEngine` | market_scanner.py | 2-TF only | MEDIUM | 19.4 |
| Setup detection | BUILT | 11O/11Q/11R | setup_confluence.py, trade_candidates.py | — | — | — |
| Entry logic | BUILT | 11R entry_reference | trade_candidates.py | — | — | — |
| Stop logic | BUILT | 11R stop_reference | trade_candidates.py | — | — | — |
| Target logic | BUILT (single) | 11R target_reference | trade_candidates.py | Target 2 unsupported | — | — |
| R:R | BUILT | 11R ratio + 11S/11T gates | trade_candidates.py | — | — | — |
| Setup quality | PARTIAL | 11S score + 11T gates | trade_decision.py | No volume/momentum/regime | HIGH | 19.5 Setup quality |
| Opportunity ranking | BUILT | 11S/11T/11U | market_scanner.py | — | — | — |
| Market scan | BUILT | `MarketScanner`/`scan_watchlist` | market_scanner.py | — | — | — |
| Continuous scanning | MISSING | (external scheduler only) | Install-PaperTradingTask.ps1 | No in-engine loop | HIGH | 19.3 Continuous scanning |
| Setup lifecycle | MISSING | (paper-trade lifecycle only) | paper_trade.py | No setup states | HIGH | 19.6 Setup lifecycle |
| Alert deduplication | MISSING | — | — | No alert subsystem | HIGH | 19.7 Alerts |
| Live market detection | PARTIAL | `YahooDataProvider` | data_provider.py | Polling only | MEDIUM | 19.3 |
| User alerts | MISSING | — | — | No notification | HIGH | 19.7 |
| Persistence | BUILT | many stores | persistence/*.py | — | — | — |
| Observability | PARTIAL | audit surfaces | — | No scan-run history | MEDIUM | 19.8 |
| Testing | BUILT | 6306 tests | tests/ | — | — | — |
| Broker execution | NOT REQUIRED / DEFERRED | Checkpoints 13–18 (frozen) | — | Out of scope | — | DEFERRED |

---

## 22. Critical Question #1

**"If I run the current engine today with a universe of stocks and live intraday
market data, will it reliably scan the market and tell me which NIFTY Top 200
stocks currently have high-quality intraday trade setups?"**

**Answer: PARTIALLY.**

Why:
- It **will** scan a configured universe of stocks against live/near-live Yahoo
  intraday data, produce a deterministic ranked scan, identify setups with
  entry/stop/target/R:R, classify decisions, rank opportunities, and surface them
  in the dashboard/CLI — reliably and point-in-time-safely (6306 tests).
- It will **NOT** scan the **NIFTY Top 200** (the universe is NIFTY 50 ∪ SENSEX
  = 50 stocks + NIFTY).
- It will **NOT** scan **continuously** (each run is a single explicit cycle;
  only an external Windows scheduler re-invokes the paper-trading CLI every 15
  minutes).
- It will **NOT tell the user when a good setup appears** (no alert/notification
  subsystem; the user must pull the dashboard or read the CLI report).
- It will **NOT** track setup lifecycle, so the same valid setup re-appears every
  cycle with no dedup/cooldown.

---

## 23. Critical Question #2

**"How far is the current engine from the actual product I want?"**

Evidence-based assessment:

- **ALREADY THERE:** market structure; multi-timeframe context (2-TF + MTF
  alignment); setup detection; entry/stop/target/R:R; decision classification +
  score; opportunity eligibility + ranking; multi-instrument scan; trade plan;
  paper trading; live/near-live data with completed-candle boundary; historical
  data + evidence; persistence; observability; extensive tests; frozen,
  separated broker-execution boundary (correctly out of scope).
- **NEEDS WORK:** universe (51 → 200); continuous scanning loop + scheduler +
  scan-state; intraday coverage (bounded, polling-only); market-session
  awareness (pre/market/post); BOS/CHOCH + structural-strength wiring into the
  live setup path; volume/ATR/momentum/regime factors for quality separation;
  scan-run observability; scale (sequential → cached/concurrent).
- **MISSING:** NIFTY Top 200 universe + canonical source; setup lifecycle
  (developing/qualified/actionable/alerted/invalidated/expired/replaced);
  alert dedup + cooldown; user alerting/notification (any channel); 3+ timeframe
  hierarchy.

No arbitrary percentage is assigned; the architecture is directionally sound and
the analytical core is largely built, but the product-defining layers
(universe, continuous scanning, lifecycle, alerts) are absent.

---

## 24. Critical Question #3

**Should the architecture continue evolving toward**
`MARKET DATA → MARKET SCANNER → MARKET STRUCTURE → MULTI-TIMEFRAME CONTEXT →
SETUP INTELLIGENCE → TRADE PLAN → OPPORTUNITY RANKING → USER ALERT` with
`USER → MANUAL EXECUTION` and broker execution outside the product boundary?

**Answer: YES — confirmed as the recommended product architecture.**

The existing analytical chain already implements the first seven stages
(through OPPORTUNITY RANKING). The missing product stages are the continuous
scanning loop, the setup lifecycle, and the USER ALERT. Broker execution
(Checkpoints 13–18) is explicitly outside the current product boundary and
should remain frozen/deferred.

---

## 25. Recommended Future Checkpoint Roadmap

Prioritized per the product goal. **No implementation was performed.** Each
checkpoint must preserve frozen Checkpoints 10–17 and keep the analytical
engines descriptive.

1. **Checkpoint 19.1 — NIFTY Top 200 Universe Correctness**
   - Objective: replace/extend the universe definition to the NIFTY Top 200 with
     a canonical, versioned, auditable constituent source; keep NIFTY benchmark
     separate; provider symbol maps for all 200.
   - Why: the product universe is the Top 200; current is NIFTY 50 ∪ SENSEX.
   - Dependencies: `engine/config/universe.py`, `dashboard/universe.py`,
     `dashboard/watchlist.py`, provider symbol maps.
   - Expected files: universe module + a Top-200 manifest + tests.
   - Production code: YES (config + provider mapping).
   - Tests: universe counts, dedup, symbol resolution, provider coverage.
   - Frozen: Checkpoints 10–17; analytical engines.
   - Acceptance: 200 canonical instruments, no duplicates, all resolve to Yahoo/
     Upstox symbols, benchmark separate.
   - Proves: universe correctness. Does NOT prove: scanning quality.

2. **Checkpoint 19.2 — Reliable Intraday Market-Data Coverage**
   - Objective: reliable, complete intraday coverage for the universe (polling
     loop with backoff, per-symbol retry, coverage accounting, gap detection).
   - Why: the product needs every Top-200 stock's intraday data during market
     hours.
   - Dependencies: `YahooDataProvider`, historical ingestion, availability
     service.
   - Expected files: provider coverage/health module + tests.
   - Production code: YES.
   - Tests: coverage matrix, failure isolation, retry, freshness.
   - Frozen: Checkpoints 10–17.
   - Proves: data reliability. Does NOT prove: setup quality.

3. **Checkpoint 19.3 — Continuous Market Scanning**
   - Objective: an in-engine continuous scan loop (configurable interval,
     candle-completion trigger, incremental vs full, scan-state persistence,
     restart recovery).
   - Why: "continuously scans" is the core product verb.
   - Dependencies: `MarketScanner`, `DashboardAnalysisService`, stores.
   - Expected files: scan scheduler/loop + scan-state store + tests.
   - Production code: YES.
   - Tests: scheduling, candle-completion, incremental, restart recovery,
     determinism, no-look-ahead.
   - Frozen: Checkpoints 10–17.
   - Proves: continuous scanning. Does NOT prove: alerting.

4. **Checkpoint 19.4 — Multi-Timeframe Market Analysis**
   - Objective: coherent 3+ timeframe hierarchy (e.g. 1D/1h/15m) with per-TF
     structure and explicit alignment; wire BOS/CHOCH + structural strength into
     the live setup path.
   - Why: the product asks for "relevant timeframes and market levels".
   - Dependencies: `MarketContextEngine`, `MTFAlignmentEngine`, BOS/CHOCH/
     StructuralLevels engines (currently unused in the live path).
   - Expected files: multi-TF stack module + tests.
   - Production code: YES.
   - Tests: per-TF independence, alignment, BOS/CHOCH wiring, no-look-ahead.
   - Frozen: Checkpoints 10–17.
   - Proves: multi-timeframe coherence. Does NOT prove: alerting.

5. **Checkpoint 19.5 — Setup-Quality Intelligence**
   - Objective: add descriptive quality factors (volume, ATR/volatility,
     momentum, relative strength, regime) as additive evidence in the existing
     scoring path, and calibrate the rejection thresholds.
   - Why: "high-quality" and "rejects poor-quality setups" need more than
     structure/geometry/R:R.
   - Dependencies: 11S scoring, 11T gates, `volume.py` (currently empty),
     `MarketRegimeEngine` (research-only).
   - Expected files: factor engines + config + tests.
   - Production code: YES (additive).
   - Tests: factor contribution, threshold behavior, no regression.
   - Frozen: Checkpoints 10–17; existing decision semantics (additive only).
   - Proves: quality separation. Does NOT prove: predictive validity.

6. **Checkpoint 19.6 — Setup Lifecycle Management**
   - Objective: setup lifecycle states (DEVELOPING/QUALIFIED/ACTIONABLE/
     ALERTED/INVALIDATED/EXPIRED/REPLACED) with persistence and re-evaluation.
   - Why: a setup should not re-alert every cycle; lifecycle enables dedup,
     confirmation, invalidation, expiry.
   - Dependencies: scan-state store, `MarketScanner`.
   - Expected files: lifecycle model + engine + store + tests.
   - Production code: YES.
   - Tests: state transitions, invalidation, expiry, restart, dedup.
   - Frozen: Checkpoints 10–17.
   - Proves: lifecycle tracking. Does NOT prove: alert delivery.

7. **Checkpoint 19.7 — User-Facing Alerts**
   - Objective: alert emission on a newly qualified setup with dedup + cooldown;
     a user-facing alert feed (console/CLI + optional webhook/notification).
   - Why: "tells the user when a good setup appears" is the product's final verb.
   - Dependencies: 19.3 (continuous scan), 19.6 (lifecycle).
   - Expected files: alert model + emitter + dedup/cooldown + tests.
   - Production code: YES.
   - Tests: new-setup detection, dedup, cooldown, no duplicate alerts, manual
     execution handoff (no broker).
   - Frozen: Checkpoints 10–17; broker execution remains out of scope.
   - Proves: alerting. Does NOT prove: execution.

8. **Checkpoint 19.8 — Reliability / Recovery / Observability**
   - Objective: scan-run history, alert history, health, restart recovery,
     scale (caching/concurrency).
   - Why: operational reliability for a continuous product.
   - Dependencies: stores, scheduler.
   - Expected files: observability module + tests.
   - Production code: YES.
   - Tests: recovery, history, determinism.
   - Frozen: Checkpoints 10–17.
   - Proves: operational reliability. Does NOT prove: trading success.

9. **Checkpoint 19.9 — Validation / Backtesting / Forward-Testing**
   - Objective: validate the setup-quality thresholds against historical +
     paper-trading outcomes (reusing Phase 6D evidence + Phase 5 paper trading).
   - Why: "high-quality" must be evidence-backed, not asserted.
   - Dependencies: Phase 6D/6E evidence, Phase 5 paper trading, 19.5 factors.
   - Expected files: validation harness + tests.
   - Production code: YES (validation only).
   - Tests: threshold validation, forward-testing, no-look-ahead.
   - Frozen: Checkpoints 10–17.
   - Proves: evidence-backed quality. Does NOT prove: future performance.

Broker execution: explicitly DEFERRED (Checkpoints 13–18 remain frozen).

---

## 26. Frozen Architecture Confirmation

- Checkpoints 10–17 (and 18.1–18.4) remain **frozen and unchanged**: no frozen
  file was modified, no frozen test was modified/deleted/weakened, no frozen
  checkpoint was reopened or refactored.
- ExecutionCommand, SubmissionLifecycle, SubmissionInfrastructure, BrokerAdapter
  contract, `execution_gate.py`, authorization boundaries: **untouched**.
- `git status` was clean before and after this audit (only the two intended new
  files were added: this document + the AGENTS.md append).
- No strategy contract was changed.

---

## 27. Security / Network / Credential Audit

- No broker was connected; no order submitted/modified/cancelled; no broker
  SDK introduced; no new execution network path introduced.
- The execution gate remains DISABLED and is not wired into any submission path.
- No live trading was enabled; no paper-to-live transition.
- No credentials were printed, logged, persisted, or committed. The only
  credential-typed env reads in the repository are the historical-data Upstox
  token (data provider, not execution) and the execution-token env-var name
  (never a value). This audit performed no network calls beyond the standard
  test suite (which is network-free/deterministic; the 12 skipped tests are
  opt-in real-broker/sandbox tests correctly gated).
- Historical/market-data inspection was limited to repository source + tests
  (no live data fetch required for the audit).

---

## 28. Regression Results

- Focused scanning/ops suites (test_market_scanner, test_watchlist_scanner,
  test_workstation, test_paper_trading_operations, test_run_paper_trading_cycle,
  test_live_paper_validation): **466 passed**.
- Full suite: **6306 passed, 12 skipped, 2 warnings** (2 pre-existing
  third-party deprecation warnings: StarletteDeprecationWarning + anyio
  BlockingPortal alias). The 12 skips are the opt-in real-broker/sandbox tests
  (`test_checkpoint_17_8_real_broker_opt_in.py`, `test_checkpoint_18_2_sandbox_opt_in.py`)
  gated on `CHECKPOINT_17_8_REAL_BROKER` + a genuine sandbox credential.
- **No test was modified, deleted, or weakened.** `git status` clean except the
  two intended new files.
- Checkpoints 10–17 remain frozen (verified: no tracked/frozen file diff).

---

## 29. PASS Findings

1. Analytical core (structure → context → setup → candidate → decision →
   opportunity → ranked scan) is fully built, deterministic, point-in-time-safe.
2. Complete trade geometry (entry/stop/target/R:R/invalidation) is built and
   reused verbatim end-to-end (11R → scan → view → plan → paper trade).
3. Multi-timeframe context + explicit MTF alignment is built and structurally
   look-ahead-safe (HTF < T, LTF ≤ T).
4. A real rejection chain exists (11Q caps → 11R gate → 11S caps → 11T gates →
   scanner eligibility) so mediocre setups are not promoted to opportunities.
5. Opportunity ranking is deterministic, direction-symmetric, thresholded, and
   capped.
6. Live/near-live data (Yahoo) with completed-candle boundary + freshness +
   per-symbol failure isolation is built.
7. Trade plan (risk/position) + paper trading (lifecycle/journal/performance/
   operations) are built and persisted.
8. Historical data + corpus + setup research + evidence (Phase 6A–6D) are built
   and offline.
9. Broker execution is cleanly separated and frozen; no accidental execution
   path exists.
10. Test suite is comprehensive (6306 passed) with zero regression.

## 30. CONCERN Findings

1. **Universe is NIFTY 50 ∪ SENSEX (51 instruments), not the NIFTY Top 200** —
   the defining product universe is absent. (HIGH)
2. **No continuous scanning** — only single explicit cycles + an external Windows
   scheduler for the paper-trading CLI. (HIGH)
3. **No user alerting/notification subsystem** — the product's "tells the user
   when a good setup appears" is unimplemented. (HIGH)
4. **No setup lifecycle** — no DEVELOPING/QUALIFIED/ACTIONABLE/ALERTED/
   INVALIDATED/EXPIRED/REPLACED states; no alert dedup/cooldown; a valid setup
   re-appears every cycle. (HIGH)
5. **No volume/ATR/momentum/regime/relative-strength factors in live scoring** —
   quality separation is limited to structure/geometry/R:R; `volume.py` is an
   empty 0-byte file. (MEDIUM)
6. **BOS/CHOCH and structural-strength engines are not wired into the live
   setup path** (they exist but are used only by the historical signal pipeline
   or standalone). (MEDIUM)
7. **Multi-timeframe is a fixed 2-TF pair** (context+setup), not a coherent
   3+ timeframe hierarchy. (MEDIUM)
8. **Intraday coverage is bounded** (Yahoo ~58 days for ≤30m) and polling-only;
   no streaming. (MEDIUM)
9. **No scan-run observability** (no scan history/alert history). (LOW-MEDIUM)
10. **Sequential scanning only** — scaling to 200 instruments × frequent polling
    needs caching/concurrency. (LOW-MEDIUM)

## 31. BLOCKER Findings

**NONE.** No architectural blocker prevents reaching the product goal; the
gaps are additive product layers (universe, continuous scan, lifecycle, alerts)
on top of a sound analytical core.

---

## 32. Final Verdict

**PASS WITH LIMITATIONS.**

The architecture is directionally sound and the analytical core is largely
built and regression-proven, but the product-defining capabilities — NIFTY Top
200 universe, continuous scanning, setup lifecycle, and user alerts — are
missing or partial. The recommended evolution is
`MARKET DATA → MARKET SCANNER → MARKET STRUCTURE → MULTI-TIMEFRAME CONTEXT →
SETUP INTELLIGENCE → TRADE PLAN → OPPORTUNITY RANKING → USER ALERT`, with
`USER → MANUAL EXECUTION` and broker execution explicitly deferred outside the
current product boundary.

**No implementation was performed. No Checkpoint 18.6 or future work was
started. Nothing was committed to git.**
