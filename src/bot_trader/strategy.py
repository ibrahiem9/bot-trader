"""Signals only consume completed sessions; execution belongs to the caller."""

import math

import numpy as np
import pandas as pd

from .config import ENTRY_FRACTION, MAX_EXPOSURE, UNIVERSE


def wilder_rsi(close: pd.Series, period: int = 2) -> pd.Series:
    """Wilder smoothing seeded by the mean of the first `period` changes.

    A flat series has RSI 50, all gains 100, and all losses 0. pandas ewm's
    default initialization is deliberately not used: it is a different rule.
    """
    if period < 1:
        raise ValueError("RSI period must be positive")
    values = close.to_numpy(dtype=float)
    result = np.full(len(values), np.nan)
    if len(values) <= period:
        return pd.Series(result, index=close.index)
    if not np.isfinite(values).all():
        raise ValueError("RSI input contains missing or non-finite prices")
    changes = np.diff(values)
    gains, losses = np.maximum(changes, 0), np.maximum(-changes, 0)
    gain, loss = gains[:period].mean(), losses[:period].mean()
    for i in range(period, len(values)):
        if i > period:
            gain = (gain * (period - 1) + gains[i - 1]) / period
            loss = (loss * (period - 1) + losses[i - 1]) / period
        result[i] = 50.0 if gain == loss == 0 else (100.0 if loss == 0 else 100 * gain / (gain + loss))
    return pd.Series(result, index=close.index)


def decide(strategy: str, closes: pd.DataFrame, held_sessions: dict[str, int], weekly: bool) -> dict:
    if strategy not in {"trend", "rebound"}:
        raise ValueError("Strategy must be trend or rebound")
    if closes.index.has_duplicates or not closes.index.is_monotonic_increasing:
        raise ValueError("Daily closes must have unique ascending sessions")
    if closes.index.tz is not None:
        raise ValueError("Daily close index must be timezone-naive session labels")
    if len(closes) < 200:
        return {}
    if not set(UNIVERSE).issubset(closes.columns):
        raise ValueError("Missing ETF universe columns")
    if not np.isfinite(closes[list(UNIVERSE)].to_numpy()).all() or (closes[list(UNIVERSE)] <= 0).any().any():
        raise ValueError("Missing or invalid completed closes")
    if strategy == "trend" and not weekly:
        return {}
    decisions = {}
    for symbol in UNIVERSE:
        prices = closes[symbol]
        above = prices.iloc[-1] > prices.iloc[-200:].mean()
        held = symbol in held_sessions
        if strategy == "trend":
            if held and not above:
                decisions[symbol] = "sell"
            elif not held and above:
                decisions[symbol] = "buy"
        else:
            rsi = wilder_rsi(prices).iloc[-1]
            if held and (rsi > 50 or held_sessions[symbol] >= 5):
                decisions[symbol] = "sell"
            elif not held and above and rsi < 10:
                decisions[symbol] = "buy"
    return decisions


def entry_notional(equity: float, cash: float, exposure: float, reserved: float = 0.0) -> float:
    """Dollar budget at submission, leaving a cash buffer for fill uncertainty."""
    if not all(math.isfinite(x) for x in (equity, cash, exposure, reserved)):
        return 0.0
    if equity <= 0 or min(cash, exposure, reserved) < 0:
        return 0.0
    return max(
        0.0,
        min(equity * ENTRY_FRACTION, cash * 0.995 - reserved, equity * MAX_EXPOSURE - exposure - reserved),
    )
