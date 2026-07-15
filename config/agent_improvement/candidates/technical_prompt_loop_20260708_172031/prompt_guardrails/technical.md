# 近期股价分析师 Prompt Guardrail 改进草案

## 背景
近期股价分析师的 prompt 场景在历史回放中出现大面积低命中信号，大量细分市场技术组合（technical_scenario_buckets、regime_sr_buckets、regime_volume_buckets 等）的准确率低于 20%，许多桶甚至为零。为确保输出质量，需要引入自检机制，强制模型在面对高风险场景时降低置信、提供反向证据，并保持高命中场景的保守策略，避免盲目乐观。

---

## 历史低命中信号分类统计

以下统计基于历史回放，仅保留准确率 ≤ 20% 且样本量 ≥ 10 的桶，作为重点防范目标。

### 零命中桶 (0.0% accuracy)

| 桶层级 | 场景组合 | 样本数 |
|--------|----------|--------|
| technical_scenario_buckets | bear_trend\|near_support\|shrinking | 20 |
| technical_scenario_buckets | sideways_range\|near_support\|confirm_down | 15 |
| technical_scenario_buckets | transition_up\|near_resistance\|neutral | 12 |
| regime_sr_buckets | bear_rebound\|upper_range | 12 |
| technical_scenario_buckets | transition_down\|near_support\|shrinking | 11 |
| technical_scenario_buckets | sideways_range\|near_resistance\|neutral | 11 |
| technical_scenario_buckets | sideways_range\|upper_range\|neutral | 10 |
| technical_scenario_buckets | bear_rebound\|squeeze\|neutral | 9 |
| technical_scenario_buckets | bear_rebound\|near_resistance\|shrinking | 9 |
| technical_scenario_buckets | sideways_range\|squeeze\|neutral | 8 |
| technical_scenario_buckets | bull_pullback\|near_support\|neutral | 8 |
| technical_scenario_buckets | transition_down\|near_support\|confirm_down | 7 |
| technical_scenario_buckets | transition_up\|near_resistance\|shrinking | 7 |
| technical_scenario_buckets | bull_pullback\|near_resistance\|confirm_up | 7 |
| technical_scenario_buckets | bear_rebound\|near_support\|confirm_down | 6 |
| regime_sr_buckets | transition_up\|lower_range | 6 |
| sr_volume_buckets | lower_range\|shrinking | 6 |
| technical_scenario_buckets | bull_pullback\|squeeze\|confirm_up | 5 |

### 极低命中桶 (≤ 20% accuracy)

| 桶层级 | 场景组合 | 样本数 | 准确率 |
|--------|----------|--------|--------|
| regime_sr_buckets | sideways_range\|near_resistance | 25 | 4.0% |
| regime_volume_buckets | bear_rebound\|neutral | 20 | 5.0% |
| regime_sr_buckets | transition_up\|near_resistance | 30 | 6.7% |
| regime_volume_buckets | sideways_range\|confirm_down | 30 | 6.7% |
| technical_scenario_buckets | bull_trend\|near_resistance\|shrinking | 15 | 6.7% |
| technical_scenario_buckets | bull_trend\|near_resistance\|neutral | 42 | 7.1% |
| regime_volume_buckets | transition_down\|shrinking | 27 | 7.4% |
| technical_scenario_buckets | bull_pullback\|upper_range\|shrinking | 13 | 7.7% |
| regime_volume_buckets | bull_pullback\|confirm_up | 25 | 8.0% |
| regime_volume_buckets | bear_rebound\|shrinking | 25 | 8.0% |
| regime_volume_buckets | bear_rebound\|confirm_down | 12 | 8.3% |
| regime_volume_buckets | bear_trend\|confirm_up | 11 | 9.1% |
| regime_sr_buckets | sideways_range\|upper_range | 20 | 10.0% |
| regime_volume_buckets | transition_down\|confirm_down | 10 | 10.0% |
| technical_scenario_buckets | bear_trend\|near_support\|confirm_down | 27 | 11.1% |
| regime_sr_buckets | bear_rebound\|squeeze | 17 | 11.8% |
| sr_volume_buckets | near_support\|confirm_down | 75 | 12.0% |
| regime_volume_buckets | sideways_range\|neutral | 41 | 12.2% |
| regime_sr_buckets | sideways_range\|squeeze | 32 | 12.5% |
| regime_sr_buckets | transition_down\|near_support | 32 | 12.5% |
| regime_sr_buckets | bear_trend\|near_support | 75 | 13.3% |
| technical_scenario_buckets | bull_trend\|squeeze\|neutral | 15 | 13.3% |
| regime_sr_buckets | transition_up\|squeeze | 15 | 13.3% |
| market_regime_buckets | bear_rebound | 82 | 13.4% |
| regime_volume_buckets | bear_trend\|confirm_down | 42 | 14.3% |
| regime_sr_buckets | bear_rebound\|near_resistance | 21 | 14.3% |
| sr_volume_buckets | near_resistance\|neutral | 107 | 15.0% |
| technical_scenario_buckets | bull_pullback\|near_support\|shrinking | 20 | 15.0% |
| regime_sr_buckets | bull_pullback\|upper_range | 20 | 15.0% |
| market_regime_buckets | sideways_range | 139 | 15.1% |
| sr_volume_buckets | near_resistance\|shrinking | 86 | 15.1% |
| regime_volume_buckets | bull_trend\|confirm_down | 26 | 15.4% |
| regime_volume_buckets | bear_trend\|shrinking | 50 | 16.0% |
| regime_sr_buckets | bull_pullback\|near_support | 43 | 16.3% |
| regime_volume_buckets | transition_up\|neutral | 36 | 16.7% |
| sr_volume_buckets | lower_range\|confirm_down | 12 | 16.7% |
| sr_volume_buckets | near_resistance\|confirm_down | 17 | 17.6% |
| technical_scenario_buckets | bull_trend\|near_support\|confirm_down | 11 | 18.2% |
| technical_scenario_buckets | transition_up\|near_resistance\|confirm_up | 11 | 18.2% |
| technical_scenario_buckets | transition_down\|near_resistance\|shrinking | 11 | 18.2% |
| technical_scenario_buckets | bull_pullback\|near_support\|confirm_up | 11 | 18.2% |
| technical_scenario_buckets | sideways_range\|squeeze\|shrinking | 16 | 18.8% |
| regime_volume_buckets | sideways_range\|shrinking | 37 | 18.9% |
| sr_volume_buckets | upper_range\|confirm_down | 21 | 19.0% |
| confidence_bins | 0.2-0.4 | 160 | 20.0% |
| sr_volume_buckets | near_support\|shrinking | 95 | 20.0% |
| technical_scenario_buckets | transition_down\|near_resistance\|neutral | 10 | 20.0% |

> 表中未列出但准确率在 0%–20% 之间的少量桶（如样本量 < 5）依然适用相同 guardrail。

---

## Prompt Guardrail 规则

将以下指令嵌入近期股价分析师的 prompt 内，要求模型在生成最终分析前执行自检流程。

### 1. 低命中场景触发条件
当当前分析的股票/品种同时满足任一**低命中桶**（即上表所列的 `technical_scenario_buckets`、`regime_sr_buckets`、`regime_volume_buckets`、`sr_volume_buckets` 等组合）时，必须强制激活“风险声明模式”。

### 2. 风险声明模式强制输出
在分析结论的**末尾**（或作为独立段落），模型必须包含以下三项：
- **反向证据**：至少列举两条与该场景历史表现相悖的技术面/市场因素，例如相反的趋势指标、未确认的成交量、支撑/阻力位破裂征兆等。
- **已消化信息清单**：简要列出模型在分析过程中考虑过的关键数据点，并标注其中哪些被弱化或排除的原因。
- **降置信理由**：明确说明为何本场景历史准确率极低，当前输出不可作为高置信判断，建议用户结合其他周期或信号验证。

示例格式：
```markdown
⚠️ 风险声明（基于历史低命中场景）
- 反向证据：
  1. 短期均线尚未形成死叉，量能萎缩可能只是盘整而非趋势反转。
  2. 同板块权重股已在阻力位出现买盘，不排除假跌破可能。
- 已消化信息：
  - 日线级别 MACD 死叉，但周线仍为多头排列；已弱化周线权重。
  - 成交量萎缩至 20 日均量下方；已纳入分析但未作为主要驱动。
- 降置信理由：
  该场景（bear_trend|near_support|shrinking）在历史回测中准确率为 0.0%，所有案例均未按预期方向运行，因此本分析仅作参考，不构成交易建议。
```

### 3. 高命中场景保守策略
对于历史准确率 ≥ 40% 的桶（如 `bull_trend|middle_range`、`bull_pullback|squeeze` 等），**禁止**自动提升全局置信度或省略反向证据检查。模型仍应按常规流程输出完整分析，但可在文末注明“该场景历史一致性较高，但仍需关注突发新闻干扰”。

### 4. 置信度区间调整
当命中置信度处于 0.2-0.4 区间（历史准确率 20%）时，最终结论的置信度应人为下调一档（若原本为“中等”，降低为“低”）。输出中须明确标明“经历史低命中场景修正后的置信度”。

---

## 声明式 Skill 定义（可选实施参考）

若系统支持声明式 prompt 技能，可按以下逻辑定义：

```yaml
skill:
  name: low_accuracy_guardrail
  description: 当近期股价分析师触发历史低命中桶时，追加风险声明
  trigger:
    any_bucket_in:
      - technical_scenario_buckets/bear_trend|near_support|shrinking
      - technical_scenario_buckets/sideways_range|near_support|confirm_down
      - technical_scenario_buckets/transition_up|near_resistance|neutral
      - ... (完整列表来自上述统计)
      - confidence_bins/0.2-0.4
  action:
    - require_output_section: "reverse_evidence"
    - require_output_section: "digested_info"
    - require_output_section: "confidence_downgrade_reason"
    - suppress_automatic_confidence_boost: true
    - override_confidence: "if original >= medium -> low"
  note: 无论是否满足高命中桶，本 skill 均强制输出风险声明，防止过度拟合。
```

> 实际集成时，需将 `any_bucket_in` 替换为全量低命中桶列表，并确保触发顺序优先于其他增强 prompt。

---

## 实施建议
1. **即时生效**：将该 guardrail 文本直接注入 “近期股价分析师” agent 的 `prompt` 字段末尾（或作为系统级指令）。
2. **监控反馈**：记录触发低命中桶后的分析输出，观察用户反馈以及后续真实走势，以迭代调整规则。
3. **定期更新**：每季度根据最新回放数据刷新低/高命中桶列表，保持自检规则与市场结构同步。
4. **灰度发布**：先对部分用户开放，对比无 guardrail 的基线，确认改进效果后全量推行。

---

*本草案仅针对 prompt 层面的防御性增强，不涉及任何代码修改或外部数据源接入。*