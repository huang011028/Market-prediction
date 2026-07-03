"""
新闻源权重自适应管理器

基于各源的历史预测准确率，动态调整源的可信度权重。
与 PredictionStore 配合使用。
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 初始先验权重（基于源的可信度评估）
DEFAULT_SOURCE_WEIGHTS = {
    "eastmoney": 0.80,   # 东方财富 — 覆盖面广，有编辑审核
    "sina": 0.75,        # 新浪财经 — 编辑质量较高
    "xueqiu": 0.50,      # 雪球 — 散户情绪，噪音大
    "yfinance": 0.70,    # yfinance — 英文源
    "official": 0.95,    # 官方公告
    "unknown": 0.50,
}


class SourceWeightManager:
    """基于历史准确率的新闻源权重管理

    策略：
    - 初始使用先验权重
    - 随着已验证预测积累，用贝叶斯更新调整权重
    - 样本 < 10 时不更新（不可靠）
    """

    def __init__(self, prediction_store=None):
        self.store = prediction_store
        self._weights = dict(DEFAULT_SOURCE_WEIGHTS)
        self._sample_counts = {s: 0 for s in self._weights}

    def get_weight(self, source: str) -> float:
        """获取某新闻源的可信度权重（0~1）"""
        # 标准化源名
        source_lower = source.lower()
        for key in self._weights:
            if key in source_lower:
                return self._weights[key]
        return self._weights.get("unknown", 0.5)

    def get_all_weights(self) -> dict:
        """获取所有源的当前权重"""
        return dict(self._weights)

    def update_weights(self):
        """从 PredictionStore 读取数据，用贝叶斯更新调整权重

        仅当某源有 >= 10 条已验证预测时才更新该源。
        """
        if not self.store:
            logger.debug("PredictionStore 不可用，跳过权重更新")
            return

        try:
            # 获取所有已验证的预测
            predictions = self.store.get_predictions(verified_only=True, limit=500)
            if len(predictions) < 10:
                return

            # 按源分组统计准确率
            source_stats = {}
            for pred in predictions:
                if not pred.agents_used:
                    continue
                # 从 news_sources_used 解析（如果存在）
                sources = getattr(pred, "news_sources_used", [])
                if not sources:
                    # 尝试从 prediction record 推断
                    # 暂时跳过没有源信息的记录
                    continue

                for src in sources:
                    if src not in source_stats:
                        source_stats[src] = {"correct": 0, "total": 0}
                    source_stats[src]["total"] += 1
                    if pred.direction_correct == 1:
                        source_stats[src]["correct"] += 1

            # 贝叶斯更新
            for src, stats in source_stats.items():
                if stats["total"] >= 10:
                    accuracy = stats["correct"] / stats["total"]
                    prior = self._weights.get(src, 0.5)
                    # 贝叶斯：先验权重 30% + 实际准确率 70%
                    self._weights[src] = round(prior * 0.3 + accuracy * 0.7, 2)
                    self._sample_counts[src] = stats["total"]
                    logger.info(
                        f"源权重更新: {src} → {self._weights[src]:.2f} "
                        f"(准确率={accuracy:.0%}, 样本={stats['total']})"
                    )

        except Exception as e:
            logger.warning(f"权重更新失败: {e}")

    def get_credibility_adjusted_sentiment(
        self, items: list[dict]
    ) -> dict:
        """计算经过源可信度加权的情感得分

        Args:
            items: 新闻列表（每条需含 _sentiment 和 source）

        Returns:
            {"weighted_positive": float, "weighted_negative": float}
        """
        pos_score = 0.0
        neg_score = 0.0

        for item in items:
            weight = self.get_weight(item.get("source", ""))
            sentiment = item.get("_sentiment", "unknown")

            if sentiment == "positive":
                pos_score += weight
            elif sentiment == "negative":
                neg_score += weight

        return {
            "weighted_positive": round(pos_score, 2),
            "weighted_negative": round(neg_score, 2),
        }
