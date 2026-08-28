"""
Deterministic tests for the Upstox historical OHLCV provider
(``engine.data.historical_provider.UpstoxHistoricalDataProvider``).

All tests are network-free: the provider's HTTP layer is injected with a
fake ``urlopen`` that receives the actual ``urllib.request.Request``
object (and token), so the URL/header construction code path is the
SAME code exercised by production calls. No real Upstox token is
required by pytest.

The task is PROVIDER-INTEGRATION ONLY: the authoritative decision
architecture, setup detection, geometry, trade plans, paper trading,
live behavior, Phase 6C/6D/6E/6F logic, the historical-data contract and
the HistoricalDataStore schema are never modified.
"""

from __future__ import annotations

import json as _json
from datetime import UTC, datetime, timedelta, timezone

import pytest

from engine.data.historical_provider import (
    UPSTOX_TOKEN_ENV,
    UPSTOX_UNIT_DAYS,
    UPSTOX_UNIT_MINUTES,
    UPSTOX_USER_AGENT,
    UpstoxHistoricalDataProvider,
    _upstox_monthly_chunks,
    _upstox_timestamp_to_utc,
)
from engine.data.historical_service import HistoricalMarketDataService
from engine.data.historical_store import HistoricalDataStore
from engine.data.historical_times import canonical_timeframe
from engine.models.historical_data import (
    HistoricalDataRequest,
    HistoricalIngestionStatus,
    ProviderResponseStatus,
)

_TOKEN = "upstox-test-token-abc123"

_START = datetime(2022, 12, 1, tzinfo=UTC)
_END = datetime(2023, 1, 1, tzinfo=UTC)

#: Upstox returns timezone-aware +05:30 (IST) timestamps.
_IST = timezone(timedelta(hours=5, minutes=30))


# ============================================================
# FIXTURE HELPERS
# ============================================================


def _ist(iso: str) -> datetime:
    return datetime.fromisoformat(iso).astimezone(_IST)


def _row(ts: datetime, open_p=2500.0, high=2550.0, low=2490.0,
         close=2520.0, volume=100000.0, oi=0) -> list:
    """One Upstox candle row ``[ts, o, h, l, c, v, oi]`` (reverse-order
    as returned by the real API)."""
    return [ts.isoformat(), open_p, high, low, close, volume, oi]


def _payload(rows) -> dict:
    """A successful Upstox response payload."""
    return {"status": "success", "candles": rows, "meta": {}}


def _request(instrument="RELIANCE", timeframe="15m", start=_START, end=_END):
    return HistoricalDataRequest(instrument, timeframe, start, end)


class _FakeUrlopen:
    """Fake urllib ``urlopen`` receiving ``(request, token)``.

    Records every request (URL + headers + token) for assertions and
    serves a deterministic payload per URL (customizable via
    ``handlers``).
    """

    def __init__(self, handlers=None):
        self.calls: list[tuple[str, dict, str]] = []  # (url, headers, token)
        self.handlers = dict(handlers) if handlers else {}

    def __call__(self, request, token):
        self.calls.append((request.full_url, dict(request.headers), token))
        payload = self.handlers.get(request.full_url)
        if payload is None:
            payload = _payload([])  # empty chunk by default
        if callable(payload):
            return payload(request)
        if isinstance(payload, Exception):
            raise payload
        return _json.dumps(payload).encode("utf-8")


def _make_provider(handlers=None, **kw) -> UpstoxHistoricalDataProvider:
    fake = _FakeUrlopen(handlers or {})
    provider = UpstoxHistoricalDataProvider(token=_TOKEN, urlopen=fake, **kw)
    provider._fake = fake  # test access to recorded calls
    return provider


def _chunk_url(from_date: str, to_date: str) -> str:
    """The exact Upstox URL the provider builds for a monthly chunk.

    ``urllib.parse.quote(instrument_key, safe="|")`` keeps the pipe
    character unencoded because the Upstox API path format carries the
    instrument key verbatim (``NSE_EQ|INE002A01018``).
    """
    return (
        "https://api.upstox.com/v3/historical-candle/"
        f"NSE_EQ|INE002A01018/minutes/15/{to_date}/{from_date}"
    )


def _reliance_15m_rows(start_date: str, n: int, step_minutes: int = 15) -> list:
    """Deterministic reverse-chronological Upstox rows for a month
    (the API returns candles newest-first)."""
    start = _ist(f"{start_date}T09:15:00+05:30")
    rows = []
    for i in range(n):
        ts = start + timedelta(minutes=step_minutes * i)
        o = 2400.0 + i
        rows.append(_row(
            ts,
            open_p=o, high=o + 10.0, low=o - 10.0,
            close=o + 5.0, volume=100000.0 + i,
        ))
    return list(reversed(rows))


# ============================================================
# 1-2. CONSTRUCTION + NAME
# ============================================================


class TestConstructionAndName:
    def test_provider_constructed_with_token(self):
        p = UpstoxHistoricalDataProvider(token=_TOKEN)
        assert p.provider_name == "upstox-historical"

    def test_provider_constructed_with_env(self, monkeypatch):
        monkeypatch.setenv(UPSTOX_TOKEN_ENV, _TOKEN)
        p = UpstoxHistoricalDataProvider()
        assert p.provider_name == "upstox-historical"
        assert p.is_available() is True

    def test_default_instrument_key_map_deterministic(self):
        a = UpstoxHistoricalDataProvider()
        b = UpstoxHistoricalDataProvider()
        assert a._instrument_key_map == b._instrument_key_map

    def test_default_map_reliance_present(self):
        p = UpstoxHistoricalDataProvider()
        assert p._instrument_key_map["RELIANCE"] == "NSE_EQ|INE002A01018"


# ============================================================
# 3-4. AVAILABILITY
# ============================================================


class TestAvailability:
    def test_unavailable_when_token_absent(self, monkeypatch):
        monkeypatch.delenv(UPSTOX_TOKEN_ENV, raising=False)
        p = UpstoxHistoricalDataProvider()
        assert p.is_available() is False

    def test_available_when_token_present(self, monkeypatch):
        monkeypatch.setenv(UPSTOX_TOKEN_ENV, "env-token")
        p = UpstoxHistoricalDataProvider()
        assert p.is_available() is True

    def test_available_when_token_ctor_override(self):
        assert UpstoxHistoricalDataProvider(
            token=_TOKEN,
        ).is_available() is True

    def test_has_token_mirrors_availability(self):
        assert UpstoxHistoricalDataProvider(token=_TOKEN).has_token() is True

    def test_fetch_without_token_returns_error(self, monkeypatch):
        monkeypatch.delenv(UPSTOX_TOKEN_ENV, raising=False)
        p = UpstoxHistoricalDataProvider()
        response = p.fetch(_request())
        assert response.status is ProviderResponseStatus.ERROR
        assert UPSTOX_TOKEN_ENV in response.reason
        assert _TOKEN not in response.reason  # token never leaked


# ============================================================
# 5-6. TIMEFRAME SUPPORT
# ============================================================


class TestTimeframeSupport:
    def test_supports_15m(self):
        p = UpstoxHistoricalDataProvider(token=_TOKEN)
        assert p.supports("RELIANCE", "15m") is True
        assert p.supports("RELIANCE", "15M") is True  # alias canonicalized

    def test_unsupported_timeframe(self):
        p = UpstoxHistoricalDataProvider(token=_TOKEN)
        for timeframe in ("30m", "1h", "5m", "bogus"):
            assert p.supports("RELIANCE", timeframe) is False

    def test_fetch_unsupported_timeframe_error(self):
        p = _make_provider()
        response = p.fetch(_request(timeframe="30m"))
        assert response.status is ProviderResponseStatus.UNSUPPORTED
        assert p._fake.calls == []  # never sent


# ============================================================
# 7-8. INSTRUMENT-KEY RESOLUTION
# ============================================================


class TestInstrumentKeyResolution:
    def test_reliance_resolves(self):
        p = UpstoxHistoricalDataProvider(token=_TOKEN)
        assert p.resolve_instrument_key("RELIANCE") == "NSE_EQ|INE002A01018"

    def test_unknown_instrument_raises(self):
        p = UpstoxHistoricalDataProvider(token=_TOKEN)
        with pytest.raises(KeyError):
            p.resolve_instrument_key("NOTANINSTRUMENT")

    def test_unknown_instrument_fetch_reports_unsupported(self):
        p = _make_provider()
        response = p.fetch(_request(instrument="NOTANINSTRUMENT"))
        assert response.status is ProviderResponseStatus.UNSUPPORTED
        assert "unknown instrument" in response.reason
        assert p._fake.calls == []  # never sent

    def test_yahoo_symbol_not_a_valid_instrument_key(self):
        # RELIANCE.NS must NOT silently fall through to the Upstox URL.
        p = UpstoxHistoricalDataProvider(token=_TOKEN)
        with pytest.raises(KeyError):
            p.resolve_instrument_key("RELIANCE.NS")

    def test_instrument_key_map_override(self):
        p = UpstoxHistoricalDataProvider(
            token=_TOKEN, instrument_key_map={"TCS": "NSE_EQ|INE467B01029"},
        )
        assert p.resolve_instrument_key("TCS") == "NSE_EQ|INE467B01029"


# ============================================================
# 9-10. URL + AUTHORIZATION CONSTRUCTION
# ============================================================


class TestUrlAndAuth:
    def _ok_provider(self):
        rows = _reliance_15m_rows("2022-12-01", 1)
        return _make_provider({
            _chunk_url("2022-12-01", "2023-01-01"): _payload(rows),
        })

    def test_url_construction_exact(self):
        p = self._ok_provider()
        response = p.fetch(_request())
        assert response.status is ProviderResponseStatus.OK
        expected = _chunk_url("2022-12-01", "2023-01-01")
        assert p._fake.calls[0][0] == expected

    def test_authorization_header_correct(self):
        p = self._ok_provider()
        p.fetch(_request())
        headers = p._fake.calls[0][1]
        assert headers["Authorization"] == f"Bearer {_TOKEN}"
        assert headers["Accept"] == "application/json"

    def test_token_never_leaks_into_url_or_reason(self):
        p = self._ok_provider()
        response = p.fetch(_request())
        for url, headers, token in p._fake.calls:
            assert _TOKEN not in url
            assert "Authorization" in headers
        assert _TOKEN not in response.reason

    def test_fetch_uses_get_method(self):
        p = self._ok_provider()
        p.fetch(_request())
        # The Request object constructed in _fetch_one uses GET; the
        # fake only sees the URL/headers, so implicit method is GET.
        assert p._fake.calls[0][1]["Authorization"].startswith("Bearer ")

    def test_user_agent_header_sent(self):
        # REGRESSION (live failure): the Cloudflare edge in front of
        # api.upstox.com rejects urllib's default Python-urllib/x.y
        # User-Agent with HTTP 403 (Error 1010 browser_signature_banned),
        # so _fetch_one MUST send an explicit User-Agent header.
        # urllib canonicalizes header names (``User-agent``) on the wire.
        p = self._ok_provider()
        p.fetch(_request())
        headers = p._fake.calls[0][1]
        ua = {k.lower(): v for k, v in headers.items()}.get("user-agent")
        assert ua is not None
        assert ua == UPSTOX_USER_AGENT
        assert "Python-urllib/3" not in ua

    def test_default_user_agent_constant_is_explicit(self):
        # The constant must never fall back to urllib's blocked default
        # (identified by the canonical ``Python-urllib/`` signature).
        assert UPSTOX_USER_AGENT
        assert "Python-urllib/" not in UPSTOX_USER_AGENT


# ============================================================
# 11-13. PARSING / CONVERSION / TIMESTAMP NORMALIZATION
# ============================================================

#: The REAL Upstox V3 response body: the candle list is nested under
#: ``data.candles`` (verified against the official V3 documentation and
#: the real API — rows ``[timestamp, open, high, low, close, volume,
#: open_interest]``). The 7-th element (open_interest) is ignored by the
#: canonical OHLCV contract. ``2022-12-30T15:15:00+05:30`` is the exact
#: real candle timestamp furnished during live verification.
def _v3_payload(rows) -> dict:
    return {"status": "success", "data": {"candles": rows}}


class TestParsingAndNormalization:
    def test_parse_real_v3_response_shape(self):
        rows = [_row(_ist("2022-12-30T15:15:00+05:30"))]
        candles = UpstoxHistoricalDataProvider._parse_candles(_v3_payload(rows))
        assert len(candles) == 1
        c = candles[0]
        assert c.timestamp == datetime(2022, 12, 30, 9, 45, tzinfo=UTC)

    def test_parse_legacy_flat_candles_backward_compatible(self):
        # The provider historically accepted a flat ``candles`` list;
        # that lenient location remains supported so existing callers /
        # imports of the pre-V3 shape keep working.
        rows = [_row(_ist("2022-12-30T15:15:00+05:30"))]
        candles = UpstoxHistoricalDataProvider._parse_candles(
            {"status": "success", "candles": rows},
        )
        assert len(candles) == 1

    def test_parse_missing_data_and_candles_rejected(self):
        # Neither ``data.candles`` nor a top-level ``candles`` list: the
        # payload is malformed and must raise, never silently EMPTY.
        with pytest.raises(ValueError):
            UpstoxHistoricalDataProvider._parse_candles(
                {"status": "success", "data": {}},
            )

    def test_fetch_real_v3_shape_is_not_empty(self):
        # REGRESSION (the live failure): a successful Upstox V3
        # response containing real candles must NOT become EMPTY.
        rows = _reliance_15m_rows("2022-12-01", 5)
        p = _make_provider({
            _chunk_url("2022-12-01", "2023-01-01"): _v3_payload(rows),
        })
        response = p.fetch(_request())
        assert response.status is ProviderResponseStatus.OK
        assert len(response.candles) == 5
        assert [c.timestamp for c in response.candles] == sorted(
            [c.timestamp for c in response.candles],
        )

    def test_fetch_real_v3_single_candle_timestamp_normalized(self):
        # The exact real candle timestamp supplied during live Upstox
        # verification must normalize to timezone-aware UTC.
        rows = [_row(_ist("2022-12-30T15:15:00+05:30"))]
        p = _make_provider({
            _chunk_url("2022-12-01", "2023-01-01"): _v3_payload(rows),
        })
        response = p.fetch(_request())
        assert response.status is ProviderResponseStatus.OK
        assert len(response.candles) == 1
        assert response.candles[0].timestamp == datetime(
            2022, 12, 30, 9, 45, tzinfo=UTC,
        )
        assert response.candles[0].timestamp.tzinfo is not None


class TestParsingAndNormalizationLegacy:
    def test_parse_success_payload(self):
        rows = [_row(_ist("2022-12-01T09:15:00+05:30"))]
        candles = UpstoxHistoricalDataProvider._parse_candles(_payload(rows))
        assert len(candles) == 1
        c = candles[0]
        assert c.timestamp == datetime(2022, 12, 1, 3, 45, tzinfo=UTC)
        assert (c.open, c.high, c.low, c.close, c.volume) == (
            2500.0, 2550.0, 2490.0, 2520.0, 100000.0,
        )

    def test_open_interest_ignored(self):
        rows = [_row(_ist("2022-12-01T09:15:00+05:30"), oi=999999)]
        candles = UpstoxHistoricalDataProvider._parse_candles(_payload(rows))
        assert len(candles) == 1  # oi has no canonical field, ignored

    def test_plus_530_timestamp_normalized_to_utc(self):
        ts = _ist("2022-01-03T09:15:00+05:30")
        normalized = _upstox_timestamp_to_utc(ts)
        assert normalized == datetime(2022, 1, 3, 3, 45, tzinfo=UTC)

    def test_non_datetime_normalizer_none(self):
        assert _upstox_timestamp_to_utc(None) is None
        assert _upstox_timestamp_to_utc(12345) is None
        assert _upstox_timestamp_to_utc("not a timestamp") is None

    def test_naive_timestamp_rejected_by_normalizer(self):
        naive = datetime(2022, 1, 3, 9, 15)  # noqa: DTZ001 - deliberate
        assert _upstox_timestamp_to_utc(naive) is None

    def test_candle_ohlcv_values_preserved(self):
        rows = [_row(_ist("2022-12-01T09:15:00+05:30"), 1.5, 3.5, 0.5, 2.5, 7.5)]
        candles = UpstoxHistoricalDataProvider._parse_candles(_payload(rows))
        assert (candles[0].open, candles[0].high, candles[0].low,
                candles[0].close, candles[0].volume) == (1.5, 3.5, 0.5, 2.5, 7.5)


# ============================================================
# 14-16. REVERSE ORDER NORMALIZATION + MONTHLY CHUNKING + MERGE
# ============================================================


class TestChunkingAndMerge:
    def test_reverse_chronological_response_becomes_ordered(self):
        rows = _reliance_15m_rows("2022-12-01", 5)
        assert rows[0][0] > rows[-1][0]  # API returns newest first
        p = _make_provider({
            _chunk_url("2022-12-01", "2023-01-01"): _payload(rows),
        })
        response = p.fetch(_request())
        ts = [c.timestamp for c in response.candles]
        assert ts == sorted(ts)
        assert len(response.candles) == 5

    def test_monthly_chunking_year(self):
        chunks = _upstox_monthly_chunks(
            datetime(2022, 1, 1, tzinfo=UTC), datetime(2023, 1, 1, tzinfo=UTC),
        )
        assert len(chunks) == 12
        assert chunks[0] == (datetime(2022, 1, 1, tzinfo=UTC),
                             datetime(2022, 2, 1, tzinfo=UTC))
        assert chunks[-1] == (datetime(2022, 12, 1, tzinfo=UTC),
                              datetime(2023, 1, 1, tzinfo=UTC))

    def test_monthly_chunking_two_months(self):
        chunks = _upstox_monthly_chunks(_START, _END)
        assert len(chunks) == 1  # single month range -> one chunk

    def test_mid_month_start_not_widened(self):
        chunks = _upstox_monthly_chunks(
            datetime(2022, 12, 15, tzinfo=UTC), datetime(2023, 1, 1, tzinfo=UTC),
        )
        assert len(chunks) == 1
        assert chunks[0][0] == datetime(2022, 12, 15, tzinfo=UTC)

    def test_chunks_never_extend_beyond_end(self):
        chunks = _upstox_monthly_chunks(
            datetime(2022, 1, 15, tzinfo=UTC), datetime(2022, 3, 10, tzinfo=UTC),
        )
        assert chunks[-1][1] == datetime(2022, 3, 10, tzinfo=UTC)

    def test_chunk_bounds_deterministic(self):
        a = _upstox_monthly_chunks(_START, datetime(2024, 1, 1, tzinfo=UTC))
        b = _upstox_monthly_chunks(_START, datetime(2024, 1, 1, tzinfo=UTC))
        assert a == b

    def test_multiple_monthly_responses_combined(self):
        jan = _reliance_15m_rows("2022-12-01", 3)
        # February rows start 2023-01-02 (within the requested window).
        feb = [
            _row(_ist("2023-01-02T09:15:00+05:30"), open_p=2600.0,
                 high=2610.0, low=2590.0, close=2605.0),
            _row(_ist("2023-01-02T09:30:00+05:30"), open_p=2605.0,
                 high=2615.0, low=2595.0, close=2610.0),
        ]
        p = _make_provider({
            _chunk_url("2022-12-01", "2023-01-01"): _payload(jan),
            _chunk_url("2023-01-01", "2023-02-01"): _payload(feb),
        })
        response = p.fetch(_request(end=datetime(2023, 2, 1, tzinfo=UTC)))
        assert len(response.candles) == 5
        ts = [c.timestamp for c in response.candles]
        assert ts == sorted(ts)
        assert p._fake.calls[0][0] == _chunk_url("2022-12-01", "2023-01-01")

    def test_chunk_error_partial_not_fabricated(self):
        jan = _reliance_15m_rows("2022-12-01", 3)
        p = _make_provider({
            _chunk_url("2022-12-01", "2023-01-01"): _payload(jan),
            _chunk_url("2023-01-01", "2023-02-01"): RuntimeError("boom"),
        })
        response = p.fetch(_request(end=datetime(2023, 2, 1, tzinfo=UTC)))
        # January data kept; February failure reported honestly.
        assert response.status is ProviderResponseStatus.OK
        assert len(response.candles) == 3
        assert "1 chunk error" in response.reason

    def test_all_chunks_fail_is_error(self):
        p = _make_provider({  # every chunk raises
            _chunk_url("2022-12-01", "2023-01-01"): RuntimeError("boom"),
        })
        response = p.fetch(_request())
        assert response.status is ProviderResponseStatus.ERROR
        assert "provider error" in response.reason


# ============================================================
# 17. DEDUP ACROSS CHUNK BOUNDARIES
# ============================================================


class TestDuplicateAcrossChunks:
    def test_duplicate_timestamps_handled_by_canonical_validation(self):
        rows_a = [_row(_ist("2022-12-30T09:15:00+05:30")),
                  _row(_ist("2022-12-30T09:30:00+05:30"))]
        # Same candles returned again (overlapping chunk boundary).
        rows_b = [_row(_ist("2022-12-30T09:15:00+05:30")),
                  _row(_ist("2022-12-31T09:15:00+05:30"))]
        p = _make_provider({
            _chunk_url("2022-12-01", "2023-01-01"): _payload(rows_a),
            _chunk_url("2023-01-01", "2023-02-01"): _payload(rows_b),
        })
        response = p.fetch(_request(end=datetime(2023, 2, 1, tzinfo=UTC)))
        # Provider merges identical timestamps before validation; a
        # duplicate row surviving into the service is handled by the
        # canonical store merge (idempotent).
        assert len(response.candles) == 3
        ts = [c.timestamp for c in response.candles]
        assert len(ts) == len(set(ts))


# ============================================================
# 18-21. ERROR / MALFORMED / EMPTY / MISSING TOKEN
# ============================================================


class TestErrorHandling:
    def test_api_error_status(self):
        p = _make_provider({
            _chunk_url("2022-12-01", "2023-01-01"):
                lambda req: _json.dumps(
                    {"status": "error", "candles": [], "errors": [{"message": "bad"}]},
                ).encode(),
        })
        response = p.fetch(_request())
        assert response.status is ProviderResponseStatus.ERROR
        assert "provider error" in response.reason

    def test_http_error_raises_internal_reported(self):
        p = _make_provider({
            _chunk_url("2022-12-01", "2023-01-01"): RuntimeError("boom"),
        })
        response = p.fetch(_request())
        assert response.status is ProviderResponseStatus.ERROR
        assert "provider error" in response.reason

    def test_malformed_json_reported(self):
        p = _make_provider({
            _chunk_url("2022-12-01", "2023-01-01"):
                lambda req: b"{not json",
        })
        response = p.fetch(_request())
        assert response.status is ProviderResponseStatus.ERROR

    def test_malformed_candle_shape_reported(self):
        p = _make_provider({
            _chunk_url("2022-12-01", "2023-01-01"):
                _payload([["2022-12-01T09:15:00+05:30", 1, 2]]),  # too short
        })
        response = p.fetch(_request())
        assert response.status is ProviderResponseStatus.ERROR

    def test_non_numeric_ohlc_reported(self):
        p = _make_provider({
            _chunk_url("2022-12-01", "2023-01-01"):
                _payload([["2022-12-01T09:15:00+05:30", "x", 2, 1, 1.5, 100]]),
        })
        response = p.fetch(_request())
        assert response.status is ProviderResponseStatus.ERROR

    def test_nan_ohlc_reported(self):
        p = _make_provider({
            _chunk_url("2022-12-01", "2023-01-01"):
                _payload([["2022-12-01T09:15:00+05:30", float("nan"), 2, 1, 1.5, 100]]),
        })
        response = p.fetch(_request())
        assert response.status is ProviderResponseStatus.ERROR

    def test_empty_response_returns_empty(self):
        p = _make_provider({
            _chunk_url("2022-12-01", "2023-01-01"): _payload([]),
        })
        response = p.fetch(_request())
        assert response.status is ProviderResponseStatus.EMPTY
        assert response.candles == ()

    def test_decode_body_plain(self):
        import gzip as _gzip
        from engine.data.historical_provider import (
            UpstoxHistoricalDataProvider as _P,
        )
        assert _P._decode_body(b'{"a":1}', None) == '{"a":1}'
        assert _P._decode_body("plain str", None) == "plain str"
        assert _P._decode_body(_gzip.compress(b'{"b":2}'), "gzip") == '{"b":2}'
        assert _P._decode_body(b'{"c":3}', "identity") == '{"c":3}'

    def test_decode_body_invalid_gzip(self):
        from engine.data.historical_provider import (
            UpstoxHistoricalDataProvider as _P,
        )
        with pytest.raises(ValueError):
            _P._decode_body(b"not-gzip-bytes", "gzip")
        with pytest.raises(ValueError):
            _P._decode_body(None, None)


# ============================================================
# 22. RANGE SEMANTICS
# ============================================================


class TestRangeSemantics:
    def test_request_end_not_exceeded(self):
        rows = [_row(datetime(2023, 1, 1, 12, 0, tzinfo=UTC))]  # >= end
        p = _make_provider({
            _chunk_url("2022-12-01", "2023-01-01"): _payload(rows),
        })
        response = p.fetch(_request())
        for c in response.candles:
            assert c.timestamp <= _END  # never beyond requested end

    def test_no_lookahead_parameter(self):
        import inspect
        sig = inspect.signature(UpstoxHistoricalDataProvider.fetch)
        assert not ({ "future", "future_candles", "lookahead" }
                    & set(sig.parameters))

    def test_all_months_chunked_not_one_giant_request(self):
        start = datetime(2022, 1, 1, tzinfo=UTC)
        end = datetime(2024, 1, 1, tzinfo=UTC)
        p = _make_provider()
        p.fetch(_request(start=start, end=end))
        assert len(p._fake.calls) == 24  # 24 monthly chunks, not 1 giant
        for url, _, _ in p._fake.calls:
            assert url.count("/") > 0  # every call is a bounded URL

    def _url_dates(self, url: str) -> tuple[str, str]:
        """Extract (to_date, from_date) from the URL path. The Upstox
        endpoint is ``/historical-candle/{instrument_key}/{unit}/
        {interval}/{to_date}/{from_date}`` so the two final path
        segments are the day labels."""
        parts = url.rstrip("/").split("/")
        return parts[-2], parts[-1]  # (to_date, from_date)

    def test_url_date_order_to_ge_from_single_month(self):
        # December 2022 (the live-verified window) must produce a URL
        # whose ``to_date`` is 2023-01-01 (clamped to the requested end)
        # and whose ``from_date`` is 2022-12-01, with to >= from.
        start = datetime(2022, 12, 1, tzinfo=UTC)
        end = datetime(2023, 1, 1, tzinfo=UTC)
        p = _make_provider({
            _chunk_url("2022-12-01", "2023-01-01"): _v3_payload([]),
        })
        p.fetch(_request(start=start, end=end))
        assert len(p._fake.calls) == 1
        to_date, from_date = self._url_dates(p._fake.calls[0][0])
        assert from_date == "2022-12-01"
        assert to_date == "2023-01-01"
        assert to_date >= from_date  # never reversed

    def test_url_date_order_multi_month_every_chunk(self):
        # For several month chunks spanning 2022-12-15 .. 2023-02-10,
        # every generated URL must satisfy ``to_date >= from_date``.
        start = datetime(2022, 12, 15, tzinfo=UTC)
        end = datetime(2023, 2, 10, tzinfo=UTC)
        p = _make_provider()
        p.fetch(_request(start=start, end=end))
        # 2022-12-15..2023-01-01 and 2023-01-01..2023-02-01 and
        # 2023-02-01..2023-02-10 -> three bounded monthly chunks.
        assert len(p._fake.calls) == 3
        for url, _, _ in p._fake.calls:
            to_date, from_date = self._url_dates(url)
            assert to_date >= from_date, (to_date, from_date)
            assert from_date >= "2022-12-15"
            assert to_date <= "2023-02-10"

    def test_url_date_order_one_month_mid_month(self):
        start = datetime(2022, 12, 20, tzinfo=UTC)
        end = datetime(2023, 1, 1, tzinfo=UTC)
        p = _make_provider({
            _chunk_url("2022-12-20", "2023-01-01"): _v3_payload([]),
        })
        p.fetch(_request(start=start, end=end))
        assert len(p._fake.calls) == 1
        to_date, from_date = self._url_dates(p._fake.calls[0][0])
        assert from_date == "2022-12-20"
        assert to_date == "2023-01-01"
        assert to_date >= from_date


# ============================================================
# 23. VALIDATOR STILL REJECTS NAIVE (unchanged contract)
# ============================================================


class TestValidatorUnchanged:
    def test_existing_validator_rejects_manually_naive(self):
        from engine.data.historical_validation import HistoricalDataValidator
        from engine.models.ohlcv import OHLCVCandle

        naive = OHLCVCandle(
            timestamp=datetime(2022, 12, 1, 9, 15),  # naive  # noqa: DTZ001
            open=1, high=2, low=0.5, close=1.5, volume=100,
        )
        accepted, issues = HistoricalDataValidator.validate(
            (naive,), instrument="RELIANCE", timeframe="15m",
            reference_now=datetime(2023, 1, 2, tzinfo=UTC),
        )
        assert accepted == ()
        assert any(i.error.name == "NAIVE_TIMESTAMP" for i in issues)


# ============================================================
# 24-26. SERVICE + STORE INTEGRATION / IDEMPOTENCY
# ============================================================


class TestServiceStoreIntegration:
    def test_ingest_via_service_store(self, tmp_path):
        rows = _reliance_15m_rows("2022-12-01", 5)
        p = _make_provider({
            _chunk_url("2022-12-01", "2023-01-01"): _payload(rows),
        })
        store = HistoricalDataStore(tmp_path)
        svc = HistoricalMarketDataService(provider=p, store=store)
        result = svc.ingest(_request(), reference_now=_END)
        assert result.fetch.status is HistoricalIngestionStatus.AVAILABLE
        assert result.store.records_added == 5
        assert result.store.total_candles == 5
        assert result.store.reload_verified is True
        _ = result

    def test_provenance_provider_name(self, tmp_path):
        rows = _reliance_15m_rows("2022-12-01", 2)
        p = _make_provider({
            _chunk_url("2022-12-01", "2023-01-01"): _payload(rows),
        })
        svc = HistoricalMarketDataService(
            provider=p, store=HistoricalDataStore(tmp_path),
        )
        result = svc.ingest(_request(), reference_now=_END)
        assert result.fetch.provenance.provider == "upstox-historical"
        assert result.fetch.provenance.records_received == 2
        assert result.fetch.provenance.records_rejected == 0

    def test_service_threads_reference_now(self, tmp_path):
        rows = _reliance_15m_rows("2022-12-01", 3)
        p = _make_provider({
            _chunk_url("2022-12-01", "2023-01-01"): _payload(rows),
        })
        svc = HistoricalMarketDataService(
            provider=p, store=HistoricalDataStore(tmp_path),
        )
        result = svc.ingest(_request(), reference_now=_END)
        assert result.fetch.status is HistoricalIngestionStatus.AVAILABLE

    def test_stored_candles_round_trip(self, tmp_path):
        rows = _reliance_15m_rows("2022-12-01", 4)
        p = _make_provider({
            _chunk_url("2022-12-01", "2023-01-01"): _payload(rows),
        })
        store = HistoricalDataStore(tmp_path)
        svc = HistoricalMarketDataService(provider=p, store=store)
        svc.ingest(_request(), reference_now=_END)
        loaded = svc.load_historical("RELIANCE", "15m")
        assert loaded.count == 4
        assert loaded.first_timestamp == datetime(2022, 12, 1, 3, 45, tzinfo=UTC)
        assert [c.timestamp for c in loaded.candles] == sorted(
            [c.timestamp for c in loaded.candles],
        )
        for c in loaded.candles:
            assert c.timestamp.tzinfo is not None

    def test_idempotent_reingestion(self, tmp_path):
        rows = _reliance_15m_rows("2022-12-01", 5)
        p = _make_provider({
            _chunk_url("2022-12-01", "2023-01-01"): _payload(rows),
        })
        store = HistoricalDataStore(tmp_path)
        svc = HistoricalMarketDataService(provider=p, store=store)
        svc.ingest(_request(), reference_now=_END)
        second = svc.ingest(_request(), reference_now=_END)
        assert second.store.records_added == 0
        assert second.store.records_existing == 5
        assert second.store.total_candles == 5
        assert len(store.load_candles("RELIANCE", "15m")) == 5
        provenance = store.load_provenance("RELIANCE", "15m")
        assert len(provenance) == 2  # both operations auditable

    def test_store_path_canonical_structure(self, tmp_path):
        rows = _reliance_15m_rows("2022-12-01", 2)
        p = _make_provider({
            _chunk_url("2022-12-01", "2023-01-01"): _payload(rows),
        })
        store = HistoricalDataStore(tmp_path)
        svc = HistoricalMarketDataService(provider=p, store=store)
        svc.ingest(_request(), reference_now=_END)
        # Canonical existing storage layout:
        assert (tmp_path / "RELIANCE" / "15m" / "candles.json").exists()


# ============================================================
# 27-28. NO CHANGE TO YAHOO / DETERMINISTIC PROVIDERS
# ============================================================


class TestNoRegression:
    def test_yahoo_provider_unchanged(self):
        from engine.data.historical_provider import YahooHistoricalDataProvider
        p = YahooHistoricalDataProvider()
        assert p.provider_name == "yahoo-historical"
        assert p.resolve_symbol("RELIANCE") == "RELIANCE.NS"
        assert p.resolve_symbol("NIFTY") == "^NSEI"

    def test_deterministic_provider_unchanged(self):
        from engine.data.historical_provider import (
            DeterministicLocalHistoricalProvider,
            InMemoryHistoricalProvider,
        )
        assert DeterministicLocalHistoricalProvider().provider_name == \
            "local-deterministic"
        assert InMemoryHistoricalProvider().provider_name == "in-memory-import"
        d = DeterministicLocalHistoricalProvider()
        assert d.supports("RELIANCE", "15m") is True

    def test_yahoo_symbol_helpers_still_available(self):
        from datetime import timedelta, timezone

        from engine.data.historical_provider import _yahoo_timestamp_to_utc
        naive = datetime(2026, 7, 1)  # noqa: DTZ001 - deliberate naive input
        assert _yahoo_timestamp_to_utc(naive) == datetime(2026, 7, 1, tzinfo=UTC)
        tz = timezone(timedelta(hours=5, minutes=30))
        aware = datetime(2026, 7, 1, 9, 15, tzinfo=tz)
        assert _yahoo_timestamp_to_utc(aware) == datetime(
            2026, 7, 1, 3, 45, tzinfo=UTC,
        )

    def test_canonical_timeframe_unaltered(self):
        assert canonical_timeframe("15m") == "15m"
        assert canonical_timeframe("15M") == "15m"
        assert canonical_timeframe("1D") == "1D"


# ============================================================
# 29. CHECKPOINT 3A — VERIFIED RESEARCH-UNIVERSE KEY MAP
# (TCS / HDFCBANK / ICICIBANK / NIFTY added; RELIANCE unchanged)
# ============================================================


class TestResearchUniverseKeyMap:
    def test_tcs_key_mapping(self):
        # A. TCS -> NSE_EQ instrument key (verified).
        p = UpstoxHistoricalDataProvider(token=_TOKEN)
        assert p.resolve_instrument_key("TCS") == "NSE_EQ|INE467B01029"

    def test_hdfcbank_key_mapping(self):
        # B. HDFCBANK -> NSE_EQ instrument key.
        p = UpstoxHistoricalDataProvider(token=_TOKEN)
        assert p.resolve_instrument_key("HDFCBANK") == "NSE_EQ|INE040A01034"

    def test_icicibank_key_mapping(self):
        # C. ICICIBANK -> NSE_EQ instrument key.
        p = UpstoxHistoricalDataProvider(token=_TOKEN)
        assert p.resolve_instrument_key("ICICIBANK") == "NSE_EQ|INE090A01021"

    def test_nifty_key_mapping(self):
        # D. NIFTY -> NSE_INDEX|Nifty 50 (the Upstox index key, which
        # contains a pipe separator and a space).
        p = UpstoxHistoricalDataProvider(token=_TOKEN)
        assert p.resolve_instrument_key("NIFTY") == "NSE_INDEX|Nifty 50"

    def test_reliance_mapping_regression(self):
        # F. RELIANCE mapping remains unchanged.
        p = UpstoxHistoricalDataProvider(token=_TOKEN)
        assert p.resolve_instrument_key("RELIANCE") == "NSE_EQ|INE002A01018"

    def test_request_instrument_normalized_to_upper(self):
        # Requests normalize to upper-case before key resolution, so a
        # lower-case "nifty" still resolves.
        p = UpstoxHistoricalDataProvider(token=_TOKEN)
        from engine.data.historical_provider import (
            _default_upstox_instrument_key_map,
        )
        mapping = _default_upstox_instrument_key_map()
        assert "NIFTY" in mapping
        assert mapping["NIFTY"] == "NSE_INDEX|Nifty 50"

    def test_no_second_universe(self):
        # The default map is derived from the canonical universe only; no
        # extra unverified instruments are guessed.
        from engine.data.historical_provider import (
            _default_upstox_instrument_key_map,
        )
        mapping = _default_upstox_instrument_key_map()
        for key in mapping:
            assert key in ("NIFTY", "RELIANCE", "TCS", "HDFCBANK",
                           "ICICIBANK")


# ============================================================
# 30. CHECKPOINT 3A — NIFTY URL ENCODING
# ============================================================


class TestNiftyUrlEncoding:
    def _nifty_url(self, fake=None, timeframe="15m"):
        import urllib.parse as _urlparse
        fake = fake or _FakeUrlopen({})
        p = UpstoxHistoricalDataProvider(token=_TOKEN, urlopen=fake)
        p.fetch(_request(instrument="NIFTY", timeframe=timeframe))
        return fake.calls[0][0]

    def test_nifty_instrument_key_quote_unquote(self):
        import urllib.parse as _urlparse
        key = "NSE_INDEX|Nifty 50"
        # The pipe is preserved (safe="|", verified live for equities);
        # the space is percent-encoded so the URL path stays on one
        # segment.
        quoted = _urlparse.quote(key, safe="|")
        assert quoted == "NSE_INDEX|Nifty%2050"
        assert _urlparse.unquote(quoted) == key

    def test_nifty_url_contains_encoded_instrument_key(self):
        # E. The constructed URL percent-encodes the space in the NIFTY
        # key (pipe preserved) and never contains a raw space.
        url = self._nifty_url()
        assert "NSE_INDEX|Nifty%2050" in url
        assert " " not in url

    def test_nifty_url_still_uses_minutes_15(self):
        url = self._nifty_url()
        assert "/minutes/15/" in url

    def test_nifty_url_dates_after_key(self):
        # The key (with its encoded space) is a single path segment and
        # the trailing to/from date labels remain intact.
        url = self._nifty_url(timeframe="1D")
        assert url.endswith("/2023-01-01/2022-12-01")


# ============================================================
# 31. CHECKPOINT 3A — DAILY (1D) SUPPORT
# ============================================================


#: The verified Upstox V3 daily candle shape: timestamps at
#: 00:00:00+05:30 (IST) — the daily bar start label.
def _daily_row(date_iso: str, open_p=18000.0, high=18100.0, low=17900.0,
               close=18050.0, volume=1_000_000.0, oi=0) -> list:
    return [f"{date_iso}T00:00:00+05:30", open_p, high, low, close, volume, oi]


def _intraday_embedded_row(date_iso: str) -> list:
    """A known NON-daily (embedded intraday) row in a daily response."""
    return [f"{date_iso}T09:15:00+05:30", 18010.0, 18020.0, 17990.0,
            18005.0, 50000.0, 0]


def _daily_url(from_date: str, to_date: str) -> str:
    """The exact Upstox URL the provider builds for a RELIANCE 1D
    monthly chunk (``unit=days``, ``interval=1``; the RELIANCE key has
    no space so quote(..., safe="|") leaves it unchanged)."""
    return (
        "https://api.upstox.com/v3/historical-candle/"
        f"NSE_EQ|INE002A01018/days/1/{to_date}/{from_date}"
    )


class TestDailySupport:
    def test_daily_unit_constants(self):
        assert UPSTOX_UNIT_DAYS == "days"
        assert UPSTOX_UNIT_MINUTES == "minutes"

    def test_1d_is_supported(self):
        # G. 1D is supported; the 1d/1D aliases canonicalize.
        p = UpstoxHistoricalDataProvider(token=_TOKEN)
        assert p.supports("RELIANCE", "1D") is True
        assert p.supports("RELIANCE", "1d") is True

    def test_15m_remains_supported(self):
        # H. 15m support is unchanged.
        p = UpstoxHistoricalDataProvider(token=_TOKEN)
        assert p.supports("RELIANCE", "15m") is True
        assert p.supports("RELIANCE", "15M") is True

    def test_1d_uses_days_1(self):
        # I. A 1D fetch builds a /days/1/ URL.
        rows = [_daily_row("2022-12-01"), _daily_row("2022-12-02")]
        p = _make_provider({
            _daily_url("2022-12-01", "2023-01-01"): _v3_payload(rows),
        })
        response = p.fetch(_request(timeframe="1D"))
        assert response.status is ProviderResponseStatus.OK
        url = p._fake.calls[0][0]
        assert "/days/1/" in url
        assert "/minutes/" not in url

    def test_1d_date_ordering(self):
        # J. The URL preserves the Upstox V3 /to_date/from_date/ order.
        rows = [_daily_row("2022-12-01")]
        p = _make_provider({
            _daily_url("2022-12-01", "2023-01-01"): _v3_payload(rows),
        })
        p.fetch(_request(timeframe="1D"))
        url = p._fake.calls[0][0]
        assert url.endswith("/2023-01-01/2022-12-01")

    def test_1d_range_clipping_strict_utc(self):
        # K. No DAILY candle outside the requested [start, end] day
        # labels is returned. A daily candle's canonical UTC timestamp
        # is 18:30:00 UTC of the PRIOR day, so acceptance is by IST day
        # label (documented provider rule).
        rows = [
            _daily_row("2022-11-30"),   # before start day label
            _daily_row("2022-12-01"),   # first requested day
            _daily_row("2022-12-15"),   # inside
            _daily_row("2022-12-31"),   # last requested day
            _daily_row("2023-01-01"),   # == end day label, allowed inclusive
        ]
        p = _make_provider({
            _daily_url("2022-12-01", "2023-01-01"): _v3_payload(rows),
        })
        response = p.fetch(_request(timeframe="1D"))
        labels = sorted(
            (c.timestamp + timedelta(hours=5, minutes=30)).date()
            for c in response.candles
        )
        assert labels == [
            datetime(2022, 12, 1).date(),   # first requested day kept
            datetime(2022, 12, 15).date(),
            datetime(2022, 12, 31).date(),
            datetime(2023, 1, 1).date(),    # inclusive end label kept
        ]
        # The day BEFORE the requested start label is never returned.
        labels_before = [
            (c.timestamp + timedelta(hours=5, minutes=30)).date()
            for c in response.candles
            if (c.timestamp + timedelta(hours=5, minutes=30)).date()
            < datetime(2022, 12, 1).date()
        ]
        assert labels_before == []
        # And the raw UTC timestamp of the first accepted candle is the
        # exact +05:30->UTC normalization (policy verifiable).
        first = response.candles[0]
        assert first.timestamp == datetime(2022, 11, 30, 18, 30, tzinfo=UTC)

    def test_1d_timezone_normalization(self):
        # L. A daily candle timestamp 00:00:00+05:30 normalizes to
        # 18:30:00 UTC of the prior day.
        row = _daily_row("2022-12-01")
        candles, filtered = (
            UpstoxHistoricalDataProvider._parse_candles_filtered(
                _v3_payload([row]), unit=UPSTOX_UNIT_DAYS,
            )
        )
        assert filtered == 0
        assert len(candles) == 1
        assert candles[0].timestamp == datetime(2022, 11, 30, 18, 30,
                                                tzinfo=UTC)
        assert candles[0].timestamp.tzinfo is not None

    def test_1d_reverse_chronological_normalized(self):
        rows = [_daily_row("2022-12-01"), _daily_row("2022-12-02")]
        # API returns them newest-first -> reverse here.
        rows = list(reversed(rows))
        p = _make_provider({
            _daily_url("2022-12-01", "2023-01-01"): _v3_payload(rows),
        })
        response = p.fetch(_request(timeframe="1D"))
        ts = [c.timestamp for c in response.candles]
        assert ts == sorted(ts)

    def test_1d_monthly_chunking_kept(self):
        # 1D keeps the same bounded-monthly-chunk semantics as 15m.
        start = datetime(2022, 1, 1, tzinfo=UTC)
        end = datetime(2023, 1, 1, tzinfo=UTC)
        p = _make_provider({})
        p.fetch(_request(timeframe="1D", start=start, end=end))
        assert len(p._fake.calls) == 12
        for url, _, _ in p._fake.calls:
            assert "/days/1/" in url


# ============================================================
# 32. CHECKPOINT 3A — DAILY RESPONSE NORMALIZATION RULE
# ============================================================


class TestDailyResponseNormalization:
    def test_real_v3_nested_data_candles_parsing(self):
        # M. The real V3 shape (data.candles) parses for daily rows.
        rows = [_daily_row("2022-12-01")]
        candles, filtered = (
            UpstoxHistoricalDataProvider._parse_candles_filtered(
                {"status": "success", "data": {"candles": rows}},
                unit=UPSTOX_UNIT_DAYS,
            )
        )
        assert len(candles) == 1
        assert filtered == 0

    def test_midnight_daily_rows_kept(self):
        rows = [_daily_row("2022-12-01"), _daily_row("2022-12-02")]
        candles, filtered = (
            UpstoxHistoricalDataProvider._parse_candles_filtered(
                _v3_payload(rows), unit=UPSTOX_UNIT_DAYS,
            )
        )
        assert len(candles) == 2
        assert filtered == 0

    def test_embedded_intraday_rows_filtered(self):
        # N. A daily response that embeds intraday-related rows (09:15
        # timestamps) keeps only the true daily bars.
        rows = [
            _daily_row("2022-12-01"),
            _intraday_embedded_row("2022-12-01"),   # 09:15 same day
            _daily_row("2022-12-02"),
            _intraday_embedded_row("2022-12-02"),   # 09:15 same day
            _intraday_embedded_row("2022-12-02"),   # 09:30? 09:15 reuse
        ]
        candles, filtered = (
            UpstoxHistoricalDataProvider._parse_candles_filtered(
                _v3_payload(rows), unit=UPSTOX_UNIT_DAYS,
            )
        )
        assert len(candles) == 2
        assert filtered == 3
        # Canonical timestamps are the true daily bars only.
        assert candles[0].timestamp == datetime(2022, 11, 30, 18, 30,
                                                tzinfo=UTC)
        assert candles[1].timestamp == datetime(2022, 12, 1, 18, 30,
                                                tzinfo=UTC)

    def test_intraday_responses_kept_unchanged(self):
        # The daily filter NEVER applies to intraday responses.
        rows = [_row(_ist("2022-12-01T09:15:00+05:30")),
                _row(_ist("2022-12-01T09:30:00+05:30"))]
        candles, filtered = (
            UpstoxHistoricalDataProvider._parse_candles_filtered(
                _v3_payload(rows), unit=UPSTOX_UNIT_MINUTES,
            )
        )
        assert len(candles) == 2
        assert filtered == 0

    def test_legacy_parse_candles_no_filter(self):
        # The backward-compatible entry point does NOT apply the daily
        # filter (existing callers keep the old behavior).
        rows = [_row(_ist("2022-12-01T09:15:00+05:30"))]
        candles = UpstoxHistoricalDataProvider._parse_candles(
            _v3_payload(rows),
        )
        assert len(candles) == 1

    def test_fetch_daily_mixed_payload_filters_and_reports(self):
        rows = [
            _daily_row("2022-12-01"),
            _intraday_embedded_row("2022-12-01"),
            _daily_row("2022-12-02"),
        ]
        p = _make_provider({
            _daily_url("2022-12-01", "2023-01-01"): _v3_payload(rows),
        })
        response = p.fetch(_request(timeframe="1D"))
        assert response.status is ProviderResponseStatus.OK
        assert len(response.candles) == 2
        assert "1 embedded intraday row(s) filtered" in response.reason

    def test_malformed_daily_rows_rejected(self):
        # O. A malformed row (bad shape) in a daily response raises ->
        # chunk error -> honest ERROR (never silently stored).
        p = _make_provider({
            _daily_url("2022-12-01", "2023-01-01"):
                _v3_payload([["2022-12-01T00:00:00+05:30", 1, 2]]),
        })
        response = p.fetch(_request(timeframe="1D"))
        assert response.status is ProviderResponseStatus.ERROR

    def test_non_numeric_daily_row_rejected(self):
        p = _make_provider({
            _daily_url("2022-12-01", "2023-01-01"):
                _v3_payload([_daily_row("2022-12-01", open_p="nan")]),
        })
        response = p.fetch(_request(timeframe="1D"))
        assert response.status is ProviderResponseStatus.ERROR

    def test_naive_daily_timestamp_rejected(self):
        p = _make_provider({
            _daily_url("2022-12-01", "2023-01-01"):
                _v3_payload([["2022-12-01T00:00:00", 1, 2, 1, 1.5, 100]]),
        })
        response = p.fetch(_request(timeframe="1D"))
        assert response.status is ProviderResponseStatus.ERROR

    def test_duplicate_daily_timestamps_rejected(self):
        # P. Duplicate daily timestamps (same day across two chunks) are
        # deduped at the provider and the store merge is idempotent.
        rows_a = [_daily_row("2022-12-01"), _daily_row("2022-12-02")]
        rows_b = [_daily_row("2022-12-02"), _daily_row("2022-12-03")]
        p = _make_provider({
            _daily_url("2022-12-01", "2023-01-01"): _v3_payload(rows_a),
            _daily_url("2023-01-01", "2023-02-01"): _v3_payload(rows_b),
        })
        response = p.fetch(
            _request(timeframe="1D",
                     end=datetime(2023, 2, 1, tzinfo=UTC)),
        )
        # 3 unique daily timestamps (2022-12-01, 2022-12-02, 2022-12-03)
        ts = [c.timestamp for c in response.candles]
        assert len(ts) == len(set(ts)) == 3

    def test_provider_ordering_invariance(self):
        # Q. Feeding rows in different orders yields the same result.
        rows = [_daily_row("2022-12-01"), _daily_row("2022-12-03"),
                _daily_row("2022-12-02")]
        p1 = _make_provider({
            _daily_url("2022-12-01", "2023-01-01"): _v3_payload(rows),
        })
        p2 = _make_provider({
            _daily_url("2022-12-01", "2023-01-01"):
                _v3_payload(list(reversed(rows))),
        })
        r1 = p1.fetch(_request(timeframe="1D"))
        r2 = p2.fetch(_request(timeframe="1D"))
        assert r1.status is ProviderResponseStatus.OK
        assert r2.status is ProviderResponseStatus.OK
        assert [c.timestamp for c in r1.candles] == \
            [c.timestamp for c in r2.candles]

    def test_no_future_daily_candles(self):
        # R. Rows strictly beyond the requested end day label are never
        # returned.
        rows = [_daily_row("2022-12-01"), _daily_row("2023-01-02")]
        p = _make_provider({
            _daily_url("2022-12-01", "2023-01-01"): _v3_payload(rows),
        })
        response = p.fetch(_request(timeframe="1D"))
        assert response.status is ProviderResponseStatus.OK
        labels = [
            (c.timestamp + timedelta(hours=5, minutes=30)).date()
            for c in response.candles
        ]
        assert labels == [datetime(2022, 12, 1).date()]

    def test_idempotent_daily_persistence(self, tmp_path):
        # S. Ingestion of the same daily data twice adds no duplicates.
        rows = [_daily_row("2022-12-01"), _daily_row("2022-12-02")]
        p = _make_provider({
            _daily_url("2022-12-01", "2023-01-01"): _v3_payload(rows),
        })
        store = HistoricalDataStore(tmp_path)
        svc = HistoricalMarketDataService(provider=p, store=store)
        first = svc.ingest(_request(timeframe="1D"), reference_now=_END)
        assert first.fetch.status is HistoricalIngestionStatus.AVAILABLE
        assert first.store.records_added == 2
        second = svc.ingest(_request(timeframe="1D"), reference_now=_END)
        assert second.store.records_added == 0
        assert second.store.total_candles == 2
        assert len(store.load_candles("RELIANCE", "1D")) == 2