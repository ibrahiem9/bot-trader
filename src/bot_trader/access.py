"""Read-only authenticated admission probes; no orders or account changes."""
from __future__ import annotations

from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import pandas as pd

from .data import SYMBOLS, credentials, external_path, write_json


def preflight(output: Path) -> dict:
    """Sample availability before committing to a multi-year minute download.

    A failed sample proves the frozen complete-data requirement cannot pass.
    Successful samples do not establish full coverage or review actions/fees.
    """
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.historical.corporate_actions import CorporateActionsClient
    from alpaca.data.requests import CorporateActionsRequest, StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    from .broker import AlpacaBroker

    output = external_path(output)
    key, secret = credentials()
    broker = AlpacaBroker()
    account = broker.account()
    positions, orders = broker.positions(), broker.open_orders()
    report = {"checked_at": datetime.now(UTC).isoformat(), "paper_authenticated": True,
              "paper_account": {"equity": account["equity"], "position_count": len(positions),
                                "open_order_count": len(orders), "blocked": account["blocked"]},
              "initial_account_eligible": 0 < account["equity"] <= 5000 and not positions and not orders
              and not account["blocked"] and account["cash"] >= 0 and account["short_market_value"] == 0,
              "checks": [], "complete_data_requirement_satisfied": False,
              "note": "Read-only samples; no performance evaluation, fees review, or strategy activation"}
    client = StockHistoricalDataClient(key, secret)
    client._session.request = partial(client._session.request, timeout=(5, 20))
    client._retry = 0
    for feed in ("iex", "sip"):
        for kind, start, end in (
            ("daily_warmup", "2016-01-04T05:00:00Z", "2016-01-09T04:59:59Z"),
            ("first_evaluation_minutes", "2017-01-03T14:30:00Z", "2017-01-03T20:59:59Z"),
        ):
            row = {"feed": feed, "sample": kind, "start": start, "end": end}
            expected = pd.date_range(start, end, freq="D" if kind == "daily_warmup" else "min")
            try:
                bars = client.get_stock_bars(StockBarsRequest(
                    symbol_or_symbols=list(SYMBOLS), start=pd.Timestamp(start).to_pydatetime(),
                    end=pd.Timestamp(end).to_pydatetime(),
                    timeframe=TimeFrame.Day if kind == "daily_warmup" else TimeFrame.Minute,
                    feed=DataFeed(feed), adjustment=Adjustment.RAW, asof="2026-09-16",
                )).data
                row["counts"] = {s: len(bars.get(s, [])) for s in SYMBOLS}
                row["expected_per_symbol"] = len(expected)
                timestamps = {
                    s: pd.DatetimeIndex(pd.to_datetime([b.timestamp for b in bars.get(s, [])], utc=True))
                    for s in SYMBOLS
                }
                row["coverage"] = {}
                for symbol, stamps in timestamps.items():
                    missing, unexpected = expected.difference(stamps), stamps.difference(expected)
                    row["coverage"][symbol] = {
                        "missing_count": len(missing), "duplicate_count": int(stamps.duplicated().sum()),
                        "unexpected_count": len(unexpected),
                        "missing_examples": [stamp.isoformat() for stamp in missing[:5]],
                        "unexpected_examples": [stamp.isoformat() for stamp in unexpected[:5]],
                    }
                row["coverage_passed"] = all(
                    not (detail["missing_count"] or detail["duplicate_count"] or detail["unexpected_count"])
                    for detail in row["coverage"].values()
                )
                if kind == "first_evaluation_minutes":
                    row["exact_0935_present"] = {
                        s: pd.Timestamp("2017-01-03T14:35:00Z") in stamps for s, stamps in timestamps.items()
                    }
            except Exception as exc:  # noqa: BLE001 - record access failures without exposing request headers
                row.update(coverage_passed=False, error_type=type(exc).__name__)
                if getattr(exc, "status_code", None) is not None:
                    row["http_status"] = exc.status_code
            report["checks"].append(row)
    try:
        action_client = CorporateActionsClient(key, secret)
        action_client._session.request = partial(action_client._session.request, timeout=(5, 20))
        action_client._retry = 0
        response = action_client.get_corporate_actions(CorporateActionsRequest(
            symbols=list(SYMBOLS), start=pd.Timestamp("2016-01-01").date(),
            end=pd.Timestamp("2026-09-16").date()
        )).model_dump(mode="json")
        write_json(output.parent / "provider-actions-unreviewed.json", response)
        report["corporate_actions"] = {"downloaded": True, "reviewed": False,
                                       "counts": {k: len(v) for k, v in response.get("data", {}).items()}}
    except Exception as exc:  # noqa: BLE001 - a provider response is never automatic action admission
        report["corporate_actions"] = {"downloaded": False, "reviewed": False, "error_type": type(exc).__name__}
    write_json(output, report)
    return report
