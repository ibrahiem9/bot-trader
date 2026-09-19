"""Independent regression tests for wall-clock expiry and degraded monitoring."""

from datetime import UTC, datetime

import exchange_calendars as xcals
import numpy as np
import pandas as pd
import pytest
from test_paper import FakeBroker, FakeNotifications

from bot_trader.config import UNIVERSE
from bot_trader.paper import PaperRunner
from bot_trader.reporting import protocol_hash
from bot_trader.state import State


@pytest.fixture
def review_runtime(tmp_path):
    clock = {"now": datetime(2026, 9, 21, 13, 35, 59, tzinfo=UTC)}
    state = State(tmp_path / "review.sqlite")
    broker, notifications = FakeBroker(), FakeNotifications()
    runner = PaperRunner(state, broker, notifications, clock=lambda: clock["now"])
    state.set("account_id", "dedicated-paper")
    state.set("active_strategy", "trend")
    state.set("active_protocol_hash", protocol_hash())
    state.set("peak", 5000)
    calendar = xcals.get_calendar("XNYS", start="2025-01-01", end="2026-09-30")
    sessions = calendar.sessions_in_range("2025-01-02", "2026-09-30")
    completed = sessions[sessions < "2026-09-21"]
    closes = pd.DataFrame({s: 100 + np.arange(len(completed)) / 100 for s in UNIVERSE}, index=completed)
    yield runner, broker, notifications, clock, closes, sessions
    state.close()


def test_broker_latency_expiring_execution_window_prevents_submission(review_runtime, monkeypatch):
    runner, broker, _, clock, closes, sessions = review_runtime
    tick_started = clock["now"]

    def slow_eligibility(symbol):
        clock["now"] = datetime(2026, 9, 21, 13, 36, 1, tzinfo=UTC)
        return True

    monkeypatch.setattr(broker, "eligible", slow_eligibility)
    runner.tick(tick_started, closes, sessions)
    assert clock["now"].minute == 36
    assert not broker.submissions
    assert not any(i["submission_started"] for i in runner.state.intentions())


def test_heartbeat_failure_still_delivers_exception_email(review_runtime, monkeypatch):
    runner, broker, notifications, clock, closes, sessions = review_runtime

    def heartbeat_unavailable():
        raise RuntimeError("CloudWatch is unavailable; SNS still works")

    monkeypatch.setattr(notifications, "heartbeat", heartbeat_unavailable)
    runner.tick(clock["now"], closes, sessions)
    assert not broker.submissions
    assert runner.state.get("notification_error")
    assert notifications.messages
    assert any("heartbeat" in message.lower() for _, message in notifications.messages)


def test_afternoon_restart_can_send_daily_summary_without_price_acquisition(review_runtime, monkeypatch):
    runner, _, notifications, clock, _, _ = review_runtime
    clock["now"] = datetime(2026, 9, 21, 20, 10, tzinfo=UTC)

    def end_first_iteration(seconds):
        raise KeyboardInterrupt

    def unavailable_provider():
        raise RuntimeError("No price data; local exchange calendar still works")

    monkeypatch.setattr("bot_trader.paper.time.sleep", end_first_iteration)
    with pytest.raises(KeyboardInterrupt):
        runner.run(unavailable_provider)
    assert any("daily_summary" in subject for subject, _ in notifications.messages)
