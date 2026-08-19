#!/usr/bin/env python3
"""Import legacy per-directory trial JSONL files into the global ledger."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.core.experiment_ledger import ExperimentLedger, ExperimentTrial


def bootstrap(
    output_root: Path,
    ledger: ExperimentLedger,
    *,
    research_family: str = "quant_directional_edge",
    target_version: str = "v3.1",
) -> dict:
    imported = 0
    skipped = 0
    errors: list[str] = []
    for path in sorted(output_root.rglob("trial_ledger.jsonl")):
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                trial_id = str(payload["trial_id"])
                if ledger.has_trial(trial_id):
                    skipped += 1
                    continue
                trial = ExperimentTrial(
                    trial_id=trial_id,
                    research_family=research_family,
                    market=str(payload.get("market") or "A"),
                    horizon=str(payload.get("horizon") or "5d"),
                    target_version=target_version,
                    feature_version=str(payload.get("feature_version") or "legacy"),
                    dataset_hash=str(payload.get("dataset_hash") or ""),
                    config_hash=str(payload.get("config_hash") or ""),
                    source_type="legacy_trial_ledger_import",
                    report_path=str(payload.get("report") or path),
                    best_model=str(payload.get("best_model") or ""),
                    should_promote=bool(payload.get("should_promote")),
                    candidates=list(payload.get("models") or []),
                    metrics={"legacy_source": str(path)},
                    created_at=str(payload.get("generated_at") or ""),
                )
                ledger.append(trial)
                imported += 1
            except Exception as exc:
                errors.append(f"{path}:{line_number}: {exc}")
    return {
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
        "ledger": ledger.status(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="导入旧 Quant 试验到全局只追加账本")
    parser.add_argument("--output-root", default=str(ROOT / "output"))
    parser.add_argument("--ledger", default="")
    args = parser.parse_args()
    ledger = ExperimentLedger(args.ledger) if args.ledger else ExperimentLedger.default()
    print(json.dumps(
        bootstrap(Path(args.output_root), ledger),
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
