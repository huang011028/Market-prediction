"""
Agent 基类

所有分析 Agent 的抽象基类，采用模板方法模式：
- run() 定义标准分析流程（采集 → 分析 → 校验）
- 子类只需实现 gather_data() 和 _get_system_prompt()
"""

import asyncio
import json
import re
import time
import logging
from abc import ABC, abstractmethod
from typing import Optional

from .result import AnalysisResult, Direction, Magnitude
from .llm_client import LLMClient

# ================================================================
# Agent 基类
# ================================================================


class BaseAgent(ABC):
    """所有分析师的抽象基类

    子类只需实现:
    1. gather_data(target, timeframe)  — 数据采集
    2. _get_system_prompt()            — 系统提示词

    analyze() 有默认实现，基于 LLM 推理。子类可覆盖以自定义分析逻辑。

    使用示例:
        class MyAnalyst(BaseAgent):
            async def gather_data(self, target, timeframe):
                return {"prices": [...]}

            def _get_system_prompt(self):
                return "你是一个专业分析师..."
    """

    def __init__(
        self,
        name: str,
        description: str,
        llm: LLMClient,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Args:
            name: Agent 名称，如 "技术面分析师"
            description: 一句话描述职责
            llm: LLM 客户端实例
            logger: 日志记录器，None 时使用默认 logger
        """
        self.name = name
        self.description = description
        self.llm = llm
        self.logger = logger or logging.getLogger(name)

    # ================================================================
    # 模板方法（子类不应覆盖）
    # ================================================================

    async def run(self, target: str, timeframe: str) -> AnalysisResult:
        """模板方法：完整的分析流程

        流程:
        1. 数据采集 (gather_data)
        2. 分析推理 (analyze)
        3. 结果校验 (validate)

        Args:
            target:  分析标的，如 "0700.HK"
            timeframe: 预测周期，如 "短期(1周)"

        Returns:
            AnalysisResult —— 一定会返回，不会抛异常
        """
        self.logger.info(f"[{self.name}] 开始分析 {target} ({timeframe})")
        start_time = time.monotonic()

        try:
            # === Step 1: 采集数据（60 秒超时）===
            self.logger.debug(f"[{self.name}] 采集数据中...")
            data = await asyncio.wait_for(
                self.gather_data(target, timeframe),
                timeout=60,
            )

            # === Step 2: 分析推理（120 秒超时）===
            self.logger.debug(f"[{self.name}] LLM 分析推理中...")
            context = self.build_context(target, timeframe)
            result = await asyncio.wait_for(
                self.analyze(data, context),
                timeout=120,
            )

            # === Step 3: 校验结果 ===
            errors = result.validate()
            if errors:
                self.logger.warning(
                    f"[{self.name}] 结果校验警告: {errors}"
                )

            elapsed = time.monotonic() - start_time
            magnitude_str = result.magnitude.range_str if result.magnitude else "N/A"
            self.logger.info(
                f"[{self.name}] 分析完成 | "
                f"方向={result.direction.value} | "
                f"幅度={magnitude_str} | "
                f"置信度={result.confidence:.0%} | "
                f"耗时={elapsed:.1f}s"
            )

            return result

        except asyncio.TimeoutError:
            self.logger.error(f"[{self.name}] 分析超时")
            return self._fallback_result(
                target, timeframe, "分析超时，无法在规定时间内完成数据采集或推理"
            )

        except Exception as e:
            self.logger.error(f"[{self.name}] 分析异常: {e}", exc_info=True)
            return self._fallback_result(
                target, timeframe, f"分析过程异常: {str(e)}"
            )

    # ================================================================
    # 子类必须实现
    # ================================================================

    @abstractmethod
    async def gather_data(self, target: str, timeframe: str) -> dict:
        """采集该 Agent 所需的原始数据

        Args:
            target: 标的代码
            timeframe: 预测周期

        Returns:
            原始数据字典，格式由各 Agent 自行定义
            例如技术面: {"prices": [...], "indicators": {...}}
        """
        ...

    @abstractmethod
    def _get_system_prompt(self) -> str:
        """返回该 Agent 的系统提示词

        提示词应包含:
        - 角色定位
        - 分析框架
        - 输出格式要求（JSON，需包含 direction/magnitude/confidence/reasoning 等字段）
        """
        ...

    # ================================================================
    # 子类可选覆盖
    # ================================================================

    async def analyze(self, data: dict, context: dict) -> AnalysisResult:
        """基于数据进行分析推理

        默认实现：将数据拼接成 prompt 发给 LLM，解析返回的 JSON。

        子类可覆盖此方法以实现:
        - 多步推理链 (Chain of Thought)
        - 自定义数据预处理
        - 不使用 LLM 的规则化分析
        """
        system_prompt = self._get_system_prompt()
        user_prompt = self._build_user_prompt(data, context)

        response = await self.llm.achat(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

        return self._parse_llm_response(response.content, context)

    def build_context(self, target: str, timeframe: str) -> dict:
        """构建上下文信息（可被子类覆盖以注入额外上下文）"""
        return {
            "target": target,
            "timeframe": timeframe,
            "agent_name": self.name,
        }

    def _build_user_prompt(self, data: dict, context: dict) -> str:
        """构建发给 LLM 的用户提示词

        默认将 data 序列化为 JSON 嵌入。子类可覆盖以自定义格式。
        """
        data_str = json.dumps(data, ensure_ascii=False, indent=2)

        # 如果数据太长，截断
        if len(data_str) > 8000:
            data_str = data_str[:8000] + "\n... (数据过长，已截断)"

        return f"""请基于以下数据进行分析：

## 分析标的
{context.get('target', 'N/A')}

## 预测周期
{context.get('timeframe', 'N/A')}

## 原始数据
```json
{data_str}
```

请严格按照要求的 JSON 格式输出分析结果。"""

    # ================================================================
    # LLM 响应解析
    # ================================================================

    def _parse_llm_response(self, content: str, context: dict) -> AnalysisResult:
        """解析 LLM 返回的内容为 AnalysisResult

        尝试从返回中提取 JSON（支持 ```json ... ``` 包裹的格式）。
        解析失败时降级为包含原始文本的 neutral 结果。
        """
        # 尝试提取 JSON 代码块
        json_match = re.search(
            r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL
        )
        if json_match:
            json_str = json_match.group(1).strip()
        else:
            # 尝试找第一个 { 到最后一个 } 之间的内容
            brace_match = re.search(r"\{.*\}", content, re.DOTALL)
            if brace_match:
                json_str = brace_match.group(0)
            else:
                json_str = content

        # 修复 LLM 常见 JSON 格式错误
        json_str = re.sub(r':\s*\+(\d+\.?\d*)', r': \1', json_str)
        json_str = re.sub(r',\s*}', '}', json_str)
        json_str = re.sub(r',\s*]', ']', json_str)

        try:
            data = json.loads(json_str)

            # 解析 direction
            direction_raw = str(data.get("direction", "neutral")).lower().strip()
            try:
                direction = Direction(direction_raw)
            except ValueError:
                direction = Direction.NEUTRAL

            # 解析 magnitude
            magnitude = None
            if "magnitude" in data and data["magnitude"]:
                mag = data["magnitude"]
                magnitude = Magnitude(
                    min_pct=float(mag.get("min_pct", 0)),
                    max_pct=float(mag.get("max_pct", 0)),
                )

            return AnalysisResult(
                agent_name=self.name,
                target=context.get("target", ""),
                timeframe=context.get("timeframe", ""),
                direction=direction,
                magnitude=magnitude,
                confidence=float(data.get("confidence", 0.5)),
                reasoning=str(data.get("reasoning", content)),
                key_factors=data.get("key_factors", []),
                risks=data.get("risks", []),
                data_summary=data.get("data_summary", {}),
                status=data.get("status", "ok"),
                error_message=data.get("error_message"),
                data_quality_score=float(data.get("data_quality_score", 1.0)),
            )

        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            self.logger.warning(
                f"[{self.name}] JSON 解析失败: {e}，使用原始文本作为 reasoning"
            )
            return AnalysisResult(
                agent_name=self.name,
                target=context.get("target", ""),
                timeframe=context.get("timeframe", ""),
                direction=Direction.NEUTRAL,
                confidence=0.0,
                reasoning=content,
                key_factors=["LLM 返回格式异常，请查看 reasoning 字段"],
                status="degraded",
                error_message="LLM 返回格式异常",
                data_quality_score=0.0,
            )

    # ================================================================
    # 容错
    # ================================================================

    def _fallback_result(self, target: str, timeframe: str, reason: str) -> AnalysisResult:
        """生成兜底结果（超时或异常时使用）"""
        return AnalysisResult(
            agent_name=self.name,
            target=target,
            timeframe=timeframe,
            direction=Direction.NEUTRAL,
            confidence=0.0,
            reasoning=reason,
            risks=[reason],
            status="failed",
            error_message=reason,
            data_quality_score=0.0,
            data_summary={"error": reason},
        )

    # ================================================================
    # 工具方法
    # ================================================================

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}: {self.name} — {self.description}>"

    def __str__(self) -> str:
        return f"【{self.name}】{self.description}"
