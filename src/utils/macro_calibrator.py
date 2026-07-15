"""
国际形势分析师置信度校准器。

按市场、行业和宏观数据质量追踪历史命中率，用于修正宏观维度置信度。
"""

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class MacroConfidenceCalibrator:
    """宏观分析师置信度校准器。"""

    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    STATS_FILE = PROJECT_ROOT / "data" / "calibration" / "macro_calibration_stats.json"

    def __init__(self, stats_file: Optional[str | Path] = None):
        self.stats_file = Path(stats_file) if stats_file else self.STATS_FILE
        self._confidence_bins = {
            "0.0-0.2": {"total": 0, "correct": 0},
            "0.2-0.4": {"total": 0, "correct": 0},
            "0.4-0.6": {"total": 0, "correct": 0},
            "0.6-0.8": {"total": 0, "correct": 0},
            "0.8-1.0": {"total": 0, "correct": 0},
        }
        self._market_buckets = defaultdict(lambda: {"total": 0, "correct": 0})
        self._sector_buckets = defaultdict(lambda: {"total": 0, "correct": 0})
        self._quality_buckets = {
            "fresh": {"total": 0, "correct": 0},
            "mixed": {"total": 0, "correct": 0},
            "reference_heavy": {"total": 0, "correct": 0},
            "sparse": {"total": 0, "correct": 0},
            "stale": {"total": 0, "correct": 0},
        }
        self._load_stats()

    def calibrate(
        self,
        raw_confidence: float,
        market: str = "",
        sector: str = "",
        data_quality_level: str = "mixed",
    ) -> float:
        """基于历史命中率校准置信度。"""
        calibrated = raw_confidence

        bin_key = self._get_bin_key(raw_confidence)
        bin_stats = self._confidence_bins.get(bin_key)
        if bin_stats and bin_stats["total"] >= 10:
            historical_acc = bin_stats["correct"] / bin_stats["total"]
            calibrated = raw_confidence * 0.7 + historical_acc * 0.3

        market_stats = self._market_buckets.get(market)
        if market_stats and market_stats["total"] >= 5:
            market_acc = market_stats["correct"] / market_stats["total"]
            calibrated = calibrated * 0.85 + market_acc * 0.15

        sector_stats = self._sector_buckets.get(sector)
        if sector_stats and sector_stats["total"] >= 5:
            sector_acc = sector_stats["correct"] / sector_stats["total"]
            calibrated = calibrated * 0.85 + sector_acc * 0.15

        quality_stats = self._quality_buckets.get(data_quality_level)
        if quality_stats and quality_stats["total"] >= 10:
            quality_acc = quality_stats["correct"] / quality_stats["total"]
            if quality_acc < calibrated * 0.8:
                calibrated = (calibrated + quality_acc) / 2

        return round(min(max(calibrated, 0.05), 0.95), 2)

    def update_from_validation(
        self,
        predicted_conf: float,
        was_correct: bool,
        market: str = "",
        sector: str = "",
        data_quality_level: str = "mixed",
    ) -> None:
        """从验证结果中学习。"""
        bin_key = self._get_bin_key(predicted_conf)
        if bin_key in self._confidence_bins:
            self._confidence_bins[bin_key]["total"] += 1
            if was_correct:
                self._confidence_bins[bin_key]["correct"] += 1

        if market:
            self._market_buckets[market]["total"] += 1
            if was_correct:
                self._market_buckets[market]["correct"] += 1

        if sector:
            self._sector_buckets[sector]["total"] += 1
            if was_correct:
                self._sector_buckets[sector]["correct"] += 1

        if data_quality_level in self._quality_buckets:
            self._quality_buckets[data_quality_level]["total"] += 1
            if was_correct:
                self._quality_buckets[data_quality_level]["correct"] += 1

        total = sum(b["total"] for b in self._confidence_bins.values())
        if total % 10 == 0:
            self._save_stats()

    def get_calibration_stats(self) -> dict:
        """获取完整校准统计。"""
        return {
            "confidence_bins": {
                k: {"total": v["total"], "accuracy": round(v["correct"] / v["total"], 3)}
                for k, v in self._confidence_bins.items() if v["total"] > 0
            },
            "market_buckets": {
                k: {"total": v["total"], "accuracy": round(v["correct"] / v["total"], 3)}
                for k, v in self._market_buckets.items() if v["total"] > 0
            },
            "sector_buckets": {
                k: {"total": v["total"], "accuracy": round(v["correct"] / v["total"], 3)}
                for k, v in self._sector_buckets.items() if v["total"] > 0
            },
            "quality_buckets": {
                k: {"total": v["total"], "accuracy": round(v["correct"] / v["total"], 3)}
                for k, v in self._quality_buckets.items() if v["total"] > 0
            },
        }

    def save(self):
        """显式保存当前校准统计。"""
        self._save_stats()

    def _get_bin_key(self, confidence: float) -> str:
        if confidence < 0.2:
            return "0.0-0.2"
        if confidence < 0.4:
            return "0.2-0.4"
        if confidence < 0.6:
            return "0.4-0.6"
        if confidence < 0.8:
            return "0.6-0.8"
        return "0.8-1.0"

    def _load_stats(self):
        try:
            if self.stats_file.exists():
                data = json.loads(self.stats_file.read_text(encoding="utf-8"))
                self._confidence_bins = data.get("confidence_bins", self._confidence_bins)
                self._market_buckets = defaultdict(
                    lambda: {"total": 0, "correct": 0},
                    data.get("market_buckets", {}),
                )
                self._sector_buckets = defaultdict(
                    lambda: {"total": 0, "correct": 0},
                    data.get("sector_buckets", {}),
                )
                self._quality_buckets = data.get("quality_buckets", self._quality_buckets)
        except Exception as e:
            logger.debug(f"宏观校准统计加载失败: {e}")

    def _save_stats(self):
        try:
            self.stats_file.parent.mkdir(parents=True, exist_ok=True)
            self.stats_file.write_text(
                json.dumps(
                    {
                        "confidence_bins": self._confidence_bins,
                        "market_buckets": dict(self._market_buckets),
                        "sector_buckets": dict(self._sector_buckets),
                        "quality_buckets": self._quality_buckets,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"宏观校准统计保存失败: {e}")
