"""Private SQLite ledger and a process-wide advisory lock."""
from __future__ import annotations

import fcntl
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class State:
    def __init__(self, path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.path)
        os.chmod(self.path, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS intentions(
                client_id TEXT PRIMARY KEY, session TEXT NOT NULL, symbol TEXT NOT NULL,
                side TEXT NOT NULL, notional REAL, qty REAL, purpose TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'prepared', filled_qty REAL NOT NULL DEFAULT 0,
                submission_started INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS events(
                id INTEGER PRIMARY KEY, created TEXT DEFAULT CURRENT_TIMESTAMP,
                kind TEXT NOT NULL, message TEXT NOT NULL, delivered INTEGER DEFAULT 0);
        """)
        self.db.commit()

    @contextmanager
    def lock(self):
        with open(str(self.path) + ".lock", "a") as handle:
            os.chmod(handle.name, 0o600)
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("Another paper process holds the state lock") from exc
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, json.dumps(value)))

    def event(self, kind, message):
        with self.db:
            self.db.execute("INSERT INTO events(kind,message) VALUES(?,?)", (kind, message))

    def intentions(self):
        return [dict(row) for row in self.db.execute("SELECT * FROM intentions ORDER BY rowid")]

    def close(self):
        self.db.close()
