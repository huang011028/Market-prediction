# 🏢 公司前景分析师改进方案 — Round 1

> **版本**: v1.0 | **日期**: 2026-07-03 | **对标**: 新闻分析师 Round 1-2、宏观分析师 Round 1 已完成

---

## 目录

1. [现状评估](#1-现状评估)
2. [改进总览](#2-改进总览)
3. [数据源增强](#3-数据源增强)
4. [财务数据预处理管线](#4-财务数据预处理管线)
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
┌──────────────────┐     ┌─────────────────┐     ┌──────────────┐
│ FundamentalFetcher│ ──▶ │  单次 LLM 推理   │ ──▶ │ AnalysisResult│
│                  │     │  (一次 prompt)   │     │              │
│ A股: akshare财务  │     │                 │     │              │
│  + 腾讯PE/PB     │     │                 │     │              │
│ HK: 腾讯PE/市值   │     │                 │     │              │
│  + Sina52周      │     │                 │     │              │
│ US: yfinance(不稳定)│   │                 │     │              │
└──────────────────┘     └─────────────────┘     └──────────────┘
```

**一个步骤即可描述当前流程**：抓取估值/财务数据 → 塞进 prompt → LLM 输出结果。

### 1.2 优点（保留）

| 项目 | 说明 |
|------|------|
| ✅ 结构清晰 | 继承 BaseAgent，与其他 Agent 风格一致 |
| ✅ 多层降级 | akshare → 腾讯 → yfinance，有兜底 |
| ✅ 数据组织有序 | `to_agent_dict()` 输出分 financials/valuation/analyst 三大块 |
| ✅ 异常处理 | 超时/异常有 fallback，不阻塞整体流程 |
| ✅ 缺数据标注 | 缺失字段记录在 `missing_fields`，LLM 会降低 confidence |
| ✅ 数据截断保护 | >8000 字符截断，避免超 token |
| ✅ 分析框架完整 | Prompt 覆盖盈利能力→估值→成长性→机构观点→特殊因子 |

### 1.3 核心问题（需要解决）

#### 🔴 严重问题

| # | 问题 | 现象 | 影响 |
|---|------|------|------|
| 1 | **港股财务数据完全缺失** | 港股只有 PE/PB/市值，无营收/利润/ROE/毛利率 | 公司前景分析沦为"只看估值"，无法评估盈利能力，置信度长期停留在 30-45% |
| 2 | **财务数据维度单一** | 仅最新一期的营业收入/利润/ROE/EPS | 无趋势数据（连续4-8季度），无法判断加速/减速；无现金流/负债/运营效率指标 |
| 3 | **无历史估值分位** | PE/PB 只有一个当前值，没有 3-5 年历史分位 | 无法回答"当前 PE 在历史上算贵还是便宜"——这是基本面分析最核心的判断依据 |
| 4 | **单 pass 推理** | 一次 prompt 完成盈利+估值+成长+机构评估 | 四个不同维度的分析挤在一起，LLM 容易跳跃或遗漏 |
| 5 | **无同业对标** | 不知道同行业其他公司的 PE/ROE/增速 | 一个公司 PE=20 无法判断贵贱，需要和同行对比 |

#### 🟡 中等问题

| # | 问题 | 现象 | 影响 |
|---|------|------|------|
| 6 | **无 few-shot 示例** | Prompt 纯文字描述 | LLM 对"好的基本面分析输出"缺乏具体参照，质量波动大 |
| 7 | **无市场区分** | A/HK/US 用同一套 prompt | A 股重政策和成长性、港股重分红和现金流、美股重回购和资本效率——分析框架应有差异 |
| 8 | **机构评级数据薄弱** | AKShare 财务摘要不含机构评级，yfinance 部分有但有偏 | 缺失重要参考维度 |
| 9 | **无自评/反思** | 输出完就结束 | 没有"我的分析有什么盲点？"这一步 |
| 10 | **置信度未校准** | confidence 的含义不明确 | LLM 给 0.4 和 0.6 的区别是什么？历史上准确率是多少？ |
| 11 | **A股财务数据滞后** | akshare 的 `stock_financial_abstract_ths` 只到最近报告期 | 如果今天是 2026-07-03，可能最新数据还是 Q1（3月底），对于半年报窗口期无关 |

#### 🟢 轻微问题

| # | 问题 | 现象 | 影响 |
|---|------|------|------|
| 12 | 无 PEG 计算 | Prompt 提到 PEG 但数据中没有 | PE / 增速 = PEG，但增速数据可能缺失导致无法计算 |
| 13 | 无股息贴现/自由现金流分析 | 价值投资的核心方法完全无法使用 | 分析停留在"看数字"而非"算价值" |
| 14 | 无行业生命周期判断 | Prompt 问到"行业景气度"但没有行业数据支撑 | 分析结论依赖 LLM 知识库，可能与当前市场认知脱节 |

### 1.4 当前在系统中的权重与现状

```
Agent 权重（短期）：12%（排第四）
Agent 权重（中期）：22%（排第一！）
Agent 权重（长期）：32%（排第一！）
```

**核心矛盾**：公司前景分析师在中长期权重最高（28-32%），但其数据质量和分析深度严重不匹配这个权重。从样本报告看：

> "公司前景分析师：中性（-3%~+3%）。PE处于历史低位提供估值支撑，但财务数据缺失，缺乏短期业绩催化剂，市场情绪主导波动。**置信度较低**。"

置信度 40% 的中性判断，对汇总分析的边际贡献非常有限。**改进公司前景分析师的回报率极高——它直接影响中长期预测质量**。

---

## 2. 改进总览

### 2.1 目标架构

```
                    ┌──────────────────────────────────────────┐
                    │        📊 多源财务数据采集层               │
                    │                                          │
                    │   akshare财报  │  东方财富F10  │  腾讯行情  │
                    │  (A股主力)    │  (补充/替代)  │  (估值)   │
                    │  AASTOCKS    │  yfinance    │  (港股补充) │
                    │  (港股补充)   │  (美股/港股)  │           │
                    │      │            │            │          │
                    │      └────────────┼────────────┘          │
                    │           ▼       ▼       ▼              │
                    │      ┌──────────────────────┐            │
                    │      │   数据补全 + 合并     │            │
                    │      └──────────┬───────────┘            │
                    └─────────────────┼────────────────────────┘
                                      │
                    ┌─────────────────▼────────────────────────┐
                    │      🔧 财务数据预处理管线               │
                    │                                          │
                    │  ① 历史估值分位计算（3年/5年）           │
                    │  ② 财务趋势提取（4-8季度同比/环比）      │
                    │  ③ 同业对标数据获取（同行业PE/ROE）       │
                    │  ④ 综合评分卡生成（量化规则预打分）       │
                    │  ⑤ 数据质量评估 + 新鲜度标注             │
                    │  ⑥ 结构化摘要输出                       │
                    └─────────────────┬────────────────────────┘
                                      │
                    ┌─────────────────▼────────────────────────┐
                    │      🧠 多步推理引擎                     │
                    │                                          │
                    │  Step 1: 内在价值评估                    │
                    │     "这家公司的质量如何？"               │
                    │           │                              │
                    │  Step 2: 估值合理性判断                  │
                    │     "当前价格合理吗？"                   │
                    │           │                              │
                    │  Step 3: 催化/风险识别                  │
                    │     "股价会被什么推动/打压？"            │
                    │           │                              │
                    │  Step 4: 综合判断 + 反思                │
                    │     "我的分析有什么盲点？"               │
                    │           │                              │
                    │  Step 5: 置信度校准                     │
                    └─────────────────┬────────────────────────┘
                                      │
                    ┌─────────────────▼────────────────────────┐
                    │      📊 输出 + 自进化反馈                │
                    │                                          │
                    │  AnalysisResult → PredictionStore        │
                    │       │                                  │
                    │       └──→ 事后验证 → 更新统计          │
                    │                  → 校准 confidence     │
                    │                  → 优化 prompt/规则     │
                    └──────────────────────────────────────────┘
```

### 2.2 改进维度一览

| 维度 | 改进项 | 优先级 | 预期收益 |
|------|--------|--------|---------|
| 📡 数据源 | 港股财务数据补充（AASTOCKS/东方财富F10） | 🔴 P0 | 港股分析从"只看估值"升级为"基本面+估值" |
| 📡 数据源 | A股历史估值数据（PE/PB 3-5年序列） | 🔴 P0 | 实现历史分位判断——基本面分析核心能力 |
| 📡 数据源 | 同业对标数据获取 | 🟡 P1 | 从"孤立看公司"升级为"行业横向对比" |
| 📡 数据源 | 多季度财务趋势数据 | 🔴 P0 | 从"看一期"升级为"看趋势" |
| 🔧 预处理 | 评分卡预打分 | 🟡 P1 | 量化规则先打分，减少 LLM 主观偏差 |
| 🔧 预处理 | 数据质量+新鲜度评估 | 🟡 P1 | confidence 上限与数据质量自动挂钩 |
| 🧠 架构 | 三步 CoT 推理（质量→估值→催化） | 🔴 P0 | 推理深度 ↑，可解释性 ↑ |
| 🧠 架构 | 反思/自评环节 | 🟡 P1 | 识别盲点，减少过度自信 |
| 🧠 架构 | 历史案例检索（RAG） | 🟢 P2 | 利用历史规律 |
| 📝 Prompt | Few-shot 示例 + 置信度锚定 | 🔴 P0 | 输出质量稳定性 ↑ |
| 📝 Prompt | 市场区分 prompt | 🟡 P1 | A股政策逻辑/港股现金流逻辑/美股资本效率逻辑 |
| 🔬 质量 | 输出校验增强 | 🟡 P1 | 检测逻辑矛盾/数据不一致 |
| 🔬 质量 | 估值-盈利一致性检查 | 🟡 P1 | 高ROE+低PE=机会，低ROE+高PE=泡沫 |
| 🧬 自进化 | 准确率追踪 + 置信度校准 | 🟡 P1 | confidence 从"感觉"变成"统计" |
| 🧬 自进化 | Prompt 效果 A/B | 🟢 P3 | 数据驱动 prompt 迭代 |

---

## 3. 数据源增强

### 3.1 当前 vs 目标

| 维度 | 当前 | 目标 |
|------|------|------|
| A股财务 | akshare 单期财务摘要 | akshare 多期(8季度) + 东方财富F10补充 |
| A股估值 | 腾讯实时 PE/PB | 腾讯实时 + 历史 PE/PB 序列(3-5年) |
| 港股估值 | 腾讯 PE + Sina 52周 | 腾讯 PE + 52周位置计算 |
| 港股财务 | **无** | AASTOCKS/东方财富港股F10 爬取 |
| 美股数据 | yfinance 不定期 | yfinance + Alpha Vantage 备选 |
| 同业对标 | **无** | 同行业 PE/ROE 参考值 |
| 机构评级 | yfinance(部分) + 无 | 东方财富机构评级汇总 |

### 3.2 新增数据源

#### 3.2.1 港股财务数据 — AASTOCKS 爬取

```python
# 方案：爬取 AASTOCKS 港股财务页
# URL: https://www.aastocks.com/sc/stocks/analysis/company-fundamental/financial-ratios?symbol=00700
# 或: https://www.aastocks.com/sc/stocks/analysis/company-fundamental/profit-loss?symbol=00700

# 可获取数据：
# - 营收、利润、毛利率、净利率（多年度）
# - ROE、ROA
# - 每股盈利 EPS
# - 现金流（经营/投资/融资）
# - 负债率

# 实现：requests + BeautifulSoup, 解析 HTML 表格
```

- **优势**：免费、稳定、包含港股完整的财务数据
- **劣势**：需解析 HTML，需处理繁体/简体和单位差异
- **价值**：**这是优先级最高的改进**——让港股公司前景分析从"只看PE"升级为完整的财务分析

#### 3.2.2 港股财务数据备选 — 东方财富港股F10

```python
# 方案：东方财富港股 F10 页面
# URL: https://emweb.securities.eastmoney.com/PC_HKF10/NewFinanceAnalysis/Index?type=web&code=0700&color=w

# 或通过 akshare: 
# ak.stock_hk_financial_analysis_indicator(symbol="0700", indicator="按报告期")
# 注：此前网络限制不可用，当前网络环境需重试
```

#### 3.2.3 A股历史估值序列

```python
# 方案1：akshare 历史PE/PB
# ak.stock_a_indicator_lg(symbol="000001")  # 获取PE/PB历史序列

# 方案2：腾讯行情 + 历史K线反推
# 从K线数据中获取每日PE/PB (腾讯行情字段包含)
# 收集 3-5 年日频数据 → 计算历史分位

def calculate_pe_percentile(current_pe: float, pe_history: list[float]) -> float:
    """
    计算当前 PE 在历史序列中的百分位
    - 0% = 历史最低 (最便宜)
    - 100% = 历史最高 (最贵)
    - 返回 0.0 ~ 1.0
    """
    if not pe_history or current_pe is None:
        return None
    sorted_pe = sorted(pe_history)
    count_below = sum(1 for pe in sorted_pe if pe <= current_pe)
    return count_below / len(sorted_pe)
```

- **价值**：基本面分析的核心问题——"当前贵不贵"——需要量化答案

#### 3.2.4 同业对标数据

```python
# 方案：东方财富行业对比
# 从 industry_fetcher 的 INDUSTRY_REF 扩展为动态获取
# 或从 akshare 获取行业板块成分股的 PE/ROE 并计算中位数

# 简化方案：基于已知行业分类 + 行业参考值表
# 与行业对比分析师数据打通，共享 industry_data

def get_peer_comparison(symbol: str, industry: str, market: str) -> dict:
    """
    返回同业对标数据:
    {
        "peers": [
            {"name": "阿里巴巴", "pe": 18.5, "roe": 12.3, "revenue_growth": 8.2},
            {"name": "百度", "pe": 12.1, "roe": 8.5, "revenue_growth": 3.1},
            ...
        ],
        "peer_avg_pe": 16.8,
        "peer_avg_roe": 10.2,
        "peer_median_pe": 15.5,
    }
    """
```

#### 3.2.5 多季度财务趋势

```python
# 扩展 _fetch_a_share 中的 akshare 调用
# 当前只取 df.iloc[-1]（最新一期）
# 改进：取 df.iloc[-8:]（最近8个季度）

def extract_financial_trends(df) -> dict:
    """
    从 akshare 财务摘要 DataFrame 提取趋势
    """
    if df is None or df.empty:
        return {}
    
    recent = df.iloc[-8:]  # 最近8个季度
    latest = df.iloc[-1]
    prev_year = df.iloc[-5] if len(df) >= 5 else None  # 去年同期
    
    trends = {
        "revenue_series": [parse(v) for v in recent.get("营业总收入", [])],
        "profit_series": [parse(v) for v in recent.get("净利润", [])],
        "roe_series": [parse(v) for v in recent.get("净资产收益率", [])],
        "revenue_acceleration": None,  # 营收增速是否在加快
        "margin_trend": None,  # 利润率趋势：expanding/compressing/stable
        "earnings_quality": None,  # 营收增长但利润不增长 = 质量下降
    }
    
    # 计算趋势
    rev = trends["revenue_series"]
    if len(rev) >= 4 and all(r is not None for r in rev[-4:]):
        yoy_changes = []
        for i in range(len(rev)-4, len(rev)):
            if rev[i] and rev[i-4] and rev[i-4] > 0:
                yoy_changes.append((rev[i] / rev[i-4] - 1) * 100)
        if yoy_changes:
            trends["revenue_acceleration"] = yoy_changes[-1] - yoy_changes[0]
    
    return trends
```

### 3.3 数据获取策略（多源并发 + 降级链）

```
A股财务数据:
  akshare(主力) → 东方财富F10(备选) → LLM知识库(兜底)
  
港股财务数据:
  腾讯行情(PE/市值) → AASTOCKS(财务) → 东方财富港股F10(备选) → LLM知识库(兜底)

美股财务数据:
  yfinance(带重试) → Alpha Vantage(备选) → LLM知识库(兜底)

A股历史估值:
  akshare PE历史 → 腾讯日行情PE(从K线提取) → LLM知识库(兜底)
```

### 3.4 数据源可信度评级

| 数据类别 | 来源 | 可信度 | 说明 |
|---------|------|--------|------|
| A股财报 | akshare（同花顺源） | ⭐⭐⭐⭐ (0.9) | 权威但可能有1-2天滞后 |
| A股估值 | 腾讯实时行情 | ⭐⭐⭐⭐⭐ (1.0) | 实时准确 |
| 港股财务 | AASTOCKS | ⭐⭐⭐⭐ (0.85) | 权威港股数据，需解析 |
| 港股估值 | 腾讯实时行情 | ⭐⭐⭐⭐⭐ (1.0) | 实时准确 |
| 美股数据 | yfinance | ⭐⭐⭐ (0.7) | 部分字段不稳定 |
| 机构评级 | yfinance/东方财富 | ⭐⭐⭐ (0.75) | 有时过时 |
| 知识库补充 | LLM训练数据 | ⭐⭐ (0.4) | 标注"[知识库]" |

---

## 4. 财务数据预处理管线

### 4.1 管线流程

```
原始财务数据（多期 + 多源合并）
        │
        ▼
┌──────────────────┐
│ ① 历史估值分位    │  → 当前 PE/PB 在 3-5 年历史中的百分位
│   计算            │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ ② 财务趋势提取    │  → 4-8 个季度的营收/利润/ROE 趋势
│   加速/减速/拐点  │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ ③ 质量评分卡      │  → 量化规则预打分
│   (规则引擎)      │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ ④ 数据质量评估    │  → completeness_score + freshness_score
│   + 新鲜度标注    │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ ⑤ 结构化摘要输出  │  → 紧凑 JSON 喂给 LLM
│   聚合统计        │
└──────────────────┘
```

### 4.2 ① 历史估值分位

```python
@dataclass
class ValuationPercentile:
    """历史估值分位"""
    metric: str              # "PE" or "PB"
    current_value: float
    percentile_3yr: float     # 三年分位 0~1
    percentile_5yr: float     # 五年分位 0~1
    historical_low: float
    historical_high: float
    historical_median: float
    interpretation: str      # "处于3年30%分位，相对便宜"

def calculate_valuation_percentile(
    current_pe: float,
    pe_history_3yr: list[float],
    pe_history_5yr: list[float],
) -> ValuationPercentile:
    p3 = percentile_of_score(pe_history_3yr, current_pe)
    p5 = percentile_of_score(pe_history_5yr, current_pe)
    
    if p3 < 0.2:
        interp = f"处于3年{p3*100:.0f}%分位，显著低于历史中枢，相对便宜"
    elif p3 < 0.4:
        interp = f"处于3年{p3*100:.0f}%分位，低于历史中枢，估值合理偏低"
    elif p3 < 0.6:
        interp = f"处于3年{p3*100:.0f}%分位，处于历史中枢，估值合理"
    elif p3 < 0.8:
        interp = f"处于3年{p3*100:.0f}%分位，高于历史中枢，估值合理偏高"
    else:
        interp = f"处于3年{p3*100:.0f}%分位，显著高于历史中枢，相对偏贵"
    
    return ValuationPercentile(
        metric="PE",
        current_value=current_pe,
        percentile_3yr=p3,
        percentile_5yr=p5,
        historical_low=min(pe_history_5yr) if pe_history_5yr else None,
        historical_high=max(pe_history_5yr) if pe_history_5yr else None,
        historical_median=median(pe_history_5yr) if pe_history_5yr else None,
        interpretation=interp,
    )
```

### 4.3 ② 财务趋势提取

```python
@dataclass
class FinancialTrend:
    """财务趋势判断"""
    revenue_trend: str        # "accelerating" | "decelerating" | "stable" | "declining"
    profit_trend: str
    margin_trend: str         # "expanding" | "compressing" | "stable"
    roe_trend: str
    earnings_quality: str     # "improving" | "stable" | "deteriorating"
    key_inflection: Optional[str]  # 是否存在拐点信号

def analyze_financial_trend(revenue_series: list, profit_series: list,
                            roe_series: list) -> FinancialTrend:
    """
    分析多季度财务数据趋势
    规则引擎，不依赖 LLM
    """
    trend = FinancialTrend(
        revenue_trend=judge_trend(revenue_series),
        profit_trend=judge_trend(profit_series),
        margin_trend=judge_margin_trend(revenue_series, profit_series),
        roe_trend=judge_trend(roe_series),
        earnings_quality=judge_earnings_quality(revenue_series, profit_series),
        key_inflection=detect_inflection(revenue_series, profit_series),
    )
    return trend

def judge_trend(series: list[float]) -> str:
    """判断序列趋势"""
    clean = [s for s in series if s is not None]
    if len(clean) < 3:
        return "insufficient_data"
    
    recent = clean[-3:]
    if all(recent[i] > recent[i-1] for i in range(1, len(recent))):
        # 连续增长，检查增速
        if len(clean) >= 6:
            early_growth = (clean[-4] / clean[-6] - 1) if clean[-6] and clean[-6] != 0 else 0
            late_growth = (clean[-1] / clean[-3] - 1) if clean[-3] and clean[-3] != 0 else 0
            if late_growth > early_growth * 1.2:
                return "accelerating"
        return "growing"
    elif all(recent[i] < recent[i-1] for i in range(1, len(recent))):
        return "declining"
    else:
        return "fluctuating"
```

### 4.4 ③ 质量评分卡

```python
def generate_quality_scorecard(data: dict) -> dict:
    """
    基于量化规则生成公司质量评分卡
    满分 100，分 4 个维度
    
    让 LLM 在已有量化分数上做判断，而不是凭直觉打分
    """
    score = 0
    breakdown = {}
    
    # 维度1: 盈利能力 (30分)
    roe = data.get("roe_pct")
    net_margin = data.get("net_margin_pct")
    profit_score = 0
    if roe and roe != "N/A":
        if roe > 20: profit_score += 18
        elif roe > 15: profit_score += 14
        elif roe > 10: profit_score += 10
        elif roe > 5: profit_score += 5
    if net_margin and net_margin != "N/A":
        if net_margin > 20: profit_score += 12
        elif net_margin > 10: profit_score += 9
        elif net_margin > 5: profit_score += 5
    score += profit_score
    breakdown["profitability"] = {"score": profit_score, "max": 30}
    
    # 维度2: 成长性 (25分)
    rev_growth = data.get("revenue_yoy_pct")
    profit_growth = data.get("profit_yoy_pct")
    growth_score = 0
    if rev_growth and rev_growth != "N/A":
        if rev_growth > 30: growth_score += 15
        elif rev_growth > 15: growth_score += 12
        elif rev_growth > 5: growth_score += 8
        elif rev_growth > 0: growth_score += 4
        else: growth_score -= 3
    if profit_growth and profit_growth != "N/A":
        if profit_growth > 30: growth_score += 10
        elif profit_growth > 15: growth_score += 8
        elif profit_growth > 5: growth_score += 5
        elif profit_growth > 0: growth_score += 2
        else: growth_score -= 2
    score += max(0, growth_score)
    breakdown["growth"] = {"score": max(0, growth_score), "max": 25}
    
    # 维度3: 估值安全边际 (25分)
    pe = data.get("pe")
    pe_percentile = data.get("pe_percentile_3yr")  # 来自预处理
    valuation_score = 0
    if pe_percentile is not None and pe_percentile != "N/A":
        if pe_percentile < 0.2: valuation_score = 22  # 很便宜
        elif pe_percentile < 0.4: valuation_score = 16
        elif pe_percentile < 0.6: valuation_score = 10
        elif pe_percentile < 0.8: valuation_score = 5
        else: valuation_score = 0
    score += valuation_score
    breakdown["valuation"] = {"score": valuation_score, "max": 25}
    
    # 维度4: 财务健康 (20分)
    # 简化的健康检查
    health_score = 10  # 基础分
    trend = data.get("financial_trend", {})
    if trend.get("earnings_quality") == "improving": health_score += 5
    elif trend.get("earnings_quality") == "deteriorating": health_score -= 5
    if trend.get("revenue_trend") == "accelerating": health_score += 5
    elif trend.get("revenue_trend") == "declining": health_score -= 3
    score += max(0, health_score)
    breakdown["health"] = {"score": max(0, health_score), "max": 20}
    
    return {
        "total_score": min(100, max(0, score)),
        "rating": "excellent" if score >= 80 else "good" if score >= 60 else "average" if score >= 40 else "weak",
        "breakdown": breakdown,
    }
```

### 4.5 ④ 数据质量评估

```python
class DataQualityAssessor:
    """评估获取到的财务数据的质量"""
    
    FINANCIAL_FIELDS = [
        "latest_revenue", "latest_net_profit", "revenue_yoy",
        "profit_yoy", "gross_margin", "net_margin", "roe", "eps"
    ]
    VALUATION_FIELDS = ["pe", "pb", "market_cap", "dividend_yield"]
    
    def assess(self, data: dict) -> dict:
        financials = data.get("financials", {})
        valuation = data.get("valuation", {})
        
        # 完整度
        fin_filled = sum(1 for f in self.FINANCIAL_FIELDS 
                        if financials.get(f) not in (None, "N/A", ""))
        fin_total = len(self.FINANCIAL_FIELDS)
        
        val_filled = sum(1 for f in self.VALUATION_FIELDS 
                        if valuation.get(f) not in (None, "N/A", ""))
        val_total = len(self.VALUATION_FIELDS)
        
        completeness = (fin_filled + val_filled) / (fin_total + val_total)
        
        # 新鲜度 (A股报告期滞后判断)
        freshness = 1.0  # 默认最新
        # 如果有报告期信息，检查距今天数
        
        # 综合质量
        overall = completeness * 0.7 + freshness * 0.3
        
        return {
            "completeness": round(completeness, 2),
            "freshness": round(freshness, 2),
            "overall_quality": round(overall, 2),
            "financial_fields_filled": f"{fin_filled}/{fin_total}",
            "valuation_fields_filled": f"{val_filled}/{val_total}",
            "data_gaps": self._identify_gaps(financials, valuation),
            "confidence_ceiling": self._calculate_confidence_ceiling(overall),
        }
    
    def _calculate_confidence_ceiling(self, quality: float) -> float:
        """基于数据质量计算置信度上限"""
        if quality >= 0.8: return 0.85
        elif quality >= 0.6: return 0.70
        elif quality >= 0.4: return 0.55
        elif quality >= 0.2: return 0.40
        else: return 0.25
```

### 4.6 ⑤ 结构化摘要输出

预处理完成后，生成如下紧凑格式喂给 LLM：

```json
{
  "symbol": "0700",
  "company_name": "腾讯控股",
  "market": "HK",
  "analysis_date": "2026-07-03",
  
  "data_quality": {
    "overall": 0.82,
    "financial_fields_filled": "7/8",
    "valuation_fields_filled": "4/4",
    "confidence_ceiling": 0.85,
    "data_sources": ["tencent", "aastocks"],
    "notes": "港股财务数据来自AASTOCKS"
  },
  
  "valuation_analysis": {
    "current_pe": 15.7,
    "pe_percentile_3yr": 0.18,
    "pe_percentile_5yr": 0.25,
    "pe_interpretation": "处于3年18%分位，显著低于历史中枢",
    "current_pb": 3.2,
    "pb_percentile_3yr": 0.22,
    "historical_range_pe": "12.0 ~ 45.0",
    "historical_median_pe": 28.0,
    "vs_industry_pe": "行业均值22.0，折价28%"
  },
  
  "quality_scorecard": {
    "total": 72,
    "rating": "good",
    "profitability": {"score": 22, "max": 30, "note": "ROE 18%优秀"},
    "growth": {"score": 15, "max": 25, "note": "营收增速12%稳健"},
    "valuation": {"score": 20, "max": 25, "note": "PE分位18%，相对便宜"},
    "health": {"score": 15, "max": 20, "note": "盈利质量稳定"}
  },
  
  "financial_trend": {
    "revenue_trend": "stable_growth",
    "profit_trend": "accelerating",
    "margin_trend": "expanding",
    "roe_trend": "improving",
    "earnings_quality": "stable",
    "quarterly_data": {
      "revenue_yoy_series": [8, 10, 12, 15],
      "profit_yoy_series": [5, 8, 14, 22],
      "roe_series": [15, 16, 17, 18]
    }
  },
  
  "peer_comparison": {
    "industry": "互联网",
    "peer_avg_pe": 22.0,
    "peer_avg_roe": 14.0,
    "stock_pe_rank": "3/15 (从低到高)",
    "stock_roe_rank": "2/15 (从高到低)"
  },
  
  "key_metrics": {
    "revenue_yoy_pct": 12,
    "profit_yoy_pct": 22,
    "gross_margin_pct": 52,
    "net_margin_pct": 28,
    "roe_pct": 18,
    "eps": 17.5,
    "dividend_yield_pct": 2.8
  },
  
  "anomaly_flags": {
    "value_trap_warning": false,     // PE低但盈利下滑 = 价值陷阱
    "overvalued_warning": false,     // PE高且盈利增速放缓
    "earnings_momentum_positive": true,  // 盈利加速
    "estimate_revision": "upward"   // 机构盈利预测方向
  }
}
```

**关键优化**：预处理管线将原始数据加工为**洞察**（interpretation），而非让 LLM 自己"算数"。LLM 收到的是 "PE 处于 3 年 18% 分位" 而不是一个裸的 PE=15.7。

---

## 5. Agent 架构升级

### 5.1 当前：单 Pass 推理

```
数据(json) → [一个大 Prompt] → 结果(json)
```

**问题**：盈利能力(数字)→估值合理性(分位)→成长性(趋势)→机构观点(外部)四个维度的分析挤在一起，LLM 容易跳跃推理。

### 5.2 目标：多步链式推理（CoT）

```python
class FundamentalAnalyst(BaseAgent):
    """升级版：三步链式推理"""
    
    async def analyze(self, data: dict, context: dict) -> AnalysisResult:
        """覆盖基类的 analyze()，实现多步推理"""
        
        # Step 1: 内在价值评估
        value_assessment = await self._step_assess_intrinsic_quality(data, context)
        
        # Step 2: 估值合理性 + 催化/风险
        valuation_judgment = await self._step_judge_valuation(data, value_assessment, context)
        
        # Step 3: 综合判断 + 反思
        final_judgment = await self._step_synthesize_with_reflection(
            data, value_assessment, valuation_judgment, context
        )
        
        # Step 4: 置信度校准
        calibrated = self._calibrate_confidence(final_judgment, data)
        
        return calibrated
```

### 5.3 各步骤详解

#### Step 1: 内在价值评估 (Intrinsic Quality Assessment)

```
输入: 质量评分卡 + 财务趋势 + 同业对标
输出: 这家公司"好不好"的判断
```

**Prompt 片段**：
```
你是公司质量评估专家。你已经有了以下量化分析结果。

## 数据已为你算好，不要重新计算
- 质量评分: 72/100 (good)
- 营收趋势: 稳健增长(8%→10%→12%→15%)
- 利润趋势: 加速增长(5%→8%→14%→22%)
- ROE趋势: 持续改善(15%→16%→17%→18%)
- 同业ROE排名: 2/15

请基于这些数据回答：
1. 这家公司的商业模式竞争力如何？给出 1-5 分
2. 成长性是真实的（有营收支撑）还是仅仅成本削减？
3. 盈利能力改善的原因是什么？能否持续？

输出 JSON:
{
  "business_quality": 4,
  "quality_assessment": "简洁判断",
  "growth_quality": "real/mixed/cost_cutting",
  "competitive_advantage": "护城河描述",
  "sustainability": "可持续/不确定/不可持续"
}
```

#### Step 2: 估值合理性判断 (Valuation Judgment)

```
输入: 历史估值分位 + Step 1 质量评估 + 催化因素
输出: 当前价格"贵不贵"的判断 + 核心驱动力
```

**评估逻辑**：
```python
# 量化规则预判断（不依赖 LLM）
def pre_judge_valuation(data: dict) -> str:
    pe_pct = data.get("valuation_analysis", {}).get("pe_percentile_3yr")
    quality = data.get("quality_scorecard", {}).get("rating")
    trend = data.get("financial_trend", {})
    
    if pe_pct is not None and pe_pct < 0.3 and quality in ("good", "excellent"):
        if trend.get("earnings_quality") != "deteriorating":
            return "undervalued_quality"  # 好公司+便宜价 = 机会
        else:
            return "value_trap_candidate"  # 便宜但有原因
    
    if pe_pct is not None and pe_pct > 0.8:
        if quality == "excellent" and trend.get("profit_trend") == "accelerating":
            return "premium_justified"  # 贵但有道理
        else:
            return "overvalued"  # 贵且无道理
    
    return "fairly_valued"  # 价格合理
```

#### Step 3: 综合判断 + 反思 (Synthesis + Reflection)

```python
# Step 3a: 综合判断
PROMPT_STEP3A = """
## 综合判断

你是首席分析师。综合以下所有信息，给出最终判断：

1. 公司质量评估: {step1_result}
2. 估值合理性: {step2_result}
3. 关键催化剂: {catalysts}
4. 关键风险: {risks}

请判断:
- direction: 基于内在价值和估值差判断方向
- magnitude: 修复/压缩的空间有多大
- confidence: 你的把握程度（参考置信度锚定表）

## 3b: 反思

现在你是风控官。请检查上述分析的盲点：
1. 有没有周期性因素被忽略？（当前是行业景气高点还是低点？）
2. 管理层/治理风险有没有考虑？
3. 是否过度依赖历史数据而忽略了结构性变化？
4. 数据缺失最大的盲区是什么？
"""
```

#### Step 4: 置信度校准

```python
def calibrate_fundamental_confidence(
    raw_confidence: float,
    data_quality: dict,
    assessment_quality: dict,
    history_accuracy: Optional[float] = None,
) -> float:
    """
    基本面分析师置信度校准
    
    数据质量 ceiling + 评估一致性 check + 历史准确率回归
    """
    # 1. 数据质量 ceiling
    ceiling = data_quality.get("confidence_ceiling", 0.70)
    calibrated = min(raw_confidence, ceiling)
    
    # 2. 评估一致性惩罚
    if assessment_quality.get("steps_agree") is False:
        # Step 1 说好但 Step 2 说贵 → 分歧大 → 降低置信度
        calibrated *= 0.8
    
    # 3. 数据缺失盲区惩罚
    blind_spots = assessment_quality.get("blind_spots", [])
    if len(blind_spots) > 2:
        calibrated *= 0.85
    
    # 4. 历史准确率回归
    if history_accuracy is not None:
        calibrated = calibrated * 0.7 + history_accuracy * 0.3
    
    return round(min(calibrated, 0.95), 2)
```

### 5.4 合并优化版（平衡质量与速度）

```
Step A: 内在价值评估 + 估值判断 (合并 1 次 LLM 调用)
Step B: 综合判断 + 反思 + 校准 (1 次 LLM 调用)
```

→ 2 次 LLM 调用，耗时增加约 50%（约 20-35s），但质量显著提升。

---

## 6. Prompt 工程优化

### 6.1 当前 Prompt 的不足

| 问题 | 说明 |
|------|------|
| 无 few-shot | LLM 对"好的基本面分析"缺乏具体参照 |
| 分析框架偏模板化 | 5 个维度平行罗列，缺乏"先判断质量、再判断价格"的优先级逻辑 |
| 无估值分位指引 | Prompt 说"与历史5年区间对比"但数据里没有历史数据 |
| 无阈值框架 | "ROE >15% 优秀"给了定性判断但缺乏比较基准 |
| 数据缺失策略单一 | "基于知识库"是被动的，应该有主动的"用替代数据推断"策略 |
| 置信度缺少锚点 | confidence 的含义不明确 |

### 6.2 Few-shot 示例

```python
FUNDAMENTAL_SYSTEM_PROMPT = """你是一个专业的公司基本面分析师...

## 输出示例

### 示例 1: 高质量+低估 (典型机会)
输入: ROE 18%, PE处于3年20%分位, 利润增速加速, 行业排名前3
输出:
{
  "direction": "bullish",
  "magnitude": {"min_pct": 5.0, "max_pct": 15.0},
  "confidence": 0.75,
  "reasoning": "1) 盈利能力: ROE 18%处于行业前3，盈利质量持续改善...",
  "key_factors": ["ROE持续改善", "PE处于3年20%分位提供安全边际", "利润增速加速"],
  "risks": ["行业周期性下行可能影响盈利持续性", "估值修复需要时间"]
}

### 示例 2: 不确定 (数据缺失)
输入: 仅PE/PB可用，财务数据全部缺失
输出:
{
  "direction": "neutral",
  "magnitude": {"min_pct": -3.0, "max_pct": 3.0},
  "confidence": 0.35,
  "reasoning": "数据严重受限，仅能基于估值水平判断...",
  "key_factors": ["PE处于合理区间"],
  "risks": ["财务数据缺失导致无法评估盈利质量", "无法判断成长性"]
}

### 示例 3: 价值陷阱风险
输入: PE处于3年10%分位(很便宜), 但ROE连续下滑, 营收负增长
输出:
{
  "direction": "bearish",
  "magnitude": {"min_pct": -8.0, "max_pct": -2.0},
  "confidence": 0.60,
  "reasoning": "1) 盈利能力: ROE从15%下滑至8%，盈利质量恶化...",
  "key_factors": ["便宜有便宜的理由", "盈利持续恶化"],
  "risks": ["可能是结构性下滑而非周期性"]
}
"""
```

### 6.3 置信度锚定指引

```python
CONFIDENCE_ANCHORS = """
## 置信度(confidence)校准指引

基本面分析的置信度取决于"数据质量 × 判断确定性"：

| confidence | 含义 | 何时使用 |
|------------|------|---------|
| 0.80-0.90 | 较有把握 | 数据充足(评分完成度>80%), 趋势明确, 估值分位极端(<20%或>80%), 有多项独立证据支撑 |
| 0.65-0.79 | 中等把握 | 数据较充足, 趋势和估值方向一致, 但存在1-2个不确定性 |
| 0.50-0.64 | 有限把握 | 数据部分缺失, 趋势和估值方向不完全一致 |
| 0.35-0.49 | 较弱判断 | 财务数据大部分缺失, 仅靠估值和知识库判断 |
| 0.20-0.34 | 信号极弱 | 几乎无财务数据, 纯知识库推断 |

注意:
- 数据完整度<50%时, confidence 不应超过 0.50
- 数据完整度<30%时, confidence 不应超过 0.35
- 趋势不明确(加速vs减速信号矛盾)时, confidence 打 8 折
"""
```

### 6.4 市场区分 Prompt

```python
A_SHARE_FUNDAMENTAL_APPENDIX = """
## A股基本面分析注意
- A股对"成长性"的定价权重高于港股/美股——高增速可以支撑更高PE
- 关注"政策驱动"的行业（新能源、半导体）——政策拐点可能比盈利拐点更重要
- A股小盘股的财务数据可信度需要打折——关注审计意见和现金流验证
- "扣非净利润"比"净利润"更能反映真实盈利能力
"""

HK_SHARE_FUNDAMENTAL_APPENDIX = """
## 港股基本面分析注意
- 港股以机构定价为主——更关注自由现金流和股息，而非单纯增长
- "分派率"(派息/盈利)是关键指标——高分派率在弱势市场有防御价值
- 关注"盈利预测修正"方向——港股对eps estimate revision高度敏感
- 中概股的VIE架构、ADR溢价等结构性因素可能影响估值参考系
"""

US_SHARE_FUNDAMENTAL_APPENDIX = """
## 美股基本面分析注意
- "Shareholder yield"(回购+股息)是核心——美股公司更重视资本回报
- "Rule of 40": 营收增速% + 利润率% > 40% → SaaS公司健康
- 关注 GAAP vs Non-GAAP 差异——差异大说明一次性项目多
"""
```

### 6.5 估值判断框架增强

在 prompt 中嵌入**估值矩阵**概念：

```
## 估值-质量矩阵（先定位，再判断）

|                | 好公司(score≥70) | 一般公司(40-70) | 差公司(<40) |
|----------------|-----------------|----------------|------------|
| 低估(分位<30%) | → 强烈看多       | → 看多          | → 中性(价值陷阱?) |
| 合理(30%-70%)  | → 温和看多       | → 中性          | → 温和看空     |
| 高估(>70%)     | → 中性(贵但合理) | → 看空          | → 强烈看空    |

关键原则：好公司便宜是机会，差公司便宜是陷阱。
```

---

## 7. 自进化机制

### 7.1 准确率追踪

利用 PredictionStore 已有机制，但按基本面分析师的特点细分：

```sql
-- 新增/扩展统计表
-- 1. 按"数据完整度"分桶统计
agent_name, data_quality_bucket, total, dir_accuracy, avg_confidence
'fundamental', 'high(>80%)', 50, 0.62, 0.70
'fundamental', 'medium(50-80%)', 30, 0.53, 0.55
'fundamental', 'low(<50%)', 20, 0.45, 0.40

-- 2. 按"判断方向"统计（看多看空的准确率是否对称）
agent_name, direction, total, accuracy
'fundamental', 'bullish', 40, 0.58
'fundamental', 'bearish', 25, 0.64
'fundamental', 'neutral', 35, 0.50
```

**预测**：基本面分析师看涨时准确率可能 > 看跌时准确率（因为"便宜+好公司"比"贵+差公司"更容易判断）。校准时应区分方向。

### 7.2 估值分位预测力分析

```python
class ValuationBacktester:
    """回测估值分位的预测力"""
    
    def analyze_pe_percentile_predictive_power(self):
        """
        历史上：
        - 当 PE 处于 <20% 分位时，未来1季/1年正收益比例？
        - 当 PE 处于 >80% 分位时，未来1季/1年负收益比例？
        - 估值分位在 A 股 vs 港股 vs 美股 的预测力差异？
        
        这些数据用于：
        1. 校准"估值极端时的 confidence 应该更高"
        2. 在 prompt 中注入"历史统计依据"
        """
```

### 7.3 评分卡权重调优

```python
class ScorecardOptimizer:
    """根据回测结果调整评分卡中各维度的权重"""
    
    def optimize_weights(self, backtest_results: list[dict]):
        """
        当前权重:
          profitability: 30, growth: 25, valuation: 25, health: 20
        
        如果发现:
          - "profitability" 维度高的公司预测准确率高 → 增加到 35
          - "growth" 维度高但准确率低（因为增速不可持续？）→ 减少到 20
        
        用网格搜索/贝叶斯优化找最优权重组合
        """
```

### 7.4 自进化数据流

```
┌─────────────────────────────────────────────────────────┐
│                   自进化数据闭环                         │
│                                                         │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐          │
│  │ 预测时刻  │    │ 验证时刻  │    │ 反馈时刻  │          │
│  │          │    │          │    │          │          │
│  │ 记录:    │    │ 记录:    │    │ 更新:    │          │
│  │ · 评分卡 │───▶│ · 实际涨跌│───▶│ · 校准曲线│          │
│  │ · 分位数 │    │ · 方向对错│    │ · 分位预测│          │
│  │ · 置信度 │    │ · 幅度误差│    │  力统计  │          │
│  │ · 质量分 │    │          │    │ · 评分权重│          │
│  └──────────┘    └──────────┘    └──────────┘          │
│                                                         │
│  周期: 预测后 1周/1月/1季 验证                           │
└─────────────────────────────────────────────────────────┘
```

---

## 8. 质量保障体系

### 8.1 专项校验

```python
class FundamentalResultValidator:
    """公司前景分析结果专项校验"""
    
    def validate(self, result: AnalysisResult, data: dict) -> list[str]:
        issues = []
        
        # 1. 估值-方向一致性检查
        valuation = data.get("valuation_analysis", {})
        pe_pct = valuation.get("pe_percentile_3yr")
        
        if pe_pct is not None:
            if result.direction == "bullish" and pe_pct > 0.85:
                issues.append(f"PE处于3年{pct:.0f}%分位(很贵)，但方向为看涨——需要特别强的理由支持")
            if result.direction == "bearish" and pe_pct < 0.15:
                issues.append(f"PE处于3年{pct:.0f}%分位(很便宜)，但方向为看跌——需要特别强的理由支持")
        
        # 2. 质量评分-方向一致性
        score = data.get("quality_scorecard", {}).get("total", 0)
        if score >= 75 and result.direction == "bearish" and pe_pct is not None and pe_pct < 0.5:
            issues.append(f"高质量公司(score={score})且估值不贵(分位{pct:.0f}%)，看跌需充分理由")
        
        # 3. 置信度-ceiling一致性
        ceiling = data.get("data_quality", {}).get("confidence_ceiling", 0.70)
        if result.confidence > ceiling + 0.05:
            issues.append(f"confidence({result.confidence})超过数据质量上限({ceiling})，可能过度自信")
        
        # 4. 风险遗漏检查
        if result.direction == "bullish" and len(result.risks) == 0:
            issues.append("看涨但未列出至少一个风险——基本面分析必须考虑下行风险")
        
        # 5. 幅度合理性（基本面驱动的变化通常较温和）
        if result.magnitude:
            if result.direction == "bullish" and result.magnitude.max_pct > 20:
                issues.append("基本面驱动的单方向看涨 >20% 需极强催化剂，确认是否有足够依据")
        
        return issues
```

### 8.2 特殊情况处理矩阵

| 情况 | 处理 | confidence 上限 |
|------|------|----------------|
| 数据充足（评分完成度>80%） | 正常分析 | 0.85 |
| 数据中等（50-80%） | 正常分析，标注缺失 | 0.70 |
| 数据不足（只有PE/PB） | 标注"有限分析" | 0.45 |
| 财务数据全缺，只有估值 | 标注"仅估值判断" | 0.35 |
| 疑似价值陷阱（低PE+盈利恶化） | 标注警告 | 0.60 |
| 数据源全部不可用 | 知识库模式 | 0.20 |

### 8.3 回测验证设计

```bash
# 改进前后对比
python scripts/run_backtest.py -t 000001 --start 2026-01-01 --end 2026-06-30 --agent fundamental
python scripts/run_backtest.py -t 0700 --start 2026-01-01 --end 2026-06-30 --agent fundamental_v2

# 关键对比维度
# 1. 方向准确率 vs 数据完整度的关系
# 2. "价值陷阱"识别率（低PE但最终下跌的案例）
# 3. 估值分位的预测力（低分位 → 正收益的概率）
```

### 8.4 测试策略

```python
# tests/test_fundamental_analyst_v2.py

class TestFinancialPreprocessing:
    def test_pe_percentile_calculation(self): ...
    def test_financial_trend_detection(self): ...
    def test_quality_scorecard_scoring(self): ...
    def test_data_quality_assessment(self): ...
    def test_value_trap_detection(self): ...

class TestMultiStepReasoning:
    def test_step1_quality_assessment_output(self): ...
    def test_step2_valuation_judgment_consistency(self): ...
    def test_step3_synthesis_direction(self): ...

class TestSelfEvolution:
    def test_confidence_ceiling_enforced(self): ...
    def test_scorecard_weight_optimization(self): ...

class TestQualityAssurance:
    def test_validator_catches_high_pe_bullish(self): ...
    def test_validator_catches_confidence_above_ceiling(self): ...
    def test_value_trap_warning_generated(self): ...
```

---

## 9. 实施路线图

### Phase A: 快速见效（1 周）🔴 P0

| 任务 | 文件 | 工作量 |
|------|------|--------|
| ① 历史估值分位计算 | `src/data/fundamental_fetcher.py` + 新增 `valuation_history.py` | 1.5 天 |
| ② 多季度财务趋势提取 | `fundamental_fetcher.py` | 1 天 |
| ③ 数据质量评估模块 | 新增 `src/data/fundamental_preprocessor.py` | 0.5 天 |
| ④ 质量评分卡 | `fundamental_preprocessor.py` | 0.5 天 |
| ⑤ Few-shot + 置信度锚定 prompt | `src/prompts/fundamental_prompts.py` | 0.5 天 |
| ⑥ 输出校验增强 | `src/agents/fundamental_analyst.py` | 0.5 天 |
| ⑦ 测试 | `tests/test_fundamental_v2.py` | 1 天 |

**预期效果**：
- 估值分位判断：从"无法判断"升级为"量化分位"
- 趋势分析：从"单期数据"升级为"4-8季度趋势"
- 置信度诚实度：ceiling 机制防止过度自信
- JSON 格式稳定性：few-shot

### Phase B: 数据源补全 + 多步推理（2 周）🟡 P1

| 任务 | 文件 | 工作量 |
|------|------|--------|
| ① 港股财务数据（AASTOCKS/东方财富F10） | `fundamental_fetcher.py` | 2 天 |
| ② 两步 CoT 推理（质量→估值+催化→综合） | `fundamental_analyst.py` | 1.5 天 |
| ③ 市场区分 prompt | `fundamental_prompts.py` | 0.5 天 |
| ④ 估值-质量矩阵嵌入 prompt | `fundamental_prompts.py` | 0.5 天 |
| ⑤ 置信度校准器 | 新增 `src/utils/confidence_calibrator.py` | 1 天 |
| ⑥ 端到端测试 | `tests/` | 1 天 |

**预期效果**：
- 港股财务数据从 0 到 1（最大痛点解决）
- 推理深度：单 pass → 链式推理
- 数据驱动估值判断（分位+矩阵）

### Phase C: 同业对标 + 自进化（2-3 周）🟢 P2

| 任务 | 文件 | 工作量 |
|------|------|--------|
| ① 同业对标数据获取 | `fundamental_fetcher.py` + `valuation_history.py` | 1.5 天 |
| ② 按数据完整度的准确率追踪 | `prediction_store.py` schema 扩展 | 1 天 |
| ③ 评分卡权重优化 | 新增 `src/utils/scorecard_optimizer.py` | 1.5 天 |
| ④ 估值分位预测力回测 | 回测脚本增强 | 1 天 |
| ⑤ 失败案例分析 | 新增 `src/utils/failure_analyzer.py` | 1 天 |
| ⑥ 市场周期判断（行业景气度） | `fundamental_preprocessor.py` | 1 天 |

### Phase D: 深度优化（1 月+）🟢 P3

| 任务 | 说明 |
|------|------|
| 自由现金流估算 | 基于有限数据估算代理自由现金流 |
| 多模型 DeepDive | 用 Qwen/DeepSeek 分别做盈利预测，取共识 |
| 实时盈利预测整合 | 整合卖方一致预期数据 |
| 行业景气度指标 | 将宏观/行业数据引入基本面判断 |
| 预测区间概率化 | 输出概率分布而非单点区间 |

---

## 10. 附录：效果度量

### 10.1 关键指标

| 指标 | 当前（估算） | Phase A 目标 | Phase B 目标 | 度量方式 |
|------|------------|-------------|-------------|---------|
| 方向准确率（A股） | ~50-55% | ≥58% | ≥65% | PredictionStore |
| 方向准确率（港股） | ~40-45% | ≥50% | ≥58% | PredictionStore |
| 置信度校准误差 | 未知 | ≤0.15 | ≤0.10 | \|confidence - actual_acc\| |
| 数据可用率（A股） | ~70% | ≥85% | ≥90% | 多源降级统计 |
| 数据可用率（港股） | ~20% | ≥50% | ≥75% | AASTOCKS 补充后 |
| 价值陷阱识别率 | 0%（无该能力） | ≥40% | ≥60% | 低PE+下跌case |
| 估值分位预测力 | 无 | 统计得出 | 纳入校准 | 回测分析 |
| JSON 格式成功率 | ~90% | ≥97% | ≥98% | 日志统计 |

### 10.2 实验设计

**对照实验**：
```bash
# 同标的、同时间区间，新老版本对比
python scripts/run_backtest.py -t 000001 --start 2026-01-01 --end 2026-06-30 --agent fundamental
python scripts/run_backtest.py -t 0700 --start 2026-01-01 --end 2026-06-30 --agent fundamental

# 重点观察：
# 1. 数据完整度高的 A 股 → 准确率提升幅度
# 2. 数据缺失的港股 → AASTOCKS 补充后的提升
# 3. "低分位+好公司" → 正收益的概率（估值分位预测力）
```

---

## 附录 A：文件变更清单

### 需要修改的文件

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `src/data/fundamental_fetcher.py` | 重构 | 多源采集 + 历史估值序列 |
| `src/agents/fundamental_analyst.py` | 重构 | 多步推理 + 校验 + 校准 |
| `src/prompts/fundamental_prompts.py` | 重写 | Few-shot + 市场区分 + 锚定 + 矩阵 |
| `config/agent_config.yaml` | 微调 | 新增 Agent 变体配置 |

### 需要新增的文件

| 文件 | 说明 |
|------|------|
| `src/data/fundamental_preprocessor.py` | 评分卡 + 趋势提取 + 质量评估 |
| `src/data/valuation_history.py` | 历史估值分位计算 |
| `src/data/hk_financial_source.py` | 港股财务数据采集（AASTOCKS/东方财富F10） |
| `src/utils/fundamental_validator.py` | 专项校验器 |
| `src/utils/confidence_calibrator.py` | 置信度校准器 |
| `src/utils/scorecard_optimizer.py` | 评分卡权重优化（Phase C） |
| `tests/test_fundamental_v2.py` | Agent v2 测试 |
| `tests/test_fundamental_preprocessing.py` | 预处理测试 |

### 不需要修改的文件

- `src/core/base_agent.py` — 接口不变，子类覆盖 `analyze()` 即可
- `src/core/orchestrator.py` — Agent 接口不变
- `src/core/result.py` — `AnalysisResult` 无需新增字段
- `src/core/llm_client.py` — 不变
- `src/data/prediction_store.py` — 已有足够字段（Phase C 需 schema 微调）

---

## 附录 B：风险与应对

| 风险 | 概率 | 应对 |
|------|------|------|
| AASTOCKS 被反爬/wall | 中 | 东方财富F10 备选；保留知识库兜底 |
| 港股财务数据解析困难 | 中 | 简化版：只抓核心指标（营收/利润/ROE） |
| 历史估值数据不完整 | 低 | 腾讯日行情 PE 字段从历史 K 线提取 |
| CoT 推理耗时过长 | 中 | 合并为 2 步；设 LLM 调用超时 |
| 评分卡"一刀切"不精准 | 高 | Phase C 持续调优权重；分区间/行业微调 |
| 知识库信息过时 | 中 | 标注数据来源时间戳；降低知识库补充的权重 |

---

## 附录 C：港股财务数据采集方案详述

### 方案 A（首选）：AASTOCKS 爬取

```python
async def fetch_hk_financials_aastocks(symbol: str) -> dict:
    """
    从 AASTOCKS 获取港股财务数据
    
    URL: https://www.aastocks.com/sc/stocks/analysis/company-fundamental/financial-ratios?symbol={code}
    URL: https://www.aastocks.com/sc/stocks/analysis/company-fundamental/profit-loss?symbol={code}
    
    可获取:
    - 年度/季度营收、利润
    - 毛利率、净利率
    - ROE、ROA
    - 每股盈利
    - 经营现金流
    - 负债比率
    """
    code = symbol.zfill(5)
    
    # 损益表
    url_pl = f"https://www.aastocks.com/sc/stocks/analysis/company-fundamental/profit-loss?symbol={code}"
    # Parse HTML table → dict
    
    # 财务比率
    url_fr = f"https://www.aastocks.com/sc/stocks/analysis/company-fundamental/financial-ratios?symbol={code}"
    # Parse HTML table → dict
    
    return {
        "revenue_series": [...],
        "profit_series": [...],
        "margin": ...,
        "roe": ...,
    }
```

### 方案 B（备选）：东方财富港股F10

```python
async def fetch_hk_financials_eastmoney(symbol: str) -> dict:
    """
    东方财富港股 F10 数据接口
    注：需测试当前网络是否可达
    """
    # akshare 接口（之前不可用，当前环境需重试）
    # ak.stock_hk_financial_analysis_indicator(symbol=symbol)
    pass
```

### 方案 C（兜选）：Capital IQ / 免费API

```python
async def fetch_hk_financials_alphavantage(symbol: str) -> dict:
    """
    Alpha Vantage INCOME_STATEMENT / BALANCE_SHEET
    免费 25次/天，需注册
    """
    pass
```

---

> 📌 **核心原则**：基本面分析师的竞争力不在于"知道多少财务公式"，而在于"能否从有限的数据中，构建出对公司内在价值的合理估计，并诚实地标注这个估计的确定性"。好的基本面分析 = 好公司判断 + 好价格判断 + 好的不确定性评估，三者缺一不可。
