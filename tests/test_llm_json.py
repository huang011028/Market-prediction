import json

from src.agents.aggregator import Aggregator
from src.core.base_agent import BaseAgent
from src.core.llm_json import parse_llm_json
from src.core.result import Direction


class DummyAgent(BaseAgent):
    async def gather_data(self, target: str, timeframe: str) -> dict:
        return {}

    def _get_system_prompt(self) -> str:
        return "dummy"


def test_parse_llm_json_repairs_missing_comma_between_fields():
    content = """```json
{
  "direction": "neutral",
  "magnitude": {"min_pct": -3.0, "max_pct": 3.0},
  "confidence": 0.55,
  "reasoning": "震荡区间判断"
  "key_factors": ["ADX确认震荡"],
  "risks": ["突破失败"]
}
```"""

    parsed = parse_llm_json(content)

    assert parsed.ok
    assert parsed.repaired
    assert "inserted_missing_line_comma" in parsed.repairs
    assert parsed.data["key_factors"] == ["ADX确认震荡"]
    assert json.loads(parsed.json_text)["reasoning"] == "震荡区间判断"


def test_base_agent_parse_keeps_result_ok_when_json_is_repaired():
    agent = DummyAgent(name="近期股价分析师", description="dummy", llm=None)
    content = """```json
{
  "direction": "中性",
  "magnitude": {"min_pct": -3.0, "max_pct": 3.0},
  "confidence": "55%",
  "reasoning": "周线偏强但日线超买，短期震荡"
  "key_factors": "强周线反弹后的日线超买修正",
  "risks": ["跌破MA60"]
}
```"""

    result = agent._parse_llm_response(
        content,
        {"target": "0700.HK", "timeframe": "短期(1周)"},
    )

    assert result.status == "ok"
    assert result.error_message is None
    assert result.direction == Direction.NEUTRAL
    assert result.confidence == 0.55
    assert result.key_factors == ["强周线反弹后的日线超买修正"]
    assert result.data_summary["llm_json_repaired"] is True


def test_aggregator_parse_uses_repaired_json_instead_of_neutral_fallback():
    content = """```json
{
  "direction": "bullish",
  "magnitude": {"min_pct": 1.0, "max_pct": 3.0},
  "confidence": 0.62,
  "summary": "多方证据略占优"
  "key_risks": ["短线拥挤"],
  "disagreements": []
}
```"""
    aggregator = Aggregator.__new__(Aggregator)

    report = aggregator._parse_response(content, "0700.HK", "短期(1周)", [], [])

    assert report.direction == Direction.BULLISH
    assert report.confidence == 0.62
    assert report.key_risks == ["短线拥挤"]
