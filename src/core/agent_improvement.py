"""
Agent 改进建议器

把校准统计翻译成可审计的工程建议，用于判断应该优化 prompt、skill、
MCP、数据源策略还是校准策略。它只生成建议，不自动修改工具/提示词配置。
"""

from dataclasses import asdict, dataclass
from typing import Optional


@dataclass
class AgentImprovementSignal:
    """一次可执行的 Agent 改进信号。"""

    agent_name: str
    area: str
    priority: str
    bucket_group: str
    bucket: str
    sample_size: int
    accuracy: float
    issue: str
    recommendation: str

    def to_dict(self) -> dict:
        return asdict(self)


class AgentImprovementAdvisor:
    """从校准统计生成 prompt/skill/MCP/data_source/calibration 改进建议。"""

    DEFAULT_MIN_SAMPLES = 5
    LOW_ACCURACY = 0.45
    HIGH_ACCURACY = 0.70

    def recommend(
        self,
        agent_name: str,
        stats: dict,
        min_samples: Optional[int] = None,
    ) -> list[AgentImprovementSignal]:
        min_samples = min_samples or self.DEFAULT_MIN_SAMPLES
        signals: list[AgentImprovementSignal] = []

        for bucket_group, buckets in (stats or {}).items():
            if not isinstance(buckets, dict):
                continue
            for bucket, values in buckets.items():
                total = int(values.get("total", 0) or 0)
                accuracy = values.get("accuracy")
                if accuracy is None or total < min_samples:
                    continue
                accuracy = float(accuracy)
                if accuracy < self.LOW_ACCURACY:
                    signals.append(
                        self._build_low_accuracy_signal(
                            agent_name, bucket_group, bucket, total, accuracy,
                        )
                    )
                elif accuracy >= self.HIGH_ACCURACY and total >= min_samples * 2:
                    signals.append(
                        self._build_strength_signal(
                            agent_name, bucket_group, bucket, total, accuracy,
                        )
                    )

        return sorted(
            signals,
            key=lambda s: (
                {"P0": 0, "P1": 1, "P2": 2}.get(s.priority, 3),
                s.accuracy,
                -s.sample_size,
            ),
        )

    def to_markdown(self, signals: list[AgentImprovementSignal]) -> str:
        if not signals:
            return "暂无达到样本阈值的 Agent 工程改进信号。"
        lines = [
            "| 优先级 | Agent | 改进面 | 场景桶 | 样本 | 命中率 | 建议 |",
            "| --- | --- | --- | --- | ---: | ---: | --- |",
        ]
        for signal in signals:
            lines.append(
                "| {priority} | {agent} | {area} | {group}/{bucket} | {total} | {acc:.1%} | {rec} |".format(
                    priority=signal.priority,
                    agent=signal.agent_name,
                    area=signal.area,
                    group=signal.bucket_group,
                    bucket=signal.bucket,
                    total=signal.sample_size,
                    acc=signal.accuracy,
                    rec=signal.recommendation,
                )
            )
        return "\n".join(lines)

    def _build_low_accuracy_signal(
        self,
        agent_name: str,
        bucket_group: str,
        bucket: str,
        total: int,
        accuracy: float,
    ) -> AgentImprovementSignal:
        area, recommendation = self._map_area_and_recommendation(
            agent_name, bucket_group, bucket, is_strength=False,
        )
        priority = "P0" if accuracy < 0.35 and total >= 10 else "P1"
        issue = (
            f"{bucket_group}/{bucket} 历史命中率仅 {accuracy:.1%}，"
            f"样本数 {total}，需要降低该场景的自动置信或改进证据解释。"
        )
        return AgentImprovementSignal(
            agent_name=agent_name,
            area=area,
            priority=priority,
            bucket_group=bucket_group,
            bucket=bucket,
            sample_size=total,
            accuracy=accuracy,
            issue=issue,
            recommendation=recommendation,
        )

    def _build_strength_signal(
        self,
        agent_name: str,
        bucket_group: str,
        bucket: str,
        total: int,
        accuracy: float,
    ) -> AgentImprovementSignal:
        area, recommendation = self._map_area_and_recommendation(
            agent_name, bucket_group, bucket, is_strength=True,
        )
        issue = (
            f"{bucket_group}/{bucket} 历史命中率 {accuracy:.1%}，"
            f"样本数 {total}，可以保留该场景的判断框架。"
        )
        return AgentImprovementSignal(
            agent_name=agent_name,
            area=area,
            priority="P2",
            bucket_group=bucket_group,
            bucket=bucket,
            sample_size=total,
            accuracy=accuracy,
            issue=issue,
            recommendation=recommendation,
        )

    @staticmethod
    def _map_area_and_recommendation(
        agent_name: str,
        bucket_group: str,
        bucket: str,
        is_strength: bool,
    ) -> tuple[str, str]:
        if is_strength:
            return (
                "prompt",
                "保留当前证据解释路径；后续 prompt/skill 调整避免削弱该场景判断。",
            )

        if bucket_group == "confidence_bins":
            return (
                "calibration",
                "下调该置信度区间的输出上限，并在 prompt 中要求引用历史校准命中率。",
            )

        if bucket_group in {
            "source_buckets",
            "freshness_buckets",
            "news_count_buckets",
            "data_quality_buckets",
            "data_quality_levels",
            "source_type_buckets",
        }:
            return (
                "data_source",
                "调整该场景的数据源策略：补充替代来源、降低低质量来源权重，并限制高置信输出。",
            )

        if bucket_group in {"mcp_buckets", "tool_buckets", "api_health_buckets"}:
            return (
                "mcp",
                "检查 MCP/工具调用覆盖率、失败重试和字段完整性，避免工具缺口直接变成强方向。",
            )

        if agent_name == "近期股价分析师":
            if bucket_group in {
                "position_buckets",
                "sr_zone_buckets",
                "risk_reward_buckets",
                "volume_buckets",
            }:
                return (
                    "prompt",
                    "强化支撑压力与量价确认规则，失败场景默认降级为中性或低置信。",
                )
            if bucket_group in {
                "trend_buckets",
                "momentum_buckets",
                "market_regime_buckets",
                "volatility_buckets",
                "technical_scenario_buckets",
                "regime_sr_buckets",
                "regime_volume_buckets",
                "sr_volume_buckets",
                "intraday_buckets",
            }:
                return (
                    "skill",
                    "检查技术指标特征工程和场景标签，补充该桶的反例规则与阈值校验。",
                )
            return (
                "calibration",
                "把该技术场景加入专用校准器硬约束，限制单次判断过度自信。",
            )

        if agent_name == "最新新闻分析师":
            if bucket_group in {"sentiment_buckets", "event_buckets"}:
                return (
                    "prompt",
                    "调整新闻事件归因 prompt，要求区分事实催化、传闻、情绪噪声和已兑现信息。",
                )
            return (
                "calibration",
                "把该新闻场景加入专用校准器硬约束，限制情绪面过度自信。",
            )

        return (
            "calibration",
            "先通过校准统计降低该场景置信度，再根据失败样本决定是否改 prompt/skill/MCP。",
        )
