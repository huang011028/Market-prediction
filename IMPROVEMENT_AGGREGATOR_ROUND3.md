# 🎯 汇总分析师 — Round 3 深度优化设计文档

> **Round 1+2 回顾**：实现了 Agent 质量感知、动态权重调整、分歧量化引擎、Agent 贡献度计算。227 测试全过。汇总 Analyst 已成为一个"看得见质量、算得清分歧、分得清贡献"的智能裁判。
>
> **Round 3 核心命题**：从"裁判"升级为"教练"——不仅综合判断，还能给出可操作的改进建议，并让决策过程可追溯、可审计。

---

## 1. Round 1+2 实际交付 vs 计划

| 轮次 | 功能 | 状态 | 方法 |
|------|------|------|------|
| R1 | Agent 质量感知 | ✅ | `_score_quality()` — N/A计数+数据源检测 |
| R1 | 动态权重调整 | ✅ | `_adjust_weights()` — poor×0.3, partial×0.6, 知识库×0.5 |
| R1 | 质量评估表 | ✅ | `_build_context()` 新增质量表 |
| R2 | 分歧量化引擎 | ✅ | `_calculate_disagreement()` — 方向熵+异常值 |
| R2 | Agent 贡献度 | ✅ | `_calculate_contributions()` — 原始vs调整权重 |
| R2 | 报告质量标签 | ✅ | `_quality_tag()` — ✅⚠️❌📚 标签 |

### 当前架构总览

```
agent_results → [质量评分] → [动态权重] → [分歧量化] → [贡献度]
                                                          ↓
                                              _build_context() 
                                              ├─ 质量评估表
                                              ├─ 权重（静态→动态）
                                              ├─ 分歧量化
                                              ├─ Agent贡献度
                                              ├─ 各Agent详细报告
                                              └─ 分析任务指引
                                                          ↓
                                                     LLM 综合研判
                                                          ↓
                                                    FinalReport
```

---

## 2. Round 3 目标清单

| # | 优化 | 产出 | 优先级 |
|---|------|------|--------|
| 3.1 | 可追溯决策链 | `aggregator.py` + `result.py` | ⭐⭐⭐ |
| 3.2 | 改进建议生成 | `aggregator.py` `_suggest_improvements()` | ⭐⭐ |
| 3.3 | 多时间维度并行输出 | `aggregator.py` 升级 | ⭐⭐ |
| 3.4 | 决策日志与审计 | `prediction_store` + 新表 | ⭐ |
| 3.5 | 对比模式（多标的） | `scripts/` 新脚本 | ⭐ |

---

## 3. 优化一：可追溯决策链

### 3.1 问题

当前 FinalReport 的 `summary` 是 LLM 的一段文字。用户无法快速追溯"这个结论是怎么得出来的"，需要读完整段文字。

### 3.2 方案：决策链 JSON

```python
@dataclass
class FinalReport:
    # 🆕 Round3
    decision_trace: dict = field(default_factory=dict)
    # {
    #     "final_direction": "bearish",
    #     "steps": [
    #         {"step": "质量评估", "output": "新闻poor(×0.5), 基本面partial(×0.6)"},
    #         {"step": "权重计算", "output": "技术30%→38%, 新闻20%→5%, ..."},
    #         {"step": "分歧检测", "output": "severe, 3方向各1, 熵=1.0"},
    #         {"step": "LLM研判", "output": "优先技术面(数据最完整)...", "confidence": 0.55},
    #     ],
    #     "data_sources_used": ["腾讯K线(实时)", "akshare财务(Q1)", "东方财富CPI(实时)"],
    #     "data_sources_missing": ["港股新闻(无)", "行业PE(参考值)"],
    # }
```

### 3.3 在 to_markdown 中展示

```markdown
## 🔗 决策追溯

1. **质量评估** → 新闻标注 poor(知识库), 基本面标注 partial(数据缺)
2. **权重调整** → 新闻 20%→5%, 基本面 12%→8%, 技术面 30%→38%
3. **分歧检测** → severe (看涨1/看跌1/中性1, 熵=1.0)
4. **最终研判** → LLM 综合: 优先技术面(数据完整+权重高) → 看跌 55%

📡 数据来源: 腾讯K线(实时) ✅ | akshare财务 ✅ | 东方财富CPI ✅
📡 数据缺失: 港股新闻 ❌ | 行业PE ⚠️(参考值)
```

---

## 4. 优化二：改进建议生成

### 4.1 问题

汇总只输出"结论"，不告诉用户"下次怎么做得更好"。

### 4.2 方案

```python
def _suggest_improvements(self, quality_scores, disagreement, failed_agents):
    """
    基于本轮分析的质量问题，生成改进建议
    
    规则引擎（不用 LLM，零成本）:
    """
    suggestions = []
    
    # 数据缺失建议
    poor_agents = [n for n, q in quality_scores.items() if q["data_quality"] == "poor"]
    if poor_agents:
        suggestions.append(f"⚠️ {', '.join(poor_agents)} 数据严重不足，建议检查数据源")
    
    # 分歧建议
    if disagreement.get("level") in ("high", "severe"):
        suggestions.append("🔀 Agent 间分歧严重，建议等待更多信息后重新分析")
    
    # 失败 Agent 建议
    if failed_agents:
        suggestions.append(f"❌ {len(failed_agents)}个Agent失败，建议检查网络或API配置")
    
    return suggestions
```

### 4.3 在报告中展示

```markdown
## 💡 改进建议

- ⚠️ 最新新闻分析师数据严重不足（知识库兜底），建议配置实时新闻源
- 🔀 Agent 间分歧严重（3个不同方向），建议等待更多信息后重新分析
```

---

## 5. 优化三：多时间维度并行输出

### 5.1 问题

当前一次只分析一个时间维度（短期/中期/长期）。用户想看"短期怎么看、中期怎么看"需要跑三次。

### 5.2 方案

```python
# run_analysis.py 新增 --multi-timeframe
async def analyze_multi_timeframe(target):
    results = {}
    for tf, label in [("短期(1周)", "short"), ("中期(1月)", "medium"), ("长期(1季)", "long")]:
        # 只对"近期股价"和"公司前景"重新分析（不同时间维度需要不同周期数据）
        # 新闻、宏观、行业可复用
        results[label] = await analyze_single(target, tf)
    
    # 汇总对比
    return MultiTimeframeReport(results)
```

### 5.3 输出

```markdown
## 📅 多时间维度对比

| 维度 | 方向 | 幅度 | 置信度 | 主导信号 |
|------|------|------|--------|---------|
| 短期(1周) | 📉 看跌 | -3%~+1% | 55% | 技术面空头 |
| 中期(1月) | ➡️ 震荡 | -5%~+5% | 40% | 信号矛盾 |
| 长期(1季) | 📈 看涨 | +5%~+15% | 35% | 低估值修复 |

💡 短期看跌但长期看涨 → 可能存在"短期挖坑、中期填坑"的机会
```

---

## 6. 优化四：决策日志与审计

### 6.1 问题

无法回溯"某次预测时汇总 Agent 收到了什么信息、做了什么判断"。

### 6.2 方案

在 `prediction_store` 中新增 `aggregation_log` 表：

```sql
CREATE TABLE IF NOT EXISTS aggregation_log (
    prediction_id TEXT PRIMARY KEY,
    quality_scores TEXT,        -- JSON: 各Agent质量评分
    original_weights TEXT,      -- JSON: 静态权重
    adjusted_weights TEXT,      -- JSON: 动态调整后权重
    disagreement TEXT,          -- JSON: 分歧量化结果
    contributions TEXT,         -- JSON: 贡献度
    llm_prompt_preview TEXT,    -- LLM收到的prompt摘要
    created_at TEXT
);
```

每次汇总完成后自动写入。

---

## 7. 优化五：对比模式

### 7.1 场景

"帮我对比分析 000001(平安银行) vs 600036(招商银行)" 

### 7.2 实现

```bash
python3 scripts/compare.py --targets 000001,600036
```

输出两个标的的并行对比报告，包含：
- 各维度方向对比表
- 汇总判断对比
- 相对强弱分析

---

## 8. 实施优先级

```
优先级  优化项                    预期收益  工作量
────── ───────────────────────   ──────  ─────
 ⭐⭐⭐  优化一: 可追溯决策链         中       低
 ⭐⭐    优化二: 改进建议生成         中       低
 ⭐⭐    优化三: 多时间维度并行        高       中
 ⭐      优化四: 决策日志            中       低
 ⭐      优化五: 对比模式            中       高
```

---

## 附录：汇总 Analyst 完整进化路线

```
Phase 1/2 (最初):
  收集 → 简单加权 → LLM → 输出

Round 1 (质量感知):
  收集 → [质量评分+动态权重] → LLM → 输出

Round 2 (分歧量化):
  收集 → [质量+权重] → [分歧+贡献度] → LLM → 输出

Round 3 (可追溯):
  收集 → [质量+权重+分歧+贡献度] → [决策链记录] → LLM → [改进建议] → 输出
```

---

> 📌 **Round 3 的核心思想**：让汇总从"输出结论"升级到"输出结论 + 解释过程 + 给出建议"。不仅能回答"怎么看"，还能回答"为什么这么看"和"下次怎么更好"。
