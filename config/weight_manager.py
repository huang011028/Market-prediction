"""
权重管理器

加载 agent_config.yaml，提供：
- 分时间维度的权重查询
- Agent 失败时的权重重新分配
- 启用的 Agent 列表查询
"""

import yaml
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class WeightConfig:
    """某个时间维度的权重配置"""
    agent_weights: dict[str, float] = field(default_factory=dict)
    synthesis_weight: float = 0.0


class WeightManager:
    """权重管理器 — 全局单例"""

    def __init__(self, config_path: Optional[Path] = None):
        """
        Args:
            config_path: agent_config.yaml 路径，None 时自动定位
        """
        if config_path is None:
            config_path = Path(__file__).resolve().parent / "agent_config.yaml"

        with open(config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

        self._fallback_strategy = self.config.get("fallback", {}).get("strategy", "proportional")

    # ================================================================
    # 权重查询
    # ================================================================

    def get_weights(self, timeframe: str) -> WeightConfig:
        """根据时间维度返回权重配置

        Args:
            timeframe: "短期" / "中期" / "长期"

        Returns:
            WeightConfig: 权重配置对象
        """
        # 解析时间维度
        label = self._parse_timeframe(timeframe)
        weights_data = self.config.get("weights", {}).get(label, {})

        # 分离 Agent 权重和综合研判权重
        agent_weights = {}
        synthesis_weight = 0.0

        for name, w in weights_data.items():
            if name == "综合研判":
                synthesis_weight = float(w)
            else:
                agent_weights[name] = float(w)

        return WeightConfig(
            agent_weights=agent_weights,
            synthesis_weight=synthesis_weight,
        )

    def get_weight(self, agent_name: str, timeframe: str) -> float:
        """获取单个 Agent 在指定时间维度的权重"""
        wc = self.get_weights(timeframe)
        return wc.agent_weights.get(agent_name, 0.0)

    def get_synthesis_weight(self, timeframe: str) -> float:
        """获取综合研判的权重"""
        wc = self.get_weights(timeframe)
        return wc.synthesis_weight

    def _parse_timeframe(self, timeframe: str) -> str:
        """将中文时间描述映射到配置中的 key"""
        t = timeframe.lower()
        if "长期" in t or "季度" in t:
            return "long"
        elif "中期" in t or "月" in t:
            return "medium"
        else:
            return "short"

    # ================================================================
    # 降级时的权重再分配
    # ================================================================

    def redistribute_weights(
        self,
        timeframe: str,
        active_agent_names: list[str],
        failed_agent_names: list[str],
    ) -> WeightConfig:
        """Agent 失败时将失败者的权重按策略再分配

        Args:
            timeframe: 时间维度
            active_agent_names: 成功执行的 Agent 名称列表
            failed_agent_names: 执行失败的 Agent 名称列表

        Returns:
            重新分配后的 WeightConfig
        """
        if not failed_agent_names:
            return self.get_weights(timeframe)

        original = self.get_weights(timeframe)

        # 收集失败的总权重
        failed_weight = sum(
            original.agent_weights.get(name, 0.0)
            for name in failed_agent_names
        )

        if failed_weight <= 0 or not active_agent_names:
            return original

        # 收集活跃 Agent 的原始权重
        active_original = {
            name: original.agent_weights.get(name, 0.0)
            for name in active_agent_names
        }
        total_active_original = sum(active_original.values())

        new_weights = dict(original.agent_weights)

        if self._fallback_strategy == "equal":
            # 均分
            share = failed_weight / len(active_agent_names)
            for name in active_agent_names:
                new_weights[name] = original.agent_weights.get(name, 0.0) + share

        elif self._fallback_strategy == "proportional":
            # 按比例分配
            if total_active_original > 0:
                for name in active_agent_names:
                    proportion = active_original[name] / total_active_original
                    new_weights[name] = (
                        original.agent_weights.get(name, 0.0) +
                        failed_weight * proportion
                    )
            else:
                # 所有活跃 Agent 原始权重为 0（极端情况），均分
                share = failed_weight / len(active_agent_names)
                for name in active_agent_names:
                    new_weights[name] = share

        elif self._fallback_strategy == "ignore":
            # 不分配，权重总和变小（不推荐）
            pass

        # 移除失败的 Agent
        for name in failed_agent_names:
            new_weights.pop(name, None)

        return WeightConfig(
            agent_weights=new_weights,
            synthesis_weight=original.synthesis_weight,
        )

    # ================================================================
    # Agent 配置查询
    # ================================================================

    def get_enabled_agents(self) -> list[str]:
        """获取配置中启用的 Agent 名称列表"""
        agents = self.config.get("agents", [])
        return [a["name"] for a in agents if a.get("enabled", True)]

    def get_agent_config(self, name: str) -> Optional[dict]:
        """获取指定 Agent 的配置"""
        for agent in self.config.get("agents", []):
            if agent["name"] == name:
                return agent
        return None

    def timeframes(self) -> list[str]:
        """所有支持的时间维度 key"""
        return list(self.config.get("weights", {}).keys())

    # ================================================================
    # 格式化输出
    # ================================================================

    def weights_summary(self, timeframe: str) -> str:
        """人类可读的权重摘要"""
        wc = self.get_weights(timeframe)
        lines = [f"时间维度: {timeframe}"]
        for name, w in sorted(wc.agent_weights.items(), key=lambda x: -x[1]):
            lines.append(f"  {name}: {w:.0%}")
        if wc.synthesis_weight > 0:
            lines.append(f"  综合研判: {wc.synthesis_weight:.0%}")
        return "\n".join(lines)
