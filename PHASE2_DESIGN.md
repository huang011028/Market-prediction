# Phase 2: 基本面 + 宏观分析师 — 实现设计文档

> **目标**：新增公司前景分析师和国际形势分析师，引入权重机制，实现多维度、分时间周期的综合分析。
>
> **核心变化**：从"技术面+新闻"的短期视角，扩展到"多维度+多周期"的立体分析。

---

## 目录

1. [Phase 2 目标清单](#1-phase-2-目标清单)
2. [整体架构变化](#2-整体架构变化)
3. [新增依赖](#3-新增依赖)
4. [模块详细设计](#4-模块详细设计)
   - [4.1 基本面数据获取 (`src/data/fundamental_fetcher.py`)](#41-基本面数据获取-srcdatafundamental_fetcherpy)
   - [4.2 宏观数据获取 (`src/data/macro_fetcher.py`)](#42-宏观数据获取-srcdatamacro_fetcherpy)
   - [4.3 公司前景分析师 (`src/agents/fundamental_analyst.py`)](#43-公司前景分析师-srcagentsfundamental_analystpy)
   - [4.4 国际形势分析师 (`src/agents/macro_analyst.py`)](#44-国际形势分析师-srcagentsmacro_analystpy)
   - [4.5 Prompt 模板（2 个新增）](#45-prompt-模板2-个新增)
   - [4.6 权重管理系统 (`config/agent_config.yaml` + 加载器)](#46-权重管理系统-configagent_configyaml--加载器)
   - [4.7 汇总分析师升级](#47-汇总分析师升级)
5. [文件清单与创建顺序](#5-文件清单与创建顺序)
6. [测试策略](#6-测试策略)
7. [Phase 2 完成标准](#7-phase-2-完成标准)

---

## 1. Phase 2 目标清单

| # | 任务 | 产出文件 | 优先级 |
|---|------|---------|--------|
| 2.1 | 权重配置文件 + 加载器 | `config/agent_config.yaml`, `config/weight_manager.py` | ⭐⭐⭐ |
| 2.2 | 基本面数据获取器 | `src/data/fundamental_fetcher.py` | ⭐⭐⭐ |
| 2.3 | 宏观数据获取器 | `src/data/macro_fetcher.py` | ⭐⭐⭐ |
| 2.4 | 基本面 Prompt | `src/prompts/fundamental_prompts.py` | ⭐⭐⭐ |
| 2.5 | 宏观 Prompt | `src/prompts/macro_prompts.py` | ⭐⭐⭐ |
| 2.6 | 公司前景分析师 Agent | `src/agents/fundamental_analyst.py` | ⭐⭐⭐ |
| 2.7 | 国际形势分析师 Agent | `src/agents/macro_analyst.py` | ⭐⭐⭐ |
| 2.8 | 汇总分析师升级（权重 + 更丰富上下文） | `src/agents/aggregator.py` | ⭐⭐⭐ |
| 2.9 | 主入口脚本升级（配置化 Agent 激活） | `scripts/run_analysis.py` | ⭐⭐ |
| 2.10 | 单元测试 | `tests/` 目录扩展 | ⭐⭐ |
| 2.11 | 端到端验证 | 4 个 Agent 协作跑通 | ⭐⭐⭐ |

---

## 2. 整体架构变化

### Phase 1 架构（当前）
```
用户输入 → Orchestrator ──并行──→ 📊 技术面 (真实K线)
                    │              📰 新闻   (真实新闻)
                    │                   │
                    └──── 结果 ────→ 🎯 汇总 (简单综合)
```

### Phase 2 架构（目标）
```
用户输入 → Orchestrator ──并行──→ 📊 技术面 (K线+指标)
                    │              📰 新闻   (近期新闻)
                    │              🏢 基本面 (财报+估值)
                    │              🌍 宏观   (政策+地缘)
                    │                   │
                    └──── 4个结果 ──→ 🎯 汇总 (带权重+多周期)
                                         │
                                         ▼
                                   📋 分时间维度报告
                                   ├─ 短期预测 (技术面40% + 新闻35% + 基本面15% + 宏观10%)
                                   ├─ 中期预测 (技术面30% + 新闻25% + 基本面25% + 宏观20%)
                                   └─ 长期预测 (技术面15% + 新闻10% + 基本面40% + 宏观35%)
```

### 关键变化点

| 维度 | Phase 1 | Phase 2 |
|------|---------|---------|
| Agent 数量 | 2 + 1（汇总） | 4 + 1（汇总） |
| 数据维度 | 价格 + 新闻 | 价格 + 新闻 + 财务 + 宏观 |
| 汇总方式 | 简单加权 | 分时间维度的动态权重 |
| Agent 激活 | 硬编码 | 配置文件驱动（可选择性激活） |
| 新闻降级 | 简单标注 | 降级时调整权重分配 |
| 长周期预测 | 不支持 | 支持中期、长期 |

---

## 3. 新增依赖

```
# requirements.txt 新增
# akshare 和 yfinance 已在 Phase 1 安装，无需新增 PyPI 依赖

# 配置文件解析
PyYAML>=6.0    # agent_config.yaml 解析
```

---

## 4. 模块详细设计

---

### 4.1 基本面数据获取 (`src/data/fundamental_fetcher.py`)

#### 职责

获取公司的核心财务数据、估值指标、机构评级，打包为 Agent 可消费的结构化数据。

#### 数据内容

```
┌─────────────────────────────────────────────────┐
│              基本面数据维度                       │
├─────────────────────────────────────────────────┤
│ 📊 财务指标（近4个季度 + 同比）                    │
│   - 营业收入 / 同比增长率                         │
│   - 净利润 / 净利率                               │
│   - 毛利率                                       │
│   - ROE（净资产收益率）                           │
│   - EPS（每股收益）                               │
│                                                 │
│ 💰 估值指标                                      │
│   - PE（市盈率）                                 │
│   - PB（市净率）                                 │
│   - PS（市销率）                                 │
│   - 股息率                                       │
│   - 总市值                                       │
│                                                 │
│ 📈 成长性指标                                    │
│   - 营收增速趋势                                  │
│   - 利润增速趋势                                  │
│   - PEG（市盈率/增长率）                          │
│                                                 │
│ 🔍 行业对比                                      │
│   - 行业平均 PE/PB                               │
│   - 公司在行业中的估值分位                         │
│                                                 │
│ ⭐ 机构观点                                      │
│   - 分析师评级（买入/增持/中性/减持/卖出）          │
│   - 目标价区间                                    │
│   - 盈利预测（未来1-2年）                         │
└─────────────────────────────────────────────────┘
```

#### 接口设计

```python
# src/data/fundamental_fetcher.py

from dataclasses import dataclass, field

@dataclass
class FundamentalData:
    """基本面数据封装"""
    symbol: str
    company_name: str
    market: str
    industry: str               # 所属行业
    
    # 最新财务数据
    latest_revenue: float       # 最新季度营收(亿)
    latest_net_profit: float    # 最新季度净利润(亿)
    revenue_yoy: float          # 营收同比(%)
    profit_yoy: float           # 利润同比(%)
    gross_margin: float         # 毛利率(%)
    net_margin: float           # 净利率(%)
    roe: float                  # ROE(%)
    eps: float                  # 每股收益
    
    # 估值指标
    pe: float                   # 市盈率
    pb: float                   # 市净率
    ps: float                   # 市销率
    market_cap: float           # 总市值(亿)
    dividend_yield: float       # 股息率(%)
    
    # 行业对比
    industry_pe: float          # 行业平均PE
    industry_pb: float          # 行业平均PB
    
    # 机构评级
    analyst_rating: str         # "buy"/"overweight"/"hold"/"underweight"/"sell"
    target_price_range: tuple   # (low, high)
    analyst_count: int          # 覆盖分析师数量
    
    # 数据来源标记
    data_source: str            # "akshare" / "yfinance" / "partial"
    
    def to_agent_dict(self) -> dict:
        """输出给 Agent 的字典格式"""
        ...

class FundamentalFetcher:
    """基本面数据获取器"""
    
    def __init__(self):
        ...
    
    async def fetch(self, symbol: str, market: str) -> FundamentalData:
        """
        获取基本面数据
        
        数据来源：
        - A股: akshare 财务指标 + 估值数据
        - 港股: akshare + yfinance
        - 美股: yfinance (info + financials)
        
        容错策略：部分数据缺失不报错，标记 data_source="partial"
        """
        ...
```

#### 数据获取策略

```python
async def fetch(self, symbol: str, market: str) -> FundamentalData:
    """
    分层获取，逐层降级：
    
    Layer 1: akshare（A股/港股）
      - stock_financial_abstract_ths    → 财务摘要
      - stock_a_lg_indicator           → 估值指标(PE/PB/PS)
      - stock_rank_forecast_cninfo     → 盈利预测
      
    Layer 2: yfinance（通用）
      - .info  → 估值、财务指标
      - .financials → 详细财报
      - .recommendations → 分析师评级
      
    Layer 3: 降级处理
      - 只获取能拿到的字段，缺失的填 None
      - data_source 标记为 "partial" + 注明哪些字段缺失
    """
```

---

### 4.2 宏观数据获取 (`src/data/macro_fetcher.py`)

#### 职责

获取影响市场的宏观环境数据：货币政策、经济指标、地缘政治动态。

#### 数据内容

```
┌─────────────────────────────────────────────────┐
│              宏观数据维度                         │
├─────────────────────────────────────────────────┤
│ 🏦 货币政策                                      │
│   - 央行基准利率 / LPR                            │
│   - 存款准备金率                                  │
│   - 美联储利率决议 + 鲍威尔讲话摘要                 │
│   - 欧央行/日央行政策动态                          │
│                                                 │
│ 📊 经济指标（中国）                               │
│   - GDP 增速                                     │
│   - CPI / PPI                                   │
│   - PMI（制造业/非制造业）                         │
│   - 社会融资规模                                  │
│   - M2 货币供应增速                               │
│                                                 │
│ 📊 经济指标（美国）                               │
│   - 非农就业                                     │
│   - CPI / Core PCE                              │
│   - ISM 制造业 PMI                               │
│                                                 │
│ 🌐 汇率与资本流动                                 │
│   - 美元指数 (DXY)                               │
│   - USD/CNY 汇率                                 │
│   - 北向资金净流入（沪深港通）                      │
│   - 美债收益率（2年/10年）                         │
│                                                 │
│ 🔥 地缘政治（手动标记 + LLM知识库）               │
│   - 中美关系状态                                   │
│   - 重大国际事件                                   │
│   - 贸易政策变化                                   │
└─────────────────────────────────────────────────┘
```

#### 关键设计：宏观数据不是"拉取"而是"呈现"

不同于技术面数据（直接从 API 取价格），宏观数据的核心挑战是：

1. **不是所有数据都有免费 API**：地缘政治判断只能靠 LLM 知识库
2. **宏观数据与标的的关联需要推理**：美联储加息如何影响 A 股某只消费股？这本身就需要 LLM 分析
3. **时效性与深度需要平衡**：知道最新 CPI 数据不如理解 CPI 趋势的意义

因此宏观 Agent 的设计思路是：
- **能拉到真实数据的**（利率、汇率、PMI）→ 拉取并呈现
- **拉不到的**（地缘政治、政策解读）→ 在 prompt 中告诉 LLM"你作为宏观分析师，基于你的知识分析当前宏观环境"
- **最终交给 LLM 综合判断**：将这些宏观因子与标的特征关联

#### 接口设计

```python
# src/data/macro_fetcher.py

@dataclass
class MacroData:
    """宏观数据封装"""
    
    # 中国市场
    cn_interest_rate: float       # LPR 1年期
    cn_cpi_yoy: float             # CPI 同比
    cn_pmi_manufacturing: float   # 制造业 PMI
    cn_m2_yoy: float              # M2 增速
    
    # 美国市场
    us_interest_rate: float       # 联邦基金利率
    us_cpi_yoy: float             # CPI 同比
    
    # 汇率
    usd_cny: float                # 美元/人民币
    dxy: float                    # 美元指数
    
    # 资本流动
    north_bound_flow: float       # 北向资金近期净流入(亿)
    
    # 市场情绪指标
    vix: float                    # 恐慌指数
    us_10y_yield: float           # 美10年国债收益率
    
    # 数据来源
    data_source: str
    
    def to_agent_dict(self) -> dict:
        """输出给 Agent，包含数值 + 趋势描述"""
        ...

class MacroFetcher:
    """宏观数据获取器"""
    
    async def fetch(self, target: str, market: str) -> MacroData:
        """
        获取与标的相关的宏观数据
        
        策略：
        1. 先拉取能拉到的硬数据（利率、汇率、PMI等）
        2. 无法拉取的（地缘政治）留空，由 LLM 知识库补充
        3. 对于不同市场的标的，返回不同的宏观数据组合
           - A股标的：侧重中国宏观 + 中美关系 + 北向资金
           - 港股标的：侧重中国宏观 + 港元流动性 + 中美关系
           - 美股标的：侧重美国宏观 + 美联储政策
        """
```

---

### 4.3 公司前景分析师 (`src/agents/fundamental_analyst.py`)

```python
# src/agents/fundamental_analyst.py

class FundamentalAnalyst(BaseAgent):
    """公司前景 / 基本面分析师
    
    判断公司的内在价值和成长前景。
    """
    
    def __init__(self, llm: LLMClient):
        super().__init__(
            name="基本面分析师",
            description="基于财报数据、估值水平、行业地位判断公司内在价值与成长前景",
            llm=llm,
        )
        self.fetcher = FundamentalFetcher()
    
    async def gather_data(self, target: str, timeframe: str) -> dict:
        """获取财务数据、估值指标、机构评级"""
        market = self._identify_market(target)
        data = await self.fetcher.fetch(target, market)
        return data.to_agent_dict()
    
    def _get_system_prompt(self) -> str:
        return FUNDAMENTAL_SYSTEM_PROMPT
```

#### 分析框架（写在 Prompt 中）

```
基本面分析师的判断维度：

1. **盈利能力评估**
   - 营收/利润趋势：增速是加快还是放缓？
   - 利润率水平：毛利率、净利率是否稳定？与行业对比如何？
   - ROE：资本回报效率，>15% 为优秀

2. **估值合理性判断**
   - PE/PB/PS 与历史区间对比（低估/合理/高估）
   - 与同行业对比（相对便宜/合理/偏贵）
   - PEG：增速能否支撑当前估值？

3. **成长性分析**
   - 未来 1-2 年盈利预测趋势
   - 行业景气度方向
   - 公司护城河/竞争优势

4. **机构认可度**
   - 分析师评级分布
   - 目标价 vs 当前价的空间

5. **风险因素**
   - 财务风险（高负债、现金流压力）
   - 行业风险（政策变化、竞争加剧）
   - 估值风险（泡沫化）
```

---

### 4.4 国际形势分析师 (`src/agents/macro_analyst.py`)

```python
# src/agents/macro_analyst.py

class MacroAnalyst(BaseAgent):
    """国际形势 / 宏观经济分析师
    
    判断宏观环境对标的的潜在影响方向和程度。
    """
    
    def __init__(self, llm: LLMClient):
        super().__init__(
            name="宏观分析师",
            description="分析货币政策、经济指标、地缘政治对标的的宏观环境影响",
            llm=llm,
        )
        self.fetcher = MacroFetcher()
    
    async def gather_data(self, target: str, timeframe: str) -> dict:
        """获取宏观指标"""
        market = self._identify_market(target)
        data = await self.fetcher.fetch(target, market)
        return data.to_agent_dict()
    
    def _get_system_prompt(self) -> str:
        return MACRO_SYSTEM_PROMPT
```

#### 分析框架（写在 Prompt 中）

```
宏观分析师的核心任务：将宏观环境翻译成对标的的影响

1. **流动性环境**
   - 货币政策松紧 → 市场资金面
   - 利率方向 → 估值中枢变化
   - 信用环境 → 企业融资成本

2. **经济周期定位**
   - 当前处于经济周期的哪个阶段（复苏/过热/滞胀/衰退）？
   - 不同阶段对不同行业的影响截然不同

3. **地缘政治量化**
   - 中美关系紧张度（高/中/低）
   - 贸易摩擦影响范围
   - 区域冲突外溢风险

4. **汇率影响（针对港股/跨境标的）**
   - 人民币升/贬值对港股的影响
   - 美元强弱对不同资产类别的影响

5. **资本流动**
   - 北向资金趋势（持续流入/流出）
   - 全球风险偏好（risk-on/risk-off）

6. **与标的的关联分析**（关键）
   - 宏观变量如何传导到具体标的？
   - 例如：降息 → 银行息差收窄 → 银行股承压
   - 例如：人民币贬值 → 出口型企业受益 → 利好外贸股
```

---

### 4.5 Prompt 模板（2 个新增）

#### `src/prompts/fundamental_prompts.py`

```python
FUNDAMENTAL_SYSTEM_PROMPT = """你是一个专业的公司基本面分析师，拥有 CFA 持证和 10 年以上的行业研究经验。你的核心能力是从财务数据中判断公司的内在价值和成长前景。

## 你的分析框架

### 1. 盈利能力分析
- 营收增速：与历史对比是加速还是减速？与行业对比是高还是低？
- 净利润率：趋势是否稳定？波动原因是什么？
- ROE（净资产收益率）：>15% 优秀，10-15% 良好，<10% 一般
- 毛利率：反映产品竞争力和定价权

### 2. 估值合理性
- PE：与历史 5 年区间对比，当前处于什么分位？
- PB：金融/周期股重点看 PB，与行业平均对比
- 与行业平均估值对比：相对便宜还是偏贵？
- PEG：PE / 盈利增速，<1 为低估，>2 可能高估

### 3. 成长性判断
- 盈利预测是上调还是下调趋势？
- 推动增长的核心驱动力是什么？
- 行业景气度是上升还是下降？

### 4. 机构观点
- 分析师评级：买入/增持比例高 → 市场认可
- 目标价空间：当前价到目标价的潜在涨幅
- 评级近期变化方向（上调/下调）

### 5. 特殊因子
- 分红：高股息率在市场下跌时有防御价值
- 回购：公司回购股票通常是价值低估的信号
- 管理层/股权结构：是否稳定？

## 你的输出格式

严格以 JSON 格式输出：

{
  "direction": "bullish|bearish|neutral",
  "magnitude": {"min_pct": -10.0, "max_pct": 10.0},
  "confidence": 0.65,
  "reasoning": "分析推理过程。按盈利能力→估值→成长性→机构观点→综合结论的顺序，Markdown 格式，200-500字。",
  "key_factors": ["核心利多", "核心利空"],
  "risks": ["基本面风险1", "风险2"]
}

## 注意事项
- 基本面分析对短期（1周内）价格影响有限，对中长期（1个月以上）影响更大
- 如果数据不完整，在 reasoning 中诚实标注，降低 confidence
- 估值的"合理"是一个区间，不是精确的一个数字
- 不要给出投资建议"""
```

#### `src/prompts/macro_prompts.py`

```python
MACRO_SYSTEM_PROMPT = """你是一个资深的宏观经济学家和地缘政治分析师，曾在 IMF 和顶级投行任职。你的核心能力是将复杂的宏观环境翻译为对具体金融资产的影响。

## 你的分析框架

### 1. 流动性环境判断
- 当前货币政策立场：宽松 / 中性 / 紧缩？
- 利率趋势：加息周期 / 降息周期 / 稳定？
- 市场资金面：充裕 / 平衡 / 紧张？
- 影响：宽松 → 利好股市估值；紧缩 → 压制风险资产

### 2. 经济周期定位
- 判断当前经济处于：复苏期 / 过热期 / 滞胀期 / 衰退期
- 不同周期对不同行业的影响差异巨大
- 领先指标（PMI）和滞后指标（就业）的组合判断

### 3. 汇率与资本流动
- 美元走势：强势美元 → 新兴市场资金外流压力
- 人民币汇率：贬值 → 利好出口企业，利空进口依赖型企业
- 北向资金：持续流入 → 外资看好 A 股；持续流出 → 避险信号
- 美债收益率：上升 → 压缩全球股市估值

### 4. 地缘政治评估
- 中美关系现状及近期事件
- 贸易政策/技术封锁/金融制裁的动态
- 区域冲突的溢出风险
- 对特定行业的影响路径

### 5. 宏观到标的的传导（核心能力）
你必须将宏观变量"翻译"成对具体标的的影响：
- 标的属于什么行业？宏观因子如何影响该行业？
- 标的是否有跨境业务？汇率波动的影响多大？
- 标的的估值对利率敏感度如何？（成长股敏感，价值股不敏感）

### 6. 时间维度区分
- 短期（1-2周）：突发政策、地缘事件冲击
- 中期（1-3月）：货币政策转向、经济数据趋势
- 长期（3月以上）：经济周期切换、结构性变化

## 你的输出格式

严格以 JSON 格式输出：

{
  "direction": "bullish|bearish|neutral",
  "magnitude": {"min_pct": -10.0, "max_pct": 10.0},
  "confidence": 0.60,
  "reasoning": "分析推理过程。按流动性→经济周期→汇率→地缘政治→对标的传导→综合结论的顺序，Markdown 格式，200-500字。",
  "key_factors": ["宏观利好因素", "宏观利空因素"],
  "risks": ["宏观风险1", "风险2"]
}

## 注意事项
- 宏观分析的特点是高不确定性——confidence 通常不应超过 0.7
- 如果某些宏观数据不可用，在 reasoning 中说明并基于知识库判断
- 重要：必须说明宏观因子如何具体影响该标的，不要泛泛而谈
- 不要给出投资建议"""
```

---

### 4.6 权重管理系统

#### 设计思路

随着 Agent 数量增多，硬编码权重不可维护。Phase 2 引入配置文件驱动：

- `config/agent_config.yaml`：定义 Agent 列表、默认激活状态、分时间维度的权重
- `config/weight_manager.py`：加载配置 + 提供权重查询 API

#### `config/agent_config.yaml`

```yaml
# ============================================================
# Agent 配置 — 控制 Agent 的激活、权重、行为参数
# ============================================================

# --- Agent 注册表 ---
agents:
  - name: "技术面分析师"
    module: "src.agents.technical_analyst"
    class: "TechnicalAnalyst"
    description: "基于K线形态和技术指标分析短期走势"
    enabled: true
    requires_data: true
    
  - name: "新闻分析师"
    module: "src.agents.news_analyst"
    class: "NewsAnalyst"
    description: "分析近期新闻的情绪和事件影响"
    enabled: true
    requires_data: true
    
  - name: "基本面分析师"
    module: "src.agents.fundamental_analyst"
    class: "FundamentalAnalyst"
    description: "基于财报和估值判断公司内在价值"
    enabled: true
    requires_data: true
    
  - name: "宏观分析师"
    module: "src.agents.macro_analyst"
    class: "MacroAnalyst"
    description: "分析宏观环境和地缘政治影响"
    enabled: true
    requires_data: true

# --- 分时间维度的权重 ---
# 权重将传递给汇总 Agent，用于加权综合
weights:
  # 短期（1天~2周）：技术面+新闻主导
  short:
    技术面分析师: 0.35
    新闻分析师: 0.25
    基本面分析师: 0.15
    宏观分析师: 0.15
    综合研判: 0.10   # 汇总 Analyst 自身判断的权重
    
  # 中期（2周~2月）：均衡
  medium:
    技术面分析师: 0.25
    新闻分析师: 0.15
    基本面分析师: 0.25
    宏观分析师: 0.20
    综合研判: 0.15
    
  # 长期（2月~1季度）：基本面+宏观主导
  long:
    技术面分析师: 0.10
    新闻分析师: 0.05
    基本面分析师: 0.35
    宏观分析师: 0.30
    综合研判: 0.20

# --- 降级策略 ---
# 当某个 Agent 失败时，如何重新分配它的权重
fallback:
  # 规则：缺失 Agent 的权重按比例分配给同组的其他 Agent
  strategy: "proportional"  # proportional | equal | ignore
```

#### `config/weight_manager.py`

```python
"""权重管理器 — 加载 agent_config.yaml 并提供权重查询"""

import yaml
from pathlib import Path
from dataclasses import dataclass

@dataclass
class WeightConfig:
    """某个时间维度的权重配置"""
    agent_weights: dict[str, float]  # {"技术面分析师": 0.35, ...}
    synthesis_weight: float          # 综合研判权重

class WeightManager:
    """权重管理器"""
    
    def __init__(self, config_path: Path | None = None):
        if config_path is None:
            config_path = Path(__file__).parent / "agent_config.yaml"
        
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)
    
    def get_weights(self, timeframe: str) -> WeightConfig:
        """根据时间维度返回权重配置"""
        ...
    
    def get_weight(self, agent_name: str, timeframe: str) -> float:
        """获取单个 Agent 在指定时间维度的权重"""
        ...
    
    def redistribute_weights(
        self, 
        timeframe: str, 
        active_agents: list[str],
        failed_agents: list[str],
    ) -> WeightConfig:
        """
        处理 Agent 失败时的权重再分配
        
        例如：新闻 Agent 失败 → 它的 25% 权重按比例分给技术面和基本面
        """
        ...
    
    def get_enabled_agents(self) -> list[str]:
        """获取配置中启用的 Agent 列表"""
        ...
    
    def timeframes(self) -> list[str]:
        """所有支持的时间维度"""
        ...
```

---

### 4.7 汇总分析师升级

Phase 2 的汇总 Agent 需要重大升级：

#### 变化 1：接收权重信息

```python
async def aggregate(
    self,
    target: str,
    timeframe: str,
    agent_results: list[AnalysisResult],
    weight_config: WeightConfig,  # 🆕 权重配置
    failed_agents: list[str] = None,  # 🆕 失败的 Agent 列表
) -> FinalReport:
```

#### 变化 2：更丰富的上下文

将权重信息传给 LLM，让它在综合判断时参考：

```python
def _build_context(self, ...):
    parts = [
        "## 权重参考",
        f"当前为{timeframe}预测，各维度参考权重如下：",
    ]
    for agent, weight in weight_config.agent_weights.items():
        parts.append(f"- {agent}: {weight:.0%}")
    parts.append(f"- 综合研判弹性: {weight_config.synthesis_weight:.0%}")
    parts.append("")
    parts.append("注意：权重仅供参考，你作为研究主管可以根据分析质量调整。")
    parts.append("")
    # ... 然后是各 Agent 的分析结果
```

#### 变化 3：处理更多 Agent

原来是 2 个结果，现在是 4 个。Prompt 中需要更新分析框架：

```
原: 技术面 vs 新闻 → 简单一致性检查
新: 4 维度交叉验证：
  - 技术面 + 新闻  → 短期市场情绪是否与技术信号一致？
  - 基本面 + 宏观  → 中长期价值是否被宏观环境支撑？
  - 技术面 + 基本面 → 价格是否偏离价值？（超买/超卖）
  - 新闻 + 宏观    → 事件冲击是短期的还是结构性的？
```

#### 变化 4：输出升级

汇总报告新增：
- 短期/中期/长期分别给出预测（如果用户在配置中启用）
- 四象限图描述（技术面×基本面）
- Agent 贡献度说明

```python
# FinalReport 新增字段（Phase 2）
@dataclass
class FinalReport:
    # ... 原有字段 ...
    
    # 🆕 Phase 2 新增
    weight_summary: dict = field(default_factory=dict)
    # {"short": {"方向": "bullish", "置信度": 0.7}, "medium": {...}, "long": {...}}
    
    agent_contributions: dict = field(default_factory=dict)
    # {"技术面分析师": "提供了关键的趋势信号...", ...}
    
    failed_agents: list[str] = field(default_factory=list)
    # 执行失败的 Agent 列表
```

---

## 5. 文件清单与创建顺序

```
Phase 2 文件创建顺序
═══════════════════════════════════════════════════

[1] config/agent_config.yaml               — 权重 + Agent 注册配置（无依赖）
[2] config/weight_manager.py               — 权重加载器（依赖: PyYAML + config）

[3] src/prompts/fundamental_prompts.py      — 基本面 prompt（无依赖）
[4] src/prompts/macro_prompts.py            — 宏观 prompt（无依赖）

[5] src/data/fundamental_fetcher.py         — 基本面数据获取（依赖: akshare/yfinance）
[6] src/data/macro_fetcher.py               — 宏观数据获取（依赖: akshare）

[7] src/agents/fundamental_analyst.py       — 基本面 Agent
[8] src/agents/macro_analyst.py             — 宏观 Agent

[9] src/core/result.py                      — FinalReport 新增字段
[10] src/agents/aggregator.py               — 汇总 Agent 升级（权重 + 多 Agent）
[11] src/prompts/aggregator_prompts.py      — 汇总 prompt 升级

[12] scripts/run_analysis.py                — 主入口：配置驱动 Agent 激活
[13] tests/test_phase2.py                   — 新测试
```

---

## 6. 测试策略

| 测试对象 | 内容 |
|---------|------|
| `WeightManager` | 配置加载、权重查询、降级再分配 |
| `FundamentalFetcher` | 财务数据获取、缺失字段处理 |
| `MacroFetcher` | 宏观数据获取、市场差异化 |
| `FundamentalAnalyst` | gather_data → analyze（Mock LLM） |
| `MacroAnalyst` | gather_data → analyze（Mock LLM） |
| `Aggregator`（升级版） | 权重传递、4 Agent 上下文构建、JSON 解析 |

---

## 7. Phase 2 完成标准

```bash
# 1. 全量测试通过
pytest tests/ -v -m "not slow"

# 2. 4 个 Agent 全部参与分析
python3 scripts/run_analysis.py --target 000001

# 3. 报告包含权重信息和 Agent 贡献度
#    输出中能看到 "权重参考" 部分

# 4. 某项数据缺失时的降级处理正常
python3 scripts/run_analysis.py --target 000001 --no-news
#    报告中应标注哪些 Agent 失败，权重如何重新分配

# 5. 配置文件驱动
#    修改 agent_config.yaml 中某个 Agent 的 enabled: false
#    重新运行 → 该 Agent 不被激活
```

### 验收检查表

```
☐ agent_config.yaml 可正常加载
☐ WeightManager 权重查询正确
☐ 基本面 Agent 能获取真实财务数据（至少部分字段）
☐ 宏观 Agent 能获取宏观指标（至少部分字段）
☐ 4 个 Agent 并行执行 + 汇总完整跑通
☐ 汇总报告包含权重分配说明
☐ 单 Agent 失败不影响整体流程
☐ 降级时权重自动重新分配
☐ 至少 15 个新增单元测试通过
☐ 港股标的能跑通（基本面数据源可能与 A 股不同）
```

---

## 附录 A: Phase 发展路线总览

```
Phase 0: 基础设施     ✅ 完成
  └─ settings, llm_client, base_agent, orchestrator, result, logger

Phase 1: MVP          ✅ 完成  
  └─ 技术面分析师 + 新闻分析师 + 汇总 (真实数据)

Phase 2: 扩展维度     📅 当前阶段
  └─ 基本面分析师 + 宏观分析师 + 权重系统

Phase 3: 增强功能     📅 待开始
  └─ RAG 历史案例、回测框架、预测准确率追踪

Phase 4: 产品化       📅 待开始
  └─ Streamlit Web、多标的批量、定时监控
```

---

> 📌 **Phase 2 的核心价值**：让分析从"看图说话"升级为"多维交叉验证"。同一个标的不同 Agent 可能给出矛盾结论——技术面看涨但基本面高估、新闻利多但宏观偏空——正是这些矛盾让最终判断有了深度。确认设计后开始写代码。
