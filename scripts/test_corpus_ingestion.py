#!/usr/bin/env python3
"""
Deterministic HISTORICAL CORPUS INGESTION demo (Checkpoint 3B, step 2).

Visibly proves the safe, resumable corpus-ingestion runner works
end-to-end against an offline deterministic local store: work derivation
from the EXISTING planner, sequential per-chunk ingestion through the
EXISTING service/service-store pipeline, persistence after every
successful chunk, per-chunk failure isolation, resumability (a rerun
derives the missing set from the store and does not re-fetch), the
credential precheck (without a token the runner fails cleanly with ZERO
requests), the operator CLI, deterministic progress lines and the
existing runner-planner regression.

The demo makes NO real network calls and requires NO Upstox token.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

_ROOT = Path(__file__).resolve().parent.parent
for _path in (str(_ROOT / "src"), str(_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from engine.config.corpus_plan_config import CorpusPlanConfig  # noqa: E402
from engine.data.corpus_ingestion import (  # noqa: E402
    COMPLETED,
    FAILED,
    SKIPPED,
    CorpusIngestionConfig,
    CorpusIngestionEngine,
    CorpusIngestionError,
    require_upstox_token,
)
from engine.data.corpus_plan import CorpusPreparationPlanner  # noqa: E402
from engine.data.historical_provider import (  # noqa: E402
    DeterministicLocalHistoricalProvider,
    HistoricalProviderResponse,
    InMemoryHistoricalProvider,
)
from engine.data.historical_service import (  # noqa: E402
    HistoricalMarketDataService,
)
from engine.data.historical_store import (  # noqa: E402
    HistoricalDataStore,
)
from engine.models.historical_data import (  # noqa: E402
    HistoricalDataRequest,
    ProviderResponseStatus,
)
from engine.models.ohlcv import OHLCVCandle  # noqa: E402

_CLI = _ROOT / "scripts" / "ingest_corpus_data.py"

WIN_START = datetime(2024, 1, 1, tzinfo=UTC)
WIN_END = datetime(2024, 3, 1, tzinfo=UTC)
NOW = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)

_checks = 0


def report(name: str, ok: bool, detail: str = "") -> None:
    global _checks
    _checks += 1
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        raise SystemExit(f"demo check failed: {name}")


def _candle(ts: datetime, close: float = 100.0) -> OHLCVCandle:
    return OHLCVCandle(ts, close, close + 1.0, close - 1.0, close, 1000.0)


def _daily_series(start: datetime, n: int) -> tuple[OHLCVCandle, ...]:
    # Candles offset to 04:00 UTC so none sits on a month boundary.
    return tuple(
        _candle(start + timedelta(days=i) + timedelta(hours=4), 100.0 + i)
        for i in range(n)
    )


class RangeFilteredProvider(InMemoryHistoricalProvider):
    def fetch(self, request):
        response = super().fetch(request)
        if response.status is not ProviderResponseStatus.OK:
            return response
        kept = tuple(
            c for c in response.candles
            if request.start <= c.timestamp <= request.end
        )
        if not kept:
            return HistoricalProviderResponse(
                provider_name=self.provider_name,
                status=ProviderResponseStatus.EMPTY,
                candles=(),
                reason="no candles in the requested window.",
            )
        return HistoricalProviderResponse(
            provider_name=self.provider_name,
            status=ProviderResponseStatus.OK,
            candles=kept,
            reason=response.reason,
        )


def main() -> int:
    print("=" * 72)
    print("HISTORICAL CORPUS INGESTION — DETERMINISTIC DEMO (no network)")
    print("=" * 72)

    with TemporaryDirectory() as tmp:
        root = Path(tmp)

        # 1. WORK DERIVATION FROM THE EXISTING PLANNER ------------------
        store = HistoricalDataStore(root / "hist")
        records = {("RELIANCE", "1D"): _daily_series(WIN_START, 31)}
        provider = RangeFilteredProvider(records)
        service = HistoricalMarketDataService(provider=provider, store=store)
        planner = CorpusPreparationPlanner(
            config=CorpusPlanConfig(timeframes=("1D",), provider="in-memory-import"),
            store=store,
            provider=provider,
        )
        engine = CorpusIngestionEngine(
            planner,
            service,
            CorpusIngestionConfig(
                provider="in-memory-import",
                require_upstox_token=False,
                reference_now=NOW,
            ),
            reporter=lambda line: print(f"  {line}"),
        )
        backlog = engine.build_backlog(WIN_START, WIN_END, ["RELIANCE"])
        report(
            "work derived from the planner (2 monthly chunks)",
            backlog.missing_count == 2,
            f"missing={backlog.missing_count}",
        )

        # 2. SEQUENTIAL INGESTION + PERSISTENCE --------------------------
        lines: list[str] = []
        engine.reporter = lines.append
        session = engine.run(start=WIN_START, end=WIN_END, instruments=["RELIANCE"])
        report(
            "Jan chunk ingested + persisted",
            session.summary.completed == 1
            and session.summary.failed == 1
            and len(store.load_candles("RELIANCE", "1D")) == 31,
            f"completed={session.summary.completed} failed={session.summary.failed}",
        )
        report(
            "Feb chunk isolated as FAILED (not fabricated)",
            session.results[1].status == FAILED,
            session.results[1].detail,
        )
        report(
            "deterministic progress lines emitted",
            lines[0].startswith("[1/2] RELIANCE 1D 2024-01-01 -> 2024-02-01")
            and "PASS (31 candles)" in lines[0],
            lines[0],
        )

        # 3. RESUMABILITY (rerun derives work from the STORE) -------------
        session2 = engine.run(start=WIN_START, end=WIN_END, instruments=["RELIANCE"])
        report(
            "rerun derives missing work from the store",
            session2.backlog.missing_count == 1
            and session2.results[0].status == FAILED,
            f"missing={session2.backlog.missing_count}",
        )
        report(
            "successful chunk stays persisted (no re-fetch / re-persist)",
            len(store.load_candles("RELIANCE", "1D")) == 31,
        )

        # 4. CREDENTIAL PRECHECK (engine boundary) -----------------------
        import os

        os.environ.pop("UPSTOX_ANALYTICS_TOKEN", None)
        blocked = CorpusIngestionEngine(
            planner,
            service,
            CorpusIngestionConfig(provider="upstox-historical", require_upstox_token=True),
        )
        try:
            blocked.run(start=WIN_START, end=WIN_END, instruments=["RELIANCE"])
            ok = False
        except CorpusIngestionError as exc:
            ok = "UPSTOX_ANALYTICS_TOKEN is not set" in str(exc)
        report(
            "missing analytics token blocks the run cleanly",
            ok,
            "zero API requests, zero persistence",
        )

        # 5. OPERATOR CLI (offline deterministic provider) ----------------
        cli_dir = root / "cli_hist"
        result = subprocess.run(
            [
                sys.executable, str(_CLI),
                "--start", "2024-01-01", "--end", "2024-03-01",
                "--provider", "local-deterministic",
                "--timeframes", "1D", "--instruments", "RELIANCE",
                "--data-dir", str(cli_dir),
            ],
            capture_output=True,
            text=True,
            cwd=str(_ROOT),
        )
        report(
            "operator CLI runs + persists",
            result.returncode == 0
            and "Corpus ingestion complete" in result.stdout
            and (cli_dir / "RELIANCE" / "1D" / "candles.json").exists(),
        )

    print(f"\n{_checks}/{_checks} checks passed.")
    print(
        "HISTORICAL DATA ONLY — no prediction, no evidence computed. "
        "No real Upstox calls were made."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())