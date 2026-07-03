# 📰 新闻分析师改进方案

> **版本**: v1.0 | **日期**: 2026-07-03 | **状态**: 设计阶段

---

## 目录

1. [现状评估](#1-现状评估)
2. [改进总览](#2-改进总览)
3. [数据源增强](#3-数据源增强)
4. [新闻预处理管线](#4-新闻预处理管线)
5. [Agent 架构升级](#5-agent-架构升级)
6. [Prompt 工程优化](#6-prompt-工程优化)
7. [自进化机制](#7-自进化机制)
8. [质量保障体系](#8-质量保障体系)
9. [实施路线图](#9-实施路线图)
10. [附录：效果度量](#10-附录效果度量)

---

## 1. 现状评估

### 1.1 当前流程

```
┌──────────────┐     ┌─────────────────┐     ┌──────────────┐
│  NewsFetcher │ ──▶ │  单次 LLM 推理   │ ──▶ │ AnalysisResult│
│  (akshare)   │     │  (一次 prompt)   │     │              │
└──────────────┘     └─────────────────┘     └──────────────┘
```

**一个步骤即可描述当前流程**：抓取新闻 → 塞进 prompt → LLM 输出结果。

### 1.2 优点（保留）

| 项目 | 说明 |
|------|------|
| ✅ 结构清晰 | 继承 BaseAgent，与其他 Agent 风格一致 |
| ✅ 多层降级 | akshare → yfinance → unavailable，有兜底 |
| ✅ 异常处理 | 超时/异常有 fallback，不阻塞整体流程 |
| ✅ 缺数据标注 | 无新闻时明确标注 `[注：基于知识库...]`，降低 confidence |
| ✅ 数据截断保护 | >8000 字符截断，避免超 token |

### 1.3 核心问题（需要解决）

#### 🔴 严重问题

| # | 问题 | 现象 | 影响 |
|---|------|------|------|
| 1 | **单一数据源** | A/H 股只依赖 akshare 的东方财富 | 新闻覆盖面窄，重大新闻可能漏掉；信息单一化导致分析偏差 |
| 2 | **无新闻预处理** | 原始新闻直接喂给 LLM | 20 条新闻中可能包含 50% 无关新闻，浪费 token + 干扰判断 |
| 3 | **无时间权重** | 上周的旧闻和今天的热点同等对待 | 时效性信息被稀释，短期预测失真 |
| 4 | **无情感预标注** | 完全依赖 LLM 逐条判断情绪 | LLM 可能在情绪统计上出错（数错正负数量），且消耗大量 token |
| 5 | **单 pass 推理** | 一次 prompt 完成所有分析 | 没有"先提取信号、再评估影响"的分步思考，容易遗漏或跳跃推理 |

#### 🟡 中等问题

| # | 问题 | 现象 | 影响 |
|---|------|------|------|
| 6 | **无历史参照** | 不知道"类似新闻在历史上对股价的影响" | 无法利用历史规律，纯靠 LLM 知识库 |
| 7 | **无市场区分** | A 股和港股用同一套 prompt | A 股政策市 vs 港股机构市的新闻影响机制不同 |
| 8 | **无来源可信度加权** | 官方公告和自媒体传闻同等对待 | prompt 里写了"注意来源可信度"但没有数据支撑 |
| 9 | **置信度未校准** | confidence = 0.65 的含义不明确 | 不知道这个 0.65 历史上准确率是多少 |
| 10 | **无自评/反思** | 输出完就结束 | 没有"我的分析有什么可能的盲点？"这一步 |

#### 🟢 轻微问题

| # | 问题 | 现象 | 影响 |
|---|------|------|------|
| 11 | 无 few-shot 示例 | prompt 纯文字描述，没有正例/反例 | LLM 输出格式不稳定，偶有 JSON 解析失败 |
| 12 | 无并发数据源 | fetcher 串行尝试 akshare → yfinance | 数据采集耗时无优化空间 |
| 13 | 无新闻去重 | 同一事件被多家媒体报道会被重复计数 | 情绪统计失真（一条利好被当成 5 条） |

### 1.4 当前在系统中的位置

```
Agent 权重（短期）：20%（仅次于技术面 30%）
Agent 权重（中期）：12%
Agent 权重（长期）：4%
```

新闻分析师在**短期预测**中权重排第二，是核心 Agent 之一。其改进对整体预测质量影响显著。

---

## 2. 改进总览

### 2.1 目标架构

```
                         ┌──────────────────────────────────────┐
                         │        🌐 多源新闻采集层              │
                         │                                      │
                         │  东方财富  │  新浪财经  │  雪球      │
                         │  (主力)   │  (补充)   │  (情绪)    │
                         │      │         │          │         │
                         │      └────┬────┴────┬─────┘         │
                         │           ▼         ▼                │
                         │      ┌─────────────────┐             │
                         │      │  去重 + 合并     │             │
                         │      └────────┬────────┘             │
                         └──────────────┼──────────────────────┘
                                        │
                         ┌──────────────▼──────────────────────┐
                         │      🔧 新闻预处理管线               │
                         │                                      │
                         │  ┌──────────┐  ┌──────────┐         │
                         │  │ 相关性    │  │ 情感      │         │
                         │  │ 评分过滤  │  │ 预标注    │         │
                         │  └────┬─────┘  └────┬─────┘         │
                         │       └──────┬──────┘                │
                         │              ▼                       │
                         │  ┌──────────────────┐               │
                         │  │ 分类 + 时间衰减   │               │
                         │  │ 权重计算          │               │
                         │  └────────┬─────────┘               │
                         └───────────┼─────────────────────────┘
                                     │
                         ┌───────────▼─────────────────────────┐
                         │      🧠 多步推理引擎                 │
                         │                                      │
                         │  Step 1: 信号提取                   │
                         │     "这些新闻在说什么？"              │
                         │           │                          │
                         │  Step 2: 影响评估                   │
                         │     "这些信号对股价意味着什么？"      │
                         │           │                          │
                         │  Step 3: 历史对比（可选，RAG）      │
                         │     "历史上类似情况发生了什么？"      │
                         │           │                          │
                         │  Step 4: 综合判断 + 反思            │
                         │     "我的分析有什么盲点？"            │
                         │           │                          │
                         │  Step 5: 置信度校准                 │
                         │     "基于历史，我的准确率大概多少？"   │
                         └───────────┬─────────────────────────┘
                                     │
                         ┌───────────▼─────────────────────────┐
                         │      📊 输出 + 自进化反馈            │
                         │                                      │
                         │  AnalysisResult → PredictionStore    │
                         │       │                              │
                         │       └──→ 事后验证 → 更新准确率     │
                         │                  → 调整置信度校准     │
                         │                  → 优化 source 权重   │
                         └──────────────────────────────────────┘
```

### 2.2 改进维度一览

| 维度 | 改进项 | 优先级 | 预期收益 |
|------|--------|--------|---------|
| 📡 数据源 | 多源采集 + 去重 | 🔴 P0 | 新闻覆盖 +50%，去重减少 token 浪费 |
| 📡 数据源 | 雪球/股吧情绪数据 | 🟡 P1 | 新增散户情绪维度 |
| 🔧 预处理 | 相关度评分过滤 | 🔴 P0 | 减少无关新闻 30-50% |
| 🔧 预处理 | 情感预标注（规则+小模型） | 🔴 P0 | LLM token 减少 40%，情绪统计更准 |
| 🔧 预处理 | 新闻分类 + 时间衰减 | 🟡 P1 | 结构化输入，时效性凸显 |
| 🧠 架构 | 多步链式推理（CoT） | 🔴 P0 | 推理质量 ↑，可解释性 ↑ |
| 🧠 架构 | 反思/自评环节 | 🟡 P1 | 识别盲点，减少过度自信 |
| 🧠 架构 | 历史案例检索（RAG） | 🟢 P2 | 利用历史规律 |
| 📝 Prompt | Few-shot 示例 | 🔴 P0 | JSON 格式稳定性 ↑ |
| 📝 Prompt | 市场区分 prompt | 🟡 P1 | A 股政策逻辑 / 港股机构逻辑 |
| 📝 Prompt | 置信度校准指引 | 🟡 P1 | confidence 含义更明确 |
| 🔬 质量 | 来源可信度数据化 | 🟡 P1 | prompt 中的指引落地 |
| 🔬 质量 | 输出校验增强 | 🟡 P1 | 检测幻觉/矛盾 |
| 🧬 自进化 | 准确率追踪 + 置信度校准 | 🟡 P1 | confidence 从"感觉"变成"统计" |
| 🧬 自进化 | 新闻源权重自适应 | 🟢 P2 | 自动识别哪个源更准 |
| 🧬 自进化 | Prompt 效果 A/B | 🟢 P3 | 数据驱动 prompt 迭代 |

---

## 3. 数据源增强

### 3.1 当前 vs 目标

| 市场 | 当前 | 目标 |
|------|------|------|
| A 股 | 东方财富新闻 (akshare) | 东方财富 + 新浪财经 + 雪球热帖 |
| 港股 | 东方财富新闻 (akshare) | 东方财富 + 新浪港股 + (可选) Yahoo Finance |
| 美股 | yfinance (不稳定) | yfinance + Alpha Vantage / Finnhub |

### 3.2 新增数据源

#### 3.2.1 新浪财经新闻（A 股 + 港股）

```python
# 方案：爬取新浪财经个股新闻页
# A股: https://vip.stock.finance.sina.com.cn/corp/go.php/vCB_AllNewsStock/symbol/{code}.phtml
# 港股: https://finance.sina.com.cn/stock/hkstock/
```

- **优势**：免费、稳定、更新快、覆盖公告+新闻
- **劣势**：需解析 HTML，无结构化 API
- **实现**：`requests` + `BeautifulSoup`，10-15 条/次

#### 3.2.2 雪球热帖 / 讨论（A 股 + 港股）

```python
# 方案：雪球个股页 API
# https://xueqiu.com/statuses/search.json?count=10&comment=0&symbol={code}&type=11
```

- **优势**：反映散户情绪、讨论热度
- **劣势**：需 cookie 模拟登录，噪音大
- **实现**：获取热帖标题 + 回复数 → 热度指标，不做深度情感分析

#### 3.2.3 Alpha Vantage News（美股备选）

```python
# 方案：Alpha Vantage News API
# https://www.alphavantage.co/query?function=NEWS_SENTIMENT&tickers={symbol}&apikey={key}
```

- **优势**：结构化数据，自带情感分
- **劣势**：免费 25次/天，需注册
- **实现**：作为 yfinance 的备选 / 补充

### 3.3 新闻去重策略

```python
def deduplicate(news_items: list[NewsItem]) -> list[NewsItem]:
    """
    基于标题相似度去重：
    1. 提取标题的关键实体（公司名、事件关键词）
    2. 计算 Jaccard 相似度 / 编辑距离
    3. 相似度 > 0.7 视为重复，保留发布时间最早的（或来源最权威的）
    """
```

### 3.4 多源并发采集

```python
async def fetch_all_sources(symbol, market, days):
    """并行从多个源采集，任一成功即可，最终合并去重"""
    tasks = []
    
    if market in ("A", "HK"):
        tasks.append(fetch_from_eastmoney(symbol, days))   # 主力
        tasks.append(fetch_from_sina(symbol, days))        # 补充
        tasks.append(fetch_from_xueqiu(symbol, days))      # 情绪
    
    if market == "US":
        tasks.append(fetch_from_yfinance(symbol, days))
        tasks.append(fetch_from_alphavantage(symbol, days))
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    all_items = []
    sources_used = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.warning(f"源 {i} 失败: {result}")
        elif result:
            all_items.extend(result)
            sources_used.append(...)
    
    # 去重 + 按时间排序
    return deduplicate_and_sort(all_items), sources_used
```

### 3.5 数据源可信度评级

| 来源 | 可信度 | 说明 |
|------|--------|------|
| 官方公告（交易所/证监会） | ⭐⭐⭐⭐⭐ (1.0) | 证监会指定披露媒体 |
| 权威财经媒体（财新/第一财经/21世纪） | ⭐⭐⭐⭐ (0.9) | 一线财经媒体 |
| 券商研报 | ⭐⭐⭐⭐ (0.8) | 有利益关联可能，但数据专业 |
| 综合财经门户（东方财富/新浪） | ⭐⭐⭐ (0.7) | 转载为主，有编辑审核 |
| 自媒体 / 雪球用户 | ⭐⭐ (0.4) | 信息质量参差不齐 |
| 来源未知 | ⭐ (0.3) | 无法验证 |

→ 在预处理时计算每条新闻的**加权可信度**，作为 LLM 输入的一部分。

---

## 4. 新闻预处理管线

### 4.1 管线流程

```
原始新闻列表 (30-50条)
        │
        ▼
┌──────────────────┐
│ ① 相关度评分过滤  │  → 剔除与标的无关的新闻（如同行业其他公司）
│   规则 + 关键词   │
└────────┬─────────┘
         │  (保留 15-25 条)
         ▼
┌──────────────────┐
│ ② 情感预标注      │  → 每条新闻打上 sentiment 标签
│   规则 + 微调模型 │     positive / negative / neutral
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ ③ 事件分类        │  → 财报 / 政策 / 并购 / 传闻 / 行业 / 其他
│   关键词匹配      │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ ④ 时间衰减加权    │  → 今天的权重=1.0，3天前=0.7，7天前=0.3
│   指数衰减函数    │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ ⑤ 结构化摘要      │  → 生成紧凑的预处理结果，喂给 LLM
│   聚合统计        │
└──────────────────┘
```

### 4.2 ① 相关度评分

```python
# 方案：规则 + 关键词匹配

STOCK_KEYWORDS = {
    "000001": ["平安银行", "平安", "平银"],
    "0700":   ["腾讯", "Tencent", "微信", "WeChat", "王者荣耀", "QQ"],
}

def relevance_score(news: NewsItem, symbol: str) -> float:
    """
    返回 0.0 ~ 1.0
    - 标题含标的名称/代码: +0.5
    - 正文含标的名称/代码: +0.3
    - 标题含行业关键词但非本公司: -0.4 (可能是行业新闻)
    - 来自官方公告: +0.2
    """
    score = 0.0
    
    # 标题匹配（最重要）
    keywords = get_keywords(symbol)
    title_hits = sum(1 for kw in keywords if kw in news.title)
    if title_hits > 0:
        score += 0.5
    
    # 正文匹配
    summary_hits = sum(1 for kw in keywords if kw in news.summary)
    if summary_hits > 0:
        score += min(0.3, summary_hits * 0.1)
    
    # 官方来源加分
    if news.source_type == "official":
        score += 0.2
    
    return min(1.0, score)

# 阈值: 保留 score >= 0.4 的新闻
```

### 4.3 ② 情感预标注

**两级方案**：

```python
# Level 1: 规则匹配（快速、免费）
SENTIMENT_RULES = {
    "positive": [
        "超预期", "大增", "突破", "利好", "增长", "创新高",
        "中标", "订单", "分红", "回购", "增持", "升级",
        "beat", "upgrade", "outperform", "buy",
    ],
    "negative": [
        "低于预期", "下滑", "暴跌", "利空", "亏损", "创新低",
        "违规", "处罚", "减持", "诉讼", "退市", "警示",
        "miss", "downgrade", "underperform", "sell",
    ],
}

def rule_based_sentiment(title: str, summary: str) -> Optional[str]:
    """返回 'positive' / 'negative' / None（无法判断则 None，交给模型）"""
    text = title + summary
    pos = sum(1 for kw in SENTIMENT_RULES["positive"] if kw in text)
    neg = sum(1 for kw in SENTIMENT_RULES["negative"] if kw in text)
    
    if pos > neg and pos >= 2:
        return "positive"
    elif neg > pos and neg >= 2:
        return "negative"
    elif pos == neg:
        return "neutral"
    return None  # 交给 LLM 判断

# Level 2: 小模型补充（可选，有成本）
# 使用 finbert-tone 或类似的金融情感模型
# 仅对 Level 1 返回 None 的新闻使用
```

→ 规则标注的置信度比 LLM 更高（因为规则不会"看走眼"），可以减少 LLM 幻觉。

### 4.4 ③ 事件分类

```python
EVENT_CATEGORIES = {
    "earnings":    ["财报", "业绩", "营收", "利润", "净利润", "EPS", "earnings"],
    "policy":      ["政策", "监管", "发改委", "工信部", "央行", "regulation"],
    "corp_action": ["回购", "分红", "增持", "减持", "并购", "重组", "收购"],
    "rumor":       ["传闻", "传言", "据传", "或", "可能", "rumor", "speculation"],
    "industry":    ["行业", "赛道", "竞品", "市场份额"],
    "rating":      ["评级", "目标价", "上调", "下调", "研报", "target price"],
    "other":       [],
}
```

→ 分类后 LLM 可以按类别组织分析，避免"混在一起说不清楚"。

### 4.5 ④ 时间衰减权重

```python
import math

def time_decay_weight(publish_date: datetime, reference_date: datetime, half_life_days: int = 3):
    """
    指数衰减：每 half_life_days 天权重减半
    
    - 今天: 1.0
    - 3天前: 0.5
    - 6天前: 0.25
    - 9天前: 0.125
    """
    days_diff = (reference_date - publish_date).days
    if days_diff <= 0:
        return 1.0
    return math.exp(-math.log(2) * days_diff / half_life_days)
```

### 4.6 ⑤ 结构化摘要输出

预处理完成后，生成如下结构喂给 LLM：

```json
{
  "symbol": "0700",
  "company": "腾讯控股",
  "fetch_time": "2026-07-03 10:30:00",
  "sources": ["eastmoney", "sina", "xueqiu"],
  "total_fetched": 35,
  "after_dedup": 28,
  "after_relevance_filter": 18,
  
  "sentiment_stats": {
    "positive": 7,
    "negative": 4,
    "neutral": 7,
    "weighted_positive_score": 12.5,   // 时间衰减+可信度加权
    "weighted_negative_score": 6.2
  },
  
  "category_breakdown": {
    "earnings": 2,
    "policy": 1,
    "corp_action": 3,
    "industry": 4,
    "rating": 2,
    "other": 6
  },
  
  "top_news": [
    {
      "title": "腾讯Q2营收同比增长15%，超市场预期",
      "source": "东方财富",
      "credibility": 0.8,
      "time": "2026-07-03",
      "time_weight": 1.0,
      "sentiment": "positive",
      "category": "earnings",
      "relevance": 1.0
    },
    // ... top 10 by composite score
  ],
  
  "hot_topics": ["业绩超预期", "AI布局加速", "游戏版号获批"],
  
  "anomaly_flags": {
    "sudden_volume_spike": false,      // 是否新闻量突然暴增
    "sentiment_divergence": true,      // 官方 vs 自媒体情绪是否分化
    "sentiment_divergence_detail": "机构评级偏正面，但雪球散户情绪偏空"
  }
}
```

**关键优化**：这个结构化摘要远比原始 20 条新闻列表更紧凑（约 1500 tokens vs 4000+ tokens），且 LLM 不需要自己做统计（减少出错）。

---

## 5. Agent 架构升级

### 5.1 当前：单 Pass 推理

```
数据 → [一个大 Prompt] → 结果
```

**问题**：
- 所有分析任务（情绪统计、事件评估、预期差、时效判断）挤在一个 prompt 里
- LLM 容易"跳跃推理"，情绪统计还没做完就开始谈预期差
- 输出质量不稳定，有时 reasoning 里情绪和事件混在一起

### 5.2 目标：多步链式推理（CoT）

```python
class NewsAnalyst(BaseAgent):
    """升级版：五步链式推理"""
    
    async def analyze(self, data: dict, context: dict) -> AnalysisResult:
        """覆盖基类的 analyze()，实现多步推理"""
        
        # Step 1: 信号提取
        signals = await self._step_extract_signals(data, context)
        
        # Step 2: 影响评估
        impacts = await self._step_assess_impacts(signals, data, context)
        
        # Step 3: 综合判断
        synthesis = await self._step_synthesize(signals, impacts, data, context)
        
        # Step 4: 反思校验
        reflection = await self._step_reflect(synthesis, data, context)
        
        # Step 5: 置信度校准
        calibrated = await self._step_calibrate(synthesis, reflection, data, context)
        
        return calibrated
```

### 5.3 各步骤详解

#### Step 1: 信号提取 (Signal Extraction)

```
输入: 结构化新闻摘要
输出: 关键信号列表，每条信号包含：
  - 信号描述
  - 方向 (+/-)
  - 强度 (1-5)
  - 持续性 (短期/中期/长期)
  - 确定性 (高/中/低)
```

**Prompt 片段**：
```
你是信息提取专家。从以下新闻中提取对股价有实质影响的关键信号。
不要评估影响大小，只提取"发生了什么"。

输出 JSON:
{
  "signals": [
    {
      "description": "Q2营收超预期15%",
      "direction": "positive",
      "strength": 4,
      "persistence": "中期",
      "certainty": "高",
      "source_news_index": [0, 3]  // 引用哪几条新闻
    }
  ],
  "noise_discarded": ["某分析师个人观点...", "无关行业动态..."]
}
```

#### Step 2: 影响评估 (Impact Assessment)

```
输入: Step 1 的信号列表 + 标的上下文
输出: 每个信号对股价的预估影响
```

**评估维度**：
- **直接影响**：信号本身对估值/盈利的改变程度
- **预期差**：市场是否已提前定价
- **传导链**：是否会引发连锁反应（如业绩超预期 → 机构上调评级 → 被动资金流入）
- **对冲因素**：是否有反向信号削弱影响

#### Step 3: 综合判断 (Synthesis)

```
输入: Step 2 的影响评估
输出: direction + magnitude + confidence + reasoning + key_factors + risks
```

将正负信号的影响量化为：

```python
# 伪代码
net_impact = sum(s.weight * s.impact_score for s in signals)
# 映射到 magnitude
if net_impact > 0.5:
    direction = "bullish"
    magnitude = (net_impact * 0.5, net_impact * 1.5)  # 区间化
elif net_impact < -0.5:
    direction = "bearish"
    magnitude = (net_impact * 1.5, net_impact * 0.5)
else:
    direction = "neutral"
    magnitude = (-net_impact, net_impact)
```

#### Step 4: 反思校验 (Reflection / Devil's Advocate)

```
你现在是"魔鬼代言人"，你的任务是找出上面分析中的漏洞：

1. 有没有忽略的反向信号？
2. 信号解读有没有其他可能？（如"营收增长"可能是"一次性收益"）
3. 有没有被新闻情绪裹挟，忽略了基本面？
4. 这个标的近期是否处于特殊状态（停牌、重大重组、涨跌停）？
5. 数据源有没有系统性偏差？（如东方财富偏多）

输出: 风险点列表 + 如果有重大漏洞，修正建议
```

#### Step 5: 置信度校准 (Confidence Calibration)

```
输入: 综合判断 + 反思结果 + 历史准确率数据
输出: 校准后的 confidence
```

**校准逻辑**：
```python
def calibrate_confidence(raw_confidence, data_quality, history_accuracy):
    """
    raw_confidence:     LLM 原始判断 0~1
    data_quality:       数据质量评分 0~1（新闻数量、来源可靠性、时效性）
    history_accuracy:   该 Agent 历史上类似 case 的准确率
    
    calibrated = raw_confidence * data_quality * history_factor
    """
    # 数据质量惩罚
    if news_count < 5:
        data_quality *= 0.5
    if "unavailable" in sources:
        data_quality *= 0.3
    
    # 历史准确率因子
    # 从 PredictionStore 读取该 Agent 的方向准确率
    if history_dir_acc > 0.6:
        history_factor = 1.0  # 历史表现好，置信度不折损
    elif history_dir_acc > 0.5:
        history_factor = 0.9
    else:
        history_factor = 0.7  # 历史表现差，打折
    
    # 信号一致性惩罚
    if sentiment_divergence:  # 正负面信号势均力敌
        data_quality *= 0.7
    
    return min(raw_confidence * data_quality * history_factor, 0.95)
```

### 5.4 整体推理流程对比

| 维度 | 当前（单 Pass） | 目标（多步 CoT） |
|------|---------------|-----------------|
| LLM 调用次数 | 1 次 | 5 次（或 3 次合并版） |
| 预计耗时 | 10-25s | 25-50s（可优化到 15-30s 合并版） |
| 可解释性 | 一段 reasoning | 5 段分步 reasoning，可追溯 |
| 情绪统计 | LLM 自己数，容易错 | 预处理已算好，LLM 只需验证 |
| 盲点检测 | 无 | 有魔鬼代言人 |
| 置信度 | LLM "感觉" | 数据驱动校准 |

### 5.5 合并优化版（平衡质量与速度）

如果将 Step 1+2 合并、Step 3+4+5 合并：

```
Step A: 信号提取 + 影响评估（1 次 LLM 调用）
Step B: 综合判断 + 反思 + 校准（1 次 LLM 调用）
```

→ 2 次 LLM 调用，耗时增加 50%（约 15-35s），质量大幅提升。

---

## 6. Prompt 工程优化

### 6.1 当前 Prompt 的不足

| 问题 | 说明 |
|------|------|
| 无 few-shot | 纯文字描述，LLM 对"好的输出"缺乏具体参照 |
| 无市场区分 | A 股和港股新闻的影响机制不同（A 股政策驱动 > 业绩驱动） |
| 无时间维度区分 | 短期看情绪、中期看趋势、长期看基本面——新闻分析也应区分 |
| 置信度缺少锚点 | "confidence=0.65"没有参考系，LLM 随意给 |
| 输出格式指令弱 | JSON 格式要求不够强硬，偶有格式错误 |

### 6.2 Few-shot 示例

```python
NEWS_SYSTEM_PROMPT = """你是一个专业的财经新闻分析师...

## 输出示例

### 示例 1: 明显利好
输入: 腾讯Q2营收超预期15%，多家投行上调目标价
输出:
{
  "direction": "bullish",
  "magnitude": {"min_pct": 1.5, "max_pct": 4.0},
  "confidence": 0.72,
  "reasoning": "1) 情绪统计：5条正面（业绩超预期+评级上调），1条负面（成本上升担忧），整体偏乐观...",
  ...
}

### 示例 2: 信息矛盾
输入: 公司发布回购公告，但同时大股东减持
输出:
{
  "direction": "neutral",
  "magnitude": {"min_pct": -2.0, "max_pct": 2.0},
  "confidence": 0.45,
  "reasoning": "1) 情绪统计：回购（正面）与减持（负面）信号矛盾...",
  ...
}

### 示例 3: 无实质新闻
输入: 仅2条行业动态新闻，与本公司无直接关系
输出:
{
  "direction": "neutral",
  "magnitude": {"min_pct": -1.5, "max_pct": 1.5},
  "confidence": 0.25,
  "reasoning": "近期无与本公司直接相关的重大新闻...",
  ...
}
"""
```

### 6.3 市场区分 Prompt

```python
# A 股新闻分析器 prompt（政策权重高）
A_SHARE_NEWS_APPENDIX = """
## A股特色分析注意
- 政策信号 > 业绩信号：A股对产业政策、监管变化的反应往往强于业绩
- "预期你的预期"：A股常有"利好出尽"现象——政策利好公告后反而可能回调
- 关注"北向资金"相关新闻（外资动向对A股情绪影响大）
- 注意"概念炒作"类新闻——短期情绪驱动，持续性和确定性都低
"""

# 港股新闻分析器 prompt（机构逻辑）
HK_SHARE_NEWS_APPENDIX = """
## 港股特色分析注意
- 机构定价：港股以机构投资者为主，对业绩/估值的反应更理性
- 流动性敏感：关注南向资金、港元汇率、美联储政策相关新闻
- 腾讯/阿里等权重股新闻会通过指数效应影响其他标的
- 注意做空报告的影响——港股做空机制成熟，负面新闻的下跌空间更大
"""

# 美股新闻分析器 prompt（多因子）
US_SHARE_NEWS_APPENDIX = """
## 美股特色分析注意
- 多因子驱动：财报 > 宏观（Fed）> 行业趋势 > 个股新闻
- 期权到期日、做市商行为等短期技术因素可能与新闻形成共振
- 盘前/盘后新闻对次日开盘价影响大，但盘中可能反转
- 注意 short squeeze / gamma squeeze 等极端情况
"""
```

### 6.4 置信度锚定指引

```python
CONFIDENCE_ANCHORS = """
## 置信度(confidence)校准指引

不要随便给 0.6-0.7！按以下标准：

| confidence | 含义 | 何时使用 |
|------------|------|---------|
| 0.85-0.95 | 几乎确定 | 多重独立信号一致指向同一方向，数据充足（10+条高相关新闻），无明显反向信号 |
| 0.70-0.84 | 较有把握 | 主信号明确，有少量杂音但不影响判断，有 5+ 条相关新闻 |
| 0.55-0.69 | 中等把握 | 信号偏多/偏空但不强烈，或正负信号混杂但有一方略占优 |
| 0.40-0.54 | 不太确定 | 信号矛盾，正负力量相当，或新闻数量少（<5条） |
| 0.25-0.39 | 很弱信号 | 只有间接/边缘新闻，或全是知识库信息无实时数据 |
| 0.10-0.24 | 几乎无信号 | 无相关新闻，纯猜测 |
| <0.10 | 不可用 | 数据源完全不可用（仅知识库），应设为 neutral |
"""
```

---

## 7. 自进化机制

### 7.1 核心思路

新闻分析师每次预测都会被 `PredictionStore` 记录并在事后验证。利用这些数据：

```
预测 → 存储 → 事后验证 → 更新统计 → 反馈到下次预测
```

### 7.2 准确率追踪（已有基础，需增强）

当前 `PredictionStore` 已支持按 Agent 统计：

```sql
-- 已有表 accuracy_stats
agent_name, timeframe, total_predictions, direction_accuracy, 
magnitude_accuracy, avg_confidence, avg_error_pct
```

**增强点**：
1. 增加**按新闻源**的准确率统计（东方财富 vs 新浪 vs 雪球 → 哪个源更有预测力？）
2. 增加**按新闻情绪**的统计（当情绪统计偏正面时，实际准确率多少？）
3. 增加**置信度校准曲线**（LLM 说 0.7 时，实际准确率是多少？校准后应该给多少？）

### 7.3 置信度校准

```python
class ConfidenceCalibrator:
    """基于历史数据的置信度校准器"""
    
    def __init__(self, prediction_store: PredictionStore):
        self.store = prediction_store
    
    def get_calibrated_confidence(self, agent_name: str, raw_conf: float) -> float:
        """
        读取历史数据，返回校准后的置信度
        
        例如：
        - LLM 给 0.7, 历史上 0.7 的预测准确率 55% → 校准到 0.55
        - LLM 给 0.5, 历史上 0.5 的预测准确率 48% → 校准到 0.48
        - LLM 给 0.9, 历史上 0.9 的预测准确率 72% → 校准到 0.72
        """
        stats = self.store.get_accuracy_stats(agent_name=agent_name)
        
        if stats["total"] < 10:
            # 样本太少，不校准
            return raw_conf
        
        # 简单校准: 向历史平均准确率回归
        hist_acc = stats["direction_accuracy"]
        calibrated = raw_conf * 0.6 + hist_acc * 0.4
        
        return round(calibrated, 2)
```

**进阶方案（样本积累后）**：构建置信度桶（0-0.1, 0.1-0.2, ...），计算每个桶的实际准确率，用 isotonic regression 做校准。

### 7.4 新闻源权重自适应

```python
class SourceWeightManager:
    """根据各新闻源的历史预测准确率，动态调整源的可信度权重"""
    
    def get_source_weights(self, agent_name: str) -> dict:
        """
        Returns:
            {"eastmoney": 0.8, "sina": 0.75, "xueqiu": 0.55}
        """
        # 从 PredictionStore 读取：当新闻主要来自某源时，预测准确率如何？
        # 关联 agent_results 和 news source 数据
        
        # 初始权重（先验）
        default_weights = {
            "official": 1.0,
            "eastmoney": 0.8,
            "sina": 0.75,
            "xueqiu": 0.5,
            "yfinance": 0.7,
        }
        
        # TODO: 积累足够数据后，用贝叶斯更新权重
        return default_weights
```

### 7.5 失败案例分析

```python
class NewsAnalysisReviewer:
    """分析预测失败的原因"""
    
    def analyze_failure(self, prediction_id: str):
        """
        对于方向判断错误的预测，分析：
        1. 新闻信号是否确实指向了那个方向？（信号提取没问题）
           → 如果是，说明市场不按新闻走 → 降低新闻维度在 aggregator 的权重
        2. 新闻信号是否被误读了？（信号解读有问题）
           → 改进 prompt，增加对应的 few-shot
        3. 是否有遗漏的重大新闻？（数据源问题）
           → 检查其他源是否有但没抓到
        4. 是否有不可预见的黑天鹅？
           → 标记为正常失败，不调整
        """
```

### 7.6 自进化数据流

```
┌─────────────────────────────────────────────────────────┐
│                    自进化数据闭环                        │
│                                                         │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐          │
│  │ 预测时刻  │    │ 验证时刻  │    │ 反馈时刻  │          │
│  │          │    │          │    │          │          │
│  │ 记录:    │    │ 记录:    │    │ 更新:    │          │
│  │ · 新闻源 │───▶│ · 实际涨跌│───▶│ · 源权重 │          │
│  │ · 情绪分布│   │ · 方向对错│    │ · 校准曲线│          │
│  │ · 置信度 │    │ · 幅度误差│    │ · Prompt │          │
│  │ · LLM输出│    │          │    │ · few-shot│          │
│  └──────────┘    └──────────┘    └──────────┘          │
│                                                         │
│  周期: 预测后 1周/1月/1季 验证                           │
└─────────────────────────────────────────────────────────┘
```

---

## 8. 质量保障体系

### 8.1 输出校验增强

当前只有 `BaseAgent._parse_llm_response` 的基本 JSON 解析校验。需要增强：

```python
class NewsResultValidator:
    """新闻分析师输出专项校验"""
    
    def validate(self, result: AnalysisResult, data: dict) -> list[str]:
        issues = []
        
        # 1. 方向与情绪统计一致性检查
        sentiment = data.get("sentiment_stats", {})
        if result.direction == "bullish" and sentiment.get("weighted_negative_score", 0) > sentiment.get("weighted_positive_score", 0):
            issues.append(f"方向看涨但加权负面得分({sentiment['weighted_negative_score']})高于正面({sentiment['weighted_positive_score']})，请检查")
        
        # 2. 置信度与数据质量一致性
        if result.confidence > 0.6 and data.get("news_count", 0) < 3:
            issues.append(f"高置信度({result.confidence})但新闻数量少({data['news_count']})，可能过度自信")
        
        if result.confidence > 0.7 and data.get("news_source") == "unavailable":
            issues.append("数据不可用状态下不应有高置信度")
        
        # 3. Reasoning 完整性检查
        required_sections = ["情绪统计", "事件", "综合"]
        for section in required_sections:
            if section not in result.reasoning:
                issues.append(f"reasoning 缺少'{section}'相关内容")
        
        # 4. 矛盾检测
        if result.direction == "bullish" and len(result.risks) == 0:
            issues.append("看涨但未列出任何风险——建议至少列出一个潜在风险")
        
        # 5. Magnitude 合理性
        if result.magnitude:
            if result.magnitude.min_pct > 10 or result.magnitude.max_pct > 10:
                issues.append(f"幅度区间过大(>{10}%)，新闻驱动的单方向变动通常不超过 5-8%")
            if result.direction == "bullish" and result.magnitude.min_pct >= 0 and result.magnitude.max_pct > 8:
                issues.append(f"单方向看涨幅度 >8% 需特别强的证据支持")
        
        return issues
```

### 8.2 幻觉检测

```python
def check_hallucination(reasoning: str, news_data: dict) -> list[str]:
    """
    检测 LLM 是否在新闻中"脑补"了不存在的信息
    """
    hallucinations = []
    
    # 提取 reasoning 中引用的"事实陈述"
    # 如 "公司宣布了50亿回购计划" 
    # 在 news_items 中搜索是否有匹配
    
    # 简单规则：
    # - 如果 reasoning 提到具体数字（金额、百分比）但新闻中没有 → 可能幻觉
    # - 如果 reasoning 提到"据报道"/"据公告"但新闻中没有对应来源 → 可能幻觉
    
    return hallucinations
```

### 8.3 特殊情况处理矩阵

| 情况 | 处理 | confidence 上限 |
|------|------|----------------|
| 新闻充足（10+条，多源） | 正常分析 | 0.90 |
| 新闻偏少（3-9条） | 正常分析，标注数量 | 0.70 |
| 新闻极少（1-2条） | 降低 confidence，标注 | 0.45 |
| 无新闻（实时不可用） | 知识库模式，标注 | 0.30 |
| 新闻矛盾（正负各半） | 标注分歧，neutral | 0.55 |
| 突发重大事件（检测到异常量） | 标注"异常波动"，高波动区间 | 0.65 |
| 仅谣言/传闻 | 标注"未证实"，低 confidence | 0.40 |
| 数据源全部不可用 | 降级 neutral | 0.15 |

### 8.4 测试策略

```python
# tests/test_news_analyst_v2.py

class TestNewsPreprocessing:
    def test_relevance_filter_removes_unrelated(self): ...
    def test_sentiment_rules_correctly_classify(self): ...
    def test_deduplication_merges_duplicates(self): ...
    def test_time_decay_weights(self): ...

class TestMultiStepReasoning:
    def test_signal_extraction_output_format(self): ...
    def test_impact_assessment_considers_expectation_gap(self): ...
    def test_reflection_identifies_contradictions(self): ...
    def test_confidence_calibrated_with_few_news(self): ...

class TestSelfEvolution:
    def test_calibrator_returns_lower_conf_when_history_poor(self): ...
    def test_source_weight_updates_with_data(self): ...

class TestQualityAssurance:
    def test_validator_catches_sentiment_direction_mismatch(self): ...
    def test_validator_flags_high_confidence_low_data(self): ...
    def test_hallucination_detector(self): ...
```

---

## 9. 实施路线图

### Phase A: 快速见效（1 周）🔴 P0

**目标**：最小改动，最大收益

| 任务 | 文件 | 工作量 |
|------|------|--------|
| ① 新闻预处理管线（去重+情感预标注+分类） | `src/data/news_fetcher.py` → 新增 `news_preprocessor.py` | 1 天 |
| ② 结构化摘要输出（替代原始新闻列表） | `src/data/news_fetcher.py` | 0.5 天 |
| ③ Few-shot + 置信度锚定 prompt | `src/prompts/news_prompts.py` | 0.5 天 |
| ④ 时间衰减权重 | `news_preprocessor.py` | 0.5 天 |
| ⑤ 输出校验增强 | `src/agents/news_analyst.py`（新增 validate） | 0.5 天 |
| ⑥ 测试 | `tests/test_news_analyst_v2.py` | 1 天 |

**预期效果**：
- 新闻质量 ↑：无关新闻减少 30-50%
- Token 消耗 ↓：结构化输入节省 40%
- 情绪统计准确率 ↑：规则预标注消除 LLM 数数错误
- JSON 格式错误 ↓：few-shot 减少解析失败

### Phase B: 架构升级（1-2 周）🟡 P1

| 任务 | 文件 | 工作量 |
|------|------|--------|
| ① 多源采集（新浪+雪球） | 新增 `src/data/news_sources/` | 2 天 |
| ② 两步 CoT 推理（信号提取→综合判断+反思） | `src/agents/news_analyst.py` | 1.5 天 |
| ③ 市场区分 prompt | `src/prompts/news_prompts.py` | 0.5 天 |
| ④ 来源可信度数据化 | `news_preprocessor.py` | 0.5 天 |
| ⑤ 置信度校准器 v1（读取历史） | 新增 `src/core/confidence_calibrator.py` | 1 天 |
| ⑥ 端到端测试 | `tests/` | 1 天 |

**预期效果**：
- 新闻覆盖率 +50%（多源）
- 推理质量 ↑（CoT + 反思）
- 置信度更诚实（历史校准）
- 散户情绪维度引入（雪球热度）

### Phase C: 自进化闭环（2-4 周）🟢 P2

| 任务 | 文件 | 工作量 |
|------|------|--------|
| ① 按新闻源的准确率追踪 | `prediction_store.py` schema 扩展 | 1 天 |
| ② 新闻源权重自适应 | 新增 `src/core/source_weight_manager.py` | 1 天 |
| ③ 失败案例自动分析 | 新增 `src/core/failure_analyzer.py` | 2 天 |
| ④ 历史案例 RAG 检索 | 复用 `case_retriever.py` | 1 天 |
| ⑤ 置信度校准 v2（isotonic regression） | `confidence_calibrator.py` | 1 天 |

### Phase D: 深度优化（1 月+）🟢 P3

| 任务 | 说明 |
|------|------|
| 小模型情感分析 | 用 finbert-tone 替代规则，提升情感标注精度 |
| Prompt A/B 测试框架 | 对比不同 prompt 在历史上的准确率 |
| 多模型辩论 | 同一新闻数据用 DeepSeek + Qwen 分别分析，取共识 |
| 异常新闻检测 | 自动识别"异常量级"的新闻爆发（可能预示重大事件） |

---

## 10. 附录：效果度量

### 10.1 关键指标

| 指标 | 当前（估算） | Phase A 目标 | Phase B 目标 | 度量方式 |
|------|------------|-------------|-------------|---------|
| 方向准确率 | ~50-55% | ≥55% | ≥60% | PredictionStore |
| 幅度命中率 | ~30-40% | ≥40% | ≥45% | PredictionStore |
| 置信度校准误差 | 未知 | ≤0.15 | ≤0.10 | | 预测置信度 - 实际准确率 | |
| 单次 Token 消耗 | ~4000 | ≤2500 | ≤3000（含CoT） | LLM API 统计 |
| JSON 解析成功率 | ~90% | ≥98% | ≥99% | 日志统计 |
| 数据可用率（A股） | ~85% | ≥95% | ≥98% | 多源降级统计 |
| 数据可用率（港股） | ~80% | ≥90% | ≥95% | 多源降级统计 |

### 10.2 实验设计

**回测验证**：
```bash
# 改进前后对比：同一历史区间，新老版本分别预测
python scripts/run_backtest.py -t 000001 --start 2026-01-01 --end 2026-06-30 --agent news
python scripts/run_backtest.py -t 000001 --start 2026-01-01 --end 2026-06-30 --agent news_v2
```

**A/B 对照**：
- 保留老版本 Agent（改名为 `新闻分析师-旧`），与新版本并行运行一周
- 对比两者的预测质量

---

## 附录 A：文件变更清单

### 需要修改的文件

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `src/data/news_fetcher.py` | 重构 | 拆分为多源采集 + 预处理管线 |
| `src/agents/news_analyst.py` | 重构 | 多步推理 + 校验增强 + 校准 |
| `src/prompts/news_prompts.py` | 重写 | Few-shot + 市场区分 + 锚定 |
| `config/agent_config.yaml` | 微调 | 如需新增 Agent 变体配置 |

### 需要新增的文件

| 文件 | 说明 |
|------|------|
| `src/data/news_preprocessor.py` | 去重、情感预标注、分类、时间衰减 |
| `src/data/news_sources/__init__.py` | 多源采集模块入口 |
| `src/data/news_sources/eastmoney.py` | 东方财富新闻源 |
| `src/data/news_sources/sina.py` | 新浪财经新闻源 |
| `src/data/news_sources/xueqiu.py` | 雪球热帖源 |
| `src/core/confidence_calibrator.py` | 置信度校准器 |
| `src/core/source_weight_manager.py` | 新闻源权重管理（Phase C） |
| `src/core/failure_analyzer.py` | 失败案例分析（Phase C） |
| `tests/test_news_preprocessing.py` | 预处理测试 |
| `tests/test_news_analyst_v2.py` | Agent v2 测试 |

### 不需要修改的文件

- `src/core/base_agent.py` — 接口不变，子类覆盖 `analyze()` 即可
- `src/core/orchestrator.py` — Agent 接口不变
- `src/core/result.py` — `AnalysisResult` 无需新增字段
- `src/core/llm_client.py` — 不变
- `src/data/prediction_store.py` — 已有足够字段（Phase C 需微调 schema）

---

## 附录 B：风险与应对

| 风险 | 概率 | 应对 |
|------|------|------|
| 多源采集被反爬/wall | 中 | 保留降级链，任一源成功即可；控制请求频率 |
| CoT 推理耗时过长 | 中 | 合并为 2 步；设 LLM 调用超时 |
| 置信度校准需足够样本 | 高 | 样本<10 时不校准；用先验锚定 |
| Few-shot 导致 prompt 过长 | 低 | 控制示例数量（3个），约占 500 tokens |
| 小模型情感分析准确率不够 | 中 | Phase A 用规则，Phase D 再考虑小模型 |

---

> 📌 **核心原则**：宁可少而准，不要多而杂。新闻分析师的竞争力不在于"看了多少条新闻"，而在于"能否从噪声中提取出真正影响股价的信号，并诚实地评估自己的把握有多大"。
