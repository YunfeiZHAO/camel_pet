"""Activity store — SQLite persistence for screen activity records.

Stores periodic activity snapshots captured by the screen monitor and
provides query methods for the dashboard and advisor.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .memory import default_db_path


@dataclass(frozen=True)
class ActivityRecord:
    id: int
    timestamp: float
    status: str
    app: str | None
    details: dict[str, Any]


@dataclass
class DailySummary:
    date: str
    breakdown: dict[str, float] = field(default_factory=dict)  # status -> minutes
    screen_time: float = 0.0  # total active minutes


class ActivityStore:
    """SQLite store for activity monitoring data."""

    def __init__(self, db_path: Path | None = None):
        self.path = db_path or default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS activities (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   REAL NOT NULL,
                status      TEXT NOT NULL,
                app         TEXT,
                details     TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_activities_ts ON activities(timestamp);

            CREATE TABLE IF NOT EXISTS daily_summary (
                date        TEXT NOT NULL,
                status      TEXT NOT NULL,
                total_minutes REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (date, status)
            );
            """
        )
        self._conn.commit()

    def insert(self, status: str, app: str | None = None, details: dict | None = None) -> int:
        """Insert a new activity record at the current time."""
        cur = self._conn.execute(
            "INSERT INTO activities (timestamp, status, app, details) VALUES (?, ?, ?, ?)",
            (time.time(), status, app, json.dumps(details or {})),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_today(self) -> list[ActivityRecord]:
        """Get all activity records for today."""
        start = _day_start_ts()
        return self._query_range(start, time.time())

    def get_range(self, start_date: str, end_date: str) -> list[ActivityRecord]:
        """Get records in a date range (inclusive). Dates are YYYY-MM-DD."""
        start_ts = datetime.fromisoformat(start_date).timestamp()
        end_ts = (datetime.fromisoformat(end_date) + timedelta(days=1)).timestamp()
        return self._query_range(start_ts, end_ts)

    def get_recent(self, seconds: float) -> list[ActivityRecord]:
        """Get records within the last N seconds (ordered ascending)."""
        now = time.time()
        return self._query_range(now - max(0.0, seconds), now)

    def _query_range(self, start_ts: float, end_ts: float) -> list[ActivityRecord]:
        rows = self._conn.execute(
            "SELECT id, timestamp, status, app, details FROM activities "
            "WHERE timestamp >= ? AND timestamp < ? ORDER BY timestamp ASC",
            (start_ts, end_ts),
        ).fetchall()
        return [
            ActivityRecord(
                id=r[0],
                timestamp=r[1],
                status=r[2],
                app=r[3],
                details=json.loads(r[4]) if r[4] else {},
            )
            for r in rows
        ]

    def today_summary(self) -> DailySummary:
        """Compute today's activity summary on the fly."""
        records = self.get_today()
        return self._compute_summary(date.today().isoformat(), records)

    def get_daily_summaries(self, start_date: str, end_date: str) -> list[DailySummary]:
        """Get daily summaries for a date range."""
        rows = self._conn.execute(
            "SELECT date, status, total_minutes FROM daily_summary "
            "WHERE date >= ? AND date <= ? ORDER BY date ASC",
            (start_date, end_date),
        ).fetchall()
        by_date: dict[str, DailySummary] = {}
        for d, status, minutes in rows:
            if d not in by_date:
                by_date[d] = DailySummary(date=d)
            by_date[d].breakdown[status] = minutes
            by_date[d].screen_time += minutes
        return list(by_date.values())

    def materialize_daily_summary(self, target_date: str | None = None) -> None:
        """Aggregate activity records into the daily_summary table."""
        target = target_date or date.today().isoformat()
        start_ts = datetime.fromisoformat(target).timestamp()
        end_ts = start_ts + 86400

        records = self._query_range(start_ts, end_ts)
        summary = self._compute_summary(target, records)

        # Upsert
        self._conn.execute("DELETE FROM daily_summary WHERE date = ?", (target,))
        for status, minutes in summary.breakdown.items():
            self._conn.execute(
                "INSERT INTO daily_summary (date, status, total_minutes) VALUES (?, ?, ?)",
                (target, status, minutes),
            )
        self._conn.commit()

    def _compute_summary(self, day: str, records: list[ActivityRecord]) -> DailySummary:
        """Compute breakdown from a list of records.

        Each record represents a snapshot. Time attributed to a record is the
        interval until the next record (capped at the monitor interval).
        """
        summary = DailySummary(date=day)
        if not records:
            return summary

        max_interval = 10 * 60  # cap at 10 minutes (covers missed captures)
        for i, rec in enumerate(records):
            if i + 1 < len(records):
                duration = min(records[i + 1].timestamp - rec.timestamp, max_interval)
            else:
                duration = min(time.time() - rec.timestamp, max_interval)
            minutes = duration / 60.0
            if rec.status != "away":
                summary.screen_time += minutes
            summary.breakdown[rec.status] = summary.breakdown.get(rec.status, 0) + minutes

        return summary

    def delete_day(self, target_date: str) -> int:
        """Delete all activity data for a specific day (YYYY-MM-DD). Returns rows deleted."""
        start_ts = datetime.fromisoformat(target_date).timestamp()
        end_ts = start_ts + 86400
        cur = self._conn.execute(
            "DELETE FROM activities WHERE timestamp >= ? AND timestamp < ?",
            (start_ts, end_ts),
        )
        self._conn.execute("DELETE FROM daily_summary WHERE date = ?", (target_date,))
        self._conn.commit()
        return cur.rowcount

    def clear(self) -> None:
        """Delete all activity data."""
        self._conn.execute("DELETE FROM activities")
        self._conn.execute("DELETE FROM daily_summary")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


def _day_start_ts() -> float:
    """Timestamp for the start of today (local time)."""
    today = date.today()
    return datetime(today.year, today.month, today.day).timestamp()
