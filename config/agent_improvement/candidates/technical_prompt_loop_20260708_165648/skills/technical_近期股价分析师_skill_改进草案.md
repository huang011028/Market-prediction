```markdown
# 近期股价分析师 skill 改进草案

## 1. 背景
近期股价分析师 skill 在历史回测中出现大量低命中率场景桶，多项关键分桶的准确率低于 10%，甚至为 0%。亟需通过对分桶规则、特征工程和反例逻辑的审查补强，提升在弱市场结构中的解释能力和命中稳定性。

## 2. 历史失败信号清单 (P0)
以下分桶因命中率过低(< 10% 或 0%)被标记为 **wrong_strategy**，需优先修正或增加 guardrail：

### 2.1 零准确率分桶（acc = 0.0%，需立即处理）
- `technical_scenario_buckets/bear_trend|near_support|shrinking` (n=12)
- `regime_sr_buckets/transition_up|near_resistance` (n=18)
- `regime_volume_buckets/bear_rebound|shrinking` (n=18)
- `regime_volume_buckets/transition_down|shrinking` (n=17)
- `regime_volume_buckets/bear_rebound|neutral` (n=15, acc=6.7%)
- `regime_sr_buckets/sideways_range|near_resistance` (n=14, acc=7.1%)
- `technical_scenario_buckets/bull_trend|near_resistance|neutral` (n=26, acc=7.7%)
- `regime_volume_buckets/bull_pullback|confirm_up` (n=13, acc=7.7%)
- `technical_scenario_buckets/bull_trend|near_resistance|shrinking` (n=11, acc=9.1%)
- `regime_sr_buckets/bear_rebound|squeeze` (n=11, acc=9.1%)
- `technical_scenario_buckets/bull_trend|squeeze|neutral` (n=10, acc=10.0%)

### 2.2 极低准确率分桶（acc 10%-20%）
- `sr_volume_buckets/near_support|confirm_down` (n=45, acc=11.1%)
- `regime_volume_buckets/sideways_range|neutral` (n=26, acc=11.5%)
- `market_regime_buckets/bear_rebound` (n=60, acc=11.7%)
- 等（详见完整清单）

## 3. 根因推测
- **特征交互过于简单**：仅依赖支撑/阻力位置、成交量变化、趋势方向等少数维度，未引入动量背离、波动率偏度、市场参与者结构等辅助信号。
- **阈值刚性**：对“near support”、“shrinking volume”等定义阈值固定，未根据波动率环境自适应，导致在扩散行情中频繁触发失效。
- **缺乏反例约束**：当前 skill 在不利确认（如 confirm_down 与支撑位共存）下仍可能给出看多判断，没有“相反证据否决”机制。
- **分桶定义粒度过细**：部分桶样本量极小（如 n≤10），导致统计噪音大，应合并相近场景或提高触发门槛。

## 4. 改进方案（声明式 guardrail 建议）

### 4.1 引入「高不确定性」拦截规则
增加以下 guardrail 条件，当任一满足时 skill 必须输出 **“无法判断”** 或转而仅提供风险提示，不输出多头/空头结论：

| 规则 ID | 条件 | 适用桶 |
|---------|------|--------|
| GRD‑01 | `trend = bear_trend` AND `sr_position = near_support` AND `volume_change = shrinking` | bear_trend\|near_support\|shrinking |
| GRD‑02 | `market_regime = transition_up` AND `sr_position = near_resistance` | transition_up\|near_resistance |
| GRD‑03 | `market_regime = bear_rebound` AND `volume_change = shrinking` | bear_rebound\|shrinking |
| GRD‑04 | `market_regime = transition_down` AND `volume_change = shrinking` | transition_down\|shrinking |
| GRD‑05 | `market_regime = bear_rebound` AND `volume_change = neutral` | bear_rebound\|neutral |
| GRD‑06 | `market_regime = sideways_range` AND `sr_position = near_resistance` | sideways_range\|near_resistance |
| GRD‑07 | `trend = bull_trend` AND `sr_position = near_resistance` AND `volume_change = neutral` | bull_trend\|near_resistance\|neutral |
| GRD‑08 | `market_regime = bull_pullback` AND `volume_change = confirm_up` | bull_pullback\|confirm_up |
| GRD‑09 | `trend = bull_trend` AND `sr_position = near_resistance` AND `volume_change = shrinking` | bull_trend\|near_resistance\|shrinking |
| GRD‑10 | `market_regime = bear_rebound` AND `sr_position = squeeze` | bear_rebound\|squeeze |
| GRD‑11 | `trend = bull_trend` AND `sr_position = squeeze` AND `volume_change = neutral` | bull_trend\|squeeze\|neutral |

*后续可根据更多低准确率桶扩展 GRD 列表。*

### 4.2 动态阈值与波动率自适应
- 将 `near_*` 的固定距离（例如 ±2%）改为基于 ATR 或近期波动率百分位的动态阈值。
- 将 `shrinking` 成交量条件关联至平均成交量下降比例 + 波动率环境，若波动率扩张则仍可视为中性。

### 4.3 反例否决逻辑
- 在做多场景下，若出现 `confirm_down` 成交量确认，且价格紧贴阻力位，skill 必须降低置信度至少两档或转为观望。
- 新增复合否决：`(regime_sr_bucket == near_resistance) AND (volume_confirmation == confirm_down)` → 多头信号无效。
- 同样，在空头场景中，若成交量显示 `confirm_up` 且支撑位附近，强制否决。

### 4.4 分桶合并与小样本处理
- 将样本量 < 15 的桶合并至更大类，例如 `bear_rebound|squeeze` 归入 `bear_rebound` 大类，仅在内部降权。
- 合并后统一应用上述 guardrail。

## 5. 验证计划
使用相同历史样本回放修改后的 skill，重点监控以下指标：
- P0 桶准确率是否脱离 0% 或提升至 > 20%
- 整体召回率是否因增加“无法判断”而大幅下降 (< 10% 降幅可接受)
- “无法判断”输出占比控制在 15% 以内，否则调整 guardrail 条件放宽或收紧

## 6. 预期效果
实施上述 guardrail 后，将显著降低错误信号输出，尤其在极端弱势场景下避免盲目看多。中长期可结合更多高频信号和基本面催化剂进一步优化。

---
**修订记录**：初稿基于 2025‑03‑26 回测信号生成，待执行后反馈。
```