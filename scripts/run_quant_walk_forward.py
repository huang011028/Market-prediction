#!/usr/bin/env python3
"""Run purged walk-forward evaluation for Quant V3.1 baselines."""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.settings import get_settings
from src.core.experiment_manifest import resolve_experiment_location
from src.core.quant_walk_forward import QuantWalkForwardEvaluator, WalkForwardConfig


def main() -> None:
    parser = argparse.ArgumentParser(description="运行 Quant V3.1 Walk-forward")
    parser.add_argument("--market", default="A", choices=["A", "HK", "US"])
    parser.add_argument("--horizon", default="5d", choices=["5d", "20d", "60d"])
    parser.add_argument("--models", nargs="+", default=["ridge", "logistic", "lightgbm"])
    parser.add_argument(
        "--feature-sets",
        nargs="+",
        default=["all"],
        choices=[
            "all", "technical", "technical_fundamental", "technical_news",
            "technical_industry", "technical_valuation", "enriched", "research_v2",
        ],
    )
    parser.add_argument("--train-days", type=int, default=180)
    parser.add_argument("--validation-days", type=int, default=45)
    parser.add_argument("--test-days", type=int, default=45)
    parser.add_argument("--purge-days", type=int, default=7)
    parser.add_argument("--lockbox-days", type=int, default=90)
    parser.add_argument("--min-train-samples", type=int, default=300)
    parser.add_argument("--min-validation-samples", type=int, default=90)
    parser.add_argument("--min-test-samples", type=int, default=90)
    parser.add_argument("--min-unique-train-dates", type=int, default=20)
    parser.add_argument("--unlock-lockbox", action="store_true")
    args = parser.parse_args()

    output = resolve_experiment_location(
        "quant_walk_forward",
        project_root=ROOT,
        output_root=get_settings().output_dir,
        stamp=datetime.now().strftime("%Y%m%d_%H%M%S"),
    ).root
    report = QuantWalkForwardEvaluator().run(WalkForwardConfig(
        market=args.market,
        horizon=args.horizon,
        model_names=args.models,
        feature_set_names=args.feature_sets,
        train_days=args.train_days,
        validation_days=args.validation_days,
        test_days=args.test_days,
        purge_days=args.purge_days,
        lockbox_days=args.lockbox_days,
        min_train_samples=args.min_train_samples,
        min_validation_samples=args.min_validation_samples,
        min_test_samples=args.min_test_samples,
        min_unique_train_dates=args.min_unique_train_dates,
        unlock_lockbox=args.unlock_lockbox,
    ), output_dir=output)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
