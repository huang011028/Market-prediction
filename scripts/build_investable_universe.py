#!/usr/bin/env python3
"""Refresh the point-in-time A-share instrument master."""

import asyncio
import argparse
import json
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from src.data.investable_universe import AShareUniverseBuilder


async def main() -> None:
    parser = argparse.ArgumentParser(description="刷新 point-in-time A 股股票池")
    parser.add_argument("--include-bse", action="store_true", help="同时请求可能较慢的北交所列表")
    args = parser.parse_args()
    report = await AShareUniverseBuilder().refresh(include_bse=args.include_bse)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
