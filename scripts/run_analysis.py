#!/usr/bin/env python3
"""
主入口脚本 — 运行一次完整的市场分析

Phase 2: 配置驱动 + 多个 Agent + 权重系统
  - 从 agent_config.yaml 读取启用的 Agent
  - 并行调度所有 Agent
  - 汇总时传入权重配置
  - 支持 --no-news / --no-fundamental / --no-macro 跳过指定维度

用法:
    python scripts/run_analysis.py --target 000001
    python scripts/run_analysis.py --target 0700.HK -f "中期(1月)"
    python scripts/run_analysis.py --target 000001 --no-news
"""

import sys
import asyncio
import argparse
import time
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")

from src.utils.logger import setup_logging, get_logger
from src.core.llm_client import create_llm_client
from src.core.orchestrator import Orchestrator
from src.agents.technical_analyst import TechnicalAnalyst
from src.agents.news_analyst import NewsAnalyst
from src.agents.fundamental_analyst import FundamentalAnalyst
from src.agents.macro_analyst import MacroAnalyst
from src.agents.industry_analyst import IndustryAnalyst
from src.agents.aggregator import Aggregator
from src.data.symbol_resolver import resolve_symbol
from config.settings import get_settings
from config.weight_manager import WeightManager

# Agent 名称常量（与 agent_config.yaml 保持一致）
AGENT_TECH = "近期股价分析师"
AGENT_NEWS = "最新新闻分析师"
AGENT_FUND = "公司前景分析师"
AGENT_MACRO = "国际形势分析师"
AGENT_INDUSTRY = "行业对比分析师"


async def main():
    parser = argparse.ArgumentParser(
        description="Market Prediction — AI Agent 团队市场分析 (Phase 2)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s --target 000001
  %(prog)s --target 0700.HK -f "中期(1月)"
  %(prog)s --target 000001 --no-news
  %(prog)s --target 000001 --no-news --no-macro
        """,
    )
    parser.add_argument("--target", "-t", type=str, default="000001",
                        help="分析标的 (A股: 000001, 港股: 0700, 美股: AAPL)")
    parser.add_argument("--timeframe", "-f", type=str, default="短期(1周)",
                        help="预测周期")
    parser.add_argument("--log-level", "-l", type=str, default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--no-news", action="store_true", help="跳过新闻分析")
    parser.add_argument("--no-fundamental", action="store_true", help="跳过基本面分析")
    parser.add_argument("--no-macro", action="store_true", help="跳过宏观分析")
    parser.add_argument("--no-industry", action="store_true", help="跳过行业对比分析")
    args = parser.parse_args()

    # === 初始化 ===
    settings = get_settings()
    setup_logging(log_level=args.log_level, log_dir=settings.logs_dir)
    logger = get_logger("main")

    # 权重管理器
    weight_mgr = WeightManager()

    logger.info("=" * 60)
    logger.info("  Market Prediction — Phase 2 (多维度分析)")
    logger.info("=" * 60)
    target_info = resolve_symbol(args.target)
    resolved_target = target_info.symbol
    target_label = target_info.display_name

    logger.info(f"  标的: {args.target} -> {target_label} [{target_info.market}]")
    logger.info(f"  周期: {args.timeframe}")
    logger.info(f"  LLM:  {settings.LLM_MODEL}")

    # === LLM ===
    try:
        llm = create_llm_client()
    except Exception as e:
        logger.error(f"❌ LLM 初始化失败: {e}")
        sys.exit(1)

    # === 注册 Agent ===
    orchestrator = Orchestrator()
    skip_agents = set()
    if args.no_news:
        skip_agents.add(AGENT_NEWS)
    if args.no_fundamental:
        skip_agents.add(AGENT_FUND)
    if args.no_macro:
        skip_agents.add(AGENT_MACRO)
    if args.no_industry:
        skip_agents.add(AGENT_INDUSTRY)

    active_names = []

    # 技术面（始终启用）
    tech = TechnicalAnalyst(llm)
    orchestrator.register(tech)
    active_names.append(AGENT_TECH)
    logger.info(f"✅ {AGENT_TECH}")

    # 新闻
    if AGENT_NEWS not in skip_agents:
        news = NewsAnalyst(llm)
        orchestrator.register(news)
        active_names.append(AGENT_NEWS)
        logger.info(f"✅ {AGENT_NEWS}")
    else:
        logger.info(f"⏭️ {AGENT_NEWS} (跳过)")

    # 基本面
    if AGENT_FUND not in skip_agents:
        fund = FundamentalAnalyst(llm)
        orchestrator.register(fund)
        active_names.append(AGENT_FUND)
        logger.info(f"✅ {AGENT_FUND}")
    else:
        logger.info(f"⏭️ {AGENT_FUND} (跳过)")

    # 宏观
    if AGENT_MACRO not in skip_agents:
        macro = MacroAnalyst(llm)
        orchestrator.register(macro)
        active_names.append(AGENT_MACRO)
        logger.info(f"✅ {AGENT_MACRO}")
    else:
        logger.info(f"⏭️ {AGENT_MACRO} (跳过)")

    # 行业对比
    if AGENT_INDUSTRY not in skip_agents:
        industry = IndustryAnalyst(llm)
        orchestrator.register(industry)
        active_names.append(AGENT_INDUSTRY)
        logger.info(f"✅ {AGENT_INDUSTRY}")
    else:
        logger.info(f"⏭️ {AGENT_INDUSTRY} (跳过)")

    logger.info(f"\n🚀 开始分析 ({len(active_names)} 个 Agent)...\n")
    start_time = time.monotonic()

    # === Step 1: 并行执行 ===
    agent_results = await orchestrator.run_selected(
        resolved_target, args.timeframe,
        agent_names=active_names,
    )

    step1_elapsed = time.monotonic() - start_time

    # 识别失败的 Agent
    succeeded_names = {r.agent_name for r in agent_results}
    failed_names = [n for n in active_names if n not in succeeded_names]

    logger.info(f"\n📊 Step 1 完成 | 耗时 {step1_elapsed:.1f}s")
    logger.info(f"   成功: {len(agent_results)} | 失败: {len(failed_names)}")
    if failed_names:
        logger.info(f"   失败列表: {', '.join(failed_names)}")

    if not agent_results:
        logger.error("❌ 所有 Agent 均失败")
        sys.exit(1)

    # === Step 2: 权重计算 ===
    # 对失败的 Agent 进行权重再分配
    weight_config = weight_mgr.redistribute_weights(
        args.timeframe,
        succeeded_names,
        failed_names,
    )

    logger.info(f"\n⚖️ 权重配置 ({args.timeframe}):")
    for name, w in sorted(weight_config.agent_weights.items(), key=lambda x: -x[1]):
        logger.info(f"   {name}: {w:.0%}")

    # === Step 3: 汇总 ===
    aggregator = Aggregator(llm)
    logger.info(f"\n🎯 汇总分析师综合研判...")

    report = await aggregator.aggregate(
        target_label, args.timeframe,
        agent_results,
        weight_config=weight_config,
        failed_agents=failed_names if failed_names else None,
    )

    total_elapsed = time.monotonic() - start_time

    # === Step 4: 保存到 PredictionStore 🆕 Phase 3 ===
    try:
        from src.data.prediction_store import PredictionStore
        store = PredictionStore()
        pid = store.save_prediction(
            target=resolved_target,
            timeframe=args.timeframe,
            report=report,
            agent_results=agent_results,
            agents_used=list(succeeded_names),
            agents_failed=failed_names,
            elapsed_seconds=total_elapsed,
            llm_model=settings.LLM_MODEL,
            target_name=target_info.name,
        )
        for agent_result in agent_results:
            if agent_result.agent_name != AGENT_NEWS:
                continue
            snapshot_meta = (agent_result.data_summary or {}).get("news_snapshot")
            if snapshot_meta:
                try:
                    from src.data.news_snapshot_archive import NewsSnapshotArchive

                    NewsSnapshotArchive().attach_prediction_id(snapshot_meta, pid)
                except Exception as e:
                    logger.debug(f"新闻快照关联预测ID失败: {e}")
        logger.info(f"💾 预测已记录: {pid}")
    except Exception as e:
        logger.warning(f"⚠️ 预测记录失败: {e}")

    # === Step 5: 输出 ===
    print("\n")
    print("=" * 60)
    print(report.to_markdown())
    print("=" * 60)

    print(f"\n{'─' * 50}")
    print("  各 Agent 详细结果")
    print(f"{'─' * 50}")
    for r in agent_results:
        mag = r.magnitude.range_str if r.magnitude else "N/A"
        print(f"\n  [{r.agent_name}]")
        print(f"    方向: {r.direction.value} | 幅度: {mag} | 置信度: {r.confidence:.0%}")
        short = r.reasoning[:150].replace('\n', ' ')
        print(f"    推理: {short}...")

    if failed_names:
        print(f"\n  ⚠️ 失败 Agent: {', '.join(failed_names)}")

    # 保存
    output_dir = settings.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_target = args.target.replace(".", "_").replace(" ", "_")
    safe_tf = args.timeframe.replace("(", "_").replace(")", "_").replace(" ", "_")

    json_path = output_dir / f"report_{safe_target}_{safe_tf}.json"
    json_path.write_text(report.to_json(), encoding="utf-8")
    logger.info(f"\n📁 JSON: {json_path}")

    md_path = output_dir / f"report_{safe_target}_{safe_tf}.md"
    md_path.write_text(report.to_markdown(), encoding="utf-8")
    logger.info(f"📁 Markdown: {md_path}")

    logger.info(f"\n{'=' * 60}")
    logger.info(f"  ✅ 分析完成! 总耗时: {total_elapsed:.1f}s")
    logger.info(f"  方向: {report.direction.value} | 置信度: {report.confidence:.0%}")
    if report.magnitude:
        logger.info(f"  幅度: {report.magnitude.range_str}")
    logger.info(f"{'=' * 60}")


if __name__ == "__main__":
    asyncio.run(main())
