import api_server
from src.core.result import AnalysisResult, Direction, Magnitude


def _result(status: str = "ok", confidence: float = 0.5) -> AnalysisResult:
    return AnalysisResult(
        agent_name="最新新闻分析师",
        target="000001",
        timeframe="短期(1周)",
        direction=Direction.NEUTRAL,
        magnitude=Magnitude(-1, 1),
        confidence=confidence,
        reasoning="新闻证据约束提示: confidence 超过新闻证据上限",
        key_factors=["新闻较少但可用"],
        risks=["新闻证据约束: 置信度已按证据上限校准"],
        data_summary={
            "source": "eastmoney+sina",
            "news_count": 5,
            "data_quality": {"score": 0.7, "is_available": True},
        },
        status=status,
        data_quality_score=0.7,
    )


def test_degraded_agent_is_alert_not_failed():
    status = api_server._agent_status_from_result(_result(status="degraded"))
    failed, degraded = api_server._split_agent_statuses([status])

    assert status["status"] == "degraded"
    assert failed == []
    assert degraded == [status]


def test_evidence_constraint_text_alone_does_not_mark_failed():
    status = api_server._agent_status_from_result(_result(status="ok"))

    assert status["status"] == "ok"


def test_failed_agent_stays_failed():
    result = _result(status="failed", confidence=0.0)
    result.error_message = "分析超时"

    status = api_server._agent_status_from_result(result)
    failed, degraded = api_server._split_agent_statuses([status])

    assert status["status"] == "failed"
    assert failed == [status]
    assert degraded == []
