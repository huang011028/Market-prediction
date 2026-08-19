"""Version lineage captured with every forward prediction."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


LINEAGE_VERSION = "prediction_lineage.v1"


def build_prediction_lineage(
    *,
    report: Any,
    agent_results: Iterable[Any],
    llm_model: str,
    project_root: str | Path | None = None,
    cohort_id: str | None = None,
) -> dict[str, Any]:
    root = Path(project_root or Path(__file__).resolve().parents[2])
    target = getattr(report, "prediction_target", None)
    target_payload = (
        target.to_dict() if hasattr(target, "to_dict") else dict(target or {})
    )
    target_version = str(target_payload.get("target_version") or "unknown")
    now = datetime.now()
    cohort = (
        cohort_id
        or os.getenv("PREDICTION_COHORT_ID")
        or f"{target_version}-forward-{now:%Y%m}"
    )
    code_revision, dirty_worktree = _git_state(root)
    prompt_hash = _directory_hash(root / "src" / "prompts", "*.py")
    skill_path = root / "config" / "agent_improvement" / "agent_skill_registry.json"
    skill_hash = _file_hash(skill_path)
    agents = []
    for result in agent_results:
        summary = dict(getattr(result, "data_summary", None) or {})
        agents.append({
            "agent_name": str(getattr(result, "agent_name", "")),
            "status": str(getattr(result, "status", "ok")),
            "data_quality_score": getattr(result, "data_quality_score", None),
            "prediction_target": (
                getattr(result, "prediction_target").to_dict()
                if hasattr(getattr(result, "prediction_target", None), "to_dict")
                else None
            ),
            "evidence_refs": _evidence_refs(summary),
        })
    return {
        "lineage_version": LINEAGE_VERSION,
        "cohort_id": cohort,
        "captured_at": now.isoformat(),
        "target_version": target_version,
        "target_spec": target_payload,
        "llm_model": llm_model,
        "code_revision": code_revision,
        "dirty_worktree": dirty_worktree,
        "prompt_bundle_hash": prompt_hash,
        "skill_registry_hash": skill_hash,
        "skill_registry_path": str(skill_path.relative_to(root)) if skill_path.exists() else "",
        "agents": agents,
    }


def _git_state(root: Path) -> tuple[str, bool | None]:
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, check=True,
            capture_output=True, text=True, timeout=3,
        ).stdout.strip()
        dirty = bool(subprocess.run(
            ["git", "status", "--porcelain"], cwd=root, check=True,
            capture_output=True, text=True, timeout=3,
        ).stdout.strip())
        return revision, dirty
    except Exception:
        return "", None


def _directory_hash(root: Path, pattern: str) -> str:
    if not root.exists():
        return ""
    digest = hashlib.sha256()
    for path in sorted(root.rglob(pattern)):
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:24]


def _file_hash(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:24]


def _evidence_refs(value: Any, prefix: str = "") -> dict[str, Any]:
    allowed_markers = (
        "source", "provider", "snapshot", "archive", "fetched_at",
        "published_at", "effective_at", "data_time", "freshness",
    )
    result: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            lowered = str(key).lower()
            if any(marker in lowered for marker in allowed_markers):
                if isinstance(item, (str, int, float, bool)) or item is None:
                    result[path] = item
                elif isinstance(item, list):
                    result[path] = item[:20]
            if isinstance(item, (dict, list)):
                result.update(_evidence_refs(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value[:20]):
            if isinstance(item, (dict, list)):
                result.update(_evidence_refs(item, f"{prefix}[{index}]"))
    return result
