"""
预测追踪存储

SQLite 数据库，记录每次预测的完整信息，
支持事后验证和准确率统计。
"""

import json
import sqlite3
import uuid
import logging
from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class PredictionRecord:
    """一次预测的完整记录"""
    id: str
    target: str
    target_name: Optional[str] = None
    timeframe: str = ""
    direction: str = "neutral"
    min_pct: Optional[float] = None
    max_pct: Optional[float] = None
    confidence: float = 0.0
    target_version: str = ""
    target_type: str = ""
    residualization_mode: str = ""
    market_beta: Optional[float] = None
    horizon: str = ""
    horizon_trading_days: Optional[int] = None
    horizon_calendar_days: Optional[int] = None
    benchmark_symbol: Optional[str] = None
    up_threshold_pct: Optional[float] = None
    down_threshold_pct: Optional[float] = None
    neutral_band_pct: Optional[float] = None
    expected_excess_return_pct: Optional[float] = None
    expected_return_p10: Optional[float] = None
    expected_return_p50: Optional[float] = None
    expected_return_p90: Optional[float] = None
    prob_up: Optional[float] = None
    prob_down: Optional[float] = None
    prob_no_edge: Optional[float] = None
    edge_score: Optional[float] = None
    decision: str = ""
    no_trade_reason: str = ""
    neutral_reason: str = ""
    predicted_at: str = ""
    valid_until: str = ""
    actual_direction: Optional[str] = None
    actual_change_pct: Optional[float] = None
    actual_effective_return_pct: Optional[float] = None
    actual_absolute_return_pct: Optional[float] = None
    actual_benchmark_return_pct: Optional[float] = None
    window_max_effective_return_pct: Optional[float] = None
    window_min_effective_return_pct: Optional[float] = None
    target_type_used: str = ""
    brier_score: Optional[float] = None
    edge_hit: Optional[int] = None
    direction_correct: Optional[int] = None
    magnitude_hit: Optional[int] = None
    verified_at: Optional[str] = None
    agents_used: list[str] = field(default_factory=list)
    agents_failed: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    llm_model: str = ""
    summary: str = ""
    report_json: str = ""
    report_md: str = ""

    @property
    def is_verified(self) -> bool:
        return self.verified_at is not None

    @property
    def predicted_magnitude_str(self) -> str:
        if self.min_pct is None or self.max_pct is None:
            return "N/A"
        return f"{self.min_pct:+.1f}% ~ {self.max_pct:+.1f}%"

    @property
    def predicted_mid_pct(self) -> Optional[float]:
        if self.min_pct is None or self.max_pct is None:
            return None
        return (self.min_pct + self.max_pct) / 2.0


class PredictionStore:
    """预测追踪数据库"""

    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            from config.settings import get_settings
            db_path = get_settings().data_dir / "predictions.db"

        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = str(db_path)
        self._init_db()

    def _init_db(self):
        """初始化数据库"""
        schema_path = Path(__file__).parent / "schema.sql"
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if schema_path.exists():
                conn.executescript(schema_path.read_text(encoding="utf-8"))
            self._migrate_schema(conn)
            self._backfill_prediction_v2_columns(conn)
            conn.commit()
        logger.info(f"数据库就绪: {self.db_path}")

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        """兼容已有 predictions.db，补齐 Prediction Target V2 列。"""
        self._ensure_columns(conn, "predictions", {
            "target_version": "TEXT",
            "target_type": "TEXT",
            "residualization_mode": "TEXT",
            "market_beta": "REAL",
            "horizon": "TEXT",
            "horizon_trading_days": "INTEGER",
            "horizon_calendar_days": "INTEGER",
            "benchmark_symbol": "TEXT",
            "up_threshold_pct": "REAL",
            "down_threshold_pct": "REAL",
            "neutral_band_pct": "REAL",
            "expected_excess_return_pct": "REAL",
            "expected_return_p10": "REAL",
            "expected_return_p50": "REAL",
            "expected_return_p90": "REAL",
            "prob_up": "REAL",
            "prob_down": "REAL",
            "prob_no_edge": "REAL",
            "edge_score": "REAL",
            "decision": "TEXT",
            "no_trade_reason": "TEXT",
            "neutral_reason": "TEXT",
            "actual_effective_return_pct": "REAL",
            "actual_absolute_return_pct": "REAL",
            "actual_benchmark_return_pct": "REAL",
            "window_max_effective_return_pct": "REAL",
            "window_min_effective_return_pct": "REAL",
            "target_type_used": "TEXT",
            "brier_score": "REAL",
            "edge_hit": "INTEGER",
        })
        self._ensure_columns(conn, "accuracy_stats", {
            "brier_score": "REAL DEFAULT 0",
            "edge_hit_rate": "REAL DEFAULT 0",
            "avg_edge_score": "REAL DEFAULT 0",
            "actionable_coverage": "REAL DEFAULT 0",
            "avg_actual_effective_return_pct": "REAL DEFAULT 0",
            "avg_expected_excess_return_pct": "REAL DEFAULT 0",
        })
        conn.execute("CREATE INDEX IF NOT EXISTS idx_predictions_decision ON predictions(decision)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_predictions_edge ON predictions(edge_score)")

    @staticmethod
    def _ensure_columns(
        conn: sqlite3.Connection,
        table: str,
        columns: dict[str, str],
    ) -> None:
        existing = {
            row[1]
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for name, definition in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def _backfill_prediction_v2_columns(self, conn: sqlite3.Connection) -> None:
        """从 report_json/旧方向字段回填 V2 可查询列。"""
        rows = conn.execute(
            """SELECT id, target, timeframe, direction, min_pct, max_pct, confidence,
                      report_json, target_version, target_type, residualization_mode,
                      market_beta, expected_excess_return_pct,
                      expected_return_p10, expected_return_p50, expected_return_p90,
                      prob_up, prob_down, prob_no_edge, edge_score, decision
               FROM predictions
               WHERE target_version IS NULL OR target_version=''
                  OR json_extract(report_json, '$.prediction_target.target_version') IS NULL
                  OR expected_excess_return_pct IS NULL
                  OR prob_up IS NULL
                  OR prob_down IS NULL
                  OR prob_no_edge IS NULL
                  OR decision IS NULL
                  OR decision=''
                  OR (decision='observe'
                      AND (no_trade_reason IS NULL OR no_trade_reason='')
                      AND (neutral_reason IS NULL OR neutral_reason='')
                      AND direction IN ('bullish','bearish'))"""
        ).fetchall()
        if not rows:
            return

        for row in rows:
            payload = self._prediction_v2_payload_from_row(dict(row))
            conn.execute(
                """UPDATE predictions SET
                   target_version=?,
                   target_type=?,
                   residualization_mode=?,
                   market_beta=?,
                   horizon=?, horizon_trading_days=?,
                   horizon_calendar_days=?, benchmark_symbol=?,
                   up_threshold_pct=?, down_threshold_pct=?, neutral_band_pct=?,
                   expected_excess_return_pct=COALESCE(expected_excess_return_pct, ?),
                   expected_return_p10=COALESCE(expected_return_p10, ?),
                   expected_return_p50=COALESCE(expected_return_p50, ?),
                   expected_return_p90=COALESCE(expected_return_p90, ?),
                   prob_up=COALESCE(prob_up, ?),
                   prob_down=COALESCE(prob_down, ?),
                   prob_no_edge=COALESCE(prob_no_edge, ?),
                   edge_score=COALESCE(edge_score, ?),
                   decision=CASE
                       WHEN decision IS NULL OR decision='' THEN ?
                       WHEN decision='observe'
                            AND (no_trade_reason IS NULL OR no_trade_reason='')
                            AND (neutral_reason IS NULL OR neutral_reason='')
                            AND direction IN ('bullish','bearish') THEN ?
                       ELSE decision
                   END,
                   no_trade_reason=CASE WHEN no_trade_reason IS NULL OR no_trade_reason='' THEN ? ELSE no_trade_reason END,
                   neutral_reason=CASE WHEN neutral_reason IS NULL OR neutral_reason='' THEN ? ELSE neutral_reason END
                   WHERE id=?""",
                (
                    payload["target_version"],
                    payload["target_type"],
                    payload["residualization_mode"],
                    payload["market_beta"],
                    payload["horizon"],
                    payload["horizon_trading_days"],
                    payload["horizon_calendar_days"],
                    payload["benchmark_symbol"],
                    payload["up_threshold_pct"],
                    payload["down_threshold_pct"],
                    payload["neutral_band_pct"],
                    payload["expected_excess_return_pct"],
                    payload["expected_return_p10"],
                    payload["expected_return_p50"],
                    payload["expected_return_p90"],
                    payload["prob_up"],
                    payload["prob_down"],
                    payload["prob_no_edge"],
                    payload["edge_score"],
                    payload["decision"],
                    payload["decision"],
                    payload["no_trade_reason"],
                    payload["neutral_reason"],
                    row["id"],
                ),
            )

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _prediction_v2_payload_from_report(self, report, target: str, timeframe: str) -> dict:
        target_spec = getattr(report, "prediction_target", None)
        pt = target_spec.to_dict() if hasattr(target_spec, "to_dict") else dict(target_spec or {})
        payload = {
            "target_version": pt.get("target_version"),
            "target_type": pt.get("target_type"),
            "residualization_mode": pt.get("residualization_mode"),
            "market_beta": pt.get("market_beta"),
            "horizon": pt.get("horizon"),
            "horizon_trading_days": pt.get("horizon_trading_days"),
            "horizon_calendar_days": pt.get("horizon_calendar_days"),
            "benchmark_symbol": pt.get("benchmark_symbol"),
            "up_threshold_pct": pt.get("up_threshold_pct"),
            "down_threshold_pct": pt.get("down_threshold_pct"),
            "neutral_band_pct": pt.get("neutral_band_pct"),
            "expected_excess_return_pct": getattr(
                report, "expected_excess_return_pct", None,
            ),
            "expected_return_p10": getattr(report, "expected_return_p10", None),
            "expected_return_p50": getattr(report, "expected_return_p50", None),
            "expected_return_p90": getattr(report, "expected_return_p90", None),
            "prob_up": getattr(report, "prob_up", None),
            "prob_down": getattr(report, "prob_down", None),
            "prob_no_edge": getattr(report, "prob_no_edge", None),
            "edge_score": getattr(report, "edge_score", None),
            "decision": getattr(report, "decision", "") or "",
            "no_trade_reason": getattr(report, "no_trade_reason", "") or "",
            "neutral_reason": getattr(report, "neutral_reason", "") or "",
        }
        if payload["expected_excess_return_pct"] is None:
            payload["expected_excess_return_pct"] = pt.get("expected_return_pct")
        if payload["prob_no_edge"] is None:
            payload["prob_no_edge"] = pt.get("prob_neutral")
        return self._normalize_prediction_v2_payload(
            payload,
            target=target,
            timeframe=timeframe,
            direction=getattr(getattr(report, "direction", None), "value", getattr(report, "direction", "neutral")),
            min_pct=getattr(getattr(report, "magnitude", None), "min_pct", None),
            max_pct=getattr(getattr(report, "magnitude", None), "max_pct", None),
            confidence=getattr(report, "confidence", 0.0),
            prediction_target=pt,
        )

    def _prediction_v2_payload_from_row(self, row: dict) -> dict:
        report = self._loads_json(row.get("report_json"), {})
        pt = report.get("prediction_target") or {}
        report_version = pt.get("target_version")
        is_legacy = not report_version
        target_type = pt.get("target_type") or (
            "legacy_direction" if is_legacy else row.get("target_type")
        )
        payload = {
            "target_version": report_version or "legacy-v2",
            "target_type": target_type,
            "residualization_mode": (
                pt.get("residualization_mode")
                or (
                    "market_difference_legacy"
                    if is_legacy and target_type == "excess_return"
                    else "none_legacy" if is_legacy else row.get("residualization_mode")
                )
            ),
            "market_beta": pt.get("market_beta") if not is_legacy else None,
            "horizon": pt.get("horizon"),
            "horizon_trading_days": pt.get("horizon_trading_days"),
            "horizon_calendar_days": pt.get("horizon_calendar_days"),
            "benchmark_symbol": pt.get("benchmark_symbol"),
            "up_threshold_pct": pt.get("up_threshold_pct"),
            "down_threshold_pct": pt.get("down_threshold_pct"),
            "neutral_band_pct": pt.get("neutral_band_pct"),
            "expected_excess_return_pct": (
                row.get("expected_excess_return_pct")
                if row.get("expected_excess_return_pct") is not None
                else report.get("expected_excess_return_pct", pt.get("expected_return_pct"))
            ),
            "expected_return_p10": row.get("expected_return_p10") if row.get("expected_return_p10") is not None else report.get("expected_return_p10", pt.get("expected_return_p10")),
            "expected_return_p50": row.get("expected_return_p50") if row.get("expected_return_p50") is not None else report.get("expected_return_p50", pt.get("expected_return_p50")),
            "expected_return_p90": row.get("expected_return_p90") if row.get("expected_return_p90") is not None else report.get("expected_return_p90", pt.get("expected_return_p90")),
            "prob_up": row.get("prob_up") if row.get("prob_up") is not None else report.get("prob_up", pt.get("prob_up")),
            "prob_down": row.get("prob_down") if row.get("prob_down") is not None else report.get("prob_down", pt.get("prob_down")),
            "prob_no_edge": (
                row.get("prob_no_edge")
                if row.get("prob_no_edge") is not None
                else report.get("prob_no_edge", pt.get("prob_neutral"))
            ),
            "edge_score": row.get("edge_score") if row.get("edge_score") is not None else report.get("edge_score"),
            "decision": row.get("decision") or report.get("decision", ""),
            "no_trade_reason": report.get("no_trade_reason", ""),
            "neutral_reason": report.get("neutral_reason", ""),
        }
        return self._normalize_prediction_v2_payload(
            payload,
            target=row.get("target"),
            timeframe=row.get("timeframe"),
            direction=row.get("direction"),
            min_pct=row.get("min_pct"),
            max_pct=row.get("max_pct"),
            confidence=row.get("confidence"),
            prediction_target=pt,
        )

    def _normalize_prediction_v2_payload(
        self,
        payload: dict,
        *,
        target: Optional[str],
        timeframe: Optional[str],
        direction: Optional[str],
        min_pct: Optional[float],
        max_pct: Optional[float],
        confidence: Optional[float],
        prediction_target: Optional[dict],
    ) -> dict:
        from src.core.prediction_target import resolve_prediction_target

        magnitude = None
        if min_pct is not None and max_pct is not None:
            magnitude = {"min_pct": min_pct, "max_pct": max_pct}
        spec = resolve_prediction_target(
            timeframe or "",
            direction or "neutral",
            magnitude,
            float(confidence or 0.0),
            prediction_target,
            target=target,
        )
        spec_dict = spec.to_dict()
        for key in (
            "target_version",
            "target_type",
            "residualization_mode",
            "market_beta",
            "horizon",
            "horizon_trading_days",
            "horizon_calendar_days",
            "benchmark_symbol",
            "up_threshold_pct",
            "down_threshold_pct",
            "neutral_band_pct",
        ):
            if payload.get(key) is None:
                payload[key] = spec_dict.get(key)
        if payload.get("expected_excess_return_pct") is None:
            payload["expected_excess_return_pct"] = spec.expected_return_pct
        for key in ("expected_return_p10", "expected_return_p50", "expected_return_p90"):
            if payload.get(key) is None:
                payload[key] = getattr(spec, key)
        if payload.get("prob_up") is None:
            payload["prob_up"] = spec.prob_up
        if payload.get("prob_down") is None:
            payload["prob_down"] = spec.prob_down
        if payload.get("prob_no_edge") is None:
            payload["prob_no_edge"] = spec.prob_neutral

        for key in (
            "up_threshold_pct",
            "down_threshold_pct",
            "neutral_band_pct",
            "expected_excess_return_pct",
            "expected_return_p10",
            "expected_return_p50",
            "expected_return_p90",
            "market_beta",
            "prob_up",
            "prob_down",
            "prob_no_edge",
            "edge_score",
        ):
            payload[key] = self._safe_float(payload.get(key), None)
        if payload["edge_score"] is None:
            threshold = max(
                abs(float(payload.get("up_threshold_pct") or 0.0)),
                abs(float(payload.get("down_threshold_pct") or 0.0)),
                1.0,
            )
            expected = abs(float(payload.get("expected_excess_return_pct") or 0.0))
            directional_edge = max(
                float(payload.get("prob_up") or 0.0),
                float(payload.get("prob_down") or 0.0),
            )
            payload["edge_score"] = round(min(1.0, (expected / threshold) * directional_edge), 4)
        payload["decision"] = self._infer_decision_v2(
            payload,
            direction=direction,
        )
        payload["no_trade_reason"] = str(payload.get("no_trade_reason") or "")
        payload["neutral_reason"] = str(payload.get("neutral_reason") or "")
        return payload

    @staticmethod
    def _infer_decision_v2(payload: dict, direction: Optional[str]) -> str:
        decision = str(payload.get("decision") or "").strip()
        reasons_present = bool(payload.get("no_trade_reason") or payload.get("neutral_reason"))
        if decision and not (
            decision == "observe"
            and not reasons_present
            and str(direction or "").lower() in {"bullish", "bearish"}
        ):
            return decision
        direction_value = str(direction or "neutral").lower()
        edge = float(payload.get("edge_score") or 0.0)
        no_edge = float(payload.get("prob_no_edge") or 0.0)
        if no_edge >= 0.55 or edge < 0.12:
            return "observe"
        if edge < 0.22:
            return "watchlist"
        if direction_value == "bullish":
            return "long_bias"
        if direction_value == "bearish":
            return "short_bias"
        return "observe"

    # ================================================================
    # 保存预测
    # ================================================================

    def save_prediction(
        self,
        target: str,
        timeframe: str,
        report,       # FinalReport
        agent_results: list,  # list[AnalysisResult]
        agents_used: list[str],
        agents_failed: list[str],
        elapsed_seconds: float,
        llm_model: str,
        target_name: str = "",
    ) -> str:
        """保存一次预测及其所有 Agent 结果

        Returns:
            prediction_id
        """
        pid = str(uuid.uuid4())[:8]

        # 计算有效期
        now = datetime.now()
        target_spec = getattr(report, "prediction_target", None)
        if target_spec is not None:
            valid_until = now + timedelta(days=int(target_spec.horizon_calendar_days))
        elif "周" in timeframe:
            valid_until = now + timedelta(days=7)
        elif "月" in timeframe:
            valid_until = now + timedelta(days=30)
        else:
            valid_until = now + timedelta(days=90)

        mag = report.magnitude
        v2 = self._prediction_v2_payload_from_report(report, target, timeframe)

        with self._conn() as conn:
            conn.execute(
                """INSERT INTO predictions 
                   (id, target, target_name, timeframe, direction, min_pct, max_pct,
                    confidence,
                    target_version, target_type, residualization_mode, market_beta,
                    horizon, horizon_trading_days, horizon_calendar_days,
                    benchmark_symbol, up_threshold_pct, down_threshold_pct,
                    neutral_band_pct, expected_excess_return_pct,
                    expected_return_p10, expected_return_p50, expected_return_p90,
                    prob_up, prob_down,
                    prob_no_edge, edge_score, decision, no_trade_reason, neutral_reason,
                    predicted_at, valid_until,
                    agents_used, agents_failed, elapsed_seconds, llm_model,
                    summary, report_json, report_md)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    pid, target, target_name, timeframe,
                    report.direction.value,
                    mag.min_pct if mag else None,
                    mag.max_pct if mag else None,
                    report.confidence,
                    v2["target_version"],
                    v2["target_type"],
                    v2["residualization_mode"],
                    v2["market_beta"],
                    v2["horizon"],
                    v2["horizon_trading_days"],
                    v2["horizon_calendar_days"],
                    v2["benchmark_symbol"],
                    v2["up_threshold_pct"],
                    v2["down_threshold_pct"],
                    v2["neutral_band_pct"],
                    v2["expected_excess_return_pct"],
                    v2["expected_return_p10"],
                    v2["expected_return_p50"],
                    v2["expected_return_p90"],
                    v2["prob_up"],
                    v2["prob_down"],
                    v2["prob_no_edge"],
                    v2["edge_score"],
                    v2["decision"],
                    v2["no_trade_reason"],
                    v2["neutral_reason"],
                    now.isoformat(), valid_until.isoformat(),
                    json.dumps(agents_used, ensure_ascii=False),
                    json.dumps(agents_failed, ensure_ascii=False) if agents_failed else None,
                    elapsed_seconds, llm_model,
                    report.summary,
                    report.to_json(),
                    report.to_markdown(),
                ),
            )

            # Agent 单独结果
            for r in agent_results:
                mag_r = r.magnitude
                summary = dict(r.data_summary or {})
                if getattr(r, "prediction_target", None):
                    summary["prediction_target"] = r.prediction_target.to_dict()
                conn.execute(
                    """INSERT INTO agent_results
                       (prediction_id, agent_name, direction, min_pct, max_pct,
                        confidence, reasoning, key_factors, risks, data_summary)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        pid, r.agent_name, r.direction.value,
                        mag_r.min_pct if mag_r else None,
                        mag_r.max_pct if mag_r else None,
                        r.confidence, r.reasoning,
                        json.dumps(r.key_factors, ensure_ascii=False),
                        json.dumps(r.risks, ensure_ascii=False),
                        json.dumps(summary, ensure_ascii=False),
                    ),
                )

            conn.commit()

        logger.info(f"预测已保存: {pid} | {target} | {timeframe} | "
                     f"方向={report.direction.value} | 置信度={report.confidence:.0%}")
        return pid

    # ================================================================
    # 事后验证
    # ================================================================

    def verify_predictions(self, target: str) -> int:
        """验证指定标的所有过期未验证的预测

        Returns:
            验证的预测数量
        """
        now = datetime.now()

        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM predictions 
                   WHERE target=? AND verified_at IS NULL AND valid_until < ?""",
                (target, now.isoformat()),
            ).fetchall()

        verified = 0
        for row in rows:
            try:
                if self._verify_one(dict(row)):
                    verified += 1
            except Exception as e:
                logger.warning(f"验证预测 {row['id']} 失败: {e}")

        if verified:
            self._refresh_stats()
        return verified

    def verify_all(self) -> dict:
        """验证所有标的的过期预测"""
        with self._conn() as conn:
            targets = conn.execute(
                "SELECT DISTINCT target FROM predictions WHERE verified_at IS NULL"
            ).fetchall()

        total = 0
        for (t,) in targets:
            total += self.verify_predictions(t)

        return {"verified": total}

    def get_verification_queue_status(self) -> dict:
        """Return a compact status view for automatic evidence maintenance."""
        now = datetime.now().isoformat()
        with self._conn() as conn:
            row = conn.execute(
                """SELECT
                       COUNT(*) AS total,
                       SUM(CASE WHEN verified_at IS NOT NULL THEN 1 ELSE 0 END) AS verified,
                       SUM(CASE WHEN verified_at IS NULL THEN 1 ELSE 0 END) AS pending,
                       SUM(CASE WHEN verified_at IS NULL AND valid_until < ? THEN 1 ELSE 0 END) AS overdue,
                       MAX(predicted_at) AS latest_prediction_at,
                       MAX(verified_at) AS latest_verified_at
                   FROM predictions""",
                (now,),
            ).fetchone()
        payload = dict(row or {})
        for key in ("total", "verified", "pending", "overdue"):
            payload[key] = int(payload.get(key) or 0)
        return payload

    def get_recent_targets(self, limit: int = 50) -> list[str]:
        """Return distinct recently predicted targets in deterministic recency order."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT target, MAX(predicted_at) AS latest
                   FROM predictions
                   WHERE target IS NOT NULL AND target != ''
                   GROUP BY target
                   ORDER BY latest DESC
                   LIMIT ?""",
                (max(1, int(limit)),),
            ).fetchall()
        return [str(row["target"]) for row in rows]

    def _verify_one(self, record: dict) -> bool:
        """验证单条预测"""
        from src.data.price_fetcher import PriceFetcher
        from src.core.prediction_target import direction_correct, direction_from_return
        from src.core.return_residualizer import estimate_market_beta
        import asyncio

        target = record["target"]
        predicted_at = datetime.fromisoformat(record["predicted_at"])
        valid_until = datetime.fromisoformat(record["valid_until"])
        target_spec = self._target_spec_from_record(record)

        # 获取预测时价格和验证窗口内所有收盘价
        async def get_prices():
            fetcher = PriceFetcher()
            price_at_predict = await fetcher.fetch_close_near(
                target,
                predicted_at,
                prefer="on_or_before",
                tolerance_days=10,
            )
            if target_spec.evaluation_mode == "fixed_horizon":
                future_closes = await fetcher.fetch_trading_horizon(
                    target,
                    predicted_at,
                    target_spec.horizon_trading_days,
                    target_spec.horizon_calendar_days + 10,
                )
                closes = future_closes
            else:
                closes = await fetcher.fetch_close_window(
                    target,
                    predicted_at + timedelta(days=1),
                    valid_until,
                )
            benchmark = None
            market_beta = target_spec.market_beta
            if target_spec.target_type in {"excess_return", "residual_return"} and target_spec.benchmark_symbol:
                try:
                    if target_spec.evaluation_mode == "fixed_horizon":
                        benchmark_future = await fetcher.fetch_trading_horizon(
                            target_spec.benchmark_symbol,
                            predicted_at,
                            target_spec.horizon_trading_days,
                            target_spec.horizon_calendar_days + 10,
                        )
                        benchmark_start = await fetcher.fetch_close_near(
                            target_spec.benchmark_symbol,
                            predicted_at,
                            prefer="on_or_before",
                            tolerance_days=10,
                        )
                        benchmark_closes = benchmark_future
                    else:
                        benchmark_start = await fetcher.fetch_close_near(
                            target_spec.benchmark_symbol,
                            predicted_at,
                            prefer="on_or_before",
                            tolerance_days=10,
                        )
                        benchmark_closes = await fetcher.fetch_close_window(
                            target_spec.benchmark_symbol,
                            predicted_at + timedelta(days=1),
                            valid_until,
                        )
                    benchmark = (benchmark_start, benchmark_closes)
                    if target_spec.target_type == "residual_return" and market_beta is None:
                        history_start = predicted_at - timedelta(
                            days=max(60, int(target_spec.beta_lookback_days) * 2)
                        )
                        asset_history = await fetcher.fetch_close_window(
                            target, history_start, predicted_at,
                        )
                        benchmark_history = await fetcher.fetch_close_window(
                            target_spec.benchmark_symbol, history_start, predicted_at,
                        )
                        market_beta = estimate_market_beta(
                            asset_history,
                            benchmark_history,
                            min_observations=target_spec.beta_min_observations,
                        )
                except Exception as e:
                    logger.debug(
                        "基准收益获取失败，回退绝对收益: prediction=%s benchmark=%s error=%s",
                        record.get("id"),
                        target_spec.benchmark_symbol,
                        e,
                    )

            return price_at_predict, closes, benchmark, market_beta

        try:
            price_before, closes, benchmark, market_beta = asyncio.run(get_prices())
        except Exception as e:
            logger.warning(f"价格获取失败: {e}")
            return False

        if price_before is None or closes is None or len(closes) == 0 or price_before == 0:
            return False

        target_changes = (closes / price_before - 1) * 100
        effective_changes = target_changes
        benchmark_final_return = None
        target_type_used = "absolute_return"
        if benchmark:
            benchmark_start, benchmark_closes = benchmark
            benchmark_changes = (benchmark_closes / benchmark_start - 1) * 100
            benchmark_aligned = benchmark_changes.reindex(
                target_changes.index,
                method="ffill",
            ).bfill()
            if not benchmark_aligned.isna().any():
                if target_spec.target_type == "residual_return" and market_beta is not None:
                    effective_changes = target_changes - float(market_beta) * benchmark_aligned
                    target_type_used = "residual_return"
                else:
                    effective_changes = target_changes - benchmark_aligned
                    target_type_used = "excess_return"
                benchmark_final_return = float(benchmark_aligned.iloc[-1])

        actual_change = float(effective_changes.iloc[-1])
        actual_absolute_change = float(target_changes.iloc[-1])
        window_max = float(effective_changes.max())
        window_min = float(effective_changes.min())

        # 方向正确？
        pred_dir = record["direction"]
        dir_correct = direction_correct(
            pred_dir,
            actual_change,
            window_max,
            window_min,
            target_spec,
        )

        # 幅度命中？
        min_p = record.get("min_pct")
        max_p = record.get("max_pct")
        if min_p is not None and max_p is not None:
            mag_hit = min_p <= actual_change <= max_p
        else:
            mag_hit = None

        if target_spec.evaluation_mode == "fixed_horizon":
            actual_dir = direction_from_return(actual_change, target_spec)
        else:
            upper_hits = effective_changes[effective_changes >= target_spec.up_threshold_pct]
            lower_hits = effective_changes[effective_changes <= target_spec.down_threshold_pct]
            if not upper_hits.empty and not lower_hits.empty:
                actual_dir = "bullish" if upper_hits.index[0] <= lower_hits.index[0] else "bearish"
            elif not upper_hits.empty:
                actual_dir = "bullish"
            elif not lower_hits.empty:
                actual_dir = "bearish"
            else:
                actual_dir = direction_from_return(actual_change, target_spec)

        brier = self._prediction_brier_score(record, actual_dir)
        edge_hit = int(bool(dir_correct))

        with self._conn() as conn:
            conn.execute(
                """UPDATE predictions SET
                   actual_direction=?, actual_change_pct=?,
                   actual_effective_return_pct=?, actual_absolute_return_pct=?,
                   actual_benchmark_return_pct=?,
                   window_max_effective_return_pct=?,
                   window_min_effective_return_pct=?,
                   target_type_used=?,
                   market_beta=?,
                   brier_score=?, edge_hit=?,
                   direction_correct=?,
                   magnitude_hit=?, verified_at=?
                   WHERE id=?""",
                (
                    actual_dir,
                    round(actual_change, 2),
                    round(actual_change, 2),
                    round(actual_absolute_change, 2),
                    round(benchmark_final_return, 2) if benchmark_final_return is not None else None,
                    round(window_max, 2),
                    round(window_min, 2),
                    target_type_used,
                    market_beta,
                    brier,
                    edge_hit,
                    int(dir_correct),
                    int(mag_hit) if mag_hit is not None else None,
                    datetime.now().isoformat(),
                    record["id"],
                ),
            )
            conn.commit()

        try:
            from src.data.quant_feature_store import QuantFeatureStore

            if target_spec.target_type != "residual_return" or target_type_used == "residual_return":
                QuantFeatureStore().update_label_by_prediction(
                    record["id"],
                    direction=actual_dir,
                    return_pct=actual_change,
                    absolute_return_pct=actual_absolute_change,
                    benchmark_return_pct=benchmark_final_return,
                    market_beta=market_beta,
                    market_residual_pct=(
                        actual_change if target_type_used == "residual_return" else None
                    ),
                    threshold_pct=target_spec.up_threshold_pct,
                    valid_date=closes.index[-1].date().isoformat(),
                )
        except Exception as exc:
            logger.debug("量化特征标签回写跳过: prediction=%s error=%s", record["id"], exc)

        logger.info(f"验证 {record['id']}: 预测={pred_dir} {min_p}~{max_p}%, "
                     f"实际={actual_dir} {actual_change:+.1f}%({target_type_used}), "
                     f"方向={'✓' if dir_correct else '✗'}, "
                     f"幅度={'✓' if mag_hit else '✗'}")

        self._update_agent_calibration_from_verification(
            record["id"], actual_dir, actual_change,
        )
        return True

    @staticmethod
    def _target_spec_from_record(record: dict):
        """从历史预测记录中恢复当时使用的预测目标规格。"""
        from src.core.prediction_target import PredictionTargetSpec, default_target_spec

        payload = {}
        try:
            report = json.loads(record.get("report_json") or "{}")
            payload = report.get("prediction_target") or {}
        except (TypeError, json.JSONDecodeError):
            payload = {}
        for source_key, target_key in {
            "target_version": "target_version",
            "target_type": "target_type",
            "residualization_mode": "residualization_mode",
            "market_beta": "market_beta",
            "horizon": "horizon",
            "horizon_trading_days": "horizon_trading_days",
            "horizon_calendar_days": "horizon_calendar_days",
            "benchmark_symbol": "benchmark_symbol",
            "up_threshold_pct": "up_threshold_pct",
            "down_threshold_pct": "down_threshold_pct",
            "neutral_band_pct": "neutral_band_pct",
            "expected_excess_return_pct": "expected_return_pct",
            "expected_return_p10": "expected_return_p10",
            "expected_return_p50": "expected_return_p50",
            "expected_return_p90": "expected_return_p90",
            "prob_up": "prob_up",
            "prob_down": "prob_down",
            "prob_no_edge": "prob_neutral",
            "direction": "direction",
        }.items():
            value = record.get(source_key)
            if value not in (None, ""):
                payload[target_key] = value

        if payload:
            return PredictionTargetSpec.from_dict(payload)
        return default_target_spec(record.get("timeframe") or "", target=record.get("target"))

    def _prediction_probabilities(self, record: dict) -> dict[str, float]:
        payload = self._prediction_v2_payload_from_row(record)
        up = self._safe_float(payload.get("prob_up"), None)
        down = self._safe_float(payload.get("prob_down"), None)
        no_edge = self._safe_float(payload.get("prob_no_edge"), None)
        if up is None or down is None or no_edge is None:
            payload = self._normalize_prediction_v2_payload(
                {},
                target=record.get("target"),
                timeframe=record.get("timeframe"),
                direction=record.get("direction"),
                min_pct=record.get("min_pct"),
                max_pct=record.get("max_pct"),
                confidence=record.get("confidence"),
                prediction_target=None,
            )
            up = payload["prob_up"]
            down = payload["prob_down"]
            no_edge = payload["prob_no_edge"]
        total = max(float(up or 0.0) + float(down or 0.0) + float(no_edge or 0.0), 1e-9)
        return {
            "bullish": float(up or 0.0) / total,
            "bearish": float(down or 0.0) / total,
            "neutral": float(no_edge or 0.0) / total,
        }

    def _prediction_brier_score(self, record: dict, actual_direction: str) -> float:
        probs = self._prediction_probabilities(record)
        actual = str(actual_direction or "neutral")
        score = sum(
            (probs[label] - (1.0 if actual == label else 0.0)) ** 2
            for label in ("bullish", "bearish", "neutral")
        ) / 3.0
        return round(score, 4)

    @staticmethod
    def _get_price_near_date(price_data, target_date: datetime) -> Optional[float]:
        """兼容旧调用：PriceData 不包含日期索引，只能返回当前快照价。"""
        import pandas as pd
        closes = price_data.recent_closes
        if closes:
            return closes[-1]
        return price_data.price_current

    # ================================================================
    # 统计查询
    # ================================================================

    def get_accuracy_stats(
        self,
        agent_name: Optional[str] = None,
        timeframe: Optional[str] = None,
    ) -> dict:
        """获取准确率统计"""
        timeframe_filter = None
        if timeframe:
            timeframe_filter = timeframe if "(" in timeframe else f"{timeframe}%"

        with self._conn() as conn:
            if agent_name is None:
                # 综合统计
                row = conn.execute(
                    """SELECT COUNT(*) as total,
                              AVG(CASE WHEN direction_correct=1 THEN 1.0 ELSE 0.0 END) as dir_acc,
                              AVG(CASE WHEN magnitude_hit=1 THEN 1.0 ELSE 0.0 END) as mag_acc,
                              AVG(confidence) as avg_conf,
                              AVG(ABS(COALESCE(actual_effective_return_pct, actual_change_pct) -
                                  COALESCE(expected_excess_return_pct, (min_pct+max_pct)/2.0))) as avg_err,
                              AVG(brier_score) as brier,
                              AVG(CASE WHEN edge_hit IS NOT NULL THEN edge_hit * 1.0 ELSE NULL END) as edge_hit_rate,
                              AVG(edge_score) as avg_edge,
                              AVG(CASE WHEN decision IN ('long_bias','short_bias','watchlist') THEN 1.0 ELSE 0.0 END) as actionable,
                              AVG(actual_effective_return_pct) as avg_effective,
                              AVG(expected_excess_return_pct) as avg_expected
                       FROM predictions WHERE verified_at IS NOT NULL
                       AND (? IS NULL OR timeframe LIKE ?)""",
                    (timeframe_filter, timeframe_filter),
                ).fetchone()
            else:
                # 单独 Agent
                row = conn.execute(
                    """SELECT COUNT(*) as total,
                              AVG(CASE WHEN ar.direction = p.actual_direction THEN 1.0 ELSE 0.0 END) as dir_acc,
                              AVG(CASE WHEN ar.min_pct <= p.actual_change_pct AND ar.max_pct >= p.actual_change_pct THEN 1.0 ELSE 0.0 END) as mag_acc,
                              AVG(ar.confidence) as avg_conf,
                              AVG(ABS(COALESCE(p.actual_effective_return_pct, p.actual_change_pct) -
                                  (ar.min_pct+ar.max_pct)/2.0)) as avg_err,
                              AVG(p.brier_score) as brier,
                              AVG(CASE WHEN p.edge_hit IS NOT NULL THEN p.edge_hit * 1.0 ELSE NULL END) as edge_hit_rate,
                              AVG(p.edge_score) as avg_edge,
                              AVG(CASE WHEN p.decision IN ('long_bias','short_bias','watchlist') THEN 1.0 ELSE 0.0 END) as actionable,
                              AVG(p.actual_effective_return_pct) as avg_effective,
                              AVG(p.expected_excess_return_pct) as avg_expected
                       FROM agent_results ar
                       JOIN predictions p ON ar.prediction_id = p.id
                       WHERE p.verified_at IS NOT NULL AND ar.agent_name=?
                       AND (? IS NULL OR p.timeframe LIKE ?)""",
                    (agent_name, timeframe_filter, timeframe_filter),
                ).fetchone()

            if row and row["total"] > 0:
                return {
                    "total": row["total"],
                    "direction_accuracy": round(row["dir_acc"], 3),
                    "magnitude_accuracy": round(row["mag_acc"], 3) if row["mag_acc"] is not None else 0,
                    "avg_confidence": round(row["avg_conf"], 3),
                    "avg_error_pct": round(row["avg_err"], 2) if row["avg_err"] is not None else 0,
                    "brier_score": round(row["brier"], 4) if row["brier"] is not None else 0,
                    "edge_hit_rate": round(row["edge_hit_rate"], 3) if row["edge_hit_rate"] is not None else 0,
                    "avg_edge_score": round(row["avg_edge"], 3) if row["avg_edge"] is not None else 0,
                    "actionable_coverage": round(row["actionable"], 3) if row["actionable"] is not None else 0,
                    "avg_actual_effective_return_pct": round(row["avg_effective"], 2) if row["avg_effective"] is not None else 0,
                    "avg_expected_excess_return_pct": round(row["avg_expected"], 2) if row["avg_expected"] is not None else 0,
                }

            return {"total": 0, "direction_accuracy": 0, "magnitude_accuracy": 0,
                    "avg_confidence": 0, "avg_error_pct": 0,
                    "brier_score": 0, "edge_hit_rate": 0, "avg_edge_score": 0,
                    "actionable_coverage": 0, "avg_actual_effective_return_pct": 0,
                    "avg_expected_excess_return_pct": 0}

    def get_stats_by_timeframe(self) -> dict:
        """按时间维度分组的统计"""
        result = {}
        for tf in ["short", "medium", "long"]:
            label = {"short": "短期", "medium": "中期", "long": "长期"}[tf]
            result[label] = self.get_accuracy_stats(timeframe=label)
        return result

    def get_stats_by_agent(self, timeframe: Optional[str] = None) -> dict:
        """各 Agent 的统计"""
        with self._conn() as conn:
            agents = conn.execute(
                "SELECT DISTINCT agent_name FROM agent_results"
            ).fetchall()

        result = {}
        for (name,) in agents:
            result[name] = self.get_accuracy_stats(agent_name=name, timeframe=timeframe)

        # 综合
        result["综合汇总"] = self.get_accuracy_stats(timeframe=timeframe)
        return result

    # ================================================================
    # 查询
    # ================================================================

    def get_prediction(self, pid: str) -> Optional[PredictionRecord]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM predictions WHERE id=?", (pid,)).fetchone()
            if row:
                return self._row_to_record(dict(row))
        return None

    def get_predictions(
        self,
        target: Optional[str] = None,
        timeframe: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        prediction_ids: Optional[list[str]] = None,
        verified_only: bool = False,
        verified: Optional[bool] = None,
        limit: int = 100,
    ) -> list[PredictionRecord]:
        if prediction_ids is not None and not prediction_ids:
            return []

        query = "SELECT * FROM predictions WHERE 1=1"
        params = []
        if target:
            query += " AND target=?"
            params.append(target)
        if timeframe:
            query += " AND timeframe=?"
            params.append(timeframe)
        if start_date:
            query += " AND predicted_at>=?"
            params.append(start_date)
        if end_date:
            query += " AND predicted_at<=?"
            params.append(end_date)
        if prediction_ids is not None:
            placeholders = ",".join("?" for _ in prediction_ids)
            query += f" AND id IN ({placeholders})"
            params.extend(prediction_ids)
        if verified_only:
            query += " AND verified_at IS NOT NULL"
        if verified is True:
            query += " AND verified_at IS NOT NULL"
        elif verified is False:
            query += " AND verified_at IS NULL"
        query += " ORDER BY predicted_at DESC LIMIT ?"
        params.append(limit)

        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_record(dict(r)) for r in rows]

    def get_prediction_summaries(
        self,
        target: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """轻量历史列表，不读取和解析完整报告正文。"""
        query = """
            SELECT id, target, target_name, timeframe, direction, confidence,
                   predicted_at, verified_at,
                   target_version, target_type, residualization_mode, market_beta,
                   expected_excess_return_pct, prob_up, prob_down, prob_no_edge,
                   expected_return_p10, expected_return_p50, expected_return_p90,
                   edge_score, decision, no_trade_reason, neutral_reason,
                   actual_effective_return_pct, actual_absolute_return_pct,
                   actual_benchmark_return_pct, target_type_used,
                   brier_score, edge_hit
            FROM predictions
            WHERE 1=1
        """
        params: list[Any] = []
        if target:
            query += " AND target=?"
            params.append(target)
        query += " ORDER BY predicted_at DESC LIMIT ?"
        params.append(max(1, int(limit or 100)))

        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_unverified_count(self) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM predictions WHERE verified_at IS NULL"
            ).fetchone()
            return row[0] if row else 0

    # ================================================================
    # 内部
    # ================================================================

    def _row_to_record(self, r: dict) -> PredictionRecord:
        return PredictionRecord(
            id=r["id"], target=r["target"], target_name=r.get("target_name"),
            timeframe=r["timeframe"], direction=r["direction"],
            min_pct=r.get("min_pct"), max_pct=r.get("max_pct"),
            confidence=r["confidence"], predicted_at=r["predicted_at"],
            target_type=r.get("target_type") or "",
            target_version=r.get("target_version") or "",
            residualization_mode=r.get("residualization_mode") or "",
            market_beta=r.get("market_beta"),
            horizon=r.get("horizon") or "",
            horizon_trading_days=r.get("horizon_trading_days"),
            horizon_calendar_days=r.get("horizon_calendar_days"),
            benchmark_symbol=r.get("benchmark_symbol"),
            up_threshold_pct=r.get("up_threshold_pct"),
            down_threshold_pct=r.get("down_threshold_pct"),
            neutral_band_pct=r.get("neutral_band_pct"),
            expected_excess_return_pct=r.get("expected_excess_return_pct"),
            expected_return_p10=r.get("expected_return_p10"),
            expected_return_p50=r.get("expected_return_p50"),
            expected_return_p90=r.get("expected_return_p90"),
            prob_up=r.get("prob_up"),
            prob_down=r.get("prob_down"),
            prob_no_edge=r.get("prob_no_edge"),
            edge_score=r.get("edge_score"),
            decision=r.get("decision") or "",
            no_trade_reason=r.get("no_trade_reason") or "",
            neutral_reason=r.get("neutral_reason") or "",
            valid_until=r["valid_until"],
            actual_direction=r.get("actual_direction"),
            actual_change_pct=r.get("actual_change_pct"),
            actual_effective_return_pct=r.get("actual_effective_return_pct"),
            actual_absolute_return_pct=r.get("actual_absolute_return_pct"),
            actual_benchmark_return_pct=r.get("actual_benchmark_return_pct"),
            window_max_effective_return_pct=r.get("window_max_effective_return_pct"),
            window_min_effective_return_pct=r.get("window_min_effective_return_pct"),
            target_type_used=r.get("target_type_used") or "",
            brier_score=r.get("brier_score"),
            edge_hit=r.get("edge_hit"),
            direction_correct=r.get("direction_correct"),
            magnitude_hit=r.get("magnitude_hit"),
            verified_at=r.get("verified_at"),
            agents_used=json.loads(r.get("agents_used", "[]")),
            agents_failed=json.loads(r.get("agents_failed", "[]")) if r.get("agents_failed") else [],
            elapsed_seconds=r.get("elapsed_seconds", 0) or 0,
            llm_model=r.get("llm_model", ""),
            summary=r.get("summary", "") or "",
            report_json=r.get("report_json", "") or "",
            report_md=r.get("report_md", "") or "",
        )

    def _update_agent_calibration_from_verification(
        self,
        prediction_id: str,
        actual_direction: str,
        actual_return_pct: float,
    ) -> None:
        """把已验证预测反馈给支持历史校准的 Agent。"""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT ar.*, p.timeframe AS prediction_timeframe
                   FROM agent_results ar
                   LEFT JOIN predictions p ON p.id = ar.prediction_id
                   WHERE ar.prediction_id=?""",
                (prediction_id,),
            ).fetchall()

        for row in rows:
            agent_name = row["agent_name"]
            if agent_name == "近期股价分析师":
                self._update_technical_calibration(
                    dict(row), actual_direction,
                )
            elif agent_name == "公司前景分析师":
                self._update_fundamental_calibration(
                    dict(row), actual_direction, actual_return_pct,
                )
            elif agent_name == "行业对比分析师":
                self._update_industry_calibration(
                    dict(row), actual_direction,
                )
            elif agent_name == "国际形势分析师":
                self._update_macro_calibration(
                    dict(row), actual_direction,
                )
            elif agent_name == "最新新闻分析师":
                self._update_news_calibration(
                    dict(row), actual_direction,
                )

    def _update_technical_calibration(
        self,
        agent_row: dict,
        actual_direction: str,
    ) -> None:
        """把近期股价分析师的验证样本写入专用校准器。"""
        try:
            from src.utils.technical_calibrator import TechnicalConfidenceCalibrator

            data_summary = self._loads_json(agent_row.get("data_summary"), {})
            evidence = data_summary.get("evidence") or {}
            timeframe = (
                agent_row.get("prediction_timeframe")
                or data_summary.get("timeframe")
                or ""
            )
            buckets = TechnicalConfidenceCalibrator.extract_buckets_from_evidence(
                evidence,
                timeframe,
            )
            was_correct = agent_row.get("direction") == actual_direction
            calibrator = TechnicalConfidenceCalibrator()
            calibrator.update_from_validation(
                predicted_conf=float(agent_row.get("confidence") or 0.0),
                was_correct=was_correct,
                **buckets,
            )
            save = getattr(calibrator, "save", None)
            if callable(save):
                save()
            logger.info(
                "技术面校准样本已更新: prediction=%s correct=%s trend=%s volume=%s position=%s",
                agent_row.get("prediction_id"),
                was_correct,
                buckets.get("trend_bucket"),
                buckets.get("volume_bucket"),
                buckets.get("position_bucket"),
            )
        except Exception as e:
            logger.debug(f"技术面校准更新跳过: {e}")

    def _update_fundamental_calibration(
        self,
        agent_row: dict,
        actual_direction: str,
        actual_return_pct: float,
    ) -> None:
        """把公司前景分析师的验证样本写入专用校准器。"""
        try:
            from src.utils.fundamental_calibrator import FundamentalConfidenceCalibrator

            data_summary = self._loads_json(agent_row.get("data_summary"), {})
            quality = self._safe_float(
                (data_summary.get("data_quality") or {}).get("overall_quality"),
                self._safe_float(data_summary.get("quality"), 0.5),
            )
            if quality >= 0.7:
                data_quality_bucket = "high"
            elif quality >= 0.4:
                data_quality_bucket = "medium"
            else:
                data_quality_bucket = "low"

            scorecard_rating = (data_summary.get("quality_scorecard") or {}).get("rating")
            pe_percentile = self._safe_float(
                (data_summary.get("valuation_analysis") or {}).get("pe_percentile_3yr"),
                None,
            )
            was_correct = agent_row.get("direction") == actual_direction

            calibrator = FundamentalConfidenceCalibrator()
            calibrator.update_from_validation(
                predicted_conf=float(agent_row.get("confidence") or 0.0),
                was_correct=was_correct,
                data_quality_bucket=data_quality_bucket,
                scorecard_rating=scorecard_rating,
                pe_percentile=pe_percentile,
                actual_return_pct=actual_return_pct,
            )
            save = getattr(calibrator, "save", None)
            if callable(save):
                save()
            logger.info(
                "公司前景校准样本已更新: prediction=%s correct=%s quality=%s rating=%s",
                agent_row.get("prediction_id"),
                was_correct,
                data_quality_bucket,
                scorecard_rating or "unknown",
            )
        except Exception as e:
            logger.debug(f"公司前景校准更新跳过: {e}")

    def _update_industry_calibration(
        self,
        agent_row: dict,
        actual_direction: str,
    ) -> None:
        """把行业对比分析师的验证样本写入专用校准器。"""
        try:
            from src.utils.industry_calibrator import IndustryConfidenceCalibrator

            data_summary = self._loads_json(agent_row.get("data_summary"), {})
            data_quality = data_summary.get("data_quality") or {}
            has_constituents = bool(data_quality.get("has_constituents"))
            has_trend = bool(data_quality.get("has_trend"))
            overall = self._safe_float(data_quality.get("overall"), 0.0)

            if has_constituents and has_trend:
                data_quality_level = "constituents+trend"
            elif has_constituents:
                data_quality_level = "constituents_only"
            elif overall and overall > 0.1:
                data_quality_level = "reference_only"
            else:
                data_quality_level = "none"

            was_correct = agent_row.get("direction") == actual_direction
            calibrator = IndustryConfidenceCalibrator()
            calibrator.update_from_validation(
                predicted_conf=float(agent_row.get("confidence") or 0.0),
                was_correct=was_correct,
                industry=data_summary.get("industry"),
                data_quality_level=data_quality_level,
            )
            save = getattr(calibrator, "save", None)
            if callable(save):
                save()
            logger.info(
                "行业对比校准样本已更新: prediction=%s correct=%s industry=%s quality=%s",
                agent_row.get("prediction_id"),
                was_correct,
                data_summary.get("industry") or "unknown",
                data_quality_level,
            )
        except Exception as e:
            logger.debug(f"行业对比校准更新跳过: {e}")

    def _update_macro_calibration(
        self,
        agent_row: dict,
        actual_direction: str,
    ) -> None:
        """把国际形势分析师的验证样本写入专用校准器。"""
        try:
            from src.utils.macro_calibrator import MacroConfidenceCalibrator

            data_summary = self._loads_json(agent_row.get("data_summary"), {})
            data_quality = data_summary.get("data_quality") or {}
            freshness = self._parse_percent(
                data_quality.get("overall_freshness"), 0.5,
            )
            ref_count = int(self._safe_float(data_quality.get("reference_count"), 0) or 0)
            realtime_count = int(self._safe_float(data_quality.get("realtime_count"), 0) or 0)
            if freshness >= 0.70 and ref_count <= 1:
                data_quality_level = "fresh"
            elif freshness >= 0.45 and ref_count <= 2:
                data_quality_level = "mixed"
            elif ref_count >= 3:
                data_quality_level = "reference_heavy"
            elif realtime_count <= 2:
                data_quality_level = "sparse"
            else:
                data_quality_level = "stale"

            was_correct = agent_row.get("direction") == actual_direction
            calibrator = MacroConfidenceCalibrator()
            calibrator.update_from_validation(
                predicted_conf=float(agent_row.get("confidence") or 0.0),
                was_correct=was_correct,
                market=data_summary.get("market") or "",
                sector=data_summary.get("sector") or "",
                data_quality_level=data_quality_level,
            )
            save = getattr(calibrator, "save", None)
            if callable(save):
                save()
            logger.info(
                "宏观校准样本已更新: prediction=%s correct=%s market=%s sector=%s quality=%s",
                agent_row.get("prediction_id"),
                was_correct,
                data_summary.get("market") or "unknown",
                data_summary.get("sector") or "unknown",
                data_quality_level,
            )
        except Exception as e:
            logger.debug(f"宏观校准更新跳过: {e}")

    def _update_news_calibration(
        self,
        agent_row: dict,
        actual_direction: str,
    ) -> None:
        """把最新新闻分析师的验证样本写入专用校准器。"""
        try:
            from src.utils.news_calibrator import NewsConfidenceCalibrator

            data_summary = self._loads_json(agent_row.get("data_summary"), {})
            evidence = data_summary.get("evidence") or {}
            buckets = NewsConfidenceCalibrator.extract_buckets_from_evidence(evidence)
            was_correct = agent_row.get("direction") == actual_direction
            calibrator = NewsConfidenceCalibrator()
            calibrator.update_from_validation(
                predicted_conf=float(agent_row.get("confidence") or 0.0),
                was_correct=was_correct,
                **buckets,
            )
            save = getattr(calibrator, "save", None)
            if callable(save):
                save()
            logger.info(
                "新闻校准样本已更新: prediction=%s correct=%s count=%s source=%s event=%s",
                agent_row.get("prediction_id"),
                was_correct,
                buckets.get("news_count_bucket"),
                buckets.get("source_bucket"),
                buckets.get("event_bucket"),
            )
        except Exception as e:
            logger.debug(f"新闻校准更新跳过: {e}")

    @staticmethod
    def _loads_json(value, default):
        if not value:
            return default
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return default

    @staticmethod
    def _safe_float(value, default: Optional[float] = 0.0) -> Optional[float]:
        try:
            if value in (None, "", "N/A"):
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _parse_percent(value, default: float = 0.5) -> float:
        if value in (None, "", "N/A"):
            return default
        try:
            if isinstance(value, str):
                value = value.strip()
                if value.endswith("%"):
                    return float(value[:-1]) / 100
            number = float(value)
            return number / 100 if number > 1 else number
        except (TypeError, ValueError):
            return default

    def _refresh_stats(self):
        """刷新 accuracy_stats 表"""
        with self._conn() as conn:
            conn.execute("DELETE FROM accuracy_stats")
            for tf in ["短期", "中期", "长期"]:
                for agent_row in conn.execute("SELECT DISTINCT agent_name FROM agent_results"):
                    name = agent_row[0]
                    stats = self.get_accuracy_stats(agent_name=name, timeframe=tf)
                    conn.execute(
                        """INSERT OR REPLACE INTO accuracy_stats
                           (agent_name, timeframe, total_predictions, direction_accuracy,
                            magnitude_accuracy, avg_confidence, avg_error_pct,
                            brier_score, edge_hit_rate, avg_edge_score,
                            actionable_coverage, avg_actual_effective_return_pct,
                            avg_expected_excess_return_pct, last_updated)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (name, tf, stats["total"], stats["direction_accuracy"],
                         stats["magnitude_accuracy"], stats["avg_confidence"],
                         stats["avg_error_pct"], stats["brier_score"],
                         stats["edge_hit_rate"], stats["avg_edge_score"],
                         stats["actionable_coverage"],
                         stats["avg_actual_effective_return_pct"],
                         stats["avg_expected_excess_return_pct"],
                         datetime.now().isoformat()),
                    )
                # 综合
                stats = self.get_accuracy_stats(timeframe=tf)
                conn.execute(
                    """INSERT OR REPLACE INTO accuracy_stats
                       (agent_name, timeframe, total_predictions, direction_accuracy,
                        magnitude_accuracy, avg_confidence, avg_error_pct,
                        brier_score, edge_hit_rate, avg_edge_score,
                        actionable_coverage, avg_actual_effective_return_pct,
                        avg_expected_excess_return_pct, last_updated)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (None, tf, stats["total"], stats["direction_accuracy"],
                     stats["magnitude_accuracy"], stats["avg_confidence"],
                     stats["avg_error_pct"], stats["brier_score"],
                     stats["edge_hit_rate"], stats["avg_edge_score"],
                     stats["actionable_coverage"],
                     stats["avg_actual_effective_return_pct"],
                     stats["avg_expected_excess_return_pct"],
                     datetime.now().isoformat()),
                )
            conn.commit()
