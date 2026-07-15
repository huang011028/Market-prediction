"""
预测目标规格。

把口头的“短期/中期/长期、看涨/看跌/中性”落到可回测的目标定义：
固定周期收益 + 窗口障碍触达 + 可选相对收益。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional


@dataclass
class PredictionTargetSpec:
    """一次预测要验证的明确目标。"""

    target_version: str = "v3.1"
    horizon: str = "5d"
    horizon_trading_days: int = 5
    horizon_calendar_days: int = 7
    target_type: str = "residual_return"
    evaluation_mode: str = "fixed_horizon"
    label_mode: str = "fixed_horizon_beta_residual"
    price_basis: str = "as_of_close_to_horizon_close"
    benchmark_symbol: Optional[str] = None
    up_threshold_pct: float = 1.5
    down_threshold_pct: float = -1.5
    neutral_band_pct: float = 1.0
    volatility_scaled: bool = True
    volatility_lookback_days: int = 20
    volatility_multiplier: float = 0.75
    min_threshold_pct: float = 1.0
    max_threshold_pct: float = 12.0
    residualization_mode: str = "rolling_beta_market"
    beta_lookback_days: int = 120
    beta_min_observations: int = 20
    market_beta: Optional[float] = None
    industry_neutral: bool = False
    quantile_levels: tuple[float, float, float] = (0.1, 0.5, 0.9)
    expected_return_pct: Optional[float] = None
    expected_return_p10: Optional[float] = None
    expected_return_p50: Optional[float] = None
    expected_return_p90: Optional[float] = None
    prob_up: Optional[float] = None
    prob_down: Optional[float] = None
    prob_neutral: Optional[float] = None
    direction: str = "neutral"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | "PredictionTargetSpec" | None) -> "PredictionTargetSpec":
        if isinstance(data, cls):
            return data
        if not isinstance(data, dict):
            return cls()
        allowed = set(cls.__dataclass_fields__)
        payload = {key: value for key, value in data.items() if key in allowed}
        return cls(**payload)


def resolve_prediction_target(
    timeframe: str,
    direction: Any = "neutral",
    magnitude: Any = None,
    confidence: float = 0.0,
    prediction_target: dict | PredictionTargetSpec | None = None,
    target: str | None = None,
    market: str | None = None,
) -> PredictionTargetSpec:
    """把旧式预测字段补齐为新的目标规格。"""
    spec = default_target_spec(timeframe, target=target, market=market)
    explicit_probs = {}
    if prediction_target:
        if isinstance(prediction_target, dict):
            explicit_probs = {
                key: prediction_target.get(key)
                for key in ("prob_up", "prob_down", "prob_neutral")
                if prediction_target.get(key) is not None
            }
        incoming = PredictionTargetSpec.from_dict(prediction_target)
        for key, value in incoming.to_dict().items():
            if value is not None:
                setattr(spec, key, value)

    direction_value = _direction_value(direction)
    expected = spec.expected_return_pct
    if expected is None:
        expected = _expected_return_from_magnitude(magnitude)
    if expected is None:
        expected = _fallback_expected_return(direction_value, spec)

    spec.expected_return_pct = round(float(expected), 2)
    spec.direction = direction_from_return(spec.expected_return_pct, spec)

    # 如果旧 direction 是强方向且 expected return 没给出，就保留旧方向的语义。
    if prediction_target is None and direction_value in {"bullish", "bearish", "neutral"}:
        spec.direction = direction_value

    probs = (
        _normalize_probabilities(explicit_probs, spec.direction, confidence)
        if explicit_probs
        else _probabilities(spec.direction, confidence)
    )
    spec.prob_up = probs["bullish"]
    spec.prob_down = probs["bearish"]
    spec.prob_neutral = probs["neutral"]
    return spec


def default_target_spec(
    timeframe: str,
    target: str | None = None,
    market: str | None = None,
) -> PredictionTargetSpec:
    """按预测周期生成默认目标规格。"""
    text = str(timeframe or "").lower()
    if "长期" in text or "季度" in text or "季" in text:
        spec = PredictionTargetSpec(
            horizon="60d",
            horizon_trading_days=60,
            horizon_calendar_days=90,
            up_threshold_pct=6.0,
            down_threshold_pct=-6.0,
            neutral_band_pct=4.0,
        )
    elif "中期" in text or "月" in text:
        spec = PredictionTargetSpec(
            horizon="20d",
            horizon_trading_days=20,
            horizon_calendar_days=30,
            up_threshold_pct=3.0,
            down_threshold_pct=-3.0,
            neutral_band_pct=2.0,
        )
    else:
        spec = PredictionTargetSpec(
            horizon="5d",
            horizon_trading_days=5,
            horizon_calendar_days=7,
            up_threshold_pct=1.5,
            down_threshold_pct=-1.5,
            neutral_band_pct=1.0,
        )

    benchmark = benchmark_for_market(market or market_for_target(target))
    if benchmark:
        spec.benchmark_symbol = benchmark
        spec.target_type = "residual_return"
    else:
        spec.target_type = "absolute_return"
    return spec


def benchmark_for_market(market: str) -> Optional[str]:
    """根据市场给相对收益基准。当前数据源不可用时调用方可回退绝对收益。

    这里优先使用可被现有行情抓取器当作普通证券处理的 ETF/指数代理：
    A 股用沪深300 ETF，港股用盈富基金，美股用 SPY。
    """
    market = str(market or "").upper()
    if market == "A":
        return "510300"
    if market == "HK":
        return "2800"
    if market == "US":
        return "SPY"
    return None


def market_for_target(target: str | None) -> Optional[str]:
    if not target:
        return None
    text = str(target)
    candidates = [text]
    if "(" in text and ")" in text:
        inside = text.rsplit("(", 1)[-1].split(")", 1)[0].strip()
        if inside:
            candidates.insert(0, inside)
    try:
        from src.data.symbol_resolver import resolve_symbol

        for candidate in candidates:
            info = resolve_symbol(candidate)
            if info.symbol:
                return info.market
    except Exception:
        return None
    return None


def direction_from_return(return_pct: float, spec: PredictionTargetSpec) -> str:
    if return_pct >= spec.up_threshold_pct:
        return "bullish"
    if return_pct <= spec.down_threshold_pct:
        return "bearish"
    return "neutral"


def direction_correct(
    predicted_direction: str,
    fixed_horizon_return_pct: float,
    window_max_return_pct: Optional[float],
    window_min_return_pct: Optional[float],
    spec: PredictionTargetSpec | dict | None = None,
) -> bool:
    """判断方向是否命中。

    V3 使用固定到期收益生成唯一标签；窗口高低点仅用于风险诊断，避免
    同一窗口先后触及上下障碍时看涨和看跌都被记为正确。旧快照仍保留
    ``fixed_horizon_and_barrier`` 的兼容行为。
    """
    target = PredictionTargetSpec.from_dict(spec)
    predicted = str(predicted_direction or "neutral")
    if target.evaluation_mode == "fixed_horizon":
        return predicted == direction_from_return(float(fixed_horizon_return_pct), target)
    if window_max_return_pct is not None and window_min_return_pct is not None:
        if predicted == "bullish":
            return float(window_max_return_pct) >= target.up_threshold_pct
        if predicted == "bearish":
            return float(window_min_return_pct) <= target.down_threshold_pct
        no_barrier = (
            float(window_max_return_pct) < target.up_threshold_pct
            and float(window_min_return_pct) > target.down_threshold_pct
        )
        return no_barrier and abs(float(fixed_horizon_return_pct)) <= target.neutral_band_pct

    if predicted == "bullish":
        return float(fixed_horizon_return_pct) >= target.up_threshold_pct
    if predicted == "bearish":
        return float(fixed_horizon_return_pct) <= target.down_threshold_pct
    return abs(float(fixed_horizon_return_pct)) <= target.neutral_band_pct


def target_spec_for_volatility(
    spec: PredictionTargetSpec | dict | None,
    daily_volatility_pct: Optional[float],
) -> PredictionTargetSpec:
    """用预测时点已知波动率生成样本级阈值。

    传入的是日收益标准差百分比。阈值按 ``sqrt(horizon)`` 缩放，并被
    min/max 限制，避免低波动股票阈值过窄或极端行情阈值失控。
    """
    target = PredictionTargetSpec.from_dict(spec)
    if not target.volatility_scaled or daily_volatility_pct in (None, 0):
        return target
    try:
        daily_vol = abs(float(daily_volatility_pct))
    except (TypeError, ValueError):
        return target
    if daily_vol <= 0:
        return target

    import math

    threshold = daily_vol * math.sqrt(max(1, int(target.horizon_trading_days)))
    threshold *= max(0.05, float(target.volatility_multiplier))
    threshold = max(float(target.min_threshold_pct), threshold)
    threshold = min(float(target.max_threshold_pct), threshold)
    threshold = round(threshold, 2)
    target.up_threshold_pct = threshold
    target.down_threshold_pct = -threshold
    target.neutral_band_pct = round(min(threshold * 0.67, threshold), 2)
    return target


def _direction_value(direction: Any) -> str:
    value = getattr(direction, "value", direction)
    value = str(value or "neutral").lower().strip()
    return value if value in {"bullish", "bearish", "neutral"} else "neutral"


def _expected_return_from_magnitude(magnitude: Any) -> Optional[float]:
    if magnitude is None:
        return None
    if hasattr(magnitude, "mid_pct"):
        return float(magnitude.mid_pct)
    if isinstance(magnitude, dict):
        try:
            return (float(magnitude["min_pct"]) + float(magnitude["max_pct"])) / 2
        except (KeyError, TypeError, ValueError):
            return None
    return None


def _fallback_expected_return(direction: str, spec: PredictionTargetSpec) -> float:
    if direction == "bullish":
        return spec.up_threshold_pct
    if direction == "bearish":
        return spec.down_threshold_pct
    return 0.0


def _probabilities(direction: str, confidence: float) -> dict[str, float]:
    try:
        conf = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        conf = 0.0

    residual = 1.0 - conf
    if direction == "bullish":
        probs = {"bullish": conf, "neutral": residual * 0.65, "bearish": residual * 0.35}
    elif direction == "bearish":
        probs = {"bearish": conf, "neutral": residual * 0.65, "bullish": residual * 0.35}
    else:
        probs = {"neutral": conf, "bullish": residual * 0.5, "bearish": residual * 0.5}

    total = sum(probs.values()) or 1.0
    return {key: round(value / total, 4) for key, value in probs.items()}


def _normalize_probabilities(
    explicit: dict,
    direction: str,
    confidence: float,
) -> dict[str, float]:
    fallback = _probabilities(direction, confidence)
    values = {
        "bullish": _coerce_probability(explicit.get("prob_up"), fallback["bullish"]),
        "bearish": _coerce_probability(explicit.get("prob_down"), fallback["bearish"]),
        "neutral": _coerce_probability(explicit.get("prob_neutral"), fallback["neutral"]),
    }
    total = sum(values.values()) or 1.0
    return {key: round(value / total, 4) for key, value in values.items()}


def _coerce_probability(value, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number > 1.0 and number <= 100.0:
        number /= 100.0
    return max(0.0, min(1.0, number))
