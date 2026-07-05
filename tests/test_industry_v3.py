"""
行业对比分析师 v3 单元测试（Round 2 新增功能）

覆盖:
- 行业轮动检测器
- 产业链分析
- 催化剂日历
- 行业置信度校准器
- 行业参考值刷新器
"""

import pytest
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.utils.sector_rotation_detector import (
    SectorRotationDetector,
    build_rotation_prompt_appendix,
)
from src.utils.industry_chain import (
    analyze_industry_chain,
    get_upcoming_catalysts,
    build_catalyst_prompt_appendix,
    INDUSTRY_CHAIN,
    INDUSTRY_CATALOGS,
)
from src.utils.industry_calibrator import IndustryConfidenceCalibrator


def _calibration_stats_path(name: str) -> Path:
    path = Path(".pytest-tmp") / "calibration" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


# ================================================================
# 行业轮动检测器测试
# ================================================================


class TestSectorRotationDetector:
    def setup_method(self):
        self.detector = SectorRotationDetector()

    def test_detect_valuation_extremes(self):
        """估值极端分化检测"""
        industry_data = {
            "银行": {"pe": 5.0, "change_20d": 3.0},
            "白酒": {"pe": 25.0, "change_20d": 5.0},
            "新能源": {"pe": 35.0, "change_20d": 8.0},
            "半导体": {"pe": 50.0, "change_20d": 10.0},
            "证券": {"pe": 18.0, "change_20d": 2.0},
        }
        signals = self.detector.detect_rotation_signals(industry_data)

        # 应该检测到估值极端分化（半导体50 vs 银行5 = 10x）
        valuation_signals = [s for s in signals if s["signal_type"] == "valuation_extreme"]
        assert len(valuation_signals) >= 1
        assert valuation_signals[0]["strength"] > 0.5

    def test_detect_momentum_reversal(self):
        """动量反转检测"""
        industry_data = {
            "银行": {"pe": 5.0, "change_20d": 8.0, "change_5d": -3.0},  # 前20日强,近5日转跌
            "白酒": {"pe": 25.0, "change_20d": 2.0, "change_5d": 1.0},
            "新能源": {"pe": 35.0, "change_20d": 3.0, "change_5d": 2.0},
            "医药": {"pe": 30.0, "change_20d": 1.0, "change_5d": 0.5},
            "家电": {"pe": 15.0, "change_20d": 2.0, "change_5d": 1.0},
        }
        signals = self.detector.detect_rotation_signals(industry_data)

        momentum_signals = [s for s in signals if s["signal_type"] == "momentum_reversal"]
        # 银行近20日+8%但近5日-3% → 应触发动量反转
        assert len(momentum_signals) >= 1

    def test_detect_style_rotation(self):
        """风格切换检测"""
        industry_data = {
            "科技": {"pe": 30.0, "change_20d": 10.0},
            "半导体": {"pe": 50.0, "change_20d": 12.0},
            "计算机": {"pe": 35.0, "change_20d": 8.0},
            "银行": {"pe": 5.0, "change_20d": 1.0},
            "保险": {"pe": 12.0, "change_20d": 0.5},
            "电力": {"pe": 15.0, "change_20d": -1.0},
        }
        signals = self.detector.detect_rotation_signals(industry_data)

        style_signals = [s for s in signals if s["signal_type"] == "style_rotation"]
        assert len(style_signals) >= 1
        assert "成长" in style_signals[0]["description"] or "价值" in style_signals[0]["description"]

    def test_no_signals_when_normal(self):
        """正常市场无轮动信号"""
        industry_data = {
            "银行": {"pe": 6.0, "change_20d": 2.0},
            "白酒": {"pe": 22.0, "change_20d": 3.0},
            "医药": {"pe": 28.0, "change_20d": 1.0},
        }
        signals = self.detector.detect_rotation_signals(industry_data)
        # 估值差异不大，不应有极端信号
        valuation_signals = [s for s in signals if s["signal_type"] == "valuation_extreme"]
        assert len(valuation_signals) == 0

    def test_empty_data(self):
        """空数据返回空信号"""
        signals = self.detector.detect_rotation_signals({})
        assert len(signals) == 0

    def test_get_industry_style(self):
        """行业风格分类（优先级: cyclical > growth > value > defensive）"""
        assert self.detector.get_industry_style("科技") == "growth"
        assert self.detector.get_industry_style("银行") == "value"  # 银行在 VALUE 中
        assert self.detector.get_industry_style("食品饮料") == "defensive"
        assert self.detector.get_industry_style("钢铁") == "cyclical"  # 钢铁在 CYCLICAL 中优先
        assert self.detector.get_industry_style("证券") == "cyclical"  # 证券在 CYCLICAL 中
        assert self.detector.get_industry_style("未知行业") == "unknown"


class TestRotationPrompt:
    def test_build_rotation_appendix_with_signals(self):
        """有轮动信号时生成 prompt"""
        signals = [
            {
                "signal_type": "valuation_extreme",
                "description": "测试信号",
                "rotation_direction": "高→低",
                "strength": 0.7,
            }
        ]
        appendix = build_rotation_prompt_appendix(signals)
        assert "行业轮动预警" in appendix
        assert "测试信号" in appendix

    def test_build_rotation_appendix_empty(self):
        """无轮动信号时返回空"""
        appendix = build_rotation_prompt_appendix([])
        assert appendix == ""


# ================================================================
# 产业链分析测试
# ================================================================


class TestIndustryChain:
    def test_new_energy_chain(self):
        """新能源产业链"""
        chain = analyze_industry_chain("新能源")
        assert "上游" in chain.get("implication", "") or "下游" in chain.get("implication", "")
        assert len(chain.get("upstream", [])) > 0
        assert len(chain.get("downstream", [])) > 0

    def test_semiconductor_chain(self):
        """半导体产业链"""
        chain = analyze_industry_chain("半导体")
        assert "description" in chain
        assert "芯片" in chain["description"] or "设计" in chain["description"]

    def test_unknown_industry_chain(self):
        """未知行业"""
        chain = analyze_industry_chain("未知_XYZ")
        assert "note" in chain

    def test_chain_coverage(self):
        """产业链覆盖"""
        # 应覆盖 10+ 行业
        assert len(INDUSTRY_CHAIN) >= 10


# ================================================================
# 催化剂日历测试
# ================================================================


class TestCatalystCalendar:
    def test_bank_catalysts(self):
        """银行催化剂"""
        catalysts = get_upcoming_catalysts("银行", months_ahead=12)
        assert len(catalysts) > 0
        assert all("event" in c for c in catalysts)

    def test_baijiu_catalysts(self):
        """白酒催化剂"""
        catalysts = get_upcoming_catalysts("白酒", months_ahead=12)
        assert len(catalysts) > 0
        # 白酒有春节旺季
        events = [c["event"] for c in catalysts]
        assert any("春节" in e or "中秋" in e for e in events)

    def test_unknown_industry_catalysts(self):
        """未知行业无催化剂"""
        catalysts = get_upcoming_catalysts("未知_XYZ")
        assert len(catalysts) == 0

    def test_catalyst_prompt_appendix(self):
        """催化剂 prompt 生成"""
        catalysts = [
            {
                "event": "测试催化",
                "description": "测试描述",
                "impact": "positive",
                "target_month": "2026-08",
                "months_until": 1,
            }
        ]
        appendix = build_catalyst_prompt_appendix(catalysts)
        assert "近期行业催化剂" in appendix
        assert "测试催化" in appendix

    def test_catalyst_prompt_empty(self):
        """无催化剂时返回空"""
        appendix = build_catalyst_prompt_appendix([])
        assert appendix == ""

    def test_calendar_coverage(self):
        """催化剂日历覆盖"""
        # 应覆盖 5+ 行业
        assert len(INDUSTRY_CATALOGS) >= 5


# ================================================================
# 行业置信度校准器测试
# ================================================================


class TestIndustryConfidenceCalibrator:
    def setup_method(self, method):
        self.calibrator = IndustryConfidenceCalibrator(
            stats_file=_calibration_stats_path(f"industry_{method.__name__}.json"),
            legacy_stats_file=_calibration_stats_path("missing_legacy.json"),
        )
        # 清空所有桶以避免跨测试污染
        for key in self.calibrator._confidence_bins:
            self.calibrator._confidence_bins[key] = {"total": 0, "correct": 0}
        for key in self.calibrator._quality_buckets:
            self.calibrator._quality_buckets[key] = {"total": 0, "correct": 0}
        self.calibrator._industry_buckets.clear()

    def test_basic_calibrate_no_history(self):
        """无历史数据时返回原始值"""
        result = self.calibrator.calibrate(0.6)
        assert result == 0.6

    def test_calibrate_with_industry_history(self):
        """有行业历史数据时校准"""
        # 模拟: 银行行业准确率 65%
        for _ in range(13):
            self.calibrator.update_from_validation(0.7, True, industry="银行")
        for _ in range(7):
            self.calibrator.update_from_validation(0.7, False, industry="银行")

        result = self.calibrator.calibrate(0.7, industry="银行")
        # 应向 0.65 回归
        assert 0.55 < result < 0.75

    def test_get_industry_accuracy(self):
        """获取行业准确率"""
        for _ in range(10):
            self.calibrator.update_from_validation(0.6, True, industry="白酒")
        for _ in range(5):
            self.calibrator.update_from_validation(0.6, False, industry="白酒")

        acc = self.calibrator.get_industry_accuracy("白酒")
        assert acc is not None
        assert acc["total"] >= 15  # 可能有跨测试的累积
        assert acc["accuracy"] == pytest.approx(10 / 15, abs=0.1)

    def test_get_all_industry_accuracy(self):
        """获取所有行业准确率"""
        for _ in range(5):
            self.calibrator.update_from_validation(0.6, True, industry="银行")
        for _ in range(5):
            self.calibrator.update_from_validation(0.6, True, industry="白酒")

        all_acc = self.calibrator.get_all_industry_accuracy()
        assert "银行" in all_acc
        assert "白酒" in all_acc

    def test_quality_level_calibration(self):
        """数据质量级别校准"""
        # 高质量数据准确率高
        for _ in range(15):
            self.calibrator.update_from_validation(
                0.7, True, data_quality_level="constituents+trend"
            )

        result = self.calibrator.calibrate(
            0.7, data_quality_level="constituents+trend"
        )
        assert result >= 0.6

    def test_calibration_stats(self):
        """校准统计"""
        self.calibrator.update_from_validation(0.6, True, industry="银行")
        stats = self.calibrator.get_calibration_stats()
        assert "confidence_bins" in stats
        assert "industry_buckets" in stats
        assert "quality_buckets" in stats


# ================================================================
# 集成测试
# ================================================================


class TestRound2Integration:
    """Round 2 端到端集成测试"""

    def test_rotation_detector_to_prompt(self):
        """轮动检测 → prompt 注入完整链路"""
        detector = SectorRotationDetector()
        industry_data = {
            "银行": {"pe": 5.0, "change_20d": 3.0, "change_5d": 1.0},
            "半导体": {"pe": 50.0, "change_20d": 10.0, "change_5d": 3.0},
            "新能源": {"pe": 35.0, "change_20d": 8.0, "change_5d": 2.0},
        }
        signals = detector.detect_rotation_signals(industry_data)
        appendix = build_rotation_prompt_appendix(signals)

        if signals:
            assert len(appendix) > 0
            assert "轮动" in appendix
        else:
            assert appendix == ""

    def test_chain_to_prompt(self):
        """产业链分析 → prompt 注入完整链路"""
        chain = analyze_industry_chain("新能源")
        if chain.get("implication"):
            assert "上游" in chain["implication"] or "下游" in chain["implication"]

    def test_calibrator_full_cycle(self):
        """校准器完整周期"""
        calibrator = IndustryConfidenceCalibrator(
            stats_file=_calibration_stats_path("industry_full_cycle.json"),
            legacy_stats_file=_calibration_stats_path("missing_legacy.json"),
        )
        # 清空状态
        for key in calibrator._confidence_bins:
            calibrator._confidence_bins[key] = {"total": 0, "correct": 0}
        calibrator._industry_buckets.clear()

        # 第一轮
        conf1 = calibrator.calibrate(0.6, industry="银行")
        assert conf1 == 0.6

        # 模拟验证
        for i in range(10):
            calibrator.update_from_validation(0.6, i < 7, industry="银行")

        # 第二轮
        conf2 = calibrator.calibrate(0.6, industry="银行")
        assert conf2 != 0.6  # 应有调整
