import json

import pytest
import pandas as pd

from src.core.calibration_bootstrap import (
    CalibrationBootstrapReport,
    CalibrationSample,
    FundamentalSnapshotCalibrationBootstrapper,
    IndustrySnapshotCalibrationBootstrapper,
    MacroSnapshotCalibrationBootstrapper,
    NewsSnapshotCalibrationBootstrapper,
)
from src.core.self_improvement_lab import SelfImprovementLab, SelfImprovementLabConfig
from src.utils.fundamental_calibrator import FundamentalConfidenceCalibrator
from src.utils.industry_calibrator import IndustryConfidenceCalibrator
from src.utils.macro_calibrator import MacroConfidenceCalibrator
from src.utils.news_calibrator import NewsConfidenceCalibrator


class FakeTechnicalBootstrapper:
    async def run(self, config):
        samples = []
        for idx, target in enumerate(config.targets):
            samples.append(
                CalibrationSample(
                    agent_name="近期股价分析师",
                    target=target,
                    as_of=f"2025-01-0{idx + 1}",
                    valid_date=f"2025-01-1{idx + 1}",
                    predicted_direction="bullish",
                    predicted_confidence=0.72,
                    actual_direction="bullish",
                    actual_change_pct=2.4,
                    was_correct=True,
                    price_start=10.0,
                    price_end=10.24,
                    buckets={"trend_bucket": "up"},
                    evidence_reason="fake trend",
                )
            )
        return CalibrationBootstrapReport(
            agent_name="近期股价分析师",
            total_candidates=len(samples),
            success_samples=len(samples),
            samples=samples,
        )


class DummyPriceFetcher:
    async def fetch_close_near(self, target, target_date, prefer="on_or_after", tolerance_days=10):
        return 10.0 if prefer == "on_or_before" else 10.8

    async def fetch_close_window(self, target, start_date, end_date):
        return pd.Series(
            [10.1, 10.8, 10.2],
            index=pd.to_datetime(["2025-01-02", "2025-01-04", "2025-01-07"]),
        )


@pytest.mark.asyncio
async def test_self_improvement_lab_builds_active_historical_evaluation(tmp_path):
    lab = SelfImprovementLab(technical_bootstrapper=FakeTechnicalBootstrapper())
    report = await lab.run(
        SelfImprovementLabConfig(
            targets=["000001", "AAPL"],
            start_date="2025-01-01",
            end_date="2025-01-31",
            evaluation_min_samples=1,
            output_dir=tmp_path / "lab",
            use_default_archives=False,
        )
    )

    assert report.total_samples == 2
    assert "近期股价分析师" in report.supported_agents
    assert any(item["agent_name"] == "公司前景分析师" for item in report.deferred_agents)
    assert (tmp_path / "lab" / "technical_bootstrap_report.json").exists()
    assert (tmp_path / "lab" / "historical_agent_evaluation.json").exists()
    assert (tmp_path / "lab" / "self_improvement_lab_report.md").exists()

    evaluation = json.loads(
        (tmp_path / "lab" / "historical_agent_evaluation.json").read_text(encoding="utf-8")
    )
    assert evaluation["agents"]["近期股价分析师"]["unique_cases"] == 2


@pytest.mark.asyncio
async def test_self_improvement_lab_can_cover_all_five_prediction_agents(tmp_path):
    news_path = tmp_path / "news.json"
    news_path.write_text(
        json.dumps([
            {
                "target": "000001",
                "as_of": "2025-01-01",
                "price_start": 10.0,
                "price_end": 10.8,
                "predicted_direction": "bullish",
                "predicted_confidence": 0.60,
                "news_data": {
                    "symbol": "000001",
                    "_market": "A",
                    "news_count": 5,
                    "sources_used": ["eastmoney", "sina"],
                    "preprocessing": {
                        "sentiment_stats": {
                            "positive": 4,
                            "negative": 0,
                            "neutral": 1,
                            "unknown": 0,
                            "weighted_positive_score": 3.2,
                            "weighted_negative_score": 0.0,
                        },
                        "category_breakdown": {"earnings": 4},
                        "anomaly_flags": {},
                        "top_news": [
                            {"_sentiment": "positive", "_category": "earnings", "_time_weight": 0.9}
                        ],
                    },
                },
            }
        ]),
        encoding="utf-8",
    )

    pit_path = tmp_path / "pit.json"
    pit_path.write_text(
        json.dumps([
            {
                "agent_name": "公司前景分析师",
                "target": "000001",
                "symbol": "000001",
                "market": "A",
                "as_of": "2025-01-01",
                "price_start": 10.0,
                "price_end": 10.8,
                "data": {
                    "symbol": "000001",
                    "market": "A",
                    "quality_scorecard": {"total": 82, "rating": "excellent"},
                    "valuation_analysis": {"pe_percentile_3yr": 0.22},
                    "financials": {"_trend": {"profit_trend": "growing"}},
                    "value_trap_analysis": {"is_trap": False},
                    "data_quality": {"overall_quality": 0.9, "confidence_ceiling": 0.72},
                },
            },
            {
                "agent_name": "行业对比分析师",
                "target": "000001",
                "symbol": "000001",
                "market": "A",
                "as_of": "2025-01-01",
                "price_start": 10.0,
                "price_end": 10.8,
                "data": {
                    "symbol": "000001",
                    "market": "A",
                    "industry_name": "银行",
                    "rank_in_industry": {"pe_percentile": 0.2, "roe_percentile": 0.4},
                    "value_score": {"score": "good"},
                    "industry_trend": {"cycle": "recovery"},
                    "data_quality": {
                        "overall": 0.9,
                        "confidence_ceiling": 0.70,
                        "has_constituents": True,
                        "has_trend": True,
                    },
                },
            },
            {
                "agent_name": "国际形势分析师",
                "target": "000001",
                "symbol": "000001",
                "market": "A",
                "as_of": "2025-01-01",
                "price_start": 10.0,
                "price_end": 10.8,
                "stock_context": {
                    "inferred_sector": "银行",
                    "macro_sensitivity": {
                        "rate_sensitive": 0.3,
                        "rate_direction": "negative",
                        "fx_sensitive": 0.3,
                        "cycle_sensitive": 0.8,
                        "geopolitical_sensitive": 0.2,
                        "liquidity_sensitive": 0.8,
                    },
                },
                "data": {
                    "data_source": "archive",
                    "china": {
                        "pmi_manufacturing": 51.2,
                        "gdp_yoy_pct": 5.2,
                        "lpr_1y_pct": 3.1,
                        "m2_yoy_pct": 8.5,
                    },
                    "us": {"10y_yield_pct": 3.6, "fed_funds_rate_pct": 4.2, "vix": 14.5},
                    "forex": {"dxy": 100.5, "usd_cny": 7.0},
                    "_geopolitical": {"risk_score": 0.35, "recent_events": ["缓和"]},
                    "data_quality": {"overall_freshness": "80%", "reference_count": 0, "realtime_count": 7},
                },
            },
        ], ensure_ascii=False),
        encoding="utf-8",
    )

    lab = SelfImprovementLab(
        technical_bootstrapper=FakeTechnicalBootstrapper(),
        news_bootstrapper=NewsSnapshotCalibrationBootstrapper(
            price_fetcher=DummyPriceFetcher(),
            calibrator=NewsConfidenceCalibrator(stats_file=tmp_path / "news_calibrator.json"),
        ),
        fundamental_bootstrapper=FundamentalSnapshotCalibrationBootstrapper(
            price_fetcher=DummyPriceFetcher(),
            calibrator=FundamentalConfidenceCalibrator(stats_file=tmp_path / "fundamental.json"),
        ),
        industry_bootstrapper=IndustrySnapshotCalibrationBootstrapper(
            price_fetcher=DummyPriceFetcher(),
            calibrator=IndustryConfidenceCalibrator(stats_file=tmp_path / "industry.json"),
        ),
        macro_bootstrapper=MacroSnapshotCalibrationBootstrapper(
            price_fetcher=DummyPriceFetcher(),
            calibrator=MacroConfidenceCalibrator(stats_file=tmp_path / "macro.json"),
        ),
    )
    report = await lab.run(
        SelfImprovementLabConfig(
            targets=["000001"],
            start_date="2025-01-01",
            end_date="2025-01-31",
            evaluation_min_samples=1,
            output_dir=tmp_path / "lab_all",
            news_snapshots_path=news_path,
            point_in_time_snapshots_path=pit_path,
            use_default_archives=False,
        )
    )

    assert set(report.supported_agents) == {
        "近期股价分析师",
        "最新新闻分析师",
        "公司前景分析师",
        "行业对比分析师",
        "国际形势分析师",
    }
    assert report.total_samples == 5
    assert not report.deferred_agents
