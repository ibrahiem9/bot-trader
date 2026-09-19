"""Synthetic two-session fixtures exercise admission rules, never performance."""

import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from bot_trader import data


@pytest.fixture
def dataset(tmp_path, monkeypatch):
    # Deliberately replace the historical boundary only for these tiny tests.
    start, end = "2024-11-27", "2024-11-29"
    monkeypatch.setattr(data, "START", start)
    monkeypatch.setattr(data, "END", end)
    cal = data.calendar(start, end)
    sessions = cal.sessions_in_range(start, end)
    daily, minutes = [], []
    for session in sessions:
        stamps = pd.date_range(cal.session_open(session), cal.session_close(session),
                               freq="min", inclusive="left")
        for symbol in data.SYMBOLS:
            daily.append({"session": session, "symbol": symbol, "open": 100., "high": 100.,
                              "low": 100., "close": 100., "volume": 100.})
            minutes.append(pd.DataFrame({"timestamp": stamps, "symbol": symbol, "open": 100.,
                                             "high": 100., "low": 100., "close": 100., "volume": 100.}))
    manifest = {"start": start, "end": end, "feed": "iex", "adjustments": ["raw", "split", "all"],
                    "minute_adjustment": "raw", "files": []}
    for kind in ("raw", "split", "all", "minutes"):
        frame = pd.concat(minutes, ignore_index=True) if kind == "minutes" else pd.DataFrame(daily)
        path = tmp_path / f"synthetic-{kind}.parquet"
        frame.to_parquet(path, index=False)
        manifest["files"].append({"path": path.name, "sha256": data.digest(path)})
    data.write_json(tmp_path / "manifest.json", manifest)
    pd.DataFrame(columns=data.ACTION_COLUMNS).to_csv(tmp_path / "actions.csv", index=False)
    approve_fixture(tmp_path)
    return tmp_path


def approve_fixture(root):
    manifest = json.loads((root / "manifest.json").read_text())
    data.write_json(root / "actions-review.json", {
        "approved": True, "reviewer": "synthetic test fixture",
        "evidence": ["Invented constant prices and no actions; not historical evidence"],
        "manifest_sha256": data.digest(root / "manifest.json"),
        "actions_sha256": data.digest(root / "actions.csv"),
        "coverage_start": manifest["start"], "coverage_end": manifest["end"], "symbols": list(data.SYMBOLS),
    })


def replace_partition(root, kind, transform):
    manifest = json.loads((root / "manifest.json").read_text())
    item = next(item for item in manifest["files"] if item["path"].endswith(f"-{kind}.parquet"))
    path = root / item["path"]
    transform(pd.read_parquet(path)).to_parquet(path, index=False)
    item["sha256"] = data.digest(path)
    data.write_json(root / "manifest.json", manifest)
    approve_fixture(root)


def test_fixture_respects_holiday_and_early_close(dataset):
    result = data.audit(dataset)
    assert result["passed"], result["errors"]
    assert result["daily_rows"] == 2 * len(data.SYMBOLS)
    assert result["minute_rows"] == (390 + 210) * len(data.SYMBOLS)
    assert result["missing_minutes"] == 0
    assert json.loads((dataset / "audit.json").read_text()) == result


def test_missing_credentials_stops_before_creating_dataset(tmp_path, monkeypatch):
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    with pytest.raises(data.DataError, match="locally"):
        data.ingest(tmp_path / "absent")
    assert not (tmp_path / "absent").exists()


def test_external_path_resolves_symlinks_and_rejects_repository(tmp_path):
    repo = Path(data.__file__).resolve().parents[2]
    with pytest.raises(data.DataError, match="outside"):
        data.external_path(repo / "private")
    shortcut = tmp_path / "repo-link"
    shortcut.symlink_to(repo, target_is_directory=True)
    with pytest.raises(data.DataError, match="outside"):
        data.external_path(shortcut / "private")
    assert data.external_path(tmp_path / "private") == tmp_path / "private"


def test_daily_bar_dates_use_eastern_session_and_preserve_utc_minutes():
    source = SimpleNamespace(df=pd.DataFrame({
        "symbol": ["SPY", "SPY"],
        "timestamp": ["2024-03-08T05:00:00Z", "2024-03-11T04:00:00Z"],
        "close": [100., 101.],
    }).set_index(["symbol", "timestamp"]))
    normalized = data._bars_frame(source, daily=True)
    assert list(normalized.session) == [pd.Timestamp("2024-03-08"), pd.Timestamp("2024-03-11")]
    assert str(normalized.timestamp.dt.tz) == "UTC"
    assert "session" not in data._bars_frame(source, daily=False)


@pytest.mark.parametrize("mutation,expected", [
    (lambda df: df.iloc[1:], "Invalid daily prices"),
    (lambda df: df.assign(low=101.), "OHLC"),
    (lambda df: df.assign(volume=-1.), "volume"),
])
def test_daily_admission_rejects_incomplete_or_impossible_bars(dataset, mutation, expected):
    replace_partition(dataset, "raw", mutation)
    result = data.audit(dataset)
    assert not result["passed"]
    assert expected in " ".join(result["errors"])


@pytest.mark.parametrize("kind", ["split", "all"])
def test_adjustment_changes_require_matching_action(dataset, kind):
    def unexplained(frame):
        frame.loc[frame.session == frame.session.max(), "close"] *= .5
        return frame
    replace_partition(dataset, kind, unexplained)
    result = data.audit(dataset)
    assert not result["passed"]
    assert any("adjust" in error for error in result["errors"])


def test_missing_exact_execution_minute_is_never_filled(dataset):
    def missing_execution(frame):
        return frame[~((frame.symbol == "SPY") & (frame.timestamp == pd.Timestamp("2024-11-27T14:35:00Z")))]
    replace_partition(dataset, "minutes", missing_execution)
    result = data.audit(dataset)
    assert not result["passed"]
    assert result["missing_minutes"] == 1
    assert any("SPY: 1 missing" in error for error in result["errors"])


@pytest.mark.parametrize("key,value", [
    ("approved", False), ("evidence", []), ("reviewer", ""),
    ("manifest_sha256", "stale"), ("actions_sha256", "stale"),
    ("coverage_start", "2024-11-28"), ("symbols", ["SPY"]),
])
def test_action_review_is_bound_to_evidence_coverage_and_inputs(dataset, key, value):
    path = dataset / "actions-review.json"
    review = json.loads(path.read_text())
    review[key] = value
    data.write_json(path, review)
    result = data.audit(dataset)
    assert not result["passed"]
    assert any("evidence-backed" in error for error in result["errors"])


def test_changed_partition_is_rejected_before_using_it(dataset):
    path = dataset / "synthetic-raw.parquet"
    frame = pd.read_parquet(path)
    frame.loc[0, "close"] = 99.
    frame.to_parquet(path, index=False)
    result = data.audit(dataset)
    assert not result["passed"]
    assert "changed since ingestion" in result["errors"][0]


def test_tiny_dataset_cannot_pass_frozen_historical_range(dataset, monkeypatch):
    monkeypatch.setattr(data, "START", "2016-01-01")
    monkeypatch.setattr(data, "END", "2026-09-16")
    result = data.audit(dataset)
    assert not result["passed"]
    assert any("frozen" in error for error in result["errors"])


def test_sdk_stock_bar_pagination_consumes_short_pages_without_network(monkeypatch):
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    client = StockHistoricalDataClient("fixture-key", "fixture-secret", raw_data=True)
    requests = []
    responses = iter([
        {"bars": {"SPY": [{"t": "2024-11-27T14:35:00Z"}]}, "next_page_token": "page-two"},
        {"bars": {"BIL": [{"t": "2024-11-27T14:35:00Z"}]}, "next_page_token": None},
    ])

    def get(*, path, data):
        requests.append(dict(data))
        return next(responses)

    monkeypatch.setattr(client, "get", get)
    result = client.get_stock_bars(StockBarsRequest(symbol_or_symbols=["SPY", "BIL"],
                                                   timeframe=TimeFrame.Minute, feed="iex"))
    assert set(result) == {"SPY", "BIL"}
    assert [request["page_token"] for request in requests] == [None, "page-two"]
    assert all(request["limit"] == 10000 for request in requests)


def test_ingest_pins_feed_adjustments_and_filters_early_close_then_resumes(tmp_path, monkeypatch):
    from alpaca.data import historical
    from alpaca.data.historical import corporate_actions
    from alpaca.data.timeframe import TimeFrame

    start, end = "2024-11-27", "2024-11-29"
    cal = data.calendar(start, end)
    sessions = cal.sessions_in_range(start, end)
    requests = []
    monkeypatch.setattr(data, "credentials", lambda: ("fixture-key", "fixture-secret"))
    monkeypatch.setattr(data, "SYMBOLS", ("SPY",))

    class FakeBars:
        def __init__(self, *args):
            pass

        def get_stock_bars(self, request):
            requests.append(request)
            stamps = []
            for session in sessions:
                if str(request.timeframe) == str(TimeFrame.Minute):
                    # Include one bar on each side of the regular session.
                    stamps.extend(pd.date_range(cal.session_open(session) - pd.Timedelta(minutes=1),
                                                cal.session_close(session), freq="min"))
                else:
                    stamps.append(session.tz_localize("America/New_York"))
            frame = pd.DataFrame({"symbol": "SPY", "timestamp": stamps, "open": 100., "high": 100.,
                                      "low": 100., "close": 100., "volume": 100.})
            return SimpleNamespace(df=frame.set_index(["symbol", "timestamp"]))

    class FakeActions:
        def __init__(self, *args):
            pass

        def get_corporate_actions(self, request):
            return SimpleNamespace(model_dump=lambda **kwargs: {})

    monkeypatch.setattr(historical, "StockHistoricalDataClient", FakeBars)
    monkeypatch.setattr(corporate_actions, "CorporateActionsClient", FakeActions)
    root = tmp_path / "downloads"
    result = data.ingest(root, feed="iex", start=start, end=end)
    assert len(requests) == 4
    assert [request.adjustment.value for request in requests] == ["raw", "split", "all", "raw"]
    assert all(request.feed.value == "iex" and request.asof == end for request in requests)
    assert all(request.limit is None for request in requests)
    assert result["provider_actions"] == "downloaded_unreviewed"
    minutes = pd.read_parquet(root / "2024/SPY-minutes.parquet")
    assert len(minutes) == 600
    assert minutes.timestamp.max() == pd.Timestamp("2024-11-29T17:59:00Z")
    daily = pd.read_parquet(root / "2024/SPY-raw.parquet")
    assert list(daily.session) == list(sessions)
    data.ingest(root, feed="iex", start=start, end=end)
    assert len(requests) == 4
    with pytest.raises(data.DataError, match="separate dataset"):
        data.ingest(root, feed="sip", start=start, end=end)


def test_invalid_json_review_fails_closed_with_persisted_audit(dataset):
    (dataset / "actions-review.json").write_text("{")
    result = data.audit(dataset)
    assert not result["passed"]
    assert result["errors"]
    assert json.loads((dataset / "audit.json").read_text())["passed"] is False


def test_nonpositive_adjusted_prices_cannot_be_admitted(dataset):
    replace_partition(dataset, "split", lambda df: df.assign(close=-100.))
    replace_partition(dataset, "all", lambda df: df.assign(close=-100.))
    result = data.audit(dataset)
    assert not result["passed"]


def test_duplicate_actions_are_reported_without_crashing(dataset):
    action = {"session": "2024-11-29", "symbol": "SPY", "split_ratio": 1., "dividend": 1., "pay_session": "2024-12-02"}
    pd.DataFrame([action, action]).to_csv(dataset / "actions.csv", index=False)
    approve_fixture(dataset)
    result = data.audit(dataset)
    assert not result["passed"]
    assert any("normalized" in error for error in result["errors"])
