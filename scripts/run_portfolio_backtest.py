#!/usr/bin/env python3
"""Run a transaction-cost-aware portfolio backtest from OOF predictions."""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.settings import get_settings
from src.core.portfolio_backtester import PortfolioBacktestConfig, PortfolioBacktester


def main() -> None:
    parser = argparse.ArgumentParser(description="运行 OOF 组合成本回测")
    parser.add_argument("prediction_paths", nargs="+")
    parser.add_argument("--market", default="A", choices=["A", "HK", "US"])
    parser.add_argument("--model-name", default=None)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--min-edge-score", type=float, default=0.10)
    parser.add_argument("--min-avg-traded-value", type=float, default=0.0)
    parser.add_argument("--initial-capital", type=float, default=1_000_000)
    args = parser.parse_args()

    output = get_settings().output_dir / "portfolio_backtest" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    report = PortfolioBacktester().run(PortfolioBacktestConfig(
        prediction_paths=args.prediction_paths,
        market=args.market,
        model_name=args.model_name,
        top_k=args.top_k,
        min_edge_score=args.min_edge_score,
        min_avg_traded_value=args.min_avg_traded_value,
        initial_capital=args.initial_capital,
    ), output_dir=output)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
