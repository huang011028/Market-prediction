import pytest

from src.core.data_source_auditor import (
    AGENT_INDUSTRY,
    AGENT_NEWS,
    DataSourceCoverageAuditor,
)


class FakeAgent:
    def __init__(self, name, data=None, error=None):
        self.name = name
        self.data = data or {}
        self.error = error

    async def gather_data(self, target, timeframe):
        if self.error:
            raise self.error
        return self.data


@pytest.mark.asyncio
async def test_auditor_marks_news_ok():
    agent = FakeAgent(
        AGENT_NEWS,
        {
            "news_count": 8,
            "news_source": "eastmoney+sina",
            "sources_used": ["eastmoney", "sina"],
            "_data_quality": {
                "score": 0.95,
                "is_available": True,
            },
        },
    )
    auditor = DataSourceCoverageAuditor(agents=[agent])

    report = await auditor.audit_targets(["京东"], agent_names=[AGENT_NEWS])
    check = report.targets[0].checks[0]

    assert check.status == "ok"
    assert check.quality_label == "good"
    assert check.field_coverage == 1.0
    assert check.data_source == "eastmoney+sina"
    assert report.summary["status_counts"]["ok"] == 1


@pytest.mark.asyncio
async def test_auditor_detects_industry_missing_fields():
    agent = FakeAgent(
        AGENT_INDUSTRY,
        {
            "industry_name": "",
            "data_source": "none",
            "stock_metrics": {"pe": "N/A", "pb": "N/A", "roe_pct": "N/A"},
            "industry_average": {"pe": "N/A", "pb": "N/A", "roe_pct": "N/A"},
            "rank_in_industry": {},
            "value_score": {},
            "data_quality": {"overall": 0.1},
        },
    )
    auditor = DataSourceCoverageAuditor(agents=[agent])

    report = await auditor.audit_targets(["00981"])
    check = report.targets[0].checks[0]

    assert check.status == "poor"
    assert "industry_name" in check.critical_missing
    assert "stock_metrics.pe" in check.missing_fields
    assert report.recurring_issues


@pytest.mark.asyncio
async def test_auditor_records_agent_failure():
    agent = FakeAgent(AGENT_NEWS, error=RuntimeError("source down"))
    auditor = DataSourceCoverageAuditor(agents=[agent], timeout_seconds=1)

    report = await auditor.audit_targets(["AAPL"])
    check = report.targets[0].checks[0]

    assert check.status == "failed"
    assert "source down" in check.error
    assert check.quality_score == 0.0


@pytest.mark.asyncio
async def test_auditor_writes_json_and_markdown(tmp_path):
    agent = FakeAgent(
        AGENT_NEWS,
        {
            "news_count": 3,
            "news_source": "yfinance",
            "sources_used": ["yfinance"],
            "_data_quality": {
                "score": 0.6,
                "is_available": True,
            },
        },
    )
    auditor = DataSourceCoverageAuditor(agents=[agent])
    report = await auditor.audit_targets(["AAPL"])

    json_path, md_path = auditor.write_report(report, tmp_path)

    assert json_path.exists()
    assert md_path.exists()
    assert "数据源覆盖率巡检报告" in md_path.read_text(encoding="utf-8")
    assert '"agent_name": "最新新闻分析师"' in json_path.read_text(encoding="utf-8")
