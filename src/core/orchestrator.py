"""
调度器 (Orchestrator)

管理 Agent 团队，负责：
- Agent 注册/移除
- 并行任务分发
- 结果收集与传递

使用 Python 3.11+ 的 asyncio.TaskGroup 实现并行执行，
任一 Agent 失败不影响其他 Agent。
"""

import asyncio
import logging
import time
from typing import Optional

from .base_agent import BaseAgent
from .result import AnalysisResult


class Orchestrator:
    """调度器：管理 Agent 团队，协调分析任务

    使用示例:
        orchestrator = Orchestrator()
        orchestrator.register(technical_analyst)
        orchestrator.register(news_analyst)

        results = await orchestrator.run_all("0700.HK", "短期(1周)")
    """

    def __init__(self, logger: Optional[logging.Logger] = None):
        """
        Args:
            logger: 日志记录器，None 时使用默认 logger
        """
        self._agents: dict[str, BaseAgent] = {}
        self.logger = logger or logging.getLogger("Orchestrator")

    # ================================================================
    # Agent 管理
    # ================================================================

    def register(self, agent: BaseAgent) -> None:
        """注册一个 Agent

        Args:
            agent: 要注册的 Agent 实例
        """
        if agent.name in self._agents:
            self.logger.warning(
                f"Agent '{agent.name}' 已存在，将被覆盖"
            )
        self._agents[agent.name] = agent
        self.logger.info(f"✅ Agent 已注册: {agent.name} — {agent.description}")

    def unregister(self, name: str) -> None:
        """移除一个 Agent

        Args:
            name: Agent 名称
        """
        if name in self._agents:
            del self._agents[name]
            self.logger.info(f"❌ Agent 已移除: {name}")
        else:
            self.logger.warning(f"Agent '{name}' 不存在，无法移除")

    def get_agent(self, name: str) -> Optional[BaseAgent]:
        """获取指定名称的 Agent

        Args:
            name: Agent 名称

        Returns:
            Agent 实例，不存在返回 None
        """
        return self._agents.get(name)

    def list_agents(self) -> list[str]:
        """列出所有已注册的 Agent 名称"""
        return list(self._agents.keys())

    @property
    def agent_count(self) -> int:
        """已注册 Agent 数量"""
        return len(self._agents)

    # ================================================================
    # 任务执行
    # ================================================================

    async def run_all(
        self,
        target: str,
        timeframe: str,
        agent_names: Optional[list[str]] = None,
    ) -> list[AnalysisResult]:
        """并行运行指定的 Agent（或全部）

        使用 asyncio.TaskGroup 实现：
        - 所有 Agent 同时开始执行
        - 任一 Agent 异常不影响其他
        - 等待所有完成（或超时/异常）后返回

        Args:
            target: 分析标的，如 "0700.HK"
            timeframe: 预测周期，如 "短期(1周)"
            agent_names: 要运行的 Agent 名称列表，None 表示运行全部

        Returns:
            所有成功完成的 Agent 分析结果列表
        """
        names = agent_names or list(self._agents.keys())

        # 筛选存在的 Agent
        agents_to_run: list[BaseAgent] = []
        for name in names:
            agent = self._agents.get(name)
            if agent:
                agents_to_run.append(agent)
            else:
                self.logger.warning(f"Agent '{name}' 未注册，跳过")

        if not agents_to_run:
            self.logger.warning("没有可用的 Agent，返回空结果")
            return []

        # 打印启动信息
        agent_names_str = ", ".join(a.name for a in agents_to_run)
        self.logger.info(
            f"🚀 开始并行分析 | 标的={target} | 周期={timeframe} | "
            f"Agent=[{agent_names_str}]"
        )
        start_time = time.monotonic()

        # === 并行执行 ===
        results: list[AnalysisResult] = []
        tasks: dict[str, asyncio.Task] = {}

        try:
            async with asyncio.TaskGroup() as tg:
                for agent in agents_to_run:
                    task = tg.create_task(
                        agent.run(target, timeframe),
                        name=agent.name,
                    )
                    tasks[agent.name] = task

        except Exception as e:
            # TaskGroup 会将子任务异常包装成 ExceptionGroup
            self.logger.error(f"TaskGroup 异常: {e}")

        # 收集结果
        success_count = 0
        fail_count = 0
        for name, task in tasks.items():
            try:
                result = task.result()
                results.append(result)
                success_count += 1
            except Exception as e:
                fail_count += 1
                self.logger.error(f"Agent '{name}' 执行失败: {e}")

        elapsed = time.monotonic() - start_time
        self.logger.info(
            f"🏁 分析完成 | 耗时={elapsed:.1f}s | "
            f"成功={success_count} | 失败={fail_count}"
        )

        return results

    async def run_single(
        self,
        target: str,
        timeframe: str,
        agent_name: str,
    ) -> Optional[AnalysisResult]:
        """运行单个 Agent

        Args:
            target: 分析标的
            timeframe: 预测周期
            agent_name: Agent 名称

        Returns:
            分析结果，Agent 不存在返回 None
        """
        agent = self._agents.get(agent_name)
        if not agent:
            self.logger.error(f"Agent '{agent_name}' 未注册")
            return None

        self.logger.info(f"🎯 单 Agent 执行: {agent_name} | {target} | {timeframe}")
        result = await agent.run(target, timeframe)
        return result

    async def run_selected(
        self,
        target: str,
        timeframe: str,
        agent_names: list[str],
    ) -> list[AnalysisResult]:
        """运行指定的 Agent 列表（run_all 的别名，语义更明确）"""
        return await self.run_all(target, timeframe, agent_names=agent_names)

    # ================================================================
    # 状态查询
    # ================================================================

    def describe(self) -> str:
        """返回当前 Agent 团队描述"""
        if not self._agents:
            return "当前无已注册 Agent"

        lines = ["当前 Agent 团队:"]
        for i, (name, agent) in enumerate(self._agents.items(), 1):
            lines.append(f"  {i}. {name} — {agent.description}")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return f"<Orchestrator: {self.agent_count} agents>"
