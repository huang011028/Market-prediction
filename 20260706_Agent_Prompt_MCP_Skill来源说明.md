# 20260706 Agent Prompt/MCP/Skill 来源说明

## 1. 总结

当前项目里的 5 个分析 Agent 都有 prompt。

当前项目没有真正接入 MCP 协议层，也没有 MCP server/client 配置。代码里提到的 MCP 改进建议，本质上是“数据源/工具源能力缺口”的工程分类。

当前项目没有独立的外部 skill manifest。这里的 skill 更接近 Agent 内部的规则化能力、特征工程、预处理器和校准器。

## 2. Agent 来源

Agent 注册配置来自:

```text
config/agent_config.yaml
```

当前 5 个 Agent:

| Agent | 类 | 来源 |
| --- | --- | --- |
| 近期股价分析师 | `TechnicalAnalyst` | `src/agents/technical_analyst.py` |
| 最新新闻分析师 | `NewsAnalyst` | `src/agents/news_analyst.py` |
| 公司前景分析师 | `FundamentalAnalyst` | `src/agents/fundamental_analyst.py` |
| 国际形势分析师 | `MacroAnalyst` | `src/agents/macro_analyst.py` |
| 行业对比分析师 | `IndustryAnalyst` | `src/agents/industry_analyst.py` |

## 3. Prompt 来源

| Agent | 系统 prompt 来源 | 动态 user prompt 来源 |
| --- | --- | --- |
| 近期股价分析师 | `src/prompts/technical_prompts.py` | `src/agents/technical_analyst.py` |
| 最新新闻分析师 | `src/prompts/news_prompts.py` | `src/agents/news_analyst.py` |
| 公司前景分析师 | `src/prompts/fundamental_prompts.py` | `src/agents/fundamental_analyst.py` |
| 国际形势分析师 | `src/prompts/macro_prompts.py` | `src/agents/macro_analyst.py` |
| 行业对比分析师 | `src/prompts/industry_prompts.py` | `src/agents/industry_analyst.py` |
| 综合研判/Aggregator | `src/prompts/aggregator_prompts.py` | `src/agents/aggregator.py` |

Prompt 的构成通常是:

```text
系统角色 prompt + 市场/行业附录 + 代码计算证据包 + 动态数据摘要 + JSON 输出约束
```

## 4. MCP / 数据工具来源

当前 repo 没有真正的 MCP 层。

目前 Agent 获取数据靠本地 fetcher 和第三方 Python 包/API:

| Agent | 数据工具来源 |
| --- | --- |
| 近期股价分析师 | `src/data/price_fetcher.py`, `src/data/technical_features.py` |
| 最新新闻分析师 | `src/data/news_fetcher.py`, `src/data/news_sources/eastmoney.py`, `src/data/news_sources/sina.py`, `yfinance` |
| 公司前景分析师 | `src/data/fundamental_fetcher.py`, `src/data/hk_financial_fetcher.py`, `src/data/valuation_history.py` |
| 国际形势分析师 | `src/data/macro_fetcher.py`, `src/data/geopolitical_fetcher.py`, `src/data/stock_context.py` |
| 行业对比分析师 | `src/data/industry_fetcher.py`, `src/data/industry_preprocessor.py`, `src/utils/industry_chain.py`, `src/utils/sector_rotation_detector.py` |

所以当改进建议写 “MCP” 时，在当前项目语境里应理解为:

```text
需要扩展数据源/工具源，未来可以封装成 MCP，但现在还不是 MCP。
```

## 5. Skill / 特征工程来源

当前 skill 主要是内部规则模块:

| 能力 | 来源 |
| --- | --- |
| 新闻去重、相关度、情绪、事件分类、时效权重 | `src/data/news_preprocessor.py` |
| 技术指标证据包 | `src/data/technical_features.py` |
| 基本面评分卡/估值历史 | `src/utils/scorecard_optimizer.py`, `src/data/valuation_history.py` |
| 行业轮动/产业链/催化剂 | `src/utils/sector_rotation_detector.py`, `src/utils/industry_chain.py` |
| 置信度校准器 | `src/utils/*_calibrator.py` |
| 失败/贡献分析 | `src/core/failure_analyzer.py`, `src/core/agent_improvement.py` |

这些 skill 不是 Codex 外部 skill，也不是 MCP tool；它们是项目运行时的确定性代码能力。

## 6. 本轮新增的自我改进层

本轮新增:

```text
src/core/improvement_patch_planner.py
scripts/build_agent_improvement_drafts.py
```

作用:

```text
新闻快照回放报告 -> improvement_signals -> prompt/MCP/skill 补丁草案
```

它不会直接改真实 prompt、MCP 或 skill 文件，只会输出可审阅草案。
