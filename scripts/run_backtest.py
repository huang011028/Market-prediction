#!/usr/bin/env python3
"""
回测 CLI — 在历史区间内滚动测试预测准确率

用法:
    python scripts/run_backtest.py --target 000001 --start 2026-01-01 --end 2026-06-30
    python scripts/run_backtest.py --target 000001 --start 2026-01-01 --end 2026-06-30 --interval 14
"""

import sys, asyncio, argparse
from pathlib import Path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv; load_dotenv(project_root / ".env")
from src.utils.logger import setup_logging, get_logger
from src.core.backtester import Backtester, BacktestConfig
from config.settings import get_settings


async def main():
    parser = argparse.ArgumentParser(description="回测引擎")
    parser.add_argument("--target", "-t", default="000001")
    parser.add_argument("--start", default="2026-01-01")
    parser.add_argument("--end", default="2026-06-30")
    parser.add_argument("--timeframe", "-f", default="短期(1周)")
    parser.add_argument("--interval", type=int, default=14, help="间隔天数")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    settings = get_settings()
    setup_logging(log_level=args.log_level, log_dir=settings.logs_dir)
    logger = get_logger("backtest")

    config = BacktestConfig(
        target=args.target,
        start_date=args.start,
        end_date=args.end,
        timeframe=args.timeframe,
        interval_days=args.interval,
    )

    bt = Backtester()
    report = await bt.run(config)

    print("\n" + report.summary())

    # 保存
    import json
    path = settings.output_dir / f"backtest_{args.target}_{args.start}_{args.end}.json"
    path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    logger.info(f"回测报告已保存: {path}")


if __name__ == "__main__":
    asyncio.run(main())
