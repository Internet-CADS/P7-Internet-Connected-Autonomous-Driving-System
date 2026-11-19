"""Durable run history. The gateway is the sole writer."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


def timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class RunStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, status TEXT NOT NULL, config TEXT NOT NULL, lanes TEXT NOT NULL,
                started_at TEXT NOT NULL, ended_at TEXT, reason TEXT,
                steps INTEGER NOT NULL DEFAULT 0, total_reward REAL NOT NULL DEFAULT 0,
                crashed INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS steps (
                run_id TEXT NOT NULL REFERENCES runs(id), seq INTEGER NOT NULL, record TEXT NOT NULL,
                PRIMARY KEY (run_id, seq)
            );
        """)
        with self.db:
            self.db.execute(
                "UPDATE runs SET status='failed', reason='Gateway restarted', ended_at=? WHERE status='running'",
                (timestamp(),),
            )

    def open_run(self, config: dict, lanes: list[dict]) -> str:
        run_id = str(uuid4())
        with self.db:
            self.db.execute(
                "INSERT INTO runs (id, status, config, lanes, started_at) VALUES (?, 'running', ?, ?, ?)",
                (run_id, json.dumps(config), json.dumps(lanes), timestamp()),
            )
        return run_id

    def commit(self, run_id: str, record: dict, completed: bool) -> None:
        with self.db:
            result = self.db.execute(
                """UPDATE runs SET steps=steps+1, total_reward=total_reward+?,
                   crashed=MAX(crashed, ?), status=?, ended_at=?
                   WHERE id=? AND status='running' AND steps=?""",
                (
                    record["reward"],
                    record["crashed"],
                    "completed" if completed else "running",
                    timestamp() if completed else None,
                    run_id,
                    record["seq"],
                ),
            )
            if result.rowcount != 1:
                raise RuntimeError("Step cannot be committed to this run")
            self.db.execute("INSERT INTO steps VALUES (?, ?, ?)", (run_id, record["seq"], json.dumps(record)))

    def fail(self, run_id: str, reason: str) -> None:
        with self.db:
            self.db.execute(
                "UPDATE runs SET status='failed', reason=?, ended_at=? WHERE id=? AND status='running'",
                (reason, timestamp(), run_id),
            )

    @staticmethod
    def _run(row: sqlite3.Row) -> dict:
        data = dict(row)
        data["config"] = json.loads(data["config"])
        data["lanes"] = json.loads(data["lanes"])
        data["crashed"] = bool(data["crashed"])
        return data

    def run(self, run_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return self._run(row) if row else None

    def runs(self, limit: int) -> list[dict]:
        return [
            self._run(row)
            for row in self.db.execute("SELECT * FROM runs ORDER BY rowid DESC LIMIT ?", (limit,))
        ]

    def encoded_records(self, run_id: str, after: int = -1) -> list[tuple[int, str]]:
        return [
            (row[0], row[1])
            for row in self.db.execute(
                "SELECT seq, record FROM steps WHERE run_id=? AND seq>? ORDER BY seq", (run_id, after)
            )
        ]
