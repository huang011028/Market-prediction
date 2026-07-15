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
import json
import types
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.utils.fundamental_calibrator import FundamentalConfidenceCalibrator
from src.utils.scorecard_optimizer import ScorecardWeightOptimizer, get_industry_benchmark, INDUSTRY_BENCHMARKS
from src.data.fundamental_fetcher import FundamentalData, FundamentalFetcher
from src.data.hk_financial_source import _parse_aastocks_value


def _calibration_stats_path(name: str) -> Path:
    path = Path(".pytest-tmp") / "calibration" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


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


class TestAShareTencentValuation:
    def test_apply_tencent_fields_fills_pb_and_market_cap(self):
        fetcher = FundamentalFetcher()
        data = FundamentalData(symbol="000001", market="A")
        fields = [""] * 47
        fields[1] = "平安银行"
        fields[3] = "10.50"
        fields[39] = "4.73"
        fields[44] = "2037.59"
        fields[46] = "0.45"

        latest_price = fetcher._apply_a_share_tencent_fields(data, fields)

        assert latest_price == 10.5
        assert data.company_name == "平安银行"
        assert data.pe == 4.73
        assert data.pb == 0.45
        assert data.market_cap == 2037.59

    @pytest.mark.asyncio
    async def test_yfinance_rate_limit_enters_cooldown(self, monkeypatch):
        class FakeTicker:
            @property
            def info(self):
                raise RuntimeError("Too Many Requests. Rate limited.")

        fake_yfinance = types.SimpleNamespace(Ticker=lambda symbol: FakeTicker())
        monkeypatch.setitem(sys.modules, "yfinance", fake_yfinance)
        monkeypatch.setattr(FundamentalFetcher, "_yfinance_rate_limited_until", 0.0)

        fetcher = FundamentalFetcher()
        data = FundamentalData(symbol="AAPL", market="US")

        await fetcher._fetch_yfinance(data, "AAPL", "US")

        assert data.data_source == "none"
        assert "yfinance: rate limited" in data.missing_fields
        assert FundamentalFetcher._yfinance_rate_limited_until > 0

    def test_hk_fetcher_uses_reference_fallback_for_meituan(self, monkeypatch):
        async def no_hk_tencent(self, result, symbol):
            return None

        async def no_hk_financials(self, result, symbol):
            return None

        monkeypatch.setattr(FundamentalFetcher, "_fetch_hk_tencent", no_hk_tencent)
        monkeypatch.setattr(FundamentalFetcher, "_fetch_hk_financials_supplement", no_hk_financials)

        fetcher = FundamentalFetcher()
        result = __import__("asyncio").run(fetcher.fetch_enhanced("3690", "HK"))

        assert result["company_name"] == "美团"
        assert result["industry"] == "互联网"
        assert result["data_source"] == "hk_reference"
        assert result["valuation"]["pe"] == 28.0
        assert result["valuation"]["pb"] == 5.0
        assert result["financials"]["roe_pct"] == 9.0
        assert result["quality_scorecard"]["rating"] == "unknown"
        assert result["quality_scorecard"]["not_scorable"] is True


# ================================================================
# 置信度校准器测试
# ================================================================


class TestFundamentalConfidenceCalibrator:
    def setup_method(self, method):
        # 使用独立实例，避免文件状态污染
        self.calibrator = FundamentalConfidenceCalibrator(
            stats_file=_calibration_stats_path(f"fundamental_{method.__name__}.json"),
            legacy_stats_file=_calibration_stats_path("missing_legacy.json"),
        )
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
# 公司前景分析师护栏测试
# ================================================================


class TestFundamentalAnalystGuardrails:
    @pytest.fixture
    def analyst(self):
        from src.agents.fundamental_analyst import FundamentalAnalyst

        instance = FundamentalAnalyst.__new__(FundamentalAnalyst)
        instance.name = "公司前景分析师"
        return instance

    def _base_data(
        self,
        *,
        total=72,
        rating="excellent",
        pe_percentile=0.2,
        trap=False,
        overall_quality=0.8,
        profit_trend="growing",
        earnings_quality="improving",
    ):
        return {
            "symbol": "002396",
            "company_name": "星网锐捷",
            "market": "A",
            "industry": "通信",
            "data_source": "fixture",
            "financials": {
                "revenue_yoy_pct": 12.0,
                "profit_yoy_pct": 8.0,
                "roe_pct": 10.5,
                "_trend": {
                    "profit_trend": profit_trend,
                    "earnings_quality": earnings_quality,
                },
            },
            "valuation": {"pe": 25.0, "pb": 2.1},
            "quality_scorecard": {"total": total, "rating": rating},
            "valuation_analysis": (
                {"pe_percentile_3yr": pe_percentile}
                if pe_percentile is not None
                else {}
            ),
            "value_trap_analysis": {
                "is_trap": trap,
                "signals": ["盈利恶化", "营收下滑"] if trap else [],
            },
            "data_quality": {
                "overall_quality": overall_quality,
                "freshness": 1.0,
                "confidence_ceiling": 0.85 if overall_quality >= 0.8 else 0.4,
                "data_gaps": [] if overall_quality >= 0.5 else ["roe", "profit_yoy"],
            },
            "anomaly_flags": {},
        }

    def test_validate_consistency_formats_pe_percentile(self, analyst):
        """高估值看涨应被检测，且不再因 pct 未定义崩溃。"""
        from src.core.result import AnalysisResult, Direction, Magnitude

        result = AnalysisResult(
            agent_name="公司前景分析师",
            target="002396",
            timeframe="中期(1月)",
            direction=Direction.BULLISH,
            magnitude=Magnitude(2.0, 8.0),
            confidence=0.7,
            reasoning="测试",
            risks=["估值消化风险"],
        )
        data = {
            "valuation_analysis": {"pe_percentile_3yr": 0.9},
            "data_quality": {"confidence_ceiling": 0.85, "overall_quality": 0.8},
            "value_trap_analysis": {"is_trap": False},
        }

        issues = analyst._validate_consistency(result, data)

        assert issues
        assert "90%" in issues[0]
        assert "方向为看涨" in issues[0]

    def test_apply_consistency_issues_degrades_result(self, analyst):
        """一致性问题应进入 risks/reasoning，并降低置信度供汇总层识别。"""
        from src.core.result import AnalysisResult, Direction, Magnitude

        result = AnalysisResult(
            agent_name="公司前景分析师",
            target="002396",
            timeframe="中期(1月)",
            direction=Direction.BULLISH,
            magnitude=Magnitude(2.0, 8.0),
            confidence=0.7,
            reasoning="测试",
            risks=[],
        )

        updated = analyst._apply_consistency_issues(
            result, ["PE处于3年90%分位(很贵)但方向为看涨"]
        )

        assert updated.status == "degraded"
        assert updated.confidence < 0.7
        assert any("基本面一致性校验" in risk for risk in updated.risks)
        assert "基本面一致性校验提示" in updated.reasoning

    def test_build_data_summary_contains_structured_evidence(self, analyst):
        """基本面摘要应带结构化证据，供 API 和 Aggregator 消费。"""
        data = self._base_data(total=62, rating="good", pe_percentile=0.35, overall_quality=0.76)
        data["data_source"] = "akshare+sina"

        summary = analyst._build_data_summary(
            data,
            {"preliminary_direction": "neutral"},
            ["测试校验问题"],
        )

        assert summary["source"] == "akshare+sina"
        assert summary["quality"] == 0.76
        assert summary["evidence"]["quality_scorecard"]["rating"] == "good"
        assert summary["consistency_issues"] == ["测试校验问题"]

    def test_evidence_matrix_good_company_undervalued(self, analyst):
        """好公司低估应生成看涨矩阵和多头证据。"""
        data = self._base_data(total=82, rating="excellent", pe_percentile=0.18)

        evidence = analyst._build_evidence_packet(data)

        assert evidence["decision_matrix"]["suggested_direction"] == "bullish"
        assert evidence["decision_matrix"]["matrix_position"] == "好公司+低估"
        assert evidence["evidence"]["bullish"]

    def test_evidence_matrix_weak_cheap_value_trap(self, analyst):
        """弱公司低估且触发价值陷阱时，应优先看空。"""
        data = self._base_data(
            total=32,
            rating="weak",
            pe_percentile=0.12,
            trap=True,
            profit_trend="declining",
            earnings_quality="deteriorating",
        )

        evidence = analyst._build_evidence_packet(data)

        assert evidence["decision_matrix"]["suggested_direction"] == "bearish"
        assert any("价值陷阱" in item for item in evidence["evidence"]["bearish"])

    def test_evidence_matrix_good_company_overvalued(self, analyst):
        """好公司高估不应自动看涨，应提示估值消化。"""
        data = self._base_data(total=78, rating="excellent", pe_percentile=0.88)

        evidence = analyst._build_evidence_packet(data)

        assert evidence["decision_matrix"]["suggested_direction"] == "neutral"
        assert evidence["decision_matrix"]["matrix_position"] == "好公司+高估"
        assert any("88%" in item for item in evidence["evidence"]["bearish"])

    def test_evidence_matrix_heavily_missing_caps_confidence(self, analyst):
        """数据严重缺失时应给低置信硬上限。"""
        data = self._base_data(
            total=35,
            rating="weak",
            pe_percentile=None,
            overall_quality=0.25,
        )

        evidence = analyst._build_evidence_packet(data)

        assert evidence["confidence_constraints"]["max_confidence"] <= 0.35
        assert evidence["confidence_constraints"]["hard_caps"]
        assert evidence["evidence"]["neutral"]

    def test_low_coverage_scorecard_is_not_treated_as_weak_company(self, analyst):
        """低覆盖评分不能被解释成公司质量极差。"""
        data = self._base_data(
            total=8,
            rating="weak",
            pe_percentile=None,
            overall_quality=0.30,
            profit_trend="insufficient_data",
            earnings_quality="unknown",
        )

        evidence = analyst._build_evidence_packet(data)

        assert evidence["decision_matrix"]["quality_bucket"] == "unknown_company"
        assert evidence["decision_matrix"]["suggested_direction"] == "neutral"
        assert any("质量评分不可强解释" in item for item in evidence["evidence"]["neutral"])

    def test_sanitize_unsupported_profit_claims(self, analyst):
        """缺少利润趋势字段时，不允许保留利润暴跌类断言。"""
        from src.core.result import AnalysisResult, Direction, Magnitude

        data = self._base_data(
            total=8,
            rating="weak",
            pe_percentile=None,
            overall_quality=0.30,
            profit_trend="insufficient_data",
            earnings_quality="unknown",
        )
        data["financials"]["profit_yoy_pct"] = "N/A"
        result = AnalysisResult(
            agent_name="公司前景分析师",
            target="3690",
            timeframe="短期(1周)",
            direction=Direction.NEUTRAL,
            magnitude=Magnitude(-2.0, 2.0),
            confidence=0.3,
            reasoning="公司质量评分极低，利润暴跌。",
            key_factors=["利润暴跌"],
            risks=[],
        )

        issues = analyst._sanitize_unsupported_fundamental_claims(result, data)

        assert issues
        assert "利润暴跌" not in result.reasoning
        assert all("利润暴跌" not in item for item in result.key_factors)

    def test_apply_evidence_constraints_caps_matrix_conflict(self, analyst):
        """LLM 方向与代码矩阵冲突时，应降权并标记 degraded。"""
        from src.core.result import AnalysisResult, Direction, Magnitude

        data = self._base_data(total=30, rating="weak", pe_percentile=0.9)
        result = AnalysisResult(
            agent_name="公司前景分析师",
            target="002396",
            timeframe="中期(1月)",
            direction=Direction.BULLISH,
            magnitude=Magnitude(2.0, 8.0),
            confidence=0.72,
            reasoning="测试",
            risks=[],
        )

        issues = analyst._apply_evidence_constraints(
            result, data, {"timeframe": "中期(1月)"}
        )

        assert issues
        assert result.status == "degraded"
        assert result.confidence <= 0.5
        assert any("基本面证据约束" in risk for risk in result.risks)

    def test_fixture_scenarios_match_decision_matrix(self, analyst):
        """固定离线样本应稳定映射到预期基本面矩阵。"""
        fixture = Path(__file__).parent / "fixtures" / "fundamental_scenarios.json"
        scenarios = json.loads(fixture.read_text(encoding="utf-8"))

        for scenario in scenarios:
            signals = analyst._derive_fundamental_signals(
                scenario["data"],
                scenario["timeframe"],
            )
            matrix = signals["decision_matrix"]
            assert matrix["suggested_direction"] == scenario["expected_direction"], scenario["name"]
            assert matrix["matrix_position"] == scenario["expected_position"], scenario["name"]


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
        calibrator = FundamentalConfidenceCalibrator(
            stats_file=_calibration_stats_path("fundamental_full_cycle.json"),
            legacy_stats_file=_calibration_stats_path("missing_legacy.json"),
        )
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
