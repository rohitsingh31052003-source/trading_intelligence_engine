# Trading Intelligence Engine — Agent Memory

## Repository
- Path: `/workspace/project/trading_intelligence_engine`
- Package layout: `src/` (setuptools `package-dir = {"" = "src"}`), `pythonpath = ["src"]` in pytest config.
- `__init__.py` convention: the engine/data/models/config/utils/core packages have INTENTIONALLY EMPTY `__init__.py` files (no re-exports) — import via full paths, e.g. `from engine.intelligence.signal import SignalEngine`. The orchestration packages are the EXCEPTION: `src/engine/pipeline/__init__.py` (Sprint 11F) and `src/engine/reporting/__init__.py` (Sprint 11G) re-export their public API for convenience, e.g. `from engine.pipeline import HistoricalEvaluationPipeline, trending_dataset` and `from engine.reporting import EvaluationReportEngine`.
- Python >= 3.11 (env runs 3.13). Tests: `python -m pytest -q`. No conftest.

## Engine public APIs (verified Sprint 11F)
- `SwingEngine(config).detect(candles) -> list[SwingPoint]` — fractal swing detection; needs `lookback` candles on each side to confirm a swing. Mutates `swing.evidence` (mutable SwingEvidence) but never the input candles.
- `MarketStructureEngine().analyze(swings) -> list[StructurePoint]`
- `StructureAnalysisEngine().analyze(structures) -> StructureAnalysis` — exposes `current_bias` / `previous_bias` (NOT `.bias`).
- `BOSEngine().analyze(analysis) -> BOSResult` — exposes `bos_type` (NOT `.type`).
- `CHOCHEngine().analyze(structures, analysis, bos) -> CHOCHResult` — exposes `choch_type` (NOT `.type`).
- `TrendEngine().analyze(analysis, bos, choch) -> TrendResult` — exposes `.state` (matches what ConfluenceEngine reads).
- `LiquidityEngine(config).detect(swings) -> list[LiquidityPool]`
- `LiquidityEventEngine(config).analyze(pools, candles) -> list[LiquidityEvent]`
- `ConfluenceEngine().analyze(analysis, bos, choch, trend, liquidity_events=None, reference_time=None) -> ConfluenceResult` — reads attributes DEFENSIVELY via getattr: `analysis.bias`, `bos.type`, `choch.type`, `trend.state`. Real model attribute names DIFFER (see above), so end-to-end wiring needs adapter views (see pipeline).
- `DecisionEngine().analyze(confluence) -> DecisionContext`
- `SignalEngine().analyze(decision, context=None) -> SignalResult` where `context` is `SignalContext(trigger_close, structure_break_level, liquidity_level, supplied_entry, reference_time)`. Entry precedence: supplied > trigger_close > structure_break > liquidity. Fallback stop = entry*0.98 (LONG) / *1.02 (SHORT); fallback target = entry +/- risk*2.0.
- `SignalValidationEngine().validate(signal, candles, max_candles=None) -> ValidationResult` — DEFAULT_MAX_CANDLES=50. Reads `signal.direction.value`, `entry_price`, `stop_loss`, `take_profit`. SignalResult works directly. Entry touch: `low <= entry <= high`.
- `PerformanceAnalyticsEngine().analyze(results: Iterable[ValidationResult]) -> PerformanceAnalytics` — never raises on empty input.

## Sprint 11F — Pipeline (added)
- `src/engine/pipeline/historical_pipeline.py`: `HistoricalEvaluationPipeline(config).evaluate(candles) -> PipelineResult`. Walk-forward: at point T, analysis..signal see only `candles[:T+1]`; validation sees `candles[T+1:]`. One-active-signal policy: next signal allowed at `T + 1 + validation.candles_evaluated`. Suppressed points flagged via `PipelineEvaluationPoint.suppressed`.
- `src/engine/pipeline/datasets.py`: `trending_dataset()`, `flat_dataset()`, `minimal_dataset()`.
- `src/engine/models/pipeline.py`: `PipelineResult`, `PipelineEvaluationPoint`.
- `scripts/test_pipeline.py`: demo. `tests/test_pipeline.py`: 30 tests.
- Adapter views (`_AnalysisView`, `_BOSView`, `_CHOCHView`) map `current_bias`→`bias`, `bos_type`→`type`, `choch_type`→`type` for the confluence engine. This is orchestration glue; engines themselves are UNCHANGED (additive integration).

## Coding conventions
- Immutable frozen+slots dataclasses for models. Mutable config dataclasses (LiquidityConfig etc.) need `field(default_factory=...)` when used as defaults in a frozen dataclass.
- No print() inside engines. Type hints everywhere. Composition over inheritance.
- Demos in `scripts/test_*.py` insert `src` onto sys.path.

## Sprint 11G — Evaluation Reporting Layer (added)
- New package `src/engine/reporting/` sits ABOVE the pipeline (dependency: models ← intelligence ← pipeline ← reporting).
- `src/engine/models/evaluation.py`: frozen+slots `PipelineStatistics`, `SignalStatistics`, `TradeStatistics`, `EvaluationReport`.
- `src/engine/reporting/evaluation.py`: `EvaluationReportEngine().analyze(result: PipelineResult, label="", metadata=None) -> EvaluationReport`. Stateless, deterministic, additive. NO existing engine/model modified.
- `EvaluationReport` bundles three independent views: pipeline funnel, signal-generation stats, completed-trade stats. Retains raw `PipelineResult` by reference (`report.result`) for future comparison/robustness/Monte Carlo layers. `label` + `metadata` Mapping[str,str] identify a run.
- Trade stats are DELEGATED, not recomputed: `TradeStatistics.from_performance(result.performance)` projects the existing `PerformanceAnalytics`. No analytics logic duplicated.
- Terminal validation statuses (for `validations_completed`): WIN, LOSS, EXPIRED, AMBIGUOUS, NOT_TRIGGERED. OPEN excluded (window ran out, unresolved).
- `result.signals` tuple = generated (non-suppressed) signals only; suppressed signals live on `PipelineEvaluationPoint.suppressed`. `eligible_signals` (== eligible_decisions) == total_signals + suppressed_signals.
- `scripts/test_evaluation.py`: demo. `tests/test_evaluation.py`: 35 tests.

## Test baseline
- Pre-11F: 290 passed. Post-11F: 320 passed (30 new in tests/test_pipeline.py).
- Post-11G: 355 passed (35 new in tests/test_evaluation.py).