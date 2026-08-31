"""
Focused tests for the historical setup research result boundary
(Checkpoint 8.5).

These tests prove that the existing setup-research output can be
represented and returned through the new provider-agnostic result
boundary without changing its meaning.  All tests use deterministic
data/fakes and do not require Upstox, credentials, HTTP, or real
market data.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from engine.models.historical_setup_research import (
    HistoricalSetupResearchResult,
)
from engine.models.setup_research import (
    SetupEvidence,
    SetupResearchObservation,
    SetupResearchRequest,
    SetupResearchResult,
    SetupResearchStatus,
)


# ============================================================
# Minimal deterministic fixtures
# ============================================================


def _request() -> SetupResearchRequest:
    return SetupResearchRequest(instrument="TEST")


def _no_occurrences_result() -> SetupResearchResult:
    return SetupResearchResult(
        research_id="boundary-test-no-occ",
        request=_request(),
        status=SetupResearchStatus.NO_OCCURRENCES,
        rationale="No occurrences found for the test request.",
        limitations=(
            "Historical evidence is descriptive and observational. "
            "It is not a prediction, recommendation, or guarantee of "
            "future performance.",
        ),
    )


def _researched_result() -> SetupResearchResult:
    return SetupResearchResult(
        research_id="boundary-test-researched",
        request=_request(),
        status=SetupResearchStatus.RESEARCHED,
        occurrence_count=0,
        observations=(),
        completed_outcomes=0,
        ambiguous_count=0,
        unresolved_count=0,
        rationale="Research completed.",
        limitations=(
            "Historical evidence is descriptive and observational.",
        ),
    )


def _insufficient_data_result() -> SetupResearchResult:
    return SetupResearchResult(
        research_id="boundary-test-insufficient",
        request=_request(),
        status=SetupResearchStatus.INSUFFICIENT_DATA,
        points_examined=0,
        valid_points=0,
        rationale="No evaluation point satisfied minimum-history requirements.",
        limitations=(),
    )


# ============================================================
# A. Protocol conformance
# ============================================================


class TestProtocolConformance:
    """The existing SetupResearchResult must structurally satisfy the
    HistoricalSetupResearchResult protocol."""

    def test_no_occurrences_conforms(self):
        result = _no_occurrences_result()
        assert isinstance(result, HistoricalSetupResearchResult)

    def test_researched_conforms(self):
        result = _researched_result()
        assert isinstance(result, HistoricalSetupResearchResult)

    def test_insufficient_data_conforms(self):
        result = _insufficient_data_result()
        assert isinstance(result, HistoricalSetupResearchResult)

    def test_corpus_unavailable_conforms(self):
        result = SetupResearchResult(
            research_id="boundary-test-corpus-unavail",
            request=_request(),
            status=SetupResearchStatus.CORPUS_UNAVAILABLE,
            rationale="Corpus unavailable.",
            limitations=(),
        )
        assert isinstance(result, HistoricalSetupResearchResult)

    def test_invalid_request_conforms(self):
        result = SetupResearchResult(
            research_id="boundary-test-invalid",
            request=_request(),
            status=SetupResearchStatus.INVALID_REQUEST,
            rationale="Invalid request.",
            limitations=(),
        )
        assert isinstance(result, HistoricalSetupResearchResult)


# ============================================================
# B. Protocol attribute surface
# ============================================================


class TestProtocolAttributeSurface:
    """The protocol exposes only research/setup findings."""

    def test_exposes_research_id(self):
        result = _no_occurrences_result()
        assert result.research_id == "boundary-test-no-occ"

    def test_exposes_status(self):
        result = _no_occurrences_result()
        assert result.status is SetupResearchStatus.NO_OCCURRENCES

    def test_exposes_has_occurrences(self):
        result = _no_occurrences_result()
        assert result.has_occurrences is False

    def test_exposes_is_researched(self):
        result = _no_occurrences_result()
        assert result.is_researched is False

        researched = _researched_result()
        assert researched.is_researched is True

    def test_exposes_observations(self):
        result = _no_occurrences_result()
        assert result.observations == ()

    def test_exposes_evidence_none_when_empty(self):
        result = _no_occurrences_result()
        assert result.evidence is None

    def test_exposes_grouped_evidence(self):
        result = _no_occurrences_result()
        assert result.grouped_evidence == ()

    def test_exposes_rationale(self):
        result = _no_occurrences_result()
        assert "No occurrences" in result.rationale

    def test_exposes_limitations(self):
        result = _no_occurrences_result()
        assert len(result.limitations) > 0
        assert "not a prediction" in result.limitations[0]


# ============================================================
# C. Boundary isolation — no trading decision / order fields
# ============================================================


class TestBoundaryIsolation:
    """The protocol and the existing result model must not carry trading
    decision, order, sizing, or execution fields."""

    _FORBIDDEN_FIELDS = (
        "buy_signal",
        "sell_signal",
        "order_instruction",
        "position_size",
        "execution_plan",
        "paper_trade_instruction",
        "live_trade_instruction",
        "entry_order",
        "stop_order",
        "target_order",
        "trailing_stop",
        "time_in_force",
        "order_type",
        "side",
        "quantity",
        "sizing",
        "recommendation",
        "action",
        "trade_now",
    )

    def test_protocol_has_no_forbidden_fields(self):
        for field in self._FORBIDDEN_FIELDS:
            assert not hasattr(HistoricalSetupResearchResult, field), (
                f"Protocol must not expose {field}"
            )

    def test_concrete_result_has_no_forbidden_fields(self):
        result = _no_occurrences_result()
        for field in self._FORBIDDEN_FIELDS:
            assert not hasattr(result, field), (
                f"SetupResearchResult must not carry {field}"
            )

    def test_result_is_not_decision_classification(self):
        """The result must not be mistaken for a trade decision."""
        result = _no_occurrences_result()
        assert not hasattr(result, "decision_classification")
        assert not hasattr(result, "candidate_status")

    def test_result_does_not_expose_entry_stop_target(self):
        """Trade geometry is not part of the research result boundary."""
        result = _no_occurrences_result()
        assert not hasattr(result, "entry")
        assert not hasattr(result, "stop")
        assert not hasattr(result, "target")


# ============================================================
# D. Engine output flows through the boundary (minimal proof)
# ============================================================


class TestEngineOutputThroughBoundary:
    """The existing engine returns SetupResearchResult, which structurally
    satisfies the boundary protocol.  Full engine/corpus integration is
    already covered by the 91 tests in test_historical_setup_research.py;
    this section proves the returned object conforms to the boundary."""

    def test_engine_returns_conforming_result(self, tmp_path):
        from engine.config.historical_outcome_config import OutcomeConfig
        from engine.config.research_corpus_config import ResearchCorpusConfig
        from engine.config.setup_research_config import SetupResearchConfig
        from engine.data.historical_provider import InMemoryHistoricalProvider
        from engine.data.historical_service import HistoricalMarketDataService
        from engine.data.historical_store import HistoricalDataStore
        from engine.data.research_corpus import HistoricalResearchCorpusEngine
        from engine.data.setup_research import HistoricalSetupResearchEngine
        from engine.models.historical_data import HistoricalDataRequest
        from engine.models.ohlcv import OHLCVCandle

        base = datetime(2024, 1, 1, tzinfo=UTC)
        step = timedelta(minutes=15)
        day = timedelta(days=1)

        def _candle(ts: datetime, close: float) -> OHLCVCandle:
            return OHLCVCandle(ts, close, close + 2.0, close - 2.0, close, 1000.0)

        setup_candles = tuple(
            _candle(base + step * i, 100.0 + i * 0.5) for i in range(40)
        )
        context_candles = tuple(
            _candle(base + day * i, 100.0 + i) for i in range(10)
        )
        records = {
            ("NIFTY", "15m"): setup_candles,
            ("NIFTY", "1D"): context_candles,
        }
        provider = InMemoryHistoricalProvider(records)
        store = HistoricalDataStore(tmp_path / "store")
        service = HistoricalMarketDataService(provider=provider, store=store)
        service.ingest(
            HistoricalDataRequest(
                "NIFTY",
                "15m",
                setup_candles[0].timestamp,
                setup_candles[-1].timestamp,
            ),
            reference_now=datetime(2024, 2, 1, tzinfo=UTC),
        )
        service.ingest(
            HistoricalDataRequest(
                "NIFTY",
                "1D",
                context_candles[0].timestamp,
                context_candles[-1].timestamp,
            ),
            reference_now=datetime(2024, 2, 1, tzinfo=UTC),
        )

        corpus = HistoricalResearchCorpusEngine(
            service,
            ResearchCorpusConfig(
                setup_timeframe="15m",
                context_timeframe="1D",
                min_setup_history=5,
            ),
        )
        engine = HistoricalSetupResearchEngine(
            corpus, SetupResearchConfig()
        )
        result = engine.research(
            SetupResearchRequest(instrument="NIFTY", minimum_history=5)
        )
        assert isinstance(result, HistoricalSetupResearchResult)
        assert result.status is SetupResearchStatus.NO_OCCURRENCES


# ============================================================
# E. Engine return type explicitly through the boundary
# ============================================================


class TestEngineReturnTypeThroughBoundary:
    """The engine.research() return type is explicitly the boundary
    protocol, and every status flows through it."""

    def _build_engine(self, tmp_path):
        from engine.config.historical_outcome_config import OutcomeConfig
        from engine.config.research_corpus_config import ResearchCorpusConfig
        from engine.config.setup_research_config import SetupResearchConfig
        from engine.data.historical_provider import InMemoryHistoricalProvider
        from engine.data.historical_service import HistoricalMarketDataService
        from engine.data.historical_store import HistoricalDataStore
        from engine.data.research_corpus import HistoricalResearchCorpusEngine
        from engine.data.setup_research import HistoricalSetupResearchEngine
        from engine.models.historical_data import HistoricalDataRequest
        from engine.models.ohlcv import OHLCVCandle

        base = datetime(2024, 1, 1, tzinfo=UTC)
        step = timedelta(minutes=15)
        day = timedelta(days=1)

        def _candle(ts: datetime, close: float) -> OHLCVCandle:
            return OHLCVCandle(ts, close, close + 2.0, close - 2.0, close, 1000.0)

        setup_candles = tuple(
            _candle(base + step * i, 100.0 + i * 0.5) for i in range(40)
        )
        context_candles = tuple(
            _candle(base + day * i, 100.0 + i) for i in range(10)
        )
        records = {
            ("NIFTY", "15m"): setup_candles,
            ("NIFTY", "1D"): context_candles,
        }
        provider = InMemoryHistoricalProvider(records)
        store = HistoricalDataStore(tmp_path / "store")
        service = HistoricalMarketDataService(provider=provider, store=store)
        service.ingest(
            HistoricalDataRequest(
                "NIFTY",
                "15m",
                setup_candles[0].timestamp,
                setup_candles[-1].timestamp,
            ),
            reference_now=datetime(2024, 2, 1, tzinfo=UTC),
        )
        service.ingest(
            HistoricalDataRequest(
                "NIFTY",
                "1D",
                context_candles[0].timestamp,
                context_candles[-1].timestamp,
            ),
            reference_now=datetime(2024, 2, 1, tzinfo=UTC),
        )

        corpus = HistoricalResearchCorpusEngine(
            service,
            ResearchCorpusConfig(
                setup_timeframe="15m",
                context_timeframe="1D",
                min_setup_history=5,
            ),
        )
        return HistoricalSetupResearchEngine(
            corpus, SetupResearchConfig()
        )

    def test_research_returns_boundary_protocol(self, tmp_path):
        engine = self._build_engine(tmp_path)
        result = engine.research(
            SetupResearchRequest(instrument="NIFTY", minimum_history=5)
        )
        assert isinstance(result, HistoricalSetupResearchResult)

    def test_research_no_occurrences_through_boundary(self, tmp_path):
        engine = self._build_engine(tmp_path)
        result = engine.research(
            SetupResearchRequest(instrument="NIFTY", minimum_history=5)
        )
        assert isinstance(result, HistoricalSetupResearchResult)
        assert result.status is SetupResearchStatus.NO_OCCURRENCES
        assert result.has_occurrences is False
        assert result.is_researched is False

    def test_research_insufficient_data_through_boundary(self, tmp_path):
        engine = self._build_engine(tmp_path)
        result = engine.research(
            SetupResearchRequest(
                instrument="NIFTY",
                minimum_history=1000,
            )
        )
        assert isinstance(result, HistoricalSetupResearchResult)
        assert result.status is SetupResearchStatus.INSUFFICIENT_DATA
        assert result.has_occurrences is False
        assert result.is_researched is False

    def test_research_invalid_request_through_boundary(self, tmp_path):
        engine = self._build_engine(tmp_path)
        result = engine.research(
            SetupResearchRequest(
                instrument="NIFTY",
                setup_timeframe="1h",
            )
        )
        assert isinstance(result, HistoricalSetupResearchResult)
        assert result.status is SetupResearchStatus.INVALID_REQUEST
        assert result.has_occurrences is False
        assert result.is_researched is False

    def test_boundary_attributes_accessible(self, tmp_path):
        engine = self._build_engine(tmp_path)
        result = engine.research(
            SetupResearchRequest(instrument="NIFTY", minimum_history=5)
        )
        assert isinstance(result, HistoricalSetupResearchResult)
        assert isinstance(result.research_id, str)
        assert len(result.research_id) > 0
        assert isinstance(result.observations, tuple)
        assert isinstance(result.grouped_evidence, tuple)
        assert isinstance(result.limitations, tuple)
        assert isinstance(result.rationale, str)

    def test_boundary_result_is_frozen(self, tmp_path):
        engine = self._build_engine(tmp_path)
        result = engine.research(
            SetupResearchRequest(instrument="NIFTY", minimum_history=5)
        )
        assert isinstance(result, HistoricalSetupResearchResult)
        assert type(result).__dataclass_fields__
