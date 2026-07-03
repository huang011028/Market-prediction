# Phase 1: MVP 最小可行版本 — 实现设计文档

> **目标**：实现技术面分析师 + 新闻分析师 + 汇总分析师，打通"输入股票代码 → 输出预测报告"的完整链路。
>
> **原则**：每个 Agent 独立开发、独立测试；先跑通再优化；真实数据取代 Mock。

---

## 目录

1. [Phase 1 目标清单](#1-phase-1-目标清单)
2. [整体数据流](#2-整体数据流)
3. [新增依赖](#3-新增依赖)
4. [模块详细设计](#4-模块详细设计)
   - [4.1 股价数据获取 (`src/data/price_fetcher.py`)](#41-股价数据获取-srcdataprice_fetcherpy)
   - [4.2 新闻数据获取 (`src/data/news_fetcher.py`)](#42-新闻数据获取-srcdatanews_fetcherpy)
   - [4.3 技术面分析师 (`src/agents/technical_analyst.py`)](#43-技术面分析师-srcagentstechnical_analystpy)
   - [4.4 新闻分析师 (`src/agents/news_analyst.py`)](#44-新闻分析师-srcagentsnews_analystpy)
   - [4.5 汇总分析师 (`src/agents/aggregator.py`)](#45-汇总分析师-srcagentsaggregatorpy)
   - [4.6 Prompt 模板](#46-prompt-模板)
   - [4.7 主入口脚本升级](#47-主入口脚本升级)
5. [文件清单与创建顺序](#5-文件清单与创建顺序)
6. [测试策略](#6-测试策略)
7. [Phase 1 完成标准](#7-phase-1-完成标准)

---

## 1. Phase 1 目标清单

| # | 任务 | 产出文件 | 优先级 |
|---|------|---------|--------|
| 1.1 | 股价数据获取器 | `src/data/price_fetcher.py` | ⭐⭐⭐ |
| 1.2 | 新闻数据获取器 | `src/data/news_fetcher.py` | ⭐⭐⭐ |
| 1.3 | 技术面分析师 Prompt | `src/prompts/technical_prompts.py` | ⭐⭐⭐ |
| 1.4 | 新闻分析师 Prompt | `src/prompts/news_prompts.py` | ⭐⭐⭐ |
| 1.5 | 汇总分析师 Prompt | `src/prompts/aggregator_prompts.py` | ⭐⭐⭐ |
| 1.6 | 技术面分析师 Agent | `src/agents/technical_analyst.py` | ⭐⭐⭐ |
| 1.7 | 新闻分析师 Agent | `src/agents/news_analyst.py` | ⭐⭐⭐ |
| 1.8 | 汇总分析师 Agent | `src/agents/aggregator.py` | ⭐⭐⭐ |
| 1.9 | 主入口脚本升级 | `scripts/run_analysis.py` | ⭐⭐ |
| 1.10 | 单元测试 | `tests/` 目录扩展 | ⭐⭐ |
| 1.11 | 端到端验证 | 完整流程跑通 | ⭐⭐⭐ |

---

## 2. 整体数据流

```
                          ┌─────────────────────┐
                          │    用户输入           │
                          │  0700.HK / 短期(1周)  │
                          └──────────┬──────────┘
                                     │
                                     ▼
                          ┌─────────────────────┐
                          │    Orchestrator      │
                          │   调度 3 个 Agent     │
                          └──────────┬──────────┘
                                     │
              ┌──────────────────────┼──────────────────────┐
              │                      │                      │
              ▼                      ▼                      ▼
   ┌────────────────────┐ ┌────────────────────┐ ┌────────────────────┐
   │ 📊 技术面分析师     │ │ 📰 新闻分析师       │ │ 🎯 汇总分析师       │
   │                    │ │                    │ │                    │
   │ 1. 取K线+技术指标   │ │ 1. 搜索相关新闻     │ │ 1. 接收前两个结果   │
   │ 2. LLM分析趋势     │ │ 2. LLM分析情绪     │ │ 2. LLM综合研判     │
   │ 3. 输出方向+幅度   │ │ 3. 输出方向+幅度   │ │ 3. 生成 FinalReport │
   └────────┬───────────┘ └────────┬───────────┘ └────────┬───────────┘
            │                      │                      │
            │  AnalysisResult      │  AnalysisResult      │  FinalReport
            └──────────────────────┼──────────────────────┘
                                   │
                                   ▼
                          ┌─────────────────────┐
                          │   输出 Markdown 报告  │
                          │       + JSON 文件     │
                          └─────────────────────┘
```

> **注意**：技术面和新闻 Agent 并行执行（各自取数据+分析），汇总 Agent 等前两个完成后才启动。

---

## 3. 新增依赖

```
# requirements.txt 新增

# 金融数据
akshare>=1.12.0        # A股/港股行情 + 新闻
yfinance>=0.2.30       # 美股行情（备选）

# 技术指标计算
ta>=0.11.0             # 技术分析库（MACD, RSI, 布林带等）
# 或者直接用 pandas 手算，避免额外依赖
```

### 数据源选型

| 市场 | 行情数据 | 新闻数据 |
|------|---------|---------|
| **A股** | akshare `stock_zh_a_hist` | akshare `stock_news_em`（东方财富新闻） |
| **港股** | akshare `stock_hk_hist` / yfinance | akshare + Google News RSS |
| **美股** | yfinance | yfinance `.news` + NewsAPI |
| **加密货币** | Binance API（Phase 4） | 暂不考虑 |

> **Phase 1 策略**：优先支持 A 股 + 港股，因为 akshare 对这两个市场的覆盖最完整且免费。

---

## 4. 模块详细设计

---

### 4.1 股价数据获取 (`src/data/price_fetcher.py`)

#### 职责

获取指定标的的历史 K 线数据，计算常用技术指标，打包为 Agent 可直接消费的字典。

#### 接口设计

```python
# src/data/price_fetcher.py

from dataclasses import dataclass
import pandas as pd

@dataclass
class PriceData:
    """股价数据封装"""
    symbol: str
    market: str          # "A" / "HK" / "US"
    timeframe: str       # 数据时间范围描述
    ohlcv: pd.DataFrame  # 原始K线 DataFrame
    indicators: dict     # 技术指标字典

class PriceFetcher:
    """股价 + 技术指标数据获取器"""
    
    def __init__(self, cache_dir: Path | None = None):
        ...
    
    async def fetch(
        self,
        symbol: str,
        period: str = "3mo",  # 1mo/3mo/6mo/1y
    ) -> PriceData:
        """
        获取股价数据 + 计算技术指标
        
        Args:
            symbol: 股票代码，如 "000001"（A股）/ "0700"（港股）
            period: 数据周期
        
        自动识别市场：
          - 纯数字 → A股
          - 纯数字+".HK" → 港股  
          - 含字母 → 美股
        """
        ...
    
    def _identify_market(self, symbol: str) -> str:
        """识别股票所属市场"""
        ...
    
    def _fetch_a_share(self, symbol: str, period: str) -> pd.DataFrame:
        """获取 A 股日K线"""
        ...
    
    def _fetch_hk_share(self, symbol: str, period: str) -> pd.DataFrame:
        """获取港股日K线"""
        ...
    
    def _compute_indicators(self, df: pd.DataFrame) -> dict:
        """
        计算常用技术指标
        
        包含：
        - 均线: MA5, MA10, MA20, MA60
        - MACD: DIF, DEA, 柱状线
        - RSI(14)
        - 布林带: 上轨/中轨/下轨
        - 成交量均线: VOL_MA5, VOL_MA20
        - 近期支撑/阻力位
        """
        ...
```

#### 市场识别逻辑

```python
def _identify_market(self, symbol: str) -> str:
    symbol = symbol.upper().strip()
    
    # 港股：含 .HK 后缀 或 4位数字（港股代码）
    if ".HK" in symbol:
        return "HK"
    
    # A股：6位纯数字
    if symbol.isdigit() and len(symbol) == 6:
        return "A"
    
    # 美股：含字母（AAPL, TSLA 等）
    if any(c.isalpha() for c in symbol):
        return "US"
    
    raise ValueError(f"无法识别股票代码: {symbol}")
```

#### 技术指标计算（pandas 手算，避免引入 ta 库）

```python
def _compute_indicators(self, df: pd.DataFrame) -> dict:
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]
    
    indicators = {}
    
    # --- 均线 ---
    for period in [5, 10, 20, 60]:
        ma = close.rolling(window=period).mean()
        indicators[f"MA{period}"] = round(ma.iloc[-1], 2) if not ma.empty else None
        # 均线方向
        if len(ma) >= 2:
            indicators[f"MA{period}_trend"] = "up" if ma.iloc[-1] > ma.iloc[-2] else "down"
    
    # --- MACD (12, 26, 9) ---
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    macd_bar = 2 * (dif - dea)
    
    indicators["MACD_DIF"] = round(dif.iloc[-1], 4)
    indicators["MACD_DEA"] = round(dea.iloc[-1], 4)
    indicators["MACD_BAR"] = round(macd_bar.iloc[-1], 4)
    indicators["MACD_signal"] = "golden_cross" if dif.iloc[-1] > dea.iloc[-1] and dif.iloc[-2] <= dea.iloc[-2] else (
        "death_cross" if dif.iloc[-1] < dea.iloc[-1] and dif.iloc[-2] >= dea.iloc[-2] else "holding"
    )
    
    # --- RSI(14) ---
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=14).mean()
    avg_loss = loss.rolling(window=14).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    indicators["RSI"] = round(rsi.iloc[-1], 2)
    
    # --- 布林带 (20, 2) ---
    ma20 = close.rolling(window=20).mean()
    std20 = close.rolling(window=20).std()
    indicators["BOLL_upper"] = round(ma20.iloc[-1] + 2 * std20.iloc[-1], 2)
    indicators["BOLL_mid"] = round(ma20.iloc[-1], 2)
    indicators["BOLL_lower"] = round(ma20.iloc[-1] - 2 * std20.iloc[-1], 2)
    
    # --- 成交量 ---
    vol_ma5 = volume.rolling(window=5).mean()
    indicators["VOL_ratio"] = round(volume.iloc[-1] / vol_ma5.iloc[-1], 2) if vol_ma5.iloc[-1] != 0 else 1.0
    
    # --- 近期价格特征 ---
    recent = df.tail(20)
    indicators["price_current"] = round(close.iloc[-1], 2)
    indicators["price_20d_high"] = round(recent["high"].max(), 2)
    indicators["price_20d_low"] = round(recent["low"].min(), 2)
    indicators["price_change_5d"] = round((close.iloc[-1] / close.iloc[-6] - 1) * 100, 2) if len(close) >= 6 else 0
    indicators["price_change_20d"] = round((close.iloc[-1] / close.iloc[-21] - 1) * 100, 2) if len(close) >= 21 else 0
    
    return indicators
```

#### 输出给 Agent 的数据格式

```python
# PriceFetcher.fetch() 返回的 dict 示例
{
    "symbol": "000001",
    "market": "A",
    "data_period": "近3个月",
    "trading_days": 58,
    
    "price_summary": {
        "latest_close": 12.50,
        "period_high": 13.80,
        "period_low": 10.20,
        "change_5d_pct": 3.2,
        "change_20d_pct": -1.5,
    },
    
    "indicators": {
        "MA5": 12.35, "MA5_trend": "up",
        "MA10": 12.20, "MA10_trend": "up",
        "MA20": 12.10, "MA20_trend": "down",
        "MA60": 12.80, "MA60_trend": "down",
        "MACD_DIF": 0.15, "MACD_DEA": 0.08,
        "MACD_signal": "golden_cross",
        "RSI": 58.5,
        "BOLL_upper": 13.20, "BOLL_mid": 12.10, "BOLL_lower": 11.00,
        "VOL_ratio": 1.35,
    },
    
    "patterns": {
        "ma_arrangement": "MA5>MA10>MA20 短期多头",
        "price_vs_ma": "价格站上 MA5/MA10/MA20，低于 MA60",
        "rsi_zone": "中性偏强(50-70)",
        "boll_position": "价格在中轨与上轨之间运行",
    },
    
    # 最近 10 个交易日的收盘价（用于 LLM 感受趋势斜率）
    "recent_closes": [12.10, 12.25, 12.18, 12.40, 12.55, ...],
}
```

---

### 4.2 新闻数据获取 (`src/data/news_fetcher.py`)

#### 职责

搜索标的相关的近期新闻/公告/研报，返回结构化的新闻列表。

#### 接口设计

```python
# src/data/news_fetcher.py

from dataclasses import dataclass
from datetime import datetime

@dataclass
class NewsItem:
    """单条新闻"""
    title: str
    summary: str        # 摘要（前200字）
    source: str         # 来源
    publish_time: str   # 发布时间
    url: str            # 原文链接

class NewsFetcher:
    """新闻数据获取器"""
    
    def __init__(self, max_items: int = 20):
        self.max_items = max_items
    
    async def fetch(
        self, 
        symbol: str, 
        market: str = "A",
        days: int = 14,
    ) -> list[NewsItem]:
        """
        获取标的相关的近期新闻
        
        实现策略（从易到难）：
        1. akshare.stock_news_em(symbol)  — 东方财富个股新闻（A股优先）
        2. Google News RSS 搜索          — 通用方案
        3. yfinance .news 属性           — 美股
        """
        ...
    
    async def search_company_news(
        self, 
        company_name: str, 
        days: int = 14
    ) -> list[NewsItem]:
        """通用新闻搜索（按公司名）"""
        ...
```

#### 数据源策略

由于免费且稳定的新闻 API 较少，采用**多层降级**策略：

```
第 1 层: akshare 个股新闻（A股/港股）
    ↓ 失败或无结果
第 2 层: Google News RSS 搜索（需翻墙或已废弃，降级）
    ↓ 失败
第 3 层: 让 LLM Agent 基于自身知识 + 搜索能力分析
         （DeepSeek 暂不支持联网搜索，降级）
    ↓ 失败
第 4 层: 返回空列表 + 标注"新闻数据暂时不可用"
```

> **Phase 1 实际策略**：优先 akshare 个股新闻。如果 akshare 不可用或数据为空，新闻 Agent 会基于自身知识（训练数据中的信息）进行分析，并在结果中标注"基于知识库而非实时新闻"。

#### 输出给 Agent 的数据格式

```python
# NewsFetcher.fetch() 返回的 dict 示例
{
    "symbol": "000001",
    "company_name": "平安银行",
    "news_count": 12,
    "date_range": "2026-06-18 ~ 2026-07-02",
    "news_items": [
        {
            "title": "平安银行发布2026年中期业绩预告...",
            "summary": "预计净利润同比增长10%-15%...",
            "source": "东方财富",
            "time": "2026-07-01",
            "url": "https://..."
        },
        ...
    ],
    "news_source": "akshare",  # "akshare" / "search" / "knowledge_base" / "unavailable"
}
```

---

### 4.3 技术面分析师 (`src/agents/technical_analyst.py`)

#### 职责

获取真实 K 线数据 → 计算技术指标 → LLM 分析趋势 → 输出方向+幅度。

```python
# src/agents/technical_analyst.py

from src.core.base_agent import BaseAgent
from src.core.result import AnalysisResult
from src.data.price_fetcher import PriceFetcher
from src.prompts.technical_prompts import TECHNICAL_SYSTEM_PROMPT

class TechnicalAnalyst(BaseAgent):
    """技术面 / 近期股价分析师"""
    
    def __init__(self, llm: LLMClient):
        super().__init__(
            name="技术面分析师",
            description="基于K线形态、均线系统、MACD、RSI等技术指标分析短期趋势",
            llm=llm,
        )
        self.price_fetcher = PriceFetcher()
    
    async def gather_data(self, target: str, timeframe: str) -> dict:
        """获取股价数据 + 计算技术指标"""
        # 根据 timeframe 决定取多长历史数据
        period_map = {
            "短期": "3mo",
            "中期": "6mo", 
            "长期": "1y",
        }
        
        # 模糊匹配 timeframe 中的关键词
        period = "3mo"
        for key, val in period_map.items():
            if key in timeframe:
                period = val
                break
        
        price_data = await self.price_fetcher.fetch(target, period)
        return price_data  # 已经是 dict 格式
    
    def _get_system_prompt(self) -> str:
        return TECHNICAL_SYSTEM_PROMPT
```

---

### 4.4 新闻分析师 (`src/agents/news_analyst.py`)

#### 职责

获取近期新闻 → LLM 分析情绪和影响 → 输出方向+幅度。

```python
# src/agents/news_analyst.py

class NewsAnalyst(BaseAgent):
    """最新新闻分析师"""
    
    def __init__(self, llm: LLMClient):
        super().__init__(
            name="新闻分析师",
            description="分析近期相关新闻的情绪方向、重大事件影响程度",
            llm=llm,
        )
        self.news_fetcher = NewsFetcher()
    
    async def gather_data(self, target: str, timeframe: str) -> dict:
        days_map = {"短期": 7, "中期": 30, "长期": 90}
        days = 7
        for key, val in days_map.items():
            if key in timeframe:
                days = val
                break
        
        news_data = await self.news_fetcher.fetch(target, days=days)
        return news_data
    
    def _get_system_prompt(self) -> str:
        return NEWS_SYSTEM_PROMPT
```

---

### 4.5 汇总分析师 (`src/agents/aggregator.py`)

#### 职责

这是 Phase 1 最关键的 Agent——它不获取外部数据，而是接收前两个 Agent 的分析结果，通过 LLM 进行综合分析。

#### 特殊设计：不继承 `BaseAgent`

汇总 Agent 的工作方式不同：
- **不需要 `gather_data()`**：它的"数据"是其他 Agent 的结果
- **不在 Orchestrator 中并行执行**：必须等技术面和新闻完成后才能运行
- **输出 `FinalReport` 而非 `AnalysisResult`**：生成完整的最终报告

```python
# src/agents/aggregator.py

from src.core.llm_client import LLMClient
from src.core.result import AnalysisResult, FinalReport, Direction, Magnitude
from src.prompts.aggregator_prompts import AGGREGATOR_SYSTEM_PROMPT

class Aggregator:
    """最终汇总分析师
    
    不继承 BaseAgent，因为它：
    1. 不需要获取外部数据
    2. 输入是其他 Agent 的 AnalysisResult
    3. 输出是 FinalReport（而非 AnalysisResult）
    """
    
    def __init__(self, llm: LLMClient):
        self.name = "汇总分析师"
        self.description = "综合各方分析结果，给出最终预测并生成完整报告"
        self.llm = llm
    
    async def aggregate(
        self,
        target: str,
        timeframe: str,
        agent_results: list[AnalysisResult],
    ) -> FinalReport:
        """
        综合所有 Agent 结果，生成最终报告
        
        Args:
            target: 分析标的
            timeframe: 预测周期
            agent_results: 各 Agent 的分析结果列表
        
        Returns:
            FinalReport: 包含综合预测的完整报告
        """
        # 1. 构建给 LLM 的上下文
        context = self._build_context(target, timeframe, agent_results)
        
        # 2. 调用 LLM 进行综合分析
        response = await self.llm.achat(
            system_prompt=AGGREGATOR_SYSTEM_PROMPT,
            user_prompt=context,
        )
        
        # 3. 解析 LLM 返回 → FinalReport
        return self._parse_response(response.content, target, timeframe, agent_results)
    
    def _build_context(
        self, 
        target: str, 
        timeframe: str, 
        results: list[AnalysisResult]
    ) -> str:
        """构建综合分析上下文"""
        parts = []
        parts.append(f"## 分析标的: {target}")
        parts.append(f"## 预测周期: {timeframe}")
        parts.append(f"\n## 各维度分析结果:\n")
        
        for r in results:
            parts.append(f"### {r.agent_name}")
            parts.append(f"- 方向: {r.direction.value}")
            if r.magnitude:
                parts.append(f"- 幅度: {r.magnitude.range_str}")
            parts.append(f"- 置信度: {r.confidence:.0%}")
            parts.append(f"- 推理: {r.reasoning}")
            if r.key_factors:
                parts.append(f"- 关键因素: {', '.join(r.key_factors[:5])}")
            if r.risks:
                parts.append(f"- 风险: {', '.join(r.risks[:5])}")
            parts.append("")
        
        return "\n".join(parts)
    
    def _parse_response(
        self, content: str, target: str, timeframe: str,
        agent_results: list[AnalysisResult],
    ) -> FinalReport:
        """解析 LLM 返回的综合分析"""
        # 从 LLM 返回中提取 JSON
        import json, re
        json_match = re.search(r'```(?:json)?\s*(.*?)\s*```', content, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            json_str = content
        
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            # 解析失败，用默认值
            data = {}
        
        direction_raw = data.get("direction", "neutral")
        try:
            direction = Direction(direction_raw)
        except ValueError:
            direction = Direction.NEUTRAL
        
        magnitude = None
        if "magnitude" in data and data["magnitude"]:
            magnitude = Magnitude(
                min_pct=float(data["magnitude"].get("min_pct", 0)),
                max_pct=float(data["magnitude"].get("max_pct", 0)),
            )
        
        return FinalReport(
            target=target,
            timeframe=timeframe,
            direction=direction,
            magnitude=magnitude,
            confidence=float(data.get("confidence", 0.5)),
            agent_results=agent_results,
            summary=data.get("summary", content[:500]),
            key_risks=data.get("key_risks", []),
            disagreements=data.get("disagreements", []),
        )
```

#### 汇总 Agent 的分析框架

LLM 在汇总时需要做以下几件事：

```
1. 🔍 一致性检查
   - 技术面看涨 + 新闻面看涨 → 强化信号
   - 技术面看涨 + 新闻面看跌 → 标记分歧，分析原因

2. ⚖️ 加权综合
   - 短期: 技术面 40% + 新闻 35% + 综合判断 25%
   - 中期: 技术面 30% + 新闻 25% + 综合判断 45%
   - （Phase 2 加入基本面/宏观后会重新设计权重）

3. 📊 方向判定
   - 综合分析后给出 bullish / bearish / neutral

4. 📐 幅度估算
   - 参考各 Agent 的幅度范围
   - 结合当前市场波动率进行调整

5. 🎯 置信度计算
   - 各 Agent 一致 → 高置信度（0.7+）
   - 有分歧但可解释 → 中等（0.5-0.7）
   - 严重矛盾 → 低置信度（<0.5）

6. ⚠️ 风险汇总
   - 收集各 Agent 提到的风险
   - 额外补充分析中发现的潜在风险
```

---

### 4.6 Prompt 模板

Phase 0 的 Mock Agent 里 prompt 是硬编码在 Agent 类中的。Phase 1 将它们独立到 `src/prompts/` 目录下，方便维护和迭代。

#### `src/prompts/technical_prompts.py`

```python
TECHNICAL_SYSTEM_PROMPT = """你是一个专业的技术面分析师，拥有 10 年以上的 K 线分析经验。

## 你的分析框架

### 1. 趋势判断
- 均线排列：MA5/MA10/MA20/MA60 的位置关系
- 金叉/死叉信号
- 价格与均线的位置关系

### 2. 动能分析
- MACD：DIF/DEA 的位置和交叉
- RSI：超买(>70)/超卖(<30)/中性(30-70)
- 成交量：放量/缩量，与价格的配合关系

### 3. 关键价位
- 布林带位置
- 近期高低点（支撑/阻力）
- 价格在布林带中的位置

### 4. 综合研判
- 多指标共振的方向判断
- 单指标背离的特殊情况
- 短期 vs 中期趋势的矛盾处理

## 你的输出格式

你必须以 JSON 格式输出，不能包含任何其他文字：

```json
{
  "direction": "bullish|bearish|neutral",
  "magnitude": {
    "min_pct": -5.0,
    "max_pct": 5.0
  },
  "confidence": 0.0到1.0,
  "reasoning": "你的分析推理过程（Markdown格式，200-500字）",
  "key_factors": ["影响判断的关键因素1", "因素2", "因素3"],
  "risks": ["潜在风险1", "风险2"]
}
```

## 注意事项
- direction 只能是 bullish、bearish、neutral 之一
- magnitude 的 min_pct 和 max_pct 是百分比变化范围（正=涨，负=跌）
- confidence 是 0 到 1 之间的小数，0.7 以上表示较有把握
- 如果多个指标互相矛盾，优先信任趋势类指标（均线>MACD>RSI）
- 不要给出投资建议，只做技术分析
"""
```

#### `src/prompts/news_prompts.py`

```python
NEWS_SYSTEM_PROMPT = """你是一个专业的财经新闻分析师，擅长从新闻中提取市场情绪和事件影响。

## 你的分析框架

### 1. 新闻情绪判断
- 统计正面/负面/中性新闻的占比
- 重点关注标题的情感倾向
- 辨别"标题党"与实质内容

### 2. 重大事件评估
- 财报/业绩预告：超预期 vs 低于预期
- 政策变化：利好 vs 利空
- 行业动态：景气度上升 vs 下滑
- 公司公告：重大合同、并购、高管变动

### 3. 市场预期差
- 好消息是否已被市场消化（price in）？
- 坏消息是否已被充分定价？
- 分析师评级变化方向

### 4. 时效性权重
- 近 3 天的新闻权重最高
- 1 周内新闻次之
- 超过 2 周的仅供参考

## 你的输出格式

你必须以 JSON 格式输出，不能包含任何其他文字：

```json
{
  "direction": "bullish|bearish|neutral",
  "magnitude": {
    "min_pct": -5.0,
    "max_pct": 5.0
  },
  "confidence": 0.0到1.0,
  "reasoning": "你的分析推理过程（Markdown格式，200-500字）",
  "key_factors": ["主要利多因素", "主要利空因素"],
  "risks": ["需要警惕的风险事件"]
}
```

## 注意事项
- 如果新闻数据为空或不足，请在 reasoning 中诚实标注，并将 confidence 设为较低值
- 注意区分"短期情绪冲击"和"基本面改变"，前者影响1-3天，后者影响数周
- 不要给出投资建议，只做新闻面分析
"""
```

#### `src/prompts/aggregator_prompts.py`

```python
AGGREGATOR_SYSTEM_PROMPT = """你是一个资深的投资研究主管，负责综合各维度分析师的报告，给出最终的投资研判。

## 你的工作流程

### 1. 阅读各方报告
仔细阅读技术面分析师和新闻分析师的分析结果。

### 2. 一致性分析
- 各方方向一致 → 信号加强
- 方向矛盾 → 分析矛盾原因，判断哪方更有说服力
- 一方明确、一方模糊 → 以明确方为主

### 3. 加权综合
- 短期预测（1天~2周）：技术面权重 40%，新闻面权重 35%，综合判断 25%
- 中期预测（2周~2月）：技术面权重 30%，新闻面权重 25%，综合判断 45%

### 4. 最终判断
- 给出综合方向（bullish/bearish/neutral）
- 估算涨跌幅度区间
- 给出综合置信度
- 汇总关键风险
- 指出分歧点（如有）

## 你的输出格式

你必须以 JSON 格式输出，不能包含任何其他文字：

```json
{
  "direction": "bullish|bearish|neutral",
  "magnitude": {
    "min_pct": -5.0,
    "max_pct": 5.0
  },
  "confidence": 0.0到1.0,
  "summary": "综合分析总结（Markdown格式，300-600字）",
  "key_risks": ["风险1", "风险2"],
  "disagreements": ["分歧点说明1（如无分歧则为空数组）"]
}
```

## 注意事项
- 置信度的参考标准：
  - 0.8+：多方高度一致，信号明确
  - 0.6-0.8：多数一致，少数谨慎
  - 0.4-0.6：分歧明显，需进一步观察
  - <0.4：信号混乱，建议观望
- 如果各方均无明确方向，诚实输出 neutral
- 不要给出"买入/卖出"建议，只做综合分析
"""
```

---

### 4.7 主入口脚本升级

`scripts/run_analysis.py` 需要从 Phase 0 的 Mock 版本升级为真实流程：

```
Phase 0 流程:
  Orchestrator.run_all() → 所有 Agent 并行 → 输出各 Agent 结果

Phase 1 流程（升级后）:
  1. Orchestrator.run_selected(["技术面分析师", "新闻分析师"])
     → 两 Agent 并行执行
  2. 拿到结果 → 传给 Aggregator
  3. Aggregator.aggregate() → FinalReport
  4. 输出 Markdown 报告 + 保存 JSON
```

核心改动点：

```python
# Phase 1 run_analysis.py 伪代码

# 注册真实 Agent
orchestrator.register(TechnicalAnalyst(llm))
orchestrator.register(NewsAnalyst(llm))
aggregator = Aggregator(llm)

# Step 1: 并行执行技术面 + 新闻
agent_results = await orchestrator.run_selected(
    target, timeframe,
    agent_names=["技术面分析师", "新闻分析师"],
)

# Step 2: 汇总
report = await aggregator.aggregate(target, timeframe, agent_results)

# Step 3: 输出
print(report.to_markdown())
# 保存 report.to_json() 到 output/
```

---

## 5. 文件清单与创建顺序

按依赖关系排列：

```
Phase 1 文件创建顺序
═══════════════════════════════════════════════════

[1] src/prompts/technical_prompts.py     — 技术面 prompt（无依赖）
[2] src/prompts/news_prompts.py          — 新闻 prompt（无依赖）
[3] src/prompts/aggregator_prompts.py    — 汇总 prompt（无依赖）

[4] src/data/price_fetcher.py            — 股价获取器（依赖: akshare/pandas）
[5] src/data/news_fetcher.py             — 新闻获取器（依赖: akshare）

[6] src/agents/technical_analyst.py      — 技术面 Agent（依赖: base_agent, price_fetcher, prompts）
[7] src/agents/news_analyst.py           — 新闻 Agent（依赖: base_agent, news_fetcher, prompts）
[8] src/agents/aggregator.py             — 汇总 Agent（依赖: result, prompts）

[9] scripts/run_analysis.py              — 主入口升级（依赖: 所有 Agent）

[10] tests/test_technical_analyst.py     — 技术面测试
[11] tests/test_news_analyst.py          — 新闻测试
[12] tests/test_aggregator.py            — 汇总测试
```

---

## 6. 测试策略

### 6.1 单元测试

| 测试对象 | 内容 |
|---------|------|
| `PriceFetcher` | 市场识别、数据获取、指标计算正确性 |
| `NewsFetcher` | 新闻获取、异常降级 |
| `TechnicalAnalyst` | gather_data → analyze 流程（Mock LLM） |
| `NewsAnalyst` | gather_data → analyze 流程（Mock LLM） |
| `Aggregator` | 结果解析、极端情况（全 neutral / 矛盾结果） |

### 6.2 Mock 策略

测试 Agent 时不应调用真实 API（花钱+慢）：

```python
# 使用 Mock LLMClient，返回预设的 JSON
class MockLLMClient:
    def __init__(self, mock_response: str):
        self.mock_response = mock_response
    
    async def achat(self, system_prompt, user_prompt):
        return LLMResponse(content=self.mock_response)
```

### 6.3 集成测试

```
端到端测试（真实 API 调用，手动触发）：
  python3 scripts/run_analysis.py --target 000001 --timeframe "短期(1周)"
  → 检查是否能跑通完整流程
  → 检查输出 Markdown 格式是否完整
```

---

## 7. Phase 1 完成标准

```bash
# 1. 单元测试全绿
pytest tests/ -v

# 2. A 股分析跑通
python3 scripts/run_analysis.py --target 000001 --timeframe "短期(1周)"

# 3. 港股分析跑通
python3 scripts/run_analysis.py --target 0700 --timeframe "短期(1周)"

# 4. 输出目录有完整的 Markdown 报告
ls output/*.md

# 5. 报告包含以下部分：
#    ✅ 综合预测（方向 + 幅度 + 置信度）
#    ✅ 技术面分析摘要
#    ✅ 新闻面分析摘要
#    ✅ 关键风险提示
#    ✅ 分歧点说明
```

### 验收检查表

```
☐ 技术面 Agent 能获取 A 股真实 K 线数据
☐ 技术面 Agent 能获取港股真实 K 线数据  
☐ 技术指标计算正确（与同花顺/东方财富对比大致一致）
☐ 新闻 Agent 能获取真实新闻（或有优雅降级）
☐ 汇总 Agent 能正确处理多个 AnalysisResult
☐ 汇总 Agent 能输出包含所有必需字段的 FinalReport
☐ 最终 Markdown 报告格式完整、可读
☐ 至少 3 个 Agent 各自的单元测试
☐ 端到端 A 股分析 5 分钟内完成
☐ 端到端港股分析 5 分钟内完成
```

---

## 附录 A: Phase 1 与 Phase 0 的关系

```
Phase 0（已完成）:
  ┌────────────────────────────────────────┐
  │  config/settings.py    ✅              │
  │  src/core/result.py    ✅              │
  │  src/core/llm_client.py ✅             │
  │  src/core/base_agent.py ✅             │
  │  src/core/orchestrator.py ✅           │
  │  src/core/data_collector.py ✅（基类）  │
  │  src/utils/logger.py   ✅              │
  └────────────────────────────────────────┘
                    ↓ 提供基础能力
Phase 1（本阶段）:
  ┌────────────────────────────────────────┐
  │  src/data/price_fetcher.py     🆕      │
  │  src/data/news_fetcher.py      🆕      │
  │  src/prompts/*.py              🆕      │
  │  src/agents/technical_analyst.py 🆕    │
  │  src/agents/news_analyst.py    🆕      │
  │  src/agents/aggregator.py      🆕      │
  └────────────────────────────────────────┘
```

---

> 📌 **Phase 1 是第一个真正有价值的阶段**——从 Mock 到真实数据，从单个 Agent 到团队协作。确认设计后就可以开始写代码了。
