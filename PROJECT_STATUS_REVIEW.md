# Market Prediction 项目现状评估与改进建议

生成日期: 2026-07-05

## 1. 这是什么项目

这是一个用多 Agent 思路做股票/市场短中期预测的研究型项目。当前代码已经不只是 Phase 0 原型，而是包含了较完整的后端、CLI、数据抓取、预测记录、回测雏形和一个静态前端。

核心目标不是给出精确价格或交易指令，而是让多个分析 Agent 从不同维度独立分析，再由汇总 Agent 给出方向、涨跌幅区间、置信度、推理和风险。

当前 Agent 体系包括:

- 近期股价分析师: 技术面、K 线、均线、MACD、RSI、布林带等。
- 最新新闻分析师: 东方财富/新浪等新闻源，新闻预处理、情绪、事件影响。
- 公司前景分析师: 财报、估值、质量评分、行业差异化评分。
- 国际形势分析师: 宏观经济、汇率、货币政策、地缘事件、标的宏观敏感度。
- 行业对比分析师: 行业估值、同行排名、行业轮动、产业链、催化剂。
- 汇总分析师: 权重综合、质量评分、分歧识别、最终报告。

## 2. 当前实现状态

### 2.1 代码结构

项目主体是 Python 3.11+:

- `src/core/`: Agent 基类、LLM 客户端、调度器、结果结构、回测、预测存储、缓存、失败分析。
- `src/agents/`: 5 个分析 Agent 和 1 个汇总 Agent。
- `src/data/`: 行情、新闻、基本面、宏观、行业、估值、预测数据库等数据层。
- `src/prompts/`: 各 Agent 的提示词模板。
- `src/utils/`: 校准器、行业链、行业轮动、日志等工具。
- `scripts/`: 命令行分析、回测、统计、追踪脚本。
- `api_server.py`: FastAPI 后端。
- `frontend/`: 原生 HTML/CSS/JS 前端，有分析、历史、Agent 页签。
- `tests/`: 单元测试和部分集成测试。

从代码规模看，项目已经进入“功能堆出来了，但工程闭环还没收口”的阶段。

### 2.2 文档状态

文档数量很多，包括 `PROJECT_FRAMEWORK.md`、`PHASE*_DESIGN.md`、`ARCHITECTURE.md` 和多份 `IMPROVEMENT_*` 记录。

但是主入口文档 `README.md` 明显过时:

- README 仍说 Phase 0 进行中、Phase 1-4 待开始。
- `ARCHITECTURE.md` 则说 Phase 0-3 已完成，且代码里确实已经有 Phase 3 的预测追踪、回测和 Web API。
- README 的安装命令是 `pip install -e ".[dev]"`，但 `pyproject.toml` 没有包含全部运行依赖。

结论: 设计文档比 README 更接近真实状态，但用户/协作者第一眼看到的 README 会误导。

### 2.3 运行与测试状态

本轮只做了本地静态阅读和测试，没有调用真实 LLM，也没有请求外网数据。

验证结果:

- `D:\anaconda\python.exe --version`: Python 3.11.7。
- 直接运行 `python` 命令失败，因为系统优先找到了不可用的 `C:\Program Files (x86)\MGLTools-1.5.7\python.exe`。
- 用 Anaconda Python 编译入口脚本成功: `api_server.py`、`scripts/run_analysis.py` 等没有语法错误。
- 第一次 pytest 因临时目录权限失败。
- 将 `TEMP/TMP` 指到仓库内可写目录后，测试结果为 `222 passed, 14 skipped, 31 warnings`。
- `pytest-asyncio` 在当前环境中不可用，导致异步测试被跳过，并出现 `Unknown pytest.mark.asyncio` / `async def functions are not natively supported` 警告。

这说明大量纯逻辑代码是有测试保护的，但异步、网络、真实数据链路并没有被当前测试环境真正覆盖。

### 2.4 本地状态与数据

当前 git 状态中，`config/fundamental_calibration_stats.json` 在本轮开始前就已经是修改状态。本轮没有覆盖它。

仓库里已经跟踪了运行数据库:

- `data/predictions.db`
- `data/predictions.db-shm`
- `data/predictions.db-wal`

数据库中有真实运行痕迹:

- `predictions`: 20 条
- `agent_results`: 65 条
- `accuracy_stats`: 0 条

这说明项目已经产生过预测记录，但还没有形成可靠的“预测到期验证 -> 准确率统计 -> 权重/置信度校准”的闭环。

## 3. 当前最大问题

### P0: 项目不可复现，安装说明和依赖定义不一致

`README.md` 推荐 `pip install -e ".[dev]"`，但 `pyproject.toml` 只声明了:

- `python-dotenv`
- `aiohttp`
- `requests`
- `pandas`
- `pytest`
- `pytest-asyncio`

实际代码还需要但没有完整进入 `pyproject.toml` 的依赖包括:

- `akshare`
- `yfinance`
- `beautifulsoup4`
- `fastapi`
- `uvicorn`
- `PyYAML`

`requirements.txt` 比 `pyproject.toml` 更完整，但也缺 `PyYAML`。这会导致新设备按 README 安装后，运行 Web/API/权重配置时出错。

建议优先修:

- 统一 `pyproject.toml` 和 `requirements.txt`，最好以 `pyproject.toml` 为唯一权威来源。
- 增加 `dev`、`web`、`data` 等 optional dependencies，或直接把 MVP 所需依赖全部纳入主依赖。
- 明确 Windows 下推荐用哪个 Python，不要依赖系统 PATH 上的 `python`。

### P0: 回测和验证逻辑目前不可信

项目已经有 `Backtester` 和 `PredictionStore.verify_*`，但当前实现存在明显的看未来/用当前价问题。

关键问题:

- `src/core/backtester.py` 的 `_run_single()` 接收 `bt_date`，但数据抓取仍是 `pf.fetch(config.target, "3mo")`，没有真正按历史日期截断输入。
- 回测里 `price_start = price_data.price_current`，本质上是当前价，不是回测日期价格。
- `future_data = await pf.fetch(config.target, "3mo")` 后直接用 `closes[-1]`，仍然是当前最新收盘附近，不是预测有效期结束日附近价格。
- 回测注册的 Agent 名称是 `技术面分析师` / `基本面分析师`，但真实 Agent 名称是 `近期股价分析师` / `公司前景分析师`，会导致 `run_selected()` 选不中已注册 Agent。
- `PredictionStore._get_price_near_date()` 名字表示按日期找价格，但实现实际只返回最近收盘价或当前价。
- `get_stats_by_timeframe()` 使用 `短期` / `中期` / `长期`，而保存时常见 timeframe 是 `短期(1周)` / `中期(1月)` / `长期(1季)`，统计口径可能对不上。

这类问题比模型效果更优先。因为如果回测口径不可信，后续任何“准确率提升”都会变成假信号。

### P0: 运行产物被提交到仓库，状态边界不清

`data/predictions.db` 以及 SQLite 的 `-shm`、`-wal` 文件被 git 跟踪。SQLite 运行时会自动创建、删除或改写这些文件，本轮只读查询数据库时也触发了 WAL/SHM 状态变化。

这会带来几个问题:

- 每次运行分析、查询历史、测试存储，都可能污染 git 状态。
- 本地实验记录会被误传到 GitHub。
- 二进制数据库不适合代码审查，也不利于多人协作。
- `accuracy_stats` 这种运行结果和 `config/*calibration_stats.json` 这种配置/校准状态边界不清。

建议:

- 将真实运行数据库移出 git 跟踪，加入 `.gitignore`: `data/*.db`, `data/*.db-shm`, `data/*.db-wal`。
- 若需要示例数据，单独提供 `data/sample_predictions.db` 或 JSON fixture。
- 把数据库 schema 保留在 `src/data/schema.sql`。
- 把会被程序更新的校准统计放到 `data/` 或 `output/`，不要放在 `config/` 下当成静态配置。

### P1: 测试看起来多，但异步/真实链路没有真正跑起来

本地测试在修正临时目录后是 `222 passed, 14 skipped`，数字不错，但有两个隐患:

- `pytest-asyncio` 当前环境不可用，所有 async 测试被跳过。
- `slow` 标记没有注册，网络/外部数据源相关测试缺少清晰分层。

建议:

- 修正 dev 依赖，确保 `pytest-asyncio` 被安装并生效。
- 在 `pyproject.toml` 注册 markers: `slow`, `network`, `integration`, `llm`。
- 默认 CI 只跑无网络、无 API Key、可稳定复现的测试。
- 网络数据源、LLM 调用、真实 API 放进显式 opt-in 的测试组。
- 所有会写数据库/校准文件的测试都必须使用临时目录和 fixture，不允许写真实 `config/` 或 `data/`。

### P1: 文档和真实系统脱节

README 是外部协作者最先看到的文件，但当前信息落后于代码。

需要更新:

- 当前 Phase: 应写成 Phase 1-3 功能已有雏形，Phase 4 产品化仍未完成。
- 快速开始: 区分 CLI 和 Web。
- 依赖安装: 给出可执行的 Windows/Unix 命令。
- API Key: 明确没有 key 时能跑哪些测试，不能跑哪些功能。
- 真实限制: 网络源不稳定、LLM 输出非确定、回测仍需修正。

### P1: Web/API 已有雏形，但启动和部署还粗糙

`frontend/` 和 `api_server.py` 已经能组成一个简单 Web 应用，但工程化还不够。

问题:

- `scripts/start_web.sh` 是 Bash 脚本，对 Windows 不友好。
- `start_web.sh` 支持传端口参数，但 `api_server.py` 内部固定 `port = 8080`，并不解析 `--port`。
- CORS 当前 `allow_origins=["*"]`，开发方便，但正式部署不安全。
- API 启动时如果 LLM 初始化失败，只在 health 里显示未就绪，用户体验和错误提示还可以更清晰。

建议:

- 给 `api_server.py` 增加 argparse，支持 `--host`、`--port`、`--reload`。
- 增加 Windows 启动脚本，例如 `scripts/start_web.ps1`。
- 开发环境允许 CORS `*`，生产环境从环境变量读取白名单。
- API 返回中增加数据源状态、Agent 失败原因、耗时分解。

### P1: 外部数据源和硬编码兜底仍是系统可靠性的主要瓶颈

项目已经做了很多数据源降级和预处理，但文档和代码里仍能看到大量硬编码、参考值、知识库兜底。

这不是坏事，研究型项目需要兜底；问题在于输出报告必须清楚标注数据新鲜度和来源等级，否则 LLM 会把“参考值/知识库猜测”包装成看似确定的分析。

建议:

- 所有数据源输出统一带 `source`, `fetched_at`, `freshness`, `quality`, `fallback_level`。
- 汇总 Agent 权重调整不只看 reasoning 文本里的 “N/A/缺失”，而是直接读取结构化数据质量。
- 报告顶部给出“本次分析数据质量摘要”，让用户知道哪些维度是实时数据，哪些是参考值。

## 4. 建议改进路线

### 第一步: 先把项目变得可安装、可运行、可测试

目标: 新设备 clone 后，30 分钟内能按文档跑通。

任务:

- 修正 `pyproject.toml` 依赖。
- 补充 `PyYAML`。
- 更新 README。
- 增加 `.env.example` 说明和无 API Key 模式说明。
- 新增 `scripts/start_web.ps1`。
- 让 `api_server.py` 支持端口参数。
- 把 pytest 临时目录和缓存目录配置到可写位置，避免 `.pytest_cache` 权限问题。

验收:

- `pip install -e ".[dev]"` 后不会缺包。
- `pytest -q` 默认不访问外网、不需要 API Key。
- `python scripts/run_analysis.py --help` 可用。
- `python api_server.py --port 8080` 可用。

### 第二步: 清理仓库状态和运行数据边界

目标: git 里只放代码、schema、示例，不放本地运行状态。

任务:

- 停止跟踪 `data/predictions.db*`。
- `.gitignore` 增加 SQLite 运行文件。
- 提供 `scripts/init_db.py` 或让 `PredictionStore` 自动建库即可。
- 把校准统计从 `config/` 迁到 `data/calibration/`。
- 测试里禁止写真实配置文件。

验收:

- 跑一次分析后，git 状态不出现数据库/WAL/日志/缓存变化。
- 示例数据和真实数据路径分离。

### 第三步: 修正预测验证和回测

目标: 先让评估口径可信，再谈模型效果。

任务:

- PriceFetcher 支持按日期区间取历史数据。
- Backtester 在 `bt_date` 时只使用此前可见数据。
- 实际收益用 `bt_date + horizon` 附近交易日价格计算。
- 修正 Agent 名称不一致问题。
- 修正 timeframe 统计口径。
- 给回测核心逻辑加无网络 fixture 测试。

验收:

- 一个固定历史 fixture 可以稳定产出同样的回测结果。
- 回测报告能明确样本数、失败数、方向准确率、幅度命中率、平均误差。
- 不存在用当前最新价格代替历史日期价格的问题。

### 第四步: 分层测试和 CI

目标: 让项目每次修改后能知道是代码坏了、网络坏了、还是模型输出变了。

任务:

- 注册 pytest markers。
- 默认测试只跑 unit。
- integration/network/llm 测试手动开启。
- mock LLMClient 和数据源。
- 增加 GitHub Actions 或本地 `scripts/check.ps1`。

验收:

- 无 API Key 环境下默认测试稳定通过。
- 有 API Key 时可选择跑端到端冒烟测试。

### 第五步: 产品化 Web 和 API

目标: 把当前 Demo 前端变成可长期使用的本地工具。

当前执行状态（2026-07-05）: 本轮已完成单标的 Web/API 产品化收口。已新增异步分析任务、任务状态轮询、取消任务接口；前端已展示 Agent 失败/降级原因、数据源新鲜度、报告生成时间和固定免责声明；历史详情已改为展示完整 Markdown 报告。

任务:

- API 端支持取消任务、进度事件或轮询状态。
- 前端展示 Agent 失败原因、数据源新鲜度、报告生成时间。
- 历史详情展示完整报告，而不只是概要。
- 增加免责声明和“非投资建议”固定提示。
- 增加批量分析和定时监控前，先把单标的流程打磨稳定。

## 5. 推荐优先级

如果只做三件事，建议按这个顺序:

1. 修正依赖/README/启动方式，让项目可复现。
2. 修正数据库和运行产物跟踪问题，让 git 状态干净。
3. 重写回测和验证口径，让准确率统计可信。

这三件事完成后，这个项目会从“很有想法的半成品”变成“可以持续迭代的研究工具”。之后再优化 Agent、提示词、数据源、前端，收益都会更真实。

## 6. 一句话总结

当前项目已经完成了多 Agent 市场分析系统的主要骨架和不少局部能力，代码量和测试量都不低；最大短板不是“功能太少”，而是可复现环境、运行状态管理、回测验证口径和文档一致性还没有闭环。先把工程底座收稳，再谈预测效果，会是最高 ROI 的路线。
