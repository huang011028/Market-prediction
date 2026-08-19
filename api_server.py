#!/usr/bin/env python3
"""
Market Prediction Web API Server

FastAPI 后端服务，为前端提供:
1. POST /api/analyze — 运行分析
2. GET  /api/agents — 获取 Agent 列表
3. GET  /api/history — 获取历史预测
4. GET  /api/history/{prediction_id} — 获取单个预测详情
5. GET  /api/health — 健康检查

启动:
    python api_server.py
    uvicorn api_server:app --host 0.0.0.0 --port 8080 --reload
"""

import sys
import asyncio
import time
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Callable, Optional
from datetime import datetime, timedelta

# 确保项目根目录在 path 中
project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from src.utils.logger import setup_logging, get_logger
from src.core.llm_client import LLMClient
from src.core.model_registry import LLMModelRegistry
from src.core.orchestrator import Orchestrator
from src.agents.technical_analyst import TechnicalAnalyst
from src.agents.news_analyst import NewsAnalyst
from src.agents.fundamental_analyst import FundamentalAnalyst
from src.agents.macro_analyst import MacroAnalyst
from src.agents.industry_analyst import IndustryAnalyst
from src.agents.aggregator import Aggregator
from src.agents.improvement_engineer import (
    AgentImprovementEngineer,
    ImprovementEngineerConfig,
)
from src.core.historical_evaluator import HistoricalAgentEvaluator
from src.core.candidate_validation_sandbox import (
    CandidateValidationSandbox,
    CandidateSandboxConfig,
)
from src.core.calibration_bootstrap import (
    CalibrationBootstrapConfig,
    TechnicalCalibrationBootstrapper,
)
from src.core.self_improvement_lab import SelfImprovementLab, SelfImprovementLabConfig
from src.core.agent_skill_registry import AgentSkillRegistry, DEFAULT_REGISTRY_PATH
from src.core.experiment_manifest import resolve_experiment_location
from config.settings import get_settings
from config.weight_manager import WeightManager
from src.data.prediction_store import PredictionStore
from src.data.symbol_resolver import resolve_symbol, SymbolInfo
from src.core.persistent_job_store import PersistentJobStore

# ================================================================
# 初始化
# ================================================================

settings = get_settings()
setup_logging(log_level="INFO", log_dir=settings.logs_dir)
logger = get_logger("api")
model_registry = LLMModelRegistry(settings=settings)

app = FastAPI(
    title="Market Prediction API",
    description="AI Agent 团队市场分析系统",
    version="3.0.0",
)

# CORS — 允许前端跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# LLM 客户端（全局单例）
try:
    llm: Optional[LLMClient] = model_registry.create_client()
    logger.info(f"LLM 初始化成功: {model_registry.get_active_model().model}")
except Exception as e:
    logger.error(f"LLM 初始化失败: {e}")
    llm = None

# 权重管理器
weight_mgr = WeightManager()

# Agent 名称常量
AGENT_NAMES = {
    "tech": "近期股价分析师",
    "news": "最新新闻分析师",
    "fundamental": "公司前景分析师",
    "macro": "国际形势分析师",
    "industry": "行业对比分析师",
}

DISCLAIMER = "本项目仅供学习、研究和工程验证使用，不构成任何投资建议。模型输出和数据源都可能出错，请勿直接据此做真实交易决策。"

# SQLite 是任务状态事实源，内存表只保存当前进程的 asyncio task 句柄。
job_store = PersistentJobStore()
analysis_jobs: dict[str, dict[str, Any]] = {
    item["job_id"]: {**item, "task": None}
    for item in job_store.list_recent(limit=100)
}
TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled"}

# ================================================================
# 数据模型
# ================================================================


class AnalyzeRequest(BaseModel):
    target: str = Field(..., description="股票代码或公司名称", json_schema_extra={"example": "0700"})
    timeframe: str = Field(default="短期(1周)", description="预测周期")
    market: Optional[str] = Field(default=None, description="市场模式：A/HK/US")
    skip_agents: list[str] = Field(default=[], description="跳过的 Agent 列表")


class AgentResult(BaseModel):
    agent_name: str
    direction: str
    magnitude: Optional[dict]
    confidence: float
    prediction_target: Optional[dict] = None
    reasoning: str
    key_factors: list[str]
    risks: list[str]
    data_summary: dict
    status: str = "ok"
    error_message: Optional[str] = None
    data_quality_score: float = 1.0


class AnalysisResponse(BaseModel):
    success: bool
    target: str
    resolved_target: Optional[str] = None
    timeframe: str
    generated_at: str
    elapsed_seconds: float
    agent_results: list[AgentResult]
    final_report: dict
    prediction_id: Optional[str] = None
    agent_statuses: list[dict] = Field(default_factory=list)
    failed_agents: list[dict] = Field(default_factory=list)
    degraded_agents: list[dict] = Field(default_factory=list)
    data_quality_summary: list[dict] = Field(default_factory=list)
    target_info: dict = Field(default_factory=dict)
    price_trend: list[dict] = Field(default_factory=list)
    intraday_trend: list[dict] = Field(default_factory=list)
    intraday_meta: dict = Field(default_factory=dict)
    disclaimer: str = DISCLAIMER


class AnalysisJobResponse(BaseModel):
    job_id: str
    status: str
    progress: int
    message: str
    created_at: str
    updated_at: str
    result: Optional[dict] = None
    error: Optional[str] = None
    attempts: int = 0
    max_attempts: int = 3


class AgentInfo(BaseModel):
    name: str
    description: str
    weight_short: float
    weight_mid: float
    weight_long: float


class LLMModelCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, description="前端显示名称")
    provider: str = Field(default="custom", min_length=1, description="供应商，如 deepseek/openai/qwen")
    base_url: str = Field(..., min_length=1, description="兼容 OpenAI Chat Completions 的 Base URL")
    model: str = Field(..., min_length=1, description="模型 ID")
    api_key: Optional[str] = Field(default=None, description="可选，不填则复用 .env 的 LLM_API_KEY")
    temperature: float = Field(default=0.3, ge=0, le=2)
    max_tokens: int = Field(default=4096, ge=256, le=200000)
    verify_ssl: bool = Field(default=True)
    set_active: bool = Field(default=False, description="添加后立即设为当前模型")


class LLMModelActivateRequest(BaseModel):
    model_id: str = Field(..., min_length=1)


class LLMModelUpdateRequest(BaseModel):
    name: Optional[str] = None
    provider: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    temperature: Optional[float] = Field(default=None, ge=0, le=2)
    max_tokens: Optional[int] = Field(default=None, ge=256, le=200000)
    verify_ssl: Optional[bool] = None


class ImprovementEvaluateRequest(BaseModel):
    min_samples: int = Field(default=5, ge=1, description="触发评估信号的最小样本数")
    limit: int = Field(default=2000, ge=1, le=10000, description="最多读取的已验证 agent 结果")
    prediction_ids: list[str] = Field(default_factory=list, description="只评估这些历史预测 ID")
    target: Optional[str] = Field(default=None, description="只评估指定标的")
    timeframe: Optional[str] = Field(default=None, description="只评估指定预测周期")
    start_date: Optional[str] = Field(default=None, description="预测开始日期，YYYY-MM-DD")
    end_date: Optional[str] = Field(default=None, description="预测结束日期，YYYY-MM-DD")


class ImprovementRunRequest(BaseModel):
    report_path: Optional[str] = Field(default=None, description="已有历史评估报告 JSON 路径")
    min_samples: int = Field(default=20, ge=1, description="自动修改 prompt/skill 的最小样本数")
    min_unique_cases: int = Field(default=5, ge=1, description="自动修改 prompt/skill 的最小独立历史案例数")
    evaluation_min_samples: int = Field(default=5, ge=1, description="生成历史评估信号的最小样本数")
    limit: int = Field(default=2000, ge=1, le=10000)
    prediction_ids: list[str] = Field(default_factory=list, description="无 report_path 时只评估这些历史预测 ID")
    target: Optional[str] = Field(default=None, description="无 report_path 时只评估指定标的")
    timeframe: Optional[str] = Field(default=None, description="无 report_path 时只评估指定周期")
    dry_run: bool = Field(default=True, description="只生成报告，不实际写入 prompt/skill")
    allow_prompt_apply: bool = Field(default=True)
    allow_skill_apply: bool = Field(default=True)
    use_llm_review: bool = Field(default=False, description="使用同一 LLM API 做边界复核")


class CandidateSandboxRequest(BaseModel):
    report_path: Optional[str] = Field(default=None, description="已有历史评估报告 JSON 路径")
    min_samples: int = Field(default=20, ge=1, description="生成候选 prompt/skill 的最小样本数")
    min_unique_cases: int = Field(default=5, ge=1, description="生成候选 prompt/skill 的最小独立历史案例数")
    evaluation_min_samples: int = Field(default=5, ge=1, description="无 report_path 时生成评估信号的样本阈值")
    limit: int = Field(default=2000, ge=1, le=10000)
    prediction_ids: list[str] = Field(default_factory=list, description="无 report_path 时只评估这些历史预测 ID")
    target: Optional[str] = Field(default=None, description="无 report_path 时只评估指定标的")
    timeframe: Optional[str] = Field(default=None, description="无 report_path 时只评估指定周期")
    use_llm_candidates: bool = Field(default=False, description="使用同一 LLM API 生成候选 prompt/skill 文案")
    apply_if_passed: bool = Field(default=False, description="通过 holdout 后晋升到正式 prompt/skill/registry")
    allow_prompt_promotion: bool = Field(default=True)
    allow_skill_promotion: bool = Field(default=True)
    validate_technical: bool = Field(default=True)
    holdout_targets: str = Field(
        default="600276,601012,000858,002594,600030,601888",
        description="逗号分隔的技术面 holdout 标的",
    )
    holdout_start_date: str = Field(default="2025-07-01")
    holdout_end_date: str = Field(default="2025-12-31")
    holdout_timeframe: str = Field(default="短期(1周)")
    holdout_interval_days: int = Field(default=14, ge=1, le=90)
    holdout_lookback_days: int = Field(default=180, ge=30, le=1000)
    holdout_tolerance_days: int = Field(default=10, ge=1, le=30)
    min_accuracy_delta: float = Field(default=0.01, ge=0, le=1)
    min_holdout_samples: int = Field(default=20, ge=1)
    min_changed_predictions: int = Field(default=1, ge=1)
    confidence_cap: float = Field(default=0.35, ge=0.05, le=0.95)
    min_brier_delta: float = Field(default=0.005, ge=0)
    min_confidence_changed: int = Field(default=3, ge=1)
    min_confidence_matched: int = Field(default=3, ge=1)
    run_technical_prompt_replay: bool = Field(default=False, description="运行技术面 baseline/candidate LLM prompt replay")
    prompt_replay_max_samples: int = Field(default=60, ge=1, le=200)
    prompt_replay_min_samples: int = Field(default=30, ge=1)
    prompt_replay_min_accuracy_delta: float = Field(default=0.01, ge=0, le=1)
    prompt_replay_min_brier_delta: float = Field(default=0.0, ge=0)
    prompt_replay_min_changed_predictions: int = Field(default=1, ge=1)
    prompt_replay_overconfidence_threshold: float = Field(default=0.60, ge=0.05, le=0.95)
    prompt_replay_max_overconfidence_delta: float = Field(default=0.02, ge=0, le=1)
    candidate_batch_count: int = Field(default=1, ge=1, le=10)


class TechnicalPromptLoopRequest(BaseModel):
    targets: str = Field(
        default="000001,600519,000333,300750,600036,601318,600900,002415,601899,300760",
        description="训练样本标的，逗号分隔",
    )
    start_date: str = Field(default="2024-01-01")
    end_date: str = Field(default="2025-06-30")
    timeframe: str = Field(default="短期(1周)")
    interval_days: int = Field(default=14, ge=1, le=90)
    lookback_days: int = Field(default=180, ge=30, le=1000)
    tolerance_days: int = Field(default=10, ge=1, le=30)
    min_samples: int = Field(default=20, ge=1)
    min_unique_cases: int = Field(default=5, ge=1)
    use_llm_candidates: bool = Field(default=True)
    apply_if_passed: bool = Field(default=False)
    allow_prompt_promotion: bool = Field(default=True)
    allow_skill_promotion: bool = Field(default=True)
    holdout_targets: str = Field(default="600276,601012,000858,002594,600030,601888")
    holdout_start_date: str = Field(default="2025-07-01")
    holdout_end_date: str = Field(default="2025-12-31")
    holdout_timeframe: str = Field(default="短期(1周)")
    holdout_interval_days: int = Field(default=14, ge=1, le=90)
    holdout_lookback_days: int = Field(default=180, ge=30, le=1000)
    holdout_tolerance_days: int = Field(default=10, ge=1, le=30)
    min_accuracy_delta: float = Field(default=0.01, ge=0, le=1)
    min_holdout_samples: int = Field(default=20, ge=1)
    min_changed_predictions: int = Field(default=1, ge=1)
    confidence_cap: float = Field(default=0.35, ge=0.05, le=0.95)
    min_brier_delta: float = Field(default=0.005, ge=0)
    min_confidence_changed: int = Field(default=3, ge=1)
    min_confidence_matched: int = Field(default=3, ge=1)
    prompt_replay_max_samples: int = Field(default=60, ge=1, le=200)
    prompt_replay_min_samples: int = Field(default=30, ge=1)
    prompt_replay_min_accuracy_delta: float = Field(default=0.01, ge=0, le=1)
    prompt_replay_min_brier_delta: float = Field(default=0.0, ge=0)
    prompt_replay_min_changed_predictions: int = Field(default=1, ge=1)
    prompt_replay_overconfidence_threshold: float = Field(default=0.60, ge=0.05, le=0.95)
    prompt_replay_max_overconfidence_delta: float = Field(default=0.02, ge=0, le=1)
    candidate_batch_count: int = Field(default=5, ge=1, le=10)


class SelfImprovementLabRequest(BaseModel):
    targets: str = Field(
        default="000001,600519,000333,300750,600036,601318,600900,002415,601899,300760",
        description="逗号分隔的标的代码",
    )
    start_date: str = Field(default="2024-01-01", description="历史样本开始日期 YYYY-MM-DD")
    end_date: str = Field(default="2025-06-30", description="历史样本结束日期 YYYY-MM-DD")
    timeframe: str = Field(default="短期(1周)")
    interval_days: int = Field(default=14, ge=1, le=90)
    lookback_days: int = Field(default=180, ge=30, le=1000)
    tolerance_days: int = Field(default=10, ge=1, le=30)
    evaluation_min_samples: int = Field(default=5, ge=1)
    run_engineer: bool = Field(default=True)
    engineer_min_samples: int = Field(default=20, ge=1)
    engineer_min_unique_cases: int = Field(default=5, ge=1)
    dry_run: bool = Field(default=True)
    allow_prompt_apply: bool = Field(default=True)
    allow_skill_apply: bool = Field(default=True)
    news_snapshots_path: Optional[str] = Field(default=None, description="可选新闻快照 JSON/JSONL 路径")
    point_in_time_snapshots_path: Optional[str] = Field(default=None, description="可选 point-in-time 快照目录、JSON 或 JSONL 路径")


class SkillToggleRequest(BaseModel):
    enabled: bool = Field(..., description="是否启用该 skill")


class QuantDatasetRequest(BaseModel):
    targets: str = Field(default="000001,600519,000333,300750,600036")
    start_date: str = Field(default="2024-01-01")
    end_date: str = Field(default="2025-12-31")
    timeframe: str = Field(default="短期(1周)")
    interval_days: int = Field(default=7, ge=1, le=90)
    lookback_days: int = Field(default=180, ge=60, le=1000)
    max_samples: int = Field(default=5000, ge=1, le=200000)
    export_parquet: bool = True
    use_universe: bool = False
    universe_market: str = Field(default="A", pattern="^(A|HK|US)$")
    universe_limit: int = Field(default=0, ge=0, le=10000)
    min_listing_days: int = Field(default=120, ge=0, le=3650)
    min_price: float = Field(default=1.0, ge=0)
    min_avg_traded_value: float = Field(default=0.0, ge=0)
    industry_neutralization: bool = False
    universe_sample_seed: str = "quant-v3.1-a-share"
    universe_stratify: bool = True
    replace_partition: bool = True
    use_pit_enrichment: bool = True
    fundamental_max_age_days: int = Field(default=550, ge=30, le=2000)
    announcement_lookback_days: int = Field(default=90, ge=1, le=730)
    industry_standard: str = "申银万国行业分类标准"
    use_price_cache: bool = True
    history_fetch_concurrency: int = Field(default=3, ge=1, le=8)


class QuantPitRefreshRequest(BaseModel):
    targets: str = Field(default="")
    start_date: str = Field(default="2024-01-01")
    end_date: str = Field(default="2025-12-31")
    use_universe: bool = True
    universe_limit: int = Field(default=60, ge=1, le=1000)
    min_listing_days: int = Field(default=120, ge=0, le=3650)
    interval_days: int = Field(default=7, ge=1, le=90)
    universe_sample_seed: str = "quant-v3.1-a-share"
    universe_stratify: bool = True
    concurrency: int = Field(default=3, ge=1, le=8)
    include_fundamental: bool = True
    include_performance: bool = True
    include_announcements: bool = True
    include_industry: bool = True
    include_financial_quality: bool = True
    include_consensus: bool = True


class QuantFeatureAuditRequest(BaseModel):
    market: str = Field(default="A", pattern="^(A|HK|US)$")
    horizon: str = Field(default="5d", pattern="^(5d|20d|60d)$")
    target_version: str = Field(default="v3.1", pattern="^v3\\.1$")
    feature_version: str = "quant_features.v4"
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    max_drift_score: float = Field(default=1.5, gt=0.0, le=10.0)


class QuantWalkForwardRequest(BaseModel):
    market: str = Field(default="A", pattern="^(A|HK|US)$")
    horizon: str = Field(default="5d", pattern="^(5d|20d|60d)$")
    target_version: str = Field(default="v3.1", pattern="^v3\\.1$")
    feature_version: str = "quant_features.v4"
    model_names: list[str] = Field(default_factory=lambda: ["ridge", "logistic", "lightgbm"])
    train_days: int = Field(default=365, ge=60, le=3650)
    validation_days: int = Field(default=90, ge=14, le=730)
    test_days: int = Field(default=90, ge=14, le=730)
    purge_days: int = Field(default=7, ge=1, le=120)
    lockbox_days: int = Field(default=90, ge=30, le=730)
    min_train_samples: int = Field(default=200, ge=20)
    min_validation_samples: int = Field(default=30, ge=5)
    min_test_samples: int = Field(default=30, ge=5)
    min_unique_train_dates: int = Field(default=60, ge=5)
    unlock_lockbox: bool = False
    feature_set_names: list[str] = Field(default_factory=lambda: ["all"])
    calibrate_probabilities: bool = True
    calibration_method: str = Field(default="temperature", pattern="^(none|temperature|temperature_prior)$")
    calibration_min_samples: int = Field(default=100, ge=20)
    enable_industry_stacking: bool = True
    max_industry_stack_weight: float = Field(default=0.35, ge=0, le=1)
    min_industry_stack_brier_delta: float = Field(default=0.0, ge=0, le=1)
    min_actionable_coverage: float = Field(default=0.01, ge=0, le=1)


class QuantTwoStageRequest(BaseModel):
    config_path: str = "config/quant/two_stage_v2.json"
    experiment_id: str = Field(
        default="",
        pattern=r"^[A-Za-z0-9_-]*$",
        max_length=80,
    )


class LearnedAggregatorRequest(BaseModel):
    market: str = Field(default="A", pattern="^(A|HK|US)$")
    horizon: str = Field(default="5d", pattern="^(5d|20d|60d)$")
    min_samples: int = Field(default=200, ge=30)
    min_unique_dates: int = Field(default=60, ge=10)
    purge_days: int = Field(default=7, ge=1, le=120)
    lockbox_days: int = Field(default=90, ge=30, le=730)
    min_brier_delta: float = Field(default=0.005, ge=0, le=1)
    min_folds: int = Field(default=3, ge=3, le=10)
    activate_if_passed: bool = False


class PortfolioBacktestRequest(BaseModel):
    prediction_paths: list[str] = Field(default_factory=list)
    market: str = Field(default="A", pattern="^(A|HK|US)$")
    model_name: Optional[str] = None
    horizon_trading_days: int = Field(default=5, ge=1, le=120)
    top_k: int = Field(default=10, ge=1, le=200)
    bottom_k: int = Field(default=0, ge=0, le=200)
    allow_short: bool = False
    min_edge_score: float = Field(default=0.10, ge=0, le=1)
    max_position_weight: float = Field(default=0.20, gt=0, le=1)
    volatility_weighted: bool = True
    initial_capital: float = Field(default=1_000_000, gt=0)
    extra_borrow_cost_bps: float = Field(default=0.0, ge=0, le=1000)
    allow_overlapping_horizons: bool = False
    min_avg_traded_value: float = Field(default=0.0, ge=0)
    max_participation_rate: float = Field(default=0.05, gt=0, le=1)
    impact_coefficient_bps: float = Field(default=15.0, ge=0, le=1000)
    policy_id: str = "interactive-diagnostic"
    policy_role: str = Field(default="diagnostic", pattern="^(production_candidate|diagnostic)$")
    pre_registered: bool = False
    selection_source: str = "interactive"
    bootstrap_iterations: int = Field(default=1000, ge=100, le=10000)
    bootstrap_block_size: int = Field(default=4, ge=1, le=60)
    bootstrap_seed: int = 42
    min_independent_dates: int = Field(default=60, ge=5)
    min_invested_dates: int = Field(default=30, ge=1)
    min_position_selections: int = Field(default=100, ge=1)
    max_top5_profit_concentration: float = Field(default=0.50, ge=0, le=1)
    min_regime_periods: int = Field(default=10, ge=1)
    max_regime_loss_pct: float = Field(default=10.0, ge=0)
    regime_threshold_pct: float = Field(default=1.0, ge=0)


class EvidenceMaintenanceRequest(BaseModel):
    collect_snapshots: bool = False
    targets: list[str] = Field(default_factory=list)
    recent_target_limit: int = Field(default=30, ge=1, le=500)
    timeframe: str = "短期(1周)"
    news_mode: str = Field(default="evidence", pattern="^(none|raw|evidence|formal)$")
    max_snapshots: int = Field(default=0, ge=0, le=10000)


# ================================================================
# 辅助函数
# ================================================================


def _reload_llm_from_registry() -> LLMClient:
    """按照当前激活模型重建全局 LLM client。"""
    global llm
    llm = model_registry.create_client()
    logger.info(f"LLM 已切换为: {model_registry.get_active_model().model}")
    return llm


def _current_llm_snapshot() -> tuple[LLMClient, str]:
    """捕获一次分析任务使用的 LLM client 和模型名。"""
    if not llm:
        raise RuntimeError("LLM 未初始化，请检查 .env 或模型配置")
    active_model = model_registry.get_active_model()
    return llm, active_model.model


def _identify_market(target: str) -> str:
    """识别市场"""
    target = target.strip().upper().replace(".HK", "").replace(".SZ", "").replace(".SS", "")
    if target.isdigit():
        return "HK" if len(target) <= 5 else "A"
    return "US"


def _build_orchestrator(
    skip_agents: list[str] = None,
    llm_client: Optional[LLMClient] = None,
) -> tuple[Orchestrator, list[str]]:
    """构建 Orchestrator 并注册 Agent"""
    orchestrator = Orchestrator()
    skip = set(skip_agents or [])
    active_names = []
    client = llm_client or llm
    if getattr(client, "max_concurrent_requests", 4) <= 1:
        orchestrator.max_concurrent_agents = 1
    serial_llm_timeout = settings.AGENT_TIMEOUT * 3

    def prepare_agent(agent):
        if getattr(client, "max_concurrent_requests", 4) <= 1:
            agent.analysis_timeout_seconds = max(
                getattr(agent, "analysis_timeout_seconds", 120),
                serial_llm_timeout,
            )
        return agent

    if "tech" not in skip:
        orchestrator.register(prepare_agent(TechnicalAnalyst(client)))
        active_names.append(AGENT_NAMES["tech"])

    if "news" not in skip:
        orchestrator.register(prepare_agent(NewsAnalyst(client)))
        active_names.append(AGENT_NAMES["news"])

    if "fundamental" not in skip:
        orchestrator.register(prepare_agent(FundamentalAnalyst(client)))
        active_names.append(AGENT_NAMES["fundamental"])

    if "macro" not in skip:
        orchestrator.register(prepare_agent(MacroAnalyst(client)))
        active_names.append(AGENT_NAMES["macro"])

    if "industry" not in skip:
        orchestrator.register(prepare_agent(IndustryAnalyst(client)))
        active_names.append(AGENT_NAMES["industry"])

    return orchestrator, active_names


def _resolve_target(target: str) -> str:
    """将中文股名解析为股票代码"""
    return resolve_symbol(target).symbol


def _agent_target_from_info(info) -> str:
    """给 Agent 使用的规范标的，保留显式市场身份。"""
    symbol = str(info.symbol or "").strip().upper()
    if info.market == "A" and symbol:
        suffix = ".SS" if symbol.startswith(("5", "6", "9")) else ".SZ"
        return f"{symbol}{suffix}"
    if info.market == "HK" and symbol:
        return f"{symbol.zfill(4)}.HK"
    return symbol


def _validate_resolved_symbol(info) -> None:
    """拦截中文简称解析失败后的无效标的，避免所有 Agent 连锁失败。"""
    symbol = str(getattr(info, "symbol", "") or "").strip()
    market = getattr(info, "market", "")
    if market == "A" and (not symbol.isdigit() or len(symbol) > 6):
        raise ValueError(
            f"无法在A股模式下把“{getattr(info, 'raw', symbol)}”解析为有效股票代码，"
            "请尝试输入更完整的公司名或6位股票代码。"
        )
    if market == "HK" and (not symbol.isdigit() or len(symbol) > 5):
        raise ValueError(
            f"无法在港股模式下把“{getattr(info, 'raw', symbol)}”解析为有效股票代码，"
            "请尝试输入更完整的公司名或港股代码。"
        )


def _safe_json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _agent_result_to_dict(result) -> dict:
    return {
        "agent_name": result.agent_name,
        "direction": result.direction.value,
        "magnitude": (
            {"min_pct": result.magnitude.min_pct, "max_pct": result.magnitude.max_pct}
            if result.magnitude
            else None
        ),
        "confidence": result.confidence,
        "prediction_target": (
            result.prediction_target.to_dict()
            if getattr(result, "prediction_target", None)
            else None
        ),
        "reasoning": result.reasoning,
        "key_factors": result.key_factors,
        "risks": result.risks,
        "data_summary": result.data_summary,
        "status": getattr(result, "status", "ok"),
        "error_message": getattr(result, "error_message", None),
        "data_quality_score": getattr(result, "data_quality_score", 1.0),
    }


def _agent_status_from_result(result) -> dict:
    """从 Agent 返回内容中提取前端可展示的执行状态。"""
    data_summary = result.data_summary or {}
    explicit_status = getattr(result, "status", "ok")
    text = " ".join([
        str(result.reasoning or ""),
        " ".join(str(r) for r in (result.risks or [])),
        _safe_json_dumps(data_summary),
    ])
    hard_failure_markers = [
        "Agent 未返回结果",
        "执行失败",
        "分析超时",
        "LLM 返回格式异常",
        "数据采集失败",
    ]
    status = "ok"
    reason = "完成"

    if explicit_status == "failed":
        status = "failed"
        reason = getattr(result, "error_message", None) or data_summary.get("error") or "Agent 标记为失败"
    elif explicit_status == "degraded":
        status = explicit_status
        reason = getattr(result, "error_message", None) or data_summary.get("error") or "Agent 已返回结果，但证据或数据质量受限"
    elif result.confidence <= 0:
        status = "degraded"
        reason = "置信度为 0，可能是数据或 LLM 调用降级结果"
    elif any(marker in text for marker in hard_failure_markers):
        status = "failed"
        reason = "结果中包含明确执行失败提示"

    if status == "failed":
        for candidate in list(result.risks or []) + [result.reasoning]:
            if candidate:
                reason = str(candidate)[:180]
                break

    return {
        "agent_name": result.agent_name,
        "status": status,
        "reason": reason,
    }


def _split_agent_statuses(agent_statuses: list[dict]) -> tuple[list[dict], list[dict]]:
    """把真正执行失败和可用但受限的 Agent 分开，避免污染 agents_failed。"""
    failed = [s for s in agent_statuses if s.get("status") == "failed"]
    degraded = [s for s in agent_statuses if s.get("status") == "degraded"]
    return failed, degraded


def _target_info_dict(info) -> dict:
    return {
        "raw": info.raw,
        "symbol": info.symbol,
        "market": info.market,
        "name": info.name,
        "display_name": info.display_name,
        "source": info.source,
    }


def _extract_price_trend(agent_results: list) -> list[dict]:
    for result in agent_results:
        if result.agent_name == AGENT_NAMES["tech"]:
            summary = result.data_summary or {}
            trend = summary.get("recent_trend") or []
            if trend:
                return trend
    return []


def _extract_intraday_context(agent_results: list) -> tuple[list[dict], dict]:
    for result in agent_results:
        if result.agent_name == AGENT_NAMES["tech"]:
            summary = result.data_summary or {}
            trend = summary.get("intraday_trend") or []
            meta = summary.get("intraday_meta") or {}
            return trend, meta
    return [], {}


def _data_quality_from_result(result) -> dict:
    """提取数据源、新鲜度和质量提示；缺字段时保持可读默认值。"""
    data_summary = result.data_summary or {}
    source = (
        data_summary.get("source")
        or data_summary.get("data_source")
        or data_summary.get("source_type")
        or data_summary.get("provider")
        or "未提供"
    )
    freshness = (
        data_summary.get("freshness")
        or data_summary.get("last_updated")
        or data_summary.get("data_time")
        or data_summary.get("fetched_at")
        or "未提供"
    )
    quality = (
        data_summary.get("data_quality")
        or data_summary.get("quality")
        or data_summary.get("_data_quality")
        or "未提供"
    )
    summary_text = _safe_json_dumps(data_summary)
    note = "结构化摘要可用" if data_summary else "未提供结构化摘要"
    if any(marker in summary_text for marker in ["参考", "降级", "fallback", "mock", "模拟", "知识库"]):
        note = "可能使用降级、参考或知识库数据"

    return {
        "agent_name": result.agent_name,
        "source": str(source),
        "freshness": str(freshness),
        "quality": str(quality),
        "note": note,
    }


def _job_view(job: dict[str, Any]) -> dict[str, Any]:
    visible = {
        "job_id", "status", "progress", "message", "created_at", "updated_at",
        "result", "error", "attempts", "max_attempts",
    }
    return {key: value for key, value in job.items() if key in visible}


def _update_job(job_id: str, **fields) -> dict[str, Any]:
    job = analysis_jobs[job_id]
    job.update(fields)
    job["updated_at"] = datetime.now().isoformat()
    job_store.update(job_id, **fields)
    return job


def _safe_project_path(value: str | None) -> Optional[Path]:
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = project_root / path
    path = path.resolve()
    try:
        path.relative_to(project_root.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="路径必须位于项目目录内")
    return path


def _latest_files(pattern: str, limit: int = 5) -> list[dict]:
    files = sorted(
        settings.output_dir.glob(pattern),
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
        reverse=True,
    )
    return [
        {
            "path": str(path),
            "name": path.name,
            "updated_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
        }
        for path in files[:limit]
    ]


def _latest_two_stage_reports(limit: int = 5) -> list[dict]:
    items = _latest_files(
        "quant_two_stage/*/quant_two_stage_pipeline_report.json",
        limit=limit,
    )
    for item in items:
        try:
            payload = json.loads(Path(item["path"]).read_text(encoding="utf-8"))
            report = payload.get("two_stage") or {}
            gate = report.get("promotion_gate") or {}
            best = (report.get("aggregate_metrics") or {}).get(gate.get("best_model")) or {}
            portfolios = payload.get("portfolio") or {}
            item["summary"] = {
                "experiment_id": payload.get("experiment_id"),
                "version": payload.get("version"),
                "folds": len(report.get("folds") or []),
                "best_model": gate.get("best_model"),
                "should_promote": bool(gate.get("should_promote")),
                "brier_score": best.get("brier_score"),
                "gate_brier_delta": best.get("gate_brier_delta"),
                "rank_ic": best.get("rank_ic"),
                "actionable_coverage": best.get("actionable_coverage"),
                "top_k_mean_return_pct": best.get("top_k_mean_return_pct"),
                "feature_incremental": gate.get("feature_incremental"),
                "portfolio_candidates": len(portfolios),
                "portfolio_promoted": sum(
                    bool(value.get("promotion_gate", {}).get("should_promote"))
                    for value in portfolios.values()
                ),
            }
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            item["summary"] = {"error": str(exc)}
    return items


def _experiment_location(kind: str, stamp: Optional[str] = None):
    return resolve_experiment_location(
        kind,
        project_root=project_root,
        output_root=settings.output_dir,
        stamp=stamp,
    )


def _latest_mtime(path: Path) -> float:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_mtime
    latest = path.stat().st_mtime
    for child in path.rglob("*"):
        try:
            latest = max(latest, child.stat().st_mtime)
        except OSError:
            continue
    return latest


def _safe_json_summary(path: Path | None) -> dict:
    if not path or not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _compact_training_report(payload: dict) -> dict:
    if not payload:
        return {}
    compact = {key: value for key, value in payload.items() if key != "samples"}
    if "samples" in payload:
        compact["sample_preview_count"] = min(len(payload.get("samples") or []), 3)
    return compact


def _technical_prompt_loop_run_item(run_dir: Path) -> dict:
    training_json = run_dir / "technical_training_report.json"
    evaluation_json = run_dir / "technical_training_evaluation.json"
    candidate_report = run_dir / "candidate_sandbox" / "candidate_sandbox_report.json"
    candidate_md = run_dir / "candidate_sandbox" / "candidate_sandbox_report.md"
    candidate_root = project_root / "config" / "agent_improvement" / "candidates" / f"technical_prompt_loop_{run_dir.name}"
    artifact_files = list((candidate_root / "artifacts").glob("*.json")) if (candidate_root / "artifacts").exists() else []
    training_payload = _safe_json_summary(training_json)
    candidate_payload = _safe_json_summary(candidate_report)

    if candidate_report.exists():
        status = "completed"
        stage = "完整闭环已完成"
        message = "最终 candidate_sandbox_report.json 已生成，可以查看验证与晋升结果。"
    elif artifact_files:
        status = "running"
        stage = "候选已生成，验证中"
        message = "候选 prompt/skill 已写入沙箱，仍在等待 replay 或 holdout 生成最终报告。"
    elif evaluation_json.exists():
        status = "running"
        stage = "训练完成，候选生成中"
        message = "训练评估 JSON 已生成，但最终验证报告还没有完成。"
    elif training_json.exists():
        status = "running"
        stage = "训练样本已生成"
        message = "训练报告已生成，后续还需要候选生成、replay 与 holdout。"
    else:
        status = "running"
        stage = "初始化中"
        message = "输出目录已创建，训练报告尚未生成。"

    updated_ts = _latest_mtime(run_dir)
    if (
        status == "running"
        and updated_ts
        and time.time() - updated_ts > 20 * 60
    ):
        status = "incomplete"
        stage = f"{stage}，但可能已中断"
        message = "超过 20 分钟没有新的输出文件更新，最终报告尚未生成；可刷新状态或重新运行。"
    summary = candidate_payload.get("summary") or {}
    return {
        "name": run_dir.name,
        "path": str(run_dir),
        "status": status,
        "stage": stage,
        "message": message,
        "updated_at": datetime.fromtimestamp(updated_ts).isoformat() if updated_ts else None,
        "training_samples": training_payload.get("success_samples"),
        "candidate_count": summary.get("artifacts", len(artifact_files) if artifact_files else None),
        "validated_passed": summary.get("validated_passed"),
        "validated_failed": summary.get("validated_failed"),
        "paths": {
            "training_json": str(training_json) if training_json.exists() else "",
            "training_markdown": str(run_dir / "technical_training_report.md") if (run_dir / "technical_training_report.md").exists() else "",
            "evaluation_json": str(evaluation_json) if evaluation_json.exists() else "",
            "candidate_report_json": str(candidate_report) if candidate_report.exists() else "",
            "candidate_report_markdown": str(candidate_md) if candidate_md.exists() else "",
            "candidate_root": str(candidate_root) if candidate_root.exists() else "",
        },
    }


def _latest_technical_prompt_loop_runs(limit: int = 5) -> list[dict]:
    root = settings.output_dir / "technical_prompt_loop"
    if not root.exists():
        return []
    run_dirs = [path for path in root.iterdir() if path.is_dir()]
    run_dirs.sort(key=_latest_mtime, reverse=True)
    return [_technical_prompt_loop_run_item(path) for path in run_dirs[:limit]]


def _start_of_day_filter(value: str | None) -> Optional[str]:
    if not value:
        return None
    return value if "T" in value else f"{value}T00:00:00"


def _end_of_day_filter(value: str | None) -> Optional[str]:
    if not value:
        return None
    return value if "T" in value else f"{value}T23:59:59"


def _parse_datetime(value: str | None) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _prediction_target_v2_from_record(record) -> dict:
    return {
        "target_type": getattr(record, "target_type", "") or "",
        "horizon": getattr(record, "horizon", "") or "",
        "horizon_trading_days": getattr(record, "horizon_trading_days", None),
        "horizon_calendar_days": getattr(record, "horizon_calendar_days", None),
        "benchmark_symbol": getattr(record, "benchmark_symbol", None),
        "up_threshold_pct": getattr(record, "up_threshold_pct", None),
        "down_threshold_pct": getattr(record, "down_threshold_pct", None),
        "neutral_band_pct": getattr(record, "neutral_band_pct", None),
        "expected_return_pct": getattr(record, "expected_excess_return_pct", None),
        "prob_up": getattr(record, "prob_up", None),
        "prob_down": getattr(record, "prob_down", None),
        "prob_neutral": getattr(record, "prob_no_edge", None),
        "direction": getattr(record, "direction", "neutral"),
    }


def _prediction_edge_v2_from_record(record) -> dict:
    return {
        "expected_excess_return_pct": getattr(record, "expected_excess_return_pct", None),
        "prob_up": getattr(record, "prob_up", None),
        "prob_down": getattr(record, "prob_down", None),
        "prob_no_edge": getattr(record, "prob_no_edge", None),
        "edge_score": getattr(record, "edge_score", None),
        "decision": getattr(record, "decision", "") or "observe",
        "no_trade_reason": getattr(record, "no_trade_reason", "") or "",
        "neutral_reason": getattr(record, "neutral_reason", "") or "",
    }


def _prediction_verification_v2_from_record(record) -> dict:
    return {
        "actual_effective_return_pct": getattr(record, "actual_effective_return_pct", None),
        "actual_absolute_return_pct": getattr(record, "actual_absolute_return_pct", None),
        "actual_benchmark_return_pct": getattr(record, "actual_benchmark_return_pct", None),
        "window_max_effective_return_pct": getattr(record, "window_max_effective_return_pct", None),
        "window_min_effective_return_pct": getattr(record, "window_min_effective_return_pct", None),
        "target_type_used": getattr(record, "target_type_used", "") or "",
        "brier_score": getattr(record, "brier_score", None),
        "edge_hit": getattr(record, "edge_hit", None),
    }


def _probabilities_from_record(record) -> dict[str, float]:
    up = getattr(record, "prob_up", None)
    down = getattr(record, "prob_down", None)
    no_edge = getattr(record, "prob_no_edge", None)
    try:
        values = [float(up), float(down), float(no_edge)]
    except (TypeError, ValueError):
        confidence = max(0.0, min(1.0, float(getattr(record, "confidence", 0.0) or 0.0)))
        residual = 1.0 - confidence
        direction = getattr(record, "direction", "neutral")
        if direction == "bullish":
            values = [confidence, residual * 0.35, residual * 0.65]
        elif direction == "bearish":
            values = [residual * 0.35, confidence, residual * 0.65]
        else:
            values = [residual * 0.5, residual * 0.5, confidence]
    total = max(sum(values), 1e-9)
    return {
        "bullish": values[0] / total,
        "bearish": values[1] / total,
        "neutral": values[2] / total,
    }


def _brier_for_actual_direction(probs: dict[str, float], actual_direction: str) -> float:
    actual = str(actual_direction or "neutral")
    return round(sum(
        (probs[label] - (1.0 if actual == label else 0.0)) ** 2
        for label in ("bullish", "bearish", "neutral")
    ) / 3.0, 4)


def _prediction_pool_overview(store: PredictionStore) -> dict:
    with store._conn() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS total,
                      SUM(CASE WHEN verified_at IS NOT NULL THEN 1 ELSE 0 END) AS verified,
                      MIN(predicted_at) AS first_predicted_at,
                      MAX(predicted_at) AS latest_predicted_at,
                      AVG(brier_score) AS avg_brier_score,
                      AVG(CASE WHEN edge_hit IS NOT NULL THEN edge_hit * 1.0 ELSE NULL END) AS edge_hit_rate,
                      AVG(edge_score) AS avg_edge_score,
                      SUM(CASE WHEN decision IN ('long_bias','short_bias','watchlist') THEN 1 ELSE 0 END) AS actionable_count,
                      AVG(actual_effective_return_pct) AS avg_actual_effective_return_pct
               FROM predictions"""
        ).fetchone()
        targets = conn.execute(
            """SELECT target,
                      COALESCE(MAX(target_name), '') AS target_name,
                      COUNT(*) AS count,
                      SUM(CASE WHEN verified_at IS NOT NULL THEN 1 ELSE 0 END) AS verified_count,
                      MAX(predicted_at) AS latest_predicted_at
               FROM predictions
               GROUP BY target
               ORDER BY latest_predicted_at DESC
               LIMIT 100"""
        ).fetchall()
        timeframes = conn.execute(
            """SELECT timeframe,
                      COUNT(*) AS count,
                      SUM(CASE WHEN verified_at IS NOT NULL THEN 1 ELSE 0 END) AS verified_count
               FROM predictions
               GROUP BY timeframe
               ORDER BY count DESC"""
        ).fetchall()

    total = int(row["total"] or 0) if row else 0
    verified = int(row["verified"] or 0) if row else 0
    return {
        "total": total,
        "verified": verified,
        "unverified": total - verified,
        "first_predicted_at": row["first_predicted_at"] if row else None,
        "latest_predicted_at": row["latest_predicted_at"] if row else None,
        "avg_brier_score": round(row["avg_brier_score"], 4) if row and row["avg_brier_score"] is not None else 0,
        "edge_hit_rate": round(row["edge_hit_rate"], 3) if row and row["edge_hit_rate"] is not None else 0,
        "avg_edge_score": round(row["avg_edge_score"], 3) if row and row["avg_edge_score"] is not None else 0,
        "actionable_count": int(row["actionable_count"] or 0) if row else 0,
        "avg_actual_effective_return_pct": round(row["avg_actual_effective_return_pct"], 2) if row and row["avg_actual_effective_return_pct"] is not None else 0,
        "targets": [dict(item) for item in targets],
        "timeframes": [dict(item) for item in timeframes],
    }


def _passive_parameter_help() -> list[dict]:
    return [
        {
            "name": "评估样本阈值",
            "field": "min_samples",
            "meaning": "同一 Agent/证据桶至少出现多少条 agent 样本，才生成错误策略或优势信号。",
            "tip": "太小容易被偶然样本误导；样本少时建议 3-5，样本多后提高到 10+。",
        },
        {
            "name": "读取上限",
            "field": "limit",
            "meaning": "最多读取多少条已验证 agent 结果，不是预测条数；一条预测通常会拆成多个 Agent 样本。",
            "tip": "如果你已勾选具体预测，系统会自动保证每条预测的 Agent 样本尽量完整。",
        },
        {
            "name": "自动修改阈值",
            "field": "min_samples",
            "meaning": "Agent 改进工程师真正生成 prompt/skill 改法前，该问题场景需要达到的样本数。",
            "tip": "建议高于评估样本阈值，避免一两条失败样本就修改策略。",
        },
        {
            "name": "独立案例阈值",
            "field": "min_unique_cases",
            "meaning": "同一问题至少覆盖多少个不同预测案例，才允许进入自动改进。",
            "tip": "它防止同一支股票的重复预测把系统带偏。",
        },
        {
            "name": "仅演练",
            "field": "dry_run",
            "meaning": "只生成建议和补丁草案，不实际写入 prompt 或 skill。",
            "tip": "第一次看历史调优结果时保持开启。",
        },
    ]


def _prediction_sample_item(record) -> dict:
    now = datetime.now()
    predicted_at = _parse_datetime(record.predicted_at)
    valid_until = _parse_datetime(record.valid_until)
    days_elapsed = (now - predicted_at).days if predicted_at else None
    days_to_valid = (valid_until - now).days if valid_until else None
    verified = record.verified_at is not None
    if verified:
        hint = "已正式验证，可勾选纳入被动历史调优。"
    elif valid_until and valid_until <= now:
        hint = "已过验证日但尚未正式验证，先刷新走势或运行验证后再纳入调优。"
    else:
        hint = "仍在观察期，适合用刷新曲线看偏离程度，暂不建议纳入正式调优。"

    return {
        "id": record.id,
        "target": record.target,
        "target_name": record.target_name or "",
        "display_name": f"{record.target_name}({record.target})" if record.target_name else record.target,
        "timeframe": record.timeframe,
        "direction": record.direction,
        "min_pct": record.min_pct,
        "max_pct": record.max_pct,
        "confidence": record.confidence,
        **_prediction_edge_v2_from_record(record),
        "prediction_target": _prediction_target_v2_from_record(record),
        "predicted_at": record.predicted_at,
        "valid_until": record.valid_until,
        "verified": verified,
        "verified_at": record.verified_at,
        "actual_change_pct": record.actual_change_pct,
        **_prediction_verification_v2_from_record(record),
        "direction_correct": record.direction_correct,
        "magnitude_hit": record.magnitude_hit,
        "agents_used": record.agents_used,
        "agents_failed": record.agents_failed,
        "days_elapsed": days_elapsed,
        "days_to_valid_until": days_to_valid,
        "eligible_for_tuning": verified,
        "tuning_hint": hint,
    }


def _build_historical_evaluation(
    min_samples: int = 5,
    limit: int = 2000,
    output_path: Optional[Path] = None,
    prediction_ids: Optional[list[str]] = None,
    target: Optional[str] = None,
    timeframe: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> tuple[dict, dict]:
    evaluator = HistoricalAgentEvaluator()
    store = PredictionStore()
    samples = evaluator.samples_from_prediction_store(
        store,
        limit=limit,
        prediction_ids=prediction_ids,
        target=target,
        timeframe=timeframe,
        start_date=start_date,
        end_date=end_date,
    )
    report = evaluator.evaluate(samples, min_samples=min_samples)
    if output_path is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = settings.output_dir / f"historical_agent_evaluation_{stamp}.json"
    written = evaluator.write_report(report, output_path)
    return report.to_dict(), written


def _tracking_points_from_intraday(
    intraday_trend: list[dict],
    start_price: float,
    predicted_at: datetime,
    benchmark_trend: Optional[list[dict]] = None,
    benchmark_start_price: Optional[float] = None,
) -> tuple[list[dict], str, Optional[str]]:
    """把当前分钟行情转换为相对预测起点的跟踪点。"""
    if not intraday_trend or not start_price:
        return [], "absolute_return", "分钟行情不可用"

    predicted_ts = predicted_at.replace(second=0, microsecond=0)
    benchmark_by_time = {}
    if benchmark_trend:
        benchmark_start = float(benchmark_start_price) if benchmark_start_price else None
        for item in benchmark_trend:
            if benchmark_start:
                break
            close = item.get("close")
            if close is None:
                continue
            benchmark_start = float(close)
        if benchmark_start:
            for item in benchmark_trend:
                time_key = item.get("time") or item.get("date")
                close = item.get("close")
                if not time_key or close is None:
                    continue
                benchmark_by_time[str(time_key)] = (float(close) / benchmark_start - 1) * 100

    points = []
    for item in intraday_trend:
        time_text = item.get("time") or item.get("date")
        close = item.get("close")
        if not time_text or close is None:
            continue
        point_ts = _parse_datetime(str(time_text))
        if point_ts and point_ts < predicted_ts:
            continue
        close_value = float(close)
        actual = (close_value / start_price - 1) * 100
        benchmark_return = benchmark_by_time.get(str(time_text)) if benchmark_by_time else None
        effective = actual - benchmark_return if benchmark_return is not None else actual
        points.append({
            "time": str(time_text),
            "date": str(item.get("date") or str(time_text)[:10]),
            "close": round(close_value, 4),
            "open": round(float(item.get("open", close_value) or close_value), 4),
            "high": round(float(item.get("high", close_value) or close_value), 4),
            "low": round(float(item.get("low", close_value) or close_value), 4),
            "volume": float(item.get("volume", 0) or 0),
            "actual_return_pct": round(actual, 2),
            "effective_return_pct": round(effective, 2),
            "benchmark_return_pct": (
                round(benchmark_return, 2)
                if benchmark_return is not None
                else None
            ),
            "change_pct": round(effective, 2),
        })

    target_type_used = "excess_return" if benchmark_by_time else "absolute_return"
    note = None
    if not points:
        note = "分钟行情存在，但没有晚于预测时间的分钟点；可能是非交易时段或数据源只返回更早片段。"
    elif not benchmark_by_time and benchmark_trend:
        note = "分钟级基准未能按时间对齐，分钟视图暂按绝对收益展示。"
    elif not benchmark_trend:
        note = "分钟视图使用最新分钟行情，收益以预测起点价计算；跨日分钟历史取决于数据源覆盖。"
    return points, target_type_used, note


async def _build_prediction_tracking(record) -> dict:
    from src.data.price_fetcher import PriceFetcher
    from src.core.prediction_target import direction_correct, direction_from_return

    predicted_at = _parse_datetime(record.predicted_at)
    valid_until = _parse_datetime(record.valid_until)
    if predicted_at is None:
        raise ValueError("预测时间格式异常")

    now = datetime.now()
    target_spec = PredictionStore._target_spec_from_record(record.__dict__)
    fetcher = PriceFetcher()

    start_price = await fetcher.fetch_close_near(
        record.target,
        predicted_at,
        prefer="on_or_before",
        tolerance_days=10,
    )
    closes = await fetcher.fetch_close_window(record.target, predicted_at, now)
    benchmark = None
    if target_spec.target_type == "excess_return" and target_spec.benchmark_symbol:
        try:
            benchmark_start = await fetcher.fetch_close_near(
                target_spec.benchmark_symbol,
                predicted_at,
                prefer="on_or_before",
                tolerance_days=10,
            )
            benchmark_closes = await fetcher.fetch_close_window(
                target_spec.benchmark_symbol,
                predicted_at,
                now,
            )
            benchmark = (benchmark_start, benchmark_closes)
        except Exception as e:
            logger.debug(
                "预测跟踪基准收益获取失败: prediction=%s benchmark=%s error=%s",
                record.id,
                target_spec.benchmark_symbol,
                e,
            )

    actual_returns = (closes / start_price - 1) * 100
    effective_returns = actual_returns
    benchmark_returns = None
    aligned_benchmark = None
    target_type_used = "absolute_return"
    if benchmark:
        benchmark_start, benchmark_closes = benchmark
        benchmark_returns = (benchmark_closes / benchmark_start - 1) * 100
        aligned_benchmark = benchmark_returns.reindex(actual_returns.index, method="ffill").bfill()
        if not aligned_benchmark.isna().any():
            effective_returns = actual_returns - aligned_benchmark
            target_type_used = "excess_return"

    points = [{
        "date": predicted_at.date().isoformat(),
        "close": round(float(start_price), 4),
        "actual_return_pct": 0.0,
        "effective_return_pct": 0.0,
        "benchmark_return_pct": 0.0 if benchmark_returns is not None else None,
        "is_prediction_start": True,
    }]
    for idx, close in closes.items():
        date_text = idx.date().isoformat() if hasattr(idx, "date") else str(idx)[:10]
        if points and points[-1]["date"] == date_text:
            points.pop()
        actual = float(actual_returns.loc[idx])
        effective = float(effective_returns.loc[idx])
        benchmark_return = None
        if aligned_benchmark is not None:
            if idx in aligned_benchmark.index:
                benchmark_return = round(float(aligned_benchmark.loc[idx]), 2)
        points.append({
            "date": date_text,
            "close": round(float(close), 4),
            "actual_return_pct": round(actual, 2),
            "effective_return_pct": round(effective, 2),
            "benchmark_return_pct": benchmark_return,
            "is_prediction_start": False,
        })

    if not points:
        raise ValueError("没有可用走势点")

    latest = points[-1]
    effective_values = [float(p["effective_return_pct"]) for p in points]
    window_max = max(effective_values)
    window_min = min(effective_values)
    latest_effective = float(latest["effective_return_pct"])
    current_direction = direction_from_return(latest_effective, target_spec)
    if hasattr(current_direction, "value"):
        current_direction = current_direction.value
    correct_so_far = direction_correct(
        record.direction,
        latest_effective,
        window_max,
        window_min,
        target_spec,
    )
    probs = _probabilities_from_record(record)
    brier_so_far = _brier_for_actual_direction(probs, current_direction)
    range_hit_now = None
    predicted_mid = None
    if record.min_pct is not None and record.max_pct is not None:
        range_hit_now = record.min_pct <= latest_effective <= record.max_pct
        predicted_mid = (record.min_pct + record.max_pct) / 2.0

    intraday_points = []
    intraday_meta = {
        "available": False,
        "source": "none",
        "interval": "5m",
        "target_type_used": "absolute_return",
        "reason": "分钟行情不可用",
    }
    try:
        price_data = await fetcher.fetch(record.target, period="1mo")
        benchmark_trend = None
        if target_spec.target_type == "excess_return" and target_spec.benchmark_symbol:
            try:
                benchmark_price_data = await fetcher.fetch(
                    target_spec.benchmark_symbol,
                    period="1mo",
                )
                benchmark_trend = benchmark_price_data.intraday_trend
            except Exception as e:
                logger.debug(
                    "预测跟踪分钟基准获取失败: prediction=%s benchmark=%s error=%s",
                    record.id,
                    target_spec.benchmark_symbol,
                    e,
                )
        intraday_points, intraday_target_type, intraday_note = _tracking_points_from_intraday(
            price_data.intraday_trend,
            float(start_price),
            predicted_at,
            benchmark_trend=benchmark_trend,
            benchmark_start_price=benchmark[0] if benchmark else None,
        )
        source_meta = dict(price_data.intraday_meta or {})
        intraday_meta = {
            **source_meta,
            "available": bool(intraday_points),
            "source": source_meta.get("source") or "intraday",
            "interval": source_meta.get("interval") or "5m",
            "target_type_used": intraday_target_type,
            "points": len(intraday_points),
            "reason": intraday_note or source_meta.get("reason") or "",
            "latest_time": (
                intraday_points[-1].get("time")
                if intraday_points
                else source_meta.get("latest_time")
            ),
        }
    except Exception as e:
        intraday_meta["reason"] = f"分钟行情刷新失败: {e}"
        logger.debug("预测跟踪分钟行情获取失败: prediction=%s error=%s", record.id, e)

    return {
        "prediction": {
            "id": record.id,
            "target": record.target,
            "target_name": record.target_name,
            "display_name": f"{record.target_name}({record.target})" if record.target_name else record.target,
            "timeframe": record.timeframe,
            "direction": record.direction,
            "min_pct": record.min_pct,
            "max_pct": record.max_pct,
            "confidence": record.confidence,
            **_prediction_edge_v2_from_record(record),
            "prediction_target": _prediction_target_v2_from_record(record),
            "predicted_at": record.predicted_at,
            "valid_until": record.valid_until,
            "verified": record.verified_at is not None,
            "verified_at": record.verified_at,
        },
        "target_spec": target_spec.to_dict(),
        "summary": {
            "start_price": round(float(start_price), 4),
            "latest_price": latest["close"],
            "latest_date": latest["date"],
            "latest_actual_return_pct": latest["actual_return_pct"],
            "latest_effective_return_pct": latest_effective,
            "max_effective_return_pct": round(window_max, 2),
            "min_effective_return_pct": round(window_min, 2),
            "target_type_used": target_type_used,
            "current_direction": current_direction,
            "correct_so_far": bool(correct_so_far),
            "edge_hit_so_far": bool(correct_so_far),
            "brier_score_so_far": brier_so_far,
            "stored_brier_score": record.brier_score,
            "stored_edge_hit": record.edge_hit,
            "range_hit_now": range_hit_now,
            "predicted_mid_pct": round(predicted_mid, 2) if predicted_mid is not None else None,
            "expected_excess_return_pct": record.expected_excess_return_pct,
            "distance_to_predicted_mid_pct": (
                round(latest_effective - predicted_mid, 2)
                if predicted_mid is not None
                else None
            ),
            "distance_to_expected_excess_pct": (
                round(latest_effective - record.expected_excess_return_pct, 2)
                if record.expected_excess_return_pct is not None
                else None
            ),
            "trading_points": len(points),
            "days_elapsed": (now - predicted_at).days,
            "is_mature": bool(valid_until and now >= valid_until),
            "eligible_for_tuning": record.verified_at is not None,
            "tuning_hint": (
                "已正式验证，可在调优页勾选纳入被动历史调优。"
                if record.verified_at is not None
                else "这只是刷新到当前的观察曲线；正式调优仍建议使用已过有效期并验证完成的样本。"
            ),
        },
        "points": points,
        "intraday_points": intraday_points,
        "intraday_meta": intraday_meta,
    }


def _parse_target_list(value: str) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _registry_file_info(path: Path = DEFAULT_REGISTRY_PATH) -> dict:
    if not path.exists():
        return {
            "path": str(path),
            "exists": False,
            "updated_at": None,
        }
    return {
        "path": str(path),
        "exists": True,
        "updated_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
    }


def _skill_to_api_dict(skill) -> dict:
    payload = skill.to_dict()
    validation = payload.get("validation") or {}
    holdout = validation.get("holdout") or {}
    source = payload.get("source") or {}
    payload["validation_summary"] = {
        "training_samples": validation.get("training_samples"),
        "training_unique_cases": validation.get("training_unique_cases"),
        "training_accuracy": validation.get("training_accuracy"),
        "training_avg_confidence": validation.get("training_avg_confidence"),
        "holdout_samples": holdout.get("holdout_samples"),
        "changed_predictions": holdout.get("changed_predictions"),
        "matched_samples": holdout.get("matched_samples"),
        "accuracy_delta": holdout.get("accuracy_delta"),
        "brier_delta": holdout.get("brier_delta"),
        "baseline_accuracy": holdout.get("baseline_accuracy"),
        "candidate_accuracy": holdout.get("candidate_accuracy"),
        "baseline_brier": holdout.get("baseline_brier"),
        "candidate_brier": holdout.get("candidate_brier"),
        "passed": bool(holdout),
        "reason": holdout.get("reason", ""),
    }
    payload["source_summary"] = {
        "generated_by": source.get("generated_by"),
        "data_source": source.get("data_source"),
        "training_report_path": source.get("training_report_path"),
        "holdout_report_path": source.get("holdout_report_path"),
        "created_at": source.get("created_at") or payload.get("created_at"),
    }
    return payload


def _load_skill_registry_response(
    agent_name: Optional[str] = None,
    skill_type: Optional[str] = None,
    enabled: Optional[bool] = None,
) -> dict:
    registry = AgentSkillRegistry()
    skills = registry.skills
    if agent_name:
        skills = [skill for skill in skills if skill.agent_name == agent_name]
    if skill_type:
        skills = [skill for skill in skills if skill.skill_type == skill_type]
    if enabled is not None:
        skills = [skill for skill in skills if skill.enabled is enabled]
    return {
        "success": True,
        "registry": _registry_file_info(registry.path),
        "summary": registry.summary(),
        "skills": [_skill_to_api_dict(skill) for skill in skills],
    }


async def _run_improvement_engineer(
    request: ImprovementRunRequest,
) -> dict:
    source_path = _safe_project_path(request.report_path)
    generated_evaluation_paths = None
    if source_path:
        if not source_path.exists():
            raise HTTPException(status_code=404, detail="历史评估报告不存在")
        evaluation_report = json.loads(source_path.read_text(encoding="utf-8"))
    else:
        prediction_ids = [pid.strip() for pid in request.prediction_ids if pid.strip()]
        limit = max(request.limit, len(prediction_ids) * 10) if prediction_ids else request.limit
        evaluation_report, generated_evaluation_paths = _build_historical_evaluation(
            min_samples=request.evaluation_min_samples,
            limit=limit,
            prediction_ids=prediction_ids or None,
            target=request.target,
            timeframe=request.timeframe,
        )

    if request.use_llm_review and not llm:
        raise HTTPException(status_code=500, detail="LLM 未初始化，无法做 LLM 复核")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = settings.output_dir / "agent_improvement_engineer" / stamp
    engineer = AgentImprovementEngineer(llm=llm if request.use_llm_review else None)
    report = await engineer.run(
        evaluation_report,
        config=ImprovementEngineerConfig(
            project_root=project_root,
            output_dir=output_dir,
            min_samples_for_auto_apply=request.min_samples,
            min_unique_cases_for_auto_apply=request.min_unique_cases,
            dry_run=request.dry_run,
            allow_prompt_apply=request.allow_prompt_apply,
            allow_declarative_skill_apply=request.allow_skill_apply,
            use_llm_review=request.use_llm_review,
        ),
        source_report_path=str(source_path) if source_path else generated_evaluation_paths["json"],
    )
    return {
        "success": True,
        "output_dir": str(output_dir),
        "generated_evaluation": generated_evaluation_paths,
        "report": report.to_dict(),
        "report_paths": {
            "json": str(output_dir / "agent_improvement_engineer_report.json"),
            "markdown": str(output_dir / "agent_improvement_engineer_report.md"),
        },
    }


async def _run_candidate_sandbox(request: CandidateSandboxRequest) -> dict:
    source_path = _safe_project_path(request.report_path)
    generated_evaluation_paths = None
    if source_path:
        if not source_path.exists():
            raise HTTPException(status_code=404, detail="历史评估报告不存在")
        evaluation_report = json.loads(source_path.read_text(encoding="utf-8"))
    else:
        prediction_ids = [pid.strip() for pid in request.prediction_ids if pid.strip()]
        limit = max(request.limit, len(prediction_ids) * 10) if prediction_ids else request.limit
        evaluation_report, generated_evaluation_paths = _build_historical_evaluation(
            min_samples=request.evaluation_min_samples,
            limit=limit,
            prediction_ids=prediction_ids or None,
            target=request.target,
            timeframe=request.timeframe,
        )

    if request.use_llm_candidates and not llm:
        raise HTTPException(status_code=500, detail="LLM 未初始化，无法生成候选 prompt/skill")
    if request.run_technical_prompt_replay and not llm:
        raise HTTPException(status_code=500, detail="LLM 未初始化，无法运行技术面 prompt replay")

    holdout_targets = _parse_target_list(request.holdout_targets)
    if request.validate_technical and not holdout_targets:
        raise HTTPException(status_code=400, detail="技术面验证至少需要一个 holdout 标的")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = settings.output_dir / "agent_candidate_sandbox" / stamp
    candidate_root = project_root / "config" / "agent_improvement" / "candidates" / stamp
    sandbox = CandidateValidationSandbox(
        llm=llm if request.use_llm_candidates else None,
    )
    source_report = str(source_path) if source_path else generated_evaluation_paths["json"]
    report = await sandbox.run(
        evaluation_report,
        config=CandidateSandboxConfig(
            project_root=project_root,
            output_dir=output_dir,
            candidate_root=candidate_root,
            candidate_id=stamp,
            min_samples=request.min_samples,
            min_unique_cases=request.min_unique_cases,
            use_llm_candidates=request.use_llm_candidates,
            apply_if_passed=request.apply_if_passed,
            allow_prompt_promotion=request.allow_prompt_promotion,
            allow_skill_promotion=request.allow_skill_promotion,
            validate_technical=request.validate_technical,
            holdout_targets=holdout_targets,
            holdout_start_date=request.holdout_start_date,
            holdout_end_date=request.holdout_end_date,
            holdout_timeframe=request.holdout_timeframe,
            holdout_interval_days=request.holdout_interval_days,
            holdout_lookback_days=request.holdout_lookback_days,
            holdout_tolerance_days=request.holdout_tolerance_days,
            min_accuracy_delta=request.min_accuracy_delta,
            min_holdout_samples=request.min_holdout_samples,
            min_changed_predictions=request.min_changed_predictions,
            confidence_cap=request.confidence_cap,
            min_brier_delta=request.min_brier_delta,
            min_confidence_changed=request.min_confidence_changed,
            min_confidence_matched=request.min_confidence_matched,
            run_technical_prompt_replay=request.run_technical_prompt_replay,
            prompt_replay_max_samples=request.prompt_replay_max_samples,
            prompt_replay_min_samples=request.prompt_replay_min_samples,
            prompt_replay_min_accuracy_delta=request.prompt_replay_min_accuracy_delta,
            prompt_replay_min_brier_delta=request.prompt_replay_min_brier_delta,
            prompt_replay_min_changed_predictions=request.prompt_replay_min_changed_predictions,
            prompt_replay_overconfidence_threshold=request.prompt_replay_overconfidence_threshold,
            prompt_replay_max_overconfidence_delta=request.prompt_replay_max_overconfidence_delta,
            candidate_batch_count=request.candidate_batch_count,
        ),
        source_report_path=source_report,
    )
    return {
        "success": True,
        "output_dir": str(output_dir),
        "candidate_root": str(candidate_root),
        "generated_evaluation": generated_evaluation_paths,
        "report": report.to_dict(),
        "report_paths": {
            "json": str(output_dir / "candidate_sandbox_report.json"),
            "markdown": str(output_dir / "candidate_sandbox_report.md"),
        },
    }


def _technical_signal_prompt_variants(raw_signals: list[dict], agent_name: str) -> list[dict]:
    signals = []
    for signal in raw_signals:
        payload = dict(signal)
        payload.setdefault("agent_name", agent_name)
        payload.setdefault("signal_type", "wrong_strategy")
        payload.setdefault("unique_cases", payload.get("sample_size", 0))
        signals.append(payload)
        if payload.get("area") != "prompt":
            prompt_payload = dict(payload)
            prompt_payload["area"] = "prompt"
            prompt_payload["recommendation"] = (
                "将该失败场景交给 LLM 生成候选 prompt guardrail，并通过 prompt replay 验证。"
            )
            signals.append(prompt_payload)
    return signals


def _technical_batch_evaluation_reports(
    bootstrap_report: dict,
    batch_count: int,
    min_samples: int,
) -> list[dict]:
    samples = list(bootstrap_report.get("samples") or [])
    agent_name = bootstrap_report.get("agent_name") or "近期股价分析师"
    if batch_count <= 1 or not samples:
        return []
    effective_batches = min(max(1, batch_count), len(samples))
    buckets = [[] for _ in range(effective_batches)]
    for idx, sample in enumerate(samples):
        buckets[idx % effective_batches].append(sample)

    evaluator = HistoricalAgentEvaluator()
    reports: list[dict] = []
    for idx, batch_samples in enumerate(buckets, start=1):
        if not batch_samples:
            continue
        sample_objs = evaluator.samples_from_bootstrap_report({
            "agent_name": agent_name,
            "samples": batch_samples,
        })
        report = evaluator.evaluate(
            sample_objs,
            min_samples=min_samples,
        ).to_dict()
        report["batch_id"] = f"batch_{idx:02d}"
        report["batch_sample_count"] = len(batch_samples)
        report["improvement_signals"] = _technical_signal_prompt_variants(
            report.get("improvement_signals") or [],
            agent_name,
        )
        report["wrong_strategy_signals"] = [
            signal for signal in report["improvement_signals"]
            if signal.get("signal_type", "wrong_strategy") == "wrong_strategy"
        ]
        reports.append(report)
    return reports


def _technical_bootstrap_to_evaluation_report(
    bootstrap_report: dict,
    candidate_batch_count: int = 1,
    min_samples: int = 20,
) -> dict:
    raw_signals = bootstrap_report.get("improvement_signals") or []
    agent_name = bootstrap_report.get("agent_name") or "近期股价分析师"
    signals = _technical_signal_prompt_variants(raw_signals, agent_name)
    return {
        "total_samples": bootstrap_report.get("success_samples", 0),
        "verified_predictions": bootstrap_report.get("success_samples", 0),
        "improvement_signals": signals,
        "wrong_strategy_signals": signals,
        "strength_signals": [],
        "candidate_batches": _technical_batch_evaluation_reports(
            bootstrap_report,
            candidate_batch_count,
            min_samples,
        ),
        "agents": {
            agent_name: {
                "total": bootstrap_report.get("success_samples", 0),
                "accuracy": bootstrap_report.get("direction_accuracy", 0.0),
            }
        },
    }


async def _run_technical_prompt_loop(request: TechnicalPromptLoopRequest) -> dict:
    if not llm:
        raise HTTPException(status_code=500, detail="LLM 未初始化，无法运行技术面 LLM 调优闭环")
    targets = _parse_target_list(request.targets)
    holdout_targets = _parse_target_list(request.holdout_targets)
    if not targets:
        raise HTTPException(status_code=400, detail="训练样本至少需要一个标的")
    if not holdout_targets:
        raise HTTPException(status_code=400, detail="holdout 至少需要一个标的")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    location = _experiment_location("technical_prompt_loop", stamp)
    output_dir = location.root
    output_dir.mkdir(parents=True, exist_ok=True)
    training_report = await TechnicalCalibrationBootstrapper().run(
        CalibrationBootstrapConfig(
            targets=targets,
            start_date=request.start_date,
            end_date=request.end_date,
            timeframe=request.timeframe,
            interval_days=request.interval_days,
            lookback_days=request.lookback_days,
            tolerance_days=request.tolerance_days,
        )
    )
    training_payload = training_report.to_dict()
    training_json = output_dir / "technical_training_report.json"
    training_md = output_dir / "technical_training_report.md"
    training_json.write_text(
        json.dumps(training_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    training_md.write_text(training_report.summary() + "\n", encoding="utf-8")

    evaluation_report = _technical_bootstrap_to_evaluation_report(
        training_payload,
        candidate_batch_count=request.candidate_batch_count,
        min_samples=request.min_samples,
    )
    evaluation_json = output_dir / "technical_training_evaluation.json"
    evaluation_json.write_text(
        json.dumps(evaluation_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    candidate_root = (
        project_root / ".pytest-tmp" / "candidates" / f"technical_prompt_loop_{stamp}"
        if location.source_type == "test"
        else project_root / "config" / "agent_improvement" / "candidates" / f"technical_prompt_loop_{stamp}"
    )
    sandbox_output_dir = output_dir / "candidate_sandbox"
    sandbox = CandidateValidationSandbox(llm=llm if request.use_llm_candidates else None)
    sandbox_report = await sandbox.run(
        evaluation_report,
        config=CandidateSandboxConfig(
            project_root=project_root,
            output_dir=sandbox_output_dir,
            candidate_root=candidate_root,
            candidate_id=f"technical_prompt_loop_{stamp}",
            min_samples=request.min_samples,
            min_unique_cases=request.min_unique_cases,
            use_llm_candidates=request.use_llm_candidates,
            apply_if_passed=request.apply_if_passed,
            allow_prompt_promotion=request.allow_prompt_promotion,
            allow_skill_promotion=request.allow_skill_promotion,
            validate_technical=True,
            holdout_targets=holdout_targets,
            holdout_start_date=request.holdout_start_date,
            holdout_end_date=request.holdout_end_date,
            holdout_timeframe=request.holdout_timeframe,
            holdout_interval_days=request.holdout_interval_days,
            holdout_lookback_days=request.holdout_lookback_days,
            holdout_tolerance_days=request.holdout_tolerance_days,
            min_accuracy_delta=request.min_accuracy_delta,
            min_holdout_samples=request.min_holdout_samples,
            min_changed_predictions=request.min_changed_predictions,
            confidence_cap=request.confidence_cap,
            min_brier_delta=request.min_brier_delta,
            min_confidence_changed=request.min_confidence_changed,
            min_confidence_matched=request.min_confidence_matched,
            run_technical_prompt_replay=True,
            prompt_replay_max_samples=request.prompt_replay_max_samples,
            prompt_replay_min_samples=request.prompt_replay_min_samples,
            prompt_replay_min_accuracy_delta=request.prompt_replay_min_accuracy_delta,
            prompt_replay_min_brier_delta=request.prompt_replay_min_brier_delta,
            prompt_replay_min_changed_predictions=request.prompt_replay_min_changed_predictions,
            prompt_replay_overconfidence_threshold=request.prompt_replay_overconfidence_threshold,
            prompt_replay_max_overconfidence_delta=request.prompt_replay_max_overconfidence_delta,
            candidate_batch_count=request.candidate_batch_count,
        ),
        source_report_path=str(evaluation_json),
    )
    return {
        "success": True,
        "output_dir": str(output_dir),
        "candidate_root": str(candidate_root),
        "training_report": _compact_training_report(training_payload),
        "generated_evaluation": {"json": str(evaluation_json)},
        "report": sandbox_report.to_dict(),
        "report_paths": {
            "training_json": str(training_json),
            "training_markdown": str(training_md),
            "json": str(sandbox_output_dir / "candidate_sandbox_report.json"),
            "markdown": str(sandbox_output_dir / "candidate_sandbox_report.md"),
        },
    }


async def _run_self_improvement_lab(request: SelfImprovementLabRequest) -> dict:
    targets = _parse_target_list(request.targets)
    if not targets:
        raise HTTPException(status_code=400, detail="至少需要一个标的代码")

    news_snapshots_path = _safe_project_path(request.news_snapshots_path)
    if news_snapshots_path and not news_snapshots_path.exists():
        raise HTTPException(status_code=404, detail="新闻快照文件不存在")
    point_in_time_snapshots_path = _safe_project_path(request.point_in_time_snapshots_path)
    if point_in_time_snapshots_path and not point_in_time_snapshots_path.exists():
        raise HTTPException(status_code=404, detail="point-in-time 快照路径不存在")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = settings.output_dir / "self_improvement_lab" / stamp
    report = await SelfImprovementLab().run(
        SelfImprovementLabConfig(
            targets=targets,
            start_date=request.start_date,
            end_date=request.end_date,
            timeframe=request.timeframe,
            interval_days=request.interval_days,
            lookback_days=request.lookback_days,
            tolerance_days=request.tolerance_days,
            evaluation_min_samples=request.evaluation_min_samples,
            run_engineer=request.run_engineer,
            engineer_min_samples=request.engineer_min_samples,
            engineer_min_unique_cases=request.engineer_min_unique_cases,
            dry_run=request.dry_run,
            allow_prompt_apply=request.allow_prompt_apply,
            allow_skill_apply=request.allow_skill_apply,
            output_dir=output_dir,
            news_snapshots_path=news_snapshots_path,
            point_in_time_snapshots_path=point_in_time_snapshots_path,
        )
    )
    return {
        "success": True,
        "output_dir": str(output_dir),
        "report": report.to_dict(),
        "report_paths": {
            "json": str(output_dir / "self_improvement_lab_report.json"),
            "markdown": str(output_dir / "self_improvement_lab_report.md"),
        },
    }


async def _run_analysis(
    target: str,
    timeframe: str,
    market: Optional[str] = None,
    skip_agents: list[str] = None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> dict:
    """执行完整分析并返回结构化结果"""
    task_llm, task_llm_model = _current_llm_snapshot()

    def progress(value: int, message: str) -> None:
        if progress_callback:
            progress_callback(value, message)

    progress(5, "解析标的")

    # 解析中文股名 → 代码
    target_info = resolve_symbol(target, market_hint=market)
    _validate_resolved_symbol(target_info)
    resolved_target = target_info.symbol
    agent_target = _agent_target_from_info(target_info)
    target_label = target_info.display_name
    if resolved_target != target:
        logger.info(f"股名解析: {target} → {target_label} [{target_info.market}]")

    start_time = time.monotonic()

    # 构建 Orchestrator
    progress(12, "构建 Agent 团队")
    orchestrator, active_names = _build_orchestrator(skip_agents, llm_client=task_llm)

    # 执行 Agent
    agent_run_mode = "顺序" if getattr(orchestrator, "max_concurrent_agents", None) == 1 else "并行"
    progress(20, f"{agent_run_mode}执行 Agent 分析")
    def on_agent_done(agent_name: str, completed: int, total: int) -> None:
        if total <= 0:
            return
        agent_progress = 20 + int((completed / total) * 42)
        progress(
            min(agent_progress, 62),
            f"Agent 分析进度 {completed}/{total}: {agent_name} 已结束",
        )

    agent_results = await orchestrator.run_selected(
        agent_target,
        timeframe,
        agent_names=active_names,
        on_agent_done=on_agent_done,
    )

    if not agent_results:
        raise RuntimeError("所有 Agent 均失败，无法生成报告")

    # 权重计算
    succeeded_names = {r.agent_name for r in agent_results}
    missing_names = [n for n in active_names if n not in succeeded_names]
    returned_failed_names = [
        r.agent_name for r in agent_results
        if getattr(r, "status", "ok") == "failed"
    ]
    failed_names = missing_names + [n for n in returned_failed_names if n not in missing_names]
    agent_statuses = [
        {"agent_name": name, "status": "failed", "reason": "Agent 未返回结果或执行失败"}
        for name in missing_names
    ]
    agent_statuses.extend(_agent_status_from_result(r) for r in agent_results)
    failed_agents, degraded_agents = _split_agent_statuses(agent_statuses)
    data_quality_summary = [_data_quality_from_result(r) for r in agent_results]

    progress(65, "计算权重并处理 Agent 状态")
    weight_config = weight_mgr.redistribute_weights(
        timeframe, succeeded_names, failed_names,
    )

    # 汇总
    progress(75, "汇总分析师综合研判")
    aggregator = Aggregator(task_llm)
    report = await aggregator.aggregate(
        target_label, timeframe, agent_results,
        weight_config=weight_config,
        failed_agents=failed_names if failed_names else None,
    )
    progress(88, "生成最终报告")

    elapsed = time.monotonic() - start_time

    # 保存到 PredictionStore
    prediction_id = None
    try:
        from src.data.prediction_store import PredictionStore
        store = PredictionStore()
        prediction_id = store.save_prediction(
            target=target_info.symbol,
            timeframe=timeframe,
            report=report,
            agent_results=agent_results,
            agents_used=list(succeeded_names),
            agents_failed=failed_names,
            elapsed_seconds=elapsed,
            llm_model=task_llm_model,
            target_name=target_info.name or (target if target != target_info.symbol else ""),
        )
        from src.data.quant_feature_store import (
            QuantFeatureRow,
            QuantFeatureStore,
            extract_prediction_features,
        )

        target_spec = report.prediction_target
        QuantFeatureStore().save(QuantFeatureRow(
            market=target_info.market,
            symbol=target_info.symbol,
            target_name=target_info.name,
            as_of=datetime.now().date().isoformat(),
            timeframe=timeframe,
            horizon=target_spec.horizon,
            target_version=target_spec.target_version,
            prediction_id=prediction_id,
            valid_date=(datetime.now() + timedelta(days=target_spec.horizon_calendar_days)).date().isoformat(),
            features=extract_prediction_features(agent_results, report),
            source_kind="current_capture",
            lineage={
                "point_in_time_verified": True,
                "prediction_origin": "live",
                "prediction_id": prediction_id,
                "llm_model": task_llm_model,
                "agent_names": list(succeeded_names),
                "source_timestamps": [datetime.now().isoformat()],
                "target_spec": target_spec.to_dict(),
            },
        ))
    except Exception as e:
        logger.warning(f"预测记录失败: {e}")

    progress(95, "整理响应数据")

    intraday_trend, intraday_meta = _extract_intraday_context(agent_results)

    # 构建响应
    return {
        "success": True,
        "target": target,
        "resolved_target": resolved_target,
        "timeframe": timeframe,
        "generated_at": datetime.now().isoformat(),
        "elapsed_seconds": round(elapsed, 1),
        "agent_results": [_agent_result_to_dict(r) for r in agent_results],
        "final_report": report.to_dict(),
        "prediction_id": prediction_id,
        "agent_statuses": agent_statuses,
        "failed_agents": failed_agents,
        "degraded_agents": degraded_agents,
        "data_quality_summary": data_quality_summary,
        "target_info": _target_info_dict(target_info),
        "price_trend": _extract_price_trend(agent_results),
        "intraday_trend": intraday_trend,
        "intraday_meta": intraday_meta,
        "disclaimer": DISCLAIMER,
    }


# ================================================================
# API 路由
# ================================================================


@app.get("/api/models")
async def list_llm_models():
    """列出可用 LLM 模型配置，API Key 只返回掩码。"""
    try:
        return model_registry.public_state(llm_ready=llm is not None)
    except Exception as e:
        logger.error(f"读取模型配置失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/models")
async def add_llm_model(request: LLMModelCreateRequest):
    """新增一个可切换的 LLM 模型配置。"""
    try:
        model = model_registry.add_model(
            {
                "name": request.name,
                "provider": request.provider,
                "base_url": request.base_url,
                "model": request.model,
                "api_key": request.api_key or "",
                "temperature": request.temperature,
                "max_tokens": request.max_tokens,
                "verify_ssl": request.verify_ssl,
            },
            activate=request.set_active,
        )
        if request.set_active:
            _reload_llm_from_registry()
        return {
            "success": True,
            "model": model.to_public_dict(active=request.set_active),
            **model_registry.public_state(llm_ready=llm is not None),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"新增模型失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/models/active")
async def activate_llm_model(request: LLMModelActivateRequest):
    """切换当前 Agent 团队使用的 LLM 模型。"""
    try:
        model_registry.set_active_model(request.model_id)
        _reload_llm_from_registry()
        return {
            "success": True,
            **model_registry.public_state(llm_ready=llm is not None),
        }
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"切换模型失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/models/{model_id}")
async def update_llm_model(model_id: str, request: LLMModelUpdateRequest):
    """更新用户添加的 LLM 模型配置。"""
    try:
        was_active = model_registry.active_model_id() == model_id
        model_registry.update_model(
            model_id,
            request.model_dump(exclude_unset=True),
        )
        if was_active:
            _reload_llm_from_registry()
        return {
            "success": True,
            **model_registry.public_state(llm_ready=llm is not None),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"更新模型失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/models/{model_id}")
async def delete_llm_model(model_id: str):
    """删除用户添加的模型配置。"""
    try:
        was_active = model_registry.active_model_id() == model_id
        model_registry.delete_model(model_id)
        if was_active:
            _reload_llm_from_registry()
        return {
            "success": True,
            **model_registry.public_state(llm_ready=llm is not None),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"删除模型失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/health")
async def health_check():
    """健康检查"""
    active_model = model_registry.get_active_model()
    return {
        "status": "ok",
        "llm_ready": llm is not None,
        "model": active_model.model,
        "model_name": active_model.name,
        "model_id": active_model.id,
        "active_jobs": sum(1 for job in analysis_jobs.values() if job["status"] not in TERMINAL_JOB_STATUSES),
        "persistent_jobs": job_store.status(),
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/api/agents")
async def list_agents():
    """获取 Agent 列表及权重信息"""
    agents = []
    descs = {
        "tech": "基于 K 线和技术指标（MA/MACD/RSI/布林带）判断短期走势",
        "news": "基于最新新闻情绪和事件评估判断市场预期差",
        "fundamental": "基于财报数据和估值水平判断公司内在价值",
        "macro": "基于宏观经济指标（CPI/PMI/GDP/汇率）判断外部环境",
        "industry": "基于行业对比和估值分位判断相对位置",
    }
    for key, name in AGENT_NAMES.items():
        w_short = weight_mgr.get_weight(name, "短期(1周)")
        w_mid = weight_mgr.get_weight(name, "中期(1月)")
        w_long = weight_mgr.get_weight(name, "长期(1季)")
        agents.append({
            "key": key,
            "name": name,
            "description": descs.get(key, ""),
            "weights": {"short": w_short, "mid": w_mid, "long": w_long},
        })
    return {"agents": agents}


@app.get("/api/improvement/status")
async def improvement_status():
    """获取 Agent 调优链路状态。"""
    return {
        "engineer": {
            "name": "Agent 改进工程师",
            "description": "基于真实历史样本评估结果，生成候选 prompt/skill 并通过沙箱验证后受控晋升",
            "auto_apply": ["validated_prompt", "validated_skill_registry"],
            "draft_only": ["core_code", "mcp", "data_source", "calibration"],
        },
        "latest_evaluations": _latest_files("historical_agent_evaluation*.json", limit=5),
        "latest_candidate_sandboxes": _latest_files(
            "agent_candidate_sandbox/*/candidate_sandbox_report.json",
            limit=5,
        ),
        "latest_technical_prompt_loops": _latest_files(
            "technical_prompt_loop/*/candidate_sandbox/candidate_sandbox_report.json",
            limit=5,
        ),
        "latest_technical_prompt_loop_runs": _latest_technical_prompt_loop_runs(limit=5),
        "latest_engineer_reports": _latest_files(
            "agent_improvement_engineer/*/agent_improvement_engineer_report.json",
            limit=5,
        ),
        "latest_self_improvement_labs": _latest_files(
            "self_improvement_lab/*/self_improvement_lab_report.json",
            limit=5,
        ),
    }


@app.get("/api/quant/status")
async def quant_status():
    """Quant Core 数据、依赖、模型和最近报告状态。"""
    from src.core.learned_aggregator import LearnedAggregatorPolicy
    from src.core.prediction_target import default_target_spec
    from src.core.quant_models import dependency_status
    from src.data.quant_feature_store import QuantFeatureStore
    from src.data.investable_universe import InvestableUniverseStore
    from src.data.quant_pit_enrichment import QuantPitEnrichmentStore
    from src.data.quant_price_cache import QuantPriceCache
    from src.core.evidence_maintenance import EvidenceMaintenanceRunner
    from src.core.experiment_ledger import ExperimentLedger

    return {
        "success": True,
        "feature_store": QuantFeatureStore().status(),
        "investable_universe": InvestableUniverseStore().status(),
        "pit_enrichment": QuantPitEnrichmentStore().status(),
        "price_cache": QuantPriceCache().status(),
        "dependencies": dependency_status(),
        "learned_aggregators": LearnedAggregatorPolicy().status(),
        "evidence_maintenance": EvidenceMaintenanceRunner.status(),
        "experiment_ledger": ExperimentLedger.default().status(),
        "runtime_jobs": job_store.status(),
        "targets": {
            market: {
                horizon: default_target_spec(timeframe, market=market).to_dict()
                for horizon, timeframe in (
                    ("5d", "短期(1周)"),
                    ("20d", "中期(1月)"),
                    ("60d", "长期(1季)"),
                )
            }
            for market in ("A", "HK", "US")
        },
        "latest_walk_forward": _latest_files(
            "quant_walk_forward/*/walk_forward_report.json", limit=8,
        ),
        "latest_two_stage": _latest_two_stage_reports(limit=8),
        "latest_research_data_v2": _latest_files(
            "research_data_v2/*/research_data_v2_report.json", limit=5,
        ),
        "latest_portfolio_backtests": _latest_files(
            "portfolio_backtest/*/portfolio_backtest.json", limit=8,
        ),
    }


@app.get("/api/evidence-maintenance/status")
async def evidence_maintenance_status():
    from src.core.evidence_maintenance import EvidenceMaintenanceRunner

    return {"success": True, **await asyncio.to_thread(EvidenceMaintenanceRunner.status)}


@app.post("/api/evidence-maintenance/run")
async def evidence_maintenance_run(request: EvidenceMaintenanceRequest):
    from src.core.evidence_maintenance import EvidenceMaintenanceConfig, EvidenceMaintenanceRunner

    report = await EvidenceMaintenanceRunner().run_once(
        EvidenceMaintenanceConfig(**request.model_dump())
    )
    return {"success": not report.errors, "report": report.to_dict()}


@app.post("/api/quant/build-dataset")
async def quant_build_dataset(request: QuantDatasetRequest):
    from src.core.quant_dataset import QuantDatasetBuildConfig, QuantHistoricalDatasetBuilder

    targets = _parse_target_list(request.targets)
    if not targets and not request.use_universe:
        raise HTTPException(status_code=400, detail="至少需要一个训练标的")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = _experiment_location("quant_dataset", stamp).root
    try:
        report = await QuantHistoricalDatasetBuilder().run(
            QuantDatasetBuildConfig(
                targets=targets,
                start_date=request.start_date,
                end_date=request.end_date,
                timeframe=request.timeframe,
                interval_days=request.interval_days,
                lookback_days=request.lookback_days,
                max_samples=request.max_samples,
                export_parquet=request.export_parquet,
                use_universe=request.use_universe,
                universe_market=request.universe_market,
                universe_limit=request.universe_limit,
                min_listing_days=request.min_listing_days,
                min_price=request.min_price,
                min_avg_traded_value=request.min_avg_traded_value,
                industry_neutralization=request.industry_neutralization,
                universe_sample_seed=request.universe_sample_seed,
                universe_stratify=request.universe_stratify,
                replace_partition=request.replace_partition,
                use_pit_enrichment=request.use_pit_enrichment,
                fundamental_max_age_days=request.fundamental_max_age_days,
                announcement_lookback_days=request.announcement_lookback_days,
                industry_standard=request.industry_standard,
                use_price_cache=request.use_price_cache,
                history_fetch_concurrency=request.history_fetch_concurrency,
            ),
            output_dir=output_dir,
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "quant_dataset_report.json"
        path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return {"success": True, "report": report.to_dict(), "report_path": str(path)}
    except Exception as exc:
        logger.error("Quant 数据集构建失败: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/quant/refresh-enrichment")
async def quant_refresh_enrichment(request: QuantPitRefreshRequest):
    from src.data.investable_universe import InvestableUniverseStore
    from src.data.quant_pit_enrichment import QuantPitEnrichmentRefresher, QuantPitRefreshConfig

    symbols = _parse_target_list(request.targets)
    if request.use_universe:
        members = InvestableUniverseStore().sampled_union(
            request.start_date,
            request.end_date,
            interval_days=request.interval_days,
            market="A",
            min_listing_days=request.min_listing_days,
            limit=request.universe_limit,
            sample_seed=request.universe_sample_seed,
            stratify=request.universe_stratify,
        )
        symbols = [item["symbol"] for item in members]
    if not symbols:
        raise HTTPException(status_code=400, detail="没有可刷新的 PIT 标的")
    try:
        report = await QuantPitEnrichmentRefresher().run(QuantPitRefreshConfig(
            symbols=symbols,
            start_date=request.start_date,
            end_date=request.end_date,
            concurrency=request.concurrency,
            include_fundamental=request.include_fundamental,
            include_performance=request.include_performance,
            include_announcements=request.include_announcements,
                include_industry=request.include_industry,
                include_financial_quality=request.include_financial_quality,
                include_consensus=request.include_consensus,
        ))
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = _experiment_location("quant_pit_enrichment", stamp).root
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "refresh_report.json"
        path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "success": not report.errors,
            "report": report.to_dict(),
            "report_path": str(path),
        }
    except Exception as exc:
        logger.error("PIT 丰富特征刷新失败: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/quant/feature-audit")
async def quant_feature_audit(request: QuantFeatureAuditRequest):
    from src.core.quant_feature_audit import FeatureAuditConfig, QuantFeatureAuditor

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = _experiment_location("quant_feature_audit", stamp).root
    try:
        report = await asyncio.to_thread(
            QuantFeatureAuditor().run,
            FeatureAuditConfig(**request.model_dump()),
            output_dir,
        )
        return {"success": True, "report": report, "report_path": report.get("report_path")}
    except Exception as exc:
        logger.error("Quant 特征质量审计失败: %s", exc, exc_info=True)
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/quant/walk-forward")
async def quant_walk_forward(request: QuantWalkForwardRequest):
    from src.core.quant_walk_forward import QuantWalkForwardEvaluator, WalkForwardConfig

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = _experiment_location("quant_walk_forward", stamp).root
    try:
        report = await asyncio.to_thread(
            QuantWalkForwardEvaluator().run,
            WalkForwardConfig(**request.model_dump()),
            output_dir,
        )
        return {"success": True, "report": report.to_dict(), "report_path": report.artifact_paths.get("report")}
    except Exception as exc:
        logger.error("Walk-forward 失败: %s", exc, exc_info=True)
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/quant/two-stage")
async def quant_two_stage(request: QuantTwoStageRequest):
    from scripts.run_quant_two_stage import run

    config_path = _safe_project_path(request.config_path)
    if not config_path or not config_path.is_file() or config_path.suffix.lower() != ".json":
        raise HTTPException(status_code=400, detail="两阶段 Quant 配置不存在或不是 JSON")
    try:
        report = await asyncio.to_thread(
            run,
            config_path,
            request.experiment_id or None,
        )
        return {
            "success": True,
            "report": report,
            "report_path": report.get("report_path"),
        }
    except Exception as exc:
        logger.error("两阶段 Quant 失败: %s", exc, exc_info=True)
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/quant/train-aggregator")
async def quant_train_aggregator(request: LearnedAggregatorRequest):
    from src.core.learned_aggregator import LearnedAggregatorTrainer

    timeframe = {"5d": "短期(1周)", "20d": "中期(1月)", "60d": "长期(1季)"}[request.horizon]
    weights = weight_mgr.get_weights(timeframe).agent_weights
    slug_by_agent = {
        "近期股价分析师": "technical",
        "最新新闻分析师": "news",
        "公司前景分析师": "fundamental",
        "国际形势分析师": "macro",
        "行业对比分析师": "industry",
    }
    prior = {slug_by_agent[name]: value for name, value in weights.items() if name in slug_by_agent}
    try:
        artifact = await asyncio.to_thread(
            LearnedAggregatorTrainer().run,
            market=request.market,
            horizon=request.horizon,
            prior_weights=prior,
            min_samples=request.min_samples,
            min_unique_dates=request.min_unique_dates,
            purge_days=request.purge_days,
            lockbox_days=request.lockbox_days,
            min_brier_delta=request.min_brier_delta,
            min_folds=request.min_folds,
            activate_if_passed=request.activate_if_passed,
        )
        return {"success": True, "artifact": artifact.to_dict()}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/quant/portfolio-backtest")
async def quant_portfolio_backtest(request: PortfolioBacktestRequest):
    from src.core.portfolio_backtester import PortfolioBacktestConfig, PortfolioBacktester

    safe_paths = []
    for value in request.prediction_paths:
        path = _safe_project_path(value)
        if not path or not path.exists():
            raise HTTPException(status_code=400, detail=f"OOF 文件不存在: {value}")
        safe_paths.append(str(path))
    if not safe_paths:
        raise HTTPException(status_code=400, detail="至少需要一个 OOF prediction JSONL")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = _experiment_location("portfolio_backtest", stamp).root
    try:
        payload = request.model_dump()
        payload["prediction_paths"] = safe_paths
        report = await asyncio.to_thread(
            PortfolioBacktester().run,
            PortfolioBacktestConfig(**payload),
            output_dir,
        )
        return {"success": True, "report": report.to_dict(), "report_path": report.output_path}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/improvement/report")
async def improvement_report(path: str):
    """读取项目内调优报告 JSON，并尽量转换成前端可渲染的数据结构。"""
    report_path = _safe_project_path(path)
    if not report_path or not report_path.exists():
        raise HTTPException(status_code=404, detail="报告文件不存在")
    if report_path.suffix.lower() != ".json":
        raise HTTPException(status_code=400, detail="仅支持读取 JSON 报告")

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    if (
        report_path.name == "candidate_sandbox_report.json"
        and "technical_prompt_loop" in str(report_path)
    ):
        run_dir = report_path.parents[1]
        training_json = run_dir / "technical_training_report.json"
        training_payload = _safe_json_summary(training_json)
        return {
            "success": True,
            "kind": "technical_prompt_loop",
            "output_dir": str(run_dir),
            "candidate_root": payload.get("candidate_root", ""),
            "training_report": _compact_training_report(training_payload),
            "generated_evaluation": {
                "json": str(run_dir / "technical_training_evaluation.json"),
            },
            "report": payload,
            "report_paths": {
                "training_json": str(training_json) if training_json.exists() else "",
                "training_markdown": str(run_dir / "technical_training_report.md"),
                "json": str(report_path),
                "markdown": str(report_path.with_suffix(".md")),
            },
        }
    return {
        "success": True,
        "kind": "json_report",
        "path": str(report_path),
        "report": payload,
    }


@app.post("/api/improvement/evaluate")
async def improvement_evaluate(request: ImprovementEvaluateRequest):
    """从已验证 PredictionStore 样本生成历史评估报告。"""
    try:
        prediction_ids = [pid.strip() for pid in request.prediction_ids if pid.strip()]
        limit = request.limit
        if prediction_ids:
            limit = max(limit, len(prediction_ids) * 10)
        report, written = _build_historical_evaluation(
            min_samples=request.min_samples,
            limit=limit,
            prediction_ids=prediction_ids or None,
            target=request.target,
            timeframe=request.timeframe,
            start_date=_start_of_day_filter(request.start_date),
            end_date=_end_of_day_filter(request.end_date),
        )
        return {
            "success": True,
            "paths": written,
            "report": report,
            "selection": {
                "prediction_ids": prediction_ids,
                "selected_predictions": len(prediction_ids),
                "agent_sample_limit": limit,
                "filters": {
                    "target": request.target,
                    "timeframe": request.timeframe,
                    "start_date": request.start_date,
                    "end_date": request.end_date,
                },
            },
        }
    except Exception as e:
        logger.error(f"历史评估生成失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/improvement/passive-samples")
async def improvement_passive_samples(
    limit: int = 200,
    target: Optional[str] = None,
    timeframe: Optional[str] = None,
    verified: str = "all",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
):
    """列出可用于被动历史结果调优的预测样本池。"""
    try:
        store = PredictionStore()
        verified_filter: Optional[bool]
        if verified == "verified":
            verified_filter = True
        elif verified == "unverified":
            verified_filter = False
        else:
            verified_filter = None
        safe_limit = max(1, min(int(limit or 200), 1000))
        records = store.get_predictions(
            target=target or None,
            timeframe=timeframe or None,
            start_date=_start_of_day_filter(start_date),
            end_date=_end_of_day_filter(end_date),
            verified=verified_filter,
            limit=safe_limit,
        )
        samples = [_prediction_sample_item(record) for record in records]
        return {
            "success": True,
            "summary": _prediction_pool_overview(store),
            "filters": {
                "target": target,
                "timeframe": timeframe,
                "verified": verified,
                "start_date": start_date,
                "end_date": end_date,
                "limit": safe_limit,
            },
            "samples": samples,
            "parameter_help": _passive_parameter_help(),
        }
    except Exception as e:
        logger.error(f"被动调优样本池读取失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/improvement/run")
async def improvement_run(request: ImprovementRunRequest):
    """运行 Agent 改进工程师。"""
    try:
        return await _run_improvement_engineer(request)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Agent 改进工程师运行失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/improvement/candidate-sandbox")
async def improvement_candidate_sandbox(request: CandidateSandboxRequest):
    """生成候选 prompt/skill，并在隔离沙箱里做自动验证。"""
    try:
        return await _run_candidate_sandbox(request)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"候选验证沙箱运行失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/improvement/technical-prompt-loop")
async def improvement_technical_prompt_loop(request: TechnicalPromptLoopRequest):
    """从主动技术面历史样本开始，执行 LLM prompt 候选生成和 replay 验证闭环。"""
    try:
        return await _run_technical_prompt_loop(request)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"技术面 LLM 调优闭环失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/improvement/self-bootstrap")
async def improvement_self_bootstrap(request: SelfImprovementLabRequest):
    """主动构造真实历史样本并驱动 Agent 调优。"""
    try:
        return await _run_self_improvement_lab(request)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"主动历史样本实验室运行失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/skills/registry")
async def skill_registry(
    agent_name: Optional[str] = None,
    skill_type: Optional[str] = None,
    enabled: Optional[bool] = None,
):
    """读取 Agent Skill Registry。"""
    try:
        return _load_skill_registry_response(
            agent_name=agent_name,
            skill_type=skill_type,
            enabled=enabled,
        )
    except Exception as e:
        logger.error(f"Skill Registry 读取失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/skills/registry/{skill_id}")
async def update_skill_registry_item(skill_id: str, request: SkillToggleRequest):
    """启用或禁用单条声明式 skill。"""
    try:
        registry = AgentSkillRegistry()
        skill = registry.set_enabled(skill_id, request.enabled)
        registry.save()
        return {
            "success": True,
            "skill": _skill_to_api_dict(skill),
            "summary": registry.summary(),
            "registry": _registry_file_info(registry.path),
        }
    except KeyError:
        raise HTTPException(status_code=404, detail="Skill 不存在")
    except Exception as e:
        logger.error(f"Skill Registry 更新失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/analyze", response_model=AnalysisResponse)
async def analyze(request: AnalyzeRequest):
    """运行分析

    输入股票代码或公司名称，返回所有 Agent 的分析结果和汇总报告。
    """
    if not request.target or not request.target.strip():
        raise HTTPException(status_code=400, detail="请输入股票代码或公司名称")

    try:
        result = await _run_analysis(
            target=request.target.strip(),
            timeframe=request.timeframe,
            market=request.market,
            skip_agents=request.skip_agents,
        )
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"分析异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"分析过程异常: {str(e)}")


async def _run_analysis_job(
    job_id: str,
    target: str,
    timeframe: str,
    skip_agents: list[str],
    market: Optional[str] = None,
) -> None:
    _update_job(job_id, status="running", progress=2, message="任务已启动")
    try:
        result = await _run_analysis(
            target=target,
            timeframe=timeframe,
            market=market,
            skip_agents=skip_agents,
            progress_callback=lambda progress, message: _update_job(
                job_id,
                progress=progress,
                message=message,
            ),
        )
    except asyncio.CancelledError:
        _update_job(job_id, status="cancelled", message="任务已取消")
        raise
    except Exception as e:
        logger.error(f"异步分析任务失败: {e}", exc_info=True)
        _update_job(job_id, status="failed", message="分析失败", error=str(e))
    else:
        _update_job(
            job_id,
            status="completed",
            progress=100,
            message="分析完成",
            result=result,
        )


@app.post("/api/analyze/async", response_model=AnalysisJobResponse)
async def analyze_async(request: AnalyzeRequest):
    """创建异步分析任务，前端可通过 /api/jobs/{job_id} 轮询进度。"""
    target = request.target.strip() if request.target else ""
    if not target:
        raise HTTPException(status_code=400, detail="请输入股票代码或公司名称")

    job_id = uuid.uuid4().hex
    request_payload = request.model_dump()
    persisted = job_store.create(
        job_id=job_id,
        kind="analysis",
        request=request_payload,
    )
    analysis_jobs[job_id] = {**persisted, "task": None}
    task = asyncio.create_task(
        _run_analysis_job(
            job_id,
            target,
            request.timeframe,
            request.skip_agents,
            request.market,
        )
    )
    analysis_jobs[job_id]["task"] = task
    return _job_view(analysis_jobs[job_id])


@app.get("/api/jobs/{job_id}", response_model=AnalysisJobResponse)
async def get_job(job_id: str):
    job = analysis_jobs.get(job_id)
    if not job:
        persisted = job_store.get(job_id)
        if persisted:
            job = {**persisted, "task": None}
            analysis_jobs[job_id] = job
    if not job:
        raise HTTPException(status_code=404, detail="分析任务不存在")
    return _job_view(job)


@app.delete("/api/jobs/{job_id}", response_model=AnalysisJobResponse)
async def cancel_job(job_id: str):
    job = analysis_jobs.get(job_id)
    if not job:
        persisted = job_store.get(job_id)
        if persisted:
            job = {**persisted, "task": None}
            analysis_jobs[job_id] = job
    if not job:
        raise HTTPException(status_code=404, detail="分析任务不存在")
    if job["status"] in TERMINAL_JOB_STATUSES:
        return _job_view(job)

    task = job.get("task")
    if task and not task.done():
        task.cancel()
        _update_job(job_id, status="cancelling", message="正在取消任务")
    else:
        _update_job(job_id, status="cancelled", message="任务已取消")
    return _job_view(job)


@app.get("/api/history")
async def get_history(limit: int = 20, target: str = None):
    """获取历史预测列表"""
    try:
        from src.data.prediction_store import PredictionStore
        store = PredictionStore()
        safe_limit = max(1, min(int(limit or 20), 200))
        records = store.get_prediction_summaries(target=target, limit=safe_limit)
        history = []
        for r in records:
            history.append({
                "id": r.get("id"),
                "target": r.get("target"),
                "target_name": r.get("target_name") or "",
                "timeframe": r.get("timeframe"),
                "direction": r.get("direction"),
                "confidence": r.get("confidence"),
                "expected_excess_return_pct": r.get("expected_excess_return_pct"),
                "prob_up": r.get("prob_up"),
                "prob_down": r.get("prob_down"),
                "prob_no_edge": r.get("prob_no_edge"),
                "edge_score": r.get("edge_score"),
                "decision": r.get("decision") or "observe",
                "no_trade_reason": r.get("no_trade_reason") or "",
                "neutral_reason": r.get("neutral_reason") or "",
                "predicted_at": r.get("predicted_at"),
                "verified": r.get("verified_at") is not None,
                "actual_effective_return_pct": r.get("actual_effective_return_pct"),
                "brier_score": r.get("brier_score"),
                "edge_hit": r.get("edge_hit"),
            })
        return {"history": history, "total": len(history)}
    except Exception as e:
        logger.warning(f"获取历史失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取历史失败: {e}")


@app.get("/api/history/{prediction_id}")
async def get_history_detail(prediction_id: str):
    """获取单个预测详情"""
    try:
        from src.data.prediction_store import PredictionStore
        store = PredictionStore()
        record = store.get_prediction(prediction_id)
        if not record:
            raise HTTPException(status_code=404, detail="预测记录不存在")
        report_json = None
        if record.report_json:
            try:
                report_json = json.loads(record.report_json)
            except json.JSONDecodeError:
                report_json = record.report_json
        return {
            "id": record.id,
            "target": record.target,
            "target_name": record.target_name,
            "timeframe": record.timeframe,
            "direction": record.direction,
            "min_pct": record.min_pct,
            "max_pct": record.max_pct,
            "confidence": record.confidence,
            **_prediction_edge_v2_from_record(record),
            "prediction_target": _prediction_target_v2_from_record(record),
            "predicted_at": record.predicted_at,
            "valid_until": record.valid_until,
            "verified": record.verified_at is not None,
            "actual_change_pct": record.actual_change_pct,
            **_prediction_verification_v2_from_record(record),
            "direction_correct": record.direction_correct,
            "magnitude_hit": record.magnitude_hit,
            "verified_at": record.verified_at,
            "agents_used": record.agents_used,
            "agents_failed": record.agents_failed,
            "elapsed_seconds": record.elapsed_seconds,
            "llm_model": record.llm_model,
            "summary": record.summary,
            "report_json": report_json,
            "report_md": record.report_md,
            "disclaimer": DISCLAIMER,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取预测详情失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/history/{prediction_id}/tracking")
async def get_history_tracking(prediction_id: str):
    """刷新单条历史预测从预测日至今的真实走势对比。"""
    try:
        store = PredictionStore()
        record = store.get_prediction(prediction_id)
        if not record:
            raise HTTPException(status_code=404, detail="预测记录不存在")
        return await _build_prediction_tracking(record)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"刷新预测走势失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ================================================================
# 静态文件服务（前端）
# ================================================================

_evidence_maintenance_task: Optional[asyncio.Task] = None


async def _evidence_maintenance_loop() -> None:
    """Verify expired predictions periodically while the API service is running."""
    from src.core.evidence_maintenance import EvidenceMaintenanceConfig, EvidenceMaintenanceRunner

    initial_delay = max(1, int(os.getenv("EVIDENCE_MAINTENANCE_INITIAL_DELAY_SECONDS", "300")))
    interval = max(300, int(os.getenv("EVIDENCE_MAINTENANCE_INTERVAL_SECONDS", "21600")))
    collect = os.getenv("AUTO_SNAPSHOT_COLLECTION", "false").lower() in {"1", "true", "yes"}
    raw_targets = os.getenv("AUTO_SNAPSHOT_TARGETS", "")
    targets = [value.strip() for value in raw_targets.split(",") if value.strip()]
    await asyncio.sleep(initial_delay)
    while True:
        try:
            report = await EvidenceMaintenanceRunner().run_once(EvidenceMaintenanceConfig(
                collect_snapshots=collect,
                targets=targets,
            ))
            logger.info(
                "证据维护完成: verified=%s collected=%s errors=%s",
                report.verified_count,
                (report.collection or {}).get("saved_count", 0),
                len(report.errors),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("证据维护任务失败: %s", exc)
        await asyncio.sleep(interval)


@app.on_event("startup")
async def start_evidence_maintenance() -> None:
    global _evidence_maintenance_task
    enabled = os.getenv("EVIDENCE_MAINTENANCE_ENABLED", "true").lower() not in {"0", "false", "no"}
    if enabled and _evidence_maintenance_task is None:
        _evidence_maintenance_task = asyncio.create_task(_evidence_maintenance_loop())

    for recovered in job_store.recover_interrupted():
        request = recovered.get("request") or {}
        job_id = recovered["job_id"]
        analysis_jobs[job_id] = {**recovered, "task": None}
        if recovered.get("kind") != "analysis" or not request.get("target"):
            _update_job(
                job_id,
                status="failed",
                progress=100,
                message="无法恢复未知任务类型",
                error="持久化请求不完整",
            )
            continue
        task = asyncio.create_task(
            _run_analysis_job(
                job_id,
                request["target"],
                request.get("timeframe", "短期(1周)"),
                request.get("skip_agents") or [],
                request.get("market"),
            )
        )
        analysis_jobs[job_id]["task"] = task


@app.on_event("shutdown")
async def stop_evidence_maintenance() -> None:
    global _evidence_maintenance_task
    if _evidence_maintenance_task is not None:
        _evidence_maintenance_task.cancel()
        try:
            await _evidence_maintenance_task
        except asyncio.CancelledError:
            pass
        _evidence_maintenance_task = None

frontend_dir = project_root / "frontend"
if frontend_dir.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")


@app.get("/")
async def serve_frontend():
    """首页 — 返回前端页面"""
    index_path = frontend_dir / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {"message": "Market Prediction API", "docs": "/docs"}


# ================================================================
# 启动入口
# ================================================================

if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="Market Prediction Web API Server")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=8080, help="监听端口")
    parser.add_argument("--reload", action="store_true", help="开发模式自动重载")
    args = parser.parse_args()

    logger.info(f"Market Prediction API 启动: http://{args.host}:{args.port}")
    logger.info(f"   API 文档: http://{args.host}:{args.port}/docs")
    logger.info(f"   前端页面: http://{args.host}:{args.port}/")

    uvicorn.run(
        "api_server:app" if args.reload else app,
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )
