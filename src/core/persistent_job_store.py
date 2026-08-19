"""SQLite-backed runtime job state for restart-safe local execution."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled"}
RECOVERABLE_JOB_STATUSES = {"queued", "running", "cancelling"}


class PersistentJobStore:
    """Persist analysis jobs and recover interrupted work after API restarts."""

    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            from config.settings import get_settings

            db_path = get_settings().data_dir / "runtime_jobs.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS runtime_jobs (
                    job_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT '',
                    request_json TEXT NOT NULL DEFAULT '{}',
                    result_json TEXT,
                    error TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_jobs_status_updated
                    ON runtime_jobs(status, updated_at);
                """
            )

    def create(
        self,
        *,
        job_id: str,
        kind: str,
        request: dict[str, Any],
        max_attempts: int = 3,
    ) -> dict[str, Any]:
        now = datetime.now().isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO runtime_jobs
                   (job_id, kind, status, progress, message, request_json,
                    attempts, max_attempts, created_at, updated_at)
                   VALUES (?, ?, 'queued', 0, '等待执行', ?, 0, ?, ?, ?)""",
                (
                    job_id,
                    kind,
                    json.dumps(request, ensure_ascii=False),
                    max(1, int(max_attempts)),
                    now,
                    now,
                ),
            )
            conn.commit()
        return self.get(job_id) or {}

    def update(self, job_id: str, **fields: Any) -> dict[str, Any]:
        allowed = {"status", "progress", "message", "result", "error", "attempts"}
        values = {key: value for key, value in fields.items() if key in allowed}
        if not values:
            return self.get(job_id) or {}
        assignments: list[str] = []
        params: list[Any] = []
        for key, value in values.items():
            column = "result_json" if key == "result" else key
            assignments.append(f"{column}=?")
            params.append(json.dumps(value, ensure_ascii=False) if key == "result" else value)
        assignments.append("updated_at=?")
        params.extend([datetime.now().isoformat(), job_id])
        with self._conn() as conn:
            cursor = conn.execute(
                f"UPDATE runtime_jobs SET {', '.join(assignments)} WHERE job_id=?",
                params,
            )
            if cursor.rowcount != 1:
                raise KeyError(job_id)
            conn.commit()
        return self.get(job_id) or {}

    def get(self, job_id: str) -> Optional[dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM runtime_jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
        return self._decode(row) if row else None

    def list_recent(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM runtime_jobs ORDER BY updated_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [self._decode(row) for row in rows]

    def recover_interrupted(self) -> list[dict[str, Any]]:
        """Requeue interrupted jobs, failing only those beyond retry budget."""
        recovered: list[dict[str, Any]] = []
        now = datetime.now().isoformat()
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM runtime_jobs
                   WHERE status IN ('queued', 'running', 'cancelling')
                   ORDER BY created_at"""
            ).fetchall()
            for row in rows:
                attempts = int(row["attempts"] or 0) + 1
                if attempts >= int(row["max_attempts"] or 3):
                    conn.execute(
                        """UPDATE runtime_jobs SET status='failed', progress=100,
                           message='重启恢复次数已耗尽', error=?, attempts=?, updated_at=?
                           WHERE job_id=?""",
                        ("API 重启期间任务中断", attempts, now, row["job_id"]),
                    )
                    continue
                conn.execute(
                    """UPDATE runtime_jobs SET status='queued', progress=0,
                       message='服务重启后重新排队', error=NULL, attempts=?, updated_at=?
                       WHERE job_id=?""",
                    (attempts, now, row["job_id"]),
                )
                recovered.append({**self._decode(row), "status": "queued", "progress": 0,
                                  "message": "服务重启后重新排队", "error": None,
                                  "attempts": attempts, "updated_at": now})
            conn.commit()
        return recovered

    def status(self) -> dict[str, Any]:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT COUNT(*) AS total,
                          SUM(status IN ('queued','running','cancelling')) AS active,
                          SUM(status='completed') AS completed,
                          SUM(status='failed') AS failed,
                          MAX(updated_at) AS latest_updated_at
                   FROM runtime_jobs"""
            ).fetchone()
        payload = dict(row or {})
        for key in ("total", "active", "completed", "failed"):
            payload[key] = int(payload.get(key) or 0)
        payload["db_path"] = str(self.db_path)
        return payload

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "job_id": row["job_id"],
            "kind": row["kind"],
            "status": row["status"],
            "progress": int(row["progress"] or 0),
            "message": row["message"] or "",
            "request": json.loads(row["request_json"] or "{}"),
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "error": row["error"],
            "attempts": int(row["attempts"] or 0),
            "max_attempts": int(row["max_attempts"] or 0),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
