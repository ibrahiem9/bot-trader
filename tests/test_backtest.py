import copy

import exchange_calendars as xcals
import numpy as np
import pandas as pd
import pytest

from bot_trader.backtest import DataQualityError, _fee_rows, performance, simulate, transaction_fee
from bot_trader.config import CASH_SYMBOL, UNIVERSE


@pytest.fixture
def market():
    cal = xcals.get_calendar("XNYS", start="2019-01-01", end="2020-01-31")
    sessions = cal.sessions_in_range("2019-01-02", "2020-01-24")
    symbols = (*UNIVERSE, CASH_SYMBOL)
    daily = []
    minutes = []
    for i, session in enumerate(sessions):
        price = 100 + i * 0.1
        daily.extend(
            {
                "session": session,
                "symbol": s,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 10000.0,
                "signal_close": price,
            }
            for s in symbols
        )
        if session >= pd.Timestamp("2020-01-06"):
            stamps = pd.date_range(
                cal.session_open(session), cal.session_close(session), freq="min", inclusive="left"
            )
            for s in symbols:
                minutes.append(
                    pd.DataFrame(
                        {
                            "timestamp": stamps,
                            "symbol": s,
                            "open": price,
                            "high": price,
                            "low": price,
                            "close": price,
                            "volume": 100.0,
                        }
                    )
                )
    actions = pd.DataFrame(columns=["session", "symbol", "split_ratio", "dividend", "pay_session"])
    actions.attrs["audited"] = True
    return {
        "daily": pd.DataFrame(daily),
        "minutes": pd.concat(minutes, ignore_index=True),
        "actions": actions,
        "sessions": sessions,
        "start": "2020-01-06",
        "end": "2020-01-24",
    }


def test_next_session_execution_fractional_sizing_and_holiday(market):
    result = simulate(**market, strategy="trend", cost_bps=5)
    assert len(result["trades"]) == 6
    assert {t["timestamp"] for t in result["trades"]} == {"2020-01-06T14:35:00+00:00"}
    assert all(t["quantity"] != int(t["quantity"]) for t in result["trades"])
    assert result["equity"][0]["exposure"] <= 0.5
    assert all(row["cash"] >= 0 for row in result["equity"])
    assert "2020-01-20" not in {r["session"] for r in result["equity"]}
    assert sum(result["metrics"]["symbol_pnl"].values()) == pytest.approx(
        result["metrics"]["ending_equity"] - 5000
    )


def test_real_simulator_outputs_render_and_serialize_without_adapter(market):
    """A short invented market checks the engine/report boundary, not returns."""
    import json

    from bot_trader.reporting import _expense_metrics, relative_metrics, render

    cash = simulate(**market, strategy="cash")
    runs = []
    for strategy in ("trend", "rebound", "passive", "cash"):
        run = cash if strategy == "cash" else simulate(**market, strategy=strategy)
        run["period"] = "synthetic_integration"
        run["relative"] = relative_metrics(run, cash)
        run["operating_expenses"] = [_expense_metrics(run, expense) for expense in (15, 100)]
        runs.append(run)
    report = {"status": "complete", "results": runs,
              "qualification": {"eligible_strategies": [], "selected_strategy": None}}
    json.dumps(report, allow_nan=False)
    markdown = render(report)
    assert "Neither candidate passes" in markdown
    assert "synthetic_integration" in markdown
    assert "End-of-day drawdown" in markdown


def test_missing_minutes_actions_and_calendar_block_conclusions(market):
    broken = {**market, "minutes": market["minutes"].iloc[1:]}
    with pytest.raises(DataQualityError, match="minute bars"):
        simulate(**broken, strategy="trend")
    broken = copy.deepcopy(market)
    broken["actions"].attrs.clear()
    with pytest.raises(DataQualityError, match="explicitly audited"):
        simulate(**broken, strategy="trend")
    broken = {**market, "sessions": market["sessions"].delete(10)}
    with pytest.raises(DataQualityError, match="exchange trading dates"):
        simulate(**broken, strategy="trend")


def test_future_data_does_not_change_past_decisions(market):
    cutoff = "2020-01-10"
    first = simulate(**{**market, "end": cutoff}, strategy="trend")
    changed = copy.deepcopy(market)
    changed["daily"].loc[
        changed["daily"].session > cutoff, ["open", "high", "low", "close", "signal_close"]
    ] *= 10
    changed["minutes"].loc[changed["minutes"].timestamp > "2020-01-11", ["open", "high", "low", "close"]] *= (
        10
    )
    second = simulate(**{**changed, "end": cutoff}, strategy="trend")
    assert first == second


def crash_market(market, time="2020-01-07T15:00:00Z"):
    stamp = pd.Timestamp(time)
    mask = (market["minutes"].timestamp >= stamp) & market["minutes"].symbol.isin(UNIVERSE)
    market["minutes"].loc[mask, ["open", "high", "low", "close"]] *= 0.7
    next_open = market["minutes"].timestamp == stamp + pd.Timedelta(minutes=1)
    market["minutes"].loc[next_open, ["open", "low"]] *= 0.9


def test_intraday_drawdown_next_minute_liquidation_and_persistent_halt(market):
    crash_market(market)
    result = simulate(**market, strategy="trend")
    assert result["halt"]["timestamp"] == "2020-01-07T15:00:00+00:00"
    sells = [t for t in result["trades"] if t["side"] == "sell"]
    assert len(sells) == 6
    assert {t["timestamp"] for t in sells} == {"2020-01-07T15:01:00+00:00"}
    assert all(t["reason"] == "drawdown_halt" for t in sells)
    assert len(result["trades"]) == 12
    assert result["metrics"]["max_drawdown"] > 0.15  # execution gap exceeds threshold
    assert result["equity"][-1]["exposure"] == 0


def test_end_of_session_halt_liquidates_next_session_open(market):
    crash_market(market, "2020-01-10T20:59:00Z")
    result = simulate(**market, strategy="trend")
    sells = [t for t in result["trades"] if t["side"] == "sell"]
    assert {t["timestamp"] for t in sells} == {"2020-01-13T14:30:00+00:00"}


def test_splits_and_dividend_receivables_survive_sale(market):
    ex = pd.Timestamp("2020-01-07")
    actions = pd.DataFrame(
        [
            {
                "session": ex,
                "symbol": "SPY",
                "split_ratio": 2.0,
                "dividend": 1.0,
                "pay_session": pd.Timestamp("2020-01-09"),
            }
        ]
    )
    actions.attrs["audited"] = True
    market["actions"] = actions
    day_mask = (market["daily"].symbol == "SPY") & (market["daily"].session >= ex)
    market["daily"].loc[day_mask, ["open", "high", "low", "close"]] /= 2
    min_mask = (market["minutes"].symbol == "SPY") & (market["minutes"].timestamp >= ex.tz_localize("UTC"))
    market["minutes"].loc[min_mask, ["open", "high", "low", "close"]] /= 2
    result = simulate(**market, strategy="passive", cost_bps=0)
    shares = next(t["quantity"] for t in result["trades"] if t["symbol"] == "SPY")
    rows = {r["session"]: r for r in result["equity"]}
    assert rows["2020-01-07"]["dividend_receivables"] == pytest.approx(2 * shares)
    assert rows["2020-01-07"]["cash"] == rows["2020-01-06"]["cash"]
    assert rows["2020-01-09"]["dividend_receivables"] == 0
    assert rows["2020-01-09"]["cash"] == pytest.approx(rows["2020-01-06"]["cash"] + 2 * shares)
    assert result["metrics"]["max_drawdown"] < 0.001
    crash_market(market)
    halted = simulate(**market, strategy="trend", cost_bps=0)
    rows = {r["session"]: r for r in halted["equity"]}
    assert rows["2020-01-07"]["exposure"] == 0
    assert rows["2020-01-07"]["dividend_receivables"] > 0
    assert rows["2020-01-09"]["cash"] == pytest.approx(
        rows["2020-01-07"]["cash"] + rows["2020-01-07"]["dividend_receivables"]
    )


def test_higher_costs_and_fixed_expenses_reduce_returns(market):
    low = simulate(**market, strategy="passive", cost_bps=5)
    high = simulate(**market, strategy="passive", cost_bps=20, monthly_expense=100)
    assert high["metrics"]["ending_equity"] < low["metrics"]["ending_equity"]
    assert high["metrics_after_expenses"]["ending_equity"] < high["metrics"]["ending_equity"]
    assert high["metrics"]["fees"] == 0
    assert high["assumptions"]["fees_modeled"] is False


def test_fee_effective_dates_caps_and_daily_rounding(market):
    schedule = [
        {
            "effective_date": "2020-01-01",
            "sec_per_dollar": 0.00002,
            "taf_per_share": 0.000195,
            "taf_maximum": 9.79,
            "cat_per_share": 0.000003,
        },
        {
            "effective_date": "2020-01-07",
            "sec_per_dollar": 0.00003,
            "taf_per_share": 0.0002,
            "taf_maximum": 9.79,
            "cat_per_share": 0.000003,
        },
    ]
    parsed = _fee_rows(schedule, pd.Timestamp("2020-01-06"))
    old = transaction_fee("sell", 100000, 1000000, pd.Timestamp("2020-01-06"), parsed)
    new = transaction_fee("sell", 100000, 1000000, pd.Timestamp("2020-01-07"), parsed)
    assert old == pytest.approx(20 + 9.79 + 0.3)
    assert new == pytest.approx(30 + 9.79 + 0.3)
    crash_market(market)
    result = simulate(**market, strategy="trend", fee_schedule=schedule)
    buys = [t for t in result["trades"] if t["side"] == "buy"]
    assert sum(t["fees"] for t in buys) == pytest.approx(0.01)  # one daily CAT rounding, not six
    sells = [t for t in result["trades"] if t["side"] == "sell"]
    expected_sec = np.ceil(sum(t["notional"] for t in sells) * 0.00003 * 100) / 100
    expected_taf = np.ceil(sum(t["quantity"] for t in sells) * 0.0002 * 100) / 100
    expected_cat = np.ceil(sum(t["quantity"] for t in sells) * 0.000003 * 100) / 100
    assert sum(t["fees"] for t in sells) == pytest.approx(expected_sec + expected_taf + expected_cat)
    assert sum(result["metrics"]["symbol_pnl"].values()) == pytest.approx(
        result["metrics"]["ending_equity"] - 5000
    )


def test_passive_benchmarks_do_not_apply_strategy_halt(market):
    crash_market(market)
    result = simulate(**market, strategy="passive")
    assert result["halt"] is None
    assert result["metrics"]["max_drawdown"] > 0.1
    cash = simulate(**market, strategy="cash")
    assert cash["trades"][0]["symbol"] == CASH_SYMBOL
    assert cash["equity"][0]["exposure"] > 0.99


def test_exhausted_expense_overlay_has_no_fictitious_compounded_return():
    result = performance(
        [
            {"session": "2020-01-06", "equity": 1.0, "exposure": 0.5},
            {"session": "2020-01-07", "equity": 0.0, "exposure": 0.5},
            {"session": "2020-01-08", "equity": -1.0, "exposure": 0.5},
        ],
        [],
    )
    assert result["wealth_exhausted"]
    assert result["annualized_return"] is None
    assert result["sharpe"] is None
    assert result["volatility"] is None


def test_early_close_calendar_and_execution(market):
    cal = xcals.get_calendar("XNYS", start="2019-01-01", end="2020-01-31")
    market["sessions"] = market["sessions"][market["sessions"] <= "2019-11-29"]
    market["daily"] = market["daily"][market["daily"].session <= "2019-11-29"]
    chunks = []
    for session in cal.sessions_in_range("2019-11-27", "2019-11-29"):
        stamps = pd.date_range(
            cal.session_open(session), cal.session_close(session), freq="min", inclusive="left"
        )
        for s in (*UNIVERSE, CASH_SYMBOL):
            row = market["daily"][(market["daily"].session == session) & (market["daily"].symbol == s)].iloc[
                0
            ]
            chunks.append(
                pd.DataFrame(
                    {
                        "timestamp": stamps,
                        "symbol": s,
                        "open": row.open,
                        "high": row.high,
                        "low": row.low,
                        "close": row.close,
                        "volume": 100.0,
                    }
                )
            )
    market.update(minutes=pd.concat(chunks), start="2019-11-27", end="2019-11-29")
    result = simulate(**market, strategy="passive")
    assert len(result["equity"]) == 2
    assert result["trades"][0]["timestamp"] == "2019-11-27T14:35:00+00:00"
    assert market["minutes"].timestamp.max() == pd.Timestamp("2019-11-29T17:59:00Z")
