"""Deterministic, long-only replay of the frozen daily strategies.

Signals use split-adjusted completed closes. Accounting uses raw prices,
explicit splits and dividend receivables. Risk is sampled at minute closes;
liquidation starts at the next available minute open. No end-of-test sale is
invented. Benchmarks remain passive and do not inherit the strategy stop.
"""

from __future__ import annotations

import math
from typing import Any

import exchange_calendars as xcals
import numpy as np
import pandas as pd

from .config import CASH_SYMBOL, DRAWDOWN_LIMIT, INITIAL_EQUITY, UNIVERSE
from .strategy import decide, entry_notional


class DataQualityError(ValueError):
    """An input cannot support defensible performance conclusions."""


def _labels(values) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(values)
    if idx.tz is not None or not idx.equals(idx.normalize()):
        raise DataQualityError("Sessions must be naive midnight date labels")
    return idx


def _finite_prices(frame: pd.DataFrame, label: str) -> None:
    cols = ["open", "high", "low", "close", "volume"]
    if not set(cols).issubset(frame.columns):
        raise DataQualityError(f"{label} lacks OHLCV columns")
    vals = frame[cols].to_numpy(dtype=float)
    if not np.isfinite(vals).all() or (vals[:, :4] <= 0).any() or (vals[:, 4] < 0).any():
        raise DataQualityError(f"{label} has invalid OHLCV values")
    if (
        (frame.high < frame[["open", "close", "low"]].max(axis=1))
        | (frame.low > frame[["open", "close", "high"]].min(axis=1))
    ).any():
        raise DataQualityError(f"{label} has inconsistent OHLC values")


def _prepare(daily, minutes, actions, sessions, symbols, start, end):
    sessions = _labels(sessions)
    if sessions.empty or sessions.has_duplicates or not sessions.is_monotonic_increasing:
        raise DataQualityError("Sessions must be unique and ascending")
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if start.tzinfo or end.tzinfo or start > end:
        raise DataQualityError("Invalid evaluation date range")
    cal = xcals.get_calendar(
        "XNYS", start=sessions[0] - pd.Timedelta(days=10), end=max(sessions[-1], end) + pd.Timedelta(days=10)
    )
    expected_sessions = cal.sessions_in_range(sessions[0], end)
    if not sessions[sessions <= end].equals(expected_sessions):
        raise DataQualityError("Input sessions omit or add exchange trading dates")
    evaluation = sessions[(sessions >= start) & (sessions <= end)]
    if len(evaluation) < 2:
        raise DataQualityError("At least two evaluation sessions are required")
    if actions.attrs.get("audited") is not True:
        raise DataQualityError("Corporate action history must be explicitly audited")
    required_actions = {"session", "symbol", "split_ratio", "dividend", "pay_session"}
    if not required_actions.issubset(actions.columns):
        raise DataQualityError("Missing normalized corporate action columns")
    daily, minutes, actions = daily.copy(), minutes.copy(), actions.copy()
    daily["session"] = _labels(daily.session)
    actions["session"] = _labels(actions.session)
    actions["pay_session"] = pd.to_datetime(actions.pay_session)
    if actions.duplicated(["session", "symbol"]).any():
        raise DataQualityError("Corporate actions must be normalized, without duplicates")
    if not np.isfinite(actions[["split_ratio", "dividend"]].to_numpy(dtype=float)).all():
        raise DataQualityError("Non-finite corporate actions")
    if (actions.split_ratio <= 0).any() or (actions.dividend < 0).any():
        raise DataQualityError("Invalid split ratio or dividend")
    div = actions[actions.dividend > 0]
    if div.pay_session.isna().any() or (div.pay_session < div.session).any():
        raise DataQualityError("Cash dividends require a pay date on or after ex date")
    if not actions.session.isin(sessions).all():
        raise DataQualityError("Corporate action ex date is not in the exchange calendar")
    daily = daily[daily.symbol.isin(symbols) & (daily.session <= end)]
    if daily.duplicated(["session", "symbol"]).any():
        raise DataQualityError("Duplicate daily bars")
    _finite_prices(daily, "daily data")
    daily_idx = pd.MultiIndex.from_product([sessions[sessions <= end], symbols], names=["session", "symbol"])
    daily = daily.set_index(["session", "symbol"]).reindex(daily_idx)
    if daily.isna().any().any() or "signal_close" not in daily or (daily.signal_close <= 0).any():
        raise DataQualityError("Missing daily sessions or split-adjusted signal prices")
    if not np.isfinite(daily.signal_close.to_numpy(dtype=float)).all():
        raise DataQualityError("Non-finite signal prices")
    timestamps = pd.DatetimeIndex(minutes.timestamp)
    if timestamps.tz is None:
        raise DataQualityError("Minute timestamps must have a UTC offset")
    minutes["timestamp"] = timestamps.tz_convert("UTC")
    minutes = minutes[minutes.symbol.isin(symbols)]
    minutes = minutes[
        (minutes.timestamp >= cal.session_open(evaluation[0]))
        & (minutes.timestamp < cal.session_close(evaluation[-1]))
    ]
    if minutes.duplicated(["timestamp", "symbol"]).any():
        raise DataQualityError("Duplicate minute bars")
    _finite_prices(minutes, "minute data")
    minute_index = pd.DatetimeIndex(
        np.concatenate(
            [
                pd.date_range(cal.session_open(s), cal.session_close(s), freq="min", inclusive="left").values
                for s in evaluation
            ]
        ),
        tz="UTC",
    )
    expected = pd.MultiIndex.from_product([minute_index, symbols], names=["timestamp", "symbol"])
    actual = pd.MultiIndex.from_frame(minutes[["timestamp", "symbol"]])
    if len(expected.difference(actual)) or len(actual.difference(expected)):
        raise DataQualityError("Missing or extra regular-session minute bars; risk replay is incomplete")
    opened = minutes.pivot(index="timestamp", columns="symbol", values="open").reindex(columns=symbols)
    closed = minutes.pivot(index="timestamp", columns="symbol", values="close").reindex(columns=symbols)
    return daily, opened, closed, actions, sessions, evaluation, cal


def _fee_rows(fee_schedule, start):
    if fee_schedule is None:
        return []
    rows = []
    allowed = {
        "effective_date",
        "buy_per_dollar",
        "buy_per_share",
        "buy_minimum",
        "buy_maximum",
        "sell_per_dollar",
        "sell_per_share",
        "sell_minimum",
        "sell_maximum",
        "commission_per_order",
        "sec_per_dollar",
        "taf_per_share",
        "taf_maximum",
        "cat_per_share",
    }
    for row in fee_schedule:
        if set(row) - allowed or "effective_date" not in row:
            raise ValueError("Unrecognized fee schedule fields")
        copied = dict(row, effective_date=pd.Timestamp(row["effective_date"]))
        for key, value in copied.items():
            if key != "effective_date" and (not math.isfinite(value) or value < 0):
                raise ValueError("Fee values must be finite and nonnegative")
        rows.append(copied)
    rows.sort(key=lambda r: r["effective_date"])
    if not rows or rows[0]["effective_date"] > pd.Timestamp(start):
        raise ValueError("Fee schedule must cover the first evaluation date")
    if len({r["effective_date"] for r in rows}) != len(rows):
        raise ValueError("Duplicate fee effective dates")
    return rows


def _fee_components(side, quantity, notional, session, fee_rows):
    available = [r for r in fee_rows if r["effective_date"] <= session]
    if not available:
        return {}
    row = available[-1]
    if "sec_per_dollar" in row:
        return {
            "sec": notional * row.get("sec_per_dollar", 0.0) if side == "sell" else 0.0,
            "taf": min(quantity * row.get("taf_per_share", 0.0), row.get("taf_maximum", float("inf")))
            if side == "sell"
            else 0.0,
            "cat": quantity * row.get("cat_per_share", 0.0),
            "commission": row.get("commission_per_order", 0.0),
        }
    share = quantity * row.get(f"{side}_per_share", 0.0)
    if share:
        share = min(max(share, row.get(f"{side}_minimum", 0.0)), row.get(f"{side}_maximum", float("inf")))
    return {
        "share": share,
        "dollar": notional * row.get(f"{side}_per_dollar", 0.0),
        "commission": row.get("commission_per_order", 0.0),
    }


def transaction_fee(side, quantity, notional, session, fee_rows):
    """Unrounded date-effective fee; simulation reconciles each fee type daily.

    Official rows name SEC, TAF and CAT separately. TAF caps apply per trade;
    daily sums of each type round upward to cents. Generic legacy fee fields
    remain available but are explicitly unverified in result metadata.
    """
    return sum(_fee_components(side, quantity, notional, session, fee_rows).values())


def performance(equity, trades, initial=INITIAL_EQUITY, equity_column="equity"):
    """Daily statistics; Sharpe uses zero cash rate until compared with BIL."""
    frame = pd.DataFrame(equity)
    values = frame[equity_column].to_numpy(dtype=float)
    path = np.r_[initial, values]
    solvent = bool((path > 0).all())
    returns = path[1:] / path[:-1] - 1 if solvent else np.array([])
    years = len(values) / 252
    std = float(np.std(returns, ddof=1)) if len(returns) > 1 else 0.0
    peaks = np.maximum.accumulate(path)
    drawdowns = 1 - path / peaks
    underwater = 0
    longest = 0
    for drawdown in drawdowns[1:]:
        underwater = underwater + 1 if drawdown > 1e-12 else 0
        longest = max(longest, underwater)
    frame["year"] = pd.to_datetime(frame.session).dt.year
    yearly = {}
    prior = initial
    for year, group in frame.groupby("year", sort=True):
        last = float(group[equity_column].iloc[-1])
        yearly[str(year)] = last / prior - 1 if prior > 0 else None
        prior = last
    cagr = (values[-1] / initial) ** (1 / years) - 1 if solvent else None
    return {
        "annualized_return": float(cagr) if cagr is not None else None,
        "total_return": float(values[-1] / initial - 1),
        "volatility": std * math.sqrt(252) if solvent else None,
        "sharpe": float(np.mean(returns) / std * math.sqrt(252)) if std > 1e-15 else None,
        "sharpe_basis": "zero_rate; compare daily returns with cash benchmark for excess Sharpe",
        "max_drawdown": float(drawdowns.max()),
        "recovery_sessions": int(longest),
        "unrecovered_at_end": bool(drawdowns[-1] > 1e-12),
        "turnover": float(sum(t["notional"] for t in trades) / np.mean(path)) if solvent else None,
        "average_exposure": float(frame.exposure.mean()),
        "maximum_exposure": float(frame.exposure.max()),
        "trade_count": len(trades),
        "buy_count": sum(t["side"] == "buy" for t in trades),
        "sell_count": sum(t["side"] == "sell" for t in trades),
        "trading_costs": float(sum(t["cost"] for t in trades)),
        "fees": float(sum(t["fees"] for t in trades)),
        "yearly_returns": yearly,
        "ending_equity": float(values[-1]),
        "wealth_exhausted": not solvent,
    }


def simulate(
    daily,
    minutes,
    actions,
    sessions,
    strategy,
    start,
    end,
    cost_bps=5.0,
    monthly_expense=0.0,
    fee_schedule=None,
) -> dict[str, Any]:
    if strategy not in {"trend", "rebound", "passive", "cash"}:
        raise ValueError("Unknown strategy")
    if (
        not math.isfinite(cost_bps)
        or cost_bps < 0
        or not math.isfinite(monthly_expense)
        or monthly_expense < 0
    ):
        raise ValueError("Costs must be finite and nonnegative")
    symbols = (CASH_SYMBOL,) if strategy == "cash" else UNIVERSE
    daily, opens, closes, actions, sessions, evaluation, cal = _prepare(
        daily, minutes, actions, sessions, symbols, start, end
    )
    history = daily.signal_close.unstack("symbol")
    if strategy in {"trend", "rebound"} and len(history.loc[history.index < evaluation[0]]) < 200:
        raise DataQualityError("At least 200 completed warm-up sessions are required")
    fees = _fee_rows(fee_schedule, evaluation[0])
    positions = {symbol: 0.0 for symbol in symbols}
    symbol_cashflows = {symbol: 0.0 for symbol in symbols}
    held = {}
    cash = INITIAL_EQUITY
    receivables = []
    trades, equity = [], []
    peak = INITIAL_EQUITY
    intraday_drawdown = 0.0
    halt = None
    liquidation_pending = False
    rate = cost_bps / 10000
    expenses = 0.0
    last_expense_date = pd.Timestamp(start) - pd.Timedelta(days=1)

    def value(prices):
        return cash + sum(r[1] for r in receivables) + sum(positions[s] * prices[s] for s in symbols)

    def trade(symbol, side, quantity, timestamp, reason):
        nonlocal cash
        if quantity <= 1e-9:
            return
        price = float(opens.loc[timestamp, symbol])
        notional = quantity * price
        cost = notional * rate
        fee = transaction_fee(
            side,
            quantity,
            notional,
            timestamp.tz_convert("America/New_York").tz_localize(None).normalize(),
            fees,
        )
        if side == "buy":
            if notional + cost + fee > cash + 1e-7:
                raise RuntimeError("Sizing would borrow cash")
            cash -= notional + cost + fee
            positions[symbol] += quantity
            held[symbol] = 0
        else:
            cash += notional - cost - fee
            positions[symbol] -= quantity
            if positions[symbol] < 1e-9:
                positions[symbol] = 0.0
                held.pop(symbol, None)
        symbol_cashflows[symbol] += (-notional if side == "buy" else notional) - cost - fee
        trades.append(
            {
                "timestamp": timestamp.isoformat(),
                "symbol": symbol,
                "side": side,
                "quantity": float(quantity),
                "price": price,
                "notional": float(notional),
                "cost": float(cost),
                "fees": float(fee),
                "reason": reason,
                "_fee_components": _fee_components(
                    side,
                    quantity,
                    notional,
                    timestamp.tz_convert("America/New_York").tz_localize(None).normalize(),
                    fees,
                ),
            }
        )

    def liquidate(timestamp):
        nonlocal liquidation_pending
        for symbol in symbols:
            trade(symbol, "sell", positions[symbol], timestamp, "drawdown_halt")
        liquidation_pending = False

    def observe(segment):
        nonlocal peak, halt, liquidation_pending, intraday_drawdown
        if segment.empty:
            return
        vals = (
            cash
            + sum(r[1] for r in receivables)
            + segment.to_numpy() @ np.array([positions[s] for s in symbols])
        )
        peaks = np.maximum.accumulate(np.r_[peak, vals])[1:]
        drawdowns = 1 - vals / peaks
        eligible = strategy in {"trend", "rebound"} and halt is None
        breach = np.flatnonzero(drawdowns >= DRAWDOWN_LIMIT - 1e-12) if eligible else np.array([], dtype=int)
        stop = int(breach[0]) if len(breach) else len(vals) - 1
        peak = max(peak, float(np.max(vals[: stop + 1])))
        intraday_drawdown = max(intraday_drawdown, float(np.max(drawdowns[: stop + 1])))
        if len(breach):
            stamp = segment.index[stop]
            halt = {"timestamp": stamp.isoformat(), "observed_drawdown": float(drawdowns[stop])}
            liquidation_pending = True
            if stop + 1 < len(segment):
                liquidate(segment.index[stop + 1])
                observe(segment.iloc[stop + 1 :])

    for session in evaluation:
        first_day_trade = len(trades)
        # Ex-date entitlement precedes new purchases, after the split's new-share basis.
        for action in actions[actions.session == session].itertuples():
            if action.symbol in positions:
                positions[action.symbol] *= float(action.split_ratio)
                amount = positions[action.symbol] * float(action.dividend)
                if amount:
                    receivables.append((action.pay_session, amount))
                    symbol_cashflows[action.symbol] += amount
        cash += sum(amount for pay, amount in receivables if pay <= session)
        receivables = [(pay, amount) for pay, amount in receivables if pay > session]
        beginning = cal.session_open(session)
        ending = cal.session_close(session)
        execution = session.tz_localize("America/New_York") + pd.Timedelta(hours=9, minutes=35)
        execution = execution.tz_convert("UTC")
        day_closes = closes.loc[beginning:ending - pd.Timedelta(minutes=1)]
        if liquidation_pending:
            liquidate(beginning)
        observe(day_closes.loc[day_closes.index < execution])
        if liquidation_pending:
            liquidate(execution)
        if halt is None:
            signal_sessions = sessions[sessions < session]
            previous = signal_sessions[-1] if len(signal_sessions) else None
            if strategy in {"passive", "cash"}:
                decisions = {s: "buy" for s in symbols} if session == evaluation[0] else {}
            else:
                # The next exchange session is in a new ISO week, including holiday-shortened weeks.
                weekly = previous.isocalendar()[:2] != session.isocalendar()[:2]
                decisions = decide(strategy, history.loc[:previous], held, weekly)
            prices = opens.loc[execution]
            for symbol, side in decisions.items():
                if side == "sell":
                    trade(symbol, "sell", positions[symbol], execution, "signal")
            for symbol, side in decisions.items():
                if side != "buy":
                    continue
                account_value = value(prices)
                exposure = sum(positions[s] * prices[s] for s in symbols)
                if strategy == "cash":
                    budget = max(0.0, cash - (0.04 if fees else 0.0))
                elif strategy == "passive":
                    budget = INITIAL_EQUITY / 12
                else:
                    budget = entry_notional(account_value, cash, exposure)
                quantity = math.floor(budget / (float(prices[symbol]) * (1 + rate)) * 1e6) / 1e6
                # Include date-effective fees in the budget; solve the monotone cost function.
                lo, hi = 0.0, quantity
                for _ in range(35):
                    mid = (lo + hi) / 2
                    total = mid * prices[symbol] * (1 + rate) + transaction_fee(
                        "buy", mid, mid * prices[symbol], session, fees
                    )
                    if total <= min(budget, cash):
                        lo = mid
                    else:
                        hi = mid
                quantity = math.floor(lo * 1e6) / 1e6
                trade(
                    symbol,
                    "buy",
                    quantity,
                    execution,
                    "initial_allocation" if strategy in {"cash", "passive"} else "signal",
                )
        observe(day_closes.loc[day_closes.index >= execution])
        # Match the broker's daily rounding independently for each fee type.
        day_trades = trades[first_day_trade:]
        for component in {k for t in day_trades for k in t["_fee_components"]}:
            charged = sum(t["_fee_components"].get(component, 0.0) for t in day_trades)
            extra = math.ceil(charged * 100 - 1e-10) / 100 - charged
            if extra > 1e-12:
                last = next(t for t in reversed(day_trades) if t["_fee_components"].get(component, 0.0) > 0)
                last["fees"] += extra
                symbol_cashflows[last["symbol"]] -= extra
                cash -= extra
        observe(day_closes.iloc[-1:])
        for symbol in held:
            held[symbol] += 1
        close_prices = day_closes.iloc[-1]
        total = float(value(close_prices))
        expense_date = pd.Timestamp(end) if session == evaluation[-1] else session
        days = (expense_date - last_expense_date).days
        expenses += monthly_expense * 12 * days / 365.2425
        last_expense_date = expense_date
        exposure = sum(positions[s] * close_prices[s] for s in symbols)
        equity.append(
            {
                "session": session.date().isoformat(),
                "equity": total,
                "equity_after_expenses": total - expenses,
                "cash": float(cash),
                "dividend_receivables": float(sum(r[1] for r in receivables)),
                "exposure": float(exposure / total) if total > 0 else 0.0,
            }
        )
    metrics = performance(equity, trades)
    metrics["symbol_pnl"] = {s: float(symbol_cashflows[s] + positions[s] * close_prices[s]) for s in symbols}
    metrics["end_of_day_max_drawdown"] = metrics["max_drawdown"]
    metrics["max_drawdown"] = max(metrics["max_drawdown"], intraday_drawdown)
    metrics["observed_intraday_max_drawdown"] = intraday_drawdown
    for t in trades:
        t.pop("_fee_components", None)
    return {
        "strategy": strategy,
        "start": str(pd.Timestamp(start).date()),
        "end": str(pd.Timestamp(end).date()),
        "cost_bps": cost_bps,
        "monthly_expense": monthly_expense,
        "metrics": metrics,
        "metrics_after_expenses": performance(equity, trades, equity_column="equity_after_expenses"),
        "equity": equity,
        "trades": trades,
        "halt": halt,
        "assumptions": {
            "fees_modeled": fee_schedule is not None,
            "fee_schedule": fee_schedule,
            "fee_model": "sec_taf_cat_daily_rounded"
            if fees
            and all(
                {"sec_per_dollar", "taf_per_share", "taf_maximum", "cat_per_share"}.issubset(r) for r in fees
            )
            else "generic_or_missing_unverified",
            "risk_sampling": "regular-session minute closes; next-minute-open liquidation",
            "fixed_expenses": "calendar-day accrual overlay, no change to strategy positions",
            "end_positions": "marked to final regular-session minute close; no forced sale",
            "cash_interest": "zero on idle cash; dividends held as receivables until payment",
            "corporate_actions_audited": True,
            "benchmark_stop": "passive and cash benchmarks do not use drawdown shutdown",
        },
    }
