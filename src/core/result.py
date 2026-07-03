"""
分析结果数据结构

定义所有 Agent 的统一输出格式，包含方向、幅度、置信度、
推理过程等核心字段。支持 JSON 序列化/反序列化。
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
import json


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

    # --- 可解释性 ---
    reasoning: str = ""  # 推理过程（Markdown 格式）
    key_factors: list[str] = field(default_factory=list)  # 关键影响因素
    risks: list[str] = field(default_factory=list)  # 风险提示

    # --- 数据摘要 ---
    data_summary: dict = field(default_factory=dict)  # 使用的数据摘要

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

    # --- 各 Agent 结果 ---
    agent_results: list[AnalysisResult] = field(default_factory=list)

    # --- 汇总分析 ---
    summary: str = ""  # 综合分析文字
    key_risks: list[str] = field(default_factory=list)
    disagreements: list[str] = field(default_factory=list)  # Agent 间分歧点

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

    def to_dict(self) -> dict:
        """序列化为字典"""
        return {
            "target": self.target,
            "timeframe": self.timeframe,
            "generated_at": self.generated_at,
            "direction": self.direction.value,
            "magnitude": asdict(self.magnitude) if self.magnitude else None,
            "confidence": self.confidence,
            "agent_results": [r.to_dict() for r in self.agent_results],
            "summary": self.summary,
            "key_risks": self.key_risks,
            "disagreements": self.disagreements,
        }

    def to_json(self, indent: int = 2) -> str:
        """序列化为 JSON 字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)
