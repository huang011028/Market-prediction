# 工作进度检查点

最后更新：2026-07-13
状态：本轮“扩大 PIT 横截面和增量特征”已完成

## 已完成

1. A 股股票池改为交易所/板块分层轮转抽样，并排除 B 股。
2. 新增原始 OHLCV Parquet 缓存，支持窗口完整性校验和增量合并。
3. A 股行情主源截断时使用完整历史回退。
4. 新增公告时点财务事件、正式公告事件和历史行业有效区间数据库。
5. 所有丰富特征严格按 `effective/published time <= as_of` 连接。
6. Quant 数据集输出各特征族覆盖率和数据资产状态。
7. Walk-forward 支持技术、基本面、公告、行业和全部增强特征的同折消融。
8. API 和前端 Quant 验证中心支持刷新 PIT 特征、构建数据集和显示消融结果。
9. 不同特征候选模型按 `fold/model_variant` 独立保存，避免资产覆盖。

## 最终真实数据

- 数据集：`output/quant_dataset/20260713_160633/quant_dataset_report.json`
- PIT 样本：4,684
- 有效股票：83
- 独立日期：101
- 价格缓存：93 只、39,944 行
- 财务事件：1,666
- 正式公告：20,615
- 行业区间：419
- 覆盖率：技术 100%、基本面 99.98%、公告 97.50%、行业 87.08%

## 最新验证

- Walk-forward：`output/quant_walk_forward/20260713_161328/walk_forward_report.json`
- 9 折，每模型 OOF 2,498 条
- 最佳候选：`lightgbm__enriched`
- Brier：0.21605
- Rank IC：0.13040，95% CI 下界 0.01203
- 结论：`should_promote=false`，保持 shadow
- Lockbox：707 条，保持 locked

## 成本回测

- 生产 edge 0.10：零交易，报告 `output/portfolio_backtest/20260713_161439_262247/portfolio_backtest.json`
- 诊断 edge 0.02：净收益 +36.996%，基准 +44.525%，复合超额 -14.873%，报告 `output/portfolio_backtest/20260713_161439_262225/portfolio_backtest.json`

## 当前科学结论

技术特征已出现正的样本外横截面排序能力，但新增基本面、公告和行业特征只带来极小的 Brier 改善，没有稳定提高 IC，成本后组合也没有跑赢基准。任何 Quant 模型都未晋升，生产 Agent 和 Aggregator 权重未被替换。

## 下一阶段

1. 将财务同比升级为 surprise、质量、现金流和估值历史分位。
2. 将公告标题计数升级为首次披露、规模、严重度和事件衰减。
3. 扩大历史行业成员覆盖并加入行业收益、广度、相对动量和同业排名。
4. 将横截面扩展到数百只、3 至 5 年，同时保留退市样本。
5. 引入分折概率校准、quantile LightGBM 和 conformal interval。
6. 持续积累五 Agent 同时点到期样本，再训练学习型 Aggregator。

恢复工作时应从上述下一阶段开始，不应为追求通过结果而继续调当前模型参数或解锁 lockbox。
