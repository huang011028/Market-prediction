import pytest
import pandas as pd
from datetime import datetime

from src.core.agent_improvement import AgentImprovementAdvisor
from src.core.calibration_bootstrap import (
    CalibrationBootstrapConfig,
    NewsSnapshotCalibrationBootstrapper,
    TechnicalCalibrationBootstrapper,
)
from src.core.agent_skill_registry import AgentSkillRegistry
from src.utils.news_calibrator import NewsConfidenceCalibrator
from src.utils.technical_calibrator import TechnicalConfidenceCalibrator


class DummyPriceData:
    trading_days = 80
    price_current = 10.0

    def to_agent_dict(self):
        return {
            "symbol": "000001",
            "name": "测试股份",
            "market": "A",
            "data_period": "2025-01-01~2025-03-01",
            "latest_date": "2025-03-01",
            "trading_days": self.trading_days,
            "price_summary": {"latest_close": self.price_current},
            "technical_snapshot": {
                "trend_regime": {
                    "short_term": "up",
                    "medium_term": "up",
                    "ma_alignment": "bullish",
                    "structure": "higher_high",
                },
                "momentum_signals": {"state": "bullish"},
                "volume_signals": {"price_up_volume_up": True},
                "volatility_signals": {
                    "atr_pct": 2.2,
                    "daily_volatility_20d_pct": 1.4,
                    "volatility_state": "normal",
                },
                "support_resistance": {
                    "resistance_distance_pct": 6.0,
                    "support_distance_pct": -8.0,
                },
                "risk_levels": {"risk_reward_ratio": 1.8},
                "confidence_model": {
                    "technical_confidence": 0.72,
                    "suggested_direction": "bullish",
                },
                "evidence": {"bullish": ["趋势向上"]},
            },
            "intraday_signals": {"available": False},
        }


class DummyPriceFetcher:
    async def fetch_as_of(self, target, as_of, lookback_days=180):
        return DummyPriceData()

    async def fetch_close_near(self, target, target_date, prefer="on_or_after", tolerance_days=10):
        if str(target) == "510300":
            return 10.0
        return 10.8

    async def fetch_close_window(self, target, start_date, end_date):
        if str(target) == "510300":
            return pd.Series(
                [10.0, 10.0, 10.0],
                index=pd.to_datetime(["2025-03-02", "2025-03-04", "2025-03-07"]),
            )
        return pd.Series(
            [10.1, 10.8, 10.2],
            index=pd.to_datetime(["2025-03-02", "2025-03-04", "2025-03-07"]),
        )


class BearishDummyPriceFetcher(DummyPriceFetcher):
    async def fetch_close_window(self, target, start_date, end_date):
        if str(target) == "510300":
            return pd.Series(
                [10.0, 10.0, 10.0],
                index=pd.to_datetime(["2025-03-02", "2025-03-04", "2025-03-07"]),
            )
        return pd.Series(
            [9.8, 9.3, 9.6],
            index=pd.to_datetime(["2025-03-02", "2025-03-04", "2025-03-07"]),
        )


def test_technical_confidence_calibrator_learns_low_accuracy(tmp_path):
    calibrator = TechnicalConfidenceCalibrator(stats_file=tmp_path / "technical.json")
    for _ in range(10):
        calibrator.update_from_validation(
            predicted_conf=0.72,
            was_correct=False,
            trend_bucket="up",
            momentum_bucket="bullish",
            volume_bucket="confirm_up",
            position_bucket="middle_range",
            intraday_bucket="unavailable",
            timeframe_bucket="短期",
        )

    adjusted = calibrator.calibrate(
        0.72,
        trend_bucket="up",
        momentum_bucket="bullish",
        volume_bucket="confirm_up",
        position_bucket="middle_range",
        intraday_bucket="unavailable",
        timeframe_bucket="短期",
    )

    assert adjusted < 0.50
    stats = calibrator.get_calibration_stats()
    assert stats["trend_buckets"]["up"]["total"] == 10


def test_news_confidence_calibrator_extracts_buckets_and_learns(tmp_path):
    evidence = {
        "news_window": {"news_count": 8, "sources_used": ["eastmoney", "sina"]},
        "source_quality": {"source_count": 2},
        "decision_matrix": {
            "volume_bucket": "adequate",
            "sentiment_bucket": "positive",
            "event_bucket": "positive_catalyst",
        },
        "event_impact_matrix": {"average_time_weight": 0.82},
    }
    buckets = NewsConfidenceCalibrator.extract_buckets_from_evidence(evidence)
    assert buckets == {
        "news_count_bucket": "adequate",
        "source_bucket": "multi_source",
        "sentiment_bucket": "positive",
        "event_bucket": "positive_catalyst",
        "freshness_bucket": "fresh",
    }

    calibrator = NewsConfidenceCalibrator(stats_file=tmp_path / "news.json")
    for _ in range(10):
        calibrator.update_from_validation(0.70, False, **buckets)

    assert calibrator.calibrate(0.70, **buckets) < 0.50


@pytest.mark.asyncio
async def test_technical_bootstrapper_generates_initial_sample(tmp_path):
    calibrator = TechnicalConfidenceCalibrator(stats_file=tmp_path / "technical.json")
    bootstrapper = TechnicalCalibrationBootstrapper(
        price_fetcher=DummyPriceFetcher(),
        calibrator=calibrator,
    )
    report = await bootstrapper.run(
        CalibrationBootstrapConfig(
            targets=["000001"],
            start_date="2025-03-01",
            end_date="2025-03-01",
        )
    )

    assert report.success_samples == 1
    sample = report.samples[0]
    assert sample.predicted_direction == "bullish"
    # V3 uses the fixed-horizon, volatility-scaled excess return.  An interim
    # +8% touch no longer turns a +2% expiry return into a bullish hit.
    assert sample.actual_direction == "neutral"
    assert sample.was_correct is False
    assert sample.buckets["trend_bucket"] == "up"
    assert sample.buckets["market_regime_bucket"] == "bull_trend"
    assert sample.buckets["volatility_bucket"] == "normal"
    assert sample.buckets["sr_zone_bucket"] == "middle_range"
    assert sample.buckets["risk_reward_bucket"] == "favorable"
    assert sample.buckets["technical_scenario_bucket"] == "bull_trend|middle_range|confirm_up"
    assert sample.buckets["regime_sr_bucket"] == "bull_trend|middle_range"
    assert sample.buckets["regime_volume_bucket"] == "bull_trend|confirm_up"
    assert sample.buckets["sr_volume_bucket"] == "middle_range|confirm_up"
    assert sample.validation_mode == "horizon_window"
    assert sample.validation_window == "2025-03-02~2025-03-08"
    assert sample.window_max_change_pct == pytest.approx(8.0)
    assert report.calibration_stats["confidence_bins"]["0.6-0.8"]["total"] == 1
    assert report.calibration_stats["market_regime_buckets"]["bull_trend"]["total"] == 1


def test_technical_calibrator_extracts_regime_volatility_and_sr_buckets():
    evidence = {
        "trend_regime": {
            "short_term": "down",
            "medium_term": "up",
            "ma_alignment": "tangled",
            "structure": "range_bound",
        },
        "volatility_signals": {"atr_pct": 6.4, "volatility_state": "high"},
        "support_resistance": {
            "resistance_distance_pct": 1.2,
            "support_distance_pct": -1.4,
        },
        "decision_matrix": {
            "trend_bucket": "down",
            "momentum_bucket": "bearish",
            "volume_bucket": "confirm_down",
            "risk_reward_bucket": "weak",
            "intraday_bucket": "unavailable",
        },
    }

    buckets = TechnicalConfidenceCalibrator.extract_buckets_from_evidence(
        evidence,
        "短期(1周)",
    )

    assert buckets["market_regime_bucket"] == "bull_pullback"
    assert buckets["volatility_bucket"] == "extreme"
    assert buckets["sr_zone_bucket"] == "squeeze"
    assert buckets["risk_reward_bucket"] == "weak"
    assert buckets["technical_scenario_bucket"] == "bull_pullback|squeeze|confirm_down"
    assert buckets["timeframe_bucket"] == "短期"


def test_technical_direction_policy_applies_holdout_passed_rule():
    policy = {
        "rules": [
            {
                "bucket_group": "market_regime_buckets",
                "bucket": "bear_rebound",
                "action": "force_bearish",
            }
        ]
    }

    direction, matched = TechnicalConfidenceCalibrator.apply_direction_policy(
        "neutral",
        {"market_regime_bucket": "bear_rebound"},
        policy=policy,
    )

    assert direction == "bearish"
    assert matched[0]["action"] == "force_bearish"


def test_technical_direction_policy_requires_all_conditions():
    policy = {
        "rules": [
            {
                "conditions": {
                    "market_regime_bucket": "bear_rebound",
                    "sr_zone_bucket": "near_resistance",
                    "volume_bucket": "shrinking",
                },
                "action": "force_bearish",
            }
        ]
    }

    direction, matched = TechnicalConfidenceCalibrator.apply_direction_policy(
        "neutral",
        {
            "market_regime_bucket": "bear_rebound",
            "sr_zone_bucket": "near_resistance",
            "volume_bucket": "confirm_up",
        },
        policy=policy,
    )

    assert direction == "neutral"
    assert matched == []


def test_agent_skill_registry_exports_technical_direction_policy(tmp_path):
    registry_path = tmp_path / "agent_skill_registry.json"
    registry = AgentSkillRegistry(registry_path)
    registry.upsert_skill({
        "skill_id": "technical.direction_policy.test",
        "agent_name": "近期股价分析师",
        "skill_type": "direction_policy",
        "enabled": True,
        "trigger_conditions": {
            "market_regime_bucket": "bull_trend",
            "volume_bucket": "neutral",
        },
        "action": {"type": "force_direction", "direction": "bullish"},
        "validation": {
            "bucket_group": "regime_volume_buckets",
            "sample_key": "regime_volume_bucket",
            "bucket": "bull_trend|neutral",
        },
    })
    registry.save()

    policy = TechnicalConfidenceCalibrator.load_direction_policy(
        policy_file=tmp_path / "missing_legacy_policy.json",
        registry_file=registry_path,
    )
    direction, matched = TechnicalConfidenceCalibrator.apply_direction_policy(
        "neutral",
        {"market_regime_bucket": "bull_trend", "volume_bucket": "neutral"},
        policy=policy,
    )

    assert policy["source"] == "agent_skill_registry"
    assert direction == "bullish"
    assert matched[0]["skill_id"] == "technical.direction_policy.test"


def test_agent_skill_registry_exports_technical_confidence_policy(tmp_path):
    registry_path = tmp_path / "agent_skill_registry.json"
    registry = AgentSkillRegistry(registry_path)
    registry.upsert_skill({
        "skill_id": "technical.confidence_policy.test",
        "agent_name": "近期股价分析师",
        "skill_type": "confidence_policy",
        "enabled": True,
        "trigger_conditions": {
            "sr_zone_bucket": "near_resistance",
            "volume_bucket": "shrinking",
        },
        "action": {"type": "cap_confidence", "confidence_cap": 0.35},
        "validation": {
            "bucket_group": "sr_volume_buckets",
            "sample_key": "sr_volume_bucket",
            "bucket": "near_resistance|shrinking",
        },
    })
    registry.save()

    policy = TechnicalConfidenceCalibrator.load_confidence_policy(
        registry_file=registry_path,
    )
    confidence, matched = TechnicalConfidenceCalibrator.apply_confidence_policy(
        0.72,
        {"sr_zone_bucket": "near_resistance", "volume_bucket": "shrinking"},
        policy=policy,
    )

    assert confidence == 0.35
    assert matched[0]["skill_id"] == "technical.confidence_policy.test"


@pytest.mark.asyncio
async def test_window_validation_counts_moves_inside_horizon():
    outcome = await TechnicalCalibrationBootstrapper._horizon_window_outcome(
        DummyPriceFetcher(),
        "000001",
        10.0,
        datetime(2025, 3, 1),
        datetime(2025, 3, 8),
        "bullish",
    )

    assert outcome["validation_mode"] == "horizon_window"
    assert outcome["window_max_change_pct"] == pytest.approx(8.0)
    assert TechnicalCalibrationBootstrapper._direction_correct(
        "bullish",
        outcome["actual_change_pct"],
        outcome["window_max_change_pct"],
        outcome["window_min_change_pct"],
    )
    assert not TechnicalCalibrationBootstrapper._direction_correct(
        "neutral",
        outcome["actual_change_pct"],
        outcome["window_max_change_pct"],
        outcome["window_min_change_pct"],
    )


@pytest.mark.asyncio
async def test_news_snapshot_bootstrapper_uses_archived_snapshot(tmp_path):
    calibrator = NewsConfidenceCalibrator(stats_file=tmp_path / "news.json")
    bootstrapper = NewsSnapshotCalibrationBootstrapper(
        price_fetcher=BearishDummyPriceFetcher(),
        calibrator=calibrator,
    )
    snapshot = {
        "target": "000001",
        "as_of": "2025-03-01",
        "price_start": 10.0,
        "price_end": 9.3,
        "news_data": {
            "symbol": "000001",
            "_market": "A",
            "news_source": "archive",
            "news_count": 6,
            "sources_used": ["eastmoney", "sina"],
            "_data_quality": {"score": 0.90, "is_available": True, "news_count": 6},
            "preprocessing": {
                "sentiment_stats": {
                    "positive": 0,
                    "negative": 4,
                    "neutral": 1,
                    "unknown": 0,
                    "weighted_positive_score": 0.0,
                    "weighted_negative_score": 3.0,
                },
                "category_breakdown": {"earnings": 4},
                "anomaly_flags": {},
                "top_news": [
                    {"_sentiment": "negative", "_category": "earnings", "_time_weight": 0.9}
                    for _ in range(4)
                ],
            },
        },
    }

    report = await bootstrapper.run_from_snapshots([snapshot])

    assert report.success_samples == 1
    sample = report.samples[0]
    assert sample.predicted_direction == "bearish"
    assert sample.actual_direction == "bearish"
    assert sample.was_correct is True
    assert sample.buckets["source_bucket"] == "multi_source"


def test_agent_improvement_advisor_flags_data_source_and_prompt_work():
    advisor = AgentImprovementAdvisor()
    signals = advisor.recommend(
        "最新新闻分析师",
        {
            "source_buckets": {"single_source": {"total": 12, "accuracy": 0.25}},
            "event_buckets": {"rumor_driven": {"total": 8, "accuracy": 0.30}},
        },
    )

    assert signals[0].area == "data_source"
    assert any(signal.area == "prompt" for signal in signals)
