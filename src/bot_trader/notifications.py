"""SNS delivery and an independently alarmed CloudWatch heartbeat."""
from __future__ import annotations

import time


class AwsNotifications:
    def __init__(self, topic_arn, namespace="BotTrader", region="us-east-1", clock=None):
        import boto3
        from botocore.config import Config

        if not topic_arn:
            raise ValueError("An SNS topic with a confirmed email subscription is required")
        self.topic = topic_arn
        self.namespace = namespace
        self.clock = clock or time.monotonic
        self._last_heartbeat_success = None
        network = Config(connect_timeout=3, read_timeout=5, retries={"total_max_attempts": 1})
        self.sns = boto3.client("sns", region_name=region, config=network)
        self.cloudwatch = boto3.client("cloudwatch", region_name=region, config=network)

    def heartbeat(self):
        if (self._last_heartbeat_success is not None
                and self.clock() - self._last_heartbeat_success < 60):
            return
        # Publishing to SNS without a confirmed subscriber is silently ineffective.
        subscriptions = []
        paginator = self.sns.get_paginator("list_subscriptions_by_topic")
        for page in paginator.paginate(TopicArn=self.topic):
            subscriptions.extend(page["Subscriptions"])
        if not any(s["Protocol"] == "email" and s["SubscriptionArn"].startswith("arn:")
                   for s in subscriptions):
            raise RuntimeError("SNS has no confirmed email subscriber")
        self.cloudwatch.put_metric_data(Namespace=self.namespace, MetricData=[
            {"MetricName": "Heartbeat", "Value": 1, "Unit": "Count"}])
        # Cache only completed subscription checks AND accepted metric writes.
        # Exceptions leave the old timestamp untouched and retry on the next tick.
        self._last_heartbeat_success = self.clock()

    def send(self, subject, message):
        self.sns.publish(TopicArn=self.topic, Subject=subject[:100], Message=message)


class DisabledNotifications:
    """Monitoring can run locally; disabled delivery always prevents entries."""
    def heartbeat(self):
        raise RuntimeError("SNS email and CloudWatch heartbeat are not configured")

    def send(self, subject, message):
        raise RuntimeError("Notifications are not configured")
