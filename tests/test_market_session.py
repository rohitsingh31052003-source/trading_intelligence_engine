"""
Checkpoint 19.2 — NSE market-session helper tests.

These tests prove :mod:`engine.data.market_session` is:

* DETERMINISTIC — pure functions of the reference instant;
* TIMEZONE-CORRECT — IST boundaries are converted from aware UTC
  instants (03:45 UTC == 09:15 IST, 10:00 UTC == 15:30 IST);
* SESSION-AWARE — trading weekdays (Mon-Fri), PRE_OPEN / OPEN /
  POST_CLOSE / WEEKEND / UNKNOWN states;
* DOCUMENTED — freshness thresholds are explicit configuration, not
  magic constants;
* SAFE — naive datetimes are NEVER silently accepted; nothing is a
  trading signal; no broker semantics.

No network access. All reference instants are explicit.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from engine.data.market_session import (
    IST_TZ,
    MarketSessionState,
    SessionAwareFreshness,
    SessionFreshnessConfig,
    is_trading_weekday,
    market_session_state,
    seconds_until_next_open,
    session_aware_freshness,
    staleness_seconds,
)

# Fixed NSE session boundary instants (UTC).
OPEN_UTC = datetime(2026, 9, 4, 3, 45, tzinfo=UTC)      # Fri 09:15 IST
CLOSE_UTC = datetime(2026, 9, 4, 10, 0, tzinfo=UTC)     # Fri 15:30 IST


def _utc(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


class TestMarketSessionState:
    def test_open_friday_morning(self):
        assert market_session_state(_utc(2026, 9, 4, 4, 0)) is MarketSessionState.OPEN

    def test_open_boundary_inclusive_start(self):
        # 09:15 IST exactly is OPEN (>= open, < close).
        assert market_session_state(OPEN_UTC) is MarketSessionState.OPEN

    def test_pre_open_before_0915(self):
        assert market_session_state(_utc(2026, 9, 4, 3, 30)) is MarketSessionState.PRE_OPEN

    def test_post_close_after_1530(self):
        assert market_session_state(_utc(2026, 9, 4, 10, 15)) is MarketSessionState.POST_CLOSE

    def test_close_boundary(self):
        # 15:30 IST exactly is POST_CLOSE (session is [09:15, 15:30)).
        assert market_session_state(CLOSE_UTC) is MarketSessionState.POST_CLOSE

    def test_weekend_saturday(self):
        assert market_session_state(_utc(2026, 9, 5, 5, 0)) is MarketSessionState.WEEKEND

    def test_weekend_sunday(self):
        assert market_session_state(_utc(2026, 9, 6, 5, 0)) is MarketSessionState.WEEKEND

    def test_naive_reference_is_unknown(self):
        assert market_session_state(datetime(2026, 9, 4, 4, 0)) is MarketSessionState.UNKNOWN

    def test_ist_equivalence(self):
        # 11:00 IST == 05:30 UTC — same state via the IST wall clock.
        assert market_session_state(_utc(2026, 9, 4, 5, 30)) is MarketSessionState.OPEN

    def test_monday_and_friday_in_session(self):
        for day in (1, 2, 3, 4):  # 2026-09 weekdays Tue..Fri
            assert market_session_state(_utc(2026, 9, day, 5, 0)) is MarketSessionState.OPEN


class TestTradingWeekday:
    def test_weekdays_true(self):
        assert is_trading_weekday(_utc(2026, 9, 1, 5, 0))   # Tue
        assert is_trading_weekday(_utc(2026, 9, 4, 5, 0))   # Fri

    def test_weekend_false(self):
        assert not is_trading_weekday(_utc(2026, 9, 5, 5, 0))  # Sat
        assert not is_trading_weekday(_utc(2026, 9, 6, 5, 0))  # Sun

    def test_naive_rejected(self):
        with pytest.raises(ValueError):
            is_trading_weekday(datetime(2026, 9, 4, 5, 0))


class TestStalenessSeconds:
    def test_open_uses_candle_multiplier(self):
        cfg = SessionFreshnessConfig(open_staleness_multiplier=2.0)
        assert staleness_seconds(_utc(2026, 9, 4, 5, 0), "15m", cfg) == 1800

    def test_open_5m(self):
        cfg = SessionFreshnessConfig(open_staleness_multiplier=2.0)
        assert staleness_seconds(_utc(2026, 9, 4, 5, 0), "5m", cfg) == 600

    def test_pre_open_uses_closed_threshold(self):
        cfg = SessionFreshnessConfig(closed_staleness_seconds=26 * 3600)
        assert staleness_seconds(_utc(2026, 9, 4, 3, 0), "15m", cfg) == 93600

    def test_post_close_uses_closed_threshold(self):
        assert staleness_seconds(_utc(2026, 9, 4, 11, 0), "15m") == 26 * 3600

    def test_weekend_uses_weekend_threshold(self):
        cfg = SessionFreshnessConfig(weekend_staleness_seconds=96 * 3600)
        assert staleness_seconds(_utc(2026, 9, 5, 5, 0), "15m", cfg) == 96 * 3600

    def test_unknown_timeframe_uses_day_candle(self):
        cfg = SessionFreshnessConfig(open_staleness_multiplier=2.0)
        assert staleness_seconds(_utc(2026, 9, 4, 5, 0), "bogus", cfg) == 2 * 86400

    def test_naive_reference_none(self):
        assert staleness_seconds(datetime(2026, 9, 4, 5, 0), "15m") is None

    def test_custom_open_multiplier(self):
        cfg = SessionFreshnessConfig(open_staleness_multiplier=1.0)
        assert staleness_seconds(_utc(2026, 9, 4, 5, 0), "15m", cfg) == 900


class TestSessionAwareFreshness:
    def test_current_while_open(self):
        last = _utc(2026, 9, 4, 3, 30)   # 09:00 IST candle (one 15m old)
        now = _utc(2026, 9, 4, 3, 45)   # 09:15 IST
        cfg = SessionFreshnessConfig(open_staleness_multiplier=2.0)
        assert session_aware_freshness(now, last, "15m", cfg) is SessionAwareFreshness.CURRENT

    def test_stale_while_open_beyond_window(self):
        last = _utc(2026, 9, 4, 3, 30)
        now = _utc(2026, 9, 4, 4, 15)   # 09:45 IST (3 x 15m old > 2x window)
        cfg = SessionFreshnessConfig(open_staleness_multiplier=2.0)
        assert session_aware_freshness(now, last, "15m", cfg) is SessionAwareFreshness.STALE

    def test_current_after_close_same_day(self):
        last = _utc(2026, 9, 4, 3, 30)
        now = _utc(2026, 9, 4, 11, 0)   # 16:30 IST, same day
        assert session_aware_freshness(now, last, "15m") is SessionAwareFreshness.CURRENT

    def test_current_over_weekend(self):
        last = _utc(2026, 9, 4, 10, 0)  # Fri 15:30 IST close
        now = _utc(2026, 9, 5, 5, 0)    # Sat
        cfg = SessionFreshnessConfig(weekend_staleness_seconds=96 * 3600)
        assert session_aware_freshness(now, last, "15m", cfg) is SessionAwareFreshness.CURRENT

    def test_stale_over_extended_weekend(self):
        last = _utc(2026, 9, 4, 3, 0)   # Fri 08:30 IST
        now = _utc(2026, 9, 6, 5, 0)    # Sun
        cfg = SessionFreshnessConfig(weekend_staleness_seconds=12 * 3600)
        assert session_aware_freshness(now, last, "15m", cfg) is SessionAwareFreshness.STALE

    def test_no_candle_unavailable(self):
        assert session_aware_freshness(_utc(2026, 9, 4, 5, 0), None, "15m") is SessionAwareFreshness.UNAVAILABLE

    def test_naive_reference_stale(self):
        last = _utc(2026, 9, 4, 3, 30)
        assert session_aware_freshness(datetime(2026, 9, 4, 4, 0), last, "15m") is SessionAwareFreshness.STALE

    def test_future_dated_candle_stale(self):
        last = _utc(2026, 9, 4, 5, 0)
        now = _utc(2026, 9, 4, 4, 0)
        assert session_aware_freshness(now, last, "15m") is SessionAwareFreshness.STALE


class TestSecondsUntilNextOpen:
    def test_open_gives_zero(self):
        assert seconds_until_next_open(_utc(2026, 9, 4, 4, 0)) == timedelta(0)

    def test_friday_afternoon_to_monday(self):
        now = _utc(2026, 9, 4, 11, 0)   # Fri 16:30 IST
        until = seconds_until_next_open(now)
        assert until is not None
        # Next open = Mon 09:15 IST = 03:45 UTC. From Fri 11:00 UTC.
        target = _utc(2026, 9, 7, 3, 45)
        assert until == target - now

    def test_saturday_to_monday(self):
        now = _utc(2026, 9, 5, 5, 0)
        until = seconds_until_next_open(now)
        target = _utc(2026, 9, 7, 3, 45)
        assert until == target - now

    def test_monday_preopen_to_monday(self):
        now = _utc(2026, 9, 7, 3, 0)    # Mon 08:30 IST
        until = seconds_until_next_open(now)
        target = _utc(2026, 9, 7, 3, 45)
        assert until == target - now

    def test_naive_none(self):
        assert seconds_until_next_open(datetime(2026, 9, 4, 4, 0)) is None


class TestConfig:
    def test_defaults_documented(self):
        cfg = SessionFreshnessConfig()
        assert cfg.open_staleness_multiplier == 2.0
        assert cfg.closed_staleness_seconds == 26 * 3600
        assert cfg.weekend_staleness_seconds == 4 * 24 * 3600

    def test_invalid_multiplier_rejected(self):
        with pytest.raises(ValueError):
            SessionFreshnessConfig(open_staleness_multiplier=0)

    def test_invalid_closed_rejected(self):
        with pytest.raises(ValueError):
            SessionFreshnessConfig(closed_staleness_seconds=0)

    def test_invalid_weekend_rejected(self):
        with pytest.raises(ValueError):
            SessionFreshnessConfig(weekend_staleness_seconds=-1)

    def test_config_frozen(self):
        cfg = SessionFreshnessConfig()
        with pytest.raises(Exception):
            cfg.closed_staleness_seconds = 1


class TestISTCrossCheck:
    def test_ist_tz_is_kolkata(self):
        assert str(IST_TZ) == "Asia/Kolkata"

    def test_open_ist_label(self):
        # A UTC instant of 03:45 on a Friday IS 09:15 IST.
        inst = _utc(2026, 9, 4, 3, 45)
        local = inst.astimezone(IST_TZ)
        assert (local.hour, local.minute) == (9, 15)