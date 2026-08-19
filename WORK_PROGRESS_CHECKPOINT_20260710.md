# 工作进度检查点

最后更新：2026-07-16

当前分支：`v1`

当前状态：V1 已发布；Research Data V2.3、Phase 0 研究治理和 Phase 1 两阶段 Quant 工程完成；正式 development 验证未通过，所有 Quant 候选保持 shadow；首条真实 V3.1 前瞻预测已进入到期队列。

## 已完成

1. 五 Agent 实时预测、Aggregator、历史记录、Target V3.1 展示和模型切换。
2. Prediction Target V3.1、数据库字段和到期验证代码。
3. 新闻、基本面、行业和宏观 PIT 快照归档。
4. A 股 PIT 股票池、OHLCV 缓存、公告时点财务、业绩、公告和历史行业数据。
5. `quant_features.v3` 数据集、Ridge/Logistic/LightGBM、特征消融和 validation-only 概率校准。
6. Purged Walk-forward、锁定 lockbox、OOF 产物和成本后组合回测。
7. Agent 改进工程师、Prompt Replay、候选沙箱和 Skill Registry。
8. Quant 验证中心和完整 LLM 调优前端。
9. V1 分支已推送至 GitHub。
10. 全局只追加试验账本已导入 7 次历史试验，V2.3 正确作为第 8 次试验运行。
11. 预注册阈值、组合 block bootstrap、稀疏度、利润集中度和跨状态门禁。
12. V3.1 预测谱系、验证重试队列和可重启恢复的 SQLite 分析任务。
13. 两阶段 Quant：Actionable Edge Gate、收益排序、validation-only 校准、conformal 区间、同折消融和自动组合门禁。

## 当前正式资产

- Quant 数据：31,946 条、221 只股票、257 个日期；
- 时间范围：2021-01-04 至 2025-12-01；
- development：27,812 条；
- lockbox：4,134 条、26 个日期，保持 locked；
- 最新实验：`output/research_data_v2/20260715_phase0_v23/`；
- 最佳 Brier 候选：`lightgbm__technical_fundamental`，Brier `0.177851`；
- 经验先验 Brier：`0.170240`；
- 晋升：`should_promote=false`、`shadow_only=true`；
- 实时预测库：34 条，23 条已验证；33 条 `legacy-v2`，1 条 V3.1 等待到期；
- Skill Registry：13 条启用规则，全部属于近期股价分析师。
- Phase 1 正式实验：`output/quant_two_stage/phase1_two_stage_v1b_20260716/`；最佳两阶段 Brier `0.170377`，Rank IC `0.033743`，12 个组合均未晋升。

## 当前科学结论

两阶段模型证明纯技术特征存在弱正 Rank IC，但 Gate 改善置信区间跨 0、Top-K 收益为负，概率改善不足预注册门槛。部分组合点估计为正，但 block bootstrap、利润集中或最差状态门禁未通过，因此没有候选晋升。

完整特征包弱于技术局部模型，说明基本面、公告、行业和估值应先作为独立专家评价，不能因为覆盖率高就无约束拼接。

## 已识别的首要阻塞

1. V3.1 前瞻 cohort 只有 1 条未到期样本，尚不能评价真实能力。
2. 五 Agent 没有同一 PIT 样本上的严格 OOF 输出，学习型 Aggregator 无法启用。
3. Quant 概率未击败经验先验，组合显著性、稀疏度和状态门禁未通过。
4. 严格 PIT 历史总股本/市值缺失，规模暴露晋升门禁保持关闭。
5. Quant 验证只覆盖 A 股 `5d`，HK/US 和 `20d/60d` 仍未完成。

## 下一步

唯一当前计划以 `PROJECT_ROADMAP.md` 为准。Phase 0 与 Phase 1 工程已完成，下一步：

1. 用正常预测填充 V3.1 前瞻 cohort，并确认重启恢复和到期验证运行表现；
2. 进入 Phase 2，补严格 PIT 历史规模、机构一致预期、现金流质量和更强行业相对特征；
3. 每个特征族先通过同折 OOF 消融，所有候选继续走全局账本、预注册和组合稳定性门禁；
4. 同步准备五 Agent 同口径 OOF，达到样本门槛后再训练 LearnedAggregator。

最终 lockbox 继续锁定，不降低晋升门槛，也不直接扩大模型复杂度。
