import numpy as np
import pandas as pd
import pytest

from bot_trader.config import UNIVERSE
from bot_trader.strategy import decide, entry_notional, wilder_rsi


def closes(values):
    return pd.DataFrame(
        {s: values for s in UNIVERSE}, index=pd.bdate_range("2020-01-01", periods=len(values))
    )


def test_wilder_seed_and_extreme_cases():
    actual = wilder_rsi(pd.Series([10.0, 12.0, 11.0, 14.0, 13.0]))
    assert actual.iloc[:2].isna().all()
    assert actual.iloc[2] == pytest.approx(100 * 1 / 1.5)
    assert actual.iloc[3] == pytest.approx(100 * 2 / 2.25)
    assert actual.iloc[4] == pytest.approx(100 * 1 / 1.625)
    assert wilder_rsi(pd.Series([1.0, 1.0, 1.0, 1.0])).iloc[-1] == 50
    assert wilder_rsi(pd.Series([4.0, 3.0, 2.0, 1.0])).iloc[-1] == 0


def test_trend_requires_week_end_and_warmup():
    prices = closes(np.arange(1.0, 202.0))
    assert decide("trend", prices.iloc[:199], {}, True) == {}
    assert decide("trend", prices, {}, False) == {}
    assert decide("trend", prices, {}, True) == dict.fromkeys(UNIVERSE, "buy")
    assert decide("trend", prices, {s: 4 for s in UNIVERSE}, True) == {}


def test_rebound_entry_and_five_session_exit():
    values = np.r_[np.arange(100.0, 301.0), [290.0, 280.0, 270.0, 260.0]]
    prices = closes(values)
    assert prices.iloc[-1, 0] > prices.iloc[-200:, 0].mean()
    assert decide("rebound", prices, {}, False) == dict.fromkeys(UNIVERSE, "buy")
    assert decide("rebound", prices, {s: 4 for s in UNIVERSE}, False) == {}
    assert decide("rebound", prices, {s: 5 for s in UNIVERSE}, False) == dict.fromkeys(UNIVERSE, "sell")


def test_cash_exposure_reservations_and_invalid_sizing():
    assert entry_notional(5000, 5000, 0) == pytest.approx(5000 / 12)
    assert entry_notional(5000, 2500, 2490) == 10
    assert entry_notional(5000, 5000, 2200, reserved=300) == 0
    assert entry_notional(5000, 10, 0) == 9.95
    assert entry_notional(5000, 5000, 2501) == 0
    assert entry_notional(float("nan"), 50, 10) == 0


def test_missing_closes_fail_closed():
    frame = closes(np.arange(1.0, 202.0))
    frame.iloc[-1, 0] = np.nan
    with pytest.raises(ValueError, match="Missing or invalid"):
        decide("trend", frame, {}, True)
