"""Repository-wide append-only research trial ledger."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


@dataclass(frozen=True)
class ExperimentTrial:
    trial_id: str
    research_family: str
    market: str
    horizon: str
    target_version: str
    feature_version: str
    dataset_hash: str
    config_hash: str
    source_type: str
    report_path: str
    best_model: str = ""
    should_promote: bool = False
    status: str = "completed"
    candidates: list[str] = field(default_factory=list)
    thresholds: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    @property
    def research_key(self) -> str:
        return ExperimentLedger.research_key(
            self.research_family,
            self.market,
            self.horizon,
            self.target_version,
        )


class ExperimentLedger:
    """SQLite ledger whose trial rows cannot be updated or deleted."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @classmethod
    def default(cls) -> "ExperimentLedger":
        from config.settings import get_settings

        return cls(get_settings().data_dir / "quant" / "experiment_ledger.db")

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS experiment_trials (
                    trial_id TEXT PRIMARY KEY,
                    research_key TEXT NOT NULL,
                    research_family TEXT NOT NULL,
                    market TEXT NOT NULL,
                    horizon TEXT NOT NULL,
                    target_version TEXT NOT NULL,
                    feature_version TEXT NOT NULL,
                    dataset_hash TEXT NOT NULL,
                    config_hash TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    report_path TEXT NOT NULL,
                    best_model TEXT,
                    should_promote INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    candidates_json TEXT NOT NULL,
                    thresholds_json TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_experiment_trials_research
                    ON experiment_trials(research_key, created_at);
                CREATE INDEX IF NOT EXISTS idx_experiment_trials_dataset
                    ON experiment_trials(dataset_hash, created_at);

                CREATE TRIGGER IF NOT EXISTS experiment_trials_no_update
                BEFORE UPDATE ON experiment_trials
                BEGIN
                    SELECT RAISE(ABORT, 'experiment ledger is append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS experiment_trials_no_delete
                BEFORE DELETE ON experiment_trials
                BEGIN
                    SELECT RAISE(ABORT, 'experiment ledger is append-only');
                END;
                """
            )
            conn.commit()

    @staticmethod
    def research_key(
        research_family: str,
        market: str,
        horizon: str,
        target_version: str,
    ) -> str:
        payload = "|".join((
            str(research_family).strip().lower(),
            str(market).strip().upper(),
            str(horizon).strip().lower(),
            str(target_version).strip().lower(),
        ))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def config_hash(config: dict[str, Any]) -> str:
        canonical = json.dumps(config, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]

    def prior_trial_count(
        self,
        *,
        research_family: str,
        market: str,
        horizon: str,
        target_version: str,
    ) -> int:
        key = self.research_key(research_family, market, horizon, target_version)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total FROM experiment_trials WHERE research_key=?",
                (key,),
            ).fetchone()
        return int((row or {})["total"] or 0)

    def append(self, trial: ExperimentTrial) -> str:
        created_at = trial.created_at or datetime.now().isoformat()
        with self._conn() as conn:
            try:
                conn.execute(
                    """INSERT INTO experiment_trials (
                           trial_id, research_key, research_family, market, horizon,
                           target_version, feature_version, dataset_hash, config_hash,
                           source_type, report_path, best_model, should_promote, status,
                           candidates_json, thresholds_json, metrics_json, created_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        trial.trial_id,
                        trial.research_key,
                        trial.research_family,
                        trial.market.upper(),
                        trial.horizon,
                        trial.target_version,
                        trial.feature_version,
                        trial.dataset_hash,
                        trial.config_hash,
                        trial.source_type,
                        trial.report_path,
                        trial.best_model,
                        int(trial.should_promote),
                        trial.status,
                        json.dumps(trial.candidates, ensure_ascii=False, sort_keys=True),
                        json.dumps(trial.thresholds, ensure_ascii=False, sort_keys=True),
                        json.dumps(trial.metrics, ensure_ascii=False, sort_keys=True),
                        created_at,
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"试验已存在，账本拒绝覆盖: {trial.trial_id}") from exc
        return trial.trial_id

    def has_trial(self, trial_id: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM experiment_trials WHERE trial_id=?",
                (trial_id,),
            ).fetchone()
        return row is not None

    def trials(
        self,
        *,
        research_family: Optional[str] = None,
        market: Optional[str] = None,
        horizon: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if research_family:
            where.append("research_family=?")
            params.append(research_family)
        if market:
            where.append("market=?")
            params.append(market.upper())
        if horizon:
            where.append("horizon=?")
            params.append(horizon)
        clause = " WHERE " + " AND ".join(where) if where else ""
        params.append(max(1, int(limit)))
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM experiment_trials{clause} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [self._decode(dict(row)) for row in rows]

    def status(self) -> dict[str, Any]:
        with self._conn() as conn:
            totals = conn.execute(
                """SELECT COUNT(*) AS total,
                          COUNT(DISTINCT research_key) AS research_questions,
                          SUM(should_promote) AS promoted,
                          MIN(created_at) AS first_trial,
                          MAX(created_at) AS latest_trial
                   FROM experiment_trials"""
            ).fetchone()
            groups = conn.execute(
                """SELECT research_family, market, horizon, target_version,
                          COUNT(*) AS trials, SUM(should_promote) AS promoted
                   FROM experiment_trials
                   GROUP BY research_family, market, horizon, target_version
                   ORDER BY research_family, market, horizon"""
            ).fetchall()
        return {
            "db_path": str(self.db_path),
            "append_only": True,
            "total_trials": int(totals["total"] or 0),
            "research_questions": int(totals["research_questions"] or 0),
            "promoted_trials": int(totals["promoted"] or 0),
            "first_trial": totals["first_trial"],
            "latest_trial": totals["latest_trial"],
            "groups": [dict(row) for row in groups],
        }

    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        for key in ("candidates", "thresholds", "metrics"):
            row[key] = json.loads(row.pop(f"{key}_json") or "{}")
        row["should_promote"] = bool(row.get("should_promote"))
        return row


def trial_from_report(
    *,
    trial_id: str,
    research_family: str,
    config: dict[str, Any],
    dataset_hash: str,
    report_path: str,
    source_type: str,
    promotion: dict[str, Any],
    aggregate_metrics: dict[str, Any],
) -> ExperimentTrial:
    candidates = sorted(
        name for name in aggregate_metrics
        if name not in {"empirical_prior", "simple_momentum"}
    )
    return ExperimentTrial(
        trial_id=trial_id,
        research_family=research_family,
        market=str(config.get("market") or ""),
        horizon=str(config.get("horizon") or ""),
        target_version=str(config.get("target_version") or ""),
        feature_version=str(config.get("feature_version") or ""),
        dataset_hash=dataset_hash,
        config_hash=ExperimentLedger.config_hash(config),
        source_type=source_type,
        report_path=report_path,
        best_model=str(promotion.get("best_model") or ""),
        should_promote=bool(promotion.get("should_promote")),
        candidates=candidates,
        thresholds={
            "required_brier_delta": promotion.get("required_brier_delta"),
            "required_actionable_coverage": promotion.get("required_actionable_coverage"),
        },
        metrics={
            "promotion_gate": promotion,
            "best_model_metrics": aggregate_metrics.get(promotion.get("best_model") or "", {}),
        },
    )
