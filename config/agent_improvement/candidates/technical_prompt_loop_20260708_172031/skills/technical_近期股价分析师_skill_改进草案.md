```markdown
# 候选人：近期股价分析师 skill 改进 v2（含 guardrail 声明）

## 背景与问题概述
- **Agent**：近期股价分析师
- **问题**：大量历史分桶场景准确率极低（多数为 0%），尤其是 `technical_scenario_buckets`、`regime_sr_buckets` 等复合桶，表明现有规则对这些市场环境的判断完全失效。
- **目标**：在保留原有有效逻辑的同时，为低命中率场景引入显式抑制，避免输出高置信但错误的分析结论。

## 总体改动策略
1. 对于以下“禁止桶”，**直接输出 `confidence=low` 且建议“当前环境信号混乱，暂不给出明确方向判断，建议观望”**。
2. 对于准确率 ≤ 20% 的场景，进行**弱化处理**：仅保留趋势描述，不给出方向性操作建议，同时标注“历史匹配度较低”。
3. 保留现有高准确率桶（如 `bull_trend` 近支撑等）的原有逻辑，但要求其触发时附带更多确认信号。
4. 增加反例规则：当落入禁止桶时，即使个别指标符合看涨/看跌特征，也不得输出明确结论，除非有强异动（成交量突增、波动率飙升等）——此时需特别标注“低概率事件”。

## 声明式规则（伪代码 + 描述）

### 1. 禁止桶列表（acc = 0.0% 且 n ≥ 5）
若当前市场状态匹配下列任一桶，则强制进入 **“unsafe scene”** 模式：

| 桶分组 | 桶标识 | 说明 |
|--------|--------|------|
| `technical_scenario_buckets` | `bear_trend\|near_support\|shrinking` | 熊市近支撑缩量 |
| `technical_scenario_buckets` | `sideways_range\|near_support\|confirm_down` | 震荡近支撑确认下跌 |
| `technical_scenario_buckets` | `transition_up\|near_resistance\|neutral` | 转升近阻力中性 |
| `regime_sr_buckets` | `bear_rebound\|upper_range` | 熊市反弹区间上沿 |
| `technical_scenario_buckets` | `transition_down\|near_support\|shrinking` | 转跌近支撑缩量 |
| `technical_scenario_buckets` | `sideways_range\|near_resistance\|neutral` | 震荡近阻力中性 |
| `technical_scenario_buckets` | `sideways_range\|upper_range\|neutral` | 震荡区间上沿中性 |
| `regime_sr_buckets` | `sideways_range\|near_resistance` | 震荡近阻力（acc 4%） – 提升为禁止 |
| `regime_volume_buckets` | `bear_rebound\|neutral` | 熊市反弹中性量（acc 5%） – 提升为禁止 |
| … （此处仅列举 top 低准确率，实际实现时应包含所有 acc=0 或 <5% 的桶） |

**guardrail 行为**：
- 输出 `prediction = "unclear"`, `confidence = 0.0`
- 附加消息：“当前技术形态（{bucket_label}）历史准确率极低，系统不会生成方向性预测。”
- 若用户要求分析，可提供基础技术描述但不给出操作建议。

### 2. 弱化桶列表（5% < acc ≤ 20%）
对该类场景：
- 降低自动置信度至 `min(原置信度, 0.3)`。
- 输出意见时添加前缀：“⚠️ 历史回溯显示该形态可靠性较低，以下分析仅供参考”。
- 不允许自动触发确定性买卖指令。

示例桶：
- `regime_sr_buckets/sideways_range|upper_range` (10%)
- `sr_volume_buckets/near_support|confirm_down` (12%)
- `regime_volume_buckets/sideways_range|neutral` (12.2%)
- `regime_sr_buckets/bear_trend|near_support` (13.3%)
- `market_regime_buckets/bear_rebound` (13.4%)
- … （完整清单从历史统计中提取）

### 3. 强化桶（acc > 35%）加强确认
对于准确率较高的桶（如 `market_regime_buckets/bull_trend` 35.6%），保留原策略，但增加**反例校验**：
- 在输出前，检查是否同时落在任何“禁止桶”或“弱化桶”的分组中。若是，则按优先级较低的规则处理。
- 要求两个独立技术指标（如 RSI 和 MACD）方向一致，否则降低置信度。

## 验证方法
- 使用同一批历史样本回放，验证：
  - 原禁止桶场景下不再输出高置信预测。
  - 弱化桶场景下输出置信度 < 0.3 或包含风险提示。
  - 整体准确率（f1）无明显下降，但拒绝率上升，符合风险控制目标。

## 实施步骤
1. 将上述桶名单硬编码为配置常量。
2. 在 skill 的规则匹配后，增加一个后处理步骤：
   ```
   if bucket in BLOCKED_BUCKETS:
       return guarded_neutral_response(bucket)
   elif bucket in WEAKENED_BUCKETS:
       return weakened_confident_response(bucket)
   else:
       proceed_as_normal()
   ```
3. 修改后重新运行历史回放，统计新准确率和拒识率。

## 期望效果
- 消除 0% 准确率场景对终端用户的误导。
- 提升整体交付质量，使分析师只在高可信场景下发表明确判断。
- 保留未来根据新数据调整桶阈值的能力。

---
*由 Agent 改进工程师基于历史失败统计生成，仅用于 Skill 定义更新，不涉及源码或 MCP。*
```