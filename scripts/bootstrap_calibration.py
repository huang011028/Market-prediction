#!/usr/bin/env python3
"""
历史校准启动 CLI

用法:
    python scripts/bootstrap_calibration.py --targets 000001,002396 --start 2025-01-01 --end 2025-12-31
    python scripts/bootstrap_calibration.py --targets 000001 --start 2025-01-01 --end 2025-12-31 --news-snapshots data/news_snapshots
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
from src.core.calibration_bootstrap import (
    CalibrationBootstrapConfig,
    NewsSnapshotCalibrationBootstrapper,
    TechnicalCalibrationBootstrapper,
)
from src.data.news_snapshot_archive import NewsSnapshotArchive
from src.utils.logger import setup_logging


async def main():
    parser = argparse.ArgumentParser(description="生成技术面/新闻面历史初始校准样本")
    parser.add_argument("--targets", "-t", default="000001", help="逗号分隔标的列表")
    parser.add_argument("--start", required=True, help="回测起始日期 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="回测结束日期 YYYY-MM-DD")
    parser.add_argument("--timeframe", "-f", default="短期(1周)")
    parser.add_argument("--interval", type=int, default=7, help="历史采样间隔天数")
    parser.add_argument("--lookback-days", type=int, default=180)
    parser.add_argument("--tolerance-days", type=int, default=10)
    parser.add_argument("--news-snapshots", type=Path, help="新闻快照 JSON/JSONL 文件或目录")
    parser.add_argument("--output", type=Path, help="报告输出路径")
    parser.add_argument("--draft-dir", type=Path, help="同时为新闻面生成 prompt/MCP/skill 改进草案")
    parser.add_argument("--draft-min-samples", type=int, default=None)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    settings = get_settings()
    setup_logging(log_level=args.log_level, log_dir=settings.logs_dir)

    targets = [target.strip() for target in args.targets.split(",") if target.strip()]
    config = CalibrationBootstrapConfig(
        targets=targets,
        start_date=args.start,
        end_date=args.end,
        timeframe=args.timeframe,
        interval_days=args.interval,
        lookback_days=args.lookback_days,
        tolerance_days=args.tolerance_days,
    )

    reports = []
    technical_report = await TechnicalCalibrationBootstrapper().run(config)
    reports.append(technical_report.to_dict())
    print(technical_report.summary())

    if args.news_snapshots:
        snapshots = NewsSnapshotArchive().load_snapshots(
            args.news_snapshots,
            start_date=args.start,
            end_date=args.end,
        )
        target_set = {target.upper() for target in targets}
        snapshots = [
            snapshot for snapshot in snapshots
            if (
                str(snapshot.get("target") or "").upper() in target_set
                or str(snapshot.get("symbol") or "").upper() in target_set
                or str((snapshot.get("news_data") or {}).get("symbol") or "").upper() in target_set
            )
        ]
        news_report = await NewsSnapshotCalibrationBootstrapper().run_from_snapshots(
            snapshots,
            timeframe=args.timeframe,
            tolerance_days=args.tolerance_days,
        )
        reports.append(news_report.to_dict())
        print(news_report.summary())

        if args.draft_dir:
            from src.core.improvement_patch_planner import AgentImprovementPatchPlanner

            planner = AgentImprovementPatchPlanner()
            plan = planner.build_plan_from_report(
                news_report.to_dict(),
                min_samples=args.draft_min_samples,
            )
            written = planner.write_plan(plan, args.draft_dir)
            print(f"新闻改进草案已保存: {written['plan_md']}")

    output = args.output
    if output is None:
        output = settings.output_dir / f"calibration_bootstrap_{args.start}_{args.end}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"reports": reports}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"校准启动报告已保存: {output}")


if __name__ == "__main__":
    asyncio.run(main())
