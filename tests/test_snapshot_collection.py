import pytest

from src.core.result import Direction
from src.core.snapshot_collection import (
    CurrentSnapshotCollector,
    SnapshotCollectionConfig,
    SnapshotCollectionReport,
)


@pytest.mark.asyncio
async def test_current_snapshot_collection_rejects_historical_as_of():
    with pytest.raises(ValueError, match="只能使用今天"):
        await CurrentSnapshotCollector().collect(SnapshotCollectionConfig(
            targets=["000001"],
            agents=["fundamental"],
            as_of="2025-01-01",
        ))


def test_snapshot_collection_report_counts_saved_items():
    report = SnapshotCollectionReport(
        generated_at="2026-07-07T10:00:00",
        root_dir="output/x",
        point_in_time_root="output/x/point_in_time_snapshots",
        news_root="output/x/news_snapshots",
        targets=["000001"],
        timeframe="短期(1周)",
        news_mode="evidence",
        saved=[{"agent_name": "公司前景分析师", "target": "000001"}],
    )

    payload = report.to_dict()
    markdown = report.to_markdown()

    assert payload["saved_count"] == 1
    assert "快照数: 1" in markdown
    assert "公司前景分析师" in markdown


def test_build_evidence_news_result_uses_news_evidence_packet(monkeypatch):
    analyst = CurrentSnapshotCollector()

    def fake_packet(self, data, timeframe):
        return {
            "decision_matrix": {
                "suggested_direction": "bullish",
                "reason": "正面新闻占优",
            },
            "confidence_constraints": {"max_confidence": 0.42},
            "evidence": {
                "bullish": ["订单增长"],
                "neutral": ["单一来源"],
            },
        }

    monkeypatch.setattr(
        "src.agents.news_analyst.NewsAnalyst._build_evidence_packet",
        fake_packet,
    )

    result = analyst._build_evidence_news_result(
        target="AAPL",
        timeframe="短期(1周)",
        news_data={"_data_quality": {"score": 0.8}},
    )

    assert result.agent_name == "最新新闻分析师"
    assert result.direction == Direction.BULLISH
    assert result.confidence == 0.42
    assert result.data_summary["collection_mode"] == "evidence_non_llm"
