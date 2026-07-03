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
from typing import Optional

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
    predicted_at: str = ""
    valid_until: str = ""
    actual_direction: Optional[str] = None
    actual_change_pct: Optional[float] = None
    direction_correct: Optional[int] = None
    magnitude_hit: Optional[int] = None
    verified_at: Optional[str] = None
    agents_used: list[str] = field(default_factory=list)
    agents_failed: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    llm_model: str = ""

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
            if schema_path.exists():
                conn.executescript(schema_path.read_text(encoding="utf-8"))
            conn.commit()
        logger.info(f"数据库就绪: {self.db_path}")

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

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
        if "周" in timeframe:
            valid_until = now + timedelta(days=7)
        elif "月" in timeframe:
            valid_until = now + timedelta(days=30)
        else:
            valid_until = now + timedelta(days=90)

        mag = report.magnitude

        with self._conn() as conn:
            conn.execute(
                """INSERT INTO predictions 
                   (id, target, target_name, timeframe, direction, min_pct, max_pct,
                    confidence, predicted_at, valid_until,
                    agents_used, agents_failed, elapsed_seconds, llm_model,
                    summary, report_json, report_md)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    pid, target, target_name, timeframe,
                    report.direction.value,
                    mag.min_pct if mag else None,
                    mag.max_pct if mag else None,
                    report.confidence,
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
                        json.dumps(r.data_summary, ensure_ascii=False),
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
                self._verify_one(dict(row))
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

    def _verify_one(self, record: dict):
        """验证单条预测"""
        from src.data.price_fetcher import PriceFetcher
        import asyncio

        target = record["target"]
        predicted_at = datetime.fromisoformat(record["predicted_at"])
        valid_until = datetime.fromisoformat(record["valid_until"])

        # 获取预测时和有效期结束时的价格
        async def get_prices():
            fetcher = PriceFetcher()
            # 预测时的价格
            data_before = await fetcher.fetch(target, "1mo")
            price_at_predict = self._get_price_near_date(data_before, predicted_at)

            # 有效期结束时的价格
            data_after = await fetcher.fetch(target, "1mo")
            price_at_valid = self._get_price_near_date(data_after, valid_until)

            return price_at_predict, price_at_valid

        try:
            price_before, price_after = asyncio.run(get_prices())
        except Exception as e:
            logger.warning(f"价格获取失败: {e}")
            return

        if price_before is None or price_after is None or price_before == 0:
            return

        actual_change = (price_after / price_before - 1) * 100

        # 方向正确？
        pred_dir = record["direction"]
        if pred_dir == "bullish":
            dir_correct = actual_change > 0.5
        elif pred_dir == "bearish":
            dir_correct = actual_change < -0.5
        else:
            dir_correct = abs(actual_change) <= 1.0

        # 幅度命中？
        min_p = record.get("min_pct")
        max_p = record.get("max_pct")
        if min_p is not None and max_p is not None:
            mag_hit = min_p <= actual_change <= max_p
        else:
            mag_hit = None

        actual_dir = "bullish" if actual_change > 0.5 else ("bearish" if actual_change < -0.5 else "neutral")

        with self._conn() as conn:
            conn.execute(
                """UPDATE predictions SET
                   actual_direction=?, actual_change_pct=?, direction_correct=?,
                   magnitude_hit=?, verified_at=?
                   WHERE id=?""",
                (actual_dir, round(actual_change, 2),
                 int(dir_correct), int(mag_hit) if mag_hit is not None else None,
                 datetime.now().isoformat(), record["id"]),
            )
            conn.commit()

        logger.info(f"验证 {record['id']}: 预测={pred_dir} {min_p}~{max_p}%, "
                     f"实际={actual_dir} {actual_change:+.1f}%, "
                     f"方向={'✓' if dir_correct else '✗'}, "
                     f"幅度={'✓' if mag_hit else '✗'}")

    @staticmethod
    def _get_price_near_date(price_data, target_date: datetime) -> Optional[float]:
        """从 PriceData 中找最接近 target_date 的收盘价"""
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
        with self._conn() as conn:
            if agent_name is None:
                # 综合统计
                row = conn.execute(
                    """SELECT COUNT(*) as total,
                              AVG(CASE WHEN direction_correct=1 THEN 1.0 ELSE 0.0 END) as dir_acc,
                              AVG(CASE WHEN magnitude_hit=1 THEN 1.0 ELSE 0.0 END) as mag_acc,
                              AVG(confidence) as avg_conf,
                              AVG(ABS(actual_change_pct - (min_pct+max_pct)/2.0)) as avg_err
                       FROM predictions WHERE verified_at IS NOT NULL
                       AND (? IS NULL OR timeframe=?)""",
                    (timeframe, timeframe),
                ).fetchone()
            else:
                # 单独 Agent
                row = conn.execute(
                    """SELECT COUNT(*) as total,
                              AVG(CASE WHEN ar.direction = p.actual_direction THEN 1.0 ELSE 0.0 END) as dir_acc,
                              AVG(CASE WHEN ar.min_pct <= p.actual_change_pct AND ar.max_pct >= p.actual_change_pct THEN 1.0 ELSE 0.0 END) as mag_acc,
                              AVG(ar.confidence) as avg_conf,
                              AVG(ABS(p.actual_change_pct - (ar.min_pct+ar.max_pct)/2.0)) as avg_err
                       FROM agent_results ar
                       JOIN predictions p ON ar.prediction_id = p.id
                       WHERE p.verified_at IS NOT NULL AND ar.agent_name=?
                       AND (? IS NULL OR p.timeframe=?)""",
                    (agent_name, timeframe, timeframe),
                ).fetchone()

            if row and row["total"] > 0:
                return {
                    "total": row["total"],
                    "direction_accuracy": round(row["dir_acc"], 3),
                    "magnitude_accuracy": round(row["mag_acc"], 3) if row["mag_acc"] is not None else 0,
                    "avg_confidence": round(row["avg_conf"], 3),
                    "avg_error_pct": round(row["avg_err"], 2) if row["avg_err"] is not None else 0,
                }

            return {"total": 0, "direction_accuracy": 0, "magnitude_accuracy": 0,
                    "avg_confidence": 0, "avg_error_pct": 0}

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
        verified_only: bool = False,
        limit: int = 100,
    ) -> list[PredictionRecord]:
        query = "SELECT * FROM predictions WHERE 1=1"
        params = []
        if target:
            query += " AND target=?"
            params.append(target)
        if verified_only:
            query += " AND verified_at IS NOT NULL"
        query += " ORDER BY predicted_at DESC LIMIT ?"
        params.append(limit)

        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_record(dict(r)) for r in rows]

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
            valid_until=r["valid_until"],
            actual_direction=r.get("actual_direction"),
            actual_change_pct=r.get("actual_change_pct"),
            direction_correct=r.get("direction_correct"),
            magnitude_hit=r.get("magnitude_hit"),
            verified_at=r.get("verified_at"),
            agents_used=json.loads(r.get("agents_used", "[]")),
            agents_failed=json.loads(r.get("agents_failed", "[]")) if r.get("agents_failed") else [],
            elapsed_seconds=r.get("elapsed_seconds", 0) or 0,
            llm_model=r.get("llm_model", ""),
        )

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
                            magnitude_accuracy, avg_confidence, avg_error_pct, last_updated)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (name, tf, stats["total"], stats["direction_accuracy"],
                         stats["magnitude_accuracy"], stats["avg_confidence"],
                         stats["avg_error_pct"], datetime.now().isoformat()),
                    )
                # 综合
                stats = self.get_accuracy_stats(timeframe=tf)
                conn.execute(
                    """INSERT OR REPLACE INTO accuracy_stats
                       (agent_name, timeframe, total_predictions, direction_accuracy,
                        magnitude_accuracy, avg_confidence, avg_error_pct, last_updated)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (None, tf, stats["total"], stats["direction_accuracy"],
                     stats["magnitude_accuracy"], stats["avg_confidence"],
                     stats["avg_error_pct"], datetime.now().isoformat()),
                )
            conn.commit()
