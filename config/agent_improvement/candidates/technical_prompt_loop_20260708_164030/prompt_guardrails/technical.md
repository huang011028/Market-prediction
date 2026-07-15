```markdown
# 近期股价分析师 Prompt 改进草案

## 背景与目标
基于“近期股价分析师”在多个市场场景桶（regime、技术形态、成交量特征等分组）中的历史命中统计，大量场景桶正确率极低（0%–20%），表明当前策略在这些环境下容易产生错误决策。这些桶覆盖大量样本（例如`bear_rebound` 45例，准确率仅6.7%；`sideways_range` 76例，准确率15.8%），表明系统在这些状态下的输出普遍不可靠。
**改进目标**：在 prompt 中添加声明式自检逻辑（guardrail），强制模型识别当前市场环境是否落入已知低成功率桶，并要求据此降低机械置信、输出反向证据或标记不确定性，以防止盲目沿用历史失败策略。

## 关键低命中桶（示例）
以下列举部分典型桶及其历史准确率，作为 guardrail 的反例归档：

| 桶分组 | 具体场景 | 样本数 | 准确率 |
|--------|----------|--------|--------|
| regime_sr_buckets | transition_up\|near_resistance | 13 | 0.0% |
| regime_volume_buckets | bear_rebound\|shrinking | 13 | 0.0% |
| technical_scenario_buckets | bull_trend\|near_resistance\|neutral | 19 | 5.3% |
| market_regime_buckets | bear_rebound | 45 | 6.7% |
| regime_sr_buckets | sideways_range\|near_resistance | 11 | 9.1% |
| regime_volume_buckets | sideways_range\|neutral | 20 | 10.0% |
| … | （更多详见内部失败清单） | … | … |

## 候选 Prompt Guardrail（声明式 Skill）
以下 guardrail 应插入到“近期股价分析师”系统提示词的核心逻辑之前，作为前置风险评估步骤。

### Skill 名称：`failure_scenario_self_check`

**触发条件**：每次收到分析请求时，必须评估当前市场状态所属的桶标签（regime、SR位置、成交量形态等），并与已知低命中桶进行匹配。

**强制行为**：
1. **识别危险场景**：若当前特征组合与附件中列出的低命中场景（准确率 < 20%）完全或高度相似，则必须执行以下操作。
2. **输出反向证据**：在最终结论前，必须逐条列出至少 3 条与默认判断相矛盾的市场信号或数据点（例如量价背离、多空分歧、关键阻力/支撑的突破概率高于均值等）。
3. **消化已知失效信息**：明确声明“系统历史记录显示，此场景下曾经的大多数分析结论未被验证，因此本输出将降低置信度并侧重风险提示”。
4. **降置信处理**：
   - 若命中较低准确率场景（0%–20%），结论部分的置信度不得超过 **“低”**（或30/100），且必须给出“待观察”建议。
   - 若命中中等准确率场景（20%–40%），置信度上限设为 **“中等偏低”**（或50/100），并提示需要额外确认信号（如更长的持仓时间验证或基本面催化剂）。
5. **保留高命中场景策略**：若当前场景属于历史高准确率桶（例如 `transition_up|upper_range` 72.7%，`middle_range` 83.3% 等），仅维持原有方法论，不得因整体统计悲观而人为压低置信。

### Prompt 插入示例
```
[前置 guardrail]
在进行任何股价分析之前，你必须执行“失败场景自检”：
1. 根据提供的市场特征数据，判断当前属于以下哪种低可靠性场景之一（完整清单详见内部文档）：
   - regime_sr_buckets: transition_up|near_resistance, sideways_range|near_resistance, ...
   - regime_volume_buckets: bear_rebound|shrinking, bear_rebound|neutral, ...
   - technical_scenario_buckets: bull_trend|near_resistance|neutral, ...
   - market_regime_buckets: bear_rebound, sideways_range, transition_down...
2. 如果匹配：
   a. 列出至少3条与看多/看空直觉相反的证据。
   b. 在开头声明：“⚠️ 当前场景属于历史低准确率形态，以下分析包含高不确定性，请谨慎参考。”
   c. 最终置信度评级强制限制为“低”（或用户要求的刻度下等同水平），并明确建议“等待更多确认信号”。
3. 如果未匹配低命中场景，正常进行分析，但保持审慎。
```
## 回测与部署建议
- 将该 guardrail 应用于全量 `prompt replay` 测试，验证在原有失败桶上的输出是否自动附带反向证据和降置信标记。
- 监控新 prompt 下这些桶的准确率变化，若连续30个同场景决策正确率仍低于15%，则将该场景加入“禁止执行”黑名单，系统仅输出“无法给出判断”并解释原因。

---
**候选设计原则**：不修改底层数据管道，仅通过 prompt 声明式技能实现风险预警与反例学习，符合运维约束。
```