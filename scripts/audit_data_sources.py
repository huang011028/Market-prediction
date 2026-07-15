#!/usr/bin/env python3
"""Batch audit agent data-source coverage without LLM reasoning.

Examples:
    python scripts/audit_data_sources.py --targets "京东,小米集团,AAPL"
    python scripts/audit_data_sources.py --concurrency 2 --timeout 90
    python scripts/audit_data_sources.py --agents "行业对比分析师,公司前景分析师"
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

load_dotenv(project_root / ".env")

from config.settings import get_settings
from src.core.data_source_auditor import DataSourceCoverageAuditor
from src.utils.logger import setup_logging


DEFAULT_TARGETS = [
    "000001",
    "600519",
    "300750",
    "京东",
    "小米集团",
    "网易",
    "中芯国际",
    "AAPL",
    "NVDA",
]


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="数据源覆盖率巡检: 批量调用各 agent 的 gather_data，不跑 LLM。",
    )
    parser.add_argument(
        "--targets",
        "-t",
        default=",".join(DEFAULT_TARGETS),
        help="逗号分隔标的列表，支持中文名/代码/美股ticker。",
    )
    parser.add_argument(
        "--timeframe",
        "-f",
        default="短期(1周)",
        help="预测周期，用于决定数据回溯窗口。",
    )
    parser.add_argument(
        "--agents",
        default="",
        help="逗号分隔 agent 名称；为空时巡检全部 5 个 agent。",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="并发标的数。外部数据源容易限流，默认 1。",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=75,
        help="单个 agent 数据采集超时秒数。",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="输出目录，默认 output/data_source_audits。",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args()

    settings = get_settings()
    setup_logging(log_level=args.log_level, log_dir=settings.logs_dir)

    targets = _split_csv(args.targets)
    agent_names = _split_csv(args.agents) or None
    output_dir = Path(args.output_dir) if args.output_dir else settings.output_dir / "data_source_audits"

    auditor = DataSourceCoverageAuditor(timeout_seconds=args.timeout)
    report = await auditor.audit_targets(
        targets=targets,
        timeframe=args.timeframe,
        agent_names=agent_names,
        concurrency=args.concurrency,
    )
    json_path, md_path = auditor.write_report(report, output_dir)

    print(report.to_markdown())
    print()
    print(f"JSON: {json_path}")
    print(f"Markdown: {md_path}")


if __name__ == "__main__":
    asyncio.run(main())
