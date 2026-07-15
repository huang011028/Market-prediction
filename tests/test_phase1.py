"""
Phase 1 集成测试

测试内容:
- PriceFetcher 市场识别
- Aggregator 结果解析
- 端到端流程（需要 akshare + LLM Key）
"""

import json
import pytest
from src.data.price_fetcher import PriceFetcher
from src.agents.aggregator import Aggregator
from src.core.result import (
    AnalysisResult, FinalReport, Direction, Magnitude,
)
from src.core.llm_client import LLMResponse
from config.weight_manager import WeightConfig


# ================================================================
# PriceFetcher 测试
# ================================================================


class TestMarketIdentification:
    """市场识别测试"""

    def test_a_share_6digit(self):
        fetcher = PriceFetcher()
        assert fetcher._identify_market("000001") == "A"
        assert fetcher._identify_market("600519") == "A"

    def test_hk_share_4digit(self):
        fetcher = PriceFetcher()
        assert fetcher._identify_market("0700") == "HK"
        assert fetcher._identify_market("9988") == "HK"

    def test_hk_share_with_suffix(self):
        fetcher = PriceFetcher()
        assert fetcher._identify_market("0700.HK") == "HK"

    def test_us_share(self):
        fetcher = PriceFetcher()
        assert fetcher._identify_market("AAPL") == "US"
        assert fetcher._identify_market("TSLA") == "US"


class TestTechnicalIndicators:
    """技术指标计算测试"""

    @pytest.fixture
    def sample_df(self):
        import pandas as pd
        import numpy as np

        dates = pd.date_range("2026-01-01", periods=100, freq="B")
        np.random.seed(42)
        close = 100 + np.cumsum(np.random.randn(100) * 1.5)
        high = close + np.abs(np.random.randn(100) * 0.5)
        low = close - np.abs(np.random.randn(100) * 0.5)
        volume = np.random.randint(1000000, 5000000, 100)

        return pd.DataFrame({
            "close": close,
            "high": high,
            "low": low,
            "volume": volume,
        }, index=dates)

    def test_ma_calculation(self, sample_df):
        fetcher = PriceFetcher()
        indicators = fetcher._compute_indicators(sample_df)

        assert indicators["MA5"] is not None
        assert indicators["MA10"] is not None
        assert indicators["MA20"] is not None
        assert indicators["MA60"] is not None

    def test_macd_calculation(self, sample_df):
        fetcher = PriceFetcher()
        indicators = fetcher._compute_indicators(sample_df)

        assert indicators["MACD_DIF"] is not None
        assert indicators["MACD_DEA"] is not None
        assert "MACD_signal" in indicators

    def test_rsi_calculation(self, sample_df):
        fetcher = PriceFetcher()
        indicators = fetcher._compute_indicators(sample_df)

        assert indicators["RSI"] is not None
        assert 0 <= indicators["RSI"] <= 100

    def test_bollinger_calculation(self, sample_df):
        fetcher = PriceFetcher()
        indicators = fetcher._compute_indicators(sample_df)

        assert indicators["BOLL_upper"] is not None
        assert indicators["BOLL_mid"] is not None
        assert indicators["BOLL_lower"] is not None
        assert indicators["BOLL_upper"] > indicators["BOLL_mid"] > indicators["BOLL_lower"]

    def test_vol_ratio(self, sample_df):
        fetcher = PriceFetcher()
        indicators = fetcher._compute_indicators(sample_df)

        assert indicators["VOL_ratio"] is not None
        assert indicators["VOL_ratio"] > 0

    def test_insufficient_data(self):
        """数据不足时不应崩溃"""
        import pandas as pd
        df = pd.DataFrame({
            "close": [100, 101, 102],
            "high": [101, 102, 103],
            "low": [99, 100, 101],
            "volume": [1000, 2000, 1500],
        })
        fetcher = PriceFetcher()
        indicators = fetcher._compute_indicators(df)
        # 应该返回，部分指标为 None 但不抛异常
        assert isinstance(indicators, dict)

    def test_patterns_generation(self, sample_df):
        fetcher = PriceFetcher()
        indicators = fetcher._compute_indicators(sample_df)
        patterns = fetcher._identify_patterns(sample_df, indicators)
        assert "ma_arrangement" in patterns
        assert "price_vs_ma" in patterns
        assert "rsi_zone" in patterns
        assert "boll_position" in patterns

    def test_fetch_as_of_truncates_future_data(self, sample_df, monkeypatch):
        """历史快照只能看到 as_of 当天及之前的数据。"""
        import asyncio

        fetcher = PriceFetcher()
        monkeypatch.setattr(fetcher, "_fetch_ohlcv", lambda *args, **kwargs: sample_df)

        as_of = sample_df.index[59]
        data = asyncio.run(fetcher.fetch_as_of("000001", as_of, lookback_days=120))

        assert data.price_current == pytest.approx(float(sample_df.loc[as_of, "close"]))
        assert data.price_current != pytest.approx(float(sample_df["close"].iloc[-1]))
        assert data.trading_days == 60
        assert str(as_of.date()) in data.data_period

    def test_fetch_as_of_uses_history_window_when_recent_source_misses_as_of(
        self, sample_df, monkeypatch
    ):
        """常规行情源只覆盖近期时，回测应按历史窗口兜底补拉。"""
        import asyncio

        fetcher = PriceFetcher()
        recent_only = sample_df.iloc[-10:]
        calls = {"history_window": 0}

        monkeypatch.setattr(fetcher, "_fetch_ohlcv", lambda *args, **kwargs: recent_only)

        def fake_history_window(*args, **kwargs):
            calls["history_window"] += 1
            return sample_df.iloc[:80]

        monkeypatch.setattr(fetcher, "_fetch_ohlcv_history_window", fake_history_window)

        as_of = sample_df.index[59]
        data = asyncio.run(fetcher.fetch_as_of("0700", as_of, lookback_days=120))

        assert calls["history_window"] == 1
        assert data.price_current == pytest.approx(float(sample_df.loc[as_of, "close"]))
        assert data.trading_days == 60

    def test_fetch_close_near_prefers_expected_side(self, sample_df, monkeypatch):
        """验证按日期取价时不会总是拿最新收盘价。"""
        import asyncio

        fetcher = PriceFetcher()
        monkeypatch.setattr(fetcher, "_fetch_ohlcv", lambda *args, **kwargs: sample_df)

        target = sample_df.index[10].to_pydatetime()
        before = asyncio.run(fetcher.fetch_close_near("000001", target, prefer="on_or_before"))
        after = asyncio.run(fetcher.fetch_close_near("000001", target, prefer="on_or_after"))

        assert before == pytest.approx(float(sample_df.iloc[10]["close"]))
        assert after == pytest.approx(float(sample_df.iloc[10]["close"]))
        assert before != pytest.approx(float(sample_df.iloc[-1]["close"]))

    def test_fetch_close_near_uses_history_window_when_recent_source_misses_target(
        self, sample_df, monkeypatch
    ):
        """验证价格也要能从历史窗口补拉，避免早期回测只看到近期 K 线。"""
        import asyncio

        fetcher = PriceFetcher()
        recent_only = sample_df.iloc[-10:]
        calls = {"history_window": 0}

        monkeypatch.setattr(fetcher, "_fetch_ohlcv", lambda *args, **kwargs: recent_only)

        def fake_history_window(*args, **kwargs):
            calls["history_window"] += 1
            return sample_df.iloc[:30]

        monkeypatch.setattr(fetcher, "_fetch_ohlcv_history_window", fake_history_window)

        target = sample_df.index[10].to_pydatetime()
        close = asyncio.run(fetcher.fetch_close_near("0700", target, prefer="on_or_after"))

        assert calls["history_window"] == 1
        assert close == pytest.approx(float(sample_df.iloc[10]["close"]))


# ================================================================
# Aggregator 测试
# ================================================================


class TestAggregatorParsing:
    """汇总分析师解析测试"""

    def test_extract_json_code_block(self):
        text = """一些前缀文字
```json
{"direction": "bullish", "confidence": 0.75}
```
一些后缀"""
        result = Aggregator._extract_json(text)
        assert "bullish" in result

    def test_extract_json_braces(self):
        text = """分析结论如下：{"direction": "bearish", "confidence": 0.6}以上是分析结果"""
        result = Aggregator._extract_json(text)
        assert "bearish" in result

    def test_parse_valid_response(self):
        llm_content = json.dumps({
            "direction": "bullish",
            "magnitude": {"min_pct": 2.0, "max_pct": 5.0},
            "confidence": 0.72,
            "summary": "综合看涨",
            "key_risks": ["风险1"],
            "disagreements": [],
        })

        aggregator = Aggregator.__new__(Aggregator)
        report = aggregator._parse_response(
            llm_content, "000001", "短期", [], []
        )

        assert report.direction == Direction.BULLISH
        assert report.confidence == 0.72
        assert report.magnitude is not None
        assert report.magnitude.min_pct == 2.0
        assert report.summary == "综合看涨"

    def test_parse_distribution_and_decision_fields(self):
        llm_content = json.dumps({
            "direction": "neutral",
            "magnitude": {"min_pct": -1.0, "max_pct": 1.0},
            "confidence": 0.52,
            "expected_excess_return_pct": 0.4,
            "prob_up": 0.31,
            "prob_down": 0.27,
            "prob_no_edge": 0.42,
            "edge_score": 0.12,
            "decision": "observe",
            "neutral_reason": "priced_in",
            "no_trade_reason": "priced_in",
            "summary": "利好已有定价风险",
            "key_risks": [],
            "disagreements": [],
            "prediction_target": {
                "horizon": "5d",
                "expected_return_pct": 0.4,
                "prob_up": 0.31,
                "prob_down": 0.27,
                "prob_neutral": 0.42,
            },
        })

        aggregator = Aggregator.__new__(Aggregator)
        report = aggregator._parse_response(
            llm_content, "000001", "短期(1周)", [], []
        )

        assert report.expected_excess_return_pct == 0.4
        assert report.prob_up == 0.31
        assert report.prob_down == 0.27
        assert report.prob_no_edge == 0.42
        assert report.decision == "observe"
        assert report.neutral_reason == "priced_in"
        assert report.prediction_target.prob_neutral == 0.42

    def test_parse_invalid_json(self):
        llm_content = "这不是有效的 JSON，LLM 返回了纯文本分析..."

        aggregator = Aggregator.__new__(Aggregator)
        report = aggregator._parse_response(
            llm_content, "000001", "短期", [], []
        )

        assert report.direction == Direction.NEUTRAL
        assert "LLM 返回格式异常" not in report.summary  # 文本本身就是 summary


class TestAggregatorFallback:
    """汇总分析降级测试"""

    def test_fallback_bullish_majority(self):
        results = [
            AnalysisResult(
                agent_name="A", target="T", timeframe="F",
                direction=Direction.BULLISH,
                confidence=0.7, reasoning="x",
            ),
            AnalysisResult(
                agent_name="B", target="T", timeframe="F",
                direction=Direction.BULLISH,
                confidence=0.6, reasoning="x",
            ),
        ]

        aggregator = Aggregator.__new__(Aggregator)
        report = aggregator._fallback_report("T", "F", results, [], "测试降级")

        assert report.direction == Direction.BULLISH
        assert "测试降级" in report.summary

    def test_fallback_mixed(self):
        results = [
            AnalysisResult(
                agent_name="A", target="T", timeframe="F",
                direction=Direction.BULLISH,
                confidence=0.7, reasoning="x",
            ),
            AnalysisResult(
                agent_name="B", target="T", timeframe="F",
                direction=Direction.BEARISH,
                confidence=0.6, reasoning="x",
            ),
        ]

        aggregator = Aggregator.__new__(Aggregator)
        report = aggregator._fallback_report("T", "F", results, [], "测试")

        # 平局时 fallback 到 neutral
        assert report.direction == Direction.NEUTRAL


class TestAggregatorFundamentalEvidence:
    """汇总层消费公司前景结构化证据。"""

    def _fundamental_result(self, status="degraded"):
        return AnalysisResult(
            agent_name="公司前景分析师",
            target="002396",
            timeframe="中期(1月)",
            direction=Direction.BULLISH,
            magnitude=Magnitude(2.0, 8.0),
            confidence=0.58,
            reasoning="基本面看多",
            risks=["估值消化风险"],
            status=status,
            data_summary={
                "data_quality": {"overall_quality": 0.80},
                "quality_scorecard": {"total": 78, "rating": "excellent"},
                "valuation_analysis": {"pe_percentile_3yr": 0.88},
                "value_trap_analysis": {"is_trap": False, "signals": []},
                "consistency_issues": ["好公司高估不应强看涨"],
                "evidence": {
                    "decision_matrix": {
                        "matrix_position": "好公司+高估",
                        "suggested_direction": "neutral",
                        "reason": "好公司但估值偏高",
                    },
                    "confidence_constraints": {
                        "max_confidence": 0.55,
                        "hard_caps": ["估值偏高时限制强方向置信度"],
                    },
                    "data_quality": {"overall_quality": 0.80},
                    "quality_scorecard": {"total": 78, "rating": "excellent"},
                    "valuation_analysis": {"pe_percentile_3yr": 0.88},
                    "value_trap_analysis": {"is_trap": False, "signals": []},
                    "evidence": {
                        "bullish": ["质量评分78分(excellent)"],
                        "bearish": ["PE 处于历史88%分位"],
                        "neutral": [],
                    },
                },
            },
        )

    def test_score_quality_uses_fundamental_evidence(self):
        aggregator = Aggregator.__new__(Aggregator)
        result = self._fundamental_result()

        quality = aggregator._score_quality(result)

        assert quality["source_type"] == "degraded"
        assert quality["data_quality"] == "good"
        assert quality["reliability"] == "medium"
        assert quality["weight_factor"] < 1.0
        assert any("矩阵建议neutral" in issue for issue in quality["issues"])

    def test_context_includes_structured_fundamental_evidence(self):
        aggregator = Aggregator.__new__(Aggregator)
        result = self._fundamental_result()
        quality = {result.agent_name: aggregator._score_quality(result)}
        weights = WeightConfig(
            agent_weights={"公司前景分析师": 0.22},
            synthesis_weight=0.13,
        )

        context = aggregator._build_context(
            "002396",
            "中期(1月)",
            [result],
            quality,
            weights,
            [],
            {"level": "none"},
            [],
        )

        assert "结构化基本面证据" in context
        assert "好公司+高估" in context
        assert "PE分位" in context
        assert "若矩阵、价值陷阱或置信上限与方向冲突" in context
        assert "不得只按 reasoning 的方向直接采信" in context

    def test_final_report_exports_fundamental_evidence(self):
        report = FinalReport(
            target="002396",
            timeframe="中期(1月)",
            direction=Direction.NEUTRAL,
            confidence=0.52,
            agent_results=[self._fundamental_result()],
            summary="综合中性",
        )

        payload = report.to_dict()
        markdown = report.to_markdown()

        assert payload["fundamental_evidence"]
        assert payload["fundamental_evidence"][0]["matrix_position"] == "好公司+高估"
        assert "公司前景证据摘要" in markdown
        assert "好公司+高估" in markdown


class TestAggregatorStructuredEvidence:
    """汇总层统一消费各 Agent 结构化证据。"""

    def _technical_result(self):
        return AnalysisResult(
            agent_name="近期股价分析师",
            target="002396",
            timeframe="短期(1周)",
            direction=Direction.BEARISH,
            magnitude=Magnitude(-6.0, -2.0),
            confidence=0.62,
            reasoning="短线下跌放量，动量偏空",
            data_quality_score=0.92,
            data_summary={
                "quality": "ok",
                "data_quality": {"status": "ok", "score": 0.92},
                "consistency_issues": [],
                "evidence": {
                    "decision_matrix": {
                        "matrix_position": "短线down+动量bearish+量能confirm_down+风险收益均衡",
                        "suggested_direction": "bearish",
                        "reason": "趋势、动量或量能证据偏空。",
                    },
                    "confidence_constraints": {
                        "max_confidence": 0.66,
                        "technical_confidence": 0.66,
                        "hard_caps": [],
                    },
                    "support_resistance": {
                        "nearest_support": 21.5,
                        "nearest_resistance": 24.2,
                    },
                    "risk_levels": {"risk_reward_ratio": 1.1},
                    "evidence": {
                        "bullish": [],
                        "bearish": ["短期趋势向下", "下跌伴随放量"],
                        "neutral": [],
                    },
                },
            },
        )

    def _news_result(self, status="degraded"):
        return AnalysisResult(
            agent_name="最新新闻分析师",
            target="0700",
            timeframe="短期(1周)",
            direction=Direction.BULLISH,
            magnitude=Magnitude(1.0, 5.0),
            confidence=0.35,
            reasoning="新闻面偏多但样本少",
            risks=["新闻样本不足"],
            status=status,
            data_quality_score=0.3,
            data_summary={
                "quality": 0.3,
                "consistency_issues": ["新闻稀少时不应高置信看涨"],
                "evidence": {
                    "decision_matrix": {
                        "matrix_position": "新闻稀少+情绪偏正+正面催化",
                        "suggested_direction": "neutral",
                        "reason": "新闻不足，不能形成强方向判断。",
                    },
                    "source_quality": {
                        "quality_score": 0.3,
                        "is_available": True,
                        "source_count": 1,
                    },
                    "event_impact_matrix": {
                        "dominant_event": "rating",
                        "positive_event_weight": 1.8,
                        "negative_event_weight": 0.0,
                    },
                    "confidence_constraints": {
                        "max_confidence": 0.35,
                        "hard_caps": ["新闻数量不超过2条，confidence 不超过0.35"],
                    },
                    "evidence": {
                        "bullish": ["正面新闻2条，加权正面得分1.80"],
                        "bearish": [],
                        "neutral": ["新闻来源单一"],
                    },
                },
            },
        )

    def _industry_result(self):
        return AnalysisResult(
            agent_name="行业对比分析师",
            target="002396",
            timeframe="中期(1月)",
            direction=Direction.BEARISH,
            magnitude=Magnitude(-8.0, -2.0),
            confidence=0.52,
            reasoning="行业下行且相对偏贵",
            data_summary={
                "data_quality": {"overall": 0.72},
                "evidence": {
                    "decision_matrix": {
                        "matrix_position": "相对偏贵+行业下行",
                        "suggested_direction": "bearish",
                        "reason": "估值偏贵且行业趋势缺少支撑。",
                    },
                    "confidence_constraints": {
                        "max_confidence": 0.6,
                        "hard_caps": [],
                    },
                    "evidence": {
                        "bullish": [],
                        "bearish": ["PE 处于行业82%分位"],
                        "neutral": [],
                    },
                },
            },
        )

    def test_score_quality_uses_generic_structured_evidence(self):
        aggregator = Aggregator.__new__(Aggregator)
        result = self._news_result()

        quality = aggregator._score_quality(result)

        assert quality["data_quality"] == "partial"
        assert quality["source_type"] == "degraded"
        assert quality["reliability"] == "low"
        assert quality["weight_factor"] < 1.0
        assert quality["structured_evidence"]["domain"] == "新闻"
        assert any("矩阵建议neutral" in issue for issue in quality["issues"])

    def test_score_quality_uses_technical_structured_evidence(self):
        aggregator = Aggregator.__new__(Aggregator)
        result = self._technical_result()

        quality = aggregator._score_quality(result)

        assert quality["data_quality"] == "good"
        assert quality["source_type"] == "structured_技术"
        assert quality["structured_evidence"]["domain"] == "技术"
        assert quality["structured_evidence"]["suggested_direction"] == "bearish"

    def test_context_includes_generic_structured_evidence(self):
        aggregator = Aggregator.__new__(Aggregator)
        technical = self._technical_result()
        news = self._news_result()
        industry = self._industry_result()
        quality = {
            technical.agent_name: aggregator._score_quality(technical),
            news.agent_name: aggregator._score_quality(news),
            industry.agent_name: aggregator._score_quality(industry),
        }
        weights = WeightConfig(
            agent_weights={
                "近期股价分析师": 0.22,
                "最新新闻分析师": 0.18,
                "行业对比分析师": 0.18,
            },
            synthesis_weight=0.13,
        )

        context = aggregator._build_context(
            "0700",
            "短期(1周)",
            [technical, news, industry],
            quality,
            weights,
            [],
            {"level": "none"},
            [],
        )

        assert "结构化 Agent 证据" in context
        assert "短线down+动量bearish+量能confirm_down" in context
        assert "新闻稀少+情绪偏正+正面催化" in context
        assert "新闻数量不超过2条" in context
        assert "相对偏贵+行业下行" in context
        assert "不得只按 reasoning 的方向直接采信" in context

    def test_final_report_exports_structured_evidence(self):
        report = FinalReport(
            target="0700",
            timeframe="短期(1周)",
            direction=Direction.NEUTRAL,
            confidence=0.45,
            agent_results=[self._technical_result(), self._news_result(), self._industry_result()],
            summary="综合中性",
        )

        payload = report.to_dict()
        markdown = report.to_markdown()

        assert payload["structured_evidence"]
        assert any(item["domain"] == "技术" for item in payload["structured_evidence"])
        assert any(item["domain"] == "新闻" for item in payload["structured_evidence"])
        assert any(item["domain"] == "行业" for item in payload["structured_evidence"])
        assert "结构化证据摘要" in markdown
        assert "新闻稀少+情绪偏正+正面催化" in markdown

    def test_final_decision_guardrail_neutralizes_conflicting_strong_call(self):
        aggregator = Aggregator.__new__(Aggregator)
        technical = self._technical_result()
        industry = self._industry_result()
        report = FinalReport(
            target="002396",
            timeframe="短期(1周)",
            direction=Direction.BULLISH,
            magnitude=Magnitude(2.0, 8.0),
            confidence=0.78,
            agent_results=[technical, industry],
            summary="LLM 综合看涨",
        )
        quality = {
            technical.agent_name: aggregator._score_quality(technical),
            industry.agent_name: aggregator._score_quality(industry),
        }
        weights = WeightConfig(
            agent_weights={
                "近期股价分析师": 0.35,
                "行业对比分析师": 0.25,
            },
            synthesis_weight=0.10,
        )

        guarded = aggregator._apply_final_decision_guardrails(
            report, quality, weights,
        )

        assert guarded.direction == Direction.NEUTRAL
        assert guarded.magnitude is None
        assert guarded.confidence <= 0.45
        assert any("最终裁决复核" in item for item in guarded.disagreements)
        assert any("最终裁决护栏" in item for item in guarded.key_risks)


# ================================================================
# 集成测试（需要 akshare，标记为 slow）
# ================================================================


@pytest.mark.slow
class TestPriceFetcherIntegration:
    """真实数据获取集成测试"""

    @pytest.mark.asyncio
    async def test_fetch_a_share(self):
        """测试获取 A 股真实数据"""
        fetcher = PriceFetcher()
        try:
            data = await fetcher.fetch("000001", "3mo")
            assert data.trading_days > 0
            assert data.price_current > 0
            assert data.indicators["MA5"] is not None
        except ImportError:
            pytest.skip("akshare 未安装")
        except Exception as e:
            if "akshare" in str(e).lower():
                pytest.skip(f"akshare 数据获取失败: {e}")
            raise

    @pytest.mark.asyncio
    async def test_fetch_hk_share(self):
        """测试获取港股真实数据"""
        fetcher = PriceFetcher()
        try:
            data = await fetcher.fetch("0700", "3mo")
            assert data.trading_days > 0
        except ImportError:
            pytest.skip("akshare 未安装")
        except Exception as e:
            if "akshare" in str(e).lower() or "yfinance" in str(e).lower():
                pytest.skip(f"数据获取失败: {e}")
            raise
