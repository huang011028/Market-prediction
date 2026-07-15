"""
近期股价分析师专用置信度校准器

基于历史验证/回测样本，按技术场景统计方向命中率，
用于冷启动后修正技术面 confidence。
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class TechnicalConfidenceCalibrator:
    """技术面置信度校准器。"""

    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    STATS_FILE = PROJECT_ROOT / "data" / "calibration" / "technical_calibration_stats.json"
    LEGACY_STATS_FILE = PROJECT_ROOT / "config" / "technical_calibration_stats.json"
    DIRECTION_POLICY_FILE = (
        PROJECT_ROOT / "config" / "agent_improvement" / "technical_direction_policy.json"
    )
    SKILL_REGISTRY_FILE = (
        PROJECT_ROOT / "config" / "agent_improvement" / "agent_skill_registry.json"
    )

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
        self._trend_buckets = self._new_bucket_map(["up", "down", "sideways", "unknown"])
        self._momentum_buckets = self._new_bucket_map(["bullish", "bearish", "mixed", "unknown"])
        self._volume_buckets = self._new_bucket_map(
            ["confirm_up", "confirm_down", "shrinking", "neutral", "unknown"]
        )
        self._position_buckets = self._new_bucket_map(
            ["near_resistance", "near_support", "middle_range", "unknown"]
        )
        self._market_regime_buckets = self._new_bucket_map([
            "bull_trend",
            "bear_trend",
            "sideways_range",
            "bull_pullback",
            "bear_rebound",
            "transition_up",
            "transition_down",
            "mixed",
            "unknown",
        ])
        self._volatility_buckets = self._new_bucket_map([
            "low",
            "normal",
            "high",
            "extreme",
            "unknown",
        ])
        self._sr_zone_buckets = self._new_bucket_map([
            "near_resistance",
            "near_support",
            "squeeze",
            "upper_range",
            "lower_range",
            "middle_range",
            "breakout_zone",
            "breakdown_zone",
            "unknown",
        ])
        self._risk_reward_buckets = self._new_bucket_map([
            "favorable",
            "balanced",
            "weak",
            "unfavorable",
            "unknown",
        ])
        self._technical_scenario_buckets = self._new_bucket_map(["unknown"])
        self._regime_sr_buckets = self._new_bucket_map(["unknown"])
        self._regime_volume_buckets = self._new_bucket_map(["unknown"])
        self._sr_volume_buckets = self._new_bucket_map(["unknown"])
        self._intraday_buckets = self._new_bucket_map(
            ["strong_up", "selloff", "mixed", "range_bound", "unavailable", "unknown"]
        )
        self._timeframe_buckets = self._new_bucket_map(["短期", "中期", "长期", "unknown"])

        self._load_stats()

    def calibrate(
        self,
        raw_confidence: float,
        trend_bucket: str = "unknown",
        momentum_bucket: str = "unknown",
        volume_bucket: str = "unknown",
        position_bucket: str = "unknown",
        market_regime_bucket: str = "unknown",
        volatility_bucket: str = "unknown",
        sr_zone_bucket: str = "unknown",
        risk_reward_bucket: str = "unknown",
        technical_scenario_bucket: str = "unknown",
        regime_sr_bucket: str = "unknown",
        regime_volume_bucket: str = "unknown",
        sr_volume_bucket: str = "unknown",
        intraday_bucket: str = "unknown",
        timeframe_bucket: str = "短期",
    ) -> float:
        """按历史命中率校准原始 confidence。"""
        calibrated = self._clip(raw_confidence)

        bin_key = self._get_bin_key(calibrated)
        bin_stats = self._confidence_bins.get(bin_key)
        if bin_stats and bin_stats["total"] >= 10:
            historical_acc = bin_stats["correct"] / bin_stats["total"]
            calibrated = calibrated * 0.70 + historical_acc * 0.30

        for buckets, key, min_samples, weight in (
            (self._trend_buckets, trend_bucket, 5, 0.15),
            (self._momentum_buckets, momentum_bucket, 5, 0.15),
            (self._volume_buckets, volume_bucket, 5, 0.15),
            (self._position_buckets, position_bucket, 5, 0.15),
            (self._market_regime_buckets, market_regime_bucket, 8, 0.10),
            (self._volatility_buckets, volatility_bucket, 8, 0.08),
            (self._sr_zone_buckets, sr_zone_bucket, 8, 0.10),
            (self._risk_reward_buckets, risk_reward_bucket, 8, 0.08),
            (self._technical_scenario_buckets, technical_scenario_bucket, 10, 0.08),
            (self._regime_sr_buckets, regime_sr_bucket, 10, 0.06),
            (self._regime_volume_buckets, regime_volume_bucket, 10, 0.06),
            (self._sr_volume_buckets, sr_volume_bucket, 10, 0.06),
            (self._intraday_buckets, intraday_bucket, 5, 0.10),
            (self._timeframe_buckets, timeframe_bucket, 10, 0.10),
        ):
            calibrated = self._apply_bucket_adjustment(
                calibrated, buckets, key, min_samples=min_samples, weight=weight,
            )

        return round(self._clip(calibrated), 2)

    def update_from_validation(
        self,
        predicted_conf: float,
        was_correct: bool,
        trend_bucket: str = "unknown",
        momentum_bucket: str = "unknown",
        volume_bucket: str = "unknown",
        position_bucket: str = "unknown",
        market_regime_bucket: str = "unknown",
        volatility_bucket: str = "unknown",
        sr_zone_bucket: str = "unknown",
        risk_reward_bucket: str = "unknown",
        technical_scenario_bucket: str = "unknown",
        regime_sr_bucket: str = "unknown",
        regime_volume_bucket: str = "unknown",
        sr_volume_bucket: str = "unknown",
        intraday_bucket: str = "unknown",
        timeframe_bucket: str = "短期",
    ) -> None:
        """从一次方向验证结果中学习。"""
        self._increment(self._confidence_bins, self._get_bin_key(predicted_conf), was_correct)
        self._increment(self._trend_buckets, trend_bucket, was_correct)
        self._increment(self._momentum_buckets, momentum_bucket, was_correct)
        self._increment(self._volume_buckets, volume_bucket, was_correct)
        self._increment(self._position_buckets, position_bucket, was_correct)
        self._increment(self._market_regime_buckets, market_regime_bucket, was_correct)
        self._increment(self._volatility_buckets, volatility_bucket, was_correct)
        self._increment(self._sr_zone_buckets, sr_zone_bucket, was_correct)
        self._increment(self._risk_reward_buckets, risk_reward_bucket, was_correct)
        self._increment(self._technical_scenario_buckets, technical_scenario_bucket, was_correct)
        self._increment(self._regime_sr_buckets, regime_sr_bucket, was_correct)
        self._increment(self._regime_volume_buckets, regime_volume_bucket, was_correct)
        self._increment(self._sr_volume_buckets, sr_volume_bucket, was_correct)
        self._increment(self._intraday_buckets, intraday_bucket, was_correct)
        self._increment(self._timeframe_buckets, timeframe_bucket, was_correct)

        total = sum(b["total"] for b in self._confidence_bins.values())
        if total and total % 10 == 0:
            self._save_stats()

    def get_calibration_stats(self) -> dict:
        """获取完整校准统计。"""
        return {
            "confidence_bins": self._visible_stats(self._confidence_bins),
            "trend_buckets": self._visible_stats(self._trend_buckets),
            "momentum_buckets": self._visible_stats(self._momentum_buckets),
            "volume_buckets": self._visible_stats(self._volume_buckets),
            "position_buckets": self._visible_stats(self._position_buckets),
            "market_regime_buckets": self._visible_stats(self._market_regime_buckets),
            "volatility_buckets": self._visible_stats(self._volatility_buckets),
            "sr_zone_buckets": self._visible_stats(self._sr_zone_buckets),
            "risk_reward_buckets": self._visible_stats(self._risk_reward_buckets),
            "technical_scenario_buckets": self._visible_stats(self._technical_scenario_buckets),
            "regime_sr_buckets": self._visible_stats(self._regime_sr_buckets),
            "regime_volume_buckets": self._visible_stats(self._regime_volume_buckets),
            "sr_volume_buckets": self._visible_stats(self._sr_volume_buckets),
            "intraday_buckets": self._visible_stats(self._intraday_buckets),
            "timeframe_buckets": self._visible_stats(self._timeframe_buckets),
        }

    def save(self) -> None:
        """显式保存当前校准统计。"""
        self._save_stats()

    @classmethod
    def load_direction_policy(
        cls,
        policy_file: Optional[str | Path] = None,
        registry_file: Optional[str | Path] = None,
    ) -> dict:
        """读取通过 holdout 的技术方向规则。

        正式来源是 Agent Skill Registry；旧的 technical_direction_policy.json
        作为兼容回退。
        """
        registry_path = Path(registry_file) if registry_file else cls.SKILL_REGISTRY_FILE
        try:
            if registry_path.exists():
                from src.core.agent_skill_registry import AgentSkillRegistry

                registry_policy = AgentSkillRegistry(
                    registry_path
                ).direction_policy_for_agent("近期股价分析师")
                if registry_policy.get("rules"):
                    return registry_policy
        except Exception as e:
            logger.debug(f"技术方向 registry 读取失败: {e}")

        path = Path(policy_file) if policy_file else cls.DIRECTION_POLICY_FILE
        try:
            if not path.exists():
                return {}
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.debug(f"技术方向规则读取失败: {e}")
            return {}

    @classmethod
    def apply_direction_policy(
        cls,
        predicted_direction: str,
        buckets: dict,
        policy: Optional[dict] = None,
    ) -> tuple[str, list[dict]]:
        """按通过 holdout 的声明式规则给出候选方向。"""
        current = getattr(predicted_direction, "value", predicted_direction) or "neutral"
        policy = policy if policy is not None else cls.load_direction_policy()
        rules = list((policy or {}).get("rules") or [])
        matched = []
        for rule in rules:
            conditions = rule.get("conditions") or {}
            if conditions:
                if all(str((buckets or {}).get(key)) == str(value) for key, value in conditions.items()):
                    matched.append(rule)
                continue
            sample_key = rule.get("sample_key") or cls._sample_key_from_group(
                rule.get("bucket_group"),
            )
            if not sample_key:
                continue
            if str((buckets or {}).get(sample_key)) != str(rule.get("bucket")):
                continue
            matched.append(rule)

        for rule in matched:
            action = str(rule.get("action") or "")
            if action == "force_bullish":
                return "bullish", matched
            if action == "force_bearish":
                return "bearish", matched
        if any(str(rule.get("action") or "") == "neutralize_direction" for rule in matched):
            return "neutral", matched
        return str(current), matched

    @classmethod
    def load_confidence_policy(
        cls,
        registry_file: Optional[str | Path] = None,
    ) -> dict:
        """读取通过 holdout 的技术置信度封顶规则。"""
        registry_path = Path(registry_file) if registry_file else cls.SKILL_REGISTRY_FILE
        try:
            if registry_path.exists():
                from src.core.agent_skill_registry import AgentSkillRegistry

                return AgentSkillRegistry(
                    registry_path
                ).confidence_policy_for_agent("近期股价分析师")
        except Exception as e:
            logger.debug(f"技术置信度 registry 读取失败: {e}")
        return {}

    @classmethod
    def apply_confidence_policy(
        cls,
        confidence: float,
        buckets: dict,
        policy: Optional[dict] = None,
    ) -> tuple[float, list[dict]]:
        """按通过 holdout 的声明式规则限制技术面置信度。"""
        current = cls._clip(confidence)
        policy = policy if policy is not None else cls.load_confidence_policy()
        rules = list((policy or {}).get("rules") or [])
        matched = []
        capped = current
        for rule in rules:
            conditions = rule.get("conditions") or {}
            if conditions:
                if not all(str((buckets or {}).get(key)) == str(value) for key, value in conditions.items()):
                    continue
            else:
                sample_key = rule.get("sample_key") or cls._sample_key_from_group(
                    rule.get("bucket_group"),
                )
                if not sample_key:
                    continue
                if str((buckets or {}).get(sample_key)) != str(rule.get("bucket")):
                    continue
            if str(rule.get("action") or "") != "cap_confidence":
                continue
            cap = cls._safe_float(rule.get("confidence_cap"), None)
            if cap is None:
                continue
            matched.append(rule)
            capped = min(capped, cls._clip(cap))
        return round(cls._clip(capped), 2), matched

    @classmethod
    def extract_buckets_from_evidence(cls, evidence_packet: dict, timeframe: str = "") -> dict:
        """从技术证据包提取校准桶。"""
        matrix = evidence_packet.get("decision_matrix") or {}
        trend_regime = evidence_packet.get("trend_regime") or {}
        support_resistance = evidence_packet.get("support_resistance") or {}
        volatility = evidence_packet.get("volatility_signals") or {}
        market_regime_bucket = cls.derive_market_regime_bucket(
            trend_regime,
            matrix.get("momentum_bucket") or "unknown",
        )
        volatility_bucket = cls.derive_volatility_bucket(volatility)
        sr_zone_bucket = cls.derive_sr_zone_bucket(support_resistance)
        volume_bucket = matrix.get("volume_bucket") or "unknown"
        return {
            "trend_bucket": matrix.get("trend_bucket") or "unknown",
            "momentum_bucket": matrix.get("momentum_bucket") or "unknown",
            "volume_bucket": volume_bucket,
            "position_bucket": cls.derive_position_bucket(support_resistance),
            "market_regime_bucket": market_regime_bucket,
            "volatility_bucket": volatility_bucket,
            "sr_zone_bucket": sr_zone_bucket,
            "risk_reward_bucket": matrix.get("risk_reward_bucket") or "unknown",
            "technical_scenario_bucket": cls.combine_buckets(
                market_regime_bucket,
                sr_zone_bucket,
                volume_bucket,
            ),
            "regime_sr_bucket": cls.combine_buckets(market_regime_bucket, sr_zone_bucket),
            "regime_volume_bucket": cls.combine_buckets(market_regime_bucket, volume_bucket),
            "sr_volume_bucket": cls.combine_buckets(sr_zone_bucket, volume_bucket),
            "intraday_bucket": matrix.get("intraday_bucket") or "unknown",
            "timeframe_bucket": cls.derive_timeframe_bucket(timeframe),
        }

    @staticmethod
    def derive_position_bucket(support_resistance: dict) -> str:
        """按当前价与支撑/压力距离划分位置桶。"""
        resistance = TechnicalConfidenceCalibrator._safe_float(
            support_resistance.get("resistance_distance_pct"), None,
        )
        support = TechnicalConfidenceCalibrator._safe_float(
            support_resistance.get("support_distance_pct"), None,
        )
        if resistance is not None and 0 <= resistance <= 2:
            return "near_resistance"
        if support is not None and -2 <= support <= 0:
            return "near_support"
        if resistance is None and support is None:
            return "unknown"
        return "middle_range"

    @staticmethod
    def derive_market_regime_bucket(trend_regime: dict, momentum_state: str = "") -> str:
        """按均线趋势结构划分牛/熊/震荡等技术状态。"""
        short = trend_regime.get("short_term") or "unknown"
        medium = trend_regime.get("medium_term") or "unknown"
        alignment = trend_regime.get("ma_alignment") or "unknown"
        structure = trend_regime.get("structure") or "unknown"
        momentum = momentum_state or "unknown"

        if short == "unknown" and medium == "unknown" and alignment == "unknown":
            return "unknown"
        if short == "up" and medium == "up":
            return "bull_trend"
        if short == "down" and medium == "down":
            return "bear_trend"
        if alignment == "bullish" and short != "down":
            return "bull_trend"
        if alignment == "bearish" and short != "up":
            return "bear_trend"
        if medium == "up" and short in {"sideways", "down"}:
            return "bull_pullback"
        if medium == "down" and short in {"sideways", "up"}:
            return "bear_rebound"
        if short == "up" and medium in {"sideways", "unknown"}:
            return "transition_up"
        if short == "down" and medium in {"sideways", "unknown"}:
            return "transition_down"
        if short == "sideways" or medium == "sideways" or structure == "range_bound":
            return "sideways_range"
        if momentum == "bullish":
            return "transition_up"
        if momentum == "bearish":
            return "transition_down"
        return "mixed"

    @staticmethod
    def derive_volatility_bucket(volatility_signals: dict) -> str:
        """按 ATR/波动率状态划分低/正常/高/极端波动。"""
        state = str(volatility_signals.get("volatility_state") or "").strip()
        atr_pct = TechnicalConfidenceCalibrator._safe_float(
            volatility_signals.get("atr_pct"), None,
        )
        daily_vol = TechnicalConfidenceCalibrator._safe_float(
            volatility_signals.get("daily_volatility_20d_pct"), None,
        )
        if atr_pct is not None:
            if atr_pct >= 6.0:
                return "extreme"
            if atr_pct >= 4.0:
                return "high"
            if atr_pct <= 1.5:
                return "low"
            return "normal"
        if daily_vol is not None:
            if daily_vol >= 5.0:
                return "extreme"
            if daily_vol >= 3.0:
                return "high"
            if daily_vol <= 1.0:
                return "low"
            return "normal"
        if state in {"low", "normal", "high"}:
            return state
        return "unknown"

    @staticmethod
    def derive_sr_zone_bucket(support_resistance: dict) -> str:
        """按支撑/压力区间位置划分更细场景桶。"""
        resistance = TechnicalConfidenceCalibrator._safe_float(
            support_resistance.get("resistance_distance_pct"), None,
        )
        support = TechnicalConfidenceCalibrator._safe_float(
            support_resistance.get("support_distance_pct"), None,
        )
        if resistance is None and support is None:
            return "unknown"
        if resistance is None:
            return "breakout_zone"
        if support is None:
            return "breakdown_zone"
        near_resistance = 0 <= resistance <= 2
        near_support = -2 <= support <= 0
        if near_resistance and near_support:
            return "squeeze"
        if near_resistance:
            return "near_resistance"
        if near_support:
            return "near_support"
        if 0 <= resistance <= 5:
            return "upper_range"
        if -5 <= support <= 0:
            return "lower_range"
        return "middle_range"

    @staticmethod
    def derive_timeframe_bucket(timeframe: str) -> str:
        text = timeframe or ""
        if "长期" in text or "季" in text:
            return "长期"
        if "中期" in text or "月" in text:
            return "中期"
        if "短期" in text or "周" in text:
            return "短期"
        return "unknown"

    @staticmethod
    def _sample_key_from_group(bucket_group: str) -> str:
        group = str(bucket_group or "")
        if group.endswith("_buckets"):
            return f"{group[:-8]}_bucket"
        return ""

    @staticmethod
    def combine_buckets(*values: str) -> str:
        normalized = [str(value or "unknown").strip() or "unknown" for value in values]
        if not normalized or any(value == "unknown" for value in normalized):
            return "unknown"
        return "|".join(normalized)

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
            self._trend_buckets = data.get("trend_buckets", self._trend_buckets)
            self._momentum_buckets = data.get("momentum_buckets", self._momentum_buckets)
            self._volume_buckets = data.get("volume_buckets", self._volume_buckets)
            self._position_buckets = data.get("position_buckets", self._position_buckets)
            self._market_regime_buckets = data.get(
                "market_regime_buckets",
                self._market_regime_buckets,
            )
            self._volatility_buckets = data.get("volatility_buckets", self._volatility_buckets)
            self._sr_zone_buckets = data.get("sr_zone_buckets", self._sr_zone_buckets)
            self._risk_reward_buckets = data.get(
                "risk_reward_buckets",
                self._risk_reward_buckets,
            )
            self._technical_scenario_buckets = data.get(
                "technical_scenario_buckets",
                self._technical_scenario_buckets,
            )
            self._regime_sr_buckets = data.get("regime_sr_buckets", self._regime_sr_buckets)
            self._regime_volume_buckets = data.get(
                "regime_volume_buckets",
                self._regime_volume_buckets,
            )
            self._sr_volume_buckets = data.get("sr_volume_buckets", self._sr_volume_buckets)
            self._intraday_buckets = data.get("intraday_buckets", self._intraday_buckets)
            self._timeframe_buckets = data.get("timeframe_buckets", self._timeframe_buckets)
        except Exception as e:
            logger.debug(f"技术面校准统计加载失败: {e}")

    def _save_stats(self) -> None:
        try:
            self.stats_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.stats_file, "w", encoding="utf-8") as f:
                json.dump({
                    "confidence_bins": self._confidence_bins,
                    "trend_buckets": self._trend_buckets,
                    "momentum_buckets": self._momentum_buckets,
                    "volume_buckets": self._volume_buckets,
                    "position_buckets": self._position_buckets,
                    "market_regime_buckets": self._market_regime_buckets,
                    "volatility_buckets": self._volatility_buckets,
                    "sr_zone_buckets": self._sr_zone_buckets,
                    "risk_reward_buckets": self._risk_reward_buckets,
                    "technical_scenario_buckets": self._technical_scenario_buckets,
                    "regime_sr_buckets": self._regime_sr_buckets,
                    "regime_volume_buckets": self._regime_volume_buckets,
                    "sr_volume_buckets": self._sr_volume_buckets,
                    "intraday_buckets": self._intraday_buckets,
                    "timeframe_buckets": self._timeframe_buckets,
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"技术面校准统计保存失败: {e}")
