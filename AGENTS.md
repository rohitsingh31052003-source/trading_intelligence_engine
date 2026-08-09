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
- Post-11H: 419 passed (64 new in tests/test_research.py).

## Sprint 11H — Research & Robustness Analysis Layer (added)
- New package `src/engine/research/` sits ABOVE the reporting layer (dependency: models ← intelligence ← pipeline ← reporting ← research).
- `src/engine/models/research.py`: frozen+slots research models. Enums: `MarketRegime` (TRENDING/FLAT/HIGH_VOLATILITY/LOW_VOLATILITY/UNKNOWN), `SegmentationDimension` (DIRECTION/SETUP_QUALITY/CONFIDENCE/RISK_REWARD/REGIME), `ConfidenceBucket` (LOW/MEDIUM/HIGH/VERY_HIGH), `RiskRewardBucket` (LOW_RR/MEDIUM_RR/HIGH_RR). Models: `RegimeStatistics`, `SegmentStatistics`, `SegmentedPerformance`, `ParameterResult`, `ParameterSensitivityReport`, `OutOfSampleReport`, `LeakageCheckResult`, `ResearchReport`.
- `src/engine/research/regime.py`: `MarketRegimeEngine(config).classify(candles) -> MarketRegime`. Deterministic, intentionally simple: normalised ATR (volatility) gates first, then directional efficiency (net move / path length). Uses ONLY the supplied walk-forward slice; never reads future candles. `RegimeConfig` is mutable (window=20, min_history=10, configurable thresholds).
- `src/engine/research/segmentation.py`: `PerformanceSegmentationEngine(config).segment(pairs, dimension, candles=None, evaluation_indices=None) -> SegmentedPerformance` where `pairs` = `(SignalResult, ValidationResult)` tuples. Per-segment metrics DELEGATED to `PerformanceAnalyticsEngine` (no recomputation). Confidence/RR bucket thresholds configurable via mutable `SegmentationConfig`. REGIME dimension needs `candles` + `evaluation_indices` (walk-forward slices `candles[:T+1]`).
- `src/engine/research/sensitivity.py`: `ParameterSensitivityEngine(config).analyze(parameter_name, parameter_values, evaluator) -> ParameterSensitivityReport`. Generic — caller supplies evaluator returning `PerformanceAnalytics` or `PipelineResult` (projected via `.performance` / `.validation_results`). NO automatic overfitting: reports `best_value_by_expectancy` (descriptive, `best_value_descriptive=True`), `median_expectancy`, `expectancy_range`, `profitable_configurations`, `stability_ratio`, `sufficient_data` (>=2 configs). No `optimal`/`deploy` attribute.
- `src/engine/research/out_of_sample.py`: `OutOfSampleEngine(config).evaluate(candles, evaluator) -> OutOfSampleReport`. CHRONOLOGICAL split only (never shuffles); `OutOfSampleConfig.split_ratio` default 0.70. Degradation = oos − in_sample for expectancy/profit_factor/win_rate/drawdown + trade_count_change. Graceful on insufficient data / evaluator exceptions.
- `src/engine/research/leakage.py`: `LeakageAuditEngine(config).audit(result, candles=None) -> LeakageCheckResult`. 5 deterministic checks: (1) analysis at T within candle range, (2) validation begins after T, (3) no future candle supplied to analysis (validated via candles_evaluated ≤ future window), (4) out-of-sample isolation (caller-declared; warning when unconfirmed), (5) chronological indices/timestamps. `passed` iff no failures. Does NOT claim mathematical zero-leakage guarantee.
- `src/engine/research/research.py`: `ResearchEngine(config).analyze(result, candles, pipeline_evaluator=None, parameter_evaluator=None, label, metadata) -> ResearchReport`. Orchestrates all sub-engines; does NOT duplicate trading logic. Conclusions are DESCRIPTIVE only (never "strategy is profitable"); uses wording like "Positive historical expectancy observed in this dataset", "Out-of-sample performance degraded...", "Insufficient trades for reliable inference", "Parameter sensitivity appears high", "No leakage violations were detected by the implemented checks".
- `src/engine/research/__init__.py` re-exports the full public API (third orchestration exception after pipeline/reporting).
- `scripts/test_research.py`: demo producing the Research Robustness Report. `tests/test_research.py`: 64 tests (regime, segmentation, sensitivity, out-of-sample, leakage, ResearchEngine, immutability, end-to-end).
- No existing engine/model modified — additive integration only. All Sprint 11G behavior preserved.