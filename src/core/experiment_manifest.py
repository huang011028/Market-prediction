"""Versioned experiment locations and audit manifests."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


VALID_SOURCE_TYPES = {"real", "test", "synthetic"}


@dataclass
class ExperimentLocation:
    experiment_id: str
    kind: str
    source_type: str
    root: Path


@dataclass
class ExperimentManifest:
    experiment_id: str
    kind: str
    source_type: str
    generated_at: str
    status: str
    config_hash: str
    config: dict[str, Any]
    dataset_hash: str = ""
    code_revision: str = ""
    branch: str = ""
    dirty_worktree: Optional[bool] = None
    artifacts: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def detect_experiment_source(explicit: Optional[str] = None) -> str:
    value = str(
        explicit
        or os.getenv("MARKET_PREDICTION_EXPERIMENT_SOURCE")
        or ("test" if os.getenv("PYTEST_CURRENT_TEST") else "real")
    ).strip().lower()
    if value not in VALID_SOURCE_TYPES:
        raise ValueError(f"未知实验来源类型: {value}")
    return value


def resolve_experiment_location(
    kind: str,
    *,
    project_root: str | Path,
    output_root: str | Path,
    stamp: Optional[str] = None,
    source_type: Optional[str] = None,
) -> ExperimentLocation:
    source = detect_experiment_source(source_type)
    experiment_id = stamp or datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    project = Path(project_root)
    if source == "test":
        root = project / ".pytest-tmp" / "experiments" / kind / experiment_id
    else:
        root = Path(output_root) / kind / experiment_id
    return ExperimentLocation(
        experiment_id=experiment_id,
        kind=kind,
        source_type=source,
        root=root,
    )


def write_experiment_manifest(
    root: str | Path,
    *,
    experiment_id: str,
    kind: str,
    config: dict[str, Any],
    source_type: Optional[str] = None,
    status: str = "completed",
    dataset_hash: str = "",
    artifacts: Optional[dict[str, Any]] = None,
    metrics: Optional[dict[str, Any]] = None,
    project_root: Optional[str | Path] = None,
) -> str:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    revision, branch, dirty = _git_state(Path(project_root) if project_root else root)
    canonical = json.dumps(config, ensure_ascii=False, sort_keys=True, default=str)
    manifest = ExperimentManifest(
        experiment_id=experiment_id,
        kind=kind,
        source_type=detect_experiment_source(source_type),
        generated_at=datetime.now().isoformat(),
        status=status,
        config_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20],
        config=config,
        dataset_hash=dataset_hash,
        code_revision=revision,
        branch=branch,
        dirty_worktree=dirty,
        artifacts=dict(artifacts or {}),
        metrics=dict(metrics or {}),
    )
    path = root / "experiment_manifest.json"
    path.write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return str(path)


def _git_state(start: Path) -> tuple[str, str, Optional[bool]]:
    cwd = start if start.is_dir() else start.parent
    for candidate in (cwd, *cwd.parents):
        if (candidate / ".git").exists():
            cwd = candidate
            break
    try:
        revision = _git(cwd, "rev-parse", "HEAD")
        branch = _git(cwd, "branch", "--show-current")
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        dirty = bool(status.stdout.strip()) if status.returncode == 0 else None
        return revision, branch, dirty
    except (OSError, subprocess.SubprocessError):
        return "", "", None


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True,
        timeout=2, check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""
