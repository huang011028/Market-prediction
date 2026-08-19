#!/usr/bin/env python3
"""Run the fixed Research Data V2 enrichment, dataset, and validation pipeline."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.settings import get_settings
from src.core.experiment_manifest import resolve_experiment_location, write_experiment_manifest
from src.core.quant_dataset import QuantDatasetBuildConfig, QuantHistoricalDatasetBuilder
from src.core.portfolio_backtester import PortfolioBacktestConfig, PortfolioBacktester
from src.core.quant_walk_forward import QuantWalkForwardEvaluator, WalkForwardConfig
from src.core.quant_feature_audit import FeatureAuditConfig, QuantFeatureAuditor
from src.data.investable_universe import InvestableUniverseStore
from src.data.quant_pit_enrichment import QuantPitEnrichmentRefresher, QuantPitRefreshConfig


def _write_json(path: Path, payload: dict) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


async def run(
    config_path: Path,
    stages: set[str],
    experiment_id: str | None = None,
) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    stamp = experiment_id or datetime.now().strftime("%Y%m%d_%H%M%S")
    location = resolve_experiment_location(
        "research_data_v2",
        project_root=ROOT,
        output_root=get_settings().output_dir,
        stamp=stamp,
    )
    location.root.mkdir(parents=True, exist_ok=True)
    state_path = location.root / "pipeline_state.json"
    if state_path.exists():
        report = json.loads(state_path.read_text(encoding="utf-8"))
        if report.get("version") != config["version"]:
            raise ValueError("断点实验的数据版本与当前配置不一致")
    else:
        report = {
            "version": config["version"],
            "generated_at": datetime.now().isoformat(),
            "config_path": str(config_path),
            "experiment_id": location.experiment_id,
            "stages": {},
        }
    report["resumed_at"] = datetime.now().isoformat()

    if "enrichment" in stages:
        source = config["enrichment"]
        members = InvestableUniverseStore().sampled_union(
            source["start_date"],
            source["end_date"],
            interval_days=source["interval_days"],
            market=config["market"],
            min_listing_days=source["min_listing_days"],
            limit=source["universe_limit"],
            sample_seed=source["sample_seed"],
            stratify=source["stratify"],
        )
        refresh_config = QuantPitRefreshConfig(
            symbols=[item["symbol"] for item in members],
            start_date=source["start_date"],
            end_date=source["end_date"],
            market=config["market"],
            concurrency=source["concurrency"],
            include_fundamental=source["include_fundamental"],
            include_performance=source["include_performance"],
            include_announcements=source["include_announcements"],
            include_industry=source["include_industry"],
            include_financial_quality=source.get("include_financial_quality", False),
            include_consensus=source.get("include_consensus", False),
        )
        enrichment = await QuantPitEnrichmentRefresher().run(refresh_config)
        payload = enrichment.to_dict()
        payload["universe_union_symbols"] = len(members)
        payload["report_path"] = _write_json(
            location.root / "enrichment" / "refresh_report.json", payload,
        )
        report["stages"]["enrichment"] = payload
        _write_json(location.root / "pipeline_state.json", report)

    if "dataset" in stages:
        dataset_config = QuantDatasetBuildConfig(**config["dataset"])
        dataset = await QuantHistoricalDatasetBuilder().run(
            dataset_config,
            output_dir=location.root / "dataset",
        )
        payload = dataset.to_dict()
        payload["report_path"] = _write_json(
            location.root / "dataset" / "quant_dataset_report.json", payload,
        )
        report["stages"]["dataset"] = payload
        _write_json(location.root / "pipeline_state.json", report)

    if "audit" in stages and config.get("audit"):
        audit = await asyncio.to_thread(
            QuantFeatureAuditor().run,
            FeatureAuditConfig(**config["audit"]),
            location.root / "audit",
        )
        report["stages"]["audit"] = audit
        _write_json(location.root / "pipeline_state.json", report)

    if "walk-forward" in stages:
        walk_config = WalkForwardConfig(**config["walk_forward"])
        walk = await asyncio.to_thread(
            QuantWalkForwardEvaluator().run,
            walk_config,
            location.root / "walk_forward",
        )
        report["stages"]["walk_forward"] = walk.to_dict()
        _write_json(location.root / "pipeline_state.json", report)

    if "portfolio" in stages:
        source = config["portfolio"]
        portfolio_reports = {}
        production_policy = source.get("production_policy") or {}
        threshold_specs = []
        if production_policy:
            threshold_specs.append({
                "threshold": float(production_policy["edge_threshold"]),
                "policy_id": str(production_policy["policy_id"]),
                "policy_role": "production_candidate",
                "pre_registered": True,
                "selection_source": str(
                    production_policy.get("selection_source") or "frozen_config"
                ),
            })
            threshold_specs.extend({
                "threshold": float(value),
                "policy_id": f"diagnostic-edge-{float(value):.2f}",
                "policy_role": "diagnostic",
                "pre_registered": False,
                "selection_source": "diagnostic_only",
            } for value in source.get("diagnostic_edge_thresholds", []))
        else:
            threshold_specs.extend({
                "threshold": float(value),
                "policy_id": f"legacy-edge-{float(value):.2f}",
                "policy_role": "diagnostic",
                "pre_registered": False,
                "selection_source": "legacy_edge_thresholds",
            } for value in source.get("edge_thresholds", []))
        for model_variant in source["model_variants"]:
            prediction_paths = sorted(
                str(path) for path in (location.root / "walk_forward" / "oof").glob(
                    f"fold_*_{model_variant}.jsonl"
                )
            )
            if not prediction_paths:
                raise ValueError(f"没有找到 {model_variant} 的 OOF 文件")
            for threshold_spec in threshold_specs:
                threshold = threshold_spec["threshold"]
                key = f"{model_variant}__edge_{float(threshold):.2f}"
                payload = {
                    key: value
                    for key, value in source.items()
                    if key not in {
                        "model_variants", "edge_thresholds", "production_policy",
                        "diagnostic_edge_thresholds",
                    }
                }
                payload.update({
                    "prediction_paths": prediction_paths,
                    "model_name": model_variant,
                    "min_edge_score": float(threshold),
                    "policy_id": threshold_spec["policy_id"],
                    "policy_role": threshold_spec["policy_role"],
                    "pre_registered": threshold_spec["pre_registered"],
                    "selection_source": threshold_spec["selection_source"],
                })
                portfolio = await asyncio.to_thread(
                    PortfolioBacktester().run,
                    PortfolioBacktestConfig(**payload),
                    location.root / "portfolio" / key,
                )
                portfolio_reports[key] = portfolio.to_dict()
        report["stages"]["portfolio"] = portfolio_reports
        _write_json(location.root / "pipeline_state.json", report)

    report_path = _write_json(location.root / "research_data_v2_report.json", report)
    manifest_path = write_experiment_manifest(
        location.root,
        experiment_id=location.experiment_id,
        kind=location.kind,
        source_type=location.source_type,
        config=config,
        dataset_hash=(
            report.get("stages", {}).get("walk_forward", {}).get("data_summary", {}).get("dataset_hash", "")
        ),
        artifacts={"report": report_path, "config": str(config_path)},
        metrics={
            "completed_stages": sorted(report["stages"]),
            "dataset_rows": report.get("stages", {}).get("dataset", {}).get("saved", 0),
            "promotion": report.get("stages", {}).get("walk_forward", {}).get("promotion_gate", {}),
        },
        project_root=ROOT,
    )
    report["manifest_path"] = manifest_path
    _write_json(location.root / "research_data_v2_report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="运行固定 Research Data V2 流水线")
    parser.add_argument(
        "--config",
        default=str(ROOT / "config" / "quant" / "research_data_v2_4.json"),
    )
    parser.add_argument(
        "--stage",
        action="append",
        choices=["enrichment", "dataset", "audit", "walk-forward", "portfolio", "all"],
        default=[],
        help="可重复指定；默认 all",
    )
    parser.add_argument(
        "--experiment-id",
        default="",
        help="继续指定 output/research_data_v2 下的既有实验目录",
    )
    args = parser.parse_args()
    selected = set(args.stage or ["all"])
    stages = (
        {"enrichment", "dataset", "audit", "walk-forward", "portfolio"}
        if "all" in selected else selected
    )
    report = asyncio.run(run(
        Path(args.config).resolve(),
        stages,
        experiment_id=args.experiment_id or None,
    ))
    print(json.dumps({
        "experiment_id": report["experiment_id"],
        "version": report["version"],
        "completed_stages": sorted(report.get("stages", {})),
        "manifest_path": report.get("manifest_path", ""),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
