import json
from datetime import UTC, datetime

import pandas as pd
import pytest

from bot_trader.config import UNIVERSE
from bot_trader.paper import PaperRunner
from bot_trader.state import State


class FakeBroker:
    def __init__(self):
        self.equity, self.cash = 5000.0, 5000.0
        self.holdings, self.orders, self.submissions = {}, {}, []
        self.mode, self.open, self.eligible_asset = "filled", True, True

    def account(self):
        return {"id": "dedicated-paper", "equity": self.equity, "cash": self.cash,
                "blocked": False, "short_market_value": 0}

    def positions(self):
        return {symbol: {"qty": qty, "market_value": qty * 100}
                for symbol, qty in self.holdings.items() if qty > 1e-9}

    def lookup(self, client_id):
        return self.orders.get(client_id)

    def open_orders(self):
        return [o for o in self.orders.values() if o["status"] not in {"filled", "canceled", "rejected"}]

    def submit(self, client_id, symbol, side, notional=None, qty=None):
        assert client_id not in self.orders
        self.submissions.append((client_id, symbol, side, notional, qty))
        if self.mode == "timeout_before":
            raise TimeoutError("unknown outcome")
        filled = qty if qty is not None else notional / 100
        status = "filled" if self.mode == "timeout_after" else self.mode
        if status == "partially_filled":
            filled /= 2
        elif status in {"new", "rejected"}:
            filled = 0
        sign = 1 if side == "buy" else -1
        self.holdings[symbol] = self.holdings.get(symbol, 0) + sign * filled
        self.cash -= sign * filled * 100
        self.orders[client_id] = {"client_id": client_id, "id": client_id, "symbol": symbol,
                                  "side": side, "status": status, "filled_qty": filled}
        if self.mode == "timeout_after":
            raise TimeoutError("response lost after fill")
        return self.orders[client_id]

    def eligible(self, symbol):
        return self.eligible_asset

    def market_open(self):
        return self.open

    def cancel(self, order):
        self.orders[order["client_id"]]["status"] = "canceled"


class FakeNotifications:
    def __init__(self):
        self.fail, self.messages = False, []

    def heartbeat(self):
        if self.fail:
            raise RuntimeError("cloud unavailable")

    def send(self, subject, message):
        if self.fail:
            raise RuntimeError("SNS unavailable")
        self.messages.append((subject, message))


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    state = State(tmp_path / "state.sqlite")
    broker, notifications = FakeBroker(), FakeNotifications()
    runner = PaperRunner(state, broker, notifications,
                         clock=lambda: datetime(2026, 9, 14, 13, 35, tzinfo=UTC))
    state.set("account_id", "dedicated-paper")
    state.set("active_strategy", "trend")
    from bot_trader.reporting import protocol_hash
    state.set("active_protocol_hash", protocol_hash())
    state.set("peak", 5000)
    monkeypatch.setattr("bot_trader.paper.decide", lambda *a: {"SPY": "buy", "IWM": "buy"})
    yield runner, broker, notifications
    state.close()


@pytest.fixture
def data():
    now = datetime(2026, 9, 14, 13, 35, tzinfo=UTC)
    sessions = pd.bdate_range("2025-01-01", "2026-09-22")
    closes = pd.DataFrame(100.0, index=sessions[sessions < "2026-09-14"], columns=UNIVERSE)
    return now, closes, sessions


def test_initially_inactive_and_qualification_required(tmp_path, monkeypatch):
    monkeypatch.setattr("bot_trader.reporting.verify_qualification", lambda path: json.loads(path.read_text()))
    state = State(tmp_path / "state.sqlite")
    broker, notifications = FakeBroker(), FakeNotifications()
    runner = PaperRunner(state, broker, notifications)
    runner.tick()
    assert not broker.submissions
    artifact = tmp_path / "qualification.json"
    artifact.write_text(json.dumps({"eligible_strategies": ["trend"], "data_audit_passed": False}))
    with pytest.raises(ValueError):
        runner.activate(artifact, "trend")
    artifact.write_text(json.dumps({"eligible_strategies": ["trend"], "selected_strategy": "trend",
                                    "data_audit_passed": True, "final_test_end": "2026-09-16",
                                    "protocol_hash": "frozen", "dataset_hash": "audited", "data_feed": "iex"}))
    broker.equity = 5001
    with pytest.raises(ValueError, match="dedicated"):
        runner.activate(artifact, "trend")
    broker.equity = 5000
    runner.activate(artifact, "trend")
    assert runner.status()["active_strategy"] == "trend"
    assert state.get("active_data_feed") == "iex"
    with pytest.raises(RuntimeError, match="already initialized"):
        runner.activate(artifact, "trend")
    state.close()


def test_sizing_fractional_and_restart_no_duplicates(runtime, data):
    runner, broker, notifications = runtime
    runner.tick(*data)
    assert len(broker.submissions) == 2
    assert broker.submissions[0][3] == 416.66
    restarted = PaperRunner(runner.state, broker, notifications, clock=runner.clock)
    restarted.tick(*data)
    assert len(broker.submissions) == 2
    assert sum(p["market_value"] for p in broker.positions().values()) <= 2500


def test_changed_research_code_blocks_signals_but_still_monitors_drawdown(runtime, data):
    runner, broker, notifications = runtime
    runner.state.set("active_protocol_hash", "old-code")
    restarted = PaperRunner(runner.state, broker, notifications, clock=runner.clock)
    restarted.tick(*data)
    assert not broker.submissions
    assert "Research code changed" in restarted.status()["suspension"]
    broker.equity = 4400
    restarted.tick(*data)
    assert restarted.status()["halt"]


@pytest.mark.parametrize("mode,count", [("timeout_before", 1), ("timeout_after", 2)])
def test_ambiguous_submission_never_duplicates(runtime, data, mode, count):
    runner, broker, notifications = runtime
    broker.mode = mode
    runner.tick(*data)
    broker.mode = "filled"
    PaperRunner(runner.state, broker, notifications, clock=runner.clock).tick(*data)
    assert len(broker.submissions) == count
    assert len({order[0] for order in broker.submissions}) == count
    if mode == "timeout_before":
        assert "unknown" in runner.status()["suspension"]


@pytest.mark.parametrize("mode", ["partially_filled", "rejected", "new"])
def test_partial_rejected_unresolved_suspend_entries(runtime, data, mode):
    runner, broker, _ = runtime
    broker.mode = mode
    runner.tick(*data)
    runner.tick(*data)
    assert len(broker.submissions) == 1
    assert runner.status()["suspension"]


def test_reconciled_fill_allows_remaining_plan(runtime, data):
    runner, broker, _ = runtime
    broker.mode = "new"
    runner.tick(*data)
    first = broker.submissions[0]
    order = broker.orders[first[0]]
    order.update(status="filled", filled_qty=first[3] / 100)
    broker.holdings[first[1]] = first[3] / 100
    broker.cash -= first[3]
    broker.mode = "filled"
    runner.tick(*data)
    assert len(broker.submissions) == 2


def test_stale_missing_sessions_and_market_holiday(runtime, data):
    runner, broker, _ = runtime
    now, closes, sessions = data
    runner.tick(now, closes.iloc[:-1], sessions)
    assert "Stale" in runner.status()["suspension"]
    runner.tick(now, closes.drop(closes.index[-20]), sessions)
    assert "missing exchange" in runner.status()["suspension"]
    runner.tick(now, closes, sessions[sessions != pd.Timestamp(now.date())])
    assert not broker.submissions


def test_unknown_positions_including_split_discrepancy_block(runtime, data):
    runner, broker, _ = runtime
    broker.holdings["SPY"] = 2
    runner.tick(*data)
    assert "position difference" in runner.status()["suspension"]
    assert not broker.submissions


def test_ineligible_fractional_asset_blocks(runtime, data):
    runner, broker, _ = runtime
    broker.eligible_asset = False
    runner.tick(*data)
    assert not broker.submissions


def test_email_heartbeat_failure_blocks_entries_and_recovers(runtime, data):
    runner, broker, notifications = runtime
    notifications.fail = True
    runner.tick(*data)
    assert not broker.submissions
    assert runner.status()["notification_error"]
    assert runner.state.db.execute("SELECT COUNT(*) FROM events WHERE delivered=0").fetchone()[0] > 0
    notifications.fail = False
    runner.tick(*data)
    runner.tick(*data)  # Clear separate heartbeat and email failure events first.
    assert len(broker.submissions) == 2
    assert runner.status()["notification_error"] is None


def test_drawdown_cancels_pending_entries_liquidates_and_survives_restart(runtime, data):
    runner, broker, notifications = runtime
    broker.mode = "partially_filled"
    runner.tick(*data)
    broker.mode = "filled"
    broker.equity = 4499
    runner.tick(*data)
    assert runner.status()["halt"]
    assert not broker.positions()
    assert [s[2] for s in broker.submissions] == ["buy", "sell"]
    PaperRunner(runner.state, broker, notifications, clock=runner.clock).tick(*data)
    assert len(broker.submissions) == 2
    assert runner.status()["halt"]


def test_halt_waits_for_market_open_and_survives_alert_failure(runtime, data):
    runner, broker, notifications = runtime
    runner.tick(*data)
    notifications.fail = True
    broker.open = False
    broker.equity = 4400
    runner.tick(*data)
    assert len(broker.submissions) == 2
    assert runner.status()["halt"]
    broker.open = True
    runner.tick(*data)
    assert not broker.positions()


def test_peak_tracks_gain_not_just_initial_balance(runtime, data):
    runner, broker, _ = runtime
    now, _, _ = data
    broker.equity = 5500
    runner.tick(now)
    broker.equity = 4950
    runner.tick(now)
    assert runner.status()["halt"]
    assert runner.status()["peak"] == 5500


def test_no_late_execution(runtime, data):
    runner, broker, _ = runtime
    now, closes, sessions = data
    runner.tick(now.replace(minute=36), closes, sessions)
    assert not broker.submissions


def test_process_lock_blocks_concurrent_runner(runtime, data):
    runner, _, _ = runtime
    other = State(runner.state.path)
    with other.lock(), pytest.raises(RuntimeError, match="Another paper process"):
        runner.tick(*data)
    other.close()


def test_daily_and_weekly_notifications_once(runtime, data):
    runner, _, notifications = runtime
    _, closes, sessions = data
    friday = datetime(2026, 9, 18, 20, 10, tzinfo=UTC)
    runner.tick(friday, closes, sessions)
    runner.tick(friday, closes, sessions)
    subjects = [subject for subject, _ in notifications.messages]
    assert subjects.count("Paper bot: daily_summary") == 1
    assert subjects.count("Paper bot: weekly_review") == 1


def test_six_entries_respect_exposure_and_cash(runtime, data, monkeypatch):
    runner, broker, _ = runtime
    monkeypatch.setattr("bot_trader.paper.decide", lambda *a: dict.fromkeys(UNIVERSE, "buy"))
    runner.tick(*data)
    assert len(broker.submissions) == 6
    assert sum(p["market_value"] for p in broker.positions().values()) <= 2500
    assert broker.cash >= 2500


def test_low_cash_cannot_borrow(runtime, data):
    runner, broker, _ = runtime
    broker.cash = 10
    runner.tick(*data)
    assert 9.94 <= broker.submissions[0][3] <= 9.95
    assert broker.cash >= 0


def test_rejected_liquidation_retries_after_backoff(runtime, data):
    runner, broker, _ = runtime
    now, closes, sessions = data
    runner.tick(*data)
    broker.equity = 4400
    broker.mode = "rejected"
    runner.tick(*data)
    count = len(broker.submissions)
    runner.tick(*data)
    assert len(broker.submissions) == count
    broker.mode = "filled"
    runner.tick(now.replace(minute=36), closes, sessions)
    assert not broker.positions()


def test_broker_has_only_paper_construction(monkeypatch):
    from bot_trader.broker import AlpacaBroker
    captured = {}
    def factory(*args, **kwargs):
        from types import SimpleNamespace
        captured.update(kwargs)
        return SimpleNamespace(_session=SimpleNamespace(request=lambda *args, **kwargs: None))
    monkeypatch.setenv("APCA_API_KEY_ID", "fake")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "fake")
    monkeypatch.setattr("alpaca.trading.client.TradingClient", factory)
    AlpacaBroker()
    assert captured == {"paper": True}


def test_forged_qualification_is_not_accepted(tmp_path):
    from bot_trader.data import DataError

    state = State(tmp_path / "state.sqlite")
    runner = PaperRunner(state, FakeBroker(), FakeNotifications())
    artifact = tmp_path / "qualification.json"
    artifact.write_text(json.dumps({"eligible_strategies": ["trend"], "selected_strategy": "trend",
                                   "data_audit_passed": True, "final_test_end": "2026-09-16",
                                   "protocol_hash": "forged", "dataset_hash": "forged", "data_feed": "iex"}))
    with pytest.raises(DataError, match="stale"):
        runner.activate(artifact, "trend")
    assert state.get("account_id") is None
    state.close()


def test_summary_calendar_survives_restart_after_execution_window(runtime, monkeypatch):
    runner, _, notifications = runtime
    runner.clock = lambda: datetime(2026, 9, 18, 20, 11, tzinfo=UTC)
    requested = []
    def stop(_interval):
        raise KeyboardInterrupt
    monkeypatch.setattr("bot_trader.paper.time.sleep", stop)
    with pytest.raises(KeyboardInterrupt):
        runner.run(lambda: requested.append(True))
    assert not requested
    subjects = [subject for subject, _ in notifications.messages]
    assert "Paper bot: daily_summary" in subjects
    assert "Paper bot: weekly_review" in subjects


def test_heartbeat_failure_can_still_deliver_exception_email(runtime, data, monkeypatch):
    runner, broker, notifications = runtime
    def fail():
        raise RuntimeError("CloudWatch only is down")
    monkeypatch.setattr(notifications, "heartbeat", fail)
    runner.tick(*data)
    assert not broker.submissions
    assert any("heartbeat_error" in subject for subject, _ in notifications.messages)
    assert "Heartbeat failed" in runner.status()["notification_error"]


def test_prefetch_starts_before_execution_window(runtime, data, monkeypatch):
    from concurrent.futures import Future

    runner, broker, _ = runtime
    now, closes, sessions = data
    runner.clock = lambda: now.replace(minute=25)
    requested, sleeps = [], []
    class ImmediateExecutor:
        def __init__(self, **kwargs):
            pass
        def submit(self, function):
            future = Future()
            future.set_result(function())
            return future
        def shutdown(self, **kwargs):
            pass
    def provider():
        requested.append(True)
        return closes, sessions
    def stop(_interval):
        sleeps.append(True)
        if len(sleeps) == 2:
            raise KeyboardInterrupt
    monkeypatch.setattr("concurrent.futures.ThreadPoolExecutor", ImmediateExecutor)
    monkeypatch.setattr("bot_trader.paper.time.sleep", stop)
    with pytest.raises(KeyboardInterrupt):
        runner.run(provider)
    assert len(requested) == 1
    assert not broker.submissions
