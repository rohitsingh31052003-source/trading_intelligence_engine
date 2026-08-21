#!/usr/bin/env python3
"""
Product Phase 6C demo — Historical Research Corpus.

Visibly proves the corpus works end-to-end:

    Historical OHLCV
        -> Stored historical dataset (Phase 6B)
        -> Evaluation-time sampling (canonical candle grid)
        -> Point-in-time historical slice
        -> Context timeframe + setup timeframe
        -> Historical market state
        -> Research-ready corpus

Every check prints an explicit PASS/FAIL; the demo exits non-zero on
any failure. The corpus is RESEARCH PREPARATION ONLY — no prediction,
no strategy, no decision, no evidence computed.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

_ROOT = Path(__file__).resolve().parent.parent
for _path in (str(_ROOT / "src"), str(_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from engine.config.research_corpus_config import ResearchCorpusConfig  # noqa: E402
from engine.data.historical_provider import InMemoryHistoricalProvider  # noqa: E402
from engine.data.historical_service import HistoricalMarketDataService  # noqa: E402
from engine.data.historical_store import HistoricalDataStore  # noqa: E402
from engine.data.research_corpus import (  # noqa: E402
    HistoricalResearchCorpusEngine,
    evaluation_grid,
)
from engine.data.research_corpus_store import ResearchCorpusStore  # noqa: E402
from engine.models.historical_data import HistoricalDataRequest  # noqa: E402
from engine.models.ohlcv import OHLCVCandle  # noqa: E402
from engine.models.research_corpus import CorpusPointStatus  # noqa: E402
from engine.pipeline import HistoricalEvaluationPipeline, trending_dataset  # noqa: E402
from engine.reporting.research_corpus import ResearchCorpusFormatter  # noqa: E402

BASE = datetime(2024, 1, 1, tzinfo=UTC)  # a Monday
NOW = BASE + timedelta(days=60)

_checks = 0


def report(name: str, ok: bool, detail: str = "") -> None:
    global _checks
    _checks += 1
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        raise SystemExit(f"demo check failed: {name}")


def _candle(ts: datetime, close: float) -> OHLCVCandle:
    return OHLCVCandle(ts, close, close + 1.0, close - 1.0, close, 1000.0)


def _zigzag(n, start, step, period=6, drift=0.0):
    candles = []
    price = 100.0
    for i in range(n):
        direction = 1.0 if (i // period) % 2 == 0 else -1.0
        price += direction * 1.5 + drift
        candles.append(_candle(start + step * i, price))
    return tuple(candles)


def main() -> int:
    print("=" * 72)
    print("PRODUCT PHASE 6C — HISTORICAL RESEARCH CORPUS — DEMO")
    print("=" * 72)

    instruments = ("RELIANCE", "TCS")
    records: dict[tuple[str, str], tuple[OHLCVCandle, ...]] = {}
    for offset, instrument in enumerate(instruments):
        records[(instrument, "15m")] = _zigzag(
            48, BASE, timedelta(minutes=15), drift=0.05 * offset,
        )
        records[(instrument, "1D")] = _zigzag(
            8, BASE, timedelta(days=1), period=3, drift=0.1 * offset,
        )

    with TemporaryDirectory() as tmp:
        # 1. Historical OHLCV -> stored historical dataset (Phase 6B).
        store = HistoricalDataStore(Path(tmp) / "hist")
        service = HistoricalMarketDataService(
            provider=InMemoryHistoricalProvider(records),
            store=store,
        )
        for (instrument, timeframe), candles in records.items():
            service.ingest(
                HistoricalDataRequest(
                    instrument, timeframe,
                    candles[0].timestamp, candles[-1].timestamp,
                ),
                reference_now=NOW,
            )
        stored = service.load_historical("RELIANCE", "15m")
        report(
            "1. historical OHLCV ingested + stored via the Phase 6B foundation",
            stored.count == 48 and stored.source_count == 48,
            f"{stored.count} setup candles stored",
        )

        # 2. Evaluation-time sampling (canonical candle grid).
        config = ResearchCorpusConfig(min_setup_history=6, sample_every=1)
        engine = HistoricalResearchCorpusEngine(service, config)
        grid = engine.evaluation_points_for("RELIANCE")
        expected = tuple(c.timestamp for c in records[("RELIANCE", "15m")])
        report(
            "2. deterministic evaluation grid == canonical candle boundaries",
            grid == expected,
            f"{len(grid)} evaluation times",
        )
        sampled = evaluation_grid(
            records[("RELIANCE", "15m")], every=4,
        )
        report(
            "3. sampling frequency supported (every 4th candle)",
            sampled == expected[::4],
            f"{len(sampled)} sampled points",
        )

        # 3. Point-in-time historical slice at evaluation time T.
        T = expected[20]
        state = engine.get_state("RELIANCE", T)
        report(
            "4. historical market state retrieved at evaluation time T",
            state is not None and state.evaluation_time == T,
            f"T = {T.isoformat()}",
        )
        report(
            "5. latest usable setup candle <= T (future candles excluded)",
            state.latest_usable_setup_timestamp == T
            and all(c.timestamp <= T for c in state.setup_slice.candles),
            f"latest usable = {state.latest_usable_setup_timestamp.isoformat()}",
        )
        report(
            "6. context timeframe uses only completed candles strictly < T",
            state.latest_usable_context_timestamp < T
            and all(c.timestamp < T for c in state.context_slice.candles),
            f"latest completed context = "
            f"{state.latest_usable_context_timestamp.isoformat()}",
        )

        # 4. Context + setup timeframes aligned; structure reused.
        report(
            "7. context/setup timeframes reconstructed independently",
            state.setup_timeframe == "15m"
            and state.context_timeframe == "1D"
            and state.setup_slice.count == 21
            and state.context_slice.count == 1,
            f"setup={state.setup_slice.count} candles, "
            f"context={state.context_slice.count} completed candle",
        )
        report(
            "8. market structure/context reused from the existing engine",
            state.setup_context is not None
            and state.context_context is not None
            and state.setup_context.trend is not None
            and state.setup_context.support_resistance is not None,
            f"setup trend={state.setup_context.trend.state.name}, "
            f"confirmed_swings={state.setup_context.confirmed_swings}, "
            f"mtf={state.mtf_alignment.name}",
        )

        # 5. No-look-ahead proof: future mutation does not change state(T).
        mutated = expected and records[("RELIANCE", "15m")][:21] + tuple(
            _candle(c.timestamp, 9999.0)
            for c in records[("RELIANCE", "15m")][21:]
        )
        store.store("RELIANCE", "15m", mutated, overwrite=True)
        after = engine.get_state("RELIANCE", T)
        report(
            "9. mutating future candles leaves the state at T unchanged",
            after.setup_slice.candles == state.setup_slice.candles
            and after.setup_context.trend.state == state.setup_context.trend.state,
        )
        store.store(
            "RELIANCE", "15m", records[("RELIANCE", "15m")], overwrite=True,
        )

        # 6. Research-ready corpus over the universe.
        corpus = engine.build(list(instruments), label="phase-6c-demo")
        report(
            "10. research-ready corpus built over multiple instruments",
            corpus.report.loaded_instruments == instruments
            and corpus.report.valid_count > 0,
            f"{corpus.report.evaluation_count} points, "
            f"{corpus.report.valid_count} valid",
        )
        report(
            "11. minimum-history requirement enforced explicitly",
            corpus.report.insufficient_history_count > 0
            and all(
                p.state is None
                for p in corpus.evaluation_points
                if p.status is CorpusPointStatus.INSUFFICIENT_HISTORY
            ),
            f"{corpus.report.insufficient_history_count} insufficient-history "
            "points skipped",
        )
        report(
            "12. data quality + provenance preserved on the report",
            corpus.report.provider == "in-memory-import"
            and corpus.report.storage_status == "persisted"
            and corpus.report.ingestion_version == "1"
            and corpus.report.missing_instruments == (),
        )
        report(
            "13. corpus build deterministic",
            engine.build(list(instruments), label="phase-6c-demo").corpus_id
            == corpus.corpus_id,
            corpus.corpus_id,
        )

        # 7. Corpus metadata persistence (manifest only — no candles).
        corpus_store = ResearchCorpusStore(Path(tmp) / "corpus")
        corpus_store.save(corpus, configuration=config.snapshot())
        manifest = corpus_store.load(corpus.corpus_id)
        report(
            "14. corpus metadata manifest persisted + reloaded",
            manifest.report == corpus.report
            and manifest.configuration == config.snapshot(),
        )

        # 8. Formatted reports.
        formatter = ResearchCorpusFormatter()
        corpus_text = formatter.format(corpus)
        point_text = formatter.format_point(corpus.valid_points[0])
        report(
            "15. corpus + evaluation-point reports render with disclaimers",
            "HISTORICAL RESEARCH CORPUS REPORT" in corpus_text
            and "HISTORICAL EVALUATION POINT" in point_text
            and "DISCLAIMER" in corpus_text
            and "Future candles (> T): excluded" in point_text,
        )

        print()
        print("--- sample evaluation point -----------------------------------")
        print(formatter.format_point(corpus.valid_points[20]))

    # 9. Existing architecture regression (decision path untouched).
    result = HistoricalEvaluationPipeline().evaluate(trending_dataset())
    report(
        "16. existing pipeline regression unchanged (signals=4, trades=3)",
        result.signals_generated == 4 and result.completed_trades == 3,
    )

    print()
    print(
        "The corpus is DESCRIPTIVE research preparation only: no trading "
        "strategy, no prediction, no decision, no evidence computed."
    )
    print(f"Product Phase 6C demo completed successfully ({_checks} checks passed).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
