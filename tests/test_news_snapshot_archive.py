import pytest
import pandas as pd

from src.core.result import AnalysisResult, Direction
from src.data.news_snapshot_archive import NewsSnapshotArchive


def _news_data():
    return {
        "symbol": "000001",
        "company_name": "测试股份",
        "_resolved_symbol": "000001",
        "_resolved_name": "测试股份",
        "_market": "A",
        "news_source": "archive",
        "news_count": 4,
        "sources_used": ["eastmoney", "sina"],
        "_data_quality": {
            "score": 0.9,
            "is_available": True,
            "news_count": 4,
            "sources": ["eastmoney", "sina"],
        },
        "preprocessing": {
            "sentiment_stats": {
                "positive": 0,
                "negative": 3,
                "neutral": 1,
                "unknown": 0,
                "weighted_positive_score": 0.0,
                "weighted_negative_score": 2.6,
            },
            "category_breakdown": {"earnings": 3},
            "anomaly_flags": {},
            "top_news": [
                {
                    "title": "业绩低于预期",
                    "source": "eastmoney",
                    "time": "2026-07-06",
                    "_sentiment": "negative",
                    "_category": "earnings",
                    "_time_weight": 0.9,
                }
            ],
        },
    }


def _news_result():
    return AnalysisResult(
        agent_name="最新新闻分析师",
        target="000001",
        timeframe="短期(1周)",
        direction=Direction.BEARISH,
        confidence=0.58,
        reasoning="负面财报新闻占优",
    )


def test_archive_save_load_and_attach_prediction_id(tmp_path):
    archive = NewsSnapshotArchive(root_dir=tmp_path)
    meta = archive.save_analysis_snapshot(
        target="000001",
        timeframe="短期(1周)",
        news_data=_news_data(),
        result=_news_result(),
        as_of="2026-07-06T10:00:00",
        source_kind="historical_replay",
        lineage={"point_in_time_verified": True, "source_timestamps": ["2026-07-06"]},
    )

    snapshot_path = tmp_path / "000001" / "20260706" / f"{meta['snapshot_id']}.json"
    assert snapshot_path.exists()
    assert (tmp_path / "index.jsonl").exists()

    snapshots = archive.load_snapshots(target="000001", start_date="2026-07-01")
    assert len(snapshots) == 1
    assert snapshots[0]["predicted_direction"] == "bearish"
    assert snapshots[0]["news_data"]["news_count"] == 4

    updated = archive.attach_prediction_id(meta, "pred-123")
    assert updated["prediction_id"] == "pred-123"
    assert archive.load_snapshots(target="000001")[0]["prediction_id"] == "pred-123"


def test_news_analyst_finalize_archives_snapshot(tmp_path):
    from src.agents.news_analyst import NewsAnalyst

    archive = NewsSnapshotArchive(root_dir=tmp_path)
    analyst = NewsAnalyst.__new__(NewsAnalyst)
    analyst.name = "最新新闻分析师"
    analyst._snapshot_archive = archive

    result = analyst._finalize_result(
        _news_result(),
        _news_data(),
        {"target": "000001", "timeframe": "短期(1周)"},
        {"signals": [{"type": "earnings", "sentiment": "negative"}]},
    )

    meta = result.data_summary["news_snapshot"]
    assert meta["snapshot_id"]
    assert len(archive.load_snapshots(target="000001")) == 1


@pytest.mark.asyncio
async def test_snapshot_replay_prefers_archived_final_prediction(tmp_path):
    from src.core.calibration_bootstrap import NewsSnapshotCalibrationBootstrapper
    from src.utils.news_calibrator import NewsConfidenceCalibrator

    class DummyPriceFetcher:
        async def fetch_close_near(self, target, target_date, prefer="on_or_after", tolerance_days=10):
            if str(target) == "510300":
                return 10.0
            return 10.0 if prefer == "on_or_before" else 10.7

        async def fetch_close_window(self, target, start_date, end_date):
            if str(target) == "510300":
                return pd.Series(
                    [10.0, 10.0, 10.0],
                    index=pd.to_datetime(["2026-07-07", "2026-07-10", "2026-07-13"]),
                )
            return pd.Series(
                [10.2, 10.7, 10.1],
                index=pd.to_datetime(["2026-07-07", "2026-07-10", "2026-07-13"]),
            )

    archive = NewsSnapshotArchive(root_dir=tmp_path)
    meta = archive.save_analysis_snapshot(
        target="000001",
        timeframe="短期(1周)",
        news_data=_news_data(),
        result=AnalysisResult(
            agent_name="最新新闻分析师",
            target="000001",
            timeframe="短期(1周)",
            direction=Direction.BULLISH,
            confidence=0.62,
            reasoning="最终判断覆盖新闻矩阵",
        ),
        as_of="2026-07-06T10:00:00",
        source_kind="historical_replay",
        lineage={"point_in_time_verified": True, "source_timestamps": ["2026-07-06"]},
    )
    snapshots = archive.load_snapshots()
    assert snapshots[0]["snapshot_id"] == meta["snapshot_id"]

    report = await NewsSnapshotCalibrationBootstrapper(
        price_fetcher=DummyPriceFetcher(),
        calibrator=NewsConfidenceCalibrator(stats_file=tmp_path / "news_calibration.json"),
    ).run_from_snapshots(snapshots)

    assert report.success_samples == 1
    assert report.samples[0].predicted_direction == "bullish"
    # V3 scores the fixed-horizon excess return (+1.0%), which remains inside
    # the no-edge band instead of rewarding the interim +7% price touch.
    assert report.samples[0].was_correct is False
    assert report.samples[0].validation_mode == "horizon_window"
