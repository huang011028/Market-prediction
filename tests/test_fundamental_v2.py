"""
公司前景分析师 v2 单元测试

覆盖:
- 预处理管线（趋势提取、评分卡、质量评估、价值陷阱检测）
- Agent 解析逻辑
- 置信度校准
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.data.fundamental_preprocessor import (
    judge_trend,
    judge_margin_trend,
    judge_earnings_quality,
    extract_financial_trend,
    generate_quality_scorecard,
    assess_data_quality,
    detect_value_trap,
    _safe_num,
)
from src.data.valuation_history import (
    percentile_of_score,
    calculate_valuation_percentile,
)


# ================================================================
# 工具函数测试
# ================================================================


class TestSafeNum:
    def test_none_returns_none(self):
        assert _safe_num(None) is None

    def test_valid_int(self):
        assert _safe_num(42) == 42.0

    def test_valid_float(self):
        assert _safe_num(3.14) == 3.14

    def test_na_string(self):
        assert _safe_num("N/A") is None

    def test_empty_string(self):
        assert _safe_num("") is None

    def test_percent_string(self):
        assert _safe_num("3.5%") == 3.5

    def test_numeric_string(self):
        assert _safe_num("42.5") == 42.5


# ================================================================
# 趋势判断测试
# ================================================================


class TestJudgeTrend:
    def test_accelerating_growth(self):
        # 前半段增长慢(10→11=10%)，后半段增长快(11→20=82%)
        series = [10.0, 10.2, 10.5, 11.0, 13.0, 16.0, 20.0]
        result = judge_trend(series)
        assert result == "accelerating"

    def test_growing(self):
        series = [10, 11, 12, 13, 14]
        result = judge_trend(series)
        assert result == "growing"

    def test_declining(self):
        series = [15, 13, 11, 9, 7]
        result = judge_trend(series)
        assert result == "declining"

    def test_stable(self):
        series = [10, 10.1, 9.9, 10.2, 10.0]
        result = judge_trend(series)
        assert result == "stable"

    def test_fluctuating(self):
        series = [10, 20, 5, 15, 8]
        result = judge_trend(series)
        assert result == "fluctuating"

    def test_insufficient_data(self):
        assert judge_trend([]) == "insufficient_data"
        assert judge_trend([10]) == "insufficient_data"
        assert judge_trend([10, 12]) == "insufficient_data"

    def test_with_none_values(self):
        series = [None, 10, None, 12, 14]
        result = judge_trend(series)
        assert result == "growing"


class TestJudgeMarginTrend:
    def test_compressing(self):
        # 营收增长快但利润增长慢 → 利润率压缩
        revenue = [100, 110, 125, 140]
        profit = [50, 52, 53, 54]
        result = judge_margin_trend(revenue, profit)
        assert result == "compressing"

    def test_expanding(self):
        # 利润增长快于营收 → 利润率扩张
        revenue = [100, 110, 120, 130]
        profit = [50, 58, 68, 80]
        result = judge_margin_trend(revenue, profit)
        assert result == "expanding"

    def test_stable(self):
        revenue = [100, 110, 120, 130]
        profit = [50, 55, 60, 65]
        result = judge_margin_trend(revenue, profit)
        assert result == "stable"


class TestJudgeEarningsQuality:
    def test_improving(self):
        rev = [100, 110, 125, 140]
        prof = [50, 58, 70, 85]
        result = judge_earnings_quality(rev, prof)
        assert result == "improving"

    def test_deteriorating_rev_up_prof_down(self):
        rev = [100, 110, 125, 140]
        prof = [50, 48, 45, 42]
        result = judge_earnings_quality(rev, prof)
        assert result == "deteriorating"

    def test_deteriorating_both_down(self):
        rev = [140, 125, 110, 100]
        prof = [85, 70, 58, 50]
        result = judge_earnings_quality(rev, prof)
        assert result == "deteriorating"


# ================================================================
# 财务趋势提取测试
# ================================================================


class TestExtractFinancialTrend:
    def test_with_series(self):
        financials = {
            "revenue_yoy_series": [5.0, 5.2, 5.5, 6.0, 8.0, 12.0, 18.0],  # 前半段慢(5→6)，后半段快(6→18)
            "profit_yoy_series": [3.0, 3.1, 3.3, 3.5, 5.0, 10.0, 20.0],   # 加速增长
            "roe_series": [15, 16, 17, 18],                               # 稳定增长(仅4个点)
        }
        trend = extract_financial_trend(financials)
        assert trend.revenue_trend == "accelerating"
        assert trend.profit_trend == "accelerating"
        assert trend.roe_trend == "growing"
        assert trend.earnings_quality == "improving"
        assert len(trend.quarterly_revenue_yoy) == 7
        assert "营收" in trend.summary

    def test_with_single_values(self):
        financials = {
            "revenue_yoy_pct": 15,
            "profit_yoy_pct": 22,
            "roe_pct": 18,
        }
        trend = extract_financial_trend(financials)
        assert trend.revenue_trend == "insufficient_data"
        assert trend.profit_trend == "insufficient_data"


# ================================================================
# 评分卡测试
# ================================================================


class TestQualityScorecard:
    def test_excellent_company(self):
        financials = {
            "roe_pct": 22,
            "net_margin_pct": 25,
            "revenue_yoy_pct": 25,
            "profit_yoy_pct": 30,
        }
        valuation = {"pe": 15, "pb": 3.0}
        sc = generate_quality_scorecard(financials, valuation, pe_percentile=0.2)
        assert sc.rating in ("excellent", "good")
        assert sc.total >= 60
        assert sc.profitability["score"] > 20

    def test_weak_company(self):
        financials = {
            "roe_pct": 3,
            "net_margin_pct": 2,
            "revenue_yoy_pct": -5,
            "profit_yoy_pct": -10,
        }
        valuation = {"pe": 50, "pb": 8.0}
        sc = generate_quality_scorecard(financials, valuation, pe_percentile=0.85)
        assert sc.rating == "weak"
        assert sc.total < 40

    def test_missing_data(self):
        financials = {}
        valuation = {}
        sc = generate_quality_scorecard(financials, valuation)
        assert sc.total == 0
        assert sc.rating == "unknown"
        assert sc.profitability["not_scorable"] is True

    def test_pe_percentile_impact(self):
        """估值分位越低，估值维度得分越高"""
        financials = {"roe_pct": 15, "net_margin_pct": 10, "revenue_yoy_pct": 10}
        valuation = {}

        sc_cheap = generate_quality_scorecard(financials, valuation, pe_percentile=0.15)
        sc_expensive = generate_quality_scorecard(financials, valuation, pe_percentile=0.90)

        assert sc_cheap.valuation["score"] > sc_expensive.valuation["score"]


# ================================================================
# 数据质量评估测试
# ================================================================


class TestDataQualityAssessment:
    def test_full_data(self):
        financials = {
            "latest_revenue": 100, "latest_net_profit": 20,
            "revenue_yoy": 15, "profit_yoy": 22,
            "gross_margin": 50, "net_margin": 20,
            "roe": 18, "eps": 2.5,
        }
        valuation = {"pe": 15, "pb": 3.0, "market_cap": 1000, "dividend_yield": 2.5}
        report = assess_data_quality(financials, valuation)
        assert report.completeness >= 0.9
        assert report.confidence_ceiling == 0.85

    def test_agent_output_field_aliases(self):
        financials = {
            "latest_revenue_100m": 100, "latest_net_profit_100m": 20,
            "revenue_yoy_pct": 15, "profit_yoy_pct": 22,
            "gross_margin_pct": 50, "net_margin_pct": 20,
            "roe_pct": 18, "eps": 2.5,
        }
        valuation = {"pe": 15, "pb": 3.0, "market_cap_100m": 1000, "dividend_yield_pct": 2.5}
        report = assess_data_quality(financials, valuation)
        assert report.completeness >= 0.9
        assert report.data_gaps == []

    def test_partial_data(self):
        financials = {"roe_pct": 15}  # 只有一个财务字段
        valuation = {"pe": 20, "pb": 3.0}  # 只有两个估值字段
        report = assess_data_quality(financials, valuation)
        assert report.completeness < 0.5
        assert "roe" in report.data_gaps or report.completeness < 0.4


# ================================================================
# 价值陷阱检测测试
# ================================================================


class TestValueTrapDetection:
    def test_value_trap_detected(self):
        """低PE + ROE下滑 + 营收负增长 → 价值陷阱"""
        financials = {
            "revenue_yoy_pct": -8,  # 营收下滑 >5 → +3 信号分
            "profit_yoy_pct": -15,
            "roe_series": [15, 12, 10, 8],  # ROE下滑 → +3 信号分
            "_margin_trend": "compressing",  # 利润率压缩 → +2 信号分
        }
        trap = detect_value_trap(financials, pe_percentile=0.10)
        assert trap["is_trap"] is True
        assert trap["risk_level"] in ("high", "moderate")  # 信号分>=4为moderate, >=4+cheap为trap
        assert any("ROE" in s for s in trap["signals"])

    def test_no_trap_good_company(self):
        """高ROE + 营收增长 + 低PE → 机会不是陷阱"""
        financials = {
            "revenue_yoy_pct": 15,
            "profit_yoy_pct": 20,
            "roe_series": [15, 16, 17, 18],
        }
        trap = detect_value_trap(financials, pe_percentile=0.15)
        assert trap["is_trap"] is False
        assert trap["risk_level"] in ("low", "uncertain")

    def test_data_insufficient(self):
        """数据不足时不判断为陷阱"""
        financials = {"roe_series": [None, None]}
        trap = detect_value_trap(financials, pe_percentile=0.20)
        assert trap["risk_level"] == "uncertain"


# ================================================================
# 估值分位测试
# ================================================================


class TestValuationPercentile:
    def test_percentile_calculation(self):
        history = list(range(1, 101))  # 1, 2, ..., 100
        # 当前值 25 → 25% 分位
        p = percentile_of_score(history, 25)
        assert p == 0.25

        # 当前值 90 → 90% 分位
        p = percentile_of_score(history, 90)
        assert p == 0.90

    def test_insufficient_history(self):
        """数据不足30个时返回 None"""
        assert percentile_of_score([1, 2, 3], 2) is None

    def test_empty_history(self):
        assert percentile_of_score([], 50) is None

    def test_valuation_percentile_good(self):
        history = [10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30,
                   15, 17, 19, 21, 23, 25, 27, 29, 31,
                   11, 13, 15, 17, 19, 21, 23, 25, 27, 29]
        vp = calculate_valuation_percentile(15, history)
        assert vp.percentile_3yr is not None
        assert vp.percentile_3yr < 0.3  # 15 在序列中偏小
        assert "便宜" in vp.interpretation or "低估" in vp.interpretation

    def test_valuation_percentile_expensive(self):
        history = [10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30,
                   15, 17, 19, 21, 23, 25, 27, 29, 31,
                   11, 13, 15, 17, 19, 21, 23, 25, 27, 29]
        vp = calculate_valuation_percentile(35, history)  # 35 超出历史范围
        assert vp.percentile_3yr is not None
        assert vp.percentile_3yr >= 0.90  # 应该处于极高位置
        # "高估" 包含 "贵"
        assert "高估" in vp.interpretation or "贵" in vp.interpretation


# ================================================================
# 集成测试
# ================================================================


class TestIntegration:
    """端到端测试: 原始数据 → 预处理 → 结构化输出"""

    def test_full_pipeline_excellent_company(self):
        """优质公司的完整管线"""
        financials_input = {
            "roe_pct": 18,
            "net_margin_pct": 22,
            "revenue_yoy_pct": 15,
            "profit_yoy_pct": 22,
            "gross_margin_pct": 55,
            "eps": 3.5,
            "revenue_yoy_series": [8, 10, 12, 15],
            "profit_yoy_series": [5, 8, 14, 22],
            "roe_series": [15, 16, 17, 18],
        }
        valuation_input = {"pe": 15, "pb": 3.0, "market_cap": 800, "dividend_yield": 2.0}

        # 1. 趋势
        trend = extract_financial_trend(financials_input)
        assert trend.earnings_quality == "improving"

        # 2. 评分卡
        sc = generate_quality_scorecard(financials_input, valuation_input, pe_percentile=0.18)
        assert sc.total >= 60
        assert sc.rating in ("excellent", "good")

        # 3. 价值陷阱
        trap = detect_value_trap(financials_input, pe_percentile=0.18)
        assert trap["is_trap"] is False

        # 4. 数据质量
        dq = assess_data_quality(financials_input, valuation_input)
        assert dq.overall_quality > 0.5

    def test_full_pipeline_data_poor(self):
        """数据严重缺失的完整管线"""
        financials_input = {}  # 无任何财务数据
        valuation_input = {"pe": 20}  # 只有PE

        trend = extract_financial_trend(financials_input)
        assert trend.revenue_trend == "insufficient_data"

        sc = generate_quality_scorecard(financials_input, valuation_input)
        assert sc.total < 20  # 数据缺失应得低分

        dq = assess_data_quality(financials_input, valuation_input)
        assert dq.confidence_ceiling <= 0.40
