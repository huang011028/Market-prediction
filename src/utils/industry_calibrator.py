"""
行业对比分析师置信度校准器

基于历史验证数据，按行业追踪准确率，
为不同行业的预测提供差异化的置信度校准。
"""

import logging
import json
from pathlib import Path
from typing import Optional
from collections import defaultdict

logger = logging.getLogger(__name__)


class IndustryConfidenceCalibrator:
    """行业对比分析师置信度校准器"""

    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    STATS_FILE = PROJECT_ROOT / "data" / "calibration" / "industry_calibration_stats.json"
    LEGACY_STATS_FILE = PROJECT_ROOT / "config" / "industry_calibration_stats.json"

    def __init__(self, stats_file: Optional[str | Path] = None,
                 legacy_stats_file: Optional[str | Path] = None):
        self.stats_file = Path(stats_file) if stats_file else self.STATS_FILE
        self.legacy_stats_file = (
            Path(legacy_stats_file) if legacy_stats_file else self.LEGACY_STATS_FILE
        )

        self._confidence_bins = {
            "0.0-0.2": {"total": 0, "correct": 0},
            "0.2-0.4": {"total": 0, "correct": 0},
            "0.4-0.6": {"total": 0, "correct": 0},
            "0.6-0.8": {"total": 0, "correct": 0},
            "0.8-1.0": {"total": 0, "correct": 0},
        }
        # 按行业统计准确率
        self._industry_buckets = defaultdict(lambda: {"total": 0, "correct": 0})
        # 按数据质量统计
        self._quality_buckets = {
            "constituents+trend": {"total": 0, "correct": 0},
            "constituents_only": {"total": 0, "correct": 0},
            "reference_only": {"total": 0, "correct": 0},
            "none": {"total": 0, "correct": 0},
        }

        self._load_stats()

    def calibrate(self, raw_confidence: float, industry: str = None,
                  data_quality_level: str = "reference_only") -> float:
        """
        校准原始置信度。

        Args:
            raw_confidence: LLM 原始判断的置信度
            industry: 行业名称
            data_quality_level: 数据质量级别
                (constituents+trend / constituents_only / reference_only / none)

        Returns:
            校准后的置信度
        """
        calibrated = raw_confidence

        # 1. 基于置信度桶的校准
        bin_key = self._get_bin_key(raw_confidence)
        bin_stats = self._confidence_bins.get(bin_key)
        if bin_stats and bin_stats["total"] >= 10:
            historical_acc = bin_stats["correct"] / bin_stats["total"]
            calibrated = raw_confidence * 0.7 + historical_acc * 0.3

        # 2. 基于行业的调整
        if industry:
            ind_stats = self._industry_buckets.get(industry)
            if ind_stats and ind_stats["total"] >= 5:
                ind_acc = ind_stats["correct"] / ind_stats["total"]
                # 轻微调整
                calibrated = calibrated * 0.85 + ind_acc * 0.15
                logger.debug(
                    f"行业置信度调整({industry}): "
                    f"历史准确率={ind_acc:.2f}, 样本={ind_stats['total']}"
                )

        # 3. 基于数据质量的调整
        quality_stats = self._quality_buckets.get(data_quality_level)
        if quality_stats and quality_stats["total"] >= 10:
            quality_acc = quality_stats["correct"] / quality_stats["total"]
            if quality_acc < calibrated * 0.8:
                calibrated = (calibrated + quality_acc) / 2

        return round(min(max(calibrated, 0.05), 0.95), 2)

    def update_from_validation(self, predicted_conf: float, was_correct: bool,
                                industry: str = None,
                                data_quality_level: str = "reference_only"):
        """从验证结果中学习"""
        # 更新置信度桶
        bin_key = self._get_bin_key(predicted_conf)
        if bin_key in self._confidence_bins:
            self._confidence_bins[bin_key]["total"] += 1
            if was_correct:
                self._confidence_bins[bin_key]["correct"] += 1

        # 更新行业桶
        if industry:
            self._industry_buckets[industry]["total"] += 1
            if was_correct:
                self._industry_buckets[industry]["correct"] += 1

        # 更新数据质量桶
        if data_quality_level in self._quality_buckets:
            self._quality_buckets[data_quality_level]["total"] += 1
            if was_correct:
                self._quality_buckets[data_quality_level]["correct"] += 1

        # 定期保存
        total = sum(b["total"] for b in self._confidence_bins.values())
        if total % 10 == 0:
            self._save_stats()

    def get_industry_accuracy(self, industry: str) -> Optional[dict]:
        """获取某个行业的历史准确率"""
        stats = self._industry_buckets.get(industry)
        if stats and stats["total"] > 0:
            return {
                "total": stats["total"],
                "accuracy": round(stats["correct"] / stats["total"], 3),
            }
        return None

    def get_all_industry_accuracy(self) -> dict:
        """获取所有行业的准确率"""
        result = {}
        for ind, stats in self._industry_buckets.items():
            if stats["total"] >= 3:
                result[ind] = {
                    "total": stats["total"],
                    "accuracy": round(stats["correct"] / stats["total"], 3),
                }
        return result

    def get_calibration_stats(self) -> dict:
        """获取完整校准统计"""
        return {
            "confidence_bins": {
                k: {"total": v["total"], "accuracy": round(v["correct"] / v["total"], 3)}
                for k, v in self._confidence_bins.items() if v["total"] > 0
            },
            "industry_buckets": {
                k: {"total": v["total"], "accuracy": round(v["correct"] / v["total"], 3)}
                for k, v in self._industry_buckets.items() if v["total"] > 0
            },
            "quality_buckets": {
                k: {"total": v["total"], "accuracy": round(v["correct"] / v["total"], 3)}
                for k, v in self._quality_buckets.items() if v["total"] > 0
            },
        }

    def _get_bin_key(self, confidence: float) -> str:
        if confidence < 0.2: return "0.0-0.2"
        elif confidence < 0.4: return "0.2-0.4"
        elif confidence < 0.6: return "0.4-0.6"
        elif confidence < 0.8: return "0.6-0.8"
        else: return "0.8-1.0"

    def _load_stats(self):
        """从文件加载统计"""
        try:
            source = self.stats_file if self.stats_file.exists() else self.legacy_stats_file
            if source.exists():
                with open(source, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._confidence_bins = data.get("confidence_bins", self._confidence_bins)
                self._industry_buckets = defaultdict(
                    lambda: {"total": 0, "correct": 0},
                    data.get("industry_buckets", {}),
                )
                self._quality_buckets = data.get("quality_buckets", self._quality_buckets)
        except Exception as e:
            logger.debug(f"行业校准统计加载失败: {e}")

    def _save_stats(self):
        """保存统计到文件"""
        try:
            self.stats_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.stats_file, "w", encoding="utf-8") as f:
                json.dump({
                    "confidence_bins": self._confidence_bins,
                    "industry_buckets": dict(self._industry_buckets),
                    "quality_buckets": self._quality_buckets,
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"行业校准统计保存失败: {e}")
