#!/usr/bin/env python3
"""Verify expired predictions and optionally collect today's PIT snapshots."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.core.evidence_maintenance import EvidenceMaintenanceConfig, EvidenceMaintenanceRunner


def main() -> None:
    parser = argparse.ArgumentParser(description="运行预测验证与时点证据维护")
    parser.add_argument("--collect", action="store_true", help="同时采集今天的基本面/行业/宏观/新闻快照")
    parser.add_argument("--targets", nargs="*", default=[], help="指定快照标的；为空时使用近期预测标的")
    parser.add_argument("--recent-limit", type=int, default=30)
    parser.add_argument("--max-snapshots", type=int, default=0)
    parser.add_argument("--news-mode", choices=["none", "raw", "evidence", "formal"], default="evidence")
    args = parser.parse_args()

    report = asyncio.run(EvidenceMaintenanceRunner().run_once(EvidenceMaintenanceConfig(
        collect_snapshots=args.collect,
        targets=args.targets,
        recent_target_limit=args.recent_limit,
        news_mode=args.news_mode,
        max_snapshots=args.max_snapshots,
    )))
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
