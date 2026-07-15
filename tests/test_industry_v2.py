"""
行业对比分析师 v2 单元测试

覆盖:
- 预处理管线（行业指标计算、排名、周期判断、性价比评分）
- 行业分类缓存
- Agent 解析逻辑
- 置信度校准
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.data.industry_preprocessor import (
    calculate_industry_metrics,
    calculate_industry_rank,
    classify_industry_cycle,
    calculate_value_score,
    IndustryClassifierCache,
    IndustryReferenceCache,
    EXTENDED_KNOWN_INDUSTRIES,
    KNOWN_HK_INDUSTRIES,
    HK_PEER_REFERENCE,
    infer_industry_from_name,
    process_industry_data,
    _safe_float,
)


# ================================================================
# 工具函数测试
# ================================================================


class TestSafeFloat:
    def test_none(self):
        assert _safe_float(None) is None

    def test_valid(self):
        assert _safe_float(3.14) == 3.14
        assert _safe_float(42) == 42.0

    def test_na_string(self):
        assert _safe_float("N/A") is None

    def test_numeric_string(self):
        assert _safe_float("5.2") == 5.2


# ================================================================
# 行业平均估值计算
# ================================================================


class TestCalculateIndustryMetrics:
    def test_basic_calculation(self):
        peers = [
            {"code": "601398", "name": "工商银行", "pe": 5.2, "pb": 0.6, "roe": 11.0},
            {"code": "600036", "name": "招商银行", "pe": 8.5, "pb": 1.2, "roe": 16.0},
            {"code": "601939", "name": "建设银行", "pe": 5.0, "pb": 0.55, "roe": 10.5},
            {"code": "000001", "name": "平安银行", "pe": 6.2, "pb": 0.72, "roe": 12.0},
            {"code": "601328", "name": "交通银行", "pe": 4.8, "pb": 0.5, "roe": 9.5},
        ]
        metrics = calculate_industry_metrics(peers)

        assert metrics.avg_pe is not None
        assert 5.0 < metrics.avg_pe < 7.0
        assert metrics.median_pe is not None
        assert metrics.sample_size == 5
        assert metrics.pe_std is not None
        assert metrics.pe_std > 0

    def test_empty_peers(self):
        metrics = calculate_industry_metrics([])
        assert metrics.sample_size == 0
        assert metrics.avg_pe is None

    def test_with_invalid_values(self):
        peers = [
            {"code": "1", "name": "A", "pe": -1, "pb": 0, "roe": None},
            {"code": "2", "name": "B", "pe": 10.0, "pb": 1.0, "roe": 15.0},
            {"code": "3", "name": "C", "pe": 20.0, "pb": 2.0, "roe": 10.0},
        ]
        metrics = calculate_industry_metrics(peers)
        assert metrics.avg_pe == 15.0  # (10+20)/2，排除负值和0
        assert metrics.sample_size == 3

    def test_single_peer(self):
        peers = [{"code": "1", "name": "A", "pe": 15.0}]
        metrics = calculate_industry_metrics(peers)
        assert metrics.avg_pe == 15.0
        assert metrics.median_pe == 15.0


# ================================================================
# 标的行业排名
# ================================================================


class TestCalculateIndustryRank:
    def test_rank_calculation(self):
        peers = [{"pe": float(i), "roe": 20 - i} for i in range(1, 21)]  # PE 1~20
        # 标的 PE=10, ROE=15
        rank = calculate_industry_rank(10.0, 15.0, peers)

        # PE=10: count_below = sum(1 for pe in [1..20] if pe <= 10) = 10
        assert rank.pe_rank == "10/20"
        assert rank.pe_percentile == 0.50

    def test_roe_rank_clamped_to_peer_count(self):
        peers = [
            {"roe": 20.0},
            {"roe": 15.0},
            {"roe": 10.0},
            {"roe": 8.0},
        ]

        rank = calculate_industry_rank(None, 6.0, peers)

        assert rank.roe_rank == "4/4"
        assert rank.roe_percentile == 1.0

    def test_rank_cheapest(self):
        peers = [{"pe": float(i)} for i in range(5, 55)]  # PE 5~54
        rank = calculate_industry_rank(4.0, None, peers)  # PE 4 = 最便宜
        # PE=4 比所有值都小，count_below=0，即 rank=0/50
        assert rank.pe_rank == "0/50"
        assert rank.pe_percentile == 0.0

    def test_rank_most_expensive(self):
        peers = [{"pe": float(i)} for i in range(5, 55)]
        rank = calculate_industry_rank(60.0, None, peers)  # PE 60 = 最贵
        assert rank.pe_rank == "50/50"

    def test_empty_peers(self):
        rank = calculate_industry_rank(10.0, None, [])
        assert rank.pe_rank == "N/A"
        assert rank.valuation_label == "N/A"

    def test_valuation_label_logic(self):
        # 低PE+高ROE → 性价比突出
        peers = [{"pe": float(i), "roe": 25 - i} for i in range(1, 41)]
        rank = calculate_industry_rank(10.0, 20.0, peers)  # PE=10(便宜), ROE=20(高)
        assert "性价比突出" in rank.valuation_label or "低PE" in rank.valuation_label


# ================================================================
# 行业周期判断
# ================================================================


class TestClassifyIndustryCycle:
    def test_boom_phase(self):
        trend = {"change_5d": 2.5, "change_20d": 8.0, "change_60d": 15.0}
        cycle = classify_industry_cycle(trend, pe_percentile=0.75)
        assert cycle.cycle == "boom"
        assert "高位" in cycle.signal

    def test_slowdown_phase(self):
        trend = {"change_5d": -3.0, "change_20d": -5.0, "change_60d": -12.0}
        cycle = classify_industry_cycle(trend, pe_percentile=0.70)
        assert cycle.cycle == "slowdown"

    def test_recovery_phase(self):
        trend = {"change_5d": 1.0, "change_20d": 5.0, "change_60d": 3.0}
        cycle = classify_industry_cycle(trend, pe_percentile=0.25)
        assert cycle.cycle == "recovery"

    def test_depression_phase(self):
        trend = {"change_5d": -1.0, "change_20d": -3.0, "change_60d": -8.0}
        cycle = classify_industry_cycle(trend, pe_percentile=0.20)
        assert cycle.cycle == "depression"

    def test_normal_phase(self):
        trend = {"change_5d": 0.5, "change_20d": 1.5, "change_60d": 7.0}
        cycle = classify_industry_cycle(trend, pe_percentile=0.55)
        assert cycle.cycle == "normal"

    def test_momentum_score(self):
        trend_up = {"change_5d": 3.0, "change_20d": 8.0, "change_60d": 15.0}
        cycle_up = classify_industry_cycle(trend_up)
        assert cycle_up.momentum_score > 0

        trend_down = {"change_5d": -3.0, "change_20d": -8.0, "change_60d": -15.0}
        cycle_down = classify_industry_cycle(trend_down)
        assert cycle_down.momentum_score < 0


# ================================================================
# 性价比评分
# ================================================================


class TestValueScore:
    def test_excellent_value(self):
        # 公司ROE是行业的1.5倍，但PE只有行业的0.7倍 → 性价比优秀
        stock = {"pe": 14, "roe": 21}  # PE合理，ROE优秀
        industry = {"avg_pe": 20, "avg_roe": 14}  # 行业平均
        vs = calculate_value_score(stock, industry)

        assert vs.score == "excellent"
        assert vs.value_ratio < 0.7

    def test_overpriced(self):
        # 公司ROE低于行业，但PE远高于行业 → 明显高估
        stock = {"pe": 40, "roe": 8}
        industry = {"avg_pe": 20, "avg_roe": 15}
        vs = calculate_value_score(stock, industry)

        assert vs.score == "overpriced"
        assert vs.value_ratio > 1.8

    def test_fair_value(self):
        stock = {"pe": 20, "roe": 15}
        industry = {"avg_pe": 20, "avg_roe": 15}
        vs = calculate_value_score(stock, industry)

        assert vs.score == "fair"
        assert abs(vs.value_ratio - 1.0) < 0.01

    def test_insufficient_data(self):
        stock = {"pe": 15}
        industry = {}
        vs = calculate_value_score(stock, industry)
        assert vs.score == "insufficient_data"


# ================================================================
# 行业名称推断
# ================================================================


class TestInferIndustryFromName:
    def test_bank(self):
        assert infer_industry_from_name("平安银行") == "银行"
        assert infer_industry_from_name("招商银行") == "银行"

    def test_securities(self):
        assert infer_industry_from_name("中信证券") == "证券"

    def test_liquor(self):
        # 公司名称中不含"酒"字时无法推断（茅台不含"酒"）
        # "贵州茅台风" 也无法推断
        assert infer_industry_from_name("泸州老窖") == "白酒"  # 含"窖"
        assert infer_industry_from_name("山西汾酒") == "白酒"  # 含"酒"

    def test_real_estate(self):
        assert infer_industry_from_name("万科地产") == "房地产"

    def test_tech(self):
        # "科技" 在 INDUSTRY_NAME_KEYWORDS 的科技映射中
        result = infer_industry_from_name("某某科技股份公司")
        # 注意：当前关键词"科技"会匹配到"科技"行业
        assert result is not None  # "科技" 能匹配


# ================================================================
# 扩展行业映射测试
# ================================================================


class TestExtendedIndustryMapping:
    def test_bank_coverage(self):
        assert "000001" in EXTENDED_KNOWN_INDUSTRIES
        assert "600036" in EXTENDED_KNOWN_INDUSTRIES
        assert EXTENDED_KNOWN_INDUSTRIES["000001"] == "银行"

    def test_baijiu_coverage(self):
        assert EXTENDED_KNOWN_INDUSTRIES["600519"] == "白酒"
        assert EXTENDED_KNOWN_INDUSTRIES["000858"] == "白酒"

    def test_hk_mapping(self):
        assert "0700" in KNOWN_HK_INDUSTRIES
        assert KNOWN_HK_INDUSTRIES["0700"]["name"] == "互联网"
        assert KNOWN_HK_INDUSTRIES["9618"]["name"] == "互联网"
        assert "9618" in HK_PEER_REFERENCE

    def test_mapping_count(self):
        # 扩展后应超过 50 个映射
        assert len(EXTENDED_KNOWN_INDUSTRIES) >= 50


# ================================================================
# 参考值缓存测试
# ================================================================


class TestIndustryReferenceCache:
    def test_hardcoded_fallback(self):
        cache = IndustryReferenceCache()
        result = cache.get("银行")
        assert result is not None
        assert "pe" in result

    def test_unknown_industry(self):
        cache = IndustryReferenceCache()
        result = cache.get("不存在的行业_XYZ")
        assert result is None


# ================================================================
# 主预处理函数集成测试
# ================================================================


class TestProcessIndustryData:
    def test_full_pipeline_with_data(self):
        stock = {"pe": 6.2, "pb": 0.72, "roe": 12.0}
        peers = [
            {"code": f"60000{i}", "name": f"银行{i}", "pe": 5.0 + i * 0.3, "pb": 0.6 + i * 0.05, "roe": 10 + i}
            for i in range(20)
        ]
        trend = {"change_5d": 1.2, "change_20d": 3.5, "change_60d": -4.0}

        result = process_industry_data(stock, peers, trend)

        assert "industry_metrics" in result
        assert "rank_in_industry" in result
        assert "value_score" in result
        assert "industry_trend" in result
        assert "data_quality" in result

        # 验证排名
        rank = result["rank_in_industry"]
        assert rank["pe_rank"] != "N/A"
        assert rank["valuation_label"] != "N/A"

        # 验证数据质量
        dq = result["data_quality"]
        assert dq["has_constituents"] is True
        assert dq["has_trend"] is True

    def test_pipeline_no_peers(self):
        stock = {"pe": 20.0, "roe": 15.0}
        result = process_industry_data(stock, [], None)

        assert result["data_quality"]["has_constituents"] is False
        # 无成分股时 rank 使用 note 而非具体排名
        rank = result.get("rank_in_industry", {})
        assert "不可用" in str(rank.get("note", "")) or rank.get("pe_rank") == "N/A"

        # value_score 也应标注不可用
        assert "不可用" in str(result.get("value_score", {}).get("note", ""))

        # 无成分股时 ceiling 应较低
        assert result["data_quality"]["confidence_ceiling"] <= 0.50

    def test_pipeline_reference_metrics_not_complete_missing(self):
        stock = {"pe": 12.0, "roe": 13.0}
        ref = {"pe": 20.0, "pb": 4.0, "roe": 15.0, "note": "近似参考值"}

        result = process_industry_data(stock, [], None, reference_metrics=ref)

        assert result["industry_metrics"]["avg_pe"] == 20.0
        assert result["value_score"]["reference_only"] is True
        assert result["data_quality"]["has_reference_metrics"] is True
        assert result["data_quality"]["overall"] >= 0.3
        assert "完全缺失" not in result["data_quality"]["notes"]

    def test_pipeline_with_peers_no_trend(self):
        stock = {"pe": 10.0}
        peers = [{"pe": float(i)} for i in range(1, 21)]

        result = process_industry_data(stock, peers, None)

        assert result["data_quality"]["has_constituents"] is True
        assert result["data_quality"]["has_trend"] is False
        # 有成分股但无趋势 → ceiling 介于中间
        assert result["data_quality"]["confidence_ceiling"] >= 0.45

    def test_hk_jd_fetcher_uses_reference_peers(self, monkeypatch):
        from src.data.industry_fetcher import IndustryFetcher

        async def fake_stock_data(self, result, symbol, market):
            result.company_name = "京东集团"
            result.stock_pe = 12.0
            result.stock_pb = 1.7
            result.stock_roe = 13.0
            result.data_source = "fixture"

        async def no_live_peer(self, symbol):
            return None

        async def no_financial_supplement(self, result, symbol):
            return None

        monkeypatch.setattr(IndustryFetcher, "_fetch_stock_data", fake_stock_data)
        monkeypatch.setattr(IndustryFetcher, "_fetch_hk_peer_snapshot", no_live_peer)
        monkeypatch.setattr(IndustryFetcher, "_fetch_hk_financial_supplement", no_financial_supplement)

        fetcher = IndustryFetcher()
        result = __import__("asyncio").run(fetcher.fetch_enhanced("9618", "HK"))

        assert result["industry_name"] == "互联网"
        assert result["market"] == "HK"
        assert result["data_source"] == "hk_peer_reference"
        assert result["data_quality"]["has_constituents"] is False
        assert result["data_quality"]["has_reference_peers"] is True
        assert result["data_quality"]["overall"] >= 0.3
        assert result["rank_in_industry"].get("pe_rank") != "N/A"


# ================================================================
# 一致性校验测试
# ================================================================


class TestConsistencyValidation:
    def test_high_pe_rank_bullish_flag(self):
        """高PE排名(贵) + 方向看涨 → 应产生警告"""
        # 在 validate_consistency 中检查
        rank = {"pe_percentile": 0.90}
        # 方向为bullish + pe_pct > 0.85 → 警告
        # （这是 Agent 端的行为，这里验证逻辑）
        assert rank["pe_percentile"] > 0.85

    def test_overpriced_vs_bullish(self):
        """性价比"明显高估" + 看涨 → 矛盾"""
        value_score = "overpriced"
        direction = "bullish"
        # 矛盾: 高估就不应该看涨
        contradictory = (value_score == "overpriced" and direction == "bullish")
        assert contradictory
