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
