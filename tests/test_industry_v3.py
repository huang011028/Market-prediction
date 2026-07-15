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
import json
import types
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
from src.data.industry_fetcher import IndustryData, IndustryFetcher
from src.data.industry_preprocessor import (
    calculate_industry_metrics,
    process_industry_data,
)


def _calibration_stats_path(name: str) -> Path:
    path = Path(".pytest-tmp") / "calibration" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


class TestIndustryDataCoverageFixes:
    def test_metrics_keep_pb_and_roe_when_pe_missing(self):
        metrics = calculate_industry_metrics([
            {"code": "A", "pb": 2.0, "roe": 10.0},
            {"code": "B", "pb": 3.0, "roe": 14.0},
        ])

        assert metrics.avg_pe is None
        assert metrics.avg_pb == 2.5
        assert metrics.avg_roe == 12.0
        assert metrics.sample_size == 2

    def test_reference_only_rank_has_explicit_pe_rank(self):
        processed = process_industry_data(
            stock_data={"pe": 10.0, "pb": 1.0, "roe": 12.0},
            industry_peers=[],
            reference_metrics={"pe": 12.0, "pb": 1.4, "roe": 11.0},
        )

        rank = processed["rank_in_industry"]
        assert rank["pe_rank"] == "参考均值(无成分股排名)"
        assert processed["data_quality"]["overall"] >= 0.5

    def test_reference_supplements_missing_peer_roe(self):
        processed = process_industry_data(
            stock_data={"pe": 9.5, "pb": 0.9, "roe": 10.5},
            industry_peers=[
                {"code": "0005", "pe": 9.5, "pb": 0.9},
                {"code": "0011", "pe": 10.5, "pb": 1.1},
                {"code": "0388", "pe": 30.0, "pb": 7.5},
            ],
            reference_metrics={"pe": 12.0, "pb": 1.4, "roe": 12.0},
        )

        metrics = processed["industry_metrics"]
        assert metrics["avg_roe"] == 12.0
        assert metrics["reference_supplemented"] is True

    def test_reference_peers_are_low_reliability_not_full_constituents(self):
        processed = process_industry_data(
            stock_data={"pe": 28.0, "pb": 5.0, "roe": 9.0},
            industry_peers=[
                {"code": "0700", "pe": 18.0, "pb": 3.6, "roe": 18.0, "source": "reference"},
                {"code": "9988", "pe": 15.0, "pb": 1.8, "roe": 12.0, "source": "reference"},
                {"code": "9618", "pe": 12.0, "pb": 1.7, "roe": 13.0, "source": "reference"},
                {"code": "9888", "pe": 11.0, "pb": 1.4, "roe": 10.0, "source": "reference"},
            ],
            reference_metrics={"pe": 20.0, "pb": 4.0, "roe": 15.0},
        )

        dq = processed["data_quality"]
        assert dq["has_constituents"] is False
        assert dq["has_reference_peers"] is True
        assert dq["ranking_reliability"] == "reference_snapshot"
        assert dq["confidence_ceiling"] <= 0.45

    def test_apply_a_share_tencent_fields_fills_stock_pb(self):
        fetcher = IndustryFetcher()
        data = IndustryData(symbol="000001")
        fields = [""] * 47
        fields[1] = "平安银行"
        fields[3] = "10.50"
        fields[39] = "4.73"
        fields[44] = "2037.59"
        fields[46] = "0.45"

        latest_price = fetcher._apply_a_share_tencent_fields(data, fields)

        assert latest_price == 10.5
        assert data.company_name == "平安银行"
        assert data.stock_pe == 4.73
        assert data.stock_pb == 0.45
        assert data.stock_market_cap == 2037.59

    def test_pb_rank_is_in_processed_result(self):
        processed = process_industry_data(
            stock_data={"pe": 10.0, "pb": 1.2, "roe": 12.0},
            industry_peers=[
                {"code": "A", "pe": 8.0, "pb": 0.8, "roe": 8.0},
                {"code": "B", "pe": 10.0, "pb": 1.2, "roe": 12.0},
                {"code": "C", "pe": 15.0, "pb": 2.4, "roe": 16.0},
            ],
        )

        rank = processed["rank_in_industry"]
        assert rank["pb_rank"] == "2/3"
        assert rank["pb_percentile"] == pytest.approx(0.67, abs=0.01)

    @pytest.mark.asyncio
    async def test_fetch_constituents_uses_board_alias_and_supplements_roe(self, monkeypatch):
        pd = pytest.importorskip("pandas")

        def stock_board_industry_cons_em(symbol):
            if symbol == "白酒":
                return pd.DataFrame()
            assert symbol == "酿酒行业"
            return pd.DataFrame([
                {
                    "代码": "600519",
                    "名称": "贵州茅台",
                    "最新价": 1500.0,
                    "市盈率-动态": 25.0,
                    "市净率": 8.0,
                    "涨跌幅": 1.2,
                },
                {
                    "代码": "000858",
                    "名称": "五粮液",
                    "最新价": 150.0,
                    "市盈率-动态": 18.0,
                    "市净率": 4.0,
                    "涨跌幅": 0.8,
                },
            ])

        def stock_financial_abstract_ths(symbol, indicator):
            return pd.DataFrame([
                {"净资产收益率": "20.0%", "每股净资产": "50.0"},
            ])

        fake_akshare = types.SimpleNamespace(
            stock_board_industry_cons_em=stock_board_industry_cons_em,
            stock_financial_abstract_ths=stock_financial_abstract_ths,
        )
        monkeypatch.setitem(sys.modules, "akshare", fake_akshare)

        fetcher = IndustryFetcher()
        monkeypatch.setattr(fetcher, "_fetch_a_share_curated_peers", lambda industry_name, target_code="": [])

        peers = await fetcher._fetch_industry_constituents("白酒", "600519")

        assert peers[0]["board"] == "酿酒行业"
        assert peers[0]["roe"] == 20.0
        assert "ths_financial" in peers[0]["source"]

    def test_curated_a_share_peers_use_tencent_realtime_snapshot(self, monkeypatch):
        def fake_get(url, timeout=6, verify=False):
            code = url[-6:]
            fields = [""] * 47
            fields[1] = "平安银行" if code == "000001" else "招商银行"
            fields[3] = "10.50"
            fields[32] = "1.20"
            fields[39] = "5.20"
            fields[44] = "2000.00"
            fields[46] = "0.55"
            return types.SimpleNamespace(text="~".join(fields))

        fake_requests = types.SimpleNamespace(get=fake_get)
        monkeypatch.setitem(sys.modules, "requests", fake_requests)

        peers = IndustryFetcher()._fetch_a_share_curated_peers("银行", "000001")

        assert peers
        assert peers[0]["source"] == "tencent_peer_realtime"
        assert peers[0]["pe"] == 5.2
        assert peers[0]["pb"] == 0.55

    def test_peer_codes_expand_from_known_industry_mapping(self):
        fetcher = IndustryFetcher()

        codes = fetcher._a_share_peer_codes("房地产", "000002")

        assert codes[0] == "000002"
        assert "600048" in codes
        assert "601155" in codes

    def test_peer_codes_include_classifier_cache(self):
        fetcher = IndustryFetcher()
        fetcher._classifier_cache.cache["688001"] = "化工"

        codes = fetcher._a_share_peer_codes("化工", "688001")

        assert codes[0] == "688001"
        assert "603260" in codes


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
# 行业对比分析师护栏测试
# ================================================================


class TestIndustryAnalystGuardrails:
    @pytest.fixture
    def analyst(self):
        from src.agents.industry_analyst import IndustryAnalyst

        instance = IndustryAnalyst.__new__(IndustryAnalyst)
        instance.name = "行业对比分析师"
        return instance

    def _base_data(
        self,
        *,
        pe_percentile=0.88,
        roe_percentile=0.78,
        value_score="overpriced",
        cycle="slowdown",
        overall=0.78,
    ):
        return {
            "symbol": "002396",
            "company_name": "星网锐捷",
            "industry_name": "通信",
            "data_source": "fixture",
            "rank_in_industry": {
                "pe_percentile": pe_percentile,
                "roe_percentile": roe_percentile,
                "valuation_label": "高PE+低ROE：估值偏高，需警惕",
            },
            "value_score": {
                "score": value_score,
                "value_ratio": 1.95,
                "interpretation": "性价比差，估值显著高于盈利能力对应的合理水平",
            },
            "industry_trend": {
                "cycle": cycle,
                "phase": "衰退期" if cycle == "slowdown" else "复苏期",
                "signal": "行业下行+估值仍贵，可能进一步下跌",
            },
            "data_quality": {
                "overall": overall,
                "has_constituents": overall >= 0.5,
                "has_trend": overall >= 0.5,
                "confidence_ceiling": 0.8 if overall >= 0.7 else 0.35,
            },
            "_rotation_signals": [],
            "_industry_chain": {},
            "_catalysts": [],
            "anomaly_flags": {},
        }

    def test_fixture_scenarios_match_decision_matrix(self, analyst):
        """固定离线样本应稳定映射到预期行业矩阵。"""
        fixture = Path(__file__).parent / "fixtures" / "industry_scenarios.json"
        scenarios = json.loads(fixture.read_text(encoding="utf-8"))

        for scenario in scenarios:
            signals = analyst._derive_industry_signals(
                scenario["data"],
                scenario["timeframe"],
            )
            matrix = signals["decision_matrix"]
            assert matrix["suggested_direction"] == scenario["expected_direction"], scenario["name"]
            assert matrix["matrix_position"] == scenario["expected_position"], scenario["name"]

    def test_validate_consistency_returns_issues(self, analyst):
        """高估、行业下行但看涨时应被校验识别。"""
        from src.core.result import AnalysisResult, Direction, Magnitude

        result = AnalysisResult(
            agent_name="行业对比分析师",
            target="002396",
            timeframe="中期(1月)",
            direction=Direction.BULLISH,
            magnitude=Magnitude(2.0, 8.0),
            confidence=0.72,
            reasoning="测试",
            risks=[],
        )

        issues = analyst._validate_consistency(result, self._base_data())

        assert issues
        assert any("方向为看涨" in issue for issue in issues)
        assert any("性价比评分" in issue for issue in issues)

    def test_apply_consistency_issues_degrades_result(self, analyst):
        """一致性问题应进入 risks/reasoning，并降低置信度。"""
        from src.core.result import AnalysisResult, Direction, Magnitude

        result = AnalysisResult(
            agent_name="行业对比分析师",
            target="002396",
            timeframe="中期(1月)",
            direction=Direction.BULLISH,
            magnitude=Magnitude(2.0, 8.0),
            confidence=0.72,
            reasoning="测试",
            risks=[],
        )

        updated = analyst._apply_consistency_issues(
            result,
            ["性价比评分为'明显高估'但方向为看涨——逻辑矛盾"],
        )

        assert updated.status == "degraded"
        assert updated.confidence < 0.72
        assert any("行业一致性校验" in risk for risk in updated.risks)
        assert "行业一致性校验提示" in updated.reasoning

    def test_build_data_summary_contains_evidence_packet(self, analyst):
        """行业摘要应带结构化证据，供 API 和 Aggregator 消费。"""
        data = self._base_data(
            pe_percentile=0.18,
            roe_percentile=0.22,
            value_score="excellent",
            cycle="recovery",
            overall=0.82,
        )

        summary = analyst._build_data_summary(
            data,
            {"preliminary_direction": "bullish"},
            ["测试校验问题"],
        )

        assert summary["industry"] == "通信"
        assert summary["quality"] == 0.82
        assert summary["evidence"]["decision_matrix"]["suggested_direction"] == "bullish"
        assert summary["consistency_issues"] == ["测试校验问题"]

    def test_reference_peer_matrix_uses_qualified_language(self, analyst):
        """港股参考 peer 排名不能输出绝对化行业结论。"""
        data = self._base_data(
            pe_percentile=1.0,
            roe_percentile=1.0,
            value_score="overpriced",
            cycle="unknown",
            overall=0.40,
        )
        data["data_source"] = "hk_peer_reference"
        data["data_quality"]["has_constituents"] = False
        data["data_quality"]["has_reference_peers"] = True
        data["data_quality"]["ranking_reliability"] = "reference_snapshot"
        data["data_quality"]["confidence_ceiling"] = 0.45

        evidence = analyst._build_evidence_packet(data)

        assert evidence["decision_matrix"]["suggested_direction"] == "neutral"
        assert "参考 peer 快照" in evidence["decision_matrix"]["reason"]
        assert evidence["confidence_constraints"]["max_confidence"] <= 0.45
        assert any("参考 peer 样本" in item for item in evidence["evidence"]["neutral"])

    def test_sanitize_reference_peer_absolute_claims(self, analyst):
        """reference peer 场景下移除垫底/绝对劣势措辞。"""
        from src.core.result import AnalysisResult, Direction, Magnitude

        data = self._base_data(overall=0.40)
        data["data_source"] = "hk_peer_reference"
        data["data_quality"]["has_reference_peers"] = True
        data["data_quality"]["ranking_reliability"] = "reference_snapshot"
        result = AnalysisResult(
            agent_name="行业对比分析师",
            target="3690",
            timeframe="短期(1周)",
            direction=Direction.NEUTRAL,
            magnitude=Magnitude(-2.0, 2.0),
            confidence=0.4,
            reasoning="ROE垫底，基本面绝对劣势。",
            key_factors=["ROE垫底"],
            risks=[],
        )

        issues = analyst._sanitize_reference_peer_claims(result, data)

        assert issues
        assert "垫底" not in result.reasoning
        assert "绝对劣势" not in result.reasoning
        assert all("垫底" not in item for item in result.key_factors)

    def test_apply_evidence_constraints_caps_matrix_conflict(self, analyst):
        """LLM 方向与行业矩阵冲突时，应降权并标记 degraded。"""
        from src.core.result import AnalysisResult, Direction, Magnitude

        data = self._base_data()
        result = AnalysisResult(
            agent_name="行业对比分析师",
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
        assert any("行业证据约束" in risk for risk in result.risks)

    def test_step_b_prompt_includes_evidence_packet(self, analyst):
        """Step B Prompt 应显式注入代码计算的行业证据包。"""
        prompt = analyst._build_step_b_prompt(
            self._base_data(),
            {"target": "002396", "timeframe": "中期(1月)"},
            {"preliminary_direction": "bearish"},
        )

        assert "行业证据包" in prompt
        assert "decision_matrix" in prompt
        assert "confidence_constraints" in prompt


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
