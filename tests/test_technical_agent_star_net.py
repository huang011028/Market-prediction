import asyncio
import sys
import types

import pandas as pd
import pytest

from src.agents.technical_analyst import TechnicalAnalyst
from src.core.result import AnalysisResult, Direction, Magnitude
from src.data.price_fetcher import PriceFetcher
from src.data.technical_features import (
    build_recent_trend,
    build_intraday_signals,
    assess_price_data_quality,
    build_technical_snapshot,
    describe_market_freshness,
)


def make_price_df(days: int = 80) -> pd.DataFrame:
    dates = pd.date_range("2026-03-16", periods=days, freq="B")
    close = [20 + i * 0.05 for i in range(days)]
    return pd.DataFrame({
        "open": [c - 0.1 for c in close],
        "high": [c + 0.4 for c in close],
        "low": [c - 0.4 for c in close],
        "close": close,
        "volume": [1_000_000 + i * 1000 for i in range(days)],
    }, index=dates)


def test_price_fetcher_resolves_chinese_name(monkeypatch):
    df = make_price_df()
    fetcher = PriceFetcher()
    calls = []

    def fake_fetch(symbol, market, period):
        calls.append((symbol, market, period))
        return df

    async def fake_weekly(symbol, market):
        return None

    def fake_intraday(symbol, market):
        return (
            [
                {"time": "2026-07-03 14:50", "date": "2026-07-03", "close": 23.2, "volume": 1000, "change_pct": 0},
                {"time": "2026-07-03 14:55", "date": "2026-07-03", "close": 23.4, "volume": 1200, "change_pct": 0.86},
            ],
            {"available": True, "source": "test_minute", "interval": "5m", "latest_time": "2026-07-03 14:55"},
        )

    monkeypatch.setattr(fetcher, "_fetch_ohlcv", fake_fetch)
    monkeypatch.setattr(fetcher, "_fetch_weekly_context", fake_weekly)
    monkeypatch.setattr(fetcher, "_fetch_intraday_context", fake_intraday)

    data = asyncio.run(fetcher.fetch("星网锐捷", "3mo"))

    assert calls[0][0] == "002396"
    assert calls[0][1] == "A"
    assert data.symbol == "002396"
    assert data.name == "星网锐捷"
    assert data.recent_trend
    assert len(data.intraday_trend) == 2
    assert data.intraday_meta["latest_time"] == "2026-07-03 14:55"
    assert data.intraday_signals["available"] is True
    assert data.data_quality["status"] == "ok"
    assert data.technical_snapshot["support_resistance"]["nearest_support"] is not None
    assert data.technical_snapshot["confidence_model"]["technical_confidence"] > 0


def test_a_share_fetch_uses_tencent_fallback(monkeypatch):
    def fail_daily(**kwargs):
        raise RuntimeError("offline")

    fake_akshare = types.SimpleNamespace(
        stock_zh_a_daily=fail_daily,
        stock_zh_a_hist=lambda **kwargs: pd.DataFrame(),
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_akshare)

    fetcher = PriceFetcher()
    calls = []

    def fake_tencent(symbol, market):
        calls.append((symbol, market))
        return make_price_df()

    monkeypatch.setattr(fetcher, "_fetch_from_tencent", fake_tencent)

    df = fetcher._fetch_a_share("002396", "3mo")

    assert calls == [("002396", "a")]
    assert not df.empty


def test_tencent_a_share_uses_prefixed_code(monkeypatch):
    import requests

    urls = []

    class FakeResponse:
        def json(self):
            return {
                "code": 0,
                "data": {
                    "sz002396": {
                        "qfqday": [
                            ["2026-06-01", "10", "11", "12", "9", "1000"],
                            ["2026-06-02", "11", "12", "13", "10", "1200"],
                        ]
                    }
                },
            }

    def fake_get(url, timeout, verify):
        urls.append(url)
        return FakeResponse()

    monkeypatch.setattr(requests, "get", fake_get)

    df = PriceFetcher()._fetch_from_tencent("002396", market="a")

    assert "param=sz002396" in urls[0]
    assert df is not None
    assert df["close"].iloc[-1] == 12


def test_recent_trend_shape():
    trend = build_recent_trend(make_price_df(), points=30)

    assert len(trend) == 30
    assert {"date", "open", "high", "low", "close", "volume", "change_pct", "daily_change_pct"} <= set(trend[0])


def test_intraday_signals_detect_strong_up():
    trend = [
        {"time": "2026-07-03 09:35", "date": "2026-07-03", "close": 10.00, "volume": 1000, "change_pct": 0.0},
        {"time": "2026-07-03 10:30", "date": "2026-07-03", "close": 10.25, "volume": 1400, "change_pct": 2.5},
        {"time": "2026-07-03 13:30", "date": "2026-07-03", "close": 10.55, "volume": 1600, "change_pct": 5.5},
        {"time": "2026-07-03 14:55", "date": "2026-07-03", "close": 10.62, "volume": 1800, "change_pct": 6.2},
    ]

    signals = build_intraday_signals(trend)

    assert signals["available"] is True
    assert signals["state"] == "strong_up"
    assert signals["change_pct"] > 0
    assert signals["latest_vs_vwap_pct"] is not None


def test_price_quality_fails_when_too_short():
    report = assess_price_data_quality(make_price_df(days=20))

    assert report["status"] == "failed"
    assert report["score"] < 0.4


def test_market_freshness_explains_weekend_daily_date():
    report = describe_market_freshness(
        "2026-07-03",
        market="A",
        now=pd.Timestamp("2026-07-05 12:00:00"),
    )

    assert report["status"] == "market_closed"
    assert report["market_closed"] is True
    assert "周末" in report["note"]

    intraday = describe_market_freshness(
        "2026-07-03",
        market="A",
        intraday_latest_time="2026-07-03 15:00",
        now=pd.Timestamp("2026-07-05 12:00:00"),
    )
    assert intraday["status"] == "intraday_market_closed"
    assert intraday["market_closed"] is True


def test_technical_agent_refuses_failed_quality():
    analyst = TechnicalAnalyst(llm=None)
    data = {
        "symbol": "002396",
        "name": "星网锐捷",
        "market": "A",
        "trading_days": 20,
        "data_quality": {
            "status": "failed",
            "score": 0.2,
            "issues": ["交易日不足 60 个: 20"],
        },
        "recent_trend": [],
    }

    result = asyncio.run(analyst.analyze(data, {"target": "002396", "timeframe": "短期(1周)"}))

    assert result.direction == Direction.NEUTRAL
    assert result.status == "failed"
    assert result.confidence <= 0.2
    assert "数据不足" in result.reasoning


def test_technical_prompt_uses_snapshot_contract():
    analyst = TechnicalAnalyst(llm=None)
    df = make_price_df()
    snapshot = build_technical_snapshot(
        df,
        indicators={"RSI": 62, "MACD_signal": "bullish_holding", "MA20": 22.8, "MA60": 21.4},
        data_quality=assess_price_data_quality(df),
        symbol="002396",
        name="星网锐捷",
        market="A",
    )
    data = {
        "symbol": "002396",
        "name": "星网锐捷",
        "market": "A",
        "data_period": "2026-03-16~2026-07-03",
        "trading_days": len(df),
        "price_summary": {"latest_close": 23.95, "change_5d_pct": 1.1, "change_20d_pct": 4.0},
        "recent_closes": [22.1, 22.3, 22.5],
        "indicators": {"RSI": 62, "MACD_signal": "bullish_holding"},
        "patterns": {},
        "technical_snapshot": snapshot,
        "intraday_signals": build_intraday_signals([
            {"time": "2026-07-03 14:50", "date": "2026-07-03", "close": 23.2, "volume": 1000, "change_pct": 0},
            {"time": "2026-07-03 14:55", "date": "2026-07-03", "close": 23.5, "volume": 1200, "change_pct": 1.29},
        ]),
    }

    prompt = analyst._build_user_prompt(data, {"target": "002396", "timeframe": "短期(1周)"})

    assert "技术证据包" in prompt
    assert "不得编造未提供的价格" in prompt
    assert "decision_matrix" in prompt
    assert "confidence_constraints" in prompt
    assert "support_resistance" in prompt
    assert "intraday_signals" in prompt

    evidence = analyst._build_evidence_packet(data, "短期(1周)")
    assert evidence["decision_matrix"]["suggested_direction"] in {"bullish", "bearish", "neutral"}
    assert "max_confidence" in evidence["confidence_constraints"]
    assert "support_resistance" in evidence
    assert evidence["evidence"]["bullish"] or evidence["evidence"]["neutral"]

    summary = analyst._build_data_summary(data)
    assert summary["evidence"]["decision_matrix"]
    assert summary["evidence"]["confidence_constraints"]["max_confidence"] >= 0


def test_intraday_selloff_caps_bullish_confidence():
    analyst = TechnicalAnalyst(llm=None)
    df = make_price_df()
    snapshot = build_technical_snapshot(
        df,
        indicators={"RSI": 62, "MACD_signal": "bullish_holding", "MA20": 22.8, "MA60": 21.4},
        data_quality=assess_price_data_quality(df),
        symbol="002396",
        name="星网锐捷",
        market="A",
    )
    snapshot["confidence_model"]["technical_confidence"] = 0.9
    snapshot["confidence_model"]["suggested_direction"] = "bullish"
    intraday_signals = build_intraday_signals([
        {"time": "2026-07-03 09:35", "date": "2026-07-03", "close": 10.60, "volume": 1000, "change_pct": 0},
        {"time": "2026-07-03 10:30", "date": "2026-07-03", "close": 10.30, "volume": 1400, "change_pct": -2.83},
        {"time": "2026-07-03 13:30", "date": "2026-07-03", "close": 10.05, "volume": 1600, "change_pct": -5.19},
        {"time": "2026-07-03 14:55", "date": "2026-07-03", "close": 9.85, "volume": 1800, "change_pct": -7.08},
    ])
    result = AnalysisResult(
        agent_name="近期股价分析师",
        target="002396",
        timeframe="短期(1周)",
        direction=Direction.BULLISH,
        magnitude=Magnitude(1.0, 3.0),
        confidence=0.8,
        reasoning="日线偏强",
    )

    analyst._apply_snapshot_constraints(result, {
        "technical_snapshot": snapshot,
        "intraday_signals": intraday_signals,
    })

    assert intraday_signals["state"] == "selloff"
    assert result.confidence <= 0.45
    assert any("分钟线" in risk for risk in result.risks)
