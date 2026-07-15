#!/usr/bin/env python3
"""Validate technical prompt/skill candidates on a holdout sample set."""

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

from src.agents.improvement_engineer import (
    AgentImprovementEngineer,
    ImprovementEngineerConfig,
)
from src.core.calibration_bootstrap import CalibrationBootstrapConfig
from src.core.technical_improvement_validation import (
    TechnicalImprovementHoldoutValidator,
)


DEFAULT_HOLDOUT_TARGETS = [
    "000333", "002594", "600036", "300750",
    "0700", "9988", "3690",
    "MSFT", "NVDA", "AMZN", "GOOGL",
]


def _parse_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


async def main():
    parser = argparse.ArgumentParser(description="技术面候选 Prompt/Skill holdout 验证")
    parser.add_argument("--training-report", required=True, type=Path, help="历史评估报告 JSON")
    parser.add_argument("--holdout-targets", help="逗号分隔 holdout 标的；默认使用内置跨市场池")
    parser.add_argument("--holdout-start", required=True, help="holdout 开始日期 YYYY-MM-DD")
    parser.add_argument("--holdout-end", required=True, help="holdout 结束日期 YYYY-MM-DD")
    parser.add_argument("--timeframe", default="短期(1周)")
    parser.add_argument("--interval-days", type=int, default=30)
    parser.add_argument("--lookback-days", type=int, default=180)
    parser.add_argument("--tolerance-days", type=int, default=10)
    parser.add_argument("--min-accuracy-delta", type=float, default=0.01)
    parser.add_argument("--min-holdout-samples", type=int, default=20)
    parser.add_argument("--min-changed-predictions", type=int, default=1)
    parser.add_argument("--output-dir", type=Path, help="输出目录")
    parser.add_argument("--apply-if-passed", action="store_true", help="holdout 通过后才应用 prompt/skill")
    parser.add_argument(
        "--registry-only",
        action="store_true",
        help="holdout 通过后只写 Agent Skill Registry，不写 prompt/markdown skill",
    )
    parser.add_argument("--engineer-min-samples", type=int, default=20)
    parser.add_argument("--engineer-min-unique-cases", type=int, default=8)
    args = parser.parse_args()

    training_report = json.loads(args.training_report.read_text(encoding="utf-8"))
    targets = (
        _parse_csv(args.holdout_targets)
        if args.holdout_targets
        else list(DEFAULT_HOLDOUT_TARGETS)
    )
    output_dir = args.output_dir or (
        project_root / "output" / "technical_improvement_validation"
    )
    report = await TechnicalImprovementHoldoutValidator().run(
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
        min_accuracy_delta=args.min_accuracy_delta,
        min_holdout_samples=args.min_holdout_samples,
        min_changed_predictions=args.min_changed_predictions,
    )

    applied = False
    registry_skill_ids = []
    if args.apply_if_passed and report.decision.should_apply:
        registry_skill_ids = TechnicalImprovementHoldoutValidator.write_registry_skills(
            report,
            holdout_report_path=output_dir / "technical_improvement_validation.json",
        )
        if args.registry_only:
            applied = bool(registry_skill_ids)
        else:
            engineer_report = await AgentImprovementEngineer().run(
                training_report,
                config=ImprovementEngineerConfig(
                    output_dir=output_dir / "agent_improvement_engineer",
                    min_samples_for_auto_apply=args.engineer_min_samples,
                    min_unique_cases_for_auto_apply=args.engineer_min_unique_cases,
                    dry_run=False,
                ),
                source_report_path=str(args.training_report),
            )
            applied = bool(registry_skill_ids) or any(
                action.status == "applied" for action in engineer_report.actions
            )

    print(f"技术面 holdout 验证完成: {output_dir}")
    print(json.dumps({
        "should_apply": report.decision.should_apply,
        "applied": applied,
        "registry_skill_ids": registry_skill_ids,
        "baseline_accuracy": report.decision.baseline_accuracy,
        "candidate_accuracy": report.decision.candidate_accuracy,
        "accuracy_delta": report.decision.accuracy_delta,
        "holdout_samples": report.decision.holdout_samples,
        "changed_predictions": report.decision.changed_predictions,
        "reason": report.decision.reason,
    }, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
