"""
置信度与幅度校准器（Round2）

基于 PredictionStore 中的历史验证数据：
- 修正 LLM 的原始置信度（去偏）
- 检查预测幅度是否在 ATR 合理范围内
- 随验证数据积累持续更新校准参数
"""

import logging
from typing import Optional
from src.data.prediction_store import PredictionStore
from src.core.result import Magnitude

logger = logging.getLogger(__name__)


class ConfidenceCalibrator:
    """置信度与幅度校准器"""

    def __init__(self, store: Optional[PredictionStore] = None):
        self.store = store or PredictionStore()

    def calibrate_confidence(
        self,
        raw_confidence: float,
        agent_name: str,
        timeframe: str,
        signal_contradiction: bool = False,
        data_quality: str = "normal",
    ) -> dict:
        """校准 LLM 输出的原始置信度"""
        calibrated = raw_confidence
        adjustments = []

        # 调整1: 历史偏差修正
        stats = self.store.get_accuracy_stats(agent_name=agent_name, timeframe=timeframe)
        if stats["total"] >= 5:
            actual_acc = stats["direction_accuracy"]
            avg_conf = stats["avg_confidence"]
            if avg_conf > 0 and actual_acc > 0:
                bias_ratio = actual_acc / avg_conf
                if bias_ratio < 0.85:
                    calibrated *= bias_ratio
                    adjustments.append(f"历史偏差: {avg_conf:.0%}→{actual_acc:.0%}(系数{bias_ratio:.2f})")
                elif bias_ratio > 1.15:
                    calibrated = min(calibrated * bias_ratio, 0.85)
                    adjustments.append(f"历史偏差(保守): 系数{bias_ratio:.2f}")

        # 调整2: 信号矛盾惩罚
        if signal_contradiction:
            calibrated *= 0.85
            adjustments.append("信号矛盾:-15%")

        # 调整3: 数据质量
        quality_factors = {"good": 1.0, "normal": 1.0, "partial": 0.85, "poor": 0.70}
        factor = quality_factors.get(data_quality, 1.0)
        if factor < 1.0:
            calibrated *= factor
            adjustments.append(f"数据质量({data_quality}):{factor:.0%}")

        calibrated = max(0.05, min(0.95, calibrated))

        if abs(calibrated - raw_confidence) > 0.02:
            logger.info(f"[校准] {agent_name}: {raw_confidence:.0%}→{calibrated:.0%} ({'; '.join(adjustments)})")

        return {"calibrated": round(calibrated, 3), "original": raw_confidence, "adjustments": adjustments}

    def calibrate(
        self,
        agent_name: str,
        raw_confidence: float,
        data_quality: float = 1.0,
        timeframe: str = "短期",
    ) -> float:
        """Compatibility API returning only the calibrated probability."""
        try:
            quality = float(data_quality)
        except (TypeError, ValueError):
            quality = 1.0
        bucket = "good" if quality >= 0.8 else "normal" if quality >= 0.6 else "partial" if quality >= 0.4 else "poor"
        result = self.calibrate_confidence(
            raw_confidence=float(raw_confidence),
            agent_name=agent_name,
            timeframe=timeframe,
            data_quality=bucket,
        )
        return float(result["calibrated"])

    def calibrate_magnitude(
        self, predicted: Magnitude, atr_pct: float,
        adx: Optional[float] = None, timeframe: str = "短期",
    ) -> dict:
        """检查预测幅度是否合理"""
        weekly_vol = atr_pct * 2.24 if atr_pct > 0 else 3.0
        predicted_range = predicted.max_pct - predicted.min_pct
        warning = None
        new_mag = predicted

        if adx is not None and adx < 20 and predicted_range > weekly_vol * 2:
            warning = f"震荡市(ADX={adx})幅度偏宽"
            center = (predicted.min_pct + predicted.max_pct) / 2
            half = weekly_vol * 0.75
            new_mag = Magnitude(round(center - half, 1), round(center + half, 1))
        elif adx is not None and adx > 30 and predicted_range < weekly_vol * 0.5:
            warning = f"强趋势市(ADX={adx})幅度偏窄"
        elif predicted_range > weekly_vol * 5:
            warning = f"幅度({predicted_range:.1f}%)远超ATR({weekly_vol:.1f}%)"
            center = (predicted.min_pct + predicted.max_pct) / 2
            half = weekly_vol * 2
            new_mag = Magnitude(round(center - half, 1), round(center + half, 1))

        if warning:
            logger.info(f"[幅度] {warning} | {predicted.range_str} → {new_mag.range_str}")

        return {"magnitude": new_mag, "warning": warning, "atr_weekly_volatility": round(weekly_vol, 1)}
