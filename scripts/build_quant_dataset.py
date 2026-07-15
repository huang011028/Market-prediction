#!/usr/bin/env python3
"""Build point-in-time Quant V3.1 features and labels."""

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.settings import get_settings
from src.core.experiment_manifest import resolve_experiment_location
from src.core.quant_dataset import QuantDatasetBuildConfig, QuantHistoricalDatasetBuilder


def main() -> None:
    parser = argparse.ArgumentParser(description="构建 PIT Quant V3.1 历史特征")
    parser.add_argument("--targets", nargs="*", default=[])
    parser.add_argument("--start-date", default="2023-01-01")
    parser.add_argument("--end-date", default="2025-12-01")
    parser.add_argument("--timeframe", default="短期(1周)")
    parser.add_argument("--interval-days", type=int, default=14)
    parser.add_argument("--lookback-days", type=int, default=180)
    parser.add_argument("--max-samples", type=int, default=5000)
    parser.add_argument("--use-universe", action="store_true")
    parser.add_argument("--universe-limit", type=int, default=30)
    parser.add_argument("--min-listing-days", type=int, default=250)
    parser.add_argument("--min-price", type=float, default=1.0)
    parser.add_argument("--min-avg-traded-value", type=float, default=0.0)
    parser.add_argument("--industry-neutralization", action="store_true")
    parser.add_argument("--sample-seed", default="quant-v3.1-a-share")
    parser.add_argument("--no-stratify", action="store_true")
    parser.add_argument("--no-pit-enrichment", action="store_true")
    parser.add_argument("--no-price-cache", action="store_true")
    parser.add_argument("--fundamental-max-age-days", type=int, default=550)
    parser.add_argument("--announcement-lookback-days", type=int, default=90)
    parser.add_argument("--history-fetch-concurrency", type=int, default=3)
    parser.add_argument("--append", action="store_true", help="不删除同市场/周期/日期分区的旧样本")
    args = parser.parse_args()

    if not args.targets and not args.use_universe:
        parser.error("需要 --targets 或 --use-universe")
    output = resolve_experiment_location(
        "quant_dataset",
        project_root=ROOT,
        output_root=get_settings().output_dir,
        stamp=datetime.now().strftime("%Y%m%d_%H%M%S"),
    ).root
    report = asyncio.run(QuantHistoricalDatasetBuilder().run(QuantDatasetBuildConfig(
        targets=args.targets,
        start_date=args.start_date,
        end_date=args.end_date,
        timeframe=args.timeframe,
        interval_days=args.interval_days,
        lookback_days=args.lookback_days,
        max_samples=args.max_samples,
        use_universe=args.use_universe,
        universe_limit=args.universe_limit,
        min_listing_days=args.min_listing_days,
        min_price=args.min_price,
        min_avg_traded_value=args.min_avg_traded_value,
        industry_neutralization=args.industry_neutralization,
        universe_sample_seed=args.sample_seed,
        universe_stratify=not args.no_stratify,
        replace_partition=not args.append,
        use_pit_enrichment=not args.no_pit_enrichment,
        fundamental_max_age_days=args.fundamental_max_age_days,
        announcement_lookback_days=args.announcement_lookback_days,
        use_price_cache=not args.no_price_cache,
        history_fetch_concurrency=args.history_fetch_concurrency,
    ), output_dir=output))
    output.mkdir(parents=True, exist_ok=True)
    path = output / "quant_dataset_report.json"
    path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report_path": str(path), **report.to_dict()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
