#!/usr/bin/env python3
"""Run the frozen two-stage Quant and portfolio validation pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.settings import get_settings
from src.core.experiment_manifest import resolve_experiment_location, write_experiment_manifest
from src.core.portfolio_backtester import PortfolioBacktestConfig, PortfolioBacktester
from src.core.quant_two_stage import TwoStageConfig, TwoStageQuantEvaluator


def run(config_path: Path, experiment_id: str | None = None) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    stamp = experiment_id or datetime.now().strftime("%Y%m%d_%H%M%S")
    location = resolve_experiment_location(
        "quant_two_stage",
        project_root=ROOT,
        output_root=get_settings().output_dir,
        stamp=stamp,
    )
    location.root.mkdir(parents=True, exist_ok=True)
    two_stage = TwoStageQuantEvaluator().run(
        TwoStageConfig(**config["two_stage"]),
        location.root / "walk_forward",
    )
    portfolio_source = config["portfolio"]
    policy = portfolio_source["production_policy"]
    portfolio_reports = {}
    variants = sorted(
        key for key in two_stage.aggregate_metrics
        if key != "empirical_prior"
    )
    for variant in variants:
        paths = sorted(
            str(path) for path in (location.root / "walk_forward" / "oof").glob(
                f"fold_*_{variant}.jsonl"
            )
        )
        if not paths:
            continue
        payload = {
            key: value for key, value in portfolio_source.items()
            if key != "production_policy"
        }
        payload.update({
            "prediction_paths": paths,
            "model_name": variant,
            "min_edge_score": float(policy["edge_threshold"]),
            "policy_id": str(policy["policy_id"]),
            "policy_role": "production_candidate",
            "pre_registered": True,
            "selection_source": str(policy["selection_source"]),
        })
        report = PortfolioBacktester().run(
            PortfolioBacktestConfig(**payload),
            location.root / "portfolio" / variant,
        )
        portfolio_reports[variant] = report.to_dict()

    payload = {
        "version": config["version"],
        "generated_at": datetime.now().isoformat(),
        "experiment_id": location.experiment_id,
        "config_path": str(config_path),
        "two_stage": two_stage.to_dict(),
        "portfolio": portfolio_reports,
    }
    report_path = location.root / "quant_two_stage_pipeline_report.json"
    payload["report_path"] = str(report_path)
    payload["manifest_path"] = write_experiment_manifest(
        location.root,
        experiment_id=location.experiment_id,
        kind="quant_two_stage_pipeline",
        source_type=location.source_type,
        config=config,
        dataset_hash=two_stage.data_summary["dataset_hash"],
        artifacts={
            "report": str(report_path),
            "two_stage_report": two_stage.artifact_paths.get("report", ""),
        },
        metrics={
            "best_model": two_stage.promotion_gate.get("best_model"),
            "model_should_promote": two_stage.promotion_gate.get("should_promote", False),
            "portfolio_candidates": len(portfolio_reports),
            "portfolio_promoted": sum(
                bool(item.get("promotion_gate", {}).get("should_promote"))
                for item in portfolio_reports.values()
            ),
        },
        project_root=ROOT,
    )
    report_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="运行两阶段 Quant 验证")
    parser.add_argument(
        "--config",
        default=str(ROOT / "config" / "quant" / "two_stage_v2.json"),
    )
    parser.add_argument("--experiment-id", default="")
    args = parser.parse_args()
    report = run(
        Path(args.config).resolve(),
        experiment_id=args.experiment_id or None,
    )
    print(json.dumps({
        "experiment_id": report["experiment_id"],
        "version": report["version"],
        "best_model": report["two_stage"]["promotion_gate"].get("best_model"),
        "should_promote": report["two_stage"]["promotion_gate"].get("should_promote"),
        "portfolio_candidates": len(report["portfolio"]),
        "manifest_path": report["manifest_path"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
