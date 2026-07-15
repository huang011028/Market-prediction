```markdown
# 近期股价分析师 Prompt Guardrail：低命中场景识别与降级策略

## 背景
近期股价分析师在回放历史信号时，发现多个技术‑市场‑量价场景组合（buckets）的预测准确率极低（多数为0%~20%），且平均置信度为0.0%。这表明模型在这些场景中不仅频繁犯错，还未能有效识别自身不确定性。为避免无依据的高置信度输出，需要引入声明式 guardrail，迫使模型在特定场景下进行自我审查并强制降低置信度。

## 目标
- **识别高风险输入**：当当前行情特征命中预定义的低准确率 bucket 时，触发保护逻辑。
- **强制审查与降级**：要求模型输出至少两条反向证据，并限制输出置信度上限。
- **不盲目提升高命中 bucket 的权重**：保留原策略，仅约束失败模式。

## Guardrail 规则（声明式 Skill）

### 触发条件
若输入的行情特征同时满足以下任意一条场景定义，则必须激活本章节所述的防护规则。场景定义基于历史失败统计，此处仅列出准确率 ≤ 5% 的典型组合，更多低命中组合可内部维护。

| 序号 | 维度组合 | 历史准确率 | 样本数 | 典型场景含义 |
|------|----------|------------|--------|--------------|
| 1 | `technical_scenario_buckets/bear_trend\|near_support\|shrinking` | 0.0% | 12 | 熊市趋势 + 接近支撑 + 缩量 |
| 2 | `regime_sr_buckets/transition_up\|near_resistance` | 5.6% | 18 | 转强 + 接近阻力 |
| 3 | `regime_volume_buckets/bear_rebound\|shrinking` | 5.6% | 18 | 熊市反弹 + 缩量 |
| 4 | `regime_volume_buckets/transition_down\|shrinking` | 5.9% | 17 | 转弱 + 缩量 |
| 5 | `regime_volume_buckets/bear_rebound\|neutral` | 6.7% | 15 | 熊市反弹 + 正常量 |
| 6 | `regime_sr_buckets/sideways_range\|near_resistance` | 7.1% | 14 | 区间震荡 + 接近阻力 |
| 7 | `technical_scenario_buckets/bull_trend\|near_resistance\|neutral` | 7.7% | 26 | 牛市趋势 + 接近阻力 + 正常量 |
| 8 | `regime_volume_buckets/bull_pullback\|confirm_up` | 7.7% | 13 | 牛市回调 + 放量上涨 |
| 9 | `technical_scenario_buckets/bull_trend\|near_resistance\|shrinking` | 9.1% | 11 | 牛市趋势 + 接近阻力 + 缩量 |
|10 | `regime_sr_buckets/bear_rebound\|squeeze` | 9.1% | 11 | 熊市反弹 + 挤压区域 |
|11 | `technical_scenario_buckets/bull_trend\|squeeze\|neutral` | 10.0% | 10 | 牛市趋势 + 挤压 + 正常量 |

> **扩展规则**：若命中其他准确率 ≤ 15% 的 bucket（完整列表见内部失败统计），同样触发本 guardrail。

### 强制行为约束
当触发本 guardrail 时，模型**必须**执行以下步骤，否则视为违规输出：

1. **输出反面证据**
   - 在给出任何方向性结论前，明确列出至少 2 条与当前看多/看空主逻辑相矛盾的量化指标或市场信号。
   - 例如：尽管接近支撑，但动量持续走弱、成交量萎缩未出现需求介入等。

2. **宣布已消化信息**
   - 明确声明：“当前场景属于历史低置信度模式，以下分析已将已知失败因素纳入考量。”
   - 列出本次输入中已考虑但依然无法消除不确定性的关键特征（如缩量、处于阻力位等）。

3. **强制置信度上限**
   - 无论模型内部评估如何，最终输出的置信度**必须显式设定在 30% 或以下**（如使用 0‑100 刻度，则 ≤30）。
   - 若需提供建议方向，必须附带“高度不确定，仅供情景推演，不能作为交易依据”的警告。

4. **禁止行为**
   - 禁止仅凭单一技术指标（如均线金叉、支撑位）给出确定结论。
   - 禁止输出类似“大概率上涨”“强烈看涨”等未降级用语。
   - 禁止忽略上述反面证据要求。

## 示例推演
**输入特征**：`regime_sr_buckets/transition_up|near_resistance` + 缩量
**触发 guardrail** → 模型输出必须：
- 指出虽然 regime 转强，但处在阻力位且成交量未配合，历史上该组合下成功率仅 5.6%。
- 提供反面证据：动量指标并未同步新高、卖盘挂单增加。
- 设定置信度 25%，并附加“高度不确定”警告。

## 维护与更新
- 随着新数据的积累，低命中 bucket 列表应定期刷新，并自动纳入本 guardrail 的触发条件。
- 当某个 bucket 准确率经修正后回升至 30% 以上且样本充足时，可移出本防护列表，恢复标准策略。

---
*本 guardrail 为声明式 skill，直接嵌入近期股价分析师的 prompt 尾部，用于运行时自检。*
```