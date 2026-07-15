from datetime import datetime

from src.core.backtester import (
    AGENT_FUNDAMENTAL,
    BacktestConfig,
    Backtester,
    FrozenSnapshotResultAgent,
)
from src.data.news_snapshot_archive import NewsSnapshotArchive
from src.data.point_in_time_snapshot_archive import PointInTimeSnapshotArchive


def _archived_result():
    return {
        "agent_name": AGENT_FUNDAMENTAL,
        "target": "000001",
        "timeframe": "短期(1周)",
        "direction": "bullish",
        "magnitude": {"min_pct": 0.5, "max_pct": 2.0},
        "confidence": 0.6,
        "reasoning": "历史结果",
        "data_summary": {},
    }


def test_backtester_finds_only_visible_snapshot(tmp_path):
    pit = PointInTimeSnapshotArchive(tmp_path / "pit")
    pit.save_snapshot(
        agent_name=AGENT_FUNDAMENTAL,
        target="000001",
        symbol="000001",
        market="A",
        timeframe="短期(1周)",
        data={"financials": {"roe_pct": 10}},
        analysis_result=_archived_result(),
        as_of="2025-01-02T10:00:00",
        source_kind="historical_replay",
        lineage={
            "point_in_time_verified": True,
            "source_timestamps": ["2025-01-02T09:30:00"],
        },
    )
    backtester = Backtester(
        llm=object(),
        point_in_time_archive=pit,
        news_archive=NewsSnapshotArchive(tmp_path / "news"),
    )
    config = BacktestConfig(target="000001", start_date="2025-01-01", end_date="2025-01-10")

    assert backtester._find_snapshot(
        "000001", AGENT_FUNDAMENTAL, datetime(2025, 1, 1), config,
    ) is None
    visible = backtester._find_snapshot(
        "000001", AGENT_FUNDAMENTAL, datetime(2025, 1, 3), config,
    )
    assert visible is not None
    assert visible["as_of"].startswith("2025-01-02")


def test_frozen_snapshot_mode_returns_archived_result(tmp_path):
    backtester = Backtester(
        llm=object(),
        point_in_time_archive=PointInTimeSnapshotArchive(tmp_path / "pit"),
        news_archive=NewsSnapshotArchive(tmp_path / "news"),
    )
    agent = backtester._build_snapshot_agent(
        AGENT_FUNDAMENTAL,
        {"analysis_result": _archived_result()},
        "frozen_result",
    )

    assert isinstance(agent, FrozenSnapshotResultAgent)
    assert agent._result.direction.value == "bullish"
