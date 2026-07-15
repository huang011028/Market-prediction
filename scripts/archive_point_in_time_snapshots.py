#!/usr/bin/env python3
"""
归档基本面/行业/宏观 point-in-time 快照。

这些快照是主动历史样本池覆盖公司前景、行业对比、国际形势分析师的
数据基础。脚本只保存当前时点可见的数据；后续回放时再用未来价格验证。

示例:
    python scripts/archive_point_in_time_snapshots.py --targets 000001,600519,AAPL
    python scripts/archive_point_in_time_snapshots.py --targets AAPL,MSFT --agents fundamental,industry
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
from src.data.fundamental_fetcher import FundamentalFetcher
from src.data.industry_fetcher import IndustryFetcher
from src.data.macro_fetcher import MacroFetcherV2
from src.data.point_in_time_snapshot_archive import PointInTimeSnapshotArchive
from src.data.stock_context import get_stock_macro_context
from src.data.symbol_resolver import resolve_symbol
from src.utils.logger import setup_logging


AGENT_ALIASES = {
    "fundamental": "公司前景分析师",
    "industry": "行业对比分析师",
    "macro": "国际形势分析师",
}


def _parse_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


async def _archive_fundamental(archive, target, timeframe, as_of):
    info = resolve_symbol(target)
    data = await FundamentalFetcher().fetch_enhanced(info.symbol, info.market)
    data["_market"] = info.market
    data["_resolved_symbol"] = info.symbol
    data["_resolved_name"] = info.name
    return archive.save_snapshot(
        agent_name="公司前景分析师",
        target=target,
        symbol=info.symbol,
        name=info.name,
        market=info.market,
        timeframe=timeframe,
        data=data,
        as_of=as_of,
    )


async def _archive_industry(archive, target, timeframe, as_of):
    info = resolve_symbol(target)
    data = await IndustryFetcher().fetch_enhanced(info.symbol, info.market)
    data["_market"] = info.market
    data["_resolved_symbol"] = info.symbol
    data["_resolved_name"] = info.name
    return archive.save_snapshot(
        agent_name="行业对比分析师",
        target=target,
        symbol=info.symbol,
        name=info.name,
        market=info.market,
        timeframe=timeframe,
        data=data,
        as_of=as_of,
    )


async def _archive_macro(archive, target, timeframe, as_of):
    info = resolve_symbol(target)
    fetcher = MacroFetcherV2()
    macro_data = await fetcher.fetch(info.symbol, info.market)
    data = macro_data.to_agent_dict()
    stock_ctx = get_stock_macro_context(info.symbol, info.market, info.name)
    data["_stock_context"] = stock_ctx
    data["_market"] = info.market
    data["_resolved_symbol"] = info.symbol
    data["_resolved_name"] = info.name
    return archive.save_snapshot(
        agent_name="国际形势分析师",
        target=target,
        symbol=info.symbol,
        name=info.name,
        market=info.market,
        timeframe=timeframe,
        data=data,
        stock_context=stock_ctx,
        as_of=as_of,
    )


async def main():
    parser = argparse.ArgumentParser(description="归档 point-in-time 历史快照")
    parser.add_argument("--targets", required=True, help="逗号分隔标的列表")
    parser.add_argument("--agents", default="fundamental,industry,macro", help="fundamental,industry,macro")
    parser.add_argument("--timeframe", default="短期(1周)")
    parser.add_argument("--as-of", help="仅允许今天 YYYY-MM-DD；当前数据不能回填历史日期")
    parser.add_argument("--output-dir", type=Path, help="快照根目录，默认 data/point_in_time_snapshots")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    settings = get_settings()
    setup_logging(log_level=args.log_level, log_dir=settings.logs_dir)
    archive = PointInTimeSnapshotArchive(root_dir=args.output_dir)
    targets = _parse_csv(args.targets)
    agents = [
        AGENT_ALIASES[item]
        for item in _parse_csv(args.agents)
        if item in AGENT_ALIASES
    ]

    records = []
    errors = []
    for target in targets:
        for agent in agents:
            try:
                if agent == "公司前景分析师":
                    records.append(
                        await _archive_fundamental(archive, target, args.timeframe, args.as_of)
                    )
                elif agent == "行业对比分析师":
                    records.append(
                        await _archive_industry(archive, target, args.timeframe, args.as_of)
                    )
                elif agent == "国际形势分析师":
                    records.append(
                        await _archive_macro(archive, target, args.timeframe, args.as_of)
                    )
            except Exception as e:
                errors.append({"target": target, "agent_name": agent, "reason": str(e)})

    print(json.dumps({
        "saved": records,
        "errors": errors,
        "root_dir": str(archive.root_dir),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
