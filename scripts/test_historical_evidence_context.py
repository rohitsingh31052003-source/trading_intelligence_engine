#!/usr/bin/env python3
"""
Product Phase 6E demo — Historical + Current Intelligence.

Visibly proves the evidence-integration layer works end-to-end and
DETERMINISTICALLY, entirely offline (the deterministic dashboard
fixtures + a temporary store; NO network / live Yahoo access):

    Phase 6C Historical Research Corpus
                |
    Phase 6D Historical Setup Research (PERSISTED)
                |
    Phase 6E Historical Evidence Lookup (this demo)
                |
    Current Market Assessment (existing scanner/analysis)
                |
    Combined Current + Historical Intelligence View

Demonstrations (each prints explicit PASS/FAIL; the demo exits non-zero
on any failure):

1.  current market assessment (existing dashboard analysis)
2.  historical evidence lookup for comparable setups
3.  comparable occurrence count
4.  historical outcome statistics where available
5.  evidence strength (reused Sprint 11Y vocabulary)
6.  evidence unavailable case (honest, never fabricated)
7.  authoritative decision remaining unchanged (REJECTED + STRONG)
8.  no geometry fabrication
9.  no paper trade created solely because of historical evidence
10. point-in-time boundary (occurrence/outcome at-or-after T excluded)

Historical evidence is DESCRIPTIVE and OBSERVATIONAL. It is NOT a
prediction, NOT a probability of success, NOT a profitability guarantee
and NOT a trading recommendation. It NEVER modifies the authoritative
existing decision.
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

from dashboard.data_provider import FixtureDataProvider  # noqa: E402
from dashboard.services import (  # noqa: E402
    AnalysisRequest,
    DashboardAnalysisService,
    HistoricalEvidenceSource,
    OperationsRequest,
)
from dashboard.views import to_jsonable  # noqa: E402
from engine.config.research_corpus_config import ResearchCorpusConfig  # noqa: E402
from engine.config.setup_research_config import SetupResearchConfig  # noqa: E402
from engine.data.historical_evidence_lookup import (  # noqa: E402
    HistoricalEvidenceLookupEngine,
)
from engine.data.historical_fixtures import (  # noqa: E402
    historical_candles_by_instrument,
)
from engine.data.historical_provider import InMemoryHistoricalProvider  # noqa: E402
from engine.data.historical_service import HistoricalMarketDataService  # noqa: E402
from engine.data.historical_store import HistoricalDataStore  # noqa: E402
from engine.data.research_corpus import HistoricalResearchCorpusEngine  # noqa: E402
from engine.data.setup_research import HistoricalSetupResearchEngine  # noqa: E402
from engine.data.setup_research_store import SetupResearchStore  # noqa: E402
from engine.intelligence.historical_outcome import OutcomeEvaluator  # noqa: E402
from engine.models.historical_context import (  # noqa: E402
    HistoricalContextStatus,
    HistoricalEvidenceRequest,
)
from engine.models.historical_data import HistoricalDataRequest  # noqa: E402
from engine.models.historical_outcome import (  # noqa: E402
    HistoricalOutcome,
    OutcomeStatus,
    OutcomeSubject,
)
from engine.models.setup_research import (  # noqa: E402
    SetupOccurrence,
    SetupResearchObservation,
    SetupResearchRequest,
    SetupResearchResult,
    SetupResearchStatus,
)
from engine.pipeline import (  # noqa: E402
    HistoricalEvaluationPipeline,
    PipelineConfig,
    trending_dataset,
)
from engine.config.swing_config import SwingConfig  # noqa: E402
from engine.reporting.historical_evidence_lookup import (  # noqa: E402
    HistoricalEvidenceContextFormatter,
)

_checks = 0


def report(name: str, ok: bool, detail: str = "") -> None:
    global _checks
    _checks += 1
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        raise SystemExit(f"demo check failed: {name}")


def _build_fixture_store(root: Path) -> SetupResearchStore:
    """Persist Phase 6D research for every dashboard fixture instrument."""

    by_inst = historical_candles_by_instrument()
    records = {(i, tf): c for i, tfs in by_inst.items() for tf, c in tfs.items()}
    service = HistoricalMarketDataService(
        provider=InMemoryHistoricalProvider(records),
        store=HistoricalDataStore(root / "historical"),
    )
    reference = records[("NIFTY", "15M")][-1].timestamp + timedelta(days=1)
    for (inst, tf), candles in records.items():
        service.ingest(
            HistoricalDataRequest(
                inst,
                tf,
                candles[0].timestamp,
                candles[-1].timestamp + timedelta(seconds=1),
            ),
            reference_now=reference,
        )
    corpus_engine = HistoricalResearchCorpusEngine(
        service,
        ResearchCorpusConfig(
            setup_timeframe="15m", context_timeframe="1D", min_setup_history=5,
        ),
    )
    research_engine = HistoricalSetupResearchEngine(corpus_engine, SetupResearchConfig())
    store = SetupResearchStore(root / "research")
    for inst in by_inst:
        store.save(research_engine.research(SetupResearchRequest(inst)))
    return store


def _strong_hdfcbank_result() -> SetupResearchResult:
    """Hand-built persisted Phase 6D result: 25 already-resolved TARGET_HIT
    occurrences (setup_type SETUP_CANDIDATE) strictly before the fixture
    evaluation point -> STRONG evidence under the default gates."""

    base = datetime(2025, 1, 20, tzinfo=UTC)
    step = timedelta(minutes=15)
    observations = []
    for i in range(25):
        ts = base + i * step
        occurrence = SetupOccurrence(
            instrument="HDFCBANK",
            setup_timeframe="15m",
            context_timeframe="1D",
            evaluation_time=ts,
            setup_classification="POTENTIAL_SETUP",
            setup_direction="BULLISH",
            confluence_score=4,
            candidate_status="CANDIDATE",
            decision_classification="QUALIFIED",
            decision_score=80,
            direction="LONG",
            setup_type="SETUP_CANDIDATE",
            trend_state="BULLISH",
            range_state="NOT_IN_RANGE",
            mtf_alignment="ALIGNED",
            geometry_available=True,
            entry=100.0,
            stop=98.0,
            target=104.0,
            reason="demo occurrence",
        )
        outcome = HistoricalOutcome(
            subject=OutcomeSubject(
                instrument="HDFCBANK",
                direction="LONG",
                evaluation_timestamp=ts,
                entry=100.0,
                stop=98.0,
                target=104.0,
                setup_timeframe="15m",
            ),
            outcome_status=OutcomeStatus.TARGET_HIT,
            outcome_timestamp=ts + 4 * step,
            exit_price=104.0,
            bars_held=4,
            mfe=5.0,
            mae=1.0,
            mfe_r=2.5,
            mae_r=0.5,
            realized_r=2.0,
            risk=2.0,
            reason="demo target hit",
        )
        observations.append(
            SetupResearchObservation(occurrence=occurrence, outcome=outcome),
        )
    return SetupResearchResult(
        research_id="setup-research-demo-hdfcbank-strong",
        request=SetupResearchRequest("HDFCBANK"),
        status=SetupResearchStatus.RESEARCHED,
        points_examined=len(observations),
        valid_points=len(observations),
        occurrences_detected=len(observations),
        occurrence_count=len(observations),
        completed_outcomes=len(observations),
        ambiguous_count=0,
        unresolved_count=0,
        observations=tuple(observations),
    )


def main() -> int:
    print("=" * 72)
    print("PHASE 6E — HISTORICAL + CURRENT INTELLIGENCE")
    print("=" * 72)
    print()

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = _build_fixture_store(root)

        # ------------------------------------------------------
        # 1. CURRENT MARKET ASSESSMENT (existing analysis)
        # ------------------------------------------------------
        plain_service = DashboardAnalysisService(provider=FixtureDataProvider())
        baseline = plain_service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15M"),
        )
        report(
            "1. current market assessment produced",
            baseline.decision.decision_classification == "QUALIFIED"
            and baseline.evaluation_timestamp is not None,
            f"NIFTY decision={baseline.decision.decision_classification} "
            f"actionability={baseline.actionability.name} "
            f"T={baseline.evaluation_timestamp}",
        )

        # ------------------------------------------------------
        # 2-5. HISTORICAL EVIDENCE LOOKUP
        # ------------------------------------------------------
        service = DashboardAnalysisService(
            provider=FixtureDataProvider(),
            historical_evidence_source=HistoricalEvidenceSource(store),
        )
        view = service.analyze(AnalysisRequest(instrument="NIFTY", setup_timeframe="15M"))
        ctx = view.historical_context
        report(
            "2. historical evidence lookup for the current assessment",
            ctx.status == "AVAILABLE" and ctx.available,
            f"match key: {ctx.match_key}",
        )
        report(
            "3. comparable historical occurrence count",
            ctx.comparable_occurrences >= 1,
            f"comparable occurrences: {ctx.comparable_occurrences} "
            f"(completed {ctx.completed_outcomes}, ambiguous "
            f"{ctx.ambiguous_count}, unresolved {ctx.unresolved_count})",
        )
        stats_ctx = HistoricalEvidenceLookupEngine(store).lookup(
            HistoricalEvidenceRequest(
                "NIFTY",
                setup_timeframe="15m",
                context_timeframe="1D",
                evaluation_time=view.evaluation_timestamp,
                setup_type=view.setup_type,
                direction=view.geometry.direction,
            ),
        )
        report(
            "4. historical outcome statistics where available",
            stats_ctx.statistics is not None
            and stats_ctx.statistics.total >= 1,
            f"sample={stats_ctx.statistics.total} "
            f"win_rate={stats_ctx.win_rate} avg_r={stats_ctx.average_realized_r} "
            f"(unavailable metrics stay None, never fabricated)",
        )
        report(
            "5. evidence strength uses the reused Sprint 11Y vocabulary",
            ctx.evidence_strength == "INSUFFICIENT",
            "fixture sample is small -> INSUFFICIENT (hard gate), never "
            "promoted by a favourable observed result",
        )

        # ------------------------------------------------------
        # 6. EVIDENCE UNAVAILABLE (honest, never fabricated)
        # ------------------------------------------------------
        plain = plain_service.analyze(
            AnalysisRequest(instrument="NIFTY", setup_timeframe="15M"),
        )
        report(
            "6. evidence unavailable case is honest",
            plain.historical_context.status == "UNAVAILABLE"
            and not plain.historical_context.available
            and plain.historical_context.win_rate is None,
            "no Phase 6D store attached -> UNAVAILABLE, no fabricated "
            "statistics",
        )

        # ------------------------------------------------------
        # 7. AUTHORITATIVE DECISION REMAINS UNCHANGED
        # ------------------------------------------------------
        strong_store = SetupResearchStore(root / "strong")
        strong_store.save(_strong_hdfcbank_result())
        strong_service = DashboardAnalysisService(
            provider=FixtureDataProvider(),
            historical_evidence_source=HistoricalEvidenceSource(strong_store),
        )
        rejected = strong_service.analyze(
            AnalysisRequest(instrument="HDFCBANK", setup_timeframe="15M"),
        )
        report(
            "7. REJECTED + STRONG historical evidence stays REJECTED",
            rejected.decision.decision_classification == "REJECTED"
            and rejected.historical_context.evidence_strength == "STRONG"
            and rejected.actionability.name == "NO_OPPORTUNITY",
            f"decision={rejected.decision.decision_classification} "
            f"evidence={rejected.historical_context.evidence_strength} "
            f"actionability={rejected.actionability.name}",
        )
        same = all(
            getattr(view, attr) == getattr(baseline, attr)
            for attr in ("decision", "geometry", "market_overview", "warnings")
        ) and view.actionability is baseline.actionability
        report(
            "7b. evidence attachment never alters the current decision",
            same,
            "decision/actionability/geometry/warnings identical with and "
            "without historical evidence",
        )

        # ------------------------------------------------------
        # 8. NO GEOMETRY FABRICATION
        # ------------------------------------------------------
        report(
            "8. historical evidence never fabricates geometry",
            view.geometry.geometry_available is False
            and view.geometry.target_1 is None
            and view.geometry.target_2 is None
            and view.geometry.target_2_supported is False
            and view.geometry == baseline.geometry,
            "incomplete geometry remains incomplete under evidence",
        )

        # ------------------------------------------------------
        # 9. NO PAPER TRADE CREATED SOLELY BY EVIDENCE
        # ------------------------------------------------------
        cycle = strong_service.run_paper_trading_cycle(
            OperationsRequest(
                account_capital="100000",
                risk_percent="1",
                setup_timeframe="15M",
                watchlist=["HDFCBANK"],
            ),
        )
        report(
            "9. REJECTED + STRONG evidence creates NO paper trade",
            cycle.trades_created == 0,
            f"trades_created={cycle.trades_created} (evidence alone never "
            "creates a trade; the authoritative decision governs)",
        )

        # ------------------------------------------------------
        # 10. POINT-IN-TIME BOUNDARY
        # ------------------------------------------------------
        lookup = HistoricalEvidenceLookupEngine(store)
        eval_t = view.evaluation_timestamp
        at_t = lookup.lookup(
            HistoricalEvidenceRequest(
                "NIFTY",
                setup_timeframe="15m",
                context_timeframe="1D",
                evaluation_time=eval_t,
            ),
        )
        strictly_before = lookup.lookup(
            HistoricalEvidenceRequest(
                "NIFTY",
                setup_timeframe="15m",
                context_timeframe="1D",
                evaluation_time=eval_t - timedelta(milliseconds=1),
            ),
        )
        no_future_params = not {
            "future", "future_candles", "lookahead",
        } & set(
            __import__("inspect").signature(
                HistoricalEvidenceLookupEngine.lookup,
            ).parameters,
        )
        report(
            "10. point-in-time boundary enforced",
            no_future_params
            and strictly_before.comparable_occurrences
            <= at_t.comparable_occurrences,
            f"occurrences visible at T-1ms={strictly_before.comparable_occurrences} "
            f"vs at T={at_t.comparable_occurrences}; occurrences at/after T "
            "are never used; no future/lookahead parameter exists",
        )

        # Outcome evaluator must NOT be consulted by the lookup.
        original = OutcomeEvaluator.evaluate

        def _explode(self, *args, **kwargs):  # pragma: no cover - guard
            raise AssertionError("outcome evaluator must not be called")

        OutcomeEvaluator.evaluate = _explode
        try:
            ctx_guard = lookup.lookup(
                HistoricalEvidenceRequest(
                    "NIFTY",
                    setup_timeframe="15m",
                    context_timeframe="1D",
                    evaluation_time=eval_t,
                ),
            )
            report(
                "10b. lookup consumes persisted research only",
                ctx_guard.status is HistoricalContextStatus.AVAILABLE,
                "OutcomeEvaluator patched to raise; lookup unaffected",
            )
        finally:
            OutcomeEvaluator.evaluate = original

        # ------------------------------------------------------
        # REPORT + JSON
        # ------------------------------------------------------
        engine_lookup = HistoricalEvidenceLookupEngine(store)
        context = engine_lookup.lookup(
            HistoricalEvidenceRequest(
                "NIFTY",
                setup_timeframe="15m",
                context_timeframe="1D",
                evaluation_time=eval_t,
                setup_type="BREAKOUT",
                direction="LONG",
            ),
        )
        print()
        print(HistoricalEvidenceContextFormatter().format(context))
        print()
        payload = to_jsonable(view)
        report(
            "11. API payload exposes the additive historical_context block",
            "historical_context" in payload
            and payload["historical_context"]["status"] == "AVAILABLE"
            and payload["decision"]["decision_classification"] == "QUALIFIED",
            "existing response fields unchanged; historical context additive",
        )

        # ------------------------------------------------------
        # 12. EXISTING PIPELINE REGRESSION
        # ------------------------------------------------------
        result = HistoricalEvaluationPipeline(
            PipelineConfig(swing_config=SwingConfig(lookback=2)),
        ).evaluate(trending_dataset())
        report(
            "12. existing pipeline regression baseline unchanged",
            result.signals_generated == 4 and result.completed_trades == 3,
            f"signals={result.signals_generated} trades={result.completed_trades}",
        )

    print()
    print(f"Phase 6E demo completed successfully ({_checks} checks passed).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
