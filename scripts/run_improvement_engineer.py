#!/usr/bin/env python3
"""
运行 Agent 改进工程师。

示例:
    python scripts/run_improvement_engineer.py --evaluation-report output/historical_agent_evaluation.json
    python scripts/run_improvement_engineer.py --evaluation-report output/historical_agent_evaluation.json --dry-run
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
from src.agents.improvement_engineer import (
    AgentImprovementEngineer,
    ImprovementEngineerConfig,
)
from src.core.llm_client import create_llm_client


async def main():
    parser = argparse.ArgumentParser(description="受控运行 Agent 改进工程师")
    parser.add_argument("--evaluation-report", type=Path, required=True, help="历史评估报告 JSON")
    parser.add_argument("--output-dir", type=Path, help="输出目录")
    parser.add_argument("--min-samples", type=int, default=20, help="自动修改最小样本数")
    parser.add_argument("--min-unique-cases", type=int, default=5, help="自动修改最小独立历史案例数")
    parser.add_argument("--dry-run", action="store_true", help="只模拟，不写 prompt/skill")
    parser.add_argument("--disable-prompt-apply", action="store_true")
    parser.add_argument("--disable-skill-apply", action="store_true")
    parser.add_argument("--llm-review", action="store_true", help="使用同一 LLM API 做权限边界复核")
    args = parser.parse_args()

    settings = get_settings()
    payload = json.loads(args.evaluation_report.read_text(encoding="utf-8"))
    llm = create_llm_client() if args.llm_review else None
    output_dir = args.output_dir or (
        settings.output_dir / "agent_improvement_engineer" / args.evaluation_report.stem
    )
    engineer = AgentImprovementEngineer(llm=llm)
    report = await engineer.run(
        payload,
        config=ImprovementEngineerConfig(
            project_root=project_root,
            output_dir=output_dir,
            min_samples_for_auto_apply=args.min_samples,
            min_unique_cases_for_auto_apply=args.min_unique_cases,
            dry_run=args.dry_run,
            allow_prompt_apply=not args.disable_prompt_apply,
            allow_declarative_skill_apply=not args.disable_skill_apply,
            use_llm_review=args.llm_review,
        ),
        source_report_path=str(args.evaluation_report),
    )

    print(f"Agent 改进工程师完成: {output_dir / 'agent_improvement_engineer_report.md'}")
    print(json.dumps({
        "actions": len(report.actions),
        "applied": len(report.applied_paths),
        "protected": len(report.protected_recommendations),
    }, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
