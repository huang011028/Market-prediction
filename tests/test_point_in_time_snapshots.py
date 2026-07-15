import pytest
import pandas as pd

from src.core.calibration_bootstrap import (
    FundamentalSnapshotCalibrationBootstrapper,
    IndustrySnapshotCalibrationBootstrapper,
    MacroSnapshotCalibrationBootstrapper,
)
from src.data.point_in_time_snapshot_archive import PointInTimeSnapshotArchive
from src.utils.fundamental_calibrator import FundamentalConfidenceCalibrator
from src.utils.industry_calibrator import IndustryConfidenceCalibrator
from src.utils.macro_calibrator import MacroConfidenceCalibrator


class DummyPriceFetcher:
    async def fetch_close_near(self, target, target_date, prefer="on_or_after", tolerance_days=10):
        if str(target) == "510300":
            return 10.0
        return 10.0 if prefer == "on_or_before" else 10.8

    async def fetch_close_window(self, target, start_date, end_date):
        if str(target) == "510300":
            return pd.Series(
                [10.0, 10.0, 10.0],
                index=pd.to_datetime(["2025-01-02", "2025-01-04", "2025-01-07"]),
            )
        return pd.Series(
            [10.1, 10.8, 10.2],
            index=pd.to_datetime(["2025-01-02", "2025-01-04", "2025-01-07"]),
        )


def _fundamental_snapshot():
    return {
        "agent_name": "公司前景分析师",
        "target": "000001",
        "symbol": "000001",
        "market": "A",
        "timeframe": "短期(1周)",
        "as_of": "2025-01-01",
        "price_start": 10.0,
        "price_end": 10.8,
        "data": {
            "symbol": "000001",
            "market": "A",
            "industry": "银行",
            "quality_scorecard": {"total": 82, "rating": "excellent"},
            "valuation_analysis": {"pe_percentile_3yr": 0.22},
            "financials": {"_trend": {"profit_trend": "growing", "earnings_quality": "improving"}},
            "value_trap_analysis": {"is_trap": False},
            "data_quality": {"overall_quality": 0.9, "confidence_ceiling": 0.72},
        },
    }


def _industry_snapshot():
    return {
        "agent_name": "行业对比分析师",
        "target": "000001",
        "symbol": "000001",
        "market": "A",
        "timeframe": "短期(1周)",
        "as_of": "2025-01-01",
        "price_start": 10.0,
        "price_end": 10.8,
        "data": {
            "symbol": "000001",
            "market": "A",
            "industry_name": "银行",
            "rank_in_industry": {"pe_percentile": 0.2, "roe_percentile": 0.4},
            "value_score": {"score": "good", "interpretation": "低估且盈利稳定"},
            "industry_trend": {"cycle": "recovery", "phase": "复苏", "signal": "景气改善"},
            "data_quality": {
                "overall": 0.9,
                "confidence_ceiling": 0.70,
                "has_constituents": True,
                "has_trend": True,
            },
        },
    }


def _macro_snapshot():
    return {
        "agent_name": "国际形势分析师",
        "target": "000001",
        "symbol": "000001",
        "market": "A",
        "timeframe": "短期(1周)",
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
            "_geopolitical": {"risk_score": 0.35, "risk_level": "low", "recent_events": ["缓和"]},
            "data_quality": {"overall_freshness": "80%", "reference_count": 0, "realtime_count": 7},
        },
    }


def test_point_in_time_archive_save_and_load(tmp_path):
    archive = PointInTimeSnapshotArchive(root_dir=tmp_path)
    meta = archive.save_snapshot(
        agent_name="fundamental",
        target="000001",
        symbol="000001",
        market="A",
        timeframe="短期(1周)",
        data=_fundamental_snapshot()["data"],
        as_of="2025-01-01",
        source_kind="historical_replay",
        lineage={"point_in_time_verified": True, "source_timestamps": ["2025-01-01"]},
    )

    snapshots = archive.load_snapshots(agent_name="公司前景分析师", target="000001")

    assert meta["agent_name"] == "公司前景分析师"
    assert len(snapshots) == 1
    assert snapshots[0]["data"]["quality_scorecard"]["rating"] == "excellent"
    assert (tmp_path / "index.jsonl").exists()


def test_point_in_time_archive_rejects_backdated_current_capture(tmp_path):
    archive = PointInTimeSnapshotArchive(root_dir=tmp_path)

    with pytest.raises(ValueError, match="不能回填"):
        archive.save_snapshot(
            agent_name="fundamental",
            target="000001",
            timeframe="短期(1周)",
            data={},
            as_of="2025-01-01",
        )


def test_historical_snapshot_rejects_future_source_timestamp(tmp_path):
    archive = PointInTimeSnapshotArchive(root_dir=tmp_path)

    with pytest.raises(ValueError, match="晚于 as_of"):
        archive.save_snapshot(
            agent_name="fundamental",
            target="000001",
            timeframe="短期(1周)",
            data={},
            as_of="2025-01-01",
            source_kind="historical_replay",
            lineage={"point_in_time_verified": True, "source_timestamps": ["2025-01-02"]},
        )


@pytest.mark.asyncio
async def test_fundamental_snapshot_bootstrapper_generates_sample(tmp_path):
    report = await FundamentalSnapshotCalibrationBootstrapper(
        price_fetcher=DummyPriceFetcher(),
        calibrator=FundamentalConfidenceCalibrator(stats_file=tmp_path / "fundamental.json"),
    ).run_from_snapshots([_fundamental_snapshot()])

    assert report.success_samples == 1
    sample = report.samples[0]
    assert sample.predicted_direction == "bullish"
    assert sample.was_correct is True
    assert sample.validation_mode == "horizon_window"
    assert sample.validation_window == "2025-01-02~2025-01-08"
    assert sample.buckets["scorecard_rating_bucket"] == "excellent"


@pytest.mark.asyncio
async def test_industry_snapshot_bootstrapper_generates_sample(tmp_path):
    report = await IndustrySnapshotCalibrationBootstrapper(
        price_fetcher=DummyPriceFetcher(),
        calibrator=IndustryConfidenceCalibrator(stats_file=tmp_path / "industry.json"),
    ).run_from_snapshots([_industry_snapshot()])

    assert report.success_samples == 1
    sample = report.samples[0]
    assert sample.predicted_direction == "bullish"
    assert sample.was_correct is True
    assert sample.buckets["data_quality_level"] == "constituents+trend"


@pytest.mark.asyncio
async def test_macro_snapshot_bootstrapper_generates_sample(tmp_path):
    report = await MacroSnapshotCalibrationBootstrapper(
        price_fetcher=DummyPriceFetcher(),
        calibrator=MacroConfidenceCalibrator(stats_file=tmp_path / "macro.json"),
    ).run_from_snapshots([_macro_snapshot()])

    assert report.success_samples == 1
    sample = report.samples[0]
    assert sample.predicted_direction == "bullish"
    assert sample.was_correct is True
    assert sample.buckets["data_quality_level"] == "fresh"
