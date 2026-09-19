from types import SimpleNamespace

import pytest

from bot_trader.notifications import AwsNotifications


@pytest.fixture
def aws(monkeypatch):
    state = SimpleNamespace(now=0.0, subscriptions=0, metrics=[], subscription_error=False,
                            metric_error=False, confirmed=True)

    def paginate(**kwargs):
        state.subscriptions += 1
        if state.subscription_error:
            raise RuntimeError("SNS unavailable")
        arn = "arn:aws:sns:us-east-1:123:paper:subscription" if state.confirmed else "PendingConfirmation"
        return [{"Subscriptions": [{"Protocol": "email", "SubscriptionArn": arn}]}]

    def put_metric_data(**kwargs):
        state.metrics.append(kwargs)
        if state.metric_error:
            raise RuntimeError("CloudWatch unavailable")

    sns = SimpleNamespace(get_paginator=lambda name: SimpleNamespace(paginate=paginate))
    cloudwatch = SimpleNamespace(put_metric_data=put_metric_data)
    monkeypatch.setattr("boto3.client", lambda name, **kwargs: sns if name == "sns" else cloudwatch)
    return AwsNotifications("arn:aws:sns:us-east-1:123:paper", clock=lambda: state.now), state


def test_heartbeat_and_subscription_check_throttled_together(aws):
    notifications, state = aws
    notifications.heartbeat()
    for second in (0, 5, 10, 30, 59.999):
        state.now = second
        notifications.heartbeat()
    assert state.subscriptions == 1
    assert len(state.metrics) == 1
    state.now = 60
    notifications.heartbeat()
    assert state.subscriptions == 2
    assert len(state.metrics) == 2
    # Matches deploy/alarms.yaml: this is a dimensionless BotTrader/Heartbeat metric.
    assert state.metrics[-1] == {"Namespace": "BotTrader", "MetricData": [
        {"MetricName": "Heartbeat", "Value": 1, "Unit": "Count"}]}


@pytest.mark.parametrize("failure", ["subscription_error", "metric_error", "unconfirmed"])
def test_failed_heartbeat_is_not_cached(aws, failure):
    notifications, state = aws
    if failure == "unconfirmed":
        state.confirmed = False
    else:
        setattr(state, failure, True)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            notifications.heartbeat()
    assert state.subscriptions == 2
    assert notifications._last_heartbeat_success is None
    state.subscription_error = state.metric_error = False
    state.confirmed = True
    notifications.heartbeat()
    assert state.subscriptions == 3
    notifications.heartbeat()
    assert state.subscriptions == 3


def test_failure_after_prior_success_retries_without_another_minute_wait(aws):
    notifications, state = aws
    notifications.heartbeat()
    state.now = 60
    state.metric_error = True
    with pytest.raises(RuntimeError):
        notifications.heartbeat()
    assert notifications._last_heartbeat_success == 0
    state.now = 65
    state.metric_error = False
    notifications.heartbeat()
    assert len(state.metrics) == 3
    assert notifications._last_heartbeat_success == 65
