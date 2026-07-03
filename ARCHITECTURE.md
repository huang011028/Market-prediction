# 🏗️ Market Prediction — 项目架构与现状文档

> **最后更新**: 2026-07-02 | **Phase**: 0-3 全部完成 | **Agent 数量**: 6 个（5 分析 + 1 汇总）

---

## 目录

1. [项目定位](#1-项目定位)
2. [系统架构](#2-系统架构)
3. [Agent 团队详情](#3-agent-团队详情)
4. [数据源矩阵](#4-数据源矩阵)
5. [一次完整分析的流程](#5-一次完整分析的流程)
6. [项目目录结构](#6-项目目录结构)
7. [技术栈](#7-技术栈)
8. [Phase 发展历程](#8-phase-发展历程)
9. [当前限制与已知问题](#9-当前限制与已知问题)
10. [未来优化方向](#10-未来优化方向)

---

## 1. 项目定位

一个由 **6 个专业 AI Agent** 组成的投资研究团队，协作预测股票/组合/货币对的**短期及中长期涨跌幅度**。

核心输出：
- **方向**：看涨/看跌/震荡
- **幅度区间**：如 `-2.5% ~ +3.0%`
- **置信度**：0~100%
- **推理过程**：每个 Agent 输出完整的分析逻辑
- **分歧点**：汇总 Agent 标注 Agent 间观点矛盾

### 不是做什么的

- ❌ 不预测精确价格
- ❌ 不给出买入/卖出建议
- ❌ 不是实时交易系统

---

## 2. 系统架构

### 2.1 整体架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                      👤 用户输入                                  │
│          python scripts/run_analysis.py --target 0700             │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                  🎯 Orchestrator（调度器）                        │
│                                                               │
│  1. 解析标的 → 识别市场（A股/港股/美股）                           │
│  2. 激活 Agent（可配置）                                         │
│  3. 并行分发（asyncio.TaskGroup）                                │
│  4. 收集结果 → 传给汇总                                         │
└──────────────────────────┬──────────────────────────────────────┘
                           │
    ┌──────────────────────┼──────────────────────┐
    │          ┌───────────┼───────────┐          │
    ▼          ▼           ▼           ▼          ▼
┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐
│📊 近期 │ │📰 最新 │ │🏢 公司 │ │🌍 国际 │ │🏭 行业 │
│  股价  │ │  新闻  │ │  前景  │ │  形势  │ │  对比  │
│分析师 │ │分析师 │ │分析师 │ │分析师 │ │分析师 │
├────────┤ ├────────┤ ├────────┤ ├────────┤ ├────────┤
│腾讯K线 │ │东方财富│ │腾讯PE  │ │东方财富│ │腾讯PE  │
│80条数据│ │10条新闻│ │Sina52周│ │CPI/PMI │ │行业参考│
│MA/MACD │ │情绪分析│ │估值判断│ │GDP+汇率│ │估值分位│
│RSI/布林│ │        │ │        │ │知识库  │ │        │
└────┬───┘ └───┬────┘ └───┬────┘ └───┬────┘ └───┬────┘
     │         │         │         │         │
     │   AnalysisResult   │         │         │
     │   方向+幅度+置信度   │         │         │
     └─────────┼──────────┴─────────┴─────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────────┐
│                  🎯 汇总分析师 (Aggregator)                       │
│                                                               │
│  1. 四维交叉验证                                               │
│  2. 一致性分析 + 分歧识别                                       │
│  3. 加权综合（分时间维度权重）                                    │
│  4. 生成 FinalReport                                          │
└──────────────────────────┬──────────────────────────────────────┘
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
    ┌──────────┐   ┌──────────┐   ┌──────────────┐
    │ Markdown │   │ JSON     │   │ Prediction   │
    │ 报告     │   │ 文件     │   │ Store (SQLite)│
    └──────────┘   └──────────┘   └──────────────┘
```

### 2.2 三层架构

```
┌──────────────────────────────────────────────┐
│              CLI 层 (scripts/)                 │
│  run_analysis.py | run_backtest.py            │
│  show_stats.py   | track_predictions.py       │
├──────────────────────────────────────────────┤
│              Agent 层 (src/agents/)            │
│  technical | news | fundamental | macro       │
│  industry | aggregator                        │
├──────────────────────────────────────────────┤
│              核心框架 (src/core/)              │
│  orchestrator | base_agent | llm_client       │
│  result | backtester | case_retriever         │
├──────────────────────────────────────────────┤
│              数据层 (src/data/)                │
│  price | news | fundamental | macro           │
│  industry | prediction_store                  │
├──────────────────────────────────────────────┤
│              配置层 (config/)                  │
│  settings | agent_config | weight_manager     │
└──────────────────────────────────────────────┘
```

---

## 3. Agent 团队详情

### 3.1 5 个分析 Agent + 1 个汇总 Agent

| # | Agent | 文件 | 权重(短期) | 数据来源 | 分析维度 |
|---|-------|------|-----------|---------|---------|
| 1 | 📊 **近期股价分析师** | `technical_analyst.py` | 30% | 腾讯K线 + akshare | MA/MACD/RSI/布林带/量价 |
| 2 | 📰 **最新新闻分析师** | `news_analyst.py` | 20% | 东方财富新闻 | 情绪统计/事件评估/预期差 |
| 3 | 🏢 **公司前景分析师** | `fundamental_analyst.py` | 12% | 腾讯PE/PB + akshare财务 | 盈利/估值/成长性/机构评级 |
| 4 | 🌍 **国际形势分析师** | `macro_analyst.py` | 12% | 东方财富CPI/PMI/GDP + 新浪汇率 | 流动性/经济周期/汇率/地缘 |
| 5 | 🏭 **行业对比分析师** | `industry_analyst.py` | 10% | 腾讯PE + 行业参考值 | 估值分位/盈利对比/板块景气 |
| 6 | 🎯 **汇总分析师** | `aggregator.py` | — | 前5个Agent的输出 | 交叉验证/加权综合/分歧识别 |

### 3.2 汇总 Agent 的分析框架

汇总 Agent 不只是简单投票，而是执行**四维交叉验证**：

```
1. 技术面 × 新闻面  → 短期情绪是否与技术信号一致？
2. 基本面 × 宏观面  → 中长期价值是否被宏观环境支撑？
3. 技术面 × 基本面  → 价格是否偏离内在价值？
4. 新闻面 × 宏观面  → 事件冲击是短期还是结构性的？
```

### 3.3 分时间维度权重

| Agent | 短期(1周) | 中期(1月) | 长期(1季) |
|-------|----------|----------|----------|
| 近期股价 | 30% | 20% | 8% |
| 最新新闻 | 20% | 12% | 4% |
| 公司前景 | 12% | 22% | 32% |
| 国际形势 | 12% | 18% | 28% |
| 行业对比 | 10% | 15% | 18% |
| 综合研判 | 16% | 13% | 10% |

> 短期重技术面+新闻（市场情绪），长期重基本面+宏观（价值驱动）

---

## 4. 数据源矩阵

### 4.1 各市场数据源可用性

| 数据类别 | A股 | 港股 | 美股 | 数据源 |
|---------|-----|------|------|--------|
| **K线数据** | ✅ 腾讯API + akshare | ✅ 腾讯API | ⚠️ yfinance(重试) | 腾讯K线、akshare、yfinance |
| **技术指标** | ✅ pandas 计算 | ✅ pandas 计算 | ⚠️ yfinance | MA/MACD/RSI/布林带 |
| **实时PE/PB** | ✅ 腾讯行情 | ✅ 腾讯行情 | ⚠️ yfinance | qt.gtimg.cn |
| **52周高低** | ✅ | ✅ 新浪行情 | ⚠️ yfinance | hq.sinajs.cn |
| **财务数据** | ✅ akshare 财报 | ⚠️ 部分缺 | ⚠️ yfinance | 营收/利润/ROE/EPS |
| **新闻** | ✅ 东方财富 | ✅ 东方财富 | ⚠️ yfinance | stock_news_em |
| **CPI/PMI/GDP** | ✅ 东方财富DC | ✅ 同上 | — | datacenter.eastmoney.com |
| **汇率** | ✅ 新浪 | ✅ 新浪 | — | USDCNY/USDCNH |
| **LPR/M2/DXY/VIX** | ⚠️ 参考值 | ⚠️ 参考值 | — | 无法实时获取 |

> ✅ = 稳定可用 | ⚠️ = 有数据但不完整或需重试 | — = 不适用

### 4.2 当前数据源架构

```
                    ┌─────────────────────────┐
                    │    腾讯行情 qt.gtimg.cn   │
                    │  A股+港股: K线/PE/PB/市值  │
                    └──────┬──────────────────┘
                           │
       ┌───────────────────┼───────────────────┐
       │                   │                   │
       ▼                   ▼                   ▼
┌─────────────┐   ┌─────────────┐   ┌─────────────┐
│ 新浪行情     │   │ 东方财富DC   │   │ 东方财富新闻  │
│ hq.sinajs.cn│   │ datacenter  │   │ stock_news  │
│             │   │             │   │ _em         │
│ 52周高低    │   │ CPI/PMI/GDP │   │ A股+港股新闻 │
│ USDCNY/CNH  │   │             │   │             │
└─────────────┘   └─────────────┘   └─────────────┘
       │                   │                   │
       └───────────────────┼───────────────────┘
                           │
            ┌──────────────┼──────────────┐
            │              │              │
            ▼              ▼              ▼
     ┌──────────┐  ┌──────────┐  ┌──────────┐
     │ akshare  │  │ yfinance │  │ LLM知识库 │
     │ (降级)   │  │ (降级)   │  │ (兜底)   │
     │ A股K线   │  │ 美股数据  │  │ 所有维度  │
     │ 财务数据  │  │          │  │          │
     └──────────┘  └──────────┘  └──────────┘
```

---

## 5. 一次完整分析的流程

### 5.1 时序图

```
时间 →
0s    │ 用户输入: python scripts/run_analysis.py --target 0700
      │
1s    │ Orchestrator: 识别市场=HK, 激活5个Agent
      │
2s    │ ┌─ 近期股价 ──── 腾讯K线API(80条) → 计算MA/MACD/RSI
      │ ├─ 最新新闻 ──── 东方财富新闻(10条)
      │ ├─ 公司前景 ──── 腾讯PE/PB + Sina52周
      │ ├─ 国际形势 ──── 东方财富CPI/PMI/GDP + 新浪汇率
      │ └─ 行业对比 ──── 腾讯PE + 行业参考值
      │         │  (并行执行 asyncio.TaskGroup)
      │         │
15-40s│         ├─ 行业对比: 完成 (16s) → neutral 45%
      │         ├─ 公司前景: 完成 (19s) → neutral 40%
      │         ├─ 最新新闻: 完成 (37s) → neutral 55%
      │         ├─ 国际形势: 完成 (39s) → bullish 60%
      │         └─ 近期股价: 完成 (46s) → neutral 60%
      │
46s   │ Aggregator: 收到5个AnalysisResult
      │ ├─ 四维交叉验证
      │ ├─ 一致性分析 (识别: 宏观看涨 vs 其他中性)
      │ ├─ 加权综合 (短期权重: 技术30%+新闻20%+...)
      │ └─ 生成 FinalReport
      │
84s   │ 输出: Markdown报告 + JSON文件 + 存入PredictionStore
      │
      │ ✅ 分析完成! 方向=neutral, 幅度=-2.5%~+3.0%, 置信度=53%
```

### 5.2 命令行使用

```bash
# A股分析（5个Agent全开）
python3 scripts/run_analysis.py --target 000001

# 港股分析
python3 scripts/run_analysis.py --target 0700

# 中期预测
python3 scripts/run_analysis.py --target 000001 --timeframe "中期(1月)"

# 跳过某些维度
python3 scripts/run_analysis.py --target 000001 --no-news --no-macro

# 查看准确率仪表盘
python3 scripts/show_stats.py

# 回测历史区间
python3 scripts/run_backtest.py --target 000001 --start 2026-01-01 --end 2026-06-30

# 验证过期预测
python3 scripts/track_predictions.py
```

---

## 6. 项目目录结构

```
Market-prediction/
│
├── 📄 README.md                    # 项目简介 + 快速开始
├── 📄 PROJECT_FRAMEWORK.md         # 原始框架设计文档
├── 📄 ARCHITECTURE.md              # 本文档 — 架构与现状
├── 📄 PHASE{0,1,2,3}_DESIGN.md   # 各 Phase 实现设计文档
├── 📄 requirements.txt
├── 📄 pyproject.toml
├── 📄 .env.example                 # 环境变量模板
│
├── 📁 config/                      # 配置层
│   ├── settings.py                 # 全局配置 (从.env加载)
│   ├── agent_config.yaml           # Agent注册 + 权重配置
│   └── weight_manager.py           # 权重查询 + 降级再分配
│
├── 📁 src/
│   ├── 📁 core/                    # 核心框架
│   │   ├── orchestrator.py         # 调度器: 并行分发任务
│   │   ├── base_agent.py           # Agent抽象基类
│   │   ├── llm_client.py           # DeepSeek API封装
│   │   ├── result.py               # 数据结构 (AnalysisResult/FinalReport)
│   │   ├── data_collector.py       # 数据采集基类
│   │   ├── backtester.py           # 回测引擎
│   │   └── case_retriever.py       # RAG历史案例检索
│   │
│   ├── 📁 agents/                  # Agent层 (6个)
│   │   ├── technical_analyst.py    # 近期股价分析师
│   │   ├── news_analyst.py         # 最新新闻分析师
│   │   ├── fundamental_analyst.py  # 公司前景分析师
│   │   ├── macro_analyst.py        # 国际形势分析师
│   │   ├── industry_analyst.py     # 行业对比分析师
│   │   └── aggregator.py           # 汇总分析师
│   │
│   ├── 📁 data/                    # 数据层
│   │   ├── price_fetcher.py        # K线 + 技术指标
│   │   ├── news_fetcher.py         # 新闻数据
│   │   ├── fundamental_fetcher.py  # 基本面数据
│   │   ├── macro_fetcher.py        # 宏观数据
│   │   ├── industry_fetcher.py     # 行业对比数据
│   │   ├── prediction_store.py     # 预测追踪 (SQLite)
│   │   └── schema.sql              # 数据库建表语句
│   │
│   ├── 📁 prompts/                 # Prompt模板 (6个)
│   │   ├── technical_prompts.py
│   │   ├── news_prompts.py
│   │   ├── fundamental_prompts.py
│   │   ├── macro_prompts.py
│   │   ├── industry_prompts.py
│   │   └── aggregator_prompts.py
│   │
│   └── 📁 utils/
│       └── logger.py               # 日志系统
│
├── 📁 scripts/                     # CLI入口
│   ├── run_analysis.py             # 主入口: 单次分析
│   ├── run_backtest.py             # 回测引擎
│   ├── show_stats.py               # 准确率仪表盘
│   └── track_predictions.py        # 预测事后验证
│
├── 📁 tests/                       # 测试 (57个)
│   ├── test_result.py              # 数据结构测试
│   ├── test_phase1.py              # Phase1 Agent+数据测试
│   └── test_phase3.py              # Phase3 回测+追踪测试
│
└── 📁 output/                      # 分析报告输出
    ├── report_*.md                  # Markdown报告
    └── report_*.json                # JSON报告
```

---

## 7. 技术栈

| 层级 | 选型 | 说明 |
|------|------|------|
| **语言** | Python 3.11+ | asyncio.TaskGroup 并行 |
| **LLM** | DeepSeek V4 Pro | API兼容OpenAI格式 |
| **数据** | 腾讯行情 + 东方财富 + 新浪 + akshare + yfinance | 免费源多层降级 |
| **存储** | SQLite (predictions.db) | 预测追踪 |
| **向量** | ChromaDB (可选) | RAG案例检索 |
| **配置** | YAML + .env | Agent权重 + API Key |
| **测试** | pytest (57个) | 单元 + 集成 |

---

## 8. Phase 发展历程

| Phase | 内容 | 状态 | 核心产出 |
|-------|------|------|---------|
| **Phase 0** | 基础设施 | ✅ | settings, llm_client, base_agent, orchestrator, result |
| **Phase 1** | MVP | ✅ | 技术面 + 新闻 + 汇总 (真实数据) |
| **Phase 2** | 扩展维度 | ✅ | 基本面 + 宏观 + 行业对比 + 权重系统 |
| **Phase 3** | 可衡量 | ✅ | 回测引擎 + RAG + 预测追踪 + 准确率仪表盘 |
| **Phase 4** | 产品化 | 📅 | Web界面 + 多标的批量 + 定时监控 |

### 代码量统计（估算）

| 模块 | 文件数 | 约行数 |
|------|--------|--------|
| 核心框架 | 7 | ~1,200 |
| Agent | 6 | ~450 |
| 数据获取 | 6 | ~1,500 |
| Prompt | 6 | ~900 |
| 配置+工具 | 4 | ~500 |
| CLI脚本 | 4 | ~600 |
| 测试 | 3 | ~700 |
| **合计** | **36** | **~5,850** |

---

## 9. 当前限制与已知问题

### 9.1 网络环境限制

用户网络有企业防火墙/代理，部分数据源不可达：

| 数据源 | 状态 | 影响 |
|--------|------|------|
| akshare 港股K线 | ❌ ConnectionError | 已用腾讯API替代 |
| akshare 宏观数据 | ❌ 超时 | 已用东方财富DC替代 |
| yfinance（全部） | ⚠️ 频繁限流 | 港股已用腾讯API替代，美股仍受限 |
| 东方财富实时行情 | ❌ ConnectionError | 估值需用腾讯API |

### 9.2 数据完整性问题

| 缺口 | 影响 | 当前方案 |
|------|------|---------|
| 港股财务数据 | 公司前景缺营收/利润/ROE | 用PE/PB+52周位置+LLM知识库 |
| 美债/V | 宏观缺美国数据 | 用参考值+LLM知识库 |
| 美股整体 | 所有Agent数据不足 | yfinance重试，成功率低 |
| 行业实时PE | 行业对比无法算分位 | 用参考值 |

### 9.3 性能

- 5个Agent全开：**80-120秒**
- 主要瓶颈：LLM推理（每个Agent约10-30秒）
- 并行执行已最大化利用，进一步优化需模型加速或更快的API

### 9.4 未实现的原始框架项

| 框架项 | 状态 |
|--------|------|
| 资金流向分析师 (V4) | 📅 未实现 |
| 社交情绪分析师 (V4) | 📅 未实现 |
| Streamlit Web界面 | 📅 未实现 |
| 多标的批量分析 | 📅 未实现 |
| 定时监控+推送 | 📅 未实现 |

---

## 10. 未来优化方向

### 10.1 短期优化（1-2周可完成）

#### 🔧 数据源增强

| 优化项 | 方案 | 价值 |
|--------|------|------|
| 港股财务数据 | 爬取 AASTOCKS 或 东方财富港股财报页 | 公司前景Agent置信度从40%→60%+ |
| 美股替代源 | Alpha Vantage (免费500次/天) 或 Polygon.io | 美股全维度可用 |
| 美债/VIX实时 | FRED API (免费注册) 或 TradingView | 宏观Agent全实时数据 |
| 北向资金 | 东方财富 DataCenter 其他接口 | 宏观Agent增加资金流维度 |

#### 🚀 性能优化

| 优化项 | 方案 | 价值 |
|--------|------|------|
| 数据缓存 | 同标的K线/基本面数据缓存24h | 重复查询2s→0s |
| LLM响应缓存 | 相似prompt复用（需向量相似度判断） | 减少API费用 |
| 流式输出 | LLM streaming（DeepSeek支持） | 用户体验提升 |

#### 🧪 质量提升

| 优化项 | 方案 | 价值 |
|--------|------|------|
| 自适应权重 | 基于回测准确率自动调整agent_config.yaml | 预测准确率提升 |
| Prompt调优 | 收集失败case，分析LLM输出质量问题 | 减少JSON解析失败 |
| Agent自评 | 每个Agent追加"自评环节"检查逻辑漏洞 | 可解释性提升 |

### 10.2 中期优化（1月内）

#### 💰 资金流向分析师 (原始框架 V4)

```
职责: 分析主力资金、北向/南向资金、机构持仓变化
数据源: 东方财富资金流向API + 沪深港通数据
分析维度: 
  - 主力净流入/流出趋势
  - 北向资金持仓变化
  - 大单/中单/小单分布
  - 融资融券余额变化
```

#### 📱 社交情绪分析师 (原始框架 V4)

```
职责: 分析社交媒体/论坛舆情、散户情绪
数据源: 
  - 东方财富股吧热度
  - 雪球讨论量/情感分析
  - 微信指数/百度指数（可选）
分析维度:
  - 讨论热度趋势
  - 正面/负面情感比例
  - 异常热度预警
```

#### 🌐 Streamlit Web 界面

```
功能:
  - 股票代码输入 → 一键分析
  - 可视化 Agent 置信度对比
  - 历史预测列表 + 验证状态
  - 准确率趋势图
  - 多标的对比
```

### 10.3 长期优化（3月+）

#### 📊 多标的批量 + 组合分析

```
- 一次分析N个标的
- 组合层面的风险收益特征
- 行业配置建议
```

#### ⏰ 定时监控 + 推送

```
- Cron 定时触发关键标的分析
- 微信/邮件/钉钉推送
- 异常波动预警
```

#### 🤖 多模型辩论模式

```
- 同一维度用 DeepSeek + Qwen + GPT-4o 分别分析
- 取三者的"辩论共识"
- 成本更高但质量更优
```

#### 📈 自适应权重系统

```
- 追踪每个Agent的历史准确率
- 短期：技术面 66% 准确率 → 自动提升权重
- 长期：基本面 52% 准确率 → 自动降低权重
- 权重随验证数据积累持续优化
```

#### 🪙 加密货币支持

```
- 数据源: Binance API / CoinGecko
- Agent调整: 高波动特性 → 放宽幅度区间
- 新增维度: 链上数据（持仓量、资金费率）
```

---

## 附录：快速参考

### 添加新 Agent 的步骤

```
1. src/data/xxx_fetcher.py      ← 数据获取
2. src/prompts/xxx_prompts.py   ← Prompt 模板
3. src/agents/xxx_analyst.py    ← Agent 实现 (继承 BaseAgent)
4. config/agent_config.yaml     ← 注册 + 配置权重
5. scripts/run_analysis.py      ← 注册到主入口
✅ 完成！
```

### 常用命令速查

```bash
# 分析
python3 scripts/run_analysis.py --target 000001                    # A股
python3 scripts/run_analysis.py --target 0700                      # 港股
python3 scripts/run_analysis.py --target 000001 --no-news          # 跳过新闻
python3 scripts/run_analysis.py --target 000001 -f "中期(1月)"     # 中期

# 统计
python3 scripts/show_stats.py                                      # 准确率仪表盘
python3 scripts/track_predictions.py                               # 验证过期预测

# 回测
python3 scripts/run_backtest.py -t 000001 --start 2026-01-01 --end 2026-06-30

# 测试
python3 -m pytest tests/ -v -q -m "not slow"                       # 单元测试
```

---

> 📌 **这个项目的核心价值不在于预测有多准，而在于它证明了：一个金融小白，用 36 个 Python 文件、6 个 AI Agent、不到 6000 行代码，就可以从零搭建一个多维度、可衡量、持续进化的投资分析系统。**
