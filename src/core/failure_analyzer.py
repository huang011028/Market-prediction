"""
失败案例自动诊断器

预测验证后 → 方向判断错误 → 自动分析失败原因，分类存储。

失败类型：
- signal_misread: 新闻信号被误读（需改进 prompt）
- data_missed: 遗漏了重大新闻（数据源问题）  
- market_irrational: 市场非理性波动（黑天鹅/情绪驱动）
- black_swan: 不可预见的突发事件
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class FailureReport:
    """失败分析报告"""
    prediction_id: str
    failure_type: str  # signal_misread | data_missed | market_irrational | black_swan
    root_cause: str
    affected_source: Optional[str] = None
    prompt_suggestion: str = ""
    should_confidence: Optional[float] = None
    analyzed_at: str = field(default_factory=lambda: datetime.now().isoformat())


class FailureAnalyzer:
    """自动分析预测失败的原因

    不需要 LLM 参与——基于规则 + 统计做快速判断。
    更深入的分析（需要 LLM 的）留给后续版本。
    """

    def __init__(self, prediction_store=None):
        self.store = prediction_store

    def analyze(self, record: dict) -> FailureReport:
        """分析单条预测失败的原因

        Args:
            record: PredictionRecord 的 dict 形式，需包含：
                - predicted_at, valid_until
                - direction, actual_direction, actual_change_pct
                - news_count (如果可用)
                - news_sources (如果可用)

        Returns:
            FailureReport
        """
        pid = record.get("id", "unknown")
        pred_dir = record.get("direction", "neutral")
        actual_dir = record.get("actual_direction", "neutral")
        actual_change = record.get("actual_change_pct", 0) or 0
        news_count = record.get("news_count", 0)

        # === 规则1: 数据缺失导致 ===
        if news_count is not None and news_count <= 1 and actual_dir != "neutral":
            return FailureReport(
                prediction_id=pid,
                failure_type="data_missed",
                root_cause=f"新闻数据严重不足({news_count}条)，实际方向为{actual_dir}({actual_change:+.1f}%)，可能遗漏重大新闻",
                prompt_suggestion="",
                should_confidence=0.2,
            )

        # === 规则2: 黑天鹅（大幅波动 + 方向完全相反）===
        if abs(actual_change) > 5 and pred_dir != actual_dir and actual_dir != "neutral":
            return FailureReport(
                prediction_id=pid,
                failure_type="black_swan",
                root_cause=f"实际波动{actual_change:+.1f}%远超正常范围，且方向与预测({pred_dir})完全相反，可能是突发黑天鹅事件",
                should_confidence=None,  # 黑天鹅不可预测，不调整
            )

        # === 规则3: 信号误读（方向相反但波动在正常范围）===
        if pred_dir != actual_dir and actual_dir != "neutral" and abs(actual_change) <= 5:
            return FailureReport(
                prediction_id=pid,
                failure_type="signal_misread",
                root_cause=f"预测方向({pred_dir})与实际({actual_dir}, {actual_change:+.1f}%)相反但波动正常，可能是新闻信号解读有偏差或过度乐观/悲观",
                prompt_suggestion="建议检查该案例的新闻数据，确认是否有被忽略的反向信号",
                should_confidence=min(record.get("confidence", 0.5) * 0.7, 0.4),
            )

        # === 规则4: 市场非理性（predict neutral 但实际大幅波动）===
        if pred_dir == "neutral" and actual_dir != "neutral" and abs(actual_change) > 3:
            return FailureReport(
                prediction_id=pid,
                failure_type="market_irrational",
                root_cause=f"预测为neutral但实际波动{actual_change:+.1f}%，市场可能受情绪驱动或非基本面因素影响",
                should_confidence=0.35,
            )

        # 默认：无法归类
        return FailureReport(
            prediction_id=pid,
            failure_type="market_irrational",
            root_cause=f"预测({pred_dir})与实际({actual_dir}, {actual_change:+.1f}%)存在偏差，可能由多种因素叠加导致",
        )

    def get_failure_stats(self, agent_name: str = None) -> dict:
        """获取失败类型分布统计"""
        if not self.store:
            return {"error": "PredictionStore not available"}

        try:
            # 查询已验证且方向错误的预测
            stats = {"signal_misread": 0, "data_missed": 0, "market_irrational": 0, "black_swan": 0}

            predictions = self.store.get_predictions(verified_only=True, limit=200)
            for pred in predictions:
                if pred.direction_correct == 0:  # 方向错误
                    report = self.analyze({
                        "id": pred.id,
                        "direction": pred.direction,
                        "actual_direction": pred.actual_direction,
                        "actual_change_pct": pred.actual_change_pct,
                        "confidence": pred.confidence,
                    })
                    stats[report.failure_type] = stats.get(report.failure_type, 0) + 1

            total = sum(stats.values())
            if total > 0:
                stats["total_failures"] = total
                stats["distribution"] = {
                    k: f"{v/total:.0%}" for k, v in stats.items() if k != "total_failures"
                }

            return stats
        except Exception as e:
            logger.warning(f"失败统计获取失败: {e}")
            return {"error": str(e)}
