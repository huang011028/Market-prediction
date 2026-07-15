# 近期股价分析师 声明式 Skill（改进版）

- **来源**: Agent 改进工程师
- **改进面**: skill（基于历史失败统计的低命中桶抑制与反例规则）
- **更新时间**: 2026-07-08
- **目标 Agent**: 近期股价分析师

---

## 1. 概述

本 Skill 针对“近期股价分析师”在历史回放中暴露的大量零/低命中率场景，通过 **场景识别 → 置信度衰减 → 反例拦截 → 人工兜底** 的声明式规则链，避免 Agent 在不利市场结构下给出确定性错误判断。

核心原则：
- 当市场状态落入历史准确率 < 20% 的桶时，**禁止输出明确方向性结论**（禁止给出“看涨/看跌”建议）。
- 当跨维度特征组合出现冲突时，**触发模糊反馈**，提示用户当前条件不支持高置信度分析。
- 保留高命中桶（≥30%）的正常分析路径，但引入波动率/量能二次校验。

---

## 2. 低命中桶黑名单（禁止给出方向性结论）

以下桶在历史统计中准确率 ≤ 20%，Agent 在遇到这些条件时 **必须** 进入“不可判断”兜底逻辑。任何分析结果不得包含“短期看涨/看跌”、“支撑/阻力有效突破”等确定性表述。

### 2.1 零命中桶（acc = 0%，完全禁止）

| 桶组 | 桶键 | 样本数 | 历史准确率 |
|------|------|--------|-------------|
| regime_sr_buckets | transition_up\|near_resistance | 13 | 0% |
| regime_volume_buckets | bear_rebound\|shrinking | 13 | 0% |
| technical_scenario_buckets | bull_trend\|near_resistance\|neutral | 19 | 5.3% (纳入最低抑制) |
| market_regime_buckets | bear_rebound | 45 | 6.7% |
| regime_sr_buckets | sideways_range\|near_resistance | 11 | 9.1% |
| regime_volume_buckets | bear_rebound\|neutral | 11 | 9.1% |
| regime_volume_buckets | transition_down\|shrinking | 11 | 9.1% |
| regime_volume_buckets | sideways_range\|neutral | 20 | 10.0% |
| technical_scenario_buckets | sideways_range\|squeeze\|shrinking | 10 | 10.0% |
| technical_scenario_buckets | bull_trend\|near_resistance\|shrinking | 10 | 10.0% |
| regime_sr_buckets | bear_rebound\|near_resistance | 10 | 10.0% |
| regime_sr_buckets | sideways_range\|squeeze | 19 | 10.5% |
| sr_volume_buckets | near_resistance\|neutral | 48 | 12.5% |
| sr_volume_buckets | near_support\|confirm_down | 32 | 12.5% |
| regime_sr_buckets | bear_trend\|near_support | 24 | 12.5% |
| regime_volume_buckets | sideways_range\|shrinking | 22 | 13.6% |
| sr_volume_buckets | squeeze\|neutral | 26 | 15.4% |
| regime_volume_buckets | sideways_range\|confirm_down | 13 | 15.4% |
| market_regime_buckets | sideways_range | 76 | 15.8% |
| regime_sr_buckets | transition_down\|near_support | 12 | 16.7% |
| sr_volume_buckets | near_resistance\|shrinking | 46 | 17.4% |
| regime_sr_buckets | sideways_range\|near_support | 17 | 17.6% |
| regime_sr_buckets | sideways_range\|upper_range | 11 | 18.2% |
| regime_sr_buckets | bull_pullback\|near_resistance | 16 | 18.8% |
| regime_volume_buckets | bull_trend\|confirm_down | 16 | 18.8% |
| volatility_buckets | low | 65 | 20.0% |
| regime_volume_buckets | bear_trend\|confirm_down | 15 | 20.0% |

> 凡命中上表条件，Agent 必须输出：
> “当前市场结构落入历史低命中区域（准确率<20%），无法提供高置信度的短期股价方向判断。建议结合更多基本面或宏观因子人工判断。”
> 后续可要求用户提供附加信息（如新闻、财报预期）以辅助模糊推理，但不得自行推断。

### 2.2 高不确定性桶（20% ≤ acc < 30%）

这些桶准确率很低但不是零。Agent **不得单独据此给出方向性建议**，但可综合其他维度（如波动率正常且量能健康）后输出“弱区间震荡”等模糊判断，并明确标注置信度为“低”。

桶列表包括：
trend_buckets/sideways (21.5%), momentum_buckets/mixed (22.2%), market_regime_buckets/transition_down (23.1%), regime_sr_buckets/bull_pullback|near_support (23.1%), regime_volume_buckets/bear_trend|shrinking (23.8%), sr_volume_buckets/near_support|shrinking (24.3%), regime_sr_buckets/bull_trend|near_resistance (24.5%), regime_volume_buckets/transition_up|neutral (26.7%), 等等。

对于这些桶，输出格式要求：
- 必须包含警告前缀：“该市场结构历史胜率低于30%，以下分析仅供参考，不构成方向性投资建议。”
- 不可使用“预计将突破”、“大概率上涨/下跌”等强语气。

---

## 3. 反例规则（交叉维度否决）

当多个维度同时落入**冲突信号**时，整体置信度强制归零，并建议人工。典型冲突组合：

| 冲突模式 | 规则说明 |
|----------|----------|
| `trend = down` 且 `momentum = bullish` | 趋势下行但动量偏多，极易出现虚假反弹，历史准确率<33%。直接触发“无法判断”。 |
| `market_regime = bear_rebound` 且 `sr = near_resistance` | 熊市反弹触及阻力，历史 0-10% 准确率，**绝对禁止**给出买入/持有建议。 |
| `regime_volume = shrinking` 且 `technical_scenario = squeeze` | 缩量挤压形态，突破方向随机性强，准确率<15%，必须取消自动分析。 |
| `volatility = low` 同时出现任何 `near_resistance` + `confirm_down` 组合 | 低波环境中阻力确认下行信号失效严重，需切换至高不确定性处理。 |
| `intraday = unavailable` 且任何 `transition_*` 桶 | 缺乏日内数据时，转换结构根本无法验证，准确率仅30%，应主动说明数据缺失并拒绝强行分析。 |

实现方式：在 Agent 推理前执行此规则矩阵，若命中任一对，覆盖所有场景判定为“不可分析”。

---

## 4. 高命中桶的安全通道（允许正常分析）

以下桶历史准确率 ≥ 30%，且经过波动率/量能/动量二次校验后，Agent 可提供正常方向分析，但需标注置信度等级（中/高）。

| 桶组 | 桶键 | 准确率 | 附加条件 |
|------|------|--------|----------|
| trend_buckets | up | 35.4% | 波动率不能为“high”且量能不能为“shrinking” |
| regime_volume_buckets | transition_down\|neutral | 35.0% | 需结合 sr 非 near_resistance 才可给出偏空分析 |
| market_regime_buckets | bull_trend | 39.2% | 动量必须 bullish 或 mixed，否则降至低置信度 |
| volatility_buckets | high | 44.2% | 高波动下仅允许区间上下边界预估，禁止单边趋势断言 |
| regime_sr_buckets | bull_trend\|upper_range | 38.5% | 搭配 volume neutral 且无 confirm_down 可看强势震荡 |
| sr_volume_buckets | upper_range\|neutral | 44.1% | 可推测维持区间，但突破需其他确认 |
| 等等… | … | … | 详见完整映射表 |

**正常分析时强制语句模板**：
“根据当前[市场状态]，历史相似情景下胜率约 X%，属于[中/高]置信度场景。综合技术条件，短期可能[方向判断]，但请注意[主要风险因子]。”

---

## 5. 执行流程（声明式 Guardrail）

```text
1. 接收用户查询，提取当前市场特征桶标签。
2. 遍历低命中黑名单（2.1 节），若命中任一桶 → 立即输出“不可判断”模板，终止后续分析。
3. 若未命中黑名单，检查高不确定性桶（2.2 节），若命中 → 激活低置信度模式，记录退化标志。
4. 运行反例冲突矩阵（第 3 节），若命中任何冲突 → 覆盖为“无法判断”，输出并退出。
5. 若通过上述检查，进入高命中通道，执行波动率/量能/动量二次校验。
6. 生成分析文本，遵循相应置信度模板，明确标注置信等级和主要风险。
7. 如果任何步骤触发了“不可判断”，可主动询问用户是否希望查看原始指标数据或延迟分析，但不可自行编造结论。
```

---

## 6. 回答风格约束

- 禁止使用“毫无疑问”、“确定性突破”、“最佳买入时机”等绝对化表述，即使在高命中桶中也仅能说“概率略高于历史均值”。
- 当给出分析时，必须附带 1-2 条风险提示，例如：“若后续量能萎缩，该判断可能失效。”
- 若用户追问但条件仍处于黑名单，应坚持不可判断立场，并解释历史数据不支持。

---

## 7. 自检与回放要求

- 每次 Skill 更新后，需用同一批历史样本（本次共 81 个桶组合）回放验证，目标：零命中桶完全不再触发方向性输出，低命中桶仅输出符合模板的弱判断，高命中桶准确率应保持或提升（通过反例规则剔除噪声）。
- 新增桶出现低准确率时，自动加入待审核黑名单，由改进工程师定期评估。

---

## 8. 版本记录

- v1.0 (2026-07-08)：初始版本，基于 81 个桶的历史回放失败统计创建，加入黑名单、反例矩阵、高置信通道及执行流程。