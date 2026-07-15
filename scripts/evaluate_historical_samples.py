#!/usr/bin/env python3
"""
批量历史样本评估 CLI。

用法:
    python scripts/evaluate_historical_samples.py --bootstrap-report output/calibration_bootstrap.json
    python scripts/evaluate_historical_samples.py --prediction-db data/predictions.db
    python scripts/evaluate_historical_samples.py --bootstrap-report output/calibration_bootstrap.json --draft-dir output/agent_improvement_drafts
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

load_dotenv(project_root / ".env")

from config.settings import get_settings
from src.core.historical_evaluator import HistoricalAgentEvaluator
from src.core.improvement_patch_planner import AgentImprovementPatchPlanner
from src.data.prediction_store import PredictionStore


def _load_bootstrap_samples(paths: list[Path], evaluator: HistoricalAgentEvaluator) -> list:
    samples = []
    for path in paths:
        payload = evaluator.load_report_file(path)
        samples.extend(evaluator.samples_from_bootstrap_report(payload))
    return samples


def _write_drafts(report: dict, output_dir: Path, min_samples: int | None) -> dict:
    planner = AgentImprovementPatchPlanner()
    written = {}
    signals_by_agent: dict[str, list[dict]] = {}
    for signal in report.get("improvement_signals") or []:
        agent_name = signal.get("agent_name") or "unknown"
        signals_by_agent.setdefault(agent_name, []).append(signal)

    for agent_name, signals in signals_by_agent.items():
        plan = planner.build_plan(agent_name, signals)
        agent_dir = output_dir / _safe_slug(agent_name)
        written[agent_name] = planner.write_plan(plan, agent_dir)
    return written


def _safe_slug(value: str) -> str:
    safe = value.strip().lower().replace(" ", "_")
    for ch in '/\\:*?"<>|()[]':
        safe = safe.replace(ch, "_")
    return safe[:80] or "agent"


def main():
    parser = argparse.ArgumentParser(description="批量历史样本评估与 agent 贡献归因")
    parser.add_argument(
        "--bootstrap-report",
        type=Path,
        action="append",
        default=[],
        help="calibration bootstrap / replay 报告 JSON，可传多次",
    )
    parser.add_argument(
        "--prediction-db",
        type=Path,
        help="PredictionStore SQLite 路径；不传则使用默认 data/predictions.db",
    )
    parser.add_argument(
        "--skip-prediction-store",
        action="store_true",
        help="只评估 bootstrap/replay 报告，不读取 PredictionStore",
    )
    parser.add_argument("--limit", type=int, default=2000, help="最多读取的已验证 agent 结果")
    parser.add_argument("--min-samples", type=int, default=5, help="触发改进信号的最小样本数")
    parser.add_argument("--output", type=Path, help="评估报告 JSON 输出路径")
    parser.add_argument("--draft-dir", type=Path, help="同时生成 prompt/MCP/skill/数据源改进草案")
    args = parser.parse_args()

    settings = get_settings()
    evaluator = HistoricalAgentEvaluator()

    samples = _load_bootstrap_samples(args.bootstrap_report, evaluator)

    if not args.skip_prediction_store:
        db_path = args.prediction_db or (settings.data_dir / "predictions.db")
        if db_path.exists():
            store = PredictionStore(db_path=db_path)
            samples.extend(evaluator.samples_from_prediction_store(store, limit=args.limit))
        elif not args.bootstrap_report:
            raise FileNotFoundError(f"没有找到 PredictionStore 数据库: {db_path}")

    report = evaluator.evaluate(samples, min_samples=args.min_samples)
    output = args.output
    if output is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output = settings.output_dir / f"historical_agent_evaluation_{stamp}.json"

    written = evaluator.write_report(report, output)
    print(f"历史评估报告已生成: {written['markdown']}")
    print(f"JSON: {written['json']}")

    if args.draft_dir:
        draft_written = _write_drafts(report.to_dict(), args.draft_dir, args.min_samples)
        print(f"改进草案目录: {args.draft_dir}")
        for agent_name, paths in draft_written.items():
            print(f"- {agent_name}: {paths.get('plan_md')}")

    print(
        json.dumps(
            {
                "total_samples": report.total_samples,
                "verified_predictions": report.verified_predictions,
                "wrong_strategy_signals": len(report.wrong_strategy_signals),
                "strength_signals": len(report.strength_signals),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
