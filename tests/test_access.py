"""Access probes are read-only and never certify a complete historical dataset."""
import json
from types import SimpleNamespace

import pandas as pd
import pytest

from bot_trader.access import preflight


@pytest.fixture
def access_fakes(monkeypatch):
    calls = []
    settings = {"equity": 5000, "missing_minutes": True, "deny_sip": False,
                "mutate_sample": None, "mutation": None}

    class Broker:
        def account(self):
            calls.append("account")
            return {"equity": settings["equity"], "cash": settings["equity"], "blocked": False,
                    "short_market_value": 0}

        def positions(self):
            calls.append("positions")
            return {}

        def open_orders(self):
            calls.append("open_orders")
            return []

    class StockClient:
        def __init__(self, *args):
            self._session = SimpleNamespace(request=lambda: None)

        def get_stock_bars(self, request):
            calls.append("bars")
            if request.feed.value == "sip" and settings["deny_sip"]:
                raise RuntimeError("simulated provider denial")
            minute = request.timeframe.amount == 1 and request.timeframe.unit.value == "Min"
            stamps = pd.date_range(request.start, periods=390 if minute else 5,
                                   freq="min" if minute else "D", tz="UTC")
            if minute and settings["missing_minutes"]:
                stamps = stamps.delete(5)
            if settings["mutate_sample"] == ("minutes" if minute else "daily"):
                if settings["mutation"] == "duplicate":
                    stamps = stamps.delete(5 if minute else 2).append(stamps[:1])
                elif settings["mutation"] == "shift":
                    stamps = stamps + pd.Timedelta(minutes=1) if minute else stamps + pd.Timedelta(days=1)
                elif settings["mutation"] == "reverse":
                    stamps = stamps[::-1]
                elif settings["mutation"] == "timezone":
                    stamps = stamps.tz_convert("America/New_York")
            bars = [SimpleNamespace(timestamp=stamp) for stamp in stamps]
            symbols = request.symbol_or_symbols
            if settings["mutation"] == "missing_symbol":
                symbols = [s for s in symbols if s != "BIL"]
            return SimpleNamespace(data={s: bars for s in symbols})

    class ActionClient:
        def __init__(self, *args):
            self._session = SimpleNamespace(request=lambda: None)

        def get_corporate_actions(self, request):
            calls.append("actions")
            return SimpleNamespace(model_dump=lambda **kw: {"data": {"cash_dividends": []}})

    monkeypatch.setattr("bot_trader.access.credentials", lambda: ("synthetic-key", "synthetic-secret"))
    monkeypatch.setattr("bot_trader.broker.AlpacaBroker", Broker)
    monkeypatch.setattr("alpaca.data.historical.StockHistoricalDataClient", StockClient)
    monkeypatch.setattr("alpaca.data.historical.corporate_actions.CorporateActionsClient", ActionClient)
    return settings, calls


def test_missing_execution_minute_is_recorded_and_no_mutations_exist(tmp_path, access_fakes):
    _, calls = access_fakes
    path = tmp_path / "preflight.json"
    report = preflight(path)
    assert report["paper_authenticated"]
    assert report["initial_account_eligible"]
    minutes = [c for c in report["checks"] if c["sample"] == "first_evaluation_minutes"]
    assert all(not c["coverage_passed"] for c in minutes)
    assert all(not any(c["exact_0935_present"].values()) for c in minutes)
    assert not report["complete_data_requirement_satisfied"]
    assert set(calls) == {"account", "positions", "open_orders", "bars", "actions"}
    assert json.loads(path.read_text()) == report
    assert path.stat().st_mode & 0o777 == 0o600
    assert "synthetic-secret" not in path.read_text()


def test_successful_samples_do_not_certify_full_history(tmp_path, access_fakes):
    settings, _ = access_fakes
    settings.update(missing_minutes=False, equity=100000)
    report = preflight(tmp_path / "preflight.json")
    assert all(c["coverage_passed"] for c in report["checks"])
    assert all(all(c["exact_0935_present"].values()) for c in report["checks"]
               if c["sample"] == "first_evaluation_minutes")
    assert not report["initial_account_eligible"]
    assert not report["complete_data_requirement_satisfied"]
    assert not report["corporate_actions"]["reviewed"]


def test_feed_denial_is_separate_from_credentials_and_other_feed(tmp_path, access_fakes):
    settings, _ = access_fakes
    settings.update(deny_sip=True, missing_minutes=False)
    report = preflight(tmp_path / "preflight.json")
    assert report["paper_authenticated"]
    assert all(c["coverage_passed"] for c in report["checks"] if c["feed"] == "iex")
    assert all(c["error_type"] == "RuntimeError" for c in report["checks"] if c["feed"] == "sip")


@pytest.mark.parametrize("sample", ["daily", "minutes"])
@pytest.mark.parametrize("mutation", ["duplicate", "shift"])
def test_correct_bar_count_cannot_hide_timestamp_gaps(tmp_path, access_fakes, sample, mutation):
    settings, _ = access_fakes
    settings.update(missing_minutes=False, mutate_sample=sample, mutation=mutation)
    report = preflight(tmp_path / "preflight.json")
    affected = "daily_warmup" if sample == "daily" else "first_evaluation_minutes"
    for check in report["checks"]:
        if check["sample"] != affected:
            assert check["coverage_passed"]
            continue
        assert all(n == check["expected_per_symbol"] for n in check["counts"].values())
        assert not check["coverage_passed"]
        for details in check["coverage"].values():
            assert details["missing_count"] == 1
            assert details["duplicate_count"] == (1 if mutation == "duplicate" else 0)
            assert details["unexpected_count"] == (1 if mutation == "shift" else 0)
            assert len(details["missing_examples"]) == 1
        if sample == "minutes" and mutation == "duplicate":
            assert not any(check["exact_0935_present"].values())


@pytest.mark.parametrize("sample", ["daily", "minutes"])
@pytest.mark.parametrize("mutation", ["reverse", "timezone"])
def test_coverage_accepts_reordering_and_equivalent_timezones(tmp_path, access_fakes, sample, mutation):
    settings, _ = access_fakes
    settings.update(missing_minutes=False, mutate_sample=sample, mutation=mutation)
    report = preflight(tmp_path / "preflight.json")
    assert all(check["coverage_passed"] for check in report["checks"])
    assert not report["complete_data_requirement_satisfied"]


def test_missing_symbol_reports_full_gap_with_bounded_examples(tmp_path, access_fakes):
    settings, _ = access_fakes
    settings.update(missing_minutes=False, mutation="missing_symbol")
    report = preflight(tmp_path / "preflight.json")
    for check in report["checks"]:
        assert not check["coverage_passed"]
        assert check["counts"]["BIL"] == 0
        details = check["coverage"]["BIL"]
        assert details["missing_count"] == check["expected_per_symbol"]
        assert len(details["missing_examples"]) == 5
        assert details["duplicate_count"] == details["unexpected_count"] == 0
        assert check["coverage"]["SPY"]["missing_count"] == 0
