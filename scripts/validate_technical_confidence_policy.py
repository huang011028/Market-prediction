#!/usr/bin/env python3
"""Validate technical confidence-cap skills on a holdout sample set."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

load_dotenv(project_root / ".env")

from src.core.calibration_bootstrap import CalibrationBootstrapConfig
from src.core.technical_improvement_validation import TechnicalConfidencePolicyValidator


DEFAULT_HOLDOUT_TARGETS = [
    "600900", "601899", "002415", "300760",
    "COST", "ORCL", "CRM", "ADBE", "PFE", "BA",
]


def _parse_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


async def main():
    parser = argparse.ArgumentParser(description="技术面候选 Confidence Skill holdout 验证")
    parser.add_argument("--training-report", required=True, type=Path, help="历史评估报告 JSON")
    parser.add_argument("--holdout-targets", help="逗号分隔 holdout 标的；默认使用内置跨市场池")
    parser.add_argument("--holdout-start", required=True, help="holdout 开始日期 YYYY-MM-DD")
    parser.add_argument("--holdout-end", required=True, help="holdout 结束日期 YYYY-MM-DD")
    parser.add_argument("--timeframe", default="短期(1周)")
    parser.add_argument("--interval-days", type=int, default=30)
    parser.add_argument("--lookback-days", type=int, default=180)
    parser.add_argument("--tolerance-days", type=int, default=10)
    parser.add_argument("--confidence-cap", type=float, default=0.35)
    parser.add_argument("--min-brier-delta", type=float, default=0.005)
    parser.add_argument("--min-holdout-samples", type=int, default=20)
    parser.add_argument("--min-changed-predictions", type=int, default=3)
    parser.add_argument("--min-matched-samples", type=int, default=3)
    parser.add_argument("--output-dir", type=Path, help="输出目录")
    parser.add_argument("--apply-if-passed", action="store_true", help="holdout 通过后写入 Agent Skill Registry")
    args = parser.parse_args()

    training_report = json.loads(args.training_report.read_text(encoding="utf-8"))
    targets = (
        _parse_csv(args.holdout_targets)
        if args.holdout_targets
        else list(DEFAULT_HOLDOUT_TARGETS)
    )
    output_dir = args.output_dir or (
        project_root / "output" / "technical_confidence_policy_validation"
    )
    validator = TechnicalConfidencePolicyValidator()
    report = await validator.run_confidence_validation(
        evaluation_report=training_report,
        training_report_path=str(args.training_report),
        holdout_config=CalibrationBootstrapConfig(
            targets=targets,
            start_date=args.holdout_start,
            end_date=args.holdout_end,
            timeframe=args.timeframe,
            interval_days=args.interval_days,
            lookback_days=args.lookback_days,
            tolerance_days=args.tolerance_days,
        ),
        output_dir=output_dir,
        confidence_cap=args.confidence_cap,
        min_brier_delta=args.min_brier_delta,
        min_holdout_samples=args.min_holdout_samples,
        min_changed_predictions=args.min_changed_predictions,
        min_matched_samples=args.min_matched_samples,
    )

    registry_skill_ids = []
    if args.apply_if_passed and report.decision.should_apply:
        registry_skill_ids = validator.write_confidence_registry_skills(
            report,
            holdout_report_path=output_dir / "technical_confidence_policy_validation.json",
        )

    print(f"技术面 confidence skill holdout 验证完成: {output_dir}")
    print(json.dumps({
        "should_apply": report.decision.should_apply,
        "applied": bool(registry_skill_ids),
        "registry_skill_ids": registry_skill_ids,
        "baseline_brier": report.decision.baseline_brier,
        "candidate_brier": report.decision.candidate_brier,
        "brier_delta": report.decision.brier_delta,
        "holdout_samples": report.decision.holdout_samples,
        "matched_samples": report.decision.matched_samples,
        "changed_predictions": report.decision.changed_predictions,
        "reason": report.decision.reason,
    }, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
