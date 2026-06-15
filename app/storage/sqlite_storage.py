import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Optional


class SqliteStorage:
    def __init__(self, db_path: str = "data/sweeper.db"):
        self._db_path = db_path
        self._local = threading.local()
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)

    @property
    def _conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self._db_path)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA synchronous=NORMAL")
        return self._local.conn

    def initialize(self) -> None:
        conn = self._conn
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern TEXT NOT NULL,
                chain TEXT NOT NULL,
                address TEXT NOT NULL,
                confirmed REAL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                details TEXT,
                timestamp TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS license_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT NOT NULL UNIQUE,
                features TEXT,
                created_at TEXT NOT NULL
            );
        """)
        conn.commit()

    def save_result(self, pattern: str, chain: str, address: str, confirmed: float) -> None:
        conn = self._conn
        conn.execute(
            "INSERT INTO results (pattern, chain, address, confirmed, created_at) VALUES (?, ?, ?, ?, ?)",
            (pattern, chain, address, confirmed, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()

    def save_audit(self, action: str, details: Optional[dict] = None) -> None:
        conn = self._conn
        conn.execute(
            "INSERT INTO audit_log (action, details, timestamp) VALUES (?, ?, ?)",
            (action, json.dumps(details) if details else None, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()

    def save_license(self, token: str, features: list[str]) -> None:
        conn = self._conn
        conn.execute(
            "INSERT OR IGNORE INTO license_records (token, features, created_at) VALUES (?, ?, ?)",
            (token, json.dumps(features), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()

    def get_recent_results(self, limit: int = 100) -> list[dict]:
        conn = self._conn
        rows = conn.execute(
            "SELECT * FROM results ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
