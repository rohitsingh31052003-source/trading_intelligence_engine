#!/usr/bin/env python3
"""
Product Phase 6D demo — Historical Setup Research.

Visibly proves the research layer works end-to-end and DETERMINISTICALLY,
entirely offline (in-memory import provider + a temporary store; no
network):

    Phase 6C Historical Research Corpus
                |
       historical evaluation point T
                |
       existing setup/structure logic (REUSED, authoritative)
                |
       setup occurrence detected
                |
       historical outcome (REUSED, forward-only)
                |
       evidence aggregation (REUSED vocabulary)
                |
       Historical Research Evidence

Demonstrations:

1.  build the Phase 6C corpus
2.  select a historical evaluation range
3.  detect historical setup occurrences (reusing the EXISTING setup
    architecture)
4.  calculate historical outcomes (reusing the EXISTING forward-only
    outcome evaluator)
5.  aggregate deterministic historical evidence
6.  print a concise research report
7.  demonstrate the no-look-ahead guarantee (future-mutation test)
8.  demonstrate serialization + persistence round trip
9.  demonstrate the existing pipeline regression baseline is intact

Every check prints an explicit PASS/FAIL; the demo exits non-zero on
any failure. Historical evidence is DESCRIPTIVE and OBSERVATIONAL. It
is not a prediction, recommendation, or guarantee of future
performance.
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
from engine.config.setup_research_config import SetupResearchConfig  # noqa: E402
from engine.data.historical_provider import InMemoryHistoricalProvider  # noqa: E402
from engine.data.historical_service import HistoricalMarketDataService  # noqa: E402
from engine.data.historical_store import HistoricalDataStore  # noqa: E402
from engine.data.research_corpus import HistoricalResearchCorpusEngine  # noqa: E402
from engine.data.setup_research import HistoricalSetupResearchEngine  # noqa: E402
from engine.data.setup_research_serialization import (  # noqa: E402
    deserialize_result,
    parse_result_header,
    serialize_result,
)
from engine.data.setup_research_store import SetupResearchStore  # noqa: E402
from engine.models.historical_data import HistoricalDataRequest  # noqa: E402
from engine.models.historical_outcome import OutcomeStatus  # noqa: E402
from engine.models.ohlcv import OHLCVCandle  # noqa: E402
from engine.models.setup_research import (  # noqa: E402
    SetupResearchRequest,
    SetupResearchStatus,
)
from engine.pipeline import HistoricalEvaluationPipeline, trending_dataset  # noqa: E402
from engine.reporting.setup_research import SetupResearchFormatter  # noqa: E402

BASE = datetime(2024, 1, 1, tzinfo=UTC)  # a Monday
NOW = BASE + timedelta(days=60)
STEP = timedelta(minutes=15)

_checks = 0


def report(name: str, ok: bool, detail: str = "") -> None:
    global _checks
    _checks += 1
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        raise SystemExit(f"demo check failed: {name}")


def _candle(ts: datetime, close: float) -> OHLCVCandle:
    return OHLCVCandle(ts, close, close + 2.0, close - 2.0, close, 1000.0)


def _hammer(ts: datetime, base_close: float, body: float = 2.0) -> OHLCVCandle:
    close = base_close + body
    return OHLCVCandle(ts, base_close, close + body, close - 3 * body, close, 1000.0)


def _trending_series(cycles: int = 6, tail: int = 20) -> tuple[OHLCVCandle, ...]:
    """Deterministic gentle uptrend embedding pullback hammers + a tail."""

    candles: list[OHLCVCandle] = []
    price = 100.0
    idx = 0
    for _ in range(cycles):
        for _ in range(3):
            price = round(price + 4.0, 2)
            candles.append(_candle(BASE + STEP * idx, price))
            idx += 1
        for _ in range(2):
            price = round(price - 2.0, 2)
            candles.append(_candle(BASE + STEP * idx, price))
            idx += 1
        candles.append(_hammer(BASE + STEP * idx, price))
        idx += 1
        price = round(price + 2.0, 2)
    # Flat tail: guarantees room beyond the last occurrence's horizon
    # for the no-look-ahead future-mutation test.
    for _ in range(tail):
        price = round(price + 0.5, 2)
        candles.append(_candle(BASE + STEP * idx, price))
        idx += 1
    return tuple(candles)


def _daily_series(n: int = 10) -> tuple[OHLCVCandle, ...]:
    return tuple(
        _candle(BASE + timedelta(days=i), 100 + i) for i in range(n)
    )


def _build_service(
    directory: str,
    setup: tuple[OHLCVCandle, ...],
    context: tuple[OHLCVCandle, ...],
) -> HistoricalMarketDataService:
    provider = InMemoryHistoricalProvider(
        {("NIFTY", "15m"): setup, ("NIFTY", "1D"): context},
    )
    service = HistoricalMarketDataService(
        provider=provider,
        store=HistoricalDataStore(Path(directory) / "historical"),
    )
    for (instrument, timeframe), candles in (
        (("NIFTY", "15m"), setup),
        (("NIFTY", "1D"), context),
    ):
        if not candles:
            continue
        service.ingest(
            HistoricalDataRequest(
                instrument,
                timeframe,
                candles[0].timestamp,
                candles[-1].timestamp + timedelta(seconds=1),
            ),
            reference_now=NOW,
        )
    return service


def _engine(
    service: HistoricalMarketDataService,
) -> HistoricalSetupResearchEngine:
    corpus = HistoricalResearchCorpusEngine(
        service,
        ResearchCorpusConfig(
            setup_timeframe="15m",
            context_timeframe="1D",
            min_setup_history=5,
        ),
    )
    return HistoricalSetupResearchEngine(corpus, SetupResearchConfig())


def main() -> int:
    print("=" * 72)
    print("PHASE 6D — HISTORICAL SETUP RESEARCH")
    print("=" * 72)
    print()

    # ----------------------------------------------------------
    # 1. BUILD THE PHASE 6C CORPUS
    # ----------------------------------------------------------
    setup = _trending_series()
    context = _daily_series()
    with TemporaryDirectory() as tmp:
        service = _build_service(tmp, setup, context)
        engine = _engine(service)
        corpus = engine.corpus.build(["NIFTY"], label="demo")

        report(
            "1. corpus built",
            len(corpus.datasets) == 1 and corpus.report.valid_count > 0,
            f"valid evaluation points: {corpus.report.valid_count}",
        )
        print(f"Corpus: READY — Instrument: NIFTY, Setup 15m, Context 1D")
        print()

        # ----------------------------------------------------------
        # 2-5. RANGE + OCCURRENCES + OUTCOMES + EVIDENCE
        # ----------------------------------------------------------
        request = SetupResearchRequest(
            "NIFTY",
            setup_timeframe="15m",
            context_timeframe="1D",
            forward_horizon=10,
            minimum_history=5,
        )
        result = engine.research(request, label="demo")

        report(
            "2. historical evaluation range examined",
            result.points_examined > 0 and result.valid_points > 0,
            f"{result.valid_points}/{result.points_examined} valid",
        )
        report(
            "3. historical setup occurrences detected",
            result.status is SetupResearchStatus.RESEARCHED
            and result.occurrence_count >= 2,
            f"occurrences: {result.occurrence_count}",
        )
        completed = [
            o for o in result.observations if o.is_completed
        ]
        report(
            "4. historical outcomes calculated deterministically",
            result.completed_outcomes >= 1
            and any(
                o.outcome_status is OutcomeStatus.TARGET_HIT
                for o in result.observations
            ),
            f"completed: {result.completed_outcomes}, "
            f"ambiguous: {result.ambiguous_count}, "
            f"unresolved: {result.unresolved_count}",
        )
        report(
            "   geometry-unavailable outcomes explicit (never fabricated)",
            any(
                o.outcome_status is OutcomeStatus.NO_GEOMETRY
                for o in result.observations
            ),
        )
        report(
            "5. evidence aggregated without fabrication",
            result.evidence is not None
            and result.evidence.sample_size == result.occurrence_count
            and any(
                g.strength.name == "INSUFFICIENT" for g in result.grouped_evidence
            ),
            f"strength: {result.evidence.strength.name if result.evidence else ''}",
        )
        report(
            "   insufficient evidence explicit on small groups",
            all(
                g.sample_size < SetupResearchConfig().min_sample_total
                for g in result.grouped_evidence
                if g.strength.name == "INSUFFICIENT"
            ),
        )
        report(
            "   regime/structure grouping available",
            any(g.dimension == "SETUP_TYPE" for g in result.grouped_evidence)
            and any(g.dimension == "TREND" for g in result.grouped_evidence)
            and any(
                g.dimension == "MTF_ALIGNMENT" for g in result.grouped_evidence
            ),
        )
        report(
            "   research filters select occurrences deterministically",
            engine.research(
                SetupResearchRequest(
                    "NIFTY",
                    minimum_history=5,
                    direction_filter="LONG",
                ),
            ).status is SetupResearchStatus.RESEARCHED
            and engine.research(
                SetupResearchRequest(
                    "NIFTY",
                    minimum_history=5,
                    direction_filter="SHORT",
                ),
            ).status is SetupResearchStatus.NO_OCCURRENCES,
        )

        # ----------------------------------------------------------
        # 6. RESEARCH REPORT
        # ----------------------------------------------------------
        text = SetupResearchFormatter().format(result)
        print()
        print(text)
        print()
        report(
            "6. concise research report rendered",
            "PHASE 6D" in text and "Evidence status" in text,
        )

        # ----------------------------------------------------------
        # 7. NO-LOOK-AHEAD (future-mutation test)
        # ----------------------------------------------------------
        # Mutate a future candle AFTER the last occurrence's horizon:
        # detection must be identical and the outcome must be identical
        # (beyond-horizon candles are never inspected).
        last_time = result.observations[-1].evaluation_time
        horizon = request.forward_horizon
        idx = next(
            i for i, c in enumerate(setup) if c.timestamp == last_time
        )
        beyond = idx + horizon + 3
        if beyond < len(setup):
            mutated = list(setup)
            victim = mutated[beyond]
            mutated[beyond] = OHLCVCandle(
                victim.timestamp, victim.open, victim.high * 10,
                victim.low / 10, victim.close / 2, victim.volume,
            )
            mutated_service = _build_service(tmp, tuple(mutated), context)
            mutated_result = _engine(mutated_service).research(
                request, label="demo",
            )
            detection_same = [
                (o.occurrence.evaluation_time, o.occurrence.setup_classification)
                for o in result.observations
            ] == [
                (o.occurrence.evaluation_time, o.occurrence.setup_classification)
                for o in mutated_result.observations
            ]
            outcome_same = [
                (o.outcome.outcome_status, o.outcome.realized_r)
                for o in result.observations
            ] == [
                (o.outcome.outcome_status, o.outcome.realized_r)
                for o in mutated_result.observations
            ]
            report(
                "7. no-look-ahead: future mutation leaves detection + "
                "outcome identical",
                detection_same and outcome_same,
            )
        else:
            report("7. no-look-ahead: future mutation test", False, "no room")

        # Detection never depends on the outcome evaluator: patch it to
        # raise and detection still works.
        from engine.intelligence.historical_outcome import OutcomeEvaluator

        original = OutcomeEvaluator.evaluate
        try:
            OutcomeEvaluator.evaluate = staticmethod(
                lambda *a, **k: (_ for _ in ()).throw(
                    RuntimeError("outcome evaluator called during detection")
                ),
            )
            detected = engine.detect(request)
            report(
                "   detection has no outcome dependency (evaluator off)",
                len(detected) == result.occurrences_detected,
            )
        finally:
            OutcomeEvaluator.evaluate = original

        # ----------------------------------------------------------
        # 8. SERIALIZATION + PERSISTENCE
        # ----------------------------------------------------------
        payload = serialize_result(result)
        reloaded = deserialize_result(payload)
        report(
            "8. serialization round trip lossless",
            reloaded == result and reloaded.research_id == result.research_id,
        )
        store = SetupResearchStore(Path(tmp) / "research")
        store.save(result)
        report(
            "   persistence round trip",
            store.exists(result.research_id)
            and store.load(result.research_id) == result
            and parse_result_header(payload)["research_id"]
            == result.research_id,
        )

        # ----------------------------------------------------------
        # 9. EXISTING PIPELINE REGRESSION BASELINE
        # ----------------------------------------------------------
        pipeline = HistoricalEvaluationPipeline()
        pipeline_result = pipeline.evaluate(trending_dataset())
        report(
            "9. existing pipeline regression intact (signals=4, trades=3)",
            pipeline_result.signals_generated == 4
            and pipeline_result.completed_trades == 3,
        )

    print()
    print(f"No-look-ahead verification: PASS")
    print(f"Demo checks passed: {_checks}")
    print("Phase 6D demo completed successfully.")
    print(
        "Historical evidence is descriptive and observational. It is not "
        "a prediction, recommendation, or guarantee of future performance."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
