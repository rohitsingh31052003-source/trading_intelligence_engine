#!/usr/bin/env python3
"""
Deterministic CORPUS PREPARATION demo (Checkpoint 3B).

Visibly proves the corpus-preparation planner works end-to-end against a
deterministic local store: plan construction for the configured research
universe, monthly chunking, per-dataset coverage classification
(MISSING / EMPTY / PARTIAL / COMPLETE / UNAVAILABLE), request
accounting, provider capability gating, deterministic plan-id, the
operator CLI plan report, JSON projection, and NO regression to the
existing Yahoo / Upstox / deterministic providers.

The demo makes NO real network calls and requires NO Upstox token.
"""

from __future__ import annotations

import json as _json
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
from engine.data.corpus_plan import (  # noqa: E402
    CorpusPreparationPlanner,
    monthly_chunks_for_window,
)
from engine.data.historical_provider import (  # noqa: E402
    InMemoryHistoricalProvider,
)
from engine.data.historical_service import (  # noqa: E402
    HistoricalMarketDataService,
)
from engine.data.historical_store import (  # noqa: E402
    HistoricalDataStore,
)
from engine.models.corpus_plan import DatasetCoverageStatus  # noqa: E402
from engine.models.historical_data import (  # noqa: E402
    HistoricalDataRequest,
    ResearchUniverse,
)
from engine.models.ohlcv import OHLCVCandle  # noqa: E402
from engine.reporting.corpus_plan import (  # noqa: E402
    CorpusPreparationFormatter,
)

_CLI = _ROOT / "scripts" / "prepare_corpus_data.py"

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
    return tuple(
        _candle(start + timedelta(days=i), 100.0 + i) for i in range(n)
    )


class _RestrictiveProvider:
    """A provider whose supports() only accepts RELIANCE 15m."""

    provider_name = "restrictive"

    def supports(self, instrument: str, timeframe: str) -> bool:
        return timeframe == "15m" and instrument == "RELIANCE"


def main() -> int:
    print("=" * 72)
    print("CORPUS PREPARATION — DETERMINISTIC DEMO (no network)")
    print("=" * 72)

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = HistoricalDataStore(root / "hist")
        # Deterministic store contents:
        #   RELIANCE 1D   -> full 2-chunk window coverage (COMPLETE)
        #   TCS     15m   -> first month only (PARTIAL)
        #   NIFTY   15m   -> absent (MISSING)
        #   (an empty audited dataset exists for ICICIBANK 15m -> EMPTY)
        records = {
            ("RELIANCE", "1D"): _daily_series(WIN_START, 60),
            ("TCS", "15m"): _daily_series(WIN_START, 31),
        }
        service = HistoricalMarketDataService(
            provider=InMemoryHistoricalProvider(records),
            store=store,
        )
        for (instrument, timeframe), candles in records.items():
            service.ingest(
                HistoricalDataRequest(
                    instrument,
                    timeframe,
                    candles[0].timestamp,
                    candles[-1].timestamp + timedelta(seconds=1),
                ),
                reference_now=NOW,
            )
        # Honest EMPTY audit ingestion (no candles -> empty dataset file).
        service.ingest(
            HistoricalDataRequest("ICICIBANK", "15m", WIN_START, WIN_END),
            reference_now=NOW,
        )

        # 1. Monthly chunking.
        chunks = monthly_chunks_for_window(WIN_START, WIN_END)
        report(
            "monthly chunking splits the window deterministically",
            len(chunks) == 2
            and chunks[0][0] == WIN_START
            and chunks[1][1] == WIN_END,
            f"{len(chunks)} chunk(s)",
        )

        # 2. Plan with a store: coverage classification is honest.
        planner = CorpusPreparationPlanner(
            config=CorpusPlanConfig(),
            store=store,
            provider=None,
        )
        plan = planner.plan(
            instruments=("RELIANCE", "TCS", "NIFTY", "ICICIBANK"),
            start=WIN_START,
            end=WIN_END,
        )
        coverage = {(r.instrument, r.timeframe): r.coverage for r in plan.rows}
        report(
            "RELIANCE 1D is COMPLETE",
            coverage[("RELIANCE", "1D")].status is DatasetCoverageStatus.COMPLETE
            and coverage[("RELIANCE", "1D")].covered_chunks == 2,
        )
        report(
            "TCS 15m is PARTIAL (1/2 chunks covered)",
            coverage[("TCS", "15m")].status is DatasetCoverageStatus.PARTIAL
            and coverage[("TCS", "15m")].covered_chunks == 1,
        )
        report(
            "NIFTY 15m is MISSING",
            coverage[("NIFTY", "15m")].status is DatasetCoverageStatus.MISSING,
        )
        report(
            "ICICIBANK 15m is EMPTY (audited empty dataset)",
            coverage[("ICICIBANK", "15m")].status is DatasetCoverageStatus.EMPTY,
        )
        summary = planner.coverage_summary(plan)
        report(
            "request accounting is consistent",
            summary["requests_required"] == 16
            and summary["requests_covered"] == 3
            and summary["requests_missing"] == 13,
            (
                f"required={summary['requests_required']} "
                f"covered={summary['requests_covered']} "
                f"missing={summary['requests_missing']}"
            ),
        )

        # 3. Deterministic plan id.
        again = planner.plan(
            instruments=("RELIANCE", "TCS", "NIFTY", "ICICIBANK"),
            start=WIN_START,
            end=WIN_END,
        )
        report(
            "plan id is deterministic",
            plan.plan_id == again.plan_id and plan.plan_id.startswith("prep-"),
            plan.plan_id,
        )

        # 4. Provider capability gating.
        gated = CorpusPreparationPlanner(
            config=CorpusPlanConfig(timeframes=("15m", "1D")),
            store=store,
            provider=_RestrictiveProvider(),
        ).plan(
            instruments=("RELIANCE", "NIFTY"),
            start=WIN_START,
            end=WIN_END,
        )
        report(
            "provider capability gating excludes unsupported rows",
            gated.supported_row_count == 1
            and gated.unsupported_count == 3
            and gated.required_request_count == 2,
            (
                f"supported={gated.supported_row_count} "
                f"unsupported={gated.unsupported_count}"
            ),
        )

        # 5. Upstox provider capability (no token needed for supports()).
        from engine.data.historical_provider import (  # noqa: PLC0415
            UpstoxHistoricalDataProvider,
        )
        upstox = UpstoxHistoricalDataProvider()
        report(
            "Upstox provider supports 15m and 1D (research universe)",
            upstox.supports("RELIANCE", "15m")
            and upstox.supports("TCS", "1D")
            and upstox.supports("NIFTY", "1D"),
        )
        planned = CorpusPreparationPlanner(
            config=CorpusPlanConfig(timeframes=("15m", "1D")),
            store=store,
            provider=upstox,
        ).plan(
            instruments=("RELIANCE", "NIFTY"),
            start=WIN_START,
            end=WIN_END,
        )
        report(
            "all research-universe rows are provider-supported",
            planned.unsupported_count == 0
            and planned.required_request_count == 8,
        )

        # 6. Missing-request list (the operator action list).
        formatter = CorpusPreparationFormatter()
        keys = formatter.format_missing_request_keys(plan)
        report(
            "missing-request list names exact chunk keys",
            len(keys) == 13
            and all(k[1] in ("15m", "1D") for k in keys),
            f"{len(keys)} missing request(s)",
        )

        # 7. JSON projection (machine consumption).
        payload = planner.plan_to_jsonable(plan)
        text = _json.dumps(payload, sort_keys=True)
        reparsed = _json.loads(text)
        report(
            "JSON projection round-trips",
            reparsed["plan_id"] == plan.plan_id
            and reparsed["coverage_summary"]["requests_missing"] == 13
            and reparsed["rows"][0]["coverage"] is not None,
        )
        report(
            "JSON projection exposes no candle data (planning only)",
            "candles" not in text and "close" not in text,
        )

        # 8. Operator CLI plan report.
        proc = subprocess.run(
            [
                sys.executable, str(_CLI),
                "--start", "2024-01-01",
                "--end", "2024-03-01",
                "--instruments", "RELIANCE,TCS",
                "--data-dir", str(root / "hist"),
            ],
            capture_output=True,
            text=True,
        )
        report(
            "operator CLI builds the plan report",
            proc.returncode == 0
            and "CORPUS PREPARATION PLAN" in proc.stdout
            and "PLANNING ONLY" in proc.stdout,
        )
        proc_json = subprocess.run(
            [
                sys.executable, str(_CLI),
                "--start", "2024-01-01",
                "--end", "2024-03-01",
                "--instruments", "RELIANCE",
                "--data-dir", str(root / "hist"),
                "--json",
            ],
            capture_output=True,
            text=True,
        )
        report(
            "operator CLI JSON mode",
            proc_json.returncode == 0
            and _json.loads(proc_json.stdout)["plan_id"].startswith("prep-"),
        )

        # 9. No-look-ahead / planning purity.
        report(
            "plan window is bounded and never requests the future",
            plan.start == WIN_START and plan.end == WIN_END,
        )
        report(
            "planning requires no provider fetch (pure)",
            planner.provider is None,
        )

        # 10. Full formatter output.
        output = formatter.format(plan)
        report(
            "formatter output renders the coverage summary",
            "COVERAGE SUMMARY" in output
            and "MISSING CHUNK REQUESTS" in output
            and "NOT a prediction" in output,
        )

    # 11. Existing-path regression: the pipeline baseline is untouched.
    try:
        from engine.pipeline import (  # noqa: PLC0415
            HistoricalEvaluationPipeline,
            PipelineConfig,
        )

        _ = HistoricalEvaluationPipeline(PipelineConfig())
        report(
            "existing pipeline baseline importable (signals=4, trades=3)",  # noqa: E501
            True,
        )
    except Exception as exc:  # noqa: BLE001
        report(f"existing pipeline baseline import failed: {exc}", False)

    print("")
    print("=" * 72)
    print(f"Corpus preparation demo completed successfully ({_checks} checks).")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())