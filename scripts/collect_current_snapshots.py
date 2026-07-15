#!/usr/bin/env python3
"""Collect current PIT snapshots for non-technical agents.

Examples:
    python scripts/collect_current_snapshots.py --preset broad --max-snapshots 160
    python scripts/collect_current_snapshots.py --targets 000001,9618,AAPL --write-default-archives
    python scripts/collect_current_snapshots.py --preset cn_hk_us --news-mode formal --target-limit 5
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

load_dotenv(project_root / ".env")

from src.core.snapshot_collection import (
    NEWS_MODES,
    CurrentSnapshotCollector,
    SnapshotCollectionConfig,
)


TARGET_PRESETS = {
    "smoke": [
        "000001", "9618", "AAPL",
    ],
    "cn_hk_us": [
        "000001", "600519", "000333", "300750", "002594", "601318",
        "招商银行", "比亚迪", "宁德时代", "美的集团",
        "0700", "9618", "9988", "3690", "1299", "小米集团",
        "腾讯控股", "阿里巴巴", "美团",
        "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA",
        "JPM", "LLY", "AVGO",
    ],
    "broad": [
        "000001", "600519", "000333", "300750", "002594", "601318",
        "600036", "601899", "600900", "000858", "000651", "002415",
        "603259", "601888", "600276", "300760", "002230", "600030",
        "600887", "601012", "0700", "9618", "9988", "3690", "1810",
        "1299", "2318", "388", "1024", "2269", "AAPL", "MSFT",
        "NVDA", "GOOGL", "AMZN", "META", "TSLA", "JPM", "LLY", "AVGO",
        "NFLX", "AMD", "COST", "UNH", "XOM", "V", "MA", "ORCL",
    ],
}


def _parse_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


async def main():
    parser = argparse.ArgumentParser(description="批量采集当前时点 Agent 快照")
    target_group = parser.add_mutually_exclusive_group(required=False)
    target_group.add_argument("--targets", help="逗号分隔标的列表")
    target_group.add_argument(
        "--preset",
        choices=sorted(TARGET_PRESETS),
        default="smoke",
        help="内置标的池",
    )
    parser.add_argument(
        "--agents",
        default="fundamental,industry,macro,news",
        help="fundamental,industry,macro,news",
    )
    parser.add_argument("--timeframe", default="短期(1周)")
    parser.add_argument("--as-of", help="快照日期 YYYY-MM-DD，默认当前日期")
    parser.add_argument(
        "--news-mode",
        choices=sorted(NEWS_MODES),
        default="evidence",
        help="none/raw/evidence 不调用 LLM；formal 调用 NewsAnalyst+LLM",
    )
    parser.add_argument("--news-max-items", type=int, default=20)
    parser.add_argument("--target-limit", type=int, default=0, help="限制使用前 N 个标的")
    parser.add_argument("--max-snapshots", type=int, default=0, help="最多保存多少条快照")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="输出目录；未指定且未写正式库时自动使用 output/snapshot_collection/时间戳",
    )
    parser.add_argument(
        "--write-default-archives",
        action="store_true",
        help="写入正式 data/point_in_time_snapshots 与 data/news_snapshots",
    )
    args = parser.parse_args()

    targets = _parse_csv(args.targets) if args.targets else list(TARGET_PRESETS[args.preset])
    if args.target_limit > 0:
        targets = targets[: args.target_limit]

    report = await CurrentSnapshotCollector().collect(
        SnapshotCollectionConfig(
            targets=targets,
            timeframe=args.timeframe,
            agents=_parse_csv(args.agents),
            news_mode=args.news_mode,
            as_of=args.as_of,
            output_dir=args.output_dir,
            write_default_archives=args.write_default_archives,
            max_snapshots=args.max_snapshots,
            news_max_items=args.news_max_items,
        )
    )
    print(f"快照采集完成: {report.root_dir}")
    print(json.dumps({
        "saved_count": report.saved_count,
        "errors": len(report.errors),
        "point_in_time_root": report.point_in_time_root,
        "news_root": report.news_root,
        "news_mode": report.news_mode,
    }, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
