"""
最新新闻分析师专用置信度校准器

基于历史验证/新闻快照样本，按新闻数量、来源、情绪、事件和时效
统计方向命中率，用于修正新闻面 confidence。
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class NewsConfidenceCalibrator:
    """新闻面置信度校准器。"""

    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    STATS_FILE = PROJECT_ROOT / "data" / "calibration" / "news_calibration_stats.json"
    LEGACY_STATS_FILE = PROJECT_ROOT / "config" / "news_calibration_stats.json"

    def __init__(
        self,
        stats_file: Optional[str | Path] = None,
        legacy_stats_file: Optional[str | Path] = None,
    ):
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
        self._news_count_buckets = self._new_bucket_map(
            ["none", "sparse", "limited", "adequate", "rich", "unknown"]
        )
        self._source_buckets = self._new_bucket_map(
            ["no_source", "single_source", "multi_source", "unknown"]
        )
        self._sentiment_buckets = self._new_bucket_map(
            ["positive", "negative", "neutral", "divergent", "unknown"]
        )
        self._event_buckets = self._new_bucket_map(
            [
                "positive_catalyst",
                "negative_catalyst",
                "mixed",
                "rumor_driven",
                "no_clear_event",
                "unknown",
            ]
        )
        self._freshness_buckets = self._new_bucket_map(["fresh", "decayed", "stale", "unknown"])

        self._load_stats()

    def calibrate(
        self,
        raw_confidence: float,
        news_count_bucket: str = "unknown",
        source_bucket: str = "unknown",
        sentiment_bucket: str = "unknown",
        event_bucket: str = "unknown",
        freshness_bucket: str = "unknown",
    ) -> float:
        """按历史命中率校准原始 confidence。"""
        calibrated = self._clip(raw_confidence)

        bin_key = self._get_bin_key(calibrated)
        bin_stats = self._confidence_bins.get(bin_key)
        if bin_stats and bin_stats["total"] >= 10:
            historical_acc = bin_stats["correct"] / bin_stats["total"]
            calibrated = calibrated * 0.70 + historical_acc * 0.30

        for buckets, key, min_samples, weight in (
            (self._news_count_buckets, news_count_bucket, 5, 0.15),
            (self._source_buckets, source_bucket, 5, 0.15),
            (self._sentiment_buckets, sentiment_bucket, 5, 0.15),
            (self._event_buckets, event_bucket, 5, 0.15),
            (self._freshness_buckets, freshness_bucket, 5, 0.10),
        ):
            calibrated = self._apply_bucket_adjustment(
                calibrated, buckets, key, min_samples=min_samples, weight=weight,
            )

        return round(self._clip(calibrated), 2)

    def update_from_validation(
        self,
        predicted_conf: float,
        was_correct: bool,
        news_count_bucket: str = "unknown",
        source_bucket: str = "unknown",
        sentiment_bucket: str = "unknown",
        event_bucket: str = "unknown",
        freshness_bucket: str = "unknown",
    ) -> None:
        """从一次方向验证结果中学习。"""
        self._increment(self._confidence_bins, self._get_bin_key(predicted_conf), was_correct)
        self._increment(self._news_count_buckets, news_count_bucket, was_correct)
        self._increment(self._source_buckets, source_bucket, was_correct)
        self._increment(self._sentiment_buckets, sentiment_bucket, was_correct)
        self._increment(self._event_buckets, event_bucket, was_correct)
        self._increment(self._freshness_buckets, freshness_bucket, was_correct)

        total = sum(b["total"] for b in self._confidence_bins.values())
        if total and total % 10 == 0:
            self._save_stats()

    def get_calibration_stats(self) -> dict:
        """获取完整校准统计。"""
        return {
            "confidence_bins": self._visible_stats(self._confidence_bins),
            "news_count_buckets": self._visible_stats(self._news_count_buckets),
            "source_buckets": self._visible_stats(self._source_buckets),
            "sentiment_buckets": self._visible_stats(self._sentiment_buckets),
            "event_buckets": self._visible_stats(self._event_buckets),
            "freshness_buckets": self._visible_stats(self._freshness_buckets),
        }

    def save(self) -> None:
        """显式保存当前校准统计。"""
        self._save_stats()

    @classmethod
    def extract_buckets_from_evidence(cls, evidence_packet: dict) -> dict:
        """从新闻证据包提取校准桶。"""
        matrix = evidence_packet.get("decision_matrix") or {}
        news_window = evidence_packet.get("news_window") or {}
        impact = evidence_packet.get("event_impact_matrix") or {}
        source_quality = evidence_packet.get("source_quality") or {}

        news_count = int(cls._safe_float(news_window.get("news_count"), 0) or 0)
        source_count_raw = source_quality.get("source_count")
        if source_count_raw is None:
            source_count = len(news_window.get("sources_used") or [])
        else:
            source_count = int(
                cls._safe_float(
                    source_count_raw,
                    len(news_window.get("sources_used") or []),
                ) or 0
            )
        avg_time_weight = cls._safe_float(impact.get("average_time_weight"), None)

        return {
            "news_count_bucket": cls.derive_news_count_bucket(
                matrix.get("volume_bucket"), news_count,
            ),
            "source_bucket": cls.derive_source_bucket(source_count),
            "sentiment_bucket": matrix.get("sentiment_bucket") or "unknown",
            "event_bucket": matrix.get("event_bucket") or "unknown",
            "freshness_bucket": cls.derive_freshness_bucket(avg_time_weight),
        }

    @staticmethod
    def derive_news_count_bucket(volume_bucket: Optional[str], news_count: int) -> str:
        if volume_bucket == "no_data" or news_count <= 0:
            return "none"
        if volume_bucket in {"sparse", "limited", "adequate", "rich"}:
            return volume_bucket
        if news_count <= 2:
            return "sparse"
        if news_count <= 5:
            return "limited"
        if news_count <= 12:
            return "adequate"
        return "rich"

    @staticmethod
    def derive_source_bucket(source_count: int) -> str:
        if source_count <= 0:
            return "no_source"
        if source_count == 1:
            return "single_source"
        return "multi_source"

    @staticmethod
    def derive_freshness_bucket(avg_time_weight: Optional[float]) -> str:
        if avg_time_weight is None:
            return "unknown"
        if avg_time_weight >= 0.75:
            return "fresh"
        if avg_time_weight >= 0.40:
            return "decayed"
        return "stale"

    @staticmethod
    def _new_bucket_map(keys: list[str]) -> dict:
        return {key: {"total": 0, "correct": 0} for key in keys}

    @staticmethod
    def _safe_float(value, default: Optional[float] = 0.0) -> Optional[float]:
        try:
            if value in (None, "", "N/A"):
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _clip(value: float) -> float:
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = 0.0
        return max(0.05, min(0.95, value))

    def _get_bin_key(self, confidence: float) -> str:
        confidence = self._clip(confidence)
        if confidence < 0.2:
            return "0.0-0.2"
        if confidence < 0.4:
            return "0.2-0.4"
        if confidence < 0.6:
            return "0.4-0.6"
        if confidence < 0.8:
            return "0.6-0.8"
        return "0.8-1.0"

    @staticmethod
    def _increment(buckets: dict, key: str, was_correct: bool) -> None:
        key = key or "unknown"
        if key not in buckets:
            buckets[key] = {"total": 0, "correct": 0}
        buckets[key]["total"] += 1
        if was_correct:
            buckets[key]["correct"] += 1

    @staticmethod
    def _apply_bucket_adjustment(
        calibrated: float,
        buckets: dict,
        key: str,
        min_samples: int,
        weight: float,
    ) -> float:
        stats = buckets.get(key or "unknown")
        if not stats or stats["total"] < min_samples:
            return calibrated
        historical_acc = stats["correct"] / stats["total"]
        if historical_acc < calibrated * 0.75:
            return (calibrated + historical_acc) / 2
        return calibrated * (1 - weight) + historical_acc * weight

    @staticmethod
    def _visible_stats(buckets: dict) -> dict:
        return {
            key: {
                "total": value["total"],
                "accuracy": round(value["correct"] / value["total"], 3),
            }
            for key, value in buckets.items()
            if value.get("total", 0) > 0
        }

    def _load_stats(self) -> None:
        try:
            source = self.stats_file if self.stats_file.exists() else self.legacy_stats_file
            if not source.exists():
                return
            with open(source, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._confidence_bins = data.get("confidence_bins", self._confidence_bins)
            self._news_count_buckets = data.get(
                "news_count_buckets", self._news_count_buckets,
            )
            self._source_buckets = data.get("source_buckets", self._source_buckets)
            self._sentiment_buckets = data.get("sentiment_buckets", self._sentiment_buckets)
            self._event_buckets = data.get("event_buckets", self._event_buckets)
            self._freshness_buckets = data.get("freshness_buckets", self._freshness_buckets)
        except Exception as e:
            logger.debug(f"新闻校准统计加载失败: {e}")

    def _save_stats(self) -> None:
        try:
            self.stats_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.stats_file, "w", encoding="utf-8") as f:
                json.dump({
                    "confidence_bins": self._confidence_bins,
                    "news_count_buckets": self._news_count_buckets,
                    "source_buckets": self._source_buckets,
                    "sentiment_buckets": self._sentiment_buckets,
                    "event_buckets": self._event_buckets,
                    "freshness_buckets": self._freshness_buckets,
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"新闻校准统计保存失败: {e}")
