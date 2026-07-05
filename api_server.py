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
import uuid
from pathlib import Path
from typing import Any, Callable, Optional
from datetime import datetime

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
from src.core.llm_client import create_llm_client
from src.core.orchestrator import Orchestrator
from src.agents.technical_analyst import TechnicalAnalyst
from src.agents.news_analyst import NewsAnalyst
from src.agents.fundamental_analyst import FundamentalAnalyst
from src.agents.macro_analyst import MacroAnalyst
from src.agents.industry_analyst import IndustryAnalyst
from src.agents.aggregator import Aggregator
from config.settings import get_settings
from config.weight_manager import WeightManager
from src.data.symbol_resolver import resolve_symbol, SymbolInfo

# ================================================================
# 初始化
# ================================================================

settings = get_settings()
setup_logging(log_level="INFO", log_dir=settings.logs_dir)
logger = get_logger("api")

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
    llm = create_llm_client()
    logger.info(f"LLM 初始化成功: {settings.LLM_MODEL}")
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

# 简单内存任务表，适合本地单进程开发运行；生产部署应换成持久化队列/任务系统。
analysis_jobs: dict[str, dict[str, Any]] = {}
TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled"}

# ================================================================
# 数据模型
# ================================================================


class AnalyzeRequest(BaseModel):
    target: str = Field(..., description="股票代码或公司名称", json_schema_extra={"example": "0700"})
    timeframe: str = Field(default="短期(1周)", description="预测周期")
    skip_agents: list[str] = Field(default=[], description="跳过的 Agent 列表")


class AgentResult(BaseModel):
    agent_name: str
    direction: str
    magnitude: Optional[dict]
    confidence: float
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


class AgentInfo(BaseModel):
    name: str
    description: str
    weight_short: float
    weight_mid: float
    weight_long: float


# ================================================================
# 辅助函数
# ================================================================


def _identify_market(target: str) -> str:
    """识别市场"""
    target = target.strip().upper().replace(".HK", "").replace(".SZ", "").replace(".SS", "")
    if target.isdigit():
        return "HK" if len(target) <= 5 else "A"
    return "US"


def _build_orchestrator(skip_agents: list[str] = None) -> tuple[Orchestrator, list[str]]:
    """构建 Orchestrator 并注册 Agent"""
    orchestrator = Orchestrator()
    skip = set(skip_agents or [])
    active_names = []

    if "tech" not in skip:
        orchestrator.register(TechnicalAnalyst(llm))
        active_names.append(AGENT_NAMES["tech"])

    if "news" not in skip:
        orchestrator.register(NewsAnalyst(llm))
        active_names.append(AGENT_NAMES["news"])

    if "fundamental" not in skip:
        orchestrator.register(FundamentalAnalyst(llm))
        active_names.append(AGENT_NAMES["fundamental"])

    if "macro" not in skip:
        orchestrator.register(MacroAnalyst(llm))
        active_names.append(AGENT_NAMES["macro"])

    if "industry" not in skip:
        orchestrator.register(IndustryAnalyst(llm))
        active_names.append(AGENT_NAMES["industry"])

    return orchestrator, active_names


def _resolve_target(target: str) -> str:
    """将中文股名解析为股票代码"""
    return resolve_symbol(target).symbol


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
    failure_markers = ["异常", "失败", "超时", "不可用", "格式异常", "LLM 返回格式异常", "降级", "fallback"]
    status = "ok"
    reason = "完成"

    if explicit_status in {"failed", "degraded"}:
        status = explicit_status
        reason = getattr(result, "error_message", None) or data_summary.get("error") or "Agent 标记为降级或失败"
    elif result.confidence <= 0:
        status = "degraded"
        reason = "置信度为 0，可能是数据或 LLM 调用降级结果"
    elif any(marker in text for marker in failure_markers):
        status = "degraded"
        reason = "结果中包含异常、失败或降级提示"

    if status != "ok":
        for candidate in list(result.risks or []) + [result.reasoning]:
            if candidate:
                reason = str(candidate)[:180]
                break

    return {
        "agent_name": result.agent_name,
        "status": status,
        "reason": reason,
    }


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
    return {k: v for k, v in job.items() if k != "task"}


def _update_job(job_id: str, **fields) -> dict[str, Any]:
    job = analysis_jobs[job_id]
    job.update(fields)
    job["updated_at"] = datetime.now().isoformat()
    return job


async def _run_analysis(
    target: str,
    timeframe: str,
    skip_agents: list[str] = None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> dict:
    """执行完整分析并返回结构化结果"""
    if not llm:
        raise RuntimeError("LLM 未初始化，请检查 .env 配置")

    def progress(value: int, message: str) -> None:
        if progress_callback:
            progress_callback(value, message)

    progress(5, "解析标的")

    # 解析中文股名 → 代码
    target_info = resolve_symbol(target)
    resolved_target = target_info.symbol
    target_label = target_info.display_name
    if resolved_target != target:
        logger.info(f"股名解析: {target} → {target_label} [{target_info.market}]")

    start_time = time.monotonic()

    # 构建 Orchestrator
    progress(12, "构建 Agent 团队")
    orchestrator, active_names = _build_orchestrator(skip_agents)

    # 并行执行 Agent
    progress(20, "并行执行 Agent 分析")
    agent_results = await orchestrator.run_selected(
        resolved_target, timeframe, agent_names=active_names,
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
    failed_agents = [s for s in agent_statuses if s["status"] != "ok"]
    data_quality_summary = [_data_quality_from_result(r) for r in agent_results]

    progress(65, "计算权重并处理 Agent 状态")
    weight_config = weight_mgr.redistribute_weights(
        timeframe, succeeded_names, failed_names,
    )

    # 汇总
    progress(75, "汇总分析师综合研判")
    aggregator = Aggregator(llm)
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
            agents_failed=[item["agent_name"] for item in failed_agents],
            elapsed_seconds=elapsed,
            llm_model=settings.LLM_MODEL,
            target_name=target_info.name or (target if target != target_info.symbol else ""),
        )
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


@app.get("/api/health")
async def health_check():
    """健康检查"""
    return {
        "status": "ok",
        "llm_ready": llm is not None,
        "model": settings.LLM_MODEL,
        "active_jobs": sum(1 for job in analysis_jobs.values() if job["status"] not in TERMINAL_JOB_STATUSES),
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
            skip_agents=request.skip_agents,
        )
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"分析异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"分析过程异常: {str(e)}")


async def _run_analysis_job(job_id: str, target: str, timeframe: str, skip_agents: list[str]) -> None:
    _update_job(job_id, status="running", progress=2, message="任务已启动")
    try:
        result = await _run_analysis(
            target=target,
            timeframe=timeframe,
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
    now = datetime.now().isoformat()
    analysis_jobs[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "progress": 0,
        "message": "等待执行",
        "created_at": now,
        "updated_at": now,
        "result": None,
        "error": None,
        "task": None,
    }
    task = asyncio.create_task(
        _run_analysis_job(job_id, target, request.timeframe, request.skip_agents)
    )
    analysis_jobs[job_id]["task"] = task
    return _job_view(analysis_jobs[job_id])


@app.get("/api/jobs/{job_id}", response_model=AnalysisJobResponse)
async def get_job(job_id: str):
    job = analysis_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="分析任务不存在")
    return _job_view(job)


@app.delete("/api/jobs/{job_id}", response_model=AnalysisJobResponse)
async def cancel_job(job_id: str):
    job = analysis_jobs.get(job_id)
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
        records = store.get_predictions(target=target, limit=limit)
        history = []
        for r in records:
            history.append({
                "id": r.id,
                "target": r.target,
                "timeframe": r.timeframe,
                "direction": r.direction,
                "confidence": r.confidence,
                "predicted_at": r.predicted_at,
                "verified": r.verified_at is not None,
            })
        return {"history": history, "total": len(history)}
    except Exception as e:
        logger.warning(f"获取历史失败: {e}")
        return {"history": [], "total": 0}


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
            "predicted_at": record.predicted_at,
            "valid_until": record.valid_until,
            "verified": record.verified_at is not None,
            "actual_change_pct": record.actual_change_pct,
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


# ================================================================
# 静态文件服务（前端）
# ================================================================

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
