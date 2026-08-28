UPSTOCK HISTORICAL PROVIDER - UPDATED FILES (live-verification fix)
===================================================================

WHAT CHANGED IN THIS PACKAGE (2026-08 live-verification session)
----------------------------------------------------------------
NEW ROOT CAUSE FOUND AND FIXED: the Cloudflare edge in front of
`api.upstox.com` rejects urllib's DEFAULT `Python-urllib/x.y`
User-Agent with HTTP 403 / Error 1010 (`browser_signature_banned`).
That is why the live verification returned `HTTP OK / Provider EMPTY /
Records 0/0/0` even after the `data.candles` parser fix:

  - with the default urllib User-Agent  -> HTTP 403 (Cloudflare blocks it)
  - with ANY explicit User-Agent        -> HTTP 200, 550 real candles

Fix (in src/engine/data/historical_provider.py, _fetch_one):
  _fetch_one now ALWAYS sends
      User-Agent: python-urllib/upstox-historical-provider
  (new exported constant `UPSTOX_USER_AGENT`). The token still goes ONLY
  in the Authorization: Bearer header.

ALSO FIXED this session: scripts/verify_upstox_live.py crashed after a
successful ingestion because it called the non-existent
`store.load_historical()`; it now uses the correct `store.load_candles()`
so the "Reload check" step completes with PASS.

This is PROVIDER + VERIFY-SCRIPT ONLY: no trading/decision/geometry/
trade-plan/paper-trading logic was modified.

LIVE-VERIFIED RESULTS (real Upstox API, RELIANCE 15m Dec 2022)
--------------------------------------------------------------
  HTTP status      : OK
  Provider status  : AVAILABLE
  Records Received : 550
  Records Accepted : 550
  Records Rejected : 0
  First Candle     : 2022-12-01T03:45:00+00:00  (= 09:15 IST)
  Last Candle      : 2022-12-30T09:45:00+00:00  (= 15:15 IST)
  Chronology       : PASS
  Reload check     : PASS (550 candles reloaded)
  Full test suite  : 3849 passed (was 3847)

FILES (keep this directory layout in your project)
--------------------------------------------------
  src/engine/data/historical_provider.py   <- implementation + User-Agent fix
  tests/test_upstox_historical.py          <- +2 UA regression tests (User-Agent header sent/constant)
  scripts/verify_upstox_live.py            <- LIVE verification script (load_candles fix)
  scripts/ingest_historical_data.py        <- ingestion CLI
  docs/upstox_historical_provider          <- provider doc (User-Agent requirement added)
  AGENTS.md                                <- project-agent memory (fix recorded)

RUN ON WINDOWS
--------------
  pip install -r requirements.txt    (or at least: pytest)
  python scripts/test_upstox_historical.py                 # deterministic demo, no network
  set UPSTOX_ANALYTICS_TOKEN=<your token>                  # or: $env:UPSTOX_ANALYTICS_TOKEN
  python scripts/verify_upstox_live.py                     # live API check
  python scripts/ingest_historical_data.py ^
      --instrument RELIANCE --timeframe 15m ^
      --start 2022-12-01 --end 2023-01-01 --provider upstox-historical

Expected live result now:
  Records Received: >0   Records Accepted: >0   Records Rejected: 0
  Validation: PASS   Reload check: PASS
  Persisted candles at data/historical/RELIANCE/15m/candles.json

NOTE: the token is used ONLY in the Authorization: Bearer header and is
never printed/logged/committed.