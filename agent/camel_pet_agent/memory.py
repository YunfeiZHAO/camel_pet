"""SQLite-backed chat history for the Camel Pet agent.

The CAMEL `ChatAgent` already manages an in-process memory window. We
persist the same turns to disk so the camel remembers across restarts.
"""
from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class Turn:
    role: str  # "user" | "assistant"
    content: str
    created_at: float


def default_db_path() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif os.sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return root / "CamelPet" / "memory.db"


class ChatStore:
    def __init__(self, db_path: Path | None = None):
        self.path = Path(db_path) if db_path else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS turns (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                role        TEXT NOT NULL,
                content     TEXT NOT NULL,
                created_at  REAL NOT NULL
            )
            """
        )
        self._conn.commit()

    def append(self, role: str, content: str) -> None:
        self._conn.execute(
            "INSERT INTO turns (role, content, created_at) VALUES (?, ?, ?)",
            (role, content, time.time()),
        )
        self._conn.commit()

    def recent(self, limit: int = 40) -> list[Turn]:
        rows = self._conn.execute(
            "SELECT role, content, created_at FROM turns ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        rows.reverse()
        return [Turn(r, c, t) for (r, c, t) in rows]

    def clear(self) -> None:
        self._conn.execute("DELETE FROM turns")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def all(self) -> Iterable[Turn]:
        rows = self._conn.execute(
            "SELECT role, content, created_at FROM turns ORDER BY id ASC"
        ).fetchall()
        return (Turn(r, c, t) for (r, c, t) in rows)
