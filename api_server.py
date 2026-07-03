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
from pathlib import Path
from typing import Optional
from datetime import datetime

# 确保项目根目录在 path 中
project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from src.utils.logger import setup_logging, get_logger
from src.core.llm_client import create_llm_client
from src.core.orchestrator import Orchestrator
from src.core.result import FinalReport
from src.agents.technical_analyst import TechnicalAnalyst
from src.agents.news_analyst import NewsAnalyst
from src.agents.fundamental_analyst import FundamentalAnalyst
from src.agents.macro_analyst import MacroAnalyst
from src.agents.industry_analyst import IndustryAnalyst
from src.agents.aggregator import Aggregator
from config.settings import get_settings
from config.weight_manager import WeightManager

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

# ================================================================
# 数据模型
# ================================================================


class AnalyzeRequest(BaseModel):
    target: str = Field(..., description="股票代码或公司名称", example="0700")
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


class AnalysisResponse(BaseModel):
    success: bool
    target: str
    timeframe: str
    elapsed_seconds: float
    agent_results: list[AgentResult]
    final_report: dict
    prediction_id: Optional[str] = None


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
    HK_NAME_TO_CODE = {
        "美团": "3690", "美团-W": "3690",
        "腾讯": "0700", "腾讯控股": "0700",
        "阿里巴巴": "9988", "阿里": "9988",
        "百度": "9888",
        "京东": "9618",
        "小米": "1810", "小米集团": "1810",
        "快手": "1024",
        "网易": "9999",
        "哔哩哔哩": "9626", "B站": "9626",
        "拼多多": "PDD",
        "商汤": "00020",
        "海底捞": "6862",
        "安踏": "2020",
        "李宁": "2331",
        "华润啤酒": "00291",
        "青岛啤酒": "00168",
        "中芯国际": "00981",
        "药明生物": "2269",
        "信达生物": "1801",
        "百济神州": "6160",
        "君实生物": "1877",
    }
    s = target.strip().upper()
    # 去掉 HK 后缀再查
    s_no_suffix = s.replace(".HK", "").replace(".SZ", "").replace(".SS", "")
    if s in HK_NAME_TO_CODE:
        return HK_NAME_TO_CODE[s]
    if s_no_suffix in HK_NAME_TO_CODE:
        return HK_NAME_TO_CODE[s_no_suffix]
    return target


async def _run_analysis(target: str, timeframe: str, skip_agents: list[str] = None) -> dict:
    """执行完整分析并返回结构化结果"""
    if not llm:
        raise RuntimeError("LLM 未初始化，请检查 .env 配置")

    # 解析中文股名 → 代码
    resolved_target = _resolve_target(target)
    if resolved_target != target:
        logger.info(f"股名解析: {target} → {resolved_target}")

    start_time = time.monotonic()

    # 构建 Orchestrator
    orchestrator, active_names = _build_orchestrator(skip_agents)

    # 并行执行 Agent
    agent_results = await orchestrator.run_selected(
        resolved_target, timeframe, agent_names=active_names,
    )

    if not agent_results:
        raise RuntimeError("所有 Agent 均失败，无法生成报告")

    # 权重计算
    succeeded_names = {r.agent_name for r in agent_results}
    failed_names = [n for n in active_names if n not in succeeded_names]
    weight_config = weight_mgr.redistribute_weights(
        timeframe, succeeded_names, failed_names,
    )

    # 汇总
    aggregator = Aggregator(llm)
    report = await aggregator.aggregate(
        resolved_target, timeframe, agent_results,
        weight_config=weight_config,
        failed_agents=failed_names if failed_names else None,
    )

    elapsed = time.monotonic() - start_time

    # 保存到 PredictionStore
    prediction_id = None
    try:
        from src.data.prediction_store import PredictionStore
        store = PredictionStore()
        prediction_id = store.save_prediction(
            target=target,
            timeframe=timeframe,
            report=report,
            agent_results=agent_results,
            agents_used=list(succeeded_names),
            agents_failed=failed_names,
            elapsed_seconds=elapsed,
            llm_model=settings.LLM_MODEL,
        )
    except Exception as e:
        logger.warning(f"预测记录失败: {e}")

    # 构建响应
    return {
        "success": True,
        "target": target,
        "timeframe": timeframe,
        "elapsed_seconds": round(elapsed, 1),
        "agent_results": [
            {
                "agent_name": r.agent_name,
                "direction": r.direction.value,
                "magnitude": {"min_pct": r.magnitude.min_pct, "max_pct": r.magnitude.max_pct} if r.magnitude else None,
                "confidence": r.confidence,
                "reasoning": r.reasoning,
                "key_factors": r.key_factors,
                "risks": r.risks,
                "data_summary": r.data_summary,
            }
            for r in agent_results
        ],
        "final_report": report.to_dict(),
        "prediction_id": prediction_id,
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
        return {
            "id": record.id,
            "target": record.target,
            "timeframe": record.timeframe,
            "direction": record.direction,
            "min_pct": record.min_pct,
            "max_pct": record.max_pct,
            "confidence": record.confidence,
            "predicted_at": record.predicted_at,
            "verified": record.verified_at is not None,
            "actual_change_pct": record.actual_change_pct,
            "direction_correct": record.direction_correct,
            "agents_used": record.agents_used,
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
    import uvicorn

    host = "0.0.0.0"
    port = 8080

    logger.info(f"🚀 Market Prediction API 启动: http://{host}:{port}")
    logger.info(f"   API 文档: http://{host}:{port}/docs")
    logger.info(f"   前端页面: http://{host}:{port}/")

    uvicorn.run(
        "api_server:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )
