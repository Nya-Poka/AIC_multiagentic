from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunStore:
    """Durable, single-node run ledger and centralized event store.

    SQLite WAL is intentionally used for the competition deployment. The API
    is small enough to replace with PostgreSQL without changing orchestration.
    """

    def __init__(self, path: Path | None) -> None:
        self.path = path.resolve() if path is not None else None
        self._lock = threading.RLock()
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize()

    @classmethod
    def disabled(cls) -> "RunStore":
        return cls(None)

    @property
    def enabled(self) -> bool:
        return self.path is not None

    def _connect(self) -> sqlite3.Connection:
        if self.path is None:
            raise RuntimeError("run store is disabled")
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS research_runs (
                    session_id TEXT PRIMARY KEY,
                    request_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    report_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS task_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    step TEXT NOT NULL,
                    skill TEXT NOT NULL,
                    agent_aic TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    duration_ms REAL,
                    error TEXT,
                    occurred_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_attempts_session
                    ON task_attempts(session_id, id);
                CREATE TABLE IF NOT EXISTS telemetry_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    service TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_session
                    ON telemetry_events(session_id, id);
                """
            )

    def begin_run(self, session_id: str, request: dict[str, Any]) -> None:
        if not self.enabled:
            return
        now = _now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO research_runs(
                    session_id, request_json, status, created_at, updated_at
                ) VALUES (?, ?, 'running', ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    request_json=excluded.request_json,
                    status='running',
                    report_json=NULL,
                    error=NULL,
                    updated_at=excluded.updated_at
                """,
                (session_id, json.dumps(request, ensure_ascii=False), now, now),
            )

    def record_attempt(
        self,
        *,
        session_id: str,
        step: str,
        skill: str,
        agent_aic: str,
        endpoint: str,
        attempt: int,
        status: str,
        duration_ms: float | None = None,
        error: str | None = None,
    ) -> None:
        if not self.enabled:
            return
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO task_attempts(
                    session_id, step, skill, agent_aic, endpoint, attempt,
                    status, duration_ms, error, occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    step,
                    skill,
                    agent_aic,
                    endpoint,
                    attempt,
                    status,
                    duration_ms,
                    error,
                    _now(),
                ),
            )

    def event(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        session_id: str | None = None,
        service: str = "research-mesh-leader",
    ) -> None:
        if not self.enabled:
            return
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO telemetry_events(
                    session_id, service, event_type, payload_json, occurred_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    service,
                    event_type,
                    json.dumps(payload, ensure_ascii=False),
                    _now(),
                ),
            )

    def finish_run(
        self,
        session_id: str,
        *,
        status: str,
        report: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        if not self.enabled:
            return
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE research_runs
                SET status=?, report_json=?, error=?, updated_at=?
                WHERE session_id=?
                """,
                (
                    status,
                    json.dumps(report, ensure_ascii=False) if report is not None else None,
                    error,
                    _now(),
                    session_id,
                ),
            )

    def get_run(self, session_id: str) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM research_runs WHERE session_id=?", (session_id,)
            ).fetchone()
            if row is None:
                return None
            attempts = connection.execute(
                "SELECT * FROM task_attempts WHERE session_id=? ORDER BY id",
                (session_id,),
            ).fetchall()
        return {
            "session_id": row["session_id"],
            "status": row["status"],
            "request": json.loads(row["request_json"]),
            "report": json.loads(row["report_json"]) if row["report_json"] else None,
            "error": row["error"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "attempts": [dict(item) for item in attempts],
        }

    def metrics(self) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False}
        with self._lock, self._connect() as connection:
            runs = connection.execute(
                "SELECT status, COUNT(*) AS count FROM research_runs GROUP BY status"
            ).fetchall()
            attempts = connection.execute(
                """
                SELECT skill, status, COUNT(*) AS count, AVG(duration_ms) AS mean_ms
                FROM task_attempts GROUP BY skill, status ORDER BY skill, status
                """
            ).fetchall()
        run_counts = {row["status"]: row["count"] for row in runs}
        total = sum(run_counts.values())
        completed = run_counts.get("completed", 0)
        return {
            "enabled": True,
            "run_count": total,
            "run_status": run_counts,
            "completion_rate": round(completed / total, 6) if total else None,
            "attempts": [dict(row) for row in attempts],
        }
