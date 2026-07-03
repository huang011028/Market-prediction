# 📈 Market Prediction — AI Agent 团队驱动的市场预测系统

> 构建一个由多个专业 AI Agent 组成的分析团队，协作预测股票/组合/货币对的**短期及中长期涨跌幅度**。

## 🎯 核心理念

传统的股票分析依赖个人经验或单一模型，容易受信息茧房和认知偏差影响。

本项目模拟一个**投资研究团队**的工作方式：
- 每个 Agent 是一个"研究员"，专注于某一维度（国际形势、技术面、基本面、新闻等）
- Agent 之间**独立分析、并行工作**
- 最终由一个**汇总分析师**综合各方意见，给出最终预测

## 📊 Agent 团队

| Agent | 职责 | 阶段 |
|-------|------|------|
| 📊 技术面分析师 | K线形态、均线、MACD、RSI、成交量 | Phase 1 |
| 📰 新闻分析师 | 新闻情绪、公告、评级变动 | Phase 1 |
| 🏢 基本面分析师 | 财报、估值、行业地位 | Phase 2 |
| 🌍 国际形势分析师 | 地缘政治、央行政策、汇率 | Phase 2 |
| 🎯 汇总分析师 | 综合各方意见，输出最终预测 | Phase 1 |

## 🏗️ 项目结构

```
Market-prediction/
├── config/              # 全局配置
│   └── settings.py      # 配置系统（从 .env 加载）
├── src/
│   ├── core/            # 核心框架
│   │   ├── base_agent.py     # Agent 基类
│   │   ├── orchestrator.py   # 调度器
│   │   ├── llm_client.py     # LLM 调用封装（DeepSeek）
│   │   ├── result.py         # 数据结构
│   │   └── data_collector.py # 数据采集基类
│   ├── agents/          # 各专业 Agent（Phase 1+）
│   ├── data/            # 数据获取（Phase 1+）
│   ├── prompts/         # Prompt 模板
│   └── utils/           # 工具函数
│       └── logger.py    # 日志系统
├── scripts/             # 运行脚本
│   └── run_analysis.py  # 主入口
├── tests/               # 单元测试
├── output/              # 分析报告输出
├── PROJECT_FRAMEWORK.md # 项目框架设计文档
├── PHASE0_DESIGN.md     # Phase 0 实现设计文档
└── README.md            # 本文件
```

## 🚀 快速开始

### 1. 环境准备

```bash
# 克隆项目
git clone <repo-url>
cd Market-prediction

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 安装依赖
pip install -e ".[dev]"
```

### 2. 配置 API Key

```bash
# 复制环境变量模板
cp .env.example .env

# 编辑 .env，填入 DeepSeek API Key
# LLM_API_KEY=sk-your-deepseek-api-key
```

> 💡 获取 DeepSeek API Key: [https://platform.deepseek.com](https://platform.deepseek.com)
>
> DeepSeek 价格极低（约 ¥1/百万 token），非常适合开发测试。

### 3. 运行测试

```bash
# 确保一切正常
pytest tests/ -v
```

### 4. 运行分析

```bash
# Phase 0: 基础设施验证（Mock Agent）
python scripts/run_analysis.py --target 0700.HK --timeframe "短期(1周)"

# 查看帮助
python scripts/run_analysis.py --help
```

## 📋 开发路线

| Phase | 内容 | 状态 |
|-------|------|------|
| **Phase 0** | 基础设施搭建（本阶段） | 🚧 进行中 |
| **Phase 1** | MVP：技术面 + 新闻 + 汇总 Agent | 📅 待开始 |
| **Phase 2** | 扩展：基本面 + 宏观 Agent | 📅 待开始 |
| **Phase 3** | 增强：RAG、回测、权重系统 | 📅 待开始 |
| **Phase 4** | 产品化：Web 界面、监控推送 | 📅 待开始 |

## ⚠️ 免责声明

本项目仅供学习和研究使用，不构成任何投资建议。

AI 分析结果具有不确定性，请勿据此做出实际投资决策。

## 📄 License

MIT
