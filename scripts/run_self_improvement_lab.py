#!/usr/bin/env python3
"""
运行 Agent 自我改进历史样本实验室。

示例:
    python scripts/run_self_improvement_lab.py --targets 000001,600519,AAPL --start 2025-01-01 --end 2025-06-30
    python scripts/run_self_improvement_lab.py --targets AAPL,MSFT --start 2025-01-01 --end 2025-12-31 --run-engineer --dry-run
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

load_dotenv(project_root / ".env")

from src.core.self_improvement_lab import SelfImprovementLab, SelfImprovementLabConfig


def _parse_targets(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


async def main():
    parser = argparse.ArgumentParser(description="主动构造真实历史样本并驱动 Agent 调优")
    parser.add_argument("--targets", required=True, help="逗号分隔的标的代码，如 000001,600519,AAPL")
    parser.add_argument("--start", required=True, help="历史样本开始日期 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="历史样本结束日期 YYYY-MM-DD")
    parser.add_argument("--timeframe", default="短期(1周)", help="预测周期")
    parser.add_argument("--interval-days", type=int, default=14, help="抽样间隔天数")
    parser.add_argument("--lookback-days", type=int, default=180, help="每个样本回看 K 线天数")
    parser.add_argument("--tolerance-days", type=int, default=10, help="寻找验证收盘价容忍天数")
    parser.add_argument("--evaluation-min-samples", type=int, default=5, help="生成评估信号的最小样本数")
    parser.add_argument("--run-engineer", action="store_true", help="评估后运行 Agent 改进工程师")
    parser.add_argument("--engineer-min-samples", type=int, default=20, help="自动修改最小样本数")
    parser.add_argument("--engineer-min-unique-cases", type=int, default=5, help="自动修改最小独立历史案例数")
    parser.add_argument("--dry-run", action="store_true", help="改进工程师只演练，不写 prompt/skill")
    parser.add_argument("--apply", action="store_true", help="允许改进工程师实际写 prompt/skill")
    parser.add_argument("--news-snapshots", type=Path, help="可选新闻快照 JSON/JSONL 文件")
    parser.add_argument("--point-in-time-snapshots", type=Path, help="可选基本面/行业/宏观 point-in-time 快照目录、JSON 或 JSONL")
    parser.add_argument("--output-dir", type=Path, help="输出目录")
    args = parser.parse_args()

    report = await SelfImprovementLab().run(
        SelfImprovementLabConfig(
            targets=_parse_targets(args.targets),
            start_date=args.start,
            end_date=args.end,
            timeframe=args.timeframe,
            interval_days=args.interval_days,
            lookback_days=args.lookback_days,
            tolerance_days=args.tolerance_days,
            evaluation_min_samples=args.evaluation_min_samples,
            run_engineer=args.run_engineer,
            engineer_min_samples=args.engineer_min_samples,
            engineer_min_unique_cases=args.engineer_min_unique_cases,
            dry_run=args.dry_run or not args.apply,
            output_dir=args.output_dir,
            news_snapshots_path=args.news_snapshots,
            point_in_time_snapshots_path=args.point_in_time_snapshots,
        )
    )

    print(f"主动历史样本实验室完成: {report.output_dir}")
    print(json.dumps({
        "total_samples": report.total_samples,
        "supported_agents": report.supported_agents,
        "deferred_agents": [item["agent_name"] for item in report.deferred_agents],
        "evaluation": report.evaluation_paths,
        "engineer": report.engineer_paths,
    }, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
