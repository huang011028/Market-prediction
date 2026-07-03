"""
历史估值分位计算模块

从多个数据源获取 PE/PB 的历史序列，计算当前估值在历史中的百分位。
这是基本面分析最核心的能力——回答"当前贵不贵"。
"""

import logging
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ValuationPercentile:
    """历史估值分位结果"""
    metric: str                  # "PE" or "PB"
    current_value: float
    percentile_3yr: Optional[float] = None   # 三年分位 0~1
    percentile_5yr: Optional[float] = None   # 五年分位 0~1
    historical_low: Optional[float] = None
    historical_high: Optional[float] = None
    historical_median: Optional[float] = None
    data_points: int = 0          # 历史数据点数
    interpretation: str = ""      # 文字解释


def percentile_of_score(history: list[float], current: float) -> Optional[float]:
    """
    计算当前值在历史序列中的百分位。

    Args:
        history: 历史值列表
        current: 当前值

    Returns:
        0.0 ~ 1.0，0% = 历史最低(最便宜), 100% = 历史最高(最贵)
        数据不足返回 None
    """
    if not history or current is None:
        return None

    clean = [h for h in history if h is not None and h > 0]
    if len(clean) < 30:  # 至少需要30个数据点
        return None

    count_below = sum(1 for h in clean if h <= current)
    return count_below / len(clean)


def calculate_valuation_percentile(
    current_value: float,
    history_3yr: list[float],
    history_5yr: list[float] = None,
    metric: str = "PE",
) -> ValuationPercentile:
    """
    计算估值历史分位。

    Args:
        current_value: 当前 PE 或 PB 值
        history_3yr: 3年历史序列
        history_5yr: 5年历史序列（可选，不提供则用3年代替）
        metric: "PE" 或 "PB"

    Returns:
        ValuationPercentile 结果
    """
    if history_5yr is None:
        history_5yr = history_3yr

    p3 = percentile_of_score(history_3yr, current_value)
    p5 = percentile_of_score(history_5yr, current_value)

    # 3年数据不足时退化为5年
    primary_pct = p3 if p3 is not None else p5

    # 生成解读
    if primary_pct is not None:
        if primary_pct < 0.10:
            interp = f"处于3年{primary_pct*100:.0f}%分位，估值极低，显著低估"
        elif primary_pct < 0.25:
            interp = f"处于3年{primary_pct*100:.0f}%分位，显著低于历史中枢，相对便宜"
        elif primary_pct < 0.40:
            interp = f"处于3年{primary_pct*100:.0f}%分位，低于历史中枢，估值合理偏低"
        elif primary_pct < 0.60:
            interp = f"处于3年{primary_pct*100:.0f}%分位，处于历史中枢，估值合理"
        elif primary_pct < 0.75:
            interp = f"处于3年{primary_pct*100:.0f}%分位，高于历史中枢，估值合理偏高"
        elif primary_pct < 0.90:
            interp = f"处于3年{primary_pct*100:.0f}%分位，显著高于历史中枢，相对偏贵"
        else:
            interp = f"处于3年{primary_pct*100:.0f}%分位，估值极高，显著高估"
    else:
        interp = "历史数据不足，无法计算分位"

    total_history = history_5yr if history_5yr else history_3yr
    clean_history = [h for h in total_history if h is not None and h > 0]

    return ValuationPercentile(
        metric=metric,
        current_value=current_value,
        percentile_3yr=p3,
        percentile_5yr=p5,
        historical_low=min(clean_history) if clean_history else None,
        historical_high=max(clean_history) if clean_history else None,
        historical_median=sorted(clean_history)[len(clean_history) // 2] if clean_history else None,
        data_points=len(clean_history),
        interpretation=interp,
    )


async def fetch_pe_history_akshare(symbol: str, market: str = "A") -> list[float]:
    """
    从 akshare 获取 PE 历史序列。

    Args:
        symbol: 股票代码
        market: "A" / "HK" / "US"

    Returns:
        PE 历史值列表
    """
    try:
        import akshare as ak

        if market == "A":
            code = symbol.zfill(6)
            try:
                df = ak.stock_a_indicator_lg(symbol=code)
                if df is not None and not df.empty and "pe" in df.columns:
                    pe_series = df["pe"].dropna()
                    pe_series = pe_series[pe_series > 0]
                    if len(pe_series) > 30:
                        logger.info(f"akshare PE历史: {code} 获取 {len(pe_series)} 条")
                        return pe_series.tolist()
            except Exception as e:
                logger.debug(f"akshare PE历史获取失败: {e}")

        # 港股/美股 akshare 接口有限，暂不支持
        return []

    except Exception as e:
        logger.debug(f"fetch_pe_history_akshare 异常: {e}")
        return []


def extract_pe_from_klines(klines: list[dict]) -> list[float]:
    """
    从日K线数据中提取PE历史。

    腾讯行情的每日K线中若包含PE字段，
    可以从历史K线反推PE序列。

    Args:
        klines: K线数据列表，每个元素含 "pe" 字段

    Returns:
        PE 历史值列表
    """
    if not klines:
        return []

    pe_values = []
    for kline in klines:
        pe = kline.get("pe")
        if pe and isinstance(pe, (int, float)) and pe > 0:
            pe_values.append(float(pe))

    return pe_values
