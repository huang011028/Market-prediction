#!/usr/bin/env python3
"""
根据回放报告生成 Agent 工程改进补丁草案。

用法:
    python scripts/build_agent_improvement_drafts.py --replay-report output/news_snapshot_replay_all.json
"""

import argparse
import json
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

load_dotenv(project_root / ".env")

from config.settings import get_settings
from src.core.improvement_patch_planner import AgentImprovementPatchPlanner


def _load_report(path: Path, agent_name: str) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and payload.get("agent_name"):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("reports"), list):
        for report in payload["reports"]:
            if report.get("agent_name") == agent_name:
                return report
    raise ValueError(f"没有在 {path} 中找到 {agent_name} 的回放报告")


def main():
    parser = argparse.ArgumentParser(description="生成 prompt/MCP/skill 改进补丁草案")
    parser.add_argument("--replay-report", type=Path, required=True, help="回放报告 JSON")
    parser.add_argument("--agent-name", default="最新新闻分析师")
    parser.add_argument("--output-dir", type=Path, help="草案输出目录")
    parser.add_argument("--min-samples", type=int, default=None)
    args = parser.parse_args()

    settings = get_settings()
    report = _load_report(args.replay_report, args.agent_name)
    output_dir = args.output_dir
    if output_dir is None:
        output_dir = settings.output_dir / "agent_improvement_drafts"

    planner = AgentImprovementPatchPlanner()
    plan = planner.build_plan_from_report(report, min_samples=args.min_samples)
    written = planner.write_plan(plan, output_dir)

    print(f"改进草案已生成: {written['plan_md']}")
    for draft_path in written.get("drafts", []):
        print(f"- {draft_path}")


if __name__ == "__main__":
    main()
