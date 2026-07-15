"""
分析结果数据结构

定义所有 Agent 的统一输出格式，包含方向、幅度、置信度、
推理过程等核心字段。支持 JSON 序列化/反序列化。
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
import json
from typing import Optional

from src.core.prediction_target import PredictionTargetSpec, resolve_prediction_target

STRUCTURED_EVIDENCE_DOMAINS = {
    "近期股价分析师": "技术",
    "公司前景分析师": "基本面",
    "行业对比分析师": "行业",
    "国际形势分析师": "宏观",
    "最新新闻分析师": "新闻",
}


# ================================================================
# 枚举定义
# ================================================================


class Direction(str, Enum):
    """预测方向"""

    BULLISH = "bullish"  # 看涨
    BEARISH = "bearish"  # 看跌
    NEUTRAL = "neutral"  # 震荡/中性


# ================================================================
# 幅度区间
# ================================================================


@dataclass
class Magnitude:
    """涨跌幅度区间（百分比）"""

    min_pct: float  # 最小变化百分比，如 -5.0 表示 -5%
    max_pct: float  # 最大变化百分比，如 +3.0 表示 +3%

    def __post_init__(self):
        if self.min_pct > self.max_pct:
            raise ValueError(
                f"min_pct ({self.min_pct}) 必须小于等于 max_pct ({self.max_pct})"
            )

    @property
    def mid_pct(self) -> float:
        """区间中点"""
        return (self.min_pct + self.max_pct) / 2.0

    @property
    def range_str(self) -> str:
        """人类可读的区间字符串，例如 '+1.0% ~ +5.0%'"""
        if self.min_pct >= 0:
            return f"+{self.min_pct:.1f}% ~ +{self.max_pct:.1f}%"
        elif self.max_pct <= 0:
            return f"{self.min_pct:.1f}% ~ {self.max_pct:.1f}%"
        else:
            return f"{self.min_pct:.1f}% ~ +{self.max_pct:.1f}%"

    def __repr__(self) -> str:
        return self.range_str


# ================================================================
# 分析结果
# ================================================================


@dataclass
class AnalysisResult:
    """所有分析 Agent 的统一输出格式

    每个 Agent 的 run() 方法必须返回此结构。
    """

    # --- 元信息 ---
    agent_name: str  # Agent 名称，如 "技术面分析师"
    target: str  # 分析标的，如 "0700.HK"
    timeframe: str  # 预测周期，如 "短期(1周)"
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    # --- 核心预测 ---
    direction: Direction = Direction.NEUTRAL
    magnitude: Magnitude | None = None
    confidence: float = 0.0  # 0.0 ~ 1.0
    prediction_target: PredictionTargetSpec | None = None

    # --- 可解释性 ---
    reasoning: str = ""  # 推理过程（Markdown 格式）
    key_factors: list[str] = field(default_factory=list)  # 关键影响因素
    risks: list[str] = field(default_factory=list)  # 风险提示

    # --- 数据摘要 ---
    data_summary: dict = field(default_factory=dict)  # 使用的数据摘要
    status: str = "ok"  # ok / degraded / failed
    error_message: Optional[str] = None
    data_quality_score: float = 1.0

    def __post_init__(self):
        if not isinstance(self.direction, Direction):
            self.direction = Direction(str(self.direction or "neutral"))
        if isinstance(self.magnitude, dict):
            self.magnitude = Magnitude(**self.magnitude)
        self.prediction_target = resolve_prediction_target(
            self.timeframe,
            self.direction,
            self.magnitude,
            self.confidence,
            self.prediction_target,
            target=self.target,
        )

    def validate(self) -> list[str]:
        """校验结果完整性

        Returns:
            错误信息列表，空列表表示校验通过
        """
        errors: list[str] = []

        if not self.agent_name:
            errors.append("agent_name 不能为空")
        if not self.target:
            errors.append("target 不能为空")
        if not self.timeframe:
            errors.append("timeframe 不能为空")

        if not isinstance(self.confidence, (int, float)):
            errors.append("confidence 必须是数字")
        elif self.confidence < 0 or self.confidence > 1:
            errors.append(f"confidence 必须在 0~1 之间，当前值: {self.confidence}")

        if self.direction not in Direction:
            errors.append(f"direction 无效值: {self.direction}")

        if self.direction != Direction.NEUTRAL and self.magnitude is None:
            errors.append("非中性方向时必须提供 magnitude")

        if not self.reasoning:
            errors.append("reasoning 不能为空（可解释性要求）")

        return errors

    def is_valid(self) -> bool:
        """快速检查结果是否合法"""
        return len(self.validate()) == 0

    # ================================================================
    # 序列化
    # ================================================================

    def to_dict(self) -> dict:
        """序列化为字典"""
        d = asdict(self)
        d["direction"] = self.direction.value
        if self.magnitude:
            d["magnitude"] = asdict(self.magnitude)
        if self.prediction_target:
            d["prediction_target"] = self.prediction_target.to_dict()
        return d

    def to_json(self, indent: int = 2) -> str:
        """序列化为 JSON 字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, data: dict) -> "AnalysisResult":
        """从字典反序列化"""
        data = data.copy()

        # 还原枚举
        if "direction" in data:
            data["direction"] = Direction(data["direction"])

        # 还原 Magnitude
        if data.get("magnitude"):
            data["magnitude"] = Magnitude(**data["magnitude"])

        if data.get("prediction_target"):
            data["prediction_target"] = PredictionTargetSpec.from_dict(
                data["prediction_target"]
            )

        return cls(**data)

    @classmethod
    def from_json(cls, json_str: str) -> "AnalysisResult":
        """从 JSON 字符串反序列化"""
        return cls.from_dict(json.loads(json_str))


# ================================================================
# 最终汇总报告
# ================================================================


@dataclass
class FinalReport:
    """最终汇总报告——由汇总 Agent 生成"""

    # --- 元信息 ---
    target: str
    timeframe: str
    generated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    # --- 综合预测 ---
    direction: Direction = Direction.NEUTRAL
    magnitude: Magnitude | None = None
    confidence: float = 0.0
    prediction_target: PredictionTargetSpec | None = None
    expected_excess_return_pct: Optional[float] = None
    expected_return_p10: Optional[float] = None
    expected_return_p50: Optional[float] = None
    expected_return_p90: Optional[float] = None
    prob_up: Optional[float] = None
    prob_down: Optional[float] = None
    prob_no_edge: Optional[float] = None
    decision: str = "observe"
    no_trade_reason: str = ""
    neutral_reason: str = ""
    edge_score: Optional[float] = None

    # --- 各 Agent 结果 ---
    agent_results: list[AnalysisResult] = field(default_factory=list)

    # --- 汇总分析 ---
    summary: str = ""  # 综合分析文字
    key_risks: list[str] = field(default_factory=list)
    disagreements: list[str] = field(default_factory=list)  # Agent 间分歧点

    def __post_init__(self):
        if not isinstance(self.direction, Direction):
            self.direction = Direction(str(self.direction or "neutral"))
        if isinstance(self.magnitude, dict):
            self.magnitude = Magnitude(**self.magnitude)
        self.prediction_target = resolve_prediction_target(
            self.timeframe,
            self.direction,
            self.magnitude,
            self.confidence,
            self.prediction_target,
            target=self.target,
        )
        self._sync_distribution_fields()

    def _sync_distribution_fields(self) -> None:
        """从 prediction_target 补齐最终收益分布字段。"""
        pt = self.prediction_target
        if pt is None:
            return
        if self.expected_excess_return_pct is None:
            self.expected_excess_return_pct = pt.expected_return_pct
        if self.expected_return_p10 is None:
            self.expected_return_p10 = pt.expected_return_p10
        if self.expected_return_p50 is None:
            self.expected_return_p50 = pt.expected_return_p50 or pt.expected_return_pct
        if self.expected_return_p90 is None:
            self.expected_return_p90 = pt.expected_return_p90
        if self.prob_up is None:
            self.prob_up = pt.prob_up
        if self.prob_down is None:
            self.prob_down = pt.prob_down
        if self.prob_no_edge is None:
            self.prob_no_edge = pt.prob_neutral
        if self.edge_score is None:
            expected = abs(float(self.expected_excess_return_pct or 0.0))
            threshold = max(abs(float(pt.up_threshold_pct or 0.0)), abs(float(pt.down_threshold_pct or 0.0)), 1.0)
            directional_edge = max(float(self.prob_up or 0.0), float(self.prob_down or 0.0))
            self.edge_score = round(min(1.0, (expected / threshold) * directional_edge), 4)
        if not self.decision:
            self.decision = "observe"
        if self.direction == Direction.NEUTRAL and not self.neutral_reason:
            self.neutral_reason = self.no_trade_reason or "no_edge"

    def to_markdown(self) -> str:
        """生成 Markdown 格式的最终报告（Phase 1+ 完整实现）"""
        lines: list[str] = []

        lines.append(f"# 📊 市场预测分析报告")
        lines.append("")
        lines.append(f"**标的**: {self.target}")
        lines.append(f"**分析时间**: {self.generated_at[:19]}")
        lines.append(f"**预测周期**: {self.timeframe}")
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## 🎯 综合预测")
        lines.append("")

        dir_emoji = {"bullish": "📈 看涨", "bearish": "📉 看跌", "neutral": "➡️ 震荡"}
        lines.append(f"- **方向**: {dir_emoji.get(self.direction.value, self.direction.value)}")
        if self.magnitude:
            lines.append(f"- **幅度区间**: {self.magnitude.range_str}")
        lines.append(f"- **综合置信度**: {self.confidence:.0%}")
        lines.append(f"- **决策边际**: {self.decision} | edge={self.edge_score if self.edge_score is not None else 'N/A'}")
        if self.no_trade_reason:
            lines.append(f"- **无交易/观望原因**: {self.no_trade_reason}")
        if self.neutral_reason:
            lines.append(f"- **中性细分原因**: {self.neutral_reason}")
        if self.prediction_target:
            pt = self.prediction_target
            lines.append(
                f"- **目标规格**: {pt.horizon} / {pt.evaluation_mode} / "
                f"{pt.target_type}"
            )
            lines.append(
                f"- **收益目标**: 预期 {pt.expected_return_pct:+.1f}% | "
                f"上障碍 {pt.up_threshold_pct:+.1f}% | 下障碍 {pt.down_threshold_pct:+.1f}%"
            )
            lines.append(
                f"- **收益分布**: P涨 {self.prob_up or 0:.0%} | "
                f"P跌 {self.prob_down or 0:.0%} | P无边际 {self.prob_no_edge or 0:.0%}"
            )
            if self.expected_return_p10 is not None and self.expected_return_p90 is not None:
                lines.append(
                    f"- **残差收益区间**: P10 {self.expected_return_p10:+.1f}% | "
                    f"P50 {(self.expected_return_p50 or 0):+.1f}% | "
                    f"P90 {self.expected_return_p90:+.1f}%"
                )

        # 🆕 Phase 2: 各 Agent 置信度一览
        if self.agent_results:
            lines.append("")
            lines.append("### 📊 各维度置信度")
            lines.append("")
            lines.append("| 分析师 | 方向 | 幅度 | 置信度 | 数据质量 |")
            lines.append("|--------|------|------|--------|---------|")
            for r in self.agent_results:
                mag = r.magnitude.range_str if r.magnitude else "N/A"
                dir_label = {"bullish": "📈", "bearish": "📉", "neutral": "➡️"}.get(r.direction.value, "❓")
                # 🆕 Round2: 质量标签
                qual = self._quality_tag(r)
                lines.append(
                    f"| {r.agent_name} | {dir_label} {r.direction.value} | {mag} | **{r.confidence:.0%}** | {qual} |"
                )
            lines.append("")

            lines.append("### 预测目标与概率")
            lines.append("")
            lines.append("| 分析师 | Horizon | 目标类型 | 预期收益 | P(涨) | P(跌) | P(中性) |")
            lines.append("|--------|---------|----------|---------:|------:|------:|--------:|")
            for r in self.agent_results:
                pt = r.prediction_target or resolve_prediction_target(
                    r.timeframe, r.direction, r.magnitude, r.confidence,
                    target=r.target,
                )
                lines.append(
                    f"| {r.agent_name} | {pt.horizon} | {pt.target_type} "
                    f"| {pt.expected_return_pct:+.1f}% "
                    f"| {pt.prob_up:.0%} | {pt.prob_down:.0%} | {pt.prob_neutral:.0%} |"
                )
            lines.append("")

            fundamental_evidence = [
                summary for summary in (
                    self._fundamental_evidence_summary(r) for r in self.agent_results
                ) if summary
            ]
            if fundamental_evidence:
                lines.append("### 公司前景证据摘要")
                lines.append("")
                lines.append("| 矩阵 | 建议方向 | 质量评分 | PE分位 | 价值陷阱 | 置信上限 |")
                lines.append("|------|----------|----------|--------|----------|----------|")
                for item in fundamental_evidence:
                    trap = "是" if item.get("is_value_trap") else "否"
                    lines.append(
                        f"| {item.get('matrix_position', 'N/A')} "
                        f"| {item.get('suggested_direction', 'neutral')} "
                        f"| {self._format_ratio(item.get('quality_score'))} "
                        f"| {self._format_ratio(item.get('pe_percentile'))} "
                        f"| {trap} | {self._format_ratio(item.get('max_confidence'))} |"
                    )
                lines.append("")

            structured_evidence = [
                summary for summary in (
                    self._structured_evidence_summary(r) for r in self.agent_results
                ) if summary
            ]
            if structured_evidence:
                lines.append("### 结构化证据摘要")
                lines.append("")
                lines.append("| 分析师 | 领域 | 矩阵 | 建议方向 | 质量评分 | 置信上限 |")
                lines.append("|--------|------|------|----------|----------|----------|")
                for item in structured_evidence:
                    lines.append(
                        f"| {item.get('agent', 'N/A')} "
                        f"| {item.get('domain', '通用')} "
                        f"| {item.get('matrix_position', 'N/A')} "
                        f"| {item.get('suggested_direction', 'neutral')} "
                        f"| {self._format_ratio(item.get('quality_score'))} "
                        f"| {self._format_ratio(item.get('max_confidence'))} |"
                    )
                lines.append("")

        if self.summary:
            lines.append("")
            lines.append(self.summary)

        if self.key_risks:
            lines.append("")
            lines.append("## ⚠️ 关键风险提示")
            lines.append("")
            for risk in self.key_risks:
                lines.append(f"- {risk}")

        if self.disagreements:
            lines.append("")
            lines.append("## 🔀 分歧点")
            lines.append("")
            for d in self.disagreements:
                lines.append(f"- {d}")

        return "\n".join(lines)

    @staticmethod
    def _quality_tag(result: AnalysisResult) -> str:
        """给 Markdown 表格生成稳定的质量标签。"""
        if result.status == "failed":
            return "❌ 失败"
        if result.status == "degraded":
            return "⚠️ 降级"
        if result.confidence <= 0.15 or result.data_quality_score < 0.4:
            return "⚠️ 低可信"
        if result.data_quality_score < 0.75:
            return "⚠️ 数据一般"
        return "✅ 正常"

    @staticmethod
    def _safe_float(value, default=None):
        try:
            if value in (None, "", "N/A"):
                return default
            if isinstance(value, str):
                stripped = value.strip()
                if stripped.endswith("%"):
                    return float(stripped[:-1]) / 100
                value = stripped
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _format_ratio(value) -> str:
        value = FinalReport._safe_float(value, None)
        if value is None:
            return "N/A"
        return f"{value:.0%}" if abs(value) <= 1 else f"{value:.0f}"

    @staticmethod
    def _fundamental_evidence_summary(result: AnalysisResult) -> dict:
        summary = result.data_summary or {}
        if not isinstance(summary, dict):
            return {}

        packet = summary.get("evidence", {})
        if not isinstance(packet, dict):
            packet = {}

        matrix = packet.get("decision_matrix") or summary.get("decision_matrix") or {}
        if result.agent_name != "公司前景分析师" and not matrix:
            return {}

        confidence = (
            packet.get("confidence_constraints")
            or packet.get("confidence_model")
            or summary.get("confidence_constraints")
            or {}
        )
        data_quality = packet.get("data_quality") or summary.get("data_quality") or {}
        valuation = packet.get("valuation_analysis") or summary.get("valuation_analysis") or {}
        trap = packet.get("value_trap_analysis") or summary.get("value_trap_analysis") or {}

        return {
            "agent": result.agent_name,
            "matrix_position": matrix.get("matrix_position"),
            "suggested_direction": matrix.get("suggested_direction"),
            "quality_score": FinalReport._safe_float(
                data_quality.get("overall_quality"),
                FinalReport._safe_float(summary.get("quality"), None),
            ),
            "pe_percentile": FinalReport._safe_float(
                valuation.get("pe_percentile_3yr"), None,
            ),
            "is_value_trap": bool(trap.get("is_trap")),
            "max_confidence": FinalReport._safe_float(
                confidence.get("max_confidence"),
                FinalReport._safe_float(confidence.get("ceiling"), None),
            ),
            "hard_caps": confidence.get("hard_caps", []) or [],
            "consistency_issues": summary.get("consistency_issues", []) or [],
        }

    @staticmethod
    def _structured_evidence_summary(result: AnalysisResult) -> dict:
        summary = result.data_summary or {}
        if not isinstance(summary, dict):
            return {}

        packet = summary.get("evidence", {})
        if not isinstance(packet, dict):
            packet = {}

        matrix = packet.get("decision_matrix") or summary.get("decision_matrix") or {}
        confidence = (
            packet.get("confidence_constraints")
            or packet.get("confidence_model")
            or summary.get("confidence_constraints")
            or {}
        )
        evidence_lists = packet.get("evidence") or {}
        if not (matrix or confidence or evidence_lists):
            return {}

        data_quality = (
            packet.get("data_quality")
            or packet.get("source_quality")
            or summary.get("data_quality")
            or {}
        )

        quality_score = FinalReport._safe_float(
            data_quality.get("overall_quality"),
            FinalReport._safe_float(
                data_quality.get("overall"),
                FinalReport._safe_float(
                    data_quality.get("overall_freshness"),
                    FinalReport._safe_float(
                        data_quality.get("quality_score"),
                        FinalReport._safe_float(
                            data_quality.get("score"),
                            FinalReport._safe_float(summary.get("quality"), None),
                        ),
                    ),
                ),
            ),
        )

        return {
            "agent": result.agent_name,
            "domain": STRUCTURED_EVIDENCE_DOMAINS.get(result.agent_name, "通用"),
            "matrix_position": matrix.get("matrix_position"),
            "suggested_direction": matrix.get("suggested_direction"),
            "quality_score": quality_score,
            "max_confidence": FinalReport._safe_float(
                confidence.get("max_confidence"),
                FinalReport._safe_float(confidence.get("ceiling"), None),
            ),
            "hard_caps": confidence.get("hard_caps", []) or [],
            "consistency_issues": summary.get("consistency_issues", []) or [],
            "bullish_evidence": evidence_lists.get("bullish", []) or [],
            "bearish_evidence": evidence_lists.get("bearish", []) or [],
            "neutral_evidence": evidence_lists.get("neutral", []) or [],
        }

    def to_dict(self) -> dict:
        """序列化为字典"""
        return {
            "target": self.target,
            "timeframe": self.timeframe,
            "generated_at": self.generated_at,
            "direction": self.direction.value,
            "magnitude": asdict(self.magnitude) if self.magnitude else None,
            "confidence": self.confidence,
            "prediction_target": (
                self.prediction_target.to_dict() if self.prediction_target else None
            ),
            "expected_excess_return_pct": self.expected_excess_return_pct,
            "expected_return_p10": self.expected_return_p10,
            "expected_return_p50": self.expected_return_p50,
            "expected_return_p90": self.expected_return_p90,
            "prob_up": self.prob_up,
            "prob_down": self.prob_down,
            "prob_no_edge": self.prob_no_edge,
            "decision": self.decision,
            "no_trade_reason": self.no_trade_reason,
            "neutral_reason": self.neutral_reason,
            "edge_score": self.edge_score,
            "agent_results": [r.to_dict() for r in self.agent_results],
            "summary": self.summary,
            "key_risks": self.key_risks,
            "disagreements": self.disagreements,
            "fundamental_evidence": [
                summary for summary in (
                    self._fundamental_evidence_summary(r)
                    for r in self.agent_results
                ) if summary
            ],
            "structured_evidence": [
                summary for summary in (
                    self._structured_evidence_summary(r)
                    for r in self.agent_results
                ) if summary
            ],
        }

    def to_json(self, indent: int = 2) -> str:
        """序列化为 JSON 字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)
