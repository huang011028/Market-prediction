# Market Prediction

AI Agent 团队驱动的市场分析与预测研究工具。

> 本项目用于学习、研究和实验，不构成任何投资建议。模型输出和数据源都可能出错，请不要直接据此做真实交易决策。

## 当前状态

项目已经不是早期 Mock 原型，目前具备以下能力:

- 多 Agent 并行分析: 技术面、新闻、基本面、宏观、行业对比。
- 汇总 Agent: 综合各维度观点，输出方向、涨跌幅区间、置信度、推理摘要和风险。
- 标的解析: 统一处理中文名、带后缀代码和常见港美股简称，例如 `星网锐捷` 会解析为 `002396` / A 股。
- 行情与质量门控: A 股优先走 AkShare，并用腾讯 K 线 API 兜底；技术面会输出最新交易日、近 30 个交易日日线走势、分钟级走势、技术证据摘要和数据质量。
- CLI: 通过 `scripts/run_analysis.py` 运行单标的分析。
- Web/API: FastAPI 后端 + 原生 HTML/CSS/JS 前端，支持异步分析任务、状态轮询、取消任务、分钟/日线走势切换、支撑压力/量能摘要、历史完整报告查看。
- 预测追踪: 使用 SQLite 记录预测与 Agent 结果。
- 研究验证: 已具备 PIT Quant 数据、两阶段 Gate+Rank 模型、Ridge/Logistic/LightGBM、purged Walk-forward、全局只追加试验账本、预注册组合门禁、lockbox、Prompt/Skill Replay 和成本后组合回测。
- 测试: 覆盖较多纯逻辑模块，网络/LLM/慢速测试需要显式开启。

仍在收口的部分:

- 外部数据源仍可能不稳定，系统会尽量标出失败/降级原因，但不能保证每次联网取数都成功。
- 中文标的解析已覆盖常见样例，并会尝试读取 AkShare A 股名称表；冷门简称或别名仍需要继续补充。
- A 股 `5d` 已完成 Research Data V2.4 与 Phase 2 两阶段 Quant 验证；严格 PIT 市值、现金流、资产负债和机构一致预期已接入，行业特征族有小幅 OOF 增量，但概率、Top-K 与成本后收益门禁仍未通过。港股、美股和 `20d/60d` 尚未形成同等级历史证据。
- 现有正式预测仍以旧口径样本为主，Target V3.1 前瞻到期样本需要持续积累。
- Web 异步任务已使用 SQLite 持久化并支持重启恢复；仍未达到分布式生产队列、SLA 和告警水平。

## Agent 团队

| Agent | 职责 | 当前状态 |
| --- | --- | --- |
| 近期股价分析师 | K 线、均线、MACD、RSI、布林带、量价结构 | 已实现 |
| 最新新闻分析师 | 新闻抓取、去重、情绪、事件影响 | 已实现 |
| 公司前景分析师 | 财报、估值、质量评分、行业差异化评分 | 已实现 |
| 国际形势分析师 | 宏观指标、汇率、货币政策、地缘事件 | 已实现 |
| 行业对比分析师 | 同行估值、行业轮动、产业链、催化剂 | 已实现 |
| 汇总分析师 | 权重综合、质量评分、分歧识别、最终报告 | 已实现 |

## 项目结构

```text
Market-prediction/
├── api_server.py                 # FastAPI 后端入口
├── config/                       # 静态配置
│   ├── agent_config.yaml         # Agent 权重与启用配置
│   └── settings.py               # .env 配置加载
├── frontend/                     # 原生前端，含分析结果和近期走势展示
├── scripts/                      # CLI 和启动脚本
│   ├── run_analysis.py
│   ├── run_backtest.py
│   ├── start_web.ps1
│   └── start_web.sh
├── src/
│   ├── agents/                   # 各分析 Agent
│   ├── core/                     # 调度、LLM、回测、结果结构
│   ├── data/                     # 行情、标的解析、技术特征、新闻、基本面、宏观、行业、存储
│   ├── prompts/                  # Prompt 模板
│   └── utils/                    # 校准、行业链、日志等工具
├── tests/                        # 单元测试和集成测试
├── ARCHITECTURE.md               # 更详细的架构说明
├── PROJECT_STATUS_REVIEW.md      # 当前问题与改进路线
├── PROJECT_FRAMEWORK.md          # V1 当前有效架构与生产边界
├── PROJECT_ROADMAP.md            # 总目标、阶段计划与验收门槛
└── 20260705_星网锐捷优化建议.md   # 星网锐捷问题复盘与优化执行记录
```

## 环境准备

推荐 Python 3.11+。

```bash
git clone <repo-url>
cd Market-prediction

python -m venv .venv
source .venv/bin/activate        # Windows PowerShell: .venv\Scripts\Activate.ps1

pip install -e ".[dev]"
```

如果不使用 editable install，也可以:

```bash
pip install -r requirements.txt
```

## 配置 API Key

```bash
cp .env.example .env
```

编辑 `.env`:

```env
LLM_API_KEY=your-deepseek-api-key-here
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-v4-pro
```

没有 API Key 时，默认单元测试仍应可运行；CLI/API 的真实分析需要可用的 LLM 配置。

## 运行测试

默认测试排除慢速、网络和 LLM 测试:

```bash
pytest
```

运行慢速或网络测试:

```bash
pytest -m slow
pytest -m network
pytest -m llm
```

## CLI 分析

```bash
python scripts/run_analysis.py --target 星网锐捷 --timeframe "短期(1周)"
python scripts/run_analysis.py --target 002396.SZ --no-news --no-macro
python scripts/run_analysis.py --target 0700 --timeframe "短期(1周)"
python scripts/run_analysis.py --help
```

分析入口会先解析标的，例如 `星网锐捷` 会进入 `002396` / A 股链路。分析结果会输出到终端，并保存到 `output/`。预测记录会写入本地 SQLite 数据库 `data/predictions.db`，该数据库不应提交到 Git。

## 启动 Web

Windows PowerShell:

```powershell
.\scripts\start_web.ps1 -Port 8080
```

macOS/Linux/Git Bash:

```bash
bash scripts/start_web.sh 8080
```

也可以直接启动 API:

```bash
python api_server.py --host 0.0.0.0 --port 8080
python api_server.py --host 127.0.0.1 --port 8080 --reload
```

访问:

- 前端: `http://localhost:8080/`
- API 文档: `http://localhost:8080/docs`
- 健康检查: `http://localhost:8080/api/health`

当前 Web 流程:

1. 在前端输入股票代码或公司名称，选择预测周期，并按需跳过部分 Agent。
2. 前端创建异步任务: `POST /api/analyze/async`。
3. 前端轮询任务状态: `GET /api/jobs/{job_id}`，页面展示进度；需要中断时调用 `DELETE /api/jobs/{job_id}`。
4. 后端解析并返回 `resolved_target`、`target_info`、日线 `price_trend` 和分钟级 `intraday_trend`。
5. 任务完成后展示综合预测、解析后的标的、分钟/日线走势折线图、每个 Agent 的分析、失败/降级原因、数据源新鲜度摘要、报告生成时间和免责声明。
6. 预测会写入 `data/predictions.db`，历史页可打开单条记录查看完整 Markdown 报告。

当前已用真实行情验证:

```text
PriceFetcher.fetch("星网锐捷", "3mo")
=> symbol=002396, market=A, trading_days=80, latest_date=2026-07-03, trend_points=30, intraday_points=96
```

仍保留同步 API，便于脚本或调试:

```bash
curl -X POST http://localhost:8080/api/analyze \
  -H "Content-Type: application/json" \
  -d "{\"target\":\"0700\",\"timeframe\":\"短期(1周)\",\"skip_agents\":[]}"
```

在 PowerShell 中更推荐:

```powershell
Invoke-RestMethod http://localhost:8080/api/analyze/async `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"target":"0700","timeframe":"短期(1周)","skip_agents":[]}'
```

## 项目运行流程

一次完整分析大致是:

1. `api_server.py` 读取 `.env` 并初始化 LLM、配置和 Agent 权重。
2. Web/CLI 输入标的后，系统会先把常见中文股名、带后缀代码或纯代码解析成内部 `symbol/market`。
3. `Orchestrator` 并行调度技术面、新闻、基本面、宏观、行业 Agent。
4. 技术面先获取 K 线，生成指标、近 30 个交易日走势和数据质量；数据不足时会返回降级/失败状态。
5. 其他 Agent 从新闻、财务、宏观或行业模块取数，必要时会降级到参考数据或知识库。
6. `Aggregator` 按权重汇总各 Agent 观点，并根据失败/降级状态调整权重，生成方向、涨跌幅区间、置信度、分歧点和风险。
7. `PredictionStore` 把最终报告、各 Agent 结果和运行元数据写入 SQLite。
8. 前端展示当前结果和近期走势；后续可通过历史页查看完整报告，通过追踪脚本验证到期预测。

## 回测与预测追踪

回测入口:

```bash
python scripts/run_backtest.py --target 000001 --start 2025-01-01 --end 2025-06-30
```

注意:

- 技术面支持历史 K 线回放，新闻、基本面、行业和宏观支持 PIT 快照归档；五个 LLM Agent 仍缺同口径的大规模 OOF 回放。
- Quant Core 已支持 A 股 `5d` 的 `quant_features.v4`、特征质量审计、统计基线、两阶段边际门控与收益排序、Walk-forward 和成本回测，但所有候选仍为 `shadow_only`。正式 Phase 2 报告位于 `output/quant_two_stage/phase2_two_stage_v2b_20260716/`。
- 回测结果应作为工程与研究验证工具，而不是投资胜率承诺。

预测追踪:

```bash
python scripts/show_stats.py
python scripts/track_predictions.py
```

## 数据与 Git 边界

以下内容是运行产物，不应提交:

- `data/*.db`
- `data/*.db-shm`
- `data/*.db-wal`
- `data/calibration/`
- `logs/`
- `output/*.json`
- `output/*.md`

数据库 schema 保留在 `src/data/schema.sql`。校准统计会优先写入 `data/calibration/`，旧的 `config/*calibration_stats.json` 仅作为兼容读取来源。

## 更多文档

- [ARCHITECTURE.md](ARCHITECTURE.md): 系统架构、数据源和 Phase 说明。
- [PROJECT_STATUS_REVIEW.md](PROJECT_STATUS_REVIEW.md): 当前最大问题和改进路线。
- [20260705_星网锐捷优化建议.md](20260705_星网锐捷优化建议.md): 星网锐捷结果差的根因、前三天优化路线和执行状态。
- `PHASE*_DESIGN.md`: 分阶段设计记录。
- `IMPROVEMENT_*`: 各 Agent 的迭代改进记录。

## License

MIT
