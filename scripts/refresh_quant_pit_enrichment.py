#!/usr/bin/env python3
"""Refresh announcement-time fundamentals, disclosures and industry intervals."""

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.settings import get_settings
from src.core.experiment_manifest import resolve_experiment_location, write_experiment_manifest
from src.data.investable_universe import InvestableUniverseStore
from src.data.quant_pit_enrichment import (
    QuantPitEnrichmentRefresher,
    QuantPitRefreshConfig,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="刷新 Quant PIT 丰富特征")
    parser.add_argument("--symbols", nargs="*", default=[])
    parser.add_argument("--retry-report", default="", help="只重跑指定报告中的失败标的")
    parser.add_argument("--start-date", default="2024-01-01")
    parser.add_argument("--end-date", default="2025-12-31")
    parser.add_argument("--use-universe", action="store_true")
    parser.add_argument("--universe-limit", type=int, default=60)
    parser.add_argument("--min-listing-days", type=int, default=120)
    parser.add_argument("--interval-days", type=int, default=7)
    parser.add_argument("--sample-seed", default="quant-v3.1-a-share")
    parser.add_argument("--no-stratify", action="store_true")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--skip-fundamental", action="store_true")
    parser.add_argument("--skip-performance", action="store_true")
    parser.add_argument("--skip-announcements", action="store_true")
    parser.add_argument("--skip-industry", action="store_true")
    args = parser.parse_args()

    symbols = list(args.symbols)
    if args.retry_report:
        retry_payload = json.loads(Path(args.retry_report).read_text(encoding="utf-8"))
        symbols.extend(item["symbol"] for item in retry_payload.get("errors", []))
    if args.use_universe:
        members = InvestableUniverseStore().sampled_union(
            args.start_date,
            args.end_date,
            interval_days=args.interval_days,
            market="A",
            min_listing_days=args.min_listing_days,
            limit=args.universe_limit,
            sample_seed=args.sample_seed,
            stratify=not args.no_stratify,
        )
        symbols = [item["symbol"] for item in members]
    if not symbols:
        parser.error("需要 --symbols 或 --use-universe")

    report = asyncio.run(QuantPitEnrichmentRefresher().run(QuantPitRefreshConfig(
        symbols=symbols,
        start_date=args.start_date,
        end_date=args.end_date,
        concurrency=args.concurrency,
        include_fundamental=not args.skip_fundamental,
        include_performance=not args.skip_performance,
        include_announcements=not args.skip_announcements,
        include_industry=not args.skip_industry,
    )))
    location = resolve_experiment_location(
        "quant_pit_enrichment",
        project_root=ROOT,
        output_root=get_settings().output_dir,
        stamp=datetime.now().strftime("%Y%m%d_%H%M%S"),
    )
    output_dir = location.root
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "refresh_report.json"
    path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    write_experiment_manifest(
        output_dir,
        experiment_id=location.experiment_id,
        kind=location.kind,
        source_type=location.source_type,
        config=report.config,
        artifacts={"report": str(path)},
        metrics={"saved": report.saved, "errors": len(report.errors)},
        project_root=ROOT,
    )
    print(json.dumps({"report_path": str(path), **report.to_dict()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
