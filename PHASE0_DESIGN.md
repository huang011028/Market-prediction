# Phase 0: 基础设施搭建 — 实现设计文档

> **目标**：搭建项目的"骨架"，让后续每个 Phase 只需关注 Agent 业务逻辑，插上就能跑。
>
> **原则**：所有路径使用相对路径，不依赖本机绝对路径，确保可无缝上传 GitHub。

---

## 目录

1. [Phase 0 目标清单](#1-phase-0-目标清单)
2. [项目根目录与路径约定](#2-项目根目录与路径约定)
3. [技术决策记录](#3-技术决策记录)
4. [模块详细设计](#4-模块详细设计)
   - [4.1 配置系统 (`config/settings.py`)](#41-配置系统-configsettingspy)
   - [4.2 LLM 客户端 (`src/core/llm_client.py`)](#42-llm-客户端-srccorellm_clientpy)
   - [4.3 分析结果数据结构 (`src/core/result.py`)](#43-分析结果数据结构-srccoreresultpy)
   - [4.4 Agent 基类 (`src/core/base_agent.py`)](#44-agent-基类-srccorebase_agentpy)
   - [4.5 调度器 (`src/core/orchestrator.py`)](#45-调度器-srccoreorchestratorpy)
   - [4.6 日志系统 (`src/utils/logger.py`)](#46-日志系统-srcutilsloggerpy)
   - [4.7 数据采集基类 (`src/core/data_collector.py`)](#47-数据采集基类-srccoredata_collectorpy)
5. [文件清单与创建顺序](#5-文件清单与创建顺序)
6. [配置模板文件](#6-配置模板文件)
7. [测试策略](#7-测试策略)
8. [Phase 0 完成标准](#8-phase-0-完成标准)

---

## 1. Phase 0 目标清单

| # | 任务 | 产出文件 | 优先级 |
|---|------|---------|--------|
| 0.1 | 创建完整目录结构 | 所有空目录 + `__init__.py` | ⭐⭐⭐ |
| 0.2 | 环境配置系统 | `config/settings.py`, `.env.example`, `.env` | ⭐⭐⭐ |
| 0.3 | LLM 调用客户端 | `src/core/llm_client.py` | ⭐⭐⭐ |
| 0.4 | 分析结果数据结构 | `src/core/result.py` | ⭐⭐⭐ |
| 0.5 | Agent 基类 | `src/core/base_agent.py` | ⭐⭐⭐ |
| 0.6 | 调度器（Orchestrator） | `src/core/orchestrator.py` | ⭐⭐ |
| 0.7 | 数据采集基类 | `src/core/data_collector.py` | ⭐⭐ |
| 0.8 | 日志系统 | `src/utils/logger.py` | ⭐⭐ |
| 0.9 | 依赖管理 | `requirements.txt` | ⭐⭐⭐ |
| 0.10 | 入口脚本（占位） | `scripts/run_analysis.py` | ⭐ |
| 0.11 | 单元测试框架 | `tests/` 目录 + 首个测试 | ⭐⭐ |
| 0.12 | `README.md` | 项目说明 | ⭐ |

---

## 2. 项目根目录与路径约定

### 2.1 根目录定位

所有模块通过 **相对于项目根目录** 的方式定位资源，核心手段：

```python
# 在 src/core/settings.py 中定义
from pathlib import Path

# 项目根目录 = 本文件向上3级 (settings.py -> config -> Market-prediction)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
```

这样无论项目被 clone 到哪个路径，都能正确找到资源。

### 2.2 路径使用规范

```python
# ✅ 正确：始终基于 PROJECT_ROOT
from config.settings import PROJECT_ROOT
data_dir = PROJECT_ROOT / "data"
env_path = PROJECT_ROOT / ".env"

# ❌ 错误：硬编码绝对路径
data_dir = "/Users/xxx/Documents/Market-prediction/data"

# ❌ 错误：依赖当前工作目录（用户可能从任意位置运行）
data_dir = Path("data")  # 取决于 os.getcwd()
```

### 2.3 Python 路径设置

推荐在项目根目录创建 `pyproject.toml`（最小化），让 `src` 下的包可以被正确导入：

```toml
# pyproject.toml
[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.backends._legacy:_Backend"

[project]
name = "market-prediction"
version = "0.1.0"
```

或者更简单的方式——在所有入口脚本开头加入：

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
```

> **Phase 0 决定**：采用 `pyproject.toml` + `pip install -e .`（可编辑安装）方式，这样 `from src.core.xxx import yyy` 在全项目任何位置都可用。

---

## 3. 技术决策记录

| 决策点 | 选择 | 理由 |
|--------|------|------|
| **LLM 调用方式** | 自建轻量 client，不依赖 LangChain | Phase 0 阶段追求简单可控，后续可迁移 |
| **异步支持** | 使用 `asyncio` + `aiohttp` | Agent 间需要并行执行，不能串行等待 |
| **配置管理** | `.env` + `python-dotenv` + dataclass | 简单、业界标准、GitHub友好 |
| **日志** | Python 标准 `logging` + `loguru` 风格封装 | 够用且零依赖 |
| **数据缓存** | JSON 文件缓存（Phase 0 不做，预留接口） | 避免过早引入数据库依赖 |
| **Python 版本** | >= 3.11 | 利用 `asyncio.TaskGroup` 等新特性 |
| **类型注解** | 全面使用 type hints | 提升代码可维护性，配合 mypy |
| **LLM Provider** | 优先支持 DeepSeek（便宜）+ OpenAI 兼容接口 | 性价比高，API 兼容 OpenAI 格式 |

---

## 4. 模块详细设计

---

### 4.1 配置系统 (`config/settings.py`)

#### 设计思路

- 使用 `.env` 文件存储敏感信息（API Keys），不提交到 Git
- 使用 `python-dotenv` 加载环境变量
- 使用 `dataclass` 封装配置，提供类型安全和 IDE 自动补全
- 单例模式：全局唯一配置实例

#### 类图

```
┌─────────────────────────────────────┐
│           Settings (dataclass)       │
├─────────────────────────────────────┤
│ + PROJECT_ROOT: Path                │
│ + LLM_API_KEY: str                  │
│ + LLM_BASE_URL: str                 │
│ + LLM_MODEL: str                    │
│ + LLM_TEMPERATURE: float            │
│ + LLM_MAX_TOKENS: int               │
│ + DATA_CACHE_DIR: Path              │
│ + OUTPUT_DIR: Path                  │
│ + LOG_LEVEL: str                    │
│ + AGENT_TIMEOUT: int                │
├─────────────────────────────────────┤
│ + from_env() -> Settings   (static) │
│ + get_data_dir() -> Path            │
│ + get_output_dir() -> Path          │
└─────────────────────────────────────┘
```

#### 核心代码签名

```python
# config/settings.py

from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv
import os

@dataclass
class Settings:
    """全局配置，从 .env 和环境变量加载"""
    
    # --- 路径 ---
    PROJECT_ROOT: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent)
    
    # --- LLM ---
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = "https://api.deepseek.com/v1"
    LLM_MODEL: str = "deepseek-chat"
    LLM_TEMPERATURE: float = 0.3
    LLM_MAX_TOKENS: int = 4096
    
    # --- 超时 ---
    AGENT_TIMEOUT: int = 120  # 秒
    
    # --- 日志 ---
    LOG_LEVEL: str = "INFO"
    
    @classmethod
    def from_env(cls) -> "Settings":
        """从 .env 文件和环境变量加载配置"""
        env_path = cls._find_env_file()
        if env_path.exists():
            load_dotenv(env_path)
        
        return cls(
            LLM_API_KEY=os.getenv("LLM_API_KEY", ""),
            LLM_BASE_URL=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"),
            LLM_MODEL=os.getenv("LLM_MODEL", "deepseek-chat"),
            LLM_TEMPERATURE=float(os.getenv("LLM_TEMPERATURE", "0.3")),
            LLM_MAX_TOKENS=int(os.getenv("LLM_MAX_TOKENS", "4096")),
            AGENT_TIMEOUT=int(os.getenv("AGENT_TIMEOUT", "120")),
            LOG_LEVEL=os.getenv("LOG_LEVEL", "INFO"),
        )
    
    @classmethod
    def _find_env_file(cls) -> Path:
        """查找 .env 文件：优先项目根目录"""
        return cls.PROJECT_ROOT / ".env"
    
    @property
    def data_dir(self) -> Path:
        return self.PROJECT_ROOT / "data"
    
    @property
    def output_dir(self) -> Path:
        return self.PROJECT_ROOT / "output"


# 全局单例（懒加载）
_settings: Settings | None = None

def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.from_env()
    return _settings
```

---

### 4.2 LLM 客户端 (`src/core/llm_client.py`)

#### 设计思路

- 封装对 LLM API 的调用，支持 OpenAI 兼容接口（DeepSeek、GPT-4o 等都兼容）
- 支持同步和异步两种调用方式
- 内置重试机制（网络波动、Rate Limit）
- 统一的错误处理，上层调用者不需要关心底层细节
- 预留多模型切换接口（不同 Agent 可用不同模型）

#### 类图

```
┌──────────────────────────────────────────────┐
│              LLMClient                        │
├──────────────────────────────────────────────┤
│ - api_key: str                                │
│ - base_url: str                               │
│ - model: str                                  │
│ - temperature: float                          │
│ - max_tokens: int                             │
│ - max_retries: int                            │
├──────────────────────────────────────────────┤
│ + chat(system: str, user: str) -> str         │
│ + async achat(system: str, user: str) -> str  │
│ - _build_messages(...) -> list[dict]          │
│ - _handle_error(...)                          │
│ + with_model(model: str) -> LLMClient         │
└──────────────────────────────────────────────┘
```

#### 核心代码签名

```python
# src/core/llm_client.py

import asyncio
import json
from dataclasses import dataclass
from typing import Optional
import aiohttp
import requests

@dataclass
class LLMResponse:
    """LLM 返回的统一结构"""
    content: str
    model: str
    usage: dict  # {"prompt_tokens": N, "completion_tokens": M}

class LLMClient:
    """LLM API 调用客户端（兼容 OpenAI 接口格式）"""
    
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com/v1",
        model: str = "deepseek-chat",
        temperature: float = 0.3,
        max_tokens: int = 4096,
        max_retries: int = 3,
    ):
        ...
    
    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: Optional[float] = None,
    ) -> LLMResponse:
        """同步调用 LLM"""
        ...
    
    async def achat(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: Optional[float] = None,
    ) -> LLMResponse:
        """异步调用 LLM"""
        ...
    
    def with_model(self, model: str) -> "LLMClient":
        """返回使用指定模型的客户端副本（用于不同Agent用不同模型）"""
        ...
    
    def _build_payload(self, system_prompt: str, user_prompt: str, temperature: float) -> dict:
        """构建请求 payload"""
        ...
    
    def _parse_response(self, data: dict) -> LLMResponse:
        """解析 API 返回"""
        ...

class LLMError(Exception):
    """LLM 调用异常"""
    pass

class LLMRateLimitError(LLMError):
    """Rate Limit 异常"""
    pass

class LLMTimeoutError(LLMError):
    """超时异常"""
    pass
```

#### 重试策略

```
调用失败 → 等待 (2^retry_count) 秒 → 重试 → 最多3次 → 仍失败则抛异常
Rate Limit → 读取 Retry-After 头 → 等待 → 重试
```

---

### 4.3 分析结果数据结构 (`src/core/result.py`)

#### 设计思路

- 统一的输出格式，所有 Agent 必须返回此结构
- 使用 `dataclass`，方便序列化/反序列化（JSON）
- 包含方向、幅度、置信度、推理过程、风险提示等核心字段
- 后续汇总 Agent 直接消费这些结构体

#### 数据结构

```python
# src/core/result.py

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional
from enum import Enum
import json


class Direction(str, Enum):
    BULLISH = "bullish"      # 看涨
    BEARISH = "bearish"      # 看跌
    NEUTRAL = "neutral"      # 震荡/中性


@dataclass
class Magnitude:
    """涨跌幅度区间"""
    min_pct: float   # 最小变化百分比，如 -5.0 表示 -5%
    max_pct: float   # 最大变化百分比，如 3.0 表示 +3%
    
    def __post_init__(self):
        if self.min_pct > self.max_pct:
            raise ValueError(f"min_pct ({self.min_pct}) must be <= max_pct ({self.max_pct})")
    
    @property
    def range_str(self) -> str:
        """人类可读的区间字符串"""
        if self.min_pct >= 0:
            return f"+{self.min_pct:.1f}% ~ +{self.max_pct:.1f}%"
        elif self.max_pct <= 0:
            return f"{self.min_pct:.1f}% ~ {self.max_pct:.1f}%"
        else:
            return f"{self.min_pct:.1f}% ~ +{self.max_pct:.1f}%"


@dataclass
class AnalysisResult:
    """所有 Agent 的统一分析结果"""
    
    # --- 元信息 ---
    agent_name: str              # Agent 名称，如 "技术面分析师"
    target: str                  # 分析标的，如 "0700.HK"
    timeframe: str               # 预测周期，如 "短期(1周)"
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    
    # --- 核心预测 ---
    direction: Direction = Direction.NEUTRAL
    magnitude: Optional[Magnitude] = None
    confidence: float = 0.0      # 0.0 ~ 1.0
    
    # --- 可解释性 ---
    reasoning: str = ""          # 推理过程（Markdown 格式）
    key_factors: list[str] = field(default_factory=list)   # 关键影响因素
    risks: list[str] = field(default_factory=list)         # 风险提示
    
    # --- 数据摘要 ---
    data_summary: dict = field(default_factory=dict)       # 使用的数据摘要
    
    def validate(self) -> list[str]:
        """校验结果完整性，返回错误列表（空列表表示合法）"""
        errors = []
        if not self.agent_name:
            errors.append("agent_name is required")
        if not self.target:
            errors.append("target is required")
        if self.confidence < 0 or self.confidence > 1:
            errors.append("confidence must be between 0 and 1")
        if self.direction != Direction.NEUTRAL and self.magnitude is None:
            errors.append("magnitude is required when direction is not neutral")
        if not self.reasoning:
            errors.append("reasoning is required for explainability")
        return errors
    
    def to_dict(self) -> dict:
        """序列化为字典"""
        d = asdict(self)
        d["direction"] = self.direction.value
        return d
    
    def to_json(self) -> str:
        """序列化为 JSON 字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
    
    @classmethod
    def from_dict(cls, data: dict) -> "AnalysisResult":
        """从字典反序列化"""
        data = data.copy()
        data["direction"] = Direction(data["direction"])
        if data.get("magnitude"):
            data["magnitude"] = Magnitude(**data["magnitude"])
        return cls(**data)


@dataclass
class FinalReport:
    """最终汇总报告"""
    
    # 元信息
    target: str
    timeframe: str
    generated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    
    # 综合预测
    direction: Direction = Direction.NEUTRAL
    magnitude: Optional[Magnitude] = None
    confidence: float = 0.0
    
    # 各 Agent 分析摘要
    agent_results: list[AnalysisResult] = field(default_factory=list)
    
    # 汇总分析
    summary: str = ""             # 综合分析文字
    key_risks: list[str] = field(default_factory=list)
    disagreements: list[str] = field(default_factory=list)  # Agent 间分歧点
    
    def to_markdown(self) -> str:
        """生成 Markdown 格式的最终报告"""
        ...  # Phase 1 实现
    
    def to_dict(self) -> dict:
        ...
```

---

### 4.4 Agent 基类 (`src/core/base_agent.py`)

#### 设计思路

- 所有分析 Agent 的抽象基类，定义统一接口
- 采用 **模板方法模式**：`run()` 是模板方法，子类只需实现 `gather_data()` 和 `analyze()`
- 内置耗时统计、异常处理、结果校验
- 通过组合方式持有 `LLMClient` 和 `Settings`

#### 类图

```
┌──────────────────────────────────────────────────┐
│              BaseAgent (ABC)                      │
├──────────────────────────────────────────────────┤
│ # name: str                                       │
│ # description: str                                │
│ # llm: LLMClient                                  │
│ # settings: Settings                              │
│ # logger: Logger                                  │
├──────────────────────────────────────────────────┤
│ + async run(target, timeframe) -> AnalysisResult  │  ← 模板方法
│ # async gather_data(target, timeframe) -> dict    │  ← 子类实现
│ # async analyze(data, context) -> AnalysisResult  │  ← 子类实现
│ # build_context(target, timeframe) -> dict        │
│ - _validate_result(result) -> None                │
│ - _get_system_prompt() -> str              (ABC)  │
│ + __repr__() -> str                               │
└──────────────────────────────────────────────────┘
```

#### 核心代码签名

```python
# src/core/base_agent.py

import time
import asyncio
from abc import ABC, abstractmethod
from typing import Optional
from logging import Logger

from .result import AnalysisResult, Direction, Magnitude
from .llm_client import LLMClient

class BaseAgent(ABC):
    """所有分析师的抽象基类
    
    子类只需实现:
    1. gather_data() — 数据采集
    2. _get_system_prompt() — 系统提示词
       （analyze() 有默认实现，基于 LLM 推理）
    """
    
    def __init__(
        self,
        name: str,
        description: str,
        llm: LLMClient,
        logger: Optional[Logger] = None,
    ):
        self.name = name
        self.description = description
        self.llm = llm
        self.logger = logger or self._default_logger()
    
    async def run(self, target: str, timeframe: str) -> AnalysisResult:
        """模板方法：完整的分析流程
        
        1. 采集数据 (gather_data)
        2. 分析推理 (analyze)  
        3. 校验结果
        4. 返回 AnalysisResult
        """
        self.logger.info(f"[{self.name}] 开始分析 {target} ({timeframe})")
        start_time = time.monotonic()
        
        try:
            # Step 1: 采集数据
            self.logger.debug(f"[{self.name}] 采集数据中...")
            data = await asyncio.wait_for(
                self.gather_data(target, timeframe),
                timeout=60  # 数据采集超时60秒
            )
            
            # Step 2: 分析推理
            self.logger.debug(f"[{self.name}] 分析推理中...")
            context = self.build_context(target, timeframe)
            result = await asyncio.wait_for(
                self.analyze(data, context),
                timeout=120  # LLM 推理超时120秒
            )
            
            # Step 3: 校验
            errors = result.validate()
            if errors:
                self.logger.warning(f"[{self.name}] 结果校验警告: {errors}")
            
            elapsed = time.monotonic() - start_time
            self.logger.info(
                f"[{self.name}] 分析完成 | "
                f"方向={result.direction.value} | "
                f"置信度={result.confidence:.0%} | "
                f"耗时={elapsed:.1f}s"
            )
            
            return result
            
        except asyncio.TimeoutError:
            self.logger.error(f"[{self.name}] 分析超时")
            return AnalysisResult(
                agent_name=self.name,
                target=target,
                timeframe=timeframe,
                direction=Direction.NEUTRAL,
                confidence=0.0,
                reasoning="分析超时，无法得出结论",
                risks=["数据获取或分析超时"],
            )
        except Exception as e:
            self.logger.error(f"[{self.name}] 分析异常: {e}", exc_info=True)
            return AnalysisResult(
                agent_name=self.name,
                target=target,
                timeframe=timeframe,
                direction=Direction.NEUTRAL,
                confidence=0.0,
                reasoning=f"分析过程异常: {str(e)}",
                risks=[str(e)],
            )
    
    @abstractmethod
    async def gather_data(self, target: str, timeframe: str) -> dict:
        """采集该 Agent 所需的原始数据
        
        Returns:
            dict: 原始数据，格式由各 Agent 自行定义
                  例如技术面: {"prices": [...], "indicators": {...}}
        """
        ...
    
    async def analyze(self, data: dict, context: dict) -> AnalysisResult:
        """基于数据进行分析推理（默认实现：调用 LLM）
        
        子类可以覆盖此方法实现自定义分析逻辑。
        默认实现将 data 和 context 拼接成 prompt 发给 LLM。
        """
        system_prompt = self._get_system_prompt()
        user_prompt = self._build_user_prompt(data, context)
        
        response = await self.llm.achat(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        
        return self._parse_llm_response(response.content, context)
    
    @abstractmethod
    def _get_system_prompt(self) -> str:
        """返回该 Agent 的系统提示词"""
        ...
    
    def build_context(self, target: str, timeframe: str) -> dict:
        """构建上下文信息"""
        return {
            "target": target,
            "timeframe": timeframe,
            "agent_name": self.name,
        }
    
    def _build_user_prompt(self, data: dict, context: dict) -> str:
        """构建用户提示词（将数据格式化后嵌入 prompt）
        
        默认实现：将 data 序列化为 JSON 嵌入 prompt。
        子类可覆盖以自定义格式。
        """
        import json
        data_str = json.dumps(data, ensure_ascii=False, indent=2)
        return f"""请基于以下数据进行分析：

## 分析标的
{context.get('target', 'N/A')}

## 预测周期
{context.get('timeframe', 'N/A')}

## 原始数据
{data_str}

请严格按指定的 JSON 格式输出分析结果。"""
    
    def _parse_llm_response(self, content: str, context: dict) -> AnalysisResult:
        """解析 LLM 返回的内容为 AnalysisResult
        
        默认尝试从 LLM 返回中提取 JSON。
        子类可覆盖以自定义解析逻辑。
        """
        import json
        import re
        
        # 尝试提取 JSON 块
        json_match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            # 尝试直接解析整个内容
            json_str = content
        
        try:
            data = json.loads(json_str)
            return AnalysisResult(
                agent_name=self.name,
                target=context.get("target", ""),
                timeframe=context.get("timeframe", ""),
                direction=Direction(data.get("direction", "neutral")),
                magnitude=Magnitude(
                    min_pct=float(data["magnitude"]["min_pct"]),
                    max_pct=float(data["magnitude"]["max_pct"]),
                ) if "magnitude" in data else None,
                confidence=float(data.get("confidence", 0.5)),
                reasoning=data.get("reasoning", content),
                key_factors=data.get("key_factors", []),
                risks=data.get("risks", []),
                data_summary=data.get("data_summary", {}),
            )
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            self.logger.warning(f"[{self.name}] JSON解析失败，使用原始文本: {e}")
            return AnalysisResult(
                agent_name=self.name,
                target=context.get("target", ""),
                timeframe=context.get("timeframe", ""),
                reasoning=content,
                key_factors=["LLM返回格式异常，请查看 reasoning 字段"],
            )
    
    def _default_logger(self) -> Logger:
        import logging
        return logging.getLogger(self.name)
    
    def __repr__(self) -> str:
        return f"<{self.name}: {self.description}>"
```

---

### 4.5 调度器 (`src/core/orchestrator.py`)

#### 设计思路

- 核心协调者：接收用户输入 → 分发任务给各 Agent → 收集结果 → 交给汇总 Agent
- 使用 `asyncio.TaskGroup`（Python 3.11+）并行执行所有 Agent
- 支持动态 Agent 注册机制
- 输出类型：既可以输出各 Agent 的原始结果列表，也可以驱动汇总 Agent 生成最终报告

#### 类图

```
┌──────────────────────────────────────────────────────┐
│                  Orchestrator                         │
├──────────────────────────────────────────────────────┤
│ - agents: dict[str, BaseAgent]                       │
│ - llm: LLMClient                                     │
│ - settings: Settings                                 │
│ - logger: Logger                                     │
├──────────────────────────────────────────────────────┤
│ + register(agent: BaseAgent) -> None                  │
│ + unregister(name: str) -> None                      │
│ + get_agent(name: str) -> BaseAgent                  │
│ + list_agents() -> list[str]                         │
│ + async run_all(target, timeframe,                    │
│           agent_names=None) -> list[AnalysisResult]  │
│ + async run_single(target, timeframe,                │
│           agent_name) -> AnalysisResult              │
└──────────────────────────────────────────────────────┘
```

#### 核心代码签名

```python
# src/core/orchestrator.py

import asyncio
import time
from typing import Optional
from logging import Logger

from .base_agent import BaseAgent
from .result import AnalysisResult

class Orchestrator:
    """调度器：管理 Agent 团队，协调分析任务"""
    
    def __init__(self, logger: Optional[Logger] = None):
        self._agents: dict[str, BaseAgent] = {}
        self.logger = logger or logging.getLogger("Orchestrator")
    
    def register(self, agent: BaseAgent) -> None:
        """注册一个 Agent"""
        if agent.name in self._agents:
            self.logger.warning(f"Agent '{agent.name}' 已存在，将被覆盖")
        self._agents[agent.name] = agent
        self.logger.info(f"Agent 已注册: {agent.name}")
    
    def unregister(self, name: str) -> None:
        """移除一个 Agent"""
        if name in self._agents:
            del self._agents[name]
            self.logger.info(f"Agent 已移除: {name}")
    
    def list_agents(self) -> list[str]:
        """列出所有已注册的 Agent"""
        return list(self._agents.keys())
    
    async def run_all(
        self,
        target: str,
        timeframe: str,
        agent_names: Optional[list[str]] = None,
    ) -> list[AnalysisResult]:
        """并行运行指定的 Agent（或全部）
        
        Args:
            target: 分析标的
            timeframe: 预测周期
            agent_names: 要运行的 Agent 名称列表，None 表示运行全部
        
        Returns:
            所有 Agent 的分析结果列表
        """
        names = agent_names or list(self._agents.keys())
        agents_to_run = [self._agents[name] for name in names if name in self._agents]
        
        if not agents_to_run:
            self.logger.warning("没有可用的 Agent")
            return []
        
        self.logger.info(
            f"开始并行分析 | 标的={target} | 周期={timeframe} | "
            f"Agent={[a.name for a in agents_to_run]}"
        )
        start_time = time.monotonic()
        
        # Python 3.11+ TaskGroup：任一任务失败不影响其他
        async with asyncio.TaskGroup() as tg:
            tasks = {
                agent.name: tg.create_task(agent.run(target, timeframe))
                for agent in agents_to_run
            }
        
        # 收集结果（TaskGroup 确保所有任务完成或异常后才退出）
        results = []
        for name, task in tasks.items():
            try:
                result = task.result()
                results.append(result)
            except Exception as e:
                self.logger.error(f"Agent '{name}' 执行异常: {e}")
        
        elapsed = time.monotonic() - start_time
        self.logger.info(f"所有 Agent 分析完成 | 耗时={elapsed:.1f}s | 成功={len(results)}个")
        
        return results
    
    async def run_single(
        self, target: str, timeframe: str, agent_name: str
    ) -> Optional[AnalysisResult]:
        """运行单个 Agent"""
        agent = self._agents.get(agent_name)
        if not agent:
            self.logger.error(f"Agent '{agent_name}' 未注册")
            return None
        return await agent.run(target, timeframe)
```

---

### 4.6 日志系统 (`src/utils/logger.py`)

#### 设计思路

- 统一日志格式，包含时间戳、模块名、日志级别
- 同时输出到控制台和文件
- 通过 `LOG_LEVEL` 环境变量控制级别
- 每个模块使用自己的 logger（`logging.getLogger(__name__)`）

#### 核心代码签名

```python
# src/utils/logger.py

import logging
import sys
from pathlib import Path
from datetime import datetime

def setup_logging(
    log_level: str = "INFO",
    log_dir: Path | None = None,
    log_to_file: bool = True,
) -> None:
    """初始化全局日志配置
    
    Args:
        log_level: 日志级别
        log_dir: 日志文件目录
        log_to_file: 是否写入文件
    """
    ...

def get_logger(name: str) -> logging.Logger:
    """获取指定名称的 logger"""
    return logging.getLogger(name)
```

---

### 4.7 数据采集基类 (`src/core/data_collector.py`)

#### 设计思路

- 虽然 Phase 0 不需要真实数据采集，但预留基类接口
- 内置缓存机制接口（Phase 3 实现）
- 统一的数据源错误处理

```python
# src/core/data_collector.py

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional
import json
import hashlib
from datetime import datetime, timedelta

class BaseDataCollector(ABC):
    """数据采集基类"""
    
    def __init__(self, cache_dir: Optional[Path] = None, cache_ttl_hours: int = 24):
        self.cache_dir = cache_dir
        self.cache_ttl_hours = cache_ttl_hours
    
    @abstractmethod
    async def fetch(self, target: str, **kwargs) -> dict:
        """获取数据（子类实现）"""
        ...
    
    def _cache_key(self, target: str, **kwargs) -> str:
        """生成缓存键"""
        raw = f"{target}_{kwargs}"
        return hashlib.md5(raw.encode()).hexdigest()
    
    def _read_cache(self, cache_key: str) -> Optional[dict]:
        """读取缓存（Phase 3 实现）"""
        # TODO: Phase 3
        return None
    
    def _write_cache(self, cache_key: str, data: dict) -> None:
        """写入缓存（Phase 3 实现）"""
        # TODO: Phase 3
        pass
```

---

## 5. 文件清单与创建顺序

按依赖关系排列，数字越小越先创建：

```
Phase 0 文件创建顺序
═══════════════════════════════════════════════════

[先决条件] 目录结构
  ├── Market-prediction/
  ├── config/__init__.py
  ├── src/__init__.py
  ├── src/core/__init__.py
  ├── src/agents/__init__.py
  ├── src/data/__init__.py
  ├── src/prompts/__init__.py
  ├── src/utils/__init__.py
  ├── tests/__init__.py
  ├── output/.gitkeep
  └── scripts/

[1] .env.example          — 配置模板（无依赖）
[2] .gitignore            — Git忽略规则（无依赖）
[3] pyproject.toml        — 项目元数据（无依赖）
[4] requirements.txt      — 依赖清单（无依赖）

[5] config/settings.py    — 配置系统（依赖: python-dotenv）
[6] src/utils/logger.py   — 日志系统（依赖: settings）

[7] src/core/result.py    — 数据结构（无内部依赖）
[8] src/core/llm_client.py — LLM客户端（依赖: settings, logger）

[9] src/core/data_collector.py — 数据采集基类（依赖: 无）

[10] src/core/base_agent.py — Agent基类（依赖: result, llm_client, logger）

[11] src/core/orchestrator.py — 调度器（依赖: base_agent, result）

[12] scripts/run_analysis.py  — 入口脚本占位（依赖: orchestrator）

[13] tests/test_result.py     — 第一个单元测试
[14] README.md               — 项目说明
```

---

## 6. 配置模板文件

### 6.1 `.env.example`（提交到 Git）

```bash
# ============================================================
# Market Prediction — 环境变量配置模板
# 复制此文件为 .env 并填入你的 API Keys
# .env 文件不会被提交到 Git
# ============================================================

# --- LLM 配置 ---
# 支持 OpenAI 兼容接口（DeepSeek, OpenAI, 智谱, 通义千问等）
LLM_API_KEY=your-api-key-here
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
LLM_TEMPERATURE=0.3
LLM_MAX_TOKENS=4096

# --- 超时配置 ---
AGENT_TIMEOUT=120

# --- 日志级别 ---
# DEBUG | INFO | WARNING | ERROR
LOG_LEVEL=INFO
```

### 6.2 `.gitignore`

```gitignore
# 环境变量（含 API Keys）
.env

# Python
__pycache__/
*.py[cod]
*.egg-info/
dist/
build/
.eggs/

# 虚拟环境
venv/
.venv/
env/

# IDE
.vscode/
.idea/
*.swp
*.swo
*~

# 系统文件
.DS_Store
Thumbs.db

# 输出目录（分析报告）
output/*.md
output/*.json
!output/.gitkeep

# 日志
logs/
*.log

# 缓存
data/cache/
.cache/

# Jupyter
.ipynb_checkpoints/
*.ipynb
```

### 6.3 `requirements.txt`

```
# ============================================================
# Market Prediction — Python 依赖
# 安装: pip install -r requirements.txt
# ============================================================

# 环境配置
python-dotenv>=1.0.0

# HTTP & 异步
aiohttp>=3.9.0
requests>=2.31.0

# 数据处理
pandas>=2.0.0

# 金融数据（Phase 1+ 使用）
# akshare>=1.12.0       # 取消注释以启用
# yfinance>=0.2.30      # 取消注释以启用

# LLM 框架（Phase 2+ 可选，Phase 0 不需要）
# langchain>=0.2.0

# 开发工具
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

### 6.4 `pyproject.toml`

```toml
[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.backends._legacy:_Backend"

[project]
name = "market-prediction"
version = "0.1.0"
description = "AI Agent 团队驱动的市场预测系统"
readme = "README.md"
requires-python = ">=3.11"
dependencies = [
    "python-dotenv>=1.0.0",
    "aiohttp>=3.9.0",
    "requests>=2.31.0",
    "pandas>=2.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
]

[tool.setuptools.packages.find]
where = ["."]
include = ["src*", "config*"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

---

## 7. 测试策略

### 7.1 Phase 0 测试范围

| 测试对象 | 测试内容 | 类型 |
|---------|---------|------|
| `AnalysisResult` | 校验逻辑、序列化/反序列化 | 单元测试 |
| `Magnitude` | 边界情况（min>max等） | 单元测试 |
| `LLMClient` | Mock API 响应，重试逻辑 | 单元测试 |
| `BaseAgent` | 超时处理、异常处理、结果校验 | 单元测试 |
| `Orchestrator` | Agent 注册/移除、并行执行 | 集成测试 |
| `Settings` | 环境变量加载、默认值 | 单元测试 |

### 7.2 第一个测试文件 `tests/test_result.py`

```python
"""测试 AnalysisResult 和 Magnitude 数据结构"""

import pytest
from src.core.result import Direction, Magnitude, AnalysisResult


class TestMagnitude:
    def test_valid_range(self):
        m = Magnitude(min_pct=-5.0, max_pct=3.0)
        assert m.range_str == "-5.0% ~ +3.0%"
    
    def test_all_positive(self):
        m = Magnitude(min_pct=1.0, max_pct=5.0)
        assert m.range_str == "+1.0% ~ +5.0%"
    
    def test_invalid_range(self):
        with pytest.raises(ValueError):
            Magnitude(min_pct=5.0, max_pct=3.0)


class TestAnalysisResult:
    def test_validation_success(self):
        result = AnalysisResult(
            agent_name="测试Agent",
            target="000001.SZ",
            timeframe="短期",
            direction=Direction.BULLISH,
            magnitude=Magnitude(1.0, 5.0),
            confidence=0.7,
            reasoning="测试推理过程",
        )
        assert result.validate() == []
    
    def test_validation_missing_fields(self):
        result = AnalysisResult(
            agent_name="",
            target="",
            timeframe="",
        )
        errors = result.validate()
        assert len(errors) > 0
    
    def test_serialization_roundtrip(self):
        original = AnalysisResult(
            agent_name="测试Agent",
            target="000001.SZ",
            timeframe="短期",
            direction=Direction.BULLISH,
            magnitude=Magnitude(1.0, 5.0),
            confidence=0.7,
            reasoning="测试",
        )
        json_str = original.to_json()
        restored = AnalysisResult.from_dict(
            __import__("json").loads(json_str)
        )
        assert restored.direction == original.direction
        assert restored.confidence == original.confidence
```

---

## 8. Phase 0 完成标准

Phase 0 完成的标志：**以下命令能成功运行且不报错**。

```bash
# 1. 克隆项目后能一键安装
git clone <repo-url>
cd Market-prediction
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 2. 配置环境
cp .env.example .env
# 编辑 .env 填入 API Key

# 3. 运行测试全部通过
pytest tests/ -v

# 4. 运行占位脚本不报错
python scripts/run_analysis.py --help

# 5. 导入核心模块不报错
python -c "from src.core.result import AnalysisResult; print('OK')"
python -c "from src.core.base_agent import BaseAgent; print('OK')"
python -c "from src.core.orchestrator import Orchestrator; print('OK')"
python -c "from config.settings import get_settings; print(get_settings().LLM_MODEL)"
```

### 验收检查表

```
☐ 目录结构完整，所有 __init__.py 就位
☐ .env.example 和 .gitignore 内容正确
☐ requirements.txt 可以正常安装
☐ pip install -e . 不报错
☐ Settings.from_env() 正确加载 .env 中的配置
☐ LLMClient 可以成功调用 LLM API（同步+异步）
☐ AnalysisResult 校验逻辑正确
☐ BaseAgent 的模板方法 run() 流程正确（超时/异常处理）
☐ Orchestrator 可以注册 Agent 并并行调用
☐ 日志正常输出到控制台
☐ 至少 3 个单元测试通过
☐ README.md 包含项目简介和快速开始指南
```

---

## 附录 A: 依赖关系图

```
                    ┌──────────────┐
                    │  .env / 环境  │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │  settings.py │
                    └──────┬───────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
     ┌────────▼───┐ ┌─────▼──────┐ ┌───▼──────────┐
     │ logger.py  │ │llm_client  │ │ data_collector│
     └────────┬───┘ └─────┬──────┘ └───┬──────────┘
              │            │            │
              └────────────┼────────────┘
                           │
                    ┌──────▼───────┐
                    │  result.py   │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │ base_agent   │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │ orchestrator │
                    └──────────────┘
```

---

> 📌 **Phase 0 是地基**：这个阶段写出的代码不涉及任何金融逻辑，但决定了整个项目的代码质量和可维护性。确认设计后，就可以开始逐文件实现了。
