# Phase 3: 行业对比 + 可衡量体系 — 实现设计文档

> **目标**：补齐行业对比分析师，完善回测与追踪体系，让系统从"能跑"进化到"可衡量"。
>
> **核心变化**：新增行业维度 + 系统开始自我验证、积累数据、持续改进。
>
> ⚠️ 本 Phase 与原始 `PROJECT_FRAMEWORK.md` 的 Phase 3 完全对齐。

---

## 与原始框架对照

| 原始框架 Phase 3 任务 | 状态 | 说明 |
|----------------------|------|------|
| **行业对比分析师** | 🆕 本 Phase 实现 | 之前遗漏，现在补上 |
| 历史分析案例存储 (RAG) | ✅ 已实现 | `case_retriever.py` |
| 回测框架 | ✅ 已实现 | `backtester.py` |
| 预测结果追踪与统计 | ✅ 已实现 | `prediction_store.py` + `show_stats.py` + `track_predictions.py` |
| 宏观经济分析师 | 已在 Phase 2 实现 | 与"国际形势分析师"合并为 `macro_analyst.py` |

---

## 目录

1. [Phase 3 目标清单](#1-phase-3-目标清单)
2. [行业对比分析师设计](#2-行业对比分析师设计)
   - [2.1 数据获取](#21-数据获取)
   - [2.2 Agent 设计](#22-agent-设计)
   - [2.3 Prompt 设计](#23-prompt-设计)
3. [已有基础设施速览](#3-已有基础设施速览)
4. [文件清单与创建顺序](#4-文件清单与创建顺序)
5. [Phase 3 完成标准](#5-phase-3-完成标准)

---

## 1. Phase 3 目标清单

| # | 任务 | 产出文件 | 优先级 |
|---|------|---------|--------|
| 3.1 | 行业数据获取器 | `src/data/industry_fetcher.py` | ⭐⭐⭐ |
| 3.2 | 行业对比 Prompt | `src/prompts/industry_prompts.py` | ⭐⭐⭐ |
| 3.3 | 行业对比分析师 Agent | `src/agents/industry_analyst.py` | ⭐⭐⭐ |
| 3.4 | 更新权重配置 | `config/agent_config.yaml` | ⭐⭐⭐ |
| 3.5 | 主入口注册新 Agent | `scripts/run_analysis.py` | ⭐⭐ |
| （以下已在 Phase 3 早期实现，无需重复开发） |
| ✅ | RAG 案例检索 | `src/core/case_retriever.py` | 已完成 |
| ✅ | 回测引擎 | `src/core/backtester.py` | 已完成 |
| ✅ | 预测追踪存储 | `src/data/prediction_store.py` | 已完成 |
| ✅ | 准确率仪表盘 | `scripts/show_stats.py` | 已完成 |
| ✅ | 事后验证脚本 | `scripts/track_predictions.py` | 已完成 |

---

## 2. 行业对比分析师设计

### 为什么需要行业对比？

当前基本面分析师能说出"PE=4.63"，但说不清**这个 PE 在银行业里是贵还是便宜**。

行业对比分析师的职责：
- 获取同行业公司的平均估值（PE/PB/ROE 等）
- 判断标的在行业中的估值分位
- 识别板块轮动趋势
- 回答："这个标的在它的行业里，是领跑的、跟跑的，还是掉队的？"

### 2.1 数据获取

#### 数据内容

```
┌─────────────────────────────────────────────────┐
│         行业对比数据维度                          │
├─────────────────────────────────────────────────┤
│ 🏭 行业概况                                      │
│   - 标的所属行业名称                               │
│   - 行业内公司数量                                 │
│   - 行业近期涨跌幅                                 │
│                                                 │
│ 📊 行业平均估值                                   │
│   - 行业平均 PE / 中位数 PE                       │
│   - 行业平均 PB / 中位数 PB                       │
│   - 行业平均 ROE                                  │
│   - 行业平均 营收增速 / 利润增速                    │
│                                                 │
│ 📈 标的在行业中的位置                              │
│   - PE 分位（比 X% 的公司便宜）                    │
│   - PB 分位                                       │
│   - ROE 分位（比 X% 的公司高）                     │
│   - 市值排名                                       │
│                                                 │
│ 🔄 板块动态                                      │
│   - 行业近期资金流向                                │
│   - 板块是否处于轮动热点                            │
└─────────────────────────────────────────────────┘
```

#### 数据源策略

```
Layer 1: akshare
  - stock_board_industry_summary_ths  → 行业板块摘要
  - stock_board_industry_cons_em      → 行业个股列表+估值

Layer 2: 东方财富行业板块
  - 直接请求东方财富行业 API

Layer 3: 降级
  - 获取不到行业数据时，基于 LLM 知识库分析
```

### 2.2 Agent 设计

```python
class IndustryAnalyst(BaseAgent):
    """行业对比分析师
    
    判断标的在其行业中的相对位置：
    - 估值是便宜还是贵？
    - 盈利能力和成长性在行业中排名如何？
    - 行业景气度方向？
    """
    
    async def gather_data(self, target: str, timeframe: str) -> dict:
        """获取行业对比数据"""
        # 1. 获取标的自身的基本面数据（复用 fundamental_fetcher）
        # 2. 获取行业平均估值
        # 3. 计算标的在行业中的分位
        # 4. 打包返回
```

### 2.3 Prompt 设计

行业对比分析师的思考框架：

```
1. **估值横向对比**
   - 标的 PE vs 行业平均 PE → 便宜/合理/偏贵
   - 标的 PB vs 行业平均 PB
   - 估值折价/溢价是否合理？（好公司可以享受溢价）

2. **盈利能力对比**
   - ROE vs 行业平均 → 资本回报效率高低
   - 净利率 vs 行业平均 → 产品竞争力
   - 增速 vs 行业平均 → 成长性强弱

3. **行业景气度**
   - 当前行业处于上升期/平台期/下降期？
   - 政策面是否支持该行业？
   - 板块资金流向

4. **综合判断**
   - 如果基本面好 + 估值低 + 行业景气 → 强烈看涨
   - 如果基本面好 + 估值高 + 行业下行 → 谨慎
   - 如果基本面差但行业在风口 → 短期可能有交易机会
```

---

## 3. 已有基础设施速览

以下模块已在 Phase 3 早期实现，**无需重复开发**，行业对比分析师只需：

1. 预测自动存入 `PredictionStore`（`run_analysis.py` 已集成）
2. 事后验证 `track_predictions.py`
3. 准确率仪表盘 `show_stats.py`
4. 回测引擎 `backtester.py`
5. RAG 案例参考 `case_retriever.py`（可选集成到汇总）

---

## 4. 文件清单与创建顺序

```
[1] src/data/industry_fetcher.py          — 行业数据获取 🆕
[2] src/prompts/industry_prompts.py       — 行业对比 prompt 🆕
[3] src/agents/industry_analyst.py        — 行业对比 Agent 🆕
[4] config/agent_config.yaml              — 新增权重配置 ✏️
[5] scripts/run_analysis.py               — 注册新 Agent ✏️
```

---

## 5. Phase 3 完成标准

```bash
# 1. 行业对比 Agent 能正常获取数据
python3 -c "from src.data.industry_fetcher import IndustryFetcher; ..."

# 2. 5 个 Agent 全开跑通
python3 scripts/run_analysis.py --target 000001

# 3. 报告中出现行业对比维度
#    汇总中能看到 "行业对比: PE 低于行业均值 50%，估值偏低"

# 4. 回测+统计基础设施正常工作
python3 scripts/show_stats.py
```

### 验收检查表

```
☐ 行业对比 Agent 注册成功
☐ 行业数据获取（至少部分字段成功）
☐ 5 个 Agent 并行执行 + 汇总
☐ agent_config.yaml 包含行业对比权重
☐ 汇总报告包含行业维度分析
☐ 所有测试通过
```
