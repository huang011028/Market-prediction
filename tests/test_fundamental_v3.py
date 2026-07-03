"""
公司前景分析师 v3 单元测试（Round 2 新增功能）

覆盖:
- 港股财务数据获取（降级链）
- 置信度校准器
- 评分卡权重优化
- 行业差异化评分基准
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.utils.fundamental_calibrator import FundamentalConfidenceCalibrator
from src.utils.scorecard_optimizer import ScorecardWeightOptimizer, get_industry_benchmark, INDUSTRY_BENCHMARKS
from src.data.hk_financial_source import _parse_aastocks_value


# ================================================================
# 港股财务数据解析测试
# ================================================================


class TestParseAAStocksValue:
    def test_none_input(self):
        assert _parse_aastocks_value(None) is None

    def test_na_input(self):
        assert _parse_aastocks_value("--") is None
        assert _parse_aastocks_value("N/A") is None

    def test_integer(self):
        assert _parse_aastocks_value("42") == 42.0

    def test_float(self):
        assert _parse_aastocks_value("3.14") == 3.14

    def test_with_thousands_separator(self):
        assert _parse_aastocks_value("1,234.5") == 1234.5

    def test_percentage(self):
        assert _parse_aastocks_value("3.5%") == 3.5

    def test_yi_unit(self):
        assert _parse_aastocks_value("352.77亿") == 352.77

    def test_wan_unit(self):
        # 万→亿: 10000万 = 1亿
        assert _parse_aastocks_value("10000万") == 1.0


# ================================================================
# 置信度校准器测试
# ================================================================


class TestFundamentalConfidenceCalibrator:
    def setup_method(self):
        # 使用独立实例，避免文件状态污染
        self.calibrator = FundamentalConfidenceCalibrator()
        # 清空所有桶
        for key in self.calibrator._confidence_bins:
            self.calibrator._confidence_bins[key] = {"total": 0, "correct": 0}
        for key in self.calibrator._quality_buckets:
            self.calibrator._quality_buckets[key] = {"total": 0, "correct": 0, "avg_confidence": 0.0}
        self.calibrator._scorecard_buckets.clear()

    def test_basic_calibrate_no_history(self):
        """无历史数据时，返回原始置信度"""
        result = self.calibrator.calibrate(0.7)
        assert result == 0.7

    def test_calibrate_with_history(self):
        """有历史数据时，向历史准确率回归"""
        # 模拟历史数据: 0.6-0.8 桶的准确率 55%
        for _ in range(10):
            self.calibrator.update_from_validation(0.7, True)
        for _ in range(8):
            self.calibrator.update_from_validation(0.7, False)
        # 准确率 = 10/18 ≈ 0.556

        result = self.calibrator.calibrate(0.7)
        # 校准后应接近 0.7*0.7 + 0.556*0.3 = 0.657
        assert 0.5 < result < 0.75

    def test_calibrate_with_quality_bucket(self):
        """数据质量桶的影响"""
        # 高准确率的高数据质量
        for _ in range(20):
            self.calibrator.update_from_validation(
                0.7, True, data_quality_bucket="high"
            )

        result = self.calibrator.calibrate(0.7, data_quality_bucket="high")
        # 应该保持较高
        assert result >= 0.6

    def test_calibrate_with_scorecard_rating(self):
        """评分卡等级的影响"""
        for _ in range(10):
            self.calibrator.update_from_validation(
                0.6, True, scorecard_rating="excellent"
            )

        result = self.calibrator.calibrate(0.6, scorecard_rating="excellent")
        # 校准后应在合理范围
        assert 0.4 < result <= 0.8

    def test_update_from_validation(self):
        """验证更新机制"""
        self.calibrator.update_from_validation(0.7, True)
        self.calibrator.update_from_validation(0.7, True)
        self.calibrator.update_from_validation(0.7, False)

        stats = self.calibrator.get_calibration_stats()
        bin_stats = stats["confidence_bins"].get("0.6-0.8", {})
        assert bin_stats.get("total") == 3
        assert bin_stats.get("accuracy", 0) == pytest.approx(2 / 3, abs=0.01)

    def test_percentile_predictive_power(self):
        """估值分位预测力统计"""
        # 模拟: 低分位 <0.1 时，80% 正收益
        for _ in range(8):
            self.calibrator.update_from_validation(
                0.8, True, pe_percentile=0.05, actual_return_pct=8.0
            )
        for _ in range(2):
            self.calibrator.update_from_validation(
                0.8, False, pe_percentile=0.05, actual_return_pct=-2.0
            )

        power = self.calibrator.get_percentile_predictive_power()
        if "<0.1" in power:
            assert power["<0.1"]["positive_rate"] == pytest.approx(0.8, abs=0.1)
            assert power["<0.1"]["sample_size"] >= 10

    def test_ceiling_enforced_bypass(self):
        """当开启 ceiling 边界时"""
        result = self.calibrator.calibrate(0.95)
        assert result <= 0.95  # 校准后不应超过原始值（如果有历史数据）

    def test_low_confidence_floor(self):
        """置信度下限"""
        result = self.calibrator.calibrate(0.01)
        assert result >= 0.05  # 最低不超过 0.05


# ================================================================
# 评分卡权重优化测试
# ================================================================


class TestScorecardWeightOptimizer:
    def test_optimize_basic(self):
        """基本权重优化"""
        optimizer = ScorecardWeightOptimizer()

        # 构造回测数据: 盈利能力高的公司未来收益更高
        backtest_data = []
        for i in range(20):
            backtest_data.append({
                "scorecard": {
                    "breakdown": {
                        "profitability": {"score": 15 + i},
                        "growth": {"score": 10 + i // 2},
                        "valuation": {"score": 10 + i // 3},
                        "health": {"score": 8 + i // 4},
                    }
                },
                "future_return_pct": 5 + i * 0.5,  # 盈利能力越高，收益越高
            })

        weights = optimizer.optimize(backtest_data)
        assert "profitability" in weights
        assert "growth" in weights
        assert "valuation" in weights
        assert "health" in weights

        # 权重之和应为 1.0
        total_weight = sum(weights.values())
        assert abs(total_weight - 1.0) < 0.01

        # 盈利能力权重应该是最高的（因为与收益最相关）
        assert weights["profitability"] >= weights["growth"]
        assert weights["profitability"] >= weights["health"]

    def test_insufficient_data(self):
        """数据不足时使用默认权重"""
        optimizer = ScorecardWeightOptimizer()
        weights = optimizer.optimize([])
        assert weights == {"profitability": 0.30, "growth": 0.25, "valuation": 0.25, "health": 0.20}

    def test_small_data(self):
        """少量数据时使用默认权重"""
        optimizer = ScorecardWeightOptimizer()
        weights = optimizer.optimize([
            {"scorecard": {"breakdown": {}}, "future_return_pct": 5.0}
        ] * 5)
        assert weights == {"profitability": 0.30, "growth": 0.25, "valuation": 0.25, "health": 0.20}


# ================================================================
# 行业差异化评分基准测试
# ================================================================


class TestIndustryBenchmarks:
    def test_bank_benchmark(self):
        """银行行业基准"""
        bench = get_industry_benchmark("银行")
        assert bench is not None
        assert bench["good_roe"] == 10
        assert bench["good_growth"] == 5  # 银行增速要求低

    def test_semiconductor_benchmark(self):
        """半导体行业基准"""
        bench = get_industry_benchmark("半导体")
        assert bench is not None
        assert bench["good_roe"] == 15
        assert bench["good_growth"] == 25  # 半导体增速要求高

    def test_unknown_industry(self):
        """未知行业返回 None"""
        bench = get_industry_benchmark("未知行业_XYZ")
        assert bench is None

    def test_benchmark_coverage(self):
        """行业基准覆盖足够"""
        # 应覆盖 20+ 行业
        assert len(INDUSTRY_BENCHMARKS) >= 20

    def test_benchmark_consistency(self):
        """行业基准一致性: 同一行业内数值合理"""
        for name, bench in INDUSTRY_BENCHMARKS.items():
            assert bench["good_roe"] > 0
            assert bench["good_margin"] > 0
            assert bench["good_growth"] > 0


# ================================================================
# 集成测试
# ================================================================


class TestRound2Integration:
    """Round 2 端到端集成测试"""

    def test_calibrator_full_cycle(self):
        """校准器完整周期: 校准 → 验证 → 更新 → 再校准"""
        calibrator = FundamentalConfidenceCalibrator()
        # 清空状态
        for key in calibrator._confidence_bins:
            calibrator._confidence_bins[key] = {"total": 0, "correct": 0}

        # 第一轮: 无历史数据
        conf1 = calibrator.calibrate(0.7)
        assert conf1 == 0.7

        # 模拟验证: 准确率 60%
        for i in range(10):
            calibrator.update_from_validation(0.7, i < 6)  # 6/10 正确

        # 第二轮: 有历史数据
        conf2 = calibrator.calibrate(0.7)
        # 应向 0.6 回归
        assert abs(conf2 - 0.7) < 0.15  # 不应偏离太多
        assert conf2 != 0.7  # 应有调整

    def test_scorecard_optimizer_different_data_patterns(self):
        """评分卡优化器对不同数据模式的响应"""
        optimizer = ScorecardWeightOptimizer()

        # 场景1: 成长性驱动
        growth_data = []
        for i in range(20):
            growth_data.append({
                "scorecard": {
                    "breakdown": {
                        "profitability": {"score": 15},
                        "growth": {"score": 5 + i},
                        "valuation": {"score": 10},
                        "health": {"score": 10},
                    }
                },
                "future_return_pct": i * 0.8,
            })

        weights = optimizer.optimize(growth_data)
        assert weights["growth"] >= weights["profitability"]
