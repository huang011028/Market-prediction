"""
基本面数据预处理管线

对原始财务数据执行：
1. 历史估值分位计算（3年/5年）
2. 财务趋势提取（4-8季度同比/环比）
3. 质量评分卡生成（量化规则预打分）
4. 数据质量评估 + 新鲜度标注
5. 结构化摘要输出

核心设计理念：将原始数据加工为"洞察"（interpretation），
而非让 LLM 自己"算数"。
"""

import logging
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ================================================================
# 数据结构
# ================================================================


@dataclass
class FinancialTrend:
    """财务趋势判断结果"""
    revenue_trend: str = "insufficient_data"    # accelerating/decelerating/stable/declining/fluctuating
    profit_trend: str = "insufficient_data"
    margin_trend: str = "insufficient_data"     # expanding/compressing/stable
    roe_trend: str = "insufficient_data"
    earnings_quality: str = "unknown"           # improving/stable/deteriorating
    quarterly_revenue_yoy: list = field(default_factory=list)
    quarterly_profit_yoy: list = field(default_factory=list)
    quarterly_roe: list = field(default_factory=list)
    summary: str = ""


@dataclass
class QualityScorecard:
    """公司质量评分卡"""
    total: float = 0
    rating: str = "unknown"          # excellent/good/average/weak
    profitability: dict = field(default_factory=dict)
    growth: dict = field(default_factory=dict)
    valuation: dict = field(default_factory=dict)
    health: dict = field(default_factory=dict)


@dataclass
class DataQualityReport:
    """数据质量评估"""
    completeness: float = 0.0         # 0~1
    freshness: float = 1.0            # 0~1
    overall_quality: float = 0.0      # 0~1
    financial_fields_filled: str = "0/8"
    valuation_fields_filled: str = "0/4"
    data_gaps: list = field(default_factory=list)
    confidence_ceiling: float = 0.70


# ================================================================
# 财务趋势提取
# ================================================================


def judge_trend(series: list) -> str:
    """判断数值序列的趋势方向。

    返回:
        accelerating: 连续增长且增速加快
        growing: 连续增长
        decelerating: 增长但增速放缓
        declining: 连续下降
        stable: 波动不大
        fluctuating: 无明显趋势
        insufficient_data: 数据不足
    """
    clean = [s for s in series if s is not None and isinstance(s, (int, float))]
    if len(clean) < 3:
        return "insufficient_data"

    recent = clean[-3:]

    # 检查是否单调
    increasing = all(recent[i] >= recent[i-1] for i in range(1, len(recent)))
    decreasing = all(recent[i] <= recent[i-1] for i in range(1, len(recent)))

    if increasing:
        if len(clean) >= 5:
            # 比较前半段和后半段的增速（用百分比变化）
            mid = len(clean) // 2
            if clean[0] != 0 and clean[mid] != 0:
                early_growth = (clean[mid] - clean[0]) / abs(clean[0])
                late_growth = (clean[-1] - clean[mid]) / abs(clean[mid])
                if late_growth > early_growth * 1.3 and late_growth > 0.05:
                    return "accelerating"
                elif late_growth < early_growth * 0.7 and early_growth > 0.05:
                    return "decelerating"
        return "growing"
    elif decreasing:
        return "declining"
    else:
        # 计算波动幅度
        if len(clean) >= 3:
            avg = sum(clean[-4:]) / len(clean[-4:])
            max_deviation = max(abs(x - avg) / abs(avg) for x in clean[-4:] if avg != 0)
            if max_deviation < 0.1:
                return "stable"
        return "fluctuating"


def judge_margin_trend(revenue_series: list, profit_series: list) -> str:
    """判断利润率趋势。

    利润率 = 利润 / 营收。
    营收增长但利润不增长 = 利润率压缩 = 盈利质量下降。
    """
    rev_clean = [r for r in revenue_series if r is not None and isinstance(r, (int, float)) and r > 0]
    prof_clean = [p for p in profit_series if p is not None and isinstance(p, (int, float)) and p > 0]

    if len(rev_clean) < 3 or len(prof_clean) < 3:
        return "insufficient_data"

    # 计算最近几期的隐含利润率趋势
    # 如果营收增长但利润增速明显落后 → 利润率压缩
    rev_growth = (rev_clean[-1] / rev_clean[-3] - 1) if len(rev_clean) >= 3 and rev_clean[-3] != 0 else 0
    prof_growth = (prof_clean[-1] / prof_clean[-3] - 1) if len(prof_clean) >= 3 and prof_clean[-3] != 0 else 0

    if rev_growth > 0.05 and prof_growth < rev_growth * 0.5:
        return "compressing"
    elif prof_growth > rev_growth * 1.2:
        return "expanding"
    else:
        return "stable"


def judge_earnings_quality(revenue_series: list, profit_series: list) -> str:
    """判断盈利质量趋势。

    营收和利润同向增长 = 质量好
    营收增长但利润不增长/下降 = 质量下降
    营收下降但利润增长 = 可能是成本削减，不可持续
    """
    rev_clean = [r for r in revenue_series if r is not None and isinstance(r, (int, float))]
    prof_clean = [p for p in profit_series if p is not None and isinstance(p, (int, float))]

    if len(rev_clean) < 4 or len(prof_clean) < 4:
        return "unknown"

    rev_up = rev_clean[-1] > rev_clean[-4]
    prof_up = prof_clean[-1] > prof_clean[-4]

    if rev_up and prof_up:
        # 都在增长，看谁增得快
        rev_g = (rev_clean[-1] / rev_clean[-4] - 1) if rev_clean[-4] > 0 else 0
        prof_g = (prof_clean[-1] / prof_clean[-4] - 1) if prof_clean[-4] > 0 else 0
        if prof_g >= rev_g * 0.8:
            return "improving"
        else:
            return "stable"
    elif rev_up and not prof_up:
        return "deteriorating"
    elif not rev_up and prof_up:
        return "stable"  # 可能是降本增效，观察
    else:
        return "deteriorating"


def extract_financial_trend(financials: dict) -> FinancialTrend:
    """
    从财务数据字典中提取趋势。

    Args:
        financials: 包含 revenue_yoy_series, profit_yoy_series, roe_series 等
                   的字典，或包含单期数据的格式。

    Returns:
        FinancialTrend 结果
    """
    trend = FinancialTrend()

    # 尝试获取多季度序列
    rev_series = financials.get("revenue_yoy_series", [])
    prof_series = financials.get("profit_yoy_series", [])
    roe_series = financials.get("roe_series", [])

    # 如果没有序列，尝试从单期数据构建
    if not rev_series:
        rev_yoy = financials.get("revenue_yoy_pct")
        if rev_yoy and rev_yoy != "N/A":
            rev_series = [float(rev_yoy)]
    if not prof_series:
        prof_yoy = financials.get("profit_yoy_pct")
        if prof_yoy and prof_yoy != "N/A":
            prof_series = [float(prof_yoy)]
    if not roe_series:
        roe = financials.get("roe_pct")
        if roe and roe != "N/A":
            roe_series = [float(roe)]

    trend.revenue_trend = judge_trend(rev_series)
    trend.profit_trend = judge_trend(prof_series)
    trend.roe_trend = judge_trend(roe_series)
    trend.margin_trend = judge_margin_trend(rev_series, prof_series)
    trend.earnings_quality = judge_earnings_quality(rev_series, prof_series)

    # 保存序列用于 LLM 参考
    trend.quarterly_revenue_yoy = [round(x, 1) for x in rev_series if isinstance(x, (int, float))][-8:]
    trend.quarterly_profit_yoy = [round(x, 1) for x in prof_series if isinstance(x, (int, float))][-8:]
    trend.quarterly_roe = [round(x, 1) for x in roe_series if isinstance(x, (int, float))][-8:]

    # 生成摘要
    parts = []
    if trend.revenue_trend not in ("insufficient_data",):
        parts.append(f"营收{trend.revenue_trend}")
    if trend.profit_trend not in ("insufficient_data",):
        parts.append(f"利润{trend.profit_trend}")
    if trend.earnings_quality != "unknown":
        quality_map = {"improving": "改善", "stable": "稳定", "deteriorating": "恶化"}
        parts.append(f"盈利质量{quality_map.get(trend.earnings_quality, trend.earnings_quality)}")
    trend.summary = "，".join(parts) if parts else "数据不足"

    return trend


# ================================================================
# 质量评分卡
# ================================================================


def _safe_num(value) -> Optional[float]:
    """安全转换为 float"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        if value in ("N/A", "", "None", "null"):
            return None
        try:
            return float(value.replace("%", ""))
        except ValueError:
            return None
    return None


def generate_quality_scorecard(financials: dict, valuation: dict,
                                pe_percentile: float = None) -> QualityScorecard:
    """
    基于量化规则生成公司质量评分卡。满分 100，分 4 个维度。

    让 LLM 在已有量化分数上做判断，而不是凭直觉打分。
    """
    scorecard = QualityScorecard()
    total = 0

    # --- 维度1: 盈利能力 (30分) ---
    roe = _safe_num(financials.get("roe_pct"))
    net_margin = _safe_num(financials.get("net_margin_pct"))
    profit_score = 0

    if roe is not None:
        if roe > 25: profit_score += 18
        elif roe > 20: profit_score += 16
        elif roe > 15: profit_score += 13
        elif roe > 10: profit_score += 9
        elif roe > 5: profit_score += 5
        elif roe > 0: profit_score += 2

    if net_margin is not None:
        if net_margin > 30: profit_score += 12
        elif net_margin > 20: profit_score += 10
        elif net_margin > 10: profit_score += 7
        elif net_margin > 5: profit_score += 4
        elif net_margin > 0: profit_score += 2

    profit_entry = {"score": profit_score, "max": 30}
    if roe is not None:
        profit_entry["note"] = f"ROE {roe}%"
    scorecard.profitability = profit_entry
    total += profit_score

    # --- 维度2: 成长性 (25分) ---
    rev_growth = _safe_num(financials.get("revenue_yoy_pct"))
    profit_growth = _safe_num(financials.get("profit_yoy_pct"))
    growth_score = 0

    if rev_growth is not None:
        if rev_growth > 50: growth_score += 15
        elif rev_growth > 30: growth_score += 13
        elif rev_growth > 15: growth_score += 10
        elif rev_growth > 5: growth_score += 6
        elif rev_growth > 0: growth_score += 3
        else: growth_score -= 2

    if profit_growth is not None:
        if profit_growth > 50: growth_score += 10
        elif profit_growth > 30: growth_score += 9
        elif profit_growth > 15: growth_score += 7
        elif profit_growth > 5: growth_score += 4
        elif profit_growth > 0: growth_score += 2
        else: growth_score -= 3

    growth_entry = {"score": max(0, growth_score), "max": 25}
    if rev_growth is not None:
        growth_entry["note"] = f"营收增速 {rev_growth}%"
    scorecard.growth = growth_entry
    total += max(0, growth_score)

    # --- 维度3: 估值安全边际 (25分) ---
    val_score = 0
    if pe_percentile is not None:
        if pe_percentile < 0.10: val_score = 23
        elif pe_percentile < 0.25: val_score = 19
        elif pe_percentile < 0.40: val_score = 14
        elif pe_percentile < 0.60: val_score = 10
        elif pe_percentile < 0.75: val_score = 5
        elif pe_percentile < 0.90: val_score = 2
        else: val_score = 0

    val_entry = {"score": val_score, "max": 25}
    if pe_percentile is not None:
        val_entry["note"] = f"PE分位 {pe_percentile*100:.0f}%"
    scorecard.valuation = val_entry
    total += val_score

    # --- 维度4: 财务健康 (20分) ---
    health_score = 8  # 基础分
    trend_data = financials.get("_trend", {})
    if isinstance(trend_data, dict):
        if trend_data.get("earnings_quality") == "improving": health_score += 6
        elif trend_data.get("earnings_quality") == "deteriorating": health_score -= 4
        if trend_data.get("revenue_trend") in ("accelerating", "growing"): health_score += 4
        elif trend_data.get("revenue_trend") == "declining": health_score -= 3
        if trend_data.get("margin_trend") == "expanding": health_score += 2
        elif trend_data.get("margin_trend") == "compressing": health_score -= 2

    health_entry = {"score": max(0, min(20, health_score)), "max": 20}
    scorecard.health = health_entry
    total += max(0, min(20, health_score))

    # --- 汇总 ---
    scorecard.total = min(100, max(0, total))
    if scorecard.total >= 80: scorecard.rating = "excellent"
    elif scorecard.total >= 60: scorecard.rating = "good"
    elif scorecard.total >= 40: scorecard.rating = "average"
    else: scorecard.rating = "weak"

    return scorecard


# ================================================================
# 数据质量评估
# ================================================================


FINANCIAL_FIELDS = [
    "latest_revenue", "latest_net_profit", "revenue_yoy",
    "profit_yoy", "gross_margin", "net_margin", "roe", "eps"
]
VALUATION_FIELDS = ["pe", "pb", "market_cap", "dividend_yield"]


def assess_data_quality(financials: dict, valuation: dict) -> DataQualityReport:
    """评估获取到的财务数据的质量"""

    fin_filled = sum(1 for f in FINANCIAL_FIELDS if _safe_num(financials.get(f)) is not None)
    fin_total = len(FINANCIAL_FIELDS)

    val_filled = sum(1 for f in VALUATION_FIELDS if _safe_num(valuation.get(f)) is not None)
    val_total = len(VALUATION_FIELDS)

    completeness = (fin_filled + val_filled) / max(1, fin_total + val_total)

    # 新鲜度 (默认1.0，后续可根据报告期计算)
    freshness = 1.0

    overall = completeness * 0.7 + freshness * 0.3

    # 识别数据缺口
    gaps = []
    for f in FINANCIAL_FIELDS:
        if _safe_num(financials.get(f)) is None:
            gaps.append(f)
    for f in VALUATION_FIELDS:
        if _safe_num(valuation.get(f)) is None:
            gaps.append(f)

    return DataQualityReport(
        completeness=round(completeness, 2),
        freshness=round(freshness, 2),
        overall_quality=round(overall, 2),
        financial_fields_filled=f"{fin_filled}/{fin_total}",
        valuation_fields_filled=f"{val_filled}/{val_total}",
        data_gaps=gaps,
        confidence_ceiling=_calculate_confidence_ceiling(overall),
    )


def _calculate_confidence_ceiling(quality: float) -> float:
    """基于数据质量计算置信度上限"""
    if quality >= 0.8: return 0.85
    elif quality >= 0.6: return 0.70
    elif quality >= 0.4: return 0.55
    elif quality >= 0.2: return 0.40
    else: return 0.25


# ================================================================
# 价值陷阱检测
# ================================================================


def detect_value_trap(financials: dict, pe_percentile: float = None) -> dict:
    """
    检测"价值陷阱"风险：PE很低但基本面在恶化。

    价值陷阱特征:
    1. PE/PB 处于历史低位 (< 30%)
    2. 但 ROE 在下滑
    3. 营收负增长
    4. 利润率压缩

    Returns:
        {"is_trap": bool, "risk_level": str, "signals": [...]}
    """
    signals = []
    risk_score = 0

    # 条件1: 估值低
    is_cheap = pe_percentile is not None and pe_percentile < 0.30

    # 条件2: ROE 下滑
    roe = _safe_num(financials.get("roe_pct"))
    roe_series = financials.get("roe_series", [])
    roe_declining = False
    if len(roe_series) >= 3:
        clean_roe = [r for r in roe_series if isinstance(r, (int, float))]
        if len(clean_roe) >= 3 and clean_roe[-1] < clean_roe[-3]:
            roe_declining = True
            signals.append(f"ROE从{clean_roe[-3]}%下滑至{clean_roe[-1]}%")
            risk_score += 3

    # 条件3: 营收负增长
    rev_yoy = _safe_num(financials.get("revenue_yoy_pct"))
    if rev_yoy is not None and rev_yoy < -5:
        signals.append(f"营收同比下滑{abs(rev_yoy):.0f}%")
        risk_score += 3

    # 条件4: 利润率压缩
    margin_trend = financials.get("_margin_trend", "")
    if margin_trend == "compressing":
        signals.append("利润率持续压缩")
        risk_score += 2

    # 综合判断
    if is_cheap and risk_score >= 4:
        is_trap = True
        risk_level = "high"
    elif is_cheap and risk_score >= 2:
        is_trap = True
        risk_level = "moderate"
    elif is_cheap and len(roe_series) < 3:
        # 估值低但数据不足以判断趋势
        is_trap = False
        risk_level = "uncertain"
        signals.append("估值低但财务数据不足以判断是否价值陷阱")
    else:
        is_trap = False
        risk_level = "low"

    return {
        "is_trap": is_trap,
        "risk_level": risk_level,
        "signals": signals,
        "cheap_but_deteriorating": is_cheap and roe_declining,
    }
