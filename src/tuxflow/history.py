"""Private local transcription history."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tuxflow.paths import database_file, ensure_directories


@dataclass(frozen=True, slots=True)
class HistoryItem:
    id: int
    created_at: str
    text: str
    raw_text: str
    language: str
    duration_seconds: float
    model: str


class HistoryStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or database_file()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        ensure_directories()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS transcriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    text TEXT NOT NULL,
                    raw_text TEXT NOT NULL,
                    language TEXT NOT NULL,
                    duration_seconds REAL NOT NULL,
                    model TEXT NOT NULL
                )
                """
            )
        self._restrict()

    def _restrict(self) -> None:
        """Keep the transcripts readable only by their owner.

        SQLite creates the database with the process umask, which normally
        means 0644. Doing this on every open also heals a database an older
        version left world-readable.
        """
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def add(
        self,
        *,
        text: str,
        raw_text: str,
        language: str,
        duration_seconds: float,
        model: str,
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO transcriptions
                    (created_at, text, raw_text, language, duration_seconds, model)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(UTC).isoformat(),
                    text,
                    raw_text,
                    language,
                    duration_seconds,
                    model,
                ),
            )
            return int(cursor.lastrowid)

    def recent(self, limit: int = 50) -> list[HistoryItem]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM transcriptions ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [HistoryItem(**dict(row)) for row in rows]

    def clear(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM transcriptions")
