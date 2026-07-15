"""
宏观分析师 v2 测试
"""
import json
from pathlib import Path

import pytest


class TestMacroFetcherV2:
    """测试宏观数据获取器 v2"""

    pytestmark = [pytest.mark.network, pytest.mark.slow]

    @pytest.mark.asyncio
    async def test_fetch_china_data(self):
        """测试中国市场数据获取"""
        from src.data.macro_fetcher import MacroFetcherV2

        fetcher = MacroFetcherV2()
        data = await fetcher.fetch("0700", "HK")

        d = data.to_agent_dict()
        china = d["china"]

        # LPR 应该是实时获取的（非硬编码）
        assert china["lpr_1y_pct"] != "N/A"
        assert "参考值" not in str(china.get("lpr_freshness", ""))

        # M2 应该是实时获取的
        assert china["m2_yoy_pct"] != "N/A"
        assert "参考值" not in str(china.get("m2_freshness", ""))

        # 基础字段存在
        assert "cpi_yoy_pct" in china
        assert "pmi_manufacturing" in china

    @pytest.mark.asyncio
    async def test_data_freshness_scoring(self):
        """测试数据新鲜度评分"""
        from src.data.macro_fetcher import MacroFetcherV2

        fetcher = MacroFetcherV2()
        data = await fetcher.fetch("0700", "HK")

        d = data.to_agent_dict()
        quality = d.get("data_quality", {})

        assert "realtime_count" in quality
        assert "reference_count" in quality
        assert "overall_freshness" in quality

        # LPR/M2 实时化后，realtime_count 应 >= 5（CPI+PMI+GDP+LPR+M2）
        assert quality["realtime_count"] >= 5

    @pytest.mark.asyncio
    async def test_us_data_fallback(self):
        """测试美国数据降级到参考值"""
        from src.data.macro_fetcher import MacroFetcherV2

        fetcher = MacroFetcherV2()
        data = await fetcher.fetch("0700", "HK")

        d = data.to_agent_dict()
        us = d["us"]

        # US 数据应该存在（无论是实时还是参考值）
        assert us["10y_yield_pct"] != "N/A"
        assert us["vix"] != "N/A"

        # 新鲜度标注应该存在
        assert "10y_freshness" in us
        assert "vix_freshness" in us
        assert "dxy_freshness" in d.get("forex", {})

    @pytest.mark.asyncio
    async def test_lpr_value_reasonable(self):
        """测试 LPR 值在合理范围"""
        from src.data.macro_fetcher import MacroFetcherV2

        fetcher = MacroFetcherV2()
        data = await fetcher.fetch("0700", "HK")
        d = data.to_agent_dict()

        lpr_str = d["china"]["lpr_1y_pct"]
        if lpr_str != "N/A":
            lpr = float(lpr_str)
            assert 2.0 <= lpr <= 5.0, f"LPR {lpr} 不在合理范围"

    @pytest.mark.asyncio
    async def test_m2_value_reasonable(self):
        """测试 M2 值在合理范围"""
        from src.data.macro_fetcher import MacroFetcherV2

        fetcher = MacroFetcherV2()
        data = await fetcher.fetch("0700", "HK")
        d = data.to_agent_dict()

        m2_str = d["china"]["m2_yoy_pct"]
        if m2_str != "N/A":
            m2 = float(m2_str)
            assert 0.0 <= m2 <= 30.0, f"M2 {m2} 不在合理范围"


class TestStockContext:
    """测试标的宏观上下文解析"""

    def test_tencent_identified_as_internet(self):
        """腾讯应识别为互联网平台"""
        from src.data.stock_context import get_stock_macro_context

        ctx = get_stock_macro_context("0700", "HK", "腾讯控股")
        assert ctx["inferred_sector"] == "互联网平台"
        assert ctx["macro_sensitivity"]["rate_sensitive"] >= 0.7
        assert "利率下行" in str(ctx["transmission_hints"])

    def test_hsbc_identified_as_bank(self):
        """汇丰应识别为银行"""
        from src.data.stock_context import get_stock_macro_context

        ctx = get_stock_macro_context("0005", "HK", "汇丰控股")
        assert ctx["inferred_sector"] == "银行"
        assert ctx["macro_sensitivity"]["rate_sensitive"] >= 0.8
        assert ctx["macro_sensitivity"]["rate_direction"] == "positive"

    def test_meituan_identified_as_internet(self):
        """美团应识别为互联网平台"""
        from src.data.stock_context import get_stock_macro_context

        ctx = get_stock_macro_context("3690", "HK", "美团")
        assert ctx["inferred_sector"] == "互联网平台"

    def test_cnooc_identified_as_energy(self):
        """中海油应识别为能源"""
        from src.data.stock_context import get_stock_macro_context

        ctx = get_stock_macro_context("0883", "HK", "中国海洋石油")
        assert ctx["inferred_sector"] == "能源/资源"

    def test_unknown_company_defaults(self):
        """未知公司使用默认值"""
        from src.data.stock_context import get_stock_macro_context

        ctx = get_stock_macro_context("9999", "HK", "")
        assert "综合" in ctx["inferred_sector"] or ctx["inferred_sector"] != ""
        assert "transmission_hints" in ctx

    def test_hk_market_has_specific_notes(self):
        """港股应有特殊市场注释"""
        from src.data.stock_context import get_stock_macro_context

        ctx = get_stock_macro_context("0700", "HK", "腾讯")
        assert "market_note" in ctx
        assert "美元定价" in ctx.get("market_note", "")

    def test_a_share_has_policy_notes(self):
        """A股应有政策相关注释"""
        from src.data.stock_context import get_stock_macro_context

        ctx = get_stock_macro_context("000001", "A", "平安银行")
        assert "market_note" in ctx

    def test_consumer_sector_cycle_sensitive(self):
        """消费行业应标注为周期敏感"""
        from src.data.stock_context import get_stock_macro_context

        ctx = get_stock_macro_context("2331", "HK", "李宁")
        assert ctx["inferred_sector"] == "消费"
        assert ctx["macro_sensitivity"]["cycle_sensitive"] >= 0.7

    def test_property_sector_rate_sensitive(self):
        """地产行业应标注为利率极度敏感"""
        from src.data.stock_context import get_stock_macro_context

        ctx = get_stock_macro_context("0016", "HK", "新鸿基地产")
        assert ctx["inferred_sector"] == "地产/物业"
        assert ctx["macro_sensitivity"]["rate_sensitive"] >= 0.8


class TestMacroPrompts:
    """测试 Prompt 完整性"""

    def test_confidence_anchors_present(self):
        from src.prompts.macro_prompts import CONFIDENCE_ANCHORS_MACRO

        assert "0.70-0.80" in CONFIDENCE_ANCHORS_MACRO
        assert "0.30-0.39" in CONFIDENCE_ANCHORS_MACRO
        assert "参考值" in CONFIDENCE_ANCHORS_MACRO

    def test_few_shot_present(self):
        from src.prompts.macro_prompts import FEW_SHOT_MACRO

        assert "腾讯控股" in FEW_SHOT_MACRO
        assert "李宁" in FEW_SHOT_MACRO

    def test_market_appendix_present(self):
        from src.prompts.macro_prompts import (
            A_SHARE_MACRO_APPENDIX,
            HK_SHARE_MACRO_APPENDIX,
            US_SHARE_MACRO_APPENDIX,
        )

        assert "政策信号" in A_SHARE_MACRO_APPENDIX
        assert "美元定价" in HK_SHARE_MACRO_APPENDIX
        assert "Fed" in US_SHARE_MACRO_APPENDIX

    def test_system_prompt_has_data_freshness_guidance(self):
        from src.prompts.macro_prompts import MACRO_SYSTEM_PROMPT

        assert "新鲜度" in MACRO_SYSTEM_PROMPT
        assert "参考值" in MACRO_SYSTEM_PROMPT

    def test_assessment_prompt_structure(self):
        from src.prompts.macro_prompts import MACRO_ASSESSMENT_PROMPT

        assert "liquidity" in MACRO_ASSESSMENT_PROMPT
        assert "economic_cycle" in MACRO_ASSESSMENT_PROMPT
        assert "geopolitical" in MACRO_ASSESSMENT_PROMPT

    def test_transmission_prompt_structure(self):
        from src.prompts.macro_prompts import MACRO_TRANSMISSION_PROMPT

        assert "transmission_chains" in MACRO_TRANSMISSION_PROMPT
        assert "sector_sensitivity" in MACRO_TRANSMISSION_PROMPT

        rendered = MACRO_TRANSMISSION_PROMPT.format(
            stock_context='{"sector":"互联网平台"}',
            macro_assessment='{"overall_macro_stance":"中性"}',
            macro_data='{"data_quality":{"overall_freshness":"100%"}}',
            market_appendix="港股测试附录",
        )
        assert '"transmission_chains"' in rendered
        assert '{"min_pct": -5.0, "max_pct": 5.0}' in rendered


class TestMacroAnalystGuardrails:
    """国际形势分析师护栏测试。"""

    @pytest.fixture
    def analyst(self):
        from src.agents.macro_analyst import MacroAnalyst

        instance = MacroAnalyst.__new__(MacroAnalyst)
        instance.name = "国际形势分析师"
        return instance

    def _bearish_macro_data(self):
        fixture = Path(__file__).parent / "fixtures" / "macro_scenarios.json"
        scenarios = json.loads(fixture.read_text(encoding="utf-8"))
        scenario = next(s for s in scenarios if s["name"] == "hk_internet_high_rate_risk_off")
        data = scenario["data"]
        data["_market"] = scenario["market"]
        return data, scenario["stock_context"], scenario["market"]

    def test_fixture_scenarios_match_transmission_matrix(self, analyst):
        """固定离线样本应稳定映射到预期宏观传导矩阵。"""
        fixture = Path(__file__).parent / "fixtures" / "macro_scenarios.json"
        scenarios = json.loads(fixture.read_text(encoding="utf-8"))

        for scenario in scenarios:
            signals = analyst._derive_macro_signals(
                scenario["data"],
                scenario["stock_context"],
                scenario["market"],
                scenario["timeframe"],
            )
            matrix = signals["decision_matrix"]
            assert matrix["suggested_direction"] == scenario["expected_direction"], scenario["name"]
            assert matrix["matrix_position"] == scenario["expected_position"], scenario["name"]

    def test_validate_macro_result_detects_risk_conflict(self, analyst):
        """高 VIX、高 DXY、高地缘风险但看涨时应被识别。"""
        from src.core.result import AnalysisResult, Direction, Magnitude

        data, stock_ctx, _market = self._bearish_macro_data()
        result = AnalysisResult(
            agent_name="国际形势分析师",
            target="0700",
            timeframe="中期(1月)",
            direction=Direction.BULLISH,
            magnitude=Magnitude(2.0, 8.0),
            confidence=0.72,
            reasoning="腾讯控股互联网平台 宏观偏多",
            risks=[],
        )

        issues = analyst._validate_macro_result(result, data, stock_ctx)

        assert issues
        assert any("VIX" in issue for issue in issues)
        assert any("DXY" in issue for issue in issues)
        assert any("地缘" in issue for issue in issues)

    def test_apply_consistency_issues_degrades_result(self, analyst):
        """一致性问题应进入 risks/reasoning，并降低置信度。"""
        from src.core.result import AnalysisResult, Direction, Magnitude

        result = AnalysisResult(
            agent_name="国际形势分析师",
            target="0700",
            timeframe="中期(1月)",
            direction=Direction.BULLISH,
            magnitude=Magnitude(2.0, 8.0),
            confidence=0.72,
            reasoning="测试",
            risks=[],
        )

        updated = analyst._apply_consistency_issues(
            result,
            ["VIX(28.0)显示风险偏好显著下降但方向为看涨"],
        )

        assert updated.status == "degraded"
        assert updated.confidence < 0.72
        assert any("宏观一致性校验" in risk for risk in updated.risks)
        assert "宏观一致性校验提示" in updated.reasoning

    def test_validate_macro_result_accepts_company_short_name(self, analyst):
        """腾讯控股这类公司名应允许 reasoning 使用简称，避免误降级。"""
        from src.core.result import AnalysisResult, Direction, Magnitude

        data, stock_ctx, _market = self._bearish_macro_data()
        stock_ctx["company_name"] = "腾讯控股"
        stock_ctx["inferred_sector"] = "互联网平台"
        result = AnalysisResult(
            agent_name="国际形势分析师",
            target="0700",
            timeframe="短期(1周)",
            direction=Direction.BEARISH,
            magnitude=Magnitude(-5.0, -1.0),
            confidence=0.55,
            reasoning="腾讯受高利率和美元偏强影响，互联网平台估值承压。",
            risks=[],
        )

        issues = analyst._validate_macro_result(result, data, stock_ctx)

        assert not any("未提及标的" in issue for issue in issues)

    def test_non_explicit_identity_warning_does_not_degrade_macro_result(self, analyst):
        """未显式点名只提示风险，不应等同于数据或方向失效。"""
        from src.core.result import AnalysisResult, Direction, Magnitude

        result = AnalysisResult(
            agent_name="国际形势分析师",
            target="0700",
            timeframe="短期(1周)",
            direction=Direction.NEUTRAL,
            magnitude=Magnitude(-2.0, 2.0),
            confidence=0.55,
            reasoning="宏观多空交织。",
            risks=[],
        )

        updated = analyst._apply_consistency_issues(
            result,
            ["reasoning 未提及标的(腾讯控股/互联网平台)，宏观分析可能泛化"],
        )

        assert updated.status == "ok"
        assert updated.confidence == 0.55
        assert any("宏观一致性校验" in risk for risk in updated.risks)

    def test_build_data_summary_contains_macro_evidence(self, analyst):
        """宏观摘要应带结构化证据，供 API 和 Aggregator 消费。"""
        data, stock_ctx, market = self._bearish_macro_data()

        summary = analyst._build_data_summary(
            data,
            stock_ctx,
            market,
            {"preliminary_direction": "bearish"},
            ["测试校验问题"],
            "中期(1月)",
        )

        assert summary["market"] == "HK"
        assert summary["sector"] == "互联网平台"
        assert summary["evidence"]["decision_matrix"]["suggested_direction"] == "bearish"
        assert summary["consistency_issues"] == ["测试校验问题"]

    def test_apply_evidence_constraints_caps_matrix_conflict(self, analyst):
        """LLM 方向与宏观矩阵冲突时，应降权并标记 degraded。"""
        from src.core.result import AnalysisResult, Direction, Magnitude

        data, stock_ctx, market = self._bearish_macro_data()
        result = AnalysisResult(
            agent_name="国际形势分析师",
            target="0700",
            timeframe="中期(1月)",
            direction=Direction.BULLISH,
            magnitude=Magnitude(2.0, 8.0),
            confidence=0.72,
            reasoning="测试",
            risks=[],
        )

        issues = analyst._apply_evidence_constraints(
            result,
            data,
            {"timeframe": "中期(1月)"},
            stock_ctx,
            market,
        )

        assert issues
        assert result.status == "degraded"
        assert result.confidence <= 0.5
        assert any("宏观证据约束" in risk for risk in result.risks)


class TestMacroConfidenceCalibrator:
    def test_basic_calibrate_no_history(self):
        from src.utils.macro_calibrator import MacroConfidenceCalibrator

        calibrator = MacroConfidenceCalibrator(
            stats_file=Path(".pytest-tmp") / "calibration" / "macro_basic.json"
        )
        assert calibrator.calibrate(0.6, market="HK", sector="互联网平台") == 0.6

    def test_update_from_validation_records_stats(self):
        from src.utils.macro_calibrator import MacroConfidenceCalibrator

        calibrator = MacroConfidenceCalibrator(
            stats_file=Path(".pytest-tmp") / "calibration" / "macro_update.json"
        )
        calibrator.update_from_validation(
            predicted_conf=0.6,
            was_correct=True,
            market="HK",
            sector="互联网平台",
            data_quality_level="fresh",
        )
        stats = calibrator.get_calibration_stats()
        assert stats["confidence_bins"]
        assert stats["market_buckets"]["HK"]["total"] >= 1
        assert stats["sector_buckets"]["互联网平台"]["total"] >= 1
