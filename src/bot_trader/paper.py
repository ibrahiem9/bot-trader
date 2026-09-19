"""Fail-closed paper operation. A broker timeout is ambiguous, never a retry signal."""
from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .config import DRAWDOWN_LIMIT, INITIAL_EQUITY, UNIVERSE
from .strategy import decide, entry_notional

TERMINAL = {"filled", "canceled", "expired", "rejected", "replaced"}
EASTERN = ZoneInfo("America/New_York")


class PaperRunner:
    def __init__(self, state, broker, notifications, clock=None):
        self.state, self.broker, self.notifications = state, broker, notifications
        self.clock = clock or (lambda: datetime.now(UTC))

    def status(self):
        return {key: self.state.get(key) for key in (
            "active_strategy", "active_data_feed", "account_id", "peak", "halt", "suspension", "last_tick",
            "last_signal_session", "qualification_sha256", "notification_error")}

    def activate(self, qualification_path, strategy):
        """Explicit local action, only after independent research qualification."""
        from .reporting import verify_qualification

        with self.state.lock():
            if self.state.get("halt"):
                raise RuntimeError("Persistent halt requires manual review; activation cannot clear it")
            if self.state.get("account_id"):
                raise RuntimeError("Account already initialized; automatic reactivation is prohibited")
            raw = Path(qualification_path).read_bytes()
            q = verify_qualification(Path(qualification_path))
            if (strategy not in {"trend", "rebound"}
                    or q.get("data_audit_passed") is not True
                    or q.get("final_test_end") != "2026-09-16"
                    or q.get("selected_strategy") != strategy
                    or not q.get("protocol_hash") or not q.get("dataset_hash")
                    or q.get("data_feed") not in {"iex", "sip"}
                    or strategy not in q.get("eligible_strategies", [])):
                raise ValueError("Research qualification does not authorize this candidate")
            account = self.broker.account()
            self._valid_account(account)
            if not 0 < account["equity"] <= INITIAL_EQUITY or self.broker.positions() or self.broker.open_orders():
                raise ValueError("Use a dedicated, empty paper account funded with at most $5,000")
            self.notifications.heartbeat()
            self.notifications.send("Paper activation", f"Candidate {strategy}; initial equity {account['equity']:.2f}")
            self.state.set("account_id", account["id"])
            self.state.set("peak", account["equity"])
            self.state.set("qualification_sha256", hashlib.sha256(raw).hexdigest())
            self.state.set("active_data_feed", q["data_feed"])
            self.state.set("active_protocol_hash", q["protocol_hash"])
            self.state.set("active_strategy", strategy)
            self.state.event("activation", f"Explicitly activated {strategy}")

    @staticmethod
    def _valid_account(account):
        if (not math.isfinite(account["equity"]) or not math.isfinite(account["cash"])
                or account["equity"] <= 0 or account["cash"] < 0
                or account.get("blocked") or account.get("short_market_value", 0) != 0):
            raise RuntimeError("Account blocked, invalid, borrowed, or short; entries prohibited")

    def _event_once(self, key, message):
        if self.state.get(key) != message:
            self.state.event(key, message)
            self.state.set(key, message)

    def _delivery(self):
        errors = []
        try:
            self.notifications.heartbeat()
            self.state.set("heartbeat_error", None)
        except Exception as exc:  # noqa: BLE001 - independent transport failures must fail closed
            message = f"Heartbeat failed: {type(exc).__name__}"
            errors.append(message)
            self._event_once("heartbeat_error", message)
        try:
            rows = self.state.db.execute("SELECT * FROM events WHERE delivered=0 ORDER BY id LIMIT 1").fetchall()
            for row in rows:
                self.notifications.send(f"Paper bot: {row['kind']}", row["message"])
                with self.state.db:
                    self.state.db.execute("UPDATE events SET delivered=1 WHERE id=?", (row["id"],))
            self.state.set("email_error", None)
        except Exception as exc:  # noqa: BLE001 - external delivery failures must fail closed
            message = f"Email delivery failed: {type(exc).__name__}"
            errors.append(message)
            self._event_once("email_error", message)
        self.state.set("notification_error", "; ".join(errors) if errors else None)
        # Successful SNS delivery cannot override a failed heartbeat.
        pending = self.state.db.execute("SELECT COUNT(*) FROM events WHERE delivered=0").fetchone()[0]
        return not errors and pending == 0

    def _reconcile(self):
        """Refresh all fills before comparing broker positions with the local ledger."""
        problem = None
        expected = {}
        for intent in self.state.intentions():
            if intent["status"] not in TERMINAL and intent["submission_started"]:
                order = self.broker.lookup(intent["client_id"])
                if order is None:
                    problem = "Submission outcome unknown; inspect client ID at broker before manual recovery"
                else:
                    if order["symbol"] != intent["symbol"] or order["side"] != intent["side"]:
                        raise RuntimeError("Broker client ID does not match persisted intention")
                    if not math.isfinite(order["filled_qty"]) or order["filled_qty"] < 0:
                        raise RuntimeError("Broker returned invalid filled quantity")
                    if order["filled_qty"] < intent["filled_qty"]:
                        raise RuntimeError("Broker filled quantity moved backward")
                    intent.update(status=order["status"], filled_qty=order["filled_qty"])
                    with self.state.db:
                        self.state.db.execute("UPDATE intentions SET status=?,filled_qty=? WHERE client_id=?",
                                              (intent["status"], intent["filled_qty"], intent["client_id"]))
                    if order["status"] == "rejected" and intent["purpose"] != "halt":
                        self._event_once("order_rejection", "An order was rejected; manual review required")
                    elif order["status"] == "rejected":
                        self.state.event("liquidation_rejected", f"Liquidation of {intent['symbol']} rejected; retry pending")
            if intent["submission_started"] and intent["status"] not in TERMINAL:
                problem = problem or "Unresolved or partially filled order; new entries suspended"
            expected[intent["symbol"]] = expected.get(intent["symbol"], 0) + (
                intent["filled_qty"] * (1 if intent["side"] == "buy" else -1))
        positions = self.broker.positions()
        if any(not math.isfinite(p[key]) or p[key] < 0 for p in positions.values()
               for key in ("qty", "market_value")):
            raise RuntimeError("Broker returned invalid or short position")
        for symbol in set(expected) | set(positions):
            if abs(expected.get(symbol, 0) - positions.get(symbol, {}).get("qty", 0)) > 1e-6:
                problem = f"Unexplained position difference for {symbol}; manual review required"
        known = {i["client_id"] for i in self.state.intentions()}
        if any(o["client_id"] not in known for o in self.broker.open_orders()):
            problem = "Unrecognized broker orders; manual review required"
        if self.state.get("order_rejection"):
            problem = problem or self.state.get("order_rejection")
        self.state.set("suspension", problem)
        return positions, problem

    def _submit(self, session, symbol, side, purpose="signal", notional=None, qty=None, attempt=0):
        identity = f"{self.state.get('account_id')}:{session}:{symbol}:{side}:{purpose}:{attempt}"
        client_id = "bt-" + hashlib.sha256(identity.encode()).hexdigest()[:40]
        with self.state.db:
            self.state.db.execute("""INSERT OR IGNORE INTO intentions
                (client_id,session,symbol,side,notional,qty,purpose) VALUES(?,?,?,?,?,?,?)""",
                (client_id, session, symbol, side, notional, qty, purpose))
        intent = self.state.db.execute("SELECT * FROM intentions WHERE client_id=?", (client_id,)).fetchone()
        if intent["submission_started"] or intent["status"] in TERMINAL:
            return True  # Includes unknown submissions: never resubmit blindly.
        if purpose == "signal" and not self._submission_window(session):
            self._expire_unsubmitted(client_id, session)
            return False
        with self.state.db:
            self.state.db.execute("UPDATE intentions SET submission_started=1 WHERE client_id=?", (client_id,))
        # A durable commit may itself cross the boundary. No network work belongs
        # between this final wall-clock check and transmission.
        if purpose == "signal" and not self._submission_window(session):
            self._expire_unsubmitted(client_id, session)
            return False
        try:
            self.broker.submit(client_id, symbol, side, notional=notional, qty=qty)
        except Exception as exc:
            self.state.event("submission_error", f"{symbol} {side}: {type(exc).__name__}; reconcile client ID {client_id}")
            raise
        return True

    def _submission_window(self, session):
        now = self.clock()
        if now.tzinfo is None:
            raise ValueError("Execution clock must have a timezone")
        local = now.astimezone(EASTERN)
        return local.date().isoformat() == session and (local.hour, local.minute) == (9, 35)

    def _expire_unsubmitted(self, client_id, session):
        with self.state.db:
            self.state.db.execute("UPDATE intentions SET submission_started=0,status='expired' WHERE client_id=?",
                                  (client_id,))
        self._event_once("execution_deadline", f"Skipped unsent orders for {session}: execution window elapsed")

    def halt(self, reason="Manual halt"):
        with self.state.lock():
            self._set_halt(reason)
            if self.state.get("account_id"):
                if self.broker.account()["id"] != self.state.get("account_id"):
                    raise RuntimeError("Paper account identity changed; liquidation prohibited")
                self._liquidate(self.clock())
            self._delivery()

    def _set_halt(self, reason):
        if not self.state.get("halt"):
            self.state.set("halt", reason)
            self.state.event("halt", reason)

    def _liquidate(self, now):
        # Cancel all known orders, including exits, then observe confirmed terminal states
        # before sizing liquidation. A cancellation request alone is not a cancellation.
        orders = self.broker.open_orders()
        known = {i["client_id"]: i for i in self.state.intentions()}
        for order in orders:
            if order["client_id"] not in known:
                self._event_once("halt_problem", "Unknown broker order prevents safe liquidation; manual review")
                return
            if known[order["client_id"]]["purpose"] != "halt":
                self.broker.cancel(order)
        positions, problem = self._reconcile()
        if problem and "order was rejected" not in problem:
            # Halt liquidation can continue after known rejection, never unknown fills.
            return
        if not self.broker.market_open():
            return
        for symbol, position in positions.items():
            if position["qty"] <= 0 or symbol not in UNIVERSE:
                self._event_once("halt_problem", "Unexpected position cannot be liquidated automatically")
                return
            attempts = [i for i in self.state.intentions() if i["symbol"] == symbol and i["purpose"] == "halt"]
            if any(i["status"] not in TERMINAL for i in attempts):
                continue
            last_attempt = self.state.get(f"liquidation_attempt:{symbol}")
            if last_attempt and (now - datetime.fromisoformat(last_attempt)).total_seconds() < 60:
                continue
            if not self.broker.eligible(symbol):
                self._event_once("halt_problem", f"{symbol} is not eligible for fractional liquidation")
                continue
            self.state.set(f"liquidation_attempt:{symbol}", now.isoformat())
            self._submit(now.date().isoformat(), symbol, "sell", "halt", qty=position["qty"], attempt=len(attempts))
            self._reconcile()

    def tick(self, now=None, closes=None, sessions=None):
        now = now or self.clock()
        if now.tzinfo is None:
            raise ValueError("tick timestamp must have a timezone")
        with self.state.lock():
            try:
                self.state.set("last_tick", now.isoformat())
                if not self.state.get("account_id"):
                    return self.status()
                account = self.broker.account()
                if account["id"] != self.state.get("account_id"):
                    raise RuntimeError("Paper account identity changed")
                # A negative cash balance blocks entries but must not block drawdown exits.
                equity = account["equity"]
                if not math.isfinite(equity):
                    raise RuntimeError("Invalid account equity")
                peak = max(self.state.get("peak", equity), equity)
                self.state.set("peak", peak)
                if equity <= peak * (1 - DRAWDOWN_LIMIT):
                    self._set_halt(f"Observed equity {equity:.2f} reached 10% below peak {peak:.2f}")
                self._summary(now, account, sessions)
                if self.state.get("halt"):
                    self._liquidate(now)
                    self._delivery()
                    return self.status()
                positions, problem = self._reconcile()
                self._valid_account(account)
                healthy = self._delivery()
                if not problem and closes is not None and sessions is not None:
                    from .reporting import protocol_hash

                    if self.state.get("active_protocol_hash") != protocol_hash():
                        raise RuntimeError("Research code changed or qualification is missing; manual review required")
                    self._signals(now, closes, sessions, positions, allow_entries=healthy)
                self._delivery()
            except Exception as exc:  # noqa: BLE001 - persist failures and continue risk monitoring
                self._event_once("suspension", f"Operation suspended: {type(exc).__name__}: {exc}")
                self._delivery()
            return self.status()

    def _signals(self, now, closes, sessions, positions, allow_entries=True):
        local = now.astimezone(EASTERN)
        today = pd.Timestamp(local.date())
        sessions = pd.DatetimeIndex(sessions).tz_localize(None).normalize()
        if today not in sessions or not self.broker.market_open():
            return
        if (local.hour, local.minute) != (9, 35):
            return  # Never chase a missed execution window.
        previous = sessions[sessions < today]
        if previous.empty:
            raise RuntimeError("Calendar does not cover previous completed session")
        completed = previous[-1]
        closes = closes.loc[closes.index <= completed, list(UNIVERSE)].copy()
        if (len(closes) < 200 or closes.index[-1] != completed or closes.index.has_duplicates
                or not closes.index.is_monotonic_increasing
                or closes.tail(200).isna().any().any()
                or not np.isfinite(closes.tail(200).to_numpy()).all()
                or not (closes.tail(200) > 0).all().all()):
            raise RuntimeError("Stale or incomplete completed-session signal data")
        expected = sessions[(sessions >= closes.index[-200]) & (sessions <= completed)]
        if not expected.equals(closes.index[-200:]):
            raise RuntimeError("Signal data is missing exchange sessions")
        if self.state.get("last_signal_session") == str(today.date()):
            signals = self.state.get("signal_plan", {})
        else:
            held = {}
            for symbol in positions:
                buys = [i for i in self.state.intentions()
                        if i["symbol"] == symbol and i["side"] == "buy" and i["filled_qty"] > 0]
                if not buys:
                    raise RuntimeError("Position has no persisted entry")
                entry = pd.Timestamp(buys[-1]["session"])
                held[symbol] = int(((sessions >= entry) & (sessions <= completed)).sum())
            weekly = completed.isocalendar()[:2] != today.isocalendar()[:2]
            signals = decide(self.state.get("active_strategy"), closes, held, weekly)
            # Save the entire plan atomically. Subsequent 09:35 ticks can continue it
            # after fills settle, while client IDs prevent repeat submissions.
            with self.state.db:
                self.state.db.executemany("INSERT OR REPLACE INTO meta VALUES(?,?)", [
                    ("signal_plan", json.dumps(signals)),
                    ("last_signal_session", json.dumps(str(today.date())))])
        for symbol, action in sorted(signals.items(), key=lambda item: item[1] == "buy"):
            positions, problem = self._reconcile()
            if problem:
                break
            account = self.broker.account()
            self._valid_account(account)
            peak = max(self.state.get("peak"), account["equity"])
            self.state.set("peak", peak)
            if account["equity"] <= peak * (1 - DRAWDOWN_LIMIT):
                self._set_halt("Drawdown threshold reached before order submission")
                self._liquidate(now)
                break
            if not self.broker.eligible(symbol):
                self.state.event("ineligible_asset", f"Skipped {symbol}: tradable fractional asset required")
                continue
            if action == "sell" and symbol in positions:
                if not self._submit(str(today.date()), symbol, "sell", qty=positions[symbol]["qty"]):
                    break
            elif action == "buy" and symbol not in positions and allow_entries:
                exposure = sum(abs(p["market_value"]) for p in positions.values())
                notional = math.floor(entry_notional(account["equity"], account["cash"], exposure) * 100) / 100
                if notional >= 1 and not self._submit(str(today.date()), symbol, "buy", notional=notional):
                    break
        self._reconcile()

    def _summary(self, now, account, sessions):
        if sessions is None:
            return
        local = now.astimezone(EASTERN)
        today = pd.Timestamp(local.date())
        sessions = pd.DatetimeIndex(sessions).tz_localize(None).normalize()
        # 16:10 also safely covers half-days; daemon runs outside market hours.
        if today not in sessions or (local.hour, local.minute) < (16, 10):
            return
        if self.state.get("last_summary") != str(today.date()):
            self.state.event("daily_summary", json.dumps({"session": str(today.date()), "account": account,
                                                         "status": self.status()}))
            self.state.set("last_summary", str(today.date()))
            following = sessions[sessions > today]
            if len(following) and following[0].isocalendar()[:2] != today.isocalendar()[:2]:
                self.state.event("weekly_review", "Review fills, costs, drawdown, and research assumptions; "
                                 "paper performance never promotes this runner to live trading.")

    def run(self, data_provider, interval=5):
        """Keep monitoring without ever opening a live endpoint. Ctrl-C exits."""
        if not 1 <= interval <= 60:
            raise ValueError("Monitoring interval must be between 1 and 60 seconds")
        # tick holds the same lock; reserve a separate daemon lock for the full lifecycle.
        from concurrent.futures import ThreadPoolExecutor

        from .state import State

        daemon = State(str(self.state.path) + ".daemon")
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="completed-data")
        future = None
        requested_day = None
        cache_day = None
        calendar_day = None
        closes, sessions = None, None
        try:
            with daemon.lock():
                while True:
                    now = self.clock()
                    local = now.astimezone(EASTERN)
                    if calendar_day != local.date():
                        try:
                            sessions = self._calendar_sessions(now)
                            calendar_day = local.date()
                        except Exception as exc:  # noqa: BLE001 - calendar failure cannot stop risk monitoring
                            self._event_once("calendar_error", f"Exchange calendar failed: {type(exc).__name__}")
                            sessions = None
                    if future and future.done():
                        try:
                            closes, _provider_sessions = future.result()
                            cache_day = requested_day
                        except Exception as exc:  # noqa: BLE001 - failed data cannot stop risk monitoring
                            self._event_once("data_error", f"Data provider failed: {type(exc).__name__}")
                            closes = None
                        future = None
                    self.tick(now, closes=closes if cache_day == local.date() else None, sessions=sessions)
                    # Network acquisition runs independently of equity monitoring.
                    if (future is None and requested_day != local.date()
                            and self.state.get("active_strategy") and not self.state.get("halt")
                            and sessions is not None and pd.Timestamp(local.date()) in sessions
                            and (9, 25) <= (local.hour, local.minute) <= (9, 35)):
                        requested_day = local.date()
                        future = executor.submit(data_provider)
                    time.sleep(interval)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
            daemon.close()

    @staticmethod
    def _calendar_sessions(now):
        from .data import calendar

        today = pd.Timestamp(now.astimezone(EASTERN).date())
        start, end = today - pd.Timedelta(days=500), today + pd.Timedelta(days=14)
        return calendar(str(start.date()), str(end.date())).sessions_in_range(start, end).tz_localize(None)
