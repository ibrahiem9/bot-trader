"""Explicit-feed downloads and fail-closed, reproducible historical data audits."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import exchange_calendars as xcals
import numpy as np
import pandas as pd

SYMBOLS = ("SPY", "IWM", "EFA", "EEM", "IEF", "GLD", "BIL")
START = "2016-01-01"
END = "2026-09-16"
ACTION_COLUMNS = ["session", "symbol", "split_ratio", "dividend", "pay_session"]


class DataError(ValueError):
    """A data requirement failed; performance conclusions are prohibited."""


def external_path(path: str | Path) -> Path:
    result = Path(path).expanduser().resolve()
    repo = Path(__file__).resolve().parents[2]
    if result == repo or repo in result.parents:
        raise DataError("Private data, state and outputs must be stored outside the repository")
    return result


def default_home() -> Path:
    return Path(os.environ.get("BOT_TRADER_HOME", "~/.local/share/bot-trader")).expanduser()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    tmp.chmod(0o600)
    tmp.replace(path)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def calendar(start: str = START, end: str = END):
    return xcals.get_calendar("XNYS", start=start, end=end)


def credentials() -> tuple[str, str]:
    """Load a complete environment pair or an inert, private local file."""
    key, secret = os.getenv("APCA_API_KEY_ID"), os.getenv("APCA_API_SECRET_KEY")
    if key is not None or secret is not None:
        if not key or not secret or not key.strip() or not secret.strip():
            raise DataError("Set both APCA_API_KEY_ID and APCA_API_SECRET_KEY to nonempty values")
        return key, secret

    path = external_path(os.environ.get("BOT_TRADER_CREDENTIALS_FILE", default_home() / "credentials.env"))
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(descriptor, encoding="utf-8") as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o077:
                raise DataError("Credentials must be a regular file with no group or world permissions (chmod 600)")
            contents = handle.read()
    except (OSError, UnicodeError):
        raise DataError(
            "Set APCA_API_KEY_ID and APCA_API_SECRET_KEY locally or provide a readable private credentials.env file"
        ) from None

    values: dict[str, str] = {}
    for line in contents.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or name in values:
            raise DataError("Malformed credentials file: expected unique KEY=value entries")
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise DataError("Malformed credentials file: unmatched quotes")
            value = value[1:-1]
        # Values stay literal: no shell execution, variable expansion or escape decoding.
        values[name] = value
    key, secret = values.get("APCA_API_KEY_ID"), values.get("APCA_API_SECRET_KEY")
    if not key or not secret or not key.strip() or not secret.strip():
        raise DataError("Credentials file must contain nonempty APCA_API_KEY_ID and APCA_API_SECRET_KEY")
    return key, secret


def _bars_frame(result, daily: bool) -> pd.DataFrame:
    frame = result.df.reset_index()
    if frame.empty:
        raise DataError("The provider returned no bars")
    frame["timestamp"] = pd.to_datetime(frame.timestamp, utc=True)
    if daily:
        frame["session"] = frame.timestamp.dt.tz_convert("America/New_York").dt.tz_localize(None).dt.normalize()
    return frame


def ingest(root: Path, feed: str = "iex", start: str = START, end: str = END) -> dict:
    """Download pinned raw/split/all daily bars and raw regular-session minutes.

    SDK pagination is automatic. Year partitions make interrupted downloads
    resumable. A manifest is committed only after all requested partitions exist.
    Corporate action API output is a review aid, never proof of complete coverage.
    """
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.historical.corporate_actions import CorporateActionsClient
    from alpaca.data.requests import CorporateActionsRequest, StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    if feed not in {"iex", "sip"}:
        raise DataError("Feed must explicitly be iex or sip")
    root = external_path(root)
    key, secret = credentials()
    cal = calendar(start, end)
    sessions = cal.sessions_in_range(start, end)
    if len(sessions) == 0:
        raise DataError("No exchange sessions in the requested range")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    request = {"feed": feed, "start": start, "end": end, "symbols": list(SYMBOLS), "schema": 1}
    request_path = root / "request.json"
    if request_path.exists() and json.loads(request_path.read_text()) != request:
        raise DataError("Use a separate dataset directory for a different feed/date range")
    write_json(request_path, request)
    client = StockHistoricalDataClient(key, secret)
    files = []
    for year in sorted(set(sessions.year)):
        year_sessions = sessions[sessions.year == year]
        first, last = year_sessions[0], year_sessions[-1]
        begin = first.tz_localize("America/New_York").to_pydatetime()
        finish = (last + pd.Timedelta(days=1)).tz_localize("America/New_York").to_pydatetime()
        for symbol in SYMBOLS:
            for kind in ("raw", "split", "all", "minutes"):
                name = f"{year}/{symbol}-{kind}.parquet"
                path = root / name
                if not path.exists():
                    bars = client.get_stock_bars(StockBarsRequest(
                        symbol_or_symbols=symbol, start=begin, end=finish,
                        timeframe=TimeFrame.Minute if kind == "minutes" else TimeFrame.Day,
                        adjustment=Adjustment.RAW if kind == "minutes" else Adjustment(kind),
                        feed=DataFeed(feed), asof=end,
                    ))
                    frame = _bars_frame(bars, daily=kind != "minutes")
                    if kind == "minutes":
                        # Exclude extended hours using the exchange's actual early closes.
                        expected = pd.DatetimeIndex(np.concatenate([
                            pd.date_range(cal.session_open(s), cal.session_close(s), freq="min",
                                          inclusive="left").asi8 for s in year_sessions
                        ]), tz="UTC")
                        frame = frame[frame.timestamp.isin(expected)]
                    else:
                        frame = frame[frame.session.isin(year_sessions)]
                    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    temporary = path.with_suffix(".tmp")
                    frame.to_parquet(temporary, index=False)
                    temporary.chmod(0o600)
                    temporary.replace(path)
                files.append({"path": name, "sha256": digest(path)})
    action_status = "downloaded_unreviewed"
    try:
        actions_client = CorporateActionsClient(key, secret)
        result = actions_client.get_corporate_actions(CorporateActionsRequest(
            symbols=list(SYMBOLS), start=pd.Timestamp(start).date(), end=pd.Timestamp(end).date()
        ))
        write_json(root / "provider-actions.json", result.model_dump(mode="json"))
    except Exception as exc:  # noqa: BLE001 - action-provider failure is a recorded admission barrier
        # Bars remain useful for a data audit; missing actions prohibit conclusions.
        action_status = f"unavailable: {type(exc).__name__}"
    manifest = {**request, "retrieved_at": datetime.now(UTC).isoformat(),
                "files": files, "provider_actions": action_status,
                "packages": {name: version(name) for name in ("alpaca-py", "pandas", "numpy", "exchange-calendars", "pyarrow")},
                "adjustments": ["raw", "split", "all"], "minute_adjustment": "raw"}
    write_json(root / "manifest.json", manifest)
    return manifest


def load(root: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DatetimeIndex, dict]:
    root = external_path(root)
    manifest = json.loads((root / "manifest.json").read_text())
    collections: dict[str, list] = {kind: [] for kind in ("raw", "split", "all", "minutes")}
    for item in manifest["files"]:
        path = (root / item["path"]).resolve()
        if root not in path.parents or digest(path) != item["sha256"]:
            raise DataError("Dataset file is outside its directory or changed since ingestion")
        kind = path.stem.rsplit("-", 1)[-1]
        if kind not in collections:
            raise DataError("Unexpected dataset partition")
        collections[kind].append(pd.read_parquet(path))
    if any(not parts for parts in collections.values()):
        raise DataError("Missing raw, split, all-adjusted or minute bars")
    frames = {kind: pd.concat(parts, ignore_index=True) for kind, parts in collections.items()}
    keys = ["session", "symbol"]
    daily = frames["raw"].merge(
        frames["split"][keys + ["close"]].rename(columns={"close": "signal_close"}),
        on=keys, how="outer", validate="one_to_one"
    ).merge(frames["all"][keys + ["close"]].rename(columns={"close": "all_close"}),
            on=keys, how="outer", validate="one_to_one")
    action_path = root / "actions.csv"
    actions = pd.read_csv(action_path) if action_path.exists() else pd.DataFrame(columns=ACTION_COLUMNS)
    if set(ACTION_COLUMNS) - set(actions.columns):
        raise DataError("actions.csv must contain " + ", ".join(ACTION_COLUMNS))
    for col in ("session", "pay_session"):
        actions[col] = pd.to_datetime(actions[col])
    sessions = calendar(manifest["start"], manifest["end"]).sessions_in_range(
        manifest["start"], manifest["end"])
    return daily, frames["minutes"], actions, sessions, manifest


def audit(root: Path) -> dict:
    root = external_path(root)
    try:
        return _audit(root)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        result = {"passed": False, "errors": [f"Malformed or incomplete dataset/review: {exc}"], "warnings": []}
        write_json(root / "audit.json", result)
        return result


def _audit(root: Path) -> dict:
    root = external_path(root)
    errors: list[str] = []
    warnings: list[str] = []
    try:
        daily, minutes, actions, sessions, manifest = load(root)
        manifest_hash = digest(root / "manifest.json")
    except (ValueError, OSError, KeyError) as exc:
        result = {"passed": False, "errors": [f"Dataset cannot be loaded: {exc}"], "warnings": []}
        write_json(root / "audit.json", result)
        return result
    if manifest.get("feed") not in {"iex", "sip"}:
        errors.append("Feed is not explicitly iex or sip")
    if manifest.get("minute_adjustment") != "raw" or manifest.get("adjustments") != ["raw", "split", "all"]:
        errors.append("Unexpected adjustment policy")
    expected = pd.MultiIndex.from_product([sessions, SYMBOLS], names=["session", "symbol"])
    actual = pd.MultiIndex.from_frame(daily[["session", "symbol"]])
    if len(expected.difference(actual)) or len(actual.difference(expected)) or actual.has_duplicates:
        errors.append(f"Daily session/symbol coverage mismatch: {len(expected.difference(actual))} missing")
    for label, frame in (("daily", daily), ("minutes", minutes)):
        prices = frame[["open", "high", "low", "close"]].to_numpy(dtype=float)
        if not np.isfinite(prices).all() or (prices <= 0).any():
            errors.append(f"Invalid {label} prices")
        if ((frame.high < frame[["open", "close", "low"]].max(axis=1)) |
                (frame.low > frame[["open", "close", "high"]].min(axis=1))).any():
            errors.append(f"Inconsistent {label} OHLC")
        if not np.isfinite(frame.volume).all() or (frame.volume < 0).any():
            errors.append(f"Invalid {label} volume")
    if (not np.isfinite(daily[["signal_close", "all_close"]]).all().all() or
            (daily[["signal_close", "all_close"]] <= 0).any().any()):
        errors.append("Missing, nonpositive or nonfinite adjustment prices")
    cal = calendar(manifest["start"], manifest["end"])
    missing_minutes = 0
    # Work one symbol at a time to bound memory. Never forward-fill a missing bar.
    expected_minutes = pd.DatetimeIndex(np.concatenate([
        pd.date_range(cal.session_open(s), cal.session_close(s), freq="min", inclusive="left").asi8
        for s in sessions
    ]), tz="UTC")
    for symbol in SYMBOLS:
        stamps = pd.DatetimeIndex(pd.to_datetime(minutes.loc[minutes.symbol == symbol, "timestamp"], utc=True))
        missing = len(expected_minutes.difference(stamps))
        missing_minutes += missing
        if missing or stamps.has_duplicates or len(stamps.difference(expected_minutes)):
            errors.append(f"{symbol}: {missing} missing regular-session minutes or duplicate/unexpected bars")
    if set(minutes.symbol) != set(SYMBOLS):
        errors.append("Unexpected minute-bar symbol universe")
    if actions.duplicated(["session", "symbol"]).any():
        errors.append("Corporate actions must be normalized to one row per ex-date/symbol")
    if not set(actions.symbol).issubset(SYMBOLS):
        errors.append("Unknown corporate-action symbols")
    for col, positive in (("split_ratio", True), ("dividend", False)):
        values = pd.to_numeric(actions[col], errors="coerce")
        if not np.isfinite(values).all() or ((values <= 0) if positive else (values < 0)).any():
            errors.append(f"Invalid corporate action {col}")
    if (actions.session.isna().any() or not actions.session.isin(sessions).all() or
            ((actions.dividend > 0) & (actions.pay_session.isna() | (actions.pay_session < actions.session))).any()):
        errors.append("Invalid corporate-action ex-date/payment dates")
    # Split-adjustment factor jumps must match the ledger; multiplicative provider
    # rounding is allowed at 5bp. Dividend-factor changes need a recorded cash event.
    for symbol in SYMBOLS:
        rows = daily[daily.symbol == symbol].sort_values("session").set_index("session")
        if rows.empty or rows.index.has_duplicates:
            continue
        ledger = actions[actions.symbol == symbol].set_index("session")
        if ledger.index.has_duplicates:
            continue  # Already failed above; do not crash while producing the audit.
        split = ledger.split_ratio.astype(float).reindex(rows.index).fillna(1)
        factor = rows.signal_close / rows.close
        ratio = factor / factor.shift(1)
        if not np.isclose(ratio.iloc[1:], split.iloc[1:], rtol=0.0005, atol=0.00001).all():
            errors.append(f"{symbol}: split-adjusted prices disagree with action ledger")
        dividend_factor = rows.all_close / rows.signal_close
        adjusted_events = dividend_factor.pct_change().abs() > 0.00005
        recorded = ledger.dividend.astype(float).reindex(rows.index).fillna(0) > 0
        if (adjusted_events & ~recorded).any():
            errors.append(f"{symbol}: unexplained dividend/other adjustment changes")
    review_path = root / "actions-review.json"
    review = json.loads(review_path.read_text()) if review_path.exists() else {}
    action_path = root / "actions.csv"
    action_hash = digest(action_path) if action_path.exists() else None
    if (review.get("approved") is not True or not review.get("reviewer") or not review.get("evidence") or
            review.get("manifest_sha256") != manifest_hash or review.get("actions_sha256") != action_hash or
            review.get("coverage_start") != manifest["start"] or review.get("coverage_end") != manifest["end"] or
            sorted(review.get("symbols", [])) != sorted(SYMBOLS) or not action_hash):
        errors.append("Complete split/dividend coverage needs an evidence-backed actions-review.json tied to file hashes")
    if manifest["start"] != START or manifest["end"] != END:
        errors.append("Dataset does not cover the frozen 2016-01-01 through 2026-09-16 protocol")
    if manifest.get("feed") == "iex":
        warnings.append("IEX is one venue, not the consolidated market; missing bars cannot be fabricated")
    result = {"passed": not errors, "errors": errors, "warnings": warnings,
              "manifest_sha256": manifest_hash, "actions_sha256": action_hash,
              "review_sha256": digest(review_path) if review_path.exists() else None,
              "daily_rows": len(daily), "minute_rows": len(minutes), "missing_minutes": missing_minutes,
              "checked_at": datetime.now(UTC).isoformat()}
    write_json(root / "audit.json", result)
    return result


def paper_data(feed: str = "iex") -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    """Fresh split-adjusted completed history; called at signal time by the runner."""
    from functools import partial

    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    now = pd.Timestamp.now(tz="America/New_York")
    start = (now.normalize() - pd.Timedelta(days=450)).date().isoformat()
    end = (now.normalize() + pd.Timedelta(days=10)).date().isoformat()
    cal = calendar(start, end)
    sessions = cal.sessions_in_range(start, end)
    client = StockHistoricalDataClient(*credentials())
    client._session.request = partial(client._session.request, timeout=(3, 10))
    client._retry = 0
    data = client.get_stock_bars(StockBarsRequest(
        symbol_or_symbols=list(SYMBOLS[:-1]), timeframe=TimeFrame.Day,
        start=pd.Timestamp(start, tz="America/New_York").to_pydatetime(),
        end=now.normalize().to_pydatetime(), adjustment=Adjustment.SPLIT, feed=DataFeed(feed),
    ))
    rows = _bars_frame(data, daily=True)
    rows = rows[rows.session < now.tz_localize(None).normalize()]
    return rows.pivot(index="session", columns="symbol", values="close").sort_index(), sessions
