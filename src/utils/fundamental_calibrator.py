"""
基本面分析师专用置信度校准器

基于历史验证数据，构建"预测置信度 → 实际准确率"的映射。
使用分桶统计 + 贝叶斯收缩做校准。

核心功能:
1. 置信度校准: 将 LLM 的原始 confidence 向历史准确率回归
2. 按数据完整度分桶: 高/中/低数据质量分别统计准确率
3. 按评分卡等级分桶: excellent/good/average/weak 分别统计
4. 估值分位预测力: 统计不同分位区间的实际收益
"""

import logging
import json
from pathlib import Path
from typing import Optional
from collections import defaultdict

logger = logging.getLogger(__name__)


class FundamentalConfidenceCalibrator:
    """基本面分析师置信度校准器"""

    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    STATS_FILE = PROJECT_ROOT / "data" / "calibration" / "fundamental_calibration_stats.json"
    LEGACY_STATS_FILE = PROJECT_ROOT / "config" / "fundamental_calibration_stats.json"

    def __init__(self, stats_file: Optional[str | Path] = None,
                 legacy_stats_file: Optional[str | Path] = None):
        self.stats_file = Path(stats_file) if stats_file else self.STATS_FILE
        self.legacy_stats_file = (
            Path(legacy_stats_file) if legacy_stats_file else self.LEGACY_STATS_FILE
        )

        # 置信度桶: 统计每个区间的实际准确率
        self._confidence_bins = {
            "0.0-0.2": {"total": 0, "correct": 0},
            "0.2-0.4": {"total": 0, "correct": 0},
            "0.4-0.6": {"total": 0, "correct": 0},
            "0.6-0.8": {"total": 0, "correct": 0},
            "0.8-1.0": {"total": 0, "correct": 0},
        }
        # 按数据完整度分桶
        self._quality_buckets = {
            "high": {"total": 0, "correct": 0, "avg_confidence": 0.0},
            "medium": {"total": 0, "correct": 0, "avg_confidence": 0.0},
            "low": {"total": 0, "correct": 0, "avg_confidence": 0.0},
        }
        # 按评分卡等级分桶
        self._scorecard_buckets = defaultdict(lambda: {"total": 0, "correct": 0})
        # 估值分位预测力
        self._percentile_buckets = {
            "<0.1": {"total": 0, "positive_return": 0, "avg_return": 0.0},
            "0.1-0.3": {"total": 0, "positive_return": 0, "avg_return": 0.0},
            "0.3-0.7": {"total": 0, "positive_return": 0, "avg_return": 0.0},
            "0.7-0.9": {"total": 0, "positive_return": 0, "avg_return": 0.0},
            ">0.9": {"total": 0, "positive_return": 0, "avg_return": 0.0},
        }

        self._load_stats()

    def calibrate(self, raw_confidence: float, data_quality_bucket: str = "medium",
                  scorecard_rating: str = None) -> float:
        """
        校准原始置信度。

        Args:
            raw_confidence: LLM 原始判断的置信度
            data_quality_bucket: 数据质量分桶（high/medium/low）
            scorecard_rating: 评分卡等级（excellent/good/average/weak）

        Returns:
            校准后的置信度
        """
        calibrated = raw_confidence

        # 1. 基于置信度桶的校准
        bin_key = self._get_bin_key(raw_confidence)
        bin_stats = self._confidence_bins.get(bin_key)
        if bin_stats and bin_stats["total"] >= 10:
            historical_acc = bin_stats["correct"] / bin_stats["total"]
            # 贝叶斯收缩: 70% 原始判断 + 30% 历史准确率
            calibrated = raw_confidence * 0.7 + historical_acc * 0.3
            logger.debug(
                f"置信度校准(桶 {bin_key}): {raw_confidence:.2f} → {calibrated:.2f} "
                f"(历史准确率={historical_acc:.2f}, 样本={bin_stats['total']})"
            )

        # 2. 基于数据完整度的调整
        quality_stats = self._quality_buckets.get(data_quality_bucket)
        if quality_stats and quality_stats["total"] >= 10:
            quality_acc = quality_stats["correct"] / quality_stats["total"]
            # 如果历史准确率显著低于原始置信度，进一步打折
            if quality_acc < calibrated * 0.8:
                calibrated = (calibrated + quality_acc) / 2
                logger.debug(f"数据质量校准({data_quality_bucket}): → {calibrated:.2f}")

        # 3. 基于评分卡等级的调整
        if scorecard_rating:
            sc_stats = self._scorecard_buckets.get(scorecard_rating)
            if sc_stats and sc_stats["total"] >= 5:
                sc_acc = sc_stats["correct"] / sc_stats["total"]
                # 轻微调整
                calibrated = calibrated * 0.85 + sc_acc * 0.15

        return round(min(max(calibrated, 0.05), 0.95), 2)

    def update_from_validation(self, predicted_conf: float, was_correct: bool,
                                data_quality_bucket: str = "medium",
                                scorecard_rating: str = None,
                                pe_percentile: float = None,
                                actual_return_pct: float = None):
        """从验证结果中学习"""
        # 更新置信度桶
        bin_key = self._get_bin_key(predicted_conf)
        if bin_key in self._confidence_bins:
            self._confidence_bins[bin_key]["total"] += 1
            if was_correct:
                self._confidence_bins[bin_key]["correct"] += 1

        # 更新数据质量桶
        if data_quality_bucket in self._quality_buckets:
            bucket = self._quality_buckets[data_quality_bucket]
            bucket["total"] += 1
            if was_correct:
                bucket["correct"] += 1

        # 更新评分卡桶
        if scorecard_rating:
            self._scorecard_buckets[scorecard_rating]["total"] += 1
            if was_correct:
                self._scorecard_buckets[scorecard_rating]["correct"] += 1

        # 更新估值分位桶
        if pe_percentile is not None and actual_return_pct is not None:
            p_bucket = self._get_percentile_bucket(pe_percentile)
            if p_bucket in self._percentile_buckets:
                pb = self._percentile_buckets[p_bucket]
                pb["total"] += 1
                if actual_return_pct > 0:
                    pb["positive_return"] += 1
                # 更新平均收益
                pb["avg_return"] = (
                    pb["avg_return"] * (pb["total"] - 1) + actual_return_pct
                ) / pb["total"]

        # 定期保存
        if sum(b["total"] for b in self._confidence_bins.values()) % 10 == 0:
            self._save_stats()

    def get_calibration_stats(self) -> dict:
        """获取校准统计"""
        stats = {
            "confidence_bins": {},
            "quality_buckets": {},
            "scorecard_buckets": dict(self._scorecard_buckets),
            "percentile_buckets": {},
        }

        for bin_key, bin_data in self._confidence_bins.items():
            if bin_data["total"] > 0:
                stats["confidence_bins"][bin_key] = {
                    "total": bin_data["total"],
                    "accuracy": round(bin_data["correct"] / bin_data["total"], 3),
                }

        for q_key, q_data in self._quality_buckets.items():
            if q_data["total"] > 0:
                stats["quality_buckets"][q_key] = {
                    "total": q_data["total"],
                    "accuracy": round(q_data["correct"] / q_data["total"], 3),
                }

        for p_key, p_data in self._percentile_buckets.items():
            if p_data["total"] > 0:
                stats["percentile_buckets"][p_key] = {
                    "total": p_data["total"],
                    "positive_rate": round(p_data["positive_return"] / p_data["total"], 3),
                    "avg_return": round(p_data["avg_return"], 2),
                }

        return stats

    def get_percentile_predictive_power(self) -> dict:
        """获取估值分位的预测力统计

        Returns:
            {
                "<0.1": {"positive_rate": 0.75, "avg_return": 8.5},
                "0.7-0.9": {"positive_rate": 0.35, "avg_return": -3.2},
                ...
            }
        """
        result = {}
        for p_key, p_data in self._percentile_buckets.items():
            if p_data["total"] >= 5:
                result[p_key] = {
                    "positive_rate": round(p_data["positive_return"] / p_data["total"], 3),
                    "avg_return": round(p_data["avg_return"], 2),
                    "sample_size": p_data["total"],
                }
        return result

    def save(self):
        """显式保存当前校准统计。"""
        self._save_stats()

    def _get_bin_key(self, confidence: float) -> str:
        if confidence < 0.2:
            return "0.0-0.2"
        elif confidence < 0.4:
            return "0.2-0.4"
        elif confidence < 0.6:
            return "0.4-0.6"
        elif confidence < 0.8:
            return "0.6-0.8"
        else:
            return "0.8-1.0"

    def _get_percentile_bucket(self, pe_percentile: float) -> str:
        if pe_percentile < 0.1:
            return "<0.1"
        elif pe_percentile < 0.3:
            return "0.1-0.3"
        elif pe_percentile < 0.7:
            return "0.3-0.7"
        elif pe_percentile < 0.9:
            return "0.7-0.9"
        else:
            return ">0.9"

    def _load_stats(self):
        """从文件加载统计"""
        try:
            source = self.stats_file if self.stats_file.exists() else self.legacy_stats_file
            if source.exists():
                with open(source, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._confidence_bins = data.get("confidence_bins", self._confidence_bins)
                self._quality_buckets = data.get("quality_buckets", self._quality_buckets)
                self._scorecard_buckets = defaultdict(
                    lambda: {"total": 0, "correct": 0},
                    data.get("scorecard_buckets", {}),
                )
                self._percentile_buckets = data.get("percentile_buckets", self._percentile_buckets)
        except Exception as e:
            logger.debug(f"校准统计加载失败: {e}")

    def _save_stats(self):
        """保存统计到文件"""
        try:
            self.stats_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.stats_file, "w", encoding="utf-8") as f:
                json.dump({
                    "confidence_bins": self._confidence_bins,
                    "quality_buckets": self._quality_buckets,
                    "scorecard_buckets": dict(self._scorecard_buckets),
                    "percentile_buckets": self._percentile_buckets,
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"校准统计保存失败: {e}")
