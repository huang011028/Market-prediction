"""
测试 AnalysisResult 和 Magnitude 数据结构
"""

import json
import pytest
from src.core.result import Direction, Magnitude, AnalysisResult, FinalReport
from src.core.prediction_target import resolve_prediction_target


# ================================================================
# Magnitude 测试
# ================================================================


class TestMagnitude:
    """幅度区间测试"""

    def test_valid_range_positive(self):
        """正区间"""
        m = Magnitude(min_pct=1.0, max_pct=5.0)
        assert m.min_pct == 1.0
        assert m.max_pct == 5.0
        assert m.mid_pct == 3.0

    def test_valid_range_negative(self):
        """负区间"""
        m = Magnitude(min_pct=-5.0, max_pct=-1.0)
        assert m.mid_pct == -3.0

    def test_valid_range_cross_zero(self):
        """跨越零轴"""
        m = Magnitude(min_pct=-3.0, max_pct=3.0)
        assert m.mid_pct == 0.0

    def test_invalid_range(self):
        """min > max 应抛出异常"""
        with pytest.raises(ValueError, match="必须小于等于"):
            Magnitude(min_pct=5.0, max_pct=3.0)

    def test_range_str_positive(self):
        m = Magnitude(min_pct=1.0, max_pct=5.0)
        assert m.range_str == "+1.0% ~ +5.0%"

    def test_range_str_negative(self):
        m = Magnitude(min_pct=-5.0, max_pct=-1.0)
        assert m.range_str == "-5.0% ~ -1.0%"

    def test_range_str_cross_zero(self):
        m = Magnitude(min_pct=-3.0, max_pct=3.0)
        assert m.range_str == "-3.0% ~ +3.0%"

    def test_repr(self):
        m = Magnitude(min_pct=1.0, max_pct=5.0)
        assert repr(m) == "+1.0% ~ +5.0%"


# ================================================================
# AnalysisResult 测试
# ================================================================


class TestAnalysisResult:
    """分析结果测试"""

    def _make_valid_result(self) -> AnalysisResult:
        """辅助：创建一个合法的结果"""
        return AnalysisResult(
            agent_name="测试Agent",
            target="000001.SZ",
            timeframe="短期(1周)",
            direction=Direction.BULLISH,
            magnitude=Magnitude(min_pct=1.0, max_pct=5.0),
            confidence=0.7,
            reasoning="这是一个测试推理过程",
            key_factors=["因素1", "因素2"],
            risks=["风险1"],
        )

    def test_validation_success(self):
        """完整结果应通过校验"""
        result = self._make_valid_result()
        assert result.validate() == []
        assert result.is_valid() is True

    def test_validation_empty_name(self):
        """空名称应报错"""
        result = AnalysisResult(agent_name="", target="X", timeframe="Y")
        errors = result.validate()
        assert any("agent_name" in e for e in errors)

    def test_validation_empty_target(self):
        """空标的应报错"""
        result = AnalysisResult(agent_name="X", target="", timeframe="Y")
        errors = result.validate()
        assert any("target" in e for e in errors)

    def test_validation_confidence_out_of_range(self):
        """置信度超范围应报错"""
        result = AnalysisResult(
            agent_name="X",
            target="Y",
            timeframe="Z",
            confidence=1.5,
        )
        errors = result.validate()
        assert any("confidence" in e for e in errors)

    def test_validation_negative_confidence(self):
        """负置信度应报错"""
        result = AnalysisResult(
            agent_name="X",
            target="Y",
            timeframe="Z",
            confidence=-0.1,
        )
        errors = result.validate()
        assert any("confidence" in e for e in errors)

    def test_validation_missing_magnitude_for_bullish(self):
        """看涨但没有 magnitude 应报错"""
        result = AnalysisResult(
            agent_name="X",
            target="Y",
            timeframe="Z",
            direction=Direction.BULLISH,
            confidence=0.5,
            reasoning="测试",
        )
        errors = result.validate()
        assert any("magnitude" in e for e in errors)

    def test_validation_neutral_without_magnitude(self):
        """中性方向不需要 magnitude"""
        result = AnalysisResult(
            agent_name="X",
            target="Y",
            timeframe="Z",
            direction=Direction.NEUTRAL,
            confidence=0.5,
            reasoning="测试",
        )
        # 中性方向不强制 magnitude
        errors = result.validate()
        assert not any("magnitude" in e.lower() for e in errors)

    def test_validation_missing_reasoning(self):
        """缺少 reasoning 应报错"""
        result = AnalysisResult(
            agent_name="X",
            target="Y",
            timeframe="Z",
        )
        errors = result.validate()
        assert any("reasoning" in e for e in errors)

    # --- 序列化测试 ---

    def test_to_dict(self):
        """序列化为字典"""
        result = self._make_valid_result()
        d = result.to_dict()
        assert d["agent_name"] == "测试Agent"
        assert d["direction"] == "bullish"
        assert d["magnitude"]["min_pct"] == 1.0
        assert d["magnitude"]["max_pct"] == 5.0

    def test_to_json(self):
        """序列化为 JSON 字符串"""
        result = self._make_valid_result()
        json_str = result.to_json()
        assert isinstance(json_str, str)
        parsed = json.loads(json_str)
        assert parsed["agent_name"] == "测试Agent"

    def test_from_dict_roundtrip(self):
        """字典序列化往返"""
        original = self._make_valid_result()
        restored = AnalysisResult.from_dict(original.to_dict())
        assert restored.direction == original.direction
        assert restored.confidence == original.confidence
        assert restored.magnitude is not None
        assert restored.magnitude.min_pct == original.magnitude.min_pct
        assert restored.magnitude.max_pct == original.magnitude.max_pct
        assert restored.reasoning == original.reasoning

    def test_from_json_roundtrip(self):
        """JSON 序列化往返"""
        original = self._make_valid_result()
        restored = AnalysisResult.from_json(original.to_json())
        assert restored.direction == original.direction
        assert restored.confidence == original.confidence

    def test_default_values(self):
        """默认值"""
        result = AnalysisResult(
            agent_name="X",
            target="Y",
            timeframe="Z",
        )
        assert result.direction == Direction.NEUTRAL
        assert result.confidence == 0.0
        assert result.magnitude is None
        assert result.key_factors == []
        assert result.risks == []


# ================================================================
# FinalReport 测试
# ================================================================


class TestFinalReport:
    """最终报告测试"""

    def test_to_markdown(self):
        """生成 Markdown 报告"""
        report = FinalReport(
            target="0700.HK",
            timeframe="短期(1周)",
            direction=Direction.BULLISH,
            magnitude=Magnitude(min_pct=3.0, max_pct=6.5),
            confidence=0.72,
            summary="综合来看短期看涨",
            key_risks=["地缘政治风险"],
            disagreements=["技术面看涨 vs 宏观面谨慎"],
        )
        md = report.to_markdown()
        assert "0700.HK" in md
        assert "看涨" in md
        assert "+3.0%" in md
        assert "72%" in md

    def test_to_dict(self):
        """序列化为字典"""
        report = FinalReport(
            target="0700.HK",
            timeframe="短期(1周)",
        )
        d = report.to_dict()
        assert d["target"] == "0700.HK"
        assert d["direction"] == "neutral"
        assert "prob_no_edge" in d

    def test_explicit_prediction_probabilities_are_preserved(self):
        spec = resolve_prediction_target(
            "短期(1周)",
            Direction.BULLISH,
            Magnitude(2.0, 4.0),
            0.80,
            {
                "expected_return_pct": 2.5,
                "prob_up": 0.55,
                "prob_down": 0.25,
                "prob_neutral": 0.20,
            },
            target="000001",
        )

        assert spec.expected_return_pct == 2.5
        assert spec.prob_up == 0.55
        assert spec.prob_down == 0.25
        assert spec.prob_neutral == 0.20


# ================================================================
# Direction 枚举测试
# ================================================================


class TestDirection:
    """方向枚举测试"""

    def test_from_string(self):
        assert Direction("bullish") == Direction.BULLISH
        assert Direction("bearish") == Direction.BEARISH
        assert Direction("neutral") == Direction.NEUTRAL

    def test_invalid_direction(self):
        with pytest.raises(ValueError):
            Direction("invalid")
