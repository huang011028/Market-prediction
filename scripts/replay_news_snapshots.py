#!/usr/bin/env python3
"""
新闻快照回放 CLI

用法:
    python scripts/replay_news_snapshots.py --snapshots data/news_snapshots
    python scripts/replay_news_snapshots.py --snapshots data/news_snapshots --target 000001 --start 2026-01-01 --end 2026-06-30
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

from config.settings import get_settings
from src.core.calibration_bootstrap import NewsSnapshotCalibrationBootstrapper
from src.data.news_snapshot_archive import NewsSnapshotArchive
from src.utils.logger import setup_logging


async def main():
    parser = argparse.ArgumentParser(description="回放新闻快照并生成新闻面校准样本")
    parser.add_argument("--snapshots", type=Path, default=None, help="快照目录、JSON 或 JSONL")
    parser.add_argument("--target", "-t", help="只回放指定标的")
    parser.add_argument("--start", help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", help="结束日期 YYYY-MM-DD")
    parser.add_argument("--timeframe", "-f", default="短期(1周)")
    parser.add_argument("--tolerance-days", type=int, default=10)
    parser.add_argument("--output", type=Path, help="报告输出路径")
    parser.add_argument("--draft-dir", type=Path, help="同时生成 prompt/MCP/skill 改进草案")
    parser.add_argument("--draft-min-samples", type=int, default=None)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    settings = get_settings()
    setup_logging(log_level=args.log_level, log_dir=settings.logs_dir)

    archive = NewsSnapshotArchive()
    snapshots = archive.load_snapshots(
        args.snapshots,
        target=args.target,
        start_date=args.start,
        end_date=args.end,
    )

    report = await NewsSnapshotCalibrationBootstrapper().run_from_snapshots(
        snapshots,
        timeframe=args.timeframe,
        tolerance_days=args.tolerance_days,
    )
    print(report.summary())

    output = args.output
    if output is None:
        suffix = args.target or "all"
        output = settings.output_dir / f"news_snapshot_replay_{suffix}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"新闻快照回放报告已保存: {output}")

    if args.draft_dir:
        from src.core.improvement_patch_planner import AgentImprovementPatchPlanner

        planner = AgentImprovementPatchPlanner()
        plan = planner.build_plan_from_report(
            report.to_dict(),
            min_samples=args.draft_min_samples,
        )
        written = planner.write_plan(plan, args.draft_dir)
        print(f"改进草案已保存: {written['plan_md']}")


if __name__ == "__main__":
    asyncio.run(main())
