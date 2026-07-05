"""技术面纯函数特征。

这些函数不依赖 LLM，便于用固定 K 线 fixture 做稳定测试。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

import pandas as pd


REQUIRED_OHLCV_COLUMNS = ("open", "high", "low", "close", "volume")


@dataclass
class TechnicalSnapshot:
    """技术面证据包。

    先由代码计算可追溯证据，再交给 LLM 解释，避免让 LLM 凭空判断技术形态。
    """

    symbol: str
    name: str
    market: str
    latest_date: str | None
    trading_days: int
    data_quality: dict[str, Any]
    close_series_summary: dict[str, Any]
    trend_regime: dict[str, Any]
    momentum_signals: dict[str, Any]
    volume_signals: dict[str, Any]
    volatility_signals: dict[str, Any]
    support_resistance: dict[str, Any]
    risk_levels: dict[str, Any]
    confidence_model: dict[str, Any]
    evidence: dict[str, list[str]]
    recent_trend: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_recent_trend(df: pd.DataFrame, points: int = 30) -> list[dict[str, Any]]:
    """返回前端可直接画线的近期走势。"""
    if df is None or df.empty or "close" not in df.columns:
        return []

    trend = []
    sample = df.tail(points)
    first_close = float(sample["close"].iloc[0]) if len(sample) else 0.0

    for idx, row in sample.iterrows():
        close = float(row["close"])
        date_value = idx
        if isinstance(date_value, pd.Timestamp):
            date_str = date_value.date().isoformat()
        elif isinstance(date_value, datetime):
            date_str = date_value.date().isoformat()
        else:
            date_str = str(date_value)[:10]

        change_from_start = ((close / first_close - 1) * 100) if first_close else 0.0
        trend.append({
            "date": date_str,
            "close": round(close, 2),
            "volume": float(row.get("volume", 0) or 0),
            "change_pct": round(change_from_start, 2),
        })
    return trend


def build_intraday_signals(trend: list[dict[str, Any]] | None) -> dict[str, Any]:
    """把分钟走势压缩成近期股价分析师可消费的盘中信号。"""
    if not trend or len(trend) < 2:
        return {
            "available": False,
            "state": "unavailable",
            "evidence": {"bullish": [], "bearish": [], "neutral": ["分钟级走势不可用"]},
        }

    points = [p for p in trend if _round(p.get("close")) is not None]
    if len(points) < 2:
        return {
            "available": False,
            "state": "unavailable",
            "evidence": {"bullish": [], "bearish": [], "neutral": ["分钟级走势格式不可用"]},
        }

    closes = [float(p["close"]) for p in points]
    volumes = [float(p.get("volume", 0) or 0) for p in points]
    first = closes[0]
    latest = closes[-1]
    high = max(closes)
    low = min(closes)
    total_volume = sum(v for v in volumes if v > 0)
    vwap = sum(c * max(v, 0) for c, v in zip(closes, volumes)) / total_volume if total_volume else None
    change_pct = _pct(latest, first) or 0.0
    intraday_range_pct = _pct(high, low) or 0.0
    pullback_from_high_pct = _pct(latest, high) or 0.0
    rebound_from_low_pct = _pct(latest, low) or 0.0
    latest_vs_vwap_pct = _pct(latest, vwap) if vwap else None

    tail_window = min(12, max(2, len(closes) // 4))
    late_change_pct = _pct(closes[-1], closes[-tail_window]) or 0.0
    early_window = min(12, len(closes) - 1)
    early_change_pct = _pct(closes[early_window], closes[0]) if early_window > 0 else 0.0

    bullish: list[str] = []
    bearish: list[str] = []
    neutral: list[str] = []

    if change_pct >= 2 and (latest_vs_vwap_pct is None or latest_vs_vwap_pct >= 0):
        bullish.append("分钟走势整体上行且价格不弱于均价")
    elif change_pct <= -2 and (latest_vs_vwap_pct is None or latest_vs_vwap_pct <= 0):
        bearish.append("分钟走势整体下行且价格不强于均价")
    else:
        neutral.append("分钟走势未形成明显单边方向")

    if pullback_from_high_pct <= -1.5:
        bearish.append("最新价较盘中高点明显回落")
    elif high and abs(pullback_from_high_pct) <= 0.4:
        bullish.append("最新价接近盘中高点")

    if latest_vs_vwap_pct is not None:
        if latest_vs_vwap_pct >= 0.5:
            bullish.append("最新价位于分钟均价上方")
        elif latest_vs_vwap_pct <= -0.5:
            bearish.append("最新价位于分钟均价下方")

    if abs(late_change_pct) <= 0.3 and intraday_range_pct >= 2:
        neutral.append("尾盘动量放缓，短线需要确认")
    elif late_change_pct >= 0.8:
        bullish.append("尾盘仍有上行动量")
    elif late_change_pct <= -0.8:
        bearish.append("尾盘仍有下行动量")

    if len(bullish) >= len(bearish) + 2:
        state = "strong_up"
        score = 0.75
    elif len(bearish) >= len(bullish) + 2:
        state = "selloff"
        score = -0.75
    elif bearish and bullish:
        state = "mixed"
        score = 0.0
    else:
        state = "range_bound"
        score = 0.0

    return {
        "available": True,
        "state": state,
        "score": score,
        "point_count": len(points),
        "first_time": points[0].get("time") or points[0].get("date"),
        "latest_time": points[-1].get("time") or points[-1].get("date"),
        "first_price": round(first, 2),
        "latest_price": round(latest, 2),
        "high": round(high, 2),
        "low": round(low, 2),
        "vwap": round(vwap, 2) if vwap else None,
        "change_pct": round(change_pct, 2),
        "intraday_range_pct": round(intraday_range_pct, 2),
        "pullback_from_high_pct": round(pullback_from_high_pct, 2),
        "rebound_from_low_pct": round(rebound_from_low_pct, 2),
        "latest_vs_vwap_pct": round(latest_vs_vwap_pct, 2) if latest_vs_vwap_pct is not None else None,
        "early_change_pct": round(early_change_pct or 0.0, 2),
        "late_change_pct": round(late_change_pct, 2),
        "evidence": {
            "bullish": bullish[:4],
            "bearish": bearish[:4],
            "neutral": neutral[:4],
        },
    }


def assess_price_data_quality(df: pd.DataFrame, max_staleness_days: int = 3) -> dict[str, Any]:
    """评估 K 线样本是否足以支撑技术面判断。"""
    issues: list[str] = []
    score = 1.0

    if df is None or df.empty:
        return {
            "status": "failed",
            "score": 0.0,
            "issues": ["无 K 线数据"],
            "trading_days": 0,
            "latest_date": None,
            "missing_columns": list(REQUIRED_OHLCV_COLUMNS),
        }

    missing = [col for col in REQUIRED_OHLCV_COLUMNS if col not in df.columns]
    if missing:
        issues.append(f"缺少字段: {', '.join(missing)}")
        score -= 0.45

    trading_days = len(df)
    insufficient_days = trading_days < 60
    if insufficient_days:
        issues.append(f"交易日不足 60 个: {trading_days}")
        score -= 0.35

    latest_date = None
    if isinstance(df.index, pd.DatetimeIndex) and len(df.index) > 0:
        latest_ts = df.index.max()
        latest_date = latest_ts.date().isoformat()
        staleness = (pd.Timestamp.today().normalize() - latest_ts.normalize()).days
        if staleness > max_staleness_days:
            issues.append(f"最新交易日距今 {staleness} 天")
            score -= 0.2
    else:
        issues.append("缺少日期索引")
        score -= 0.1

    if "volume" in df.columns and float(df["volume"].tail(20).fillna(0).sum()) <= 0:
        issues.append("成交量缺失或全为 0")
        score -= 0.15

    score = max(0.0, min(1.0, round(score, 2)))
    if insufficient_days:
        score = min(score, 0.35)
    if insufficient_days:
        status = "failed"
    elif score >= 0.75:
        status = "ok"
    elif score >= 0.4:
        status = "degraded"
    else:
        status = "failed"

    return {
        "status": status,
        "score": score,
        "issues": issues,
        "trading_days": trading_days,
        "latest_date": latest_date,
        "missing_columns": missing,
    }


def describe_market_freshness(
    latest_date: str | None,
    *,
    market: str = "",
    intraday_latest_time: str | None = None,
    now: pd.Timestamp | None = None,
) -> dict[str, Any]:
    """解释行情新鲜度，区分交易日、周末休市和分钟级更新时间。"""
    now = (now or pd.Timestamp.now()).tz_localize(None) if getattr(now or pd.Timestamp.now(), "tzinfo", None) else (now or pd.Timestamp.now())
    today = now.normalize()
    weekday = today.weekday()
    market_upper = (market or "").upper()

    latest_ts = None
    if latest_date:
        try:
            latest_ts = pd.to_datetime(latest_date).normalize()
        except Exception:
            latest_ts = None

    age_days = int((today - latest_ts).days) if latest_ts is not None else None
    is_weekend = weekday >= 5
    is_a_share = market_upper == "A"
    if intraday_latest_time:
        status = "intraday"
        note = f"分钟级走势最新到 {intraday_latest_time}"
        if is_a_share and is_weekend and age_days is not None and 0 <= age_days <= 3:
            status = "intraday_market_closed"
            note += f"；当前为周末/休市日，A股日线最新交易日为 {latest_date}"
        elif latest_date:
            note += f"，日线最新交易日 {latest_date}"
    elif is_a_share and is_weekend and age_days is not None and 0 <= age_days <= 3:
        status = "market_closed"
        note = f"当前为周末/休市日，A股日线最新交易日为 {latest_date}"
    elif age_days == 0:
        status = "fresh"
        note = f"日线已更新至今日 {latest_date}"
    elif age_days is not None and age_days <= 3:
        status = "recent"
        note = f"日线最新交易日 {latest_date}，距今 {age_days} 天"
    elif age_days is not None:
        status = "stale"
        note = f"日线最新交易日 {latest_date}，距今 {age_days} 天，可能偏旧"
    else:
        status = "unknown"
        note = "未提供行情更新时间"

    return {
        "status": status,
        "note": note,
        "latest_date": latest_date,
        "intraday_latest_time": intraday_latest_time,
        "age_days": age_days,
        "market_closed": bool(status in {"market_closed", "intraday_market_closed"}),
    }


def build_technical_snapshot(
    df: pd.DataFrame,
    *,
    indicators: dict[str, Any] | None = None,
    data_quality: dict[str, Any] | None = None,
    recent_trend: list[dict[str, Any]] | None = None,
    symbol: str = "",
    name: str = "",
    market: str = "",
) -> dict[str, Any]:
    """构建近期股价分析师使用的结构化技术证据包。"""
    indicators = indicators or {}
    recent_trend = recent_trend if recent_trend is not None else build_recent_trend(df)
    data_quality = data_quality or assess_price_data_quality(df)

    if df is None or df.empty or "close" not in df.columns:
        empty = TechnicalSnapshot(
            symbol=symbol,
            name=name,
            market=market,
            latest_date=data_quality.get("latest_date"),
            trading_days=0,
            data_quality=data_quality,
            close_series_summary={},
            trend_regime={"short_term": "unknown", "medium_term": "unknown"},
            momentum_signals={},
            volume_signals={},
            volatility_signals={},
            support_resistance={},
            risk_levels={},
            confidence_model={
                "technical_confidence": 0.0,
                "signal_consistency_score": 0.0,
                "risk_reward_score": 0.0,
                "backtest_prior_score": 0.5,
                "suggested_direction": "neutral",
                "hard_caps": ["无可用 K 线数据"],
            },
            evidence={"bullish": [], "bearish": [], "neutral": ["无可用 K 线数据"]},
            recent_trend=recent_trend,
        )
        return empty.to_dict()

    df = _ensure_sorted_datetime_index(df)
    close = df["close"].astype(float)
    high = df.get("high", close).astype(float)
    low = df.get("low", close).astype(float)
    volume = df.get("volume", pd.Series([0] * len(df), index=df.index)).fillna(0).astype(float)

    latest_close = float(close.iloc[-1])
    latest_date = _date_str(df.index[-1])
    close_summary = _close_series_summary(close)
    trend_regime = _trend_regime(close)
    momentum_signals = _momentum_signals(close, indicators)
    volume_signals = _volume_signals(close, volume)
    volatility_signals = _volatility_signals(close, high, low, indicators)
    support_resistance = _support_resistance(close, high, low, indicators)
    risk_levels = _risk_levels(latest_close, support_resistance)
    evidence = _build_evidence(
        trend_regime,
        momentum_signals,
        volume_signals,
        volatility_signals,
        support_resistance,
        risk_levels,
    )
    confidence_model = _confidence_model(
        data_quality=data_quality,
        trend_regime=trend_regime,
        momentum_signals=momentum_signals,
        volume_signals=volume_signals,
        support_resistance=support_resistance,
        risk_levels=risk_levels,
        evidence=evidence,
    )

    snapshot = TechnicalSnapshot(
        symbol=symbol,
        name=name,
        market=market,
        latest_date=latest_date,
        trading_days=len(df),
        data_quality=data_quality,
        close_series_summary=close_summary,
        trend_regime=trend_regime,
        momentum_signals=momentum_signals,
        volume_signals=volume_signals,
        volatility_signals=volatility_signals,
        support_resistance=support_resistance,
        risk_levels=risk_levels,
        confidence_model=confidence_model,
        evidence=evidence,
        recent_trend=recent_trend,
    )
    return snapshot.to_dict()


def _ensure_sorted_datetime_index(df: pd.DataFrame) -> pd.DataFrame:
    if "date" in df.columns:
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
    if not isinstance(df.index, pd.DatetimeIndex):
        df = df.copy()
        df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df = df.copy()
        df.index = df.index.tz_localize(None)
    return df.sort_index()


def _date_str(value: Any) -> str:
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.date().isoformat()
    return str(value)[:10]


def _round(value: Any, digits: int = 2) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(value):
        return None
    return round(value, digits)


def _pct(current: float, base: float) -> float | None:
    if not base:
        return None
    return round((current / base - 1) * 100, 2)


def _close_series_summary(close: pd.Series) -> dict[str, Any]:
    latest = float(close.iloc[-1])
    return {
        "latest_close": round(latest, 2),
        "change_1d_pct": _pct(latest, float(close.iloc[-2])) if len(close) >= 2 else None,
        "change_5d_pct": _pct(latest, float(close.iloc[-6])) if len(close) >= 6 else None,
        "change_20d_pct": _pct(latest, float(close.iloc[-21])) if len(close) >= 21 else None,
        "change_60d_pct": _pct(latest, float(close.iloc[-61])) if len(close) >= 61 else None,
        "close_20d_high": _round(close.tail(20).max()),
        "close_20d_low": _round(close.tail(20).min()),
        "close_60d_high": _round(close.tail(60).max()) if len(close) >= 60 else None,
        "close_60d_low": _round(close.tail(60).min()) if len(close) >= 60 else None,
    }


def _moving_average(close: pd.Series, window: int) -> pd.Series:
    return close.rolling(window=window, min_periods=window).mean()


def _slope_score(series: pd.Series, lookback: int = 5) -> float:
    values = series.dropna()
    if len(values) <= lookback:
        return 0.0
    current = float(values.iloc[-1])
    previous = float(values.iloc[-lookback - 1])
    if previous == 0:
        return 0.0
    raw = (current / previous - 1) * 20
    return round(max(-1.0, min(1.0, raw)), 3)


def _trend_regime(close: pd.Series) -> dict[str, Any]:
    ma5 = _moving_average(close, 5)
    ma10 = _moving_average(close, 10)
    ma20 = _moving_average(close, 20)
    ma60 = _moving_average(close, 60)
    latest = float(close.iloc[-1])

    ma5_last = _round(ma5.iloc[-1])
    ma10_last = _round(ma10.iloc[-1])
    ma20_last = _round(ma20.iloc[-1])
    ma60_last = _round(ma60.iloc[-1])
    ma20_slope = _slope_score(ma20)
    ma60_slope = _slope_score(ma60)

    if ma20_last is None:
        short_term = "unknown"
    elif latest > ma20.iloc[-1] and (ma5_last is None or ma5.iloc[-1] >= ma20.iloc[-1]) and ma20_slope > 0.03:
        short_term = "up"
    elif latest < ma20.iloc[-1] and (ma5_last is None or ma5.iloc[-1] <= ma20.iloc[-1]) and ma20_slope < -0.03:
        short_term = "down"
    else:
        short_term = "sideways"

    if ma60_last is None:
        medium_term = "unknown"
    elif latest > ma60.iloc[-1] and ma60_slope > 0.02:
        medium_term = "up"
    elif latest < ma60.iloc[-1] and ma60_slope < -0.02:
        medium_term = "down"
    else:
        medium_term = "sideways"

    ma_values = [ma5_last, ma10_last, ma20_last, ma60_last]
    if all(v is not None for v in ma_values) and ma5.iloc[-1] > ma10.iloc[-1] > ma20.iloc[-1] > ma60.iloc[-1]:
        ma_alignment = "bullish"
    elif all(v is not None for v in ma_values) and ma5.iloc[-1] < ma10.iloc[-1] < ma20.iloc[-1] < ma60.iloc[-1]:
        ma_alignment = "bearish"
    else:
        ma_alignment = "tangled"

    recent_high = close.tail(20).max()
    prior_high = close.iloc[-40:-20].max() if len(close) >= 40 else recent_high
    recent_low = close.tail(20).min()
    prior_low = close.iloc[-40:-20].min() if len(close) >= 40 else recent_low
    if recent_high > prior_high and recent_low >= prior_low:
        structure = "higher_high"
    elif recent_low < prior_low and recent_high <= prior_high:
        structure = "lower_low"
    else:
        structure = "range_bound"

    return {
        "short_term": short_term,
        "medium_term": medium_term,
        "ma_alignment": ma_alignment,
        "slope_score": ma20_slope,
        "ma20_slope_score": ma20_slope,
        "ma60_slope_score": ma60_slope,
        "structure": structure,
        "ma": {"MA5": ma5_last, "MA10": ma10_last, "MA20": ma20_last, "MA60": ma60_last},
    }


def _momentum_signals(close: pd.Series, indicators: dict[str, Any]) -> dict[str, Any]:
    latest = float(close.iloc[-1])
    ma20 = _moving_average(close, 20)
    score = 0
    notes: list[str] = []

    macd_signal = indicators.get("MACD_signal")
    if macd_signal in {"golden_cross", "bullish_holding"}:
        score += 1
        notes.append("MACD 偏多")
    elif macd_signal in {"death_cross", "bearish_holding"}:
        score -= 1
        notes.append("MACD 偏空")

    rsi = _round(indicators.get("RSI"), 2)
    if rsi is not None:
        if 55 <= rsi <= 70:
            score += 1
            notes.append("RSI 偏强但未极端")
        elif rsi > 75:
            score -= 1
            notes.append("RSI 过热")
        elif rsi < 45:
            score -= 1
            notes.append("RSI 偏弱")

    if len(ma20.dropna()) and latest > float(ma20.iloc[-1]):
        score += 1
        notes.append("收盘价站上 MA20")
    elif len(ma20.dropna()) and latest < float(ma20.iloc[-1]):
        score -= 1
        notes.append("收盘价跌破 MA20")

    if len(close) >= 6 and latest > float(close.iloc[-6]):
        score += 1
    elif len(close) >= 6 and latest < float(close.iloc[-6]):
        score -= 1

    if score >= 2:
        state = "bullish"
    elif score <= -2:
        state = "bearish"
    else:
        state = "mixed"

    return {
        "state": state,
        "score": max(-1.0, min(1.0, round(score / 4, 3))),
        "macd_signal": macd_signal,
        "rsi": rsi,
        "kdj_signal": indicators.get("KDJ_signal"),
        "notes": notes[:4],
    }


def _volume_signals(close: pd.Series, volume: pd.Series) -> dict[str, Any]:
    latest_volume = float(volume.iloc[-1]) if len(volume) else 0.0
    avg5 = float(volume.tail(5).mean()) if len(volume) >= 5 else 0.0
    avg20 = float(volume.tail(20).mean()) if len(volume) >= 20 else avg5
    prev20 = float(volume.iloc[-40:-20].mean()) if len(volume) >= 40 else avg20
    ratio5 = latest_volume / avg5 if avg5 else 0.0
    ratio20 = latest_volume / avg20 if avg20 else 0.0
    expansion_ratio = avg5 / prev20 if prev20 else 1.0
    price_change_1d = _pct(float(close.iloc[-1]), float(close.iloc[-2])) if len(close) >= 2 else 0.0

    price_up_volume_up = bool(price_change_1d is not None and price_change_1d > 0.3 and ratio5 >= 1.1)
    price_down_volume_up = bool(price_change_1d is not None and price_change_1d < -0.3 and ratio5 >= 1.1)
    abnormal_volume = ratio20 >= 2.0

    if expansion_ratio >= 1.15:
        volume_trend = "expanding"
    elif expansion_ratio <= 0.85:
        volume_trend = "shrinking"
    else:
        volume_trend = "neutral"

    if price_up_volume_up:
        interpretation = "bullish_confirmation"
        score = 0.7
    elif price_down_volume_up:
        interpretation = "bearish_pressure"
        score = -0.7
    elif volume_trend == "shrinking":
        interpretation = "low_conviction"
        score = 0.0
    else:
        interpretation = "neutral"
        score = 0.0

    return {
        "latest_volume": round(latest_volume, 2),
        "volume_ratio_5d": round(ratio5, 2),
        "volume_ratio_20d": round(ratio20, 2),
        "volume_trend": volume_trend,
        "price_change_1d_pct": price_change_1d,
        "price_up_volume_up": price_up_volume_up,
        "price_down_volume_up": price_down_volume_up,
        "abnormal_volume": abnormal_volume,
        "interpretation": interpretation,
        "score": score,
    }


def _volatility_signals(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    indicators: dict[str, Any],
) -> dict[str, Any]:
    atr = _round(indicators.get("ATR"), 3)
    atr_pct = _round(indicators.get("ATR_pct"), 2)
    if atr_pct is None and len(close) >= 15:
        prev_close = close.shift(1)
        tr = pd.concat([
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr = _round(tr.rolling(14).mean().iloc[-1], 3)
        atr_pct = _round((atr or 0) / float(close.iloc[-1]) * 100, 2)

    returns = close.pct_change().dropna()
    daily_vol_20 = _round(returns.tail(20).std() * 100, 2) if len(returns) >= 20 else None
    tail20 = close.tail(20)
    running_max = tail20.cummax()
    drawdown = (tail20 / running_max - 1) * 100
    max_drawdown_20d = _round(drawdown.min(), 2)

    if atr_pct is None:
        state = "unknown"
    elif atr_pct >= 4:
        state = "high"
    elif atr_pct <= 1.5:
        state = "low"
    else:
        state = "normal"

    return {
        "atr": atr,
        "atr_pct": atr_pct,
        "daily_volatility_20d_pct": daily_vol_20,
        "max_drawdown_20d_pct": max_drawdown_20d,
        "volatility_state": state,
    }


def _support_resistance(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    indicators: dict[str, Any],
) -> dict[str, Any]:
    latest = float(close.iloc[-1])
    support_candidates = [
        _round(low.tail(20).min()),
        _round(low.tail(60).min()) if len(low) >= 60 else None,
        _round(indicators.get("MA20")),
        _round(indicators.get("MA60")),
    ]
    resistance_candidates = [
        _round(high.tail(20).max()),
        _round(high.tail(60).max()) if len(high) >= 60 else None,
        _round(indicators.get("MA20")),
        _round(indicators.get("MA60")),
    ]

    supports = sorted({v for v in support_candidates if v is not None and v < latest})
    resistances = sorted({v for v in resistance_candidates if v is not None and v > latest})
    nearest_support = supports[-1] if supports else None
    nearest_resistance = resistances[0] if resistances else None

    support_distance = _pct(nearest_support, latest) if nearest_support else None
    resistance_distance = _pct(nearest_resistance, latest) if nearest_resistance else None

    return {
        "nearest_support": nearest_support,
        "nearest_resistance": nearest_resistance,
        "support_distance_pct": support_distance,
        "resistance_distance_pct": resistance_distance,
        "support_candidates": supports[-3:],
        "resistance_candidates": resistances[:3],
    }


def _risk_levels(latest_close: float, sr: dict[str, Any]) -> dict[str, Any]:
    support = sr.get("nearest_support")
    resistance = sr.get("nearest_resistance")
    stop_loss = round(support * 0.985, 2) if support else None
    breakout = round(resistance * 1.005, 2) if resistance else None
    downside = abs(sr.get("support_distance_pct") or 0)
    upside = abs(sr.get("resistance_distance_pct") or 0)
    risk_reward = round(upside / downside, 2) if downside > 0 and upside > 0 else None

    return {
        "latest_close": round(latest_close, 2),
        "stop_loss_reference": stop_loss,
        "breakout_reference": breakout,
        "downside_to_support_pct": round(downside, 2) if support else None,
        "upside_to_resistance_pct": round(upside, 2) if resistance else None,
        "risk_reward_ratio": risk_reward,
    }


def _build_evidence(
    trend: dict[str, Any],
    momentum: dict[str, Any],
    volume: dict[str, Any],
    volatility: dict[str, Any],
    sr: dict[str, Any],
    risk: dict[str, Any],
) -> dict[str, list[str]]:
    bullish: list[str] = []
    bearish: list[str] = []
    neutral: list[str] = []

    if trend.get("short_term") == "up":
        bullish.append("短期趋势向上")
    elif trend.get("short_term") == "down":
        bearish.append("短期趋势向下")
    else:
        neutral.append("短期趋势未形成单边方向")

    if trend.get("ma_alignment") == "bullish":
        bullish.append("均线多头排列")
    elif trend.get("ma_alignment") == "bearish":
        bearish.append("均线空头排列")
    else:
        neutral.append("均线缠绕")

    if momentum.get("state") == "bullish":
        bullish.append("动量指标偏多")
    elif momentum.get("state") == "bearish":
        bearish.append("动量指标偏空")
    else:
        neutral.append("动量指标分歧")

    if volume.get("price_up_volume_up"):
        bullish.append("上涨伴随放量")
    if volume.get("price_down_volume_up"):
        bearish.append("下跌伴随放量")
    if volume.get("volume_trend") == "shrinking":
        neutral.append("成交量收缩，方向确认度不足")

    resistance_distance = sr.get("resistance_distance_pct")
    support_distance = sr.get("support_distance_pct")
    if resistance_distance is not None and 0 <= resistance_distance <= 2:
        bearish.append("价格接近上方压力位")
    if support_distance is not None and -2 <= support_distance <= 0:
        bullish.append("价格接近下方支撑位")

    if volatility.get("volatility_state") == "high":
        neutral.append("波动率偏高，短线不确定性提高")
    if risk.get("risk_reward_ratio") is not None and risk["risk_reward_ratio"] < 0.8:
        bearish.append("上行空间相对下行风险不足")

    return {"bullish": bullish[:5], "bearish": bearish[:5], "neutral": neutral[:5]}


def _confidence_model(
    *,
    data_quality: dict[str, Any],
    trend_regime: dict[str, Any],
    momentum_signals: dict[str, Any],
    volume_signals: dict[str, Any],
    support_resistance: dict[str, Any],
    risk_levels: dict[str, Any],
    evidence: dict[str, list[str]],
) -> dict[str, Any]:
    trend_score = {"up": 1, "down": -1, "sideways": 0}.get(trend_regime.get("short_term"), 0)
    momentum_score = float(momentum_signals.get("score") or 0)
    volume_score = float(volume_signals.get("score") or 0)
    signed_scores = [trend_score, momentum_score, volume_score]
    non_zero = [s for s in signed_scores if abs(s) > 0.05]
    if non_zero:
        signal_consistency = abs(sum(non_zero)) / len(non_zero)
    else:
        signal_consistency = 0.25
    signal_consistency = max(0.0, min(1.0, round(signal_consistency, 3)))

    risk_reward = risk_levels.get("risk_reward_ratio")
    if risk_reward is None:
        risk_reward_score = 0.5
    elif risk_reward >= 1.5:
        risk_reward_score = 0.85
    elif risk_reward >= 1.0:
        risk_reward_score = 0.65
    elif risk_reward >= 0.7:
        risk_reward_score = 0.45
    else:
        risk_reward_score = 0.25

    backtest_prior_score = 0.5
    data_quality_score = float(data_quality.get("score", 0.0) or 0.0)
    technical_confidence = (
        data_quality_score * 0.35
        + signal_consistency * 0.35
        + risk_reward_score * 0.15
        + backtest_prior_score * 0.15
    )
    hard_caps: list[str] = []
    if data_quality.get("status") == "failed":
        technical_confidence = min(technical_confidence, 0.2)
        hard_caps.append("数据质量失败，置信度不超过 0.20")
    if signal_consistency < 0.45:
        technical_confidence = min(technical_confidence, 0.45)
        hard_caps.append("趋势/动量/量能信号不一致，置信度不超过 0.45")

    resistance_distance = support_resistance.get("resistance_distance_pct")
    if (
        resistance_distance is not None
        and 0 <= resistance_distance <= 2
        and not volume_signals.get("price_up_volume_up")
        and not volume_signals.get("abnormal_volume")
    ):
        technical_confidence = min(technical_confidence, 0.45)
        hard_caps.append("接近压力位且未放量突破，置信度不超过 0.45")

    if trend_regime.get("short_term") == "sideways" and volume_signals.get("volume_trend") == "shrinking":
        suggested_direction = "neutral"
    elif len(evidence.get("bullish", [])) > len(evidence.get("bearish", [])) + 1:
        suggested_direction = "bullish"
    elif len(evidence.get("bearish", [])) > len(evidence.get("bullish", [])) + 1:
        suggested_direction = "bearish"
    else:
        suggested_direction = "neutral"

    return {
        "technical_confidence": round(max(0.0, min(0.95, technical_confidence)), 3),
        "data_quality_score": round(data_quality_score, 3),
        "signal_consistency_score": signal_consistency,
        "risk_reward_score": round(risk_reward_score, 3),
        "backtest_prior_score": backtest_prior_score,
        "suggested_direction": suggested_direction,
        "hard_caps": hard_caps,
    }
