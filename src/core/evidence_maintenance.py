"""Automatic verification and point-in-time evidence collection."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from config.settings import get_settings
from src.core.snapshot_collection import CurrentSnapshotCollector, SnapshotCollectionConfig
from src.data.prediction_store import PredictionStore


@dataclass
class EvidenceMaintenanceConfig:
    collect_snapshots: bool = False
    targets: list[str] = field(default_factory=list)
    recent_target_limit: int = 30
    timeframe: str = "短期(1周)"
    news_mode: str = "evidence"
    max_snapshots: int = 0


@dataclass
class EvidenceMaintenanceReport:
    started_at: str
    completed_at: str
    queue_before: dict
    queue_after: dict
    verified_count: int
    collection: Optional[dict] = None
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class EvidenceMaintenanceRunner:
    """Close the production evidence loop without changing prompts or models."""

    def __init__(
        self,
        prediction_store: Optional[PredictionStore] = None,
        collector: Optional[CurrentSnapshotCollector] = None,
    ):
        self.prediction_store = prediction_store or PredictionStore()
        self.collector = collector or CurrentSnapshotCollector()

    async def run_once(
        self,
        config: Optional[EvidenceMaintenanceConfig] = None,
    ) -> EvidenceMaintenanceReport:
        config = config or EvidenceMaintenanceConfig()
        started_at = datetime.now().replace(microsecond=0).isoformat()
        queue_before = self.prediction_store.get_verification_queue_status()
        errors: list[str] = []
        verified_count = 0
        collection = None

        try:
            result = await asyncio.to_thread(self.prediction_store.verify_all)
            verified_count = int(result.get("verified") or 0)
        except Exception as exc:
            errors.append(f"到期验证失败: {exc}")

        if config.collect_snapshots:
            targets = list(dict.fromkeys(config.targets))
            if not targets:
                targets = self.prediction_store.get_recent_targets(config.recent_target_limit)
            if targets:
                try:
                    report = await self.collector.collect(SnapshotCollectionConfig(
                        targets=targets,
                        timeframe=config.timeframe,
                        news_mode=config.news_mode,
                        write_default_archives=True,
                        max_snapshots=config.max_snapshots,
                    ))
                    collection = report.to_dict()
                except Exception as exc:
                    errors.append(f"当前快照采集失败: {exc}")

        completed_at = datetime.now().replace(microsecond=0).isoformat()
        report = EvidenceMaintenanceReport(
            started_at=started_at,
            completed_at=completed_at,
            queue_before=queue_before,
            queue_after=self.prediction_store.get_verification_queue_status(),
            verified_count=verified_count,
            collection=collection,
            errors=errors,
        )
        self._save_report(report)
        return report

    @staticmethod
    def status() -> dict:
        settings = get_settings()
        store = PredictionStore()
        pit_status = _snapshot_directory_status(settings.data_dir / "point_in_time_snapshots")
        news_status = _snapshot_directory_status(settings.data_dir / "news_snapshots")
        last_report_path = settings.data_dir / "evidence_maintenance" / "latest.json"
        last_report = None
        if last_report_path.exists():
            try:
                last_report = json.loads(last_report_path.read_text(encoding="utf-8"))
            except Exception:
                last_report = None
        return {
            "verification_queue": store.get_verification_queue_status(),
            "point_in_time_snapshots": pit_status["count"],
            "news_snapshots": news_status["count"],
            "pit_agents": pit_status["groups"],
            "snapshot_latest_mtime": max(
                pit_status.get("latest_mtime") or "",
                news_status.get("latest_mtime") or "",
            ),
            "last_run": last_report,
        }

    @staticmethod
    def _save_report(report: EvidenceMaintenanceReport) -> Path:
        root = get_settings().data_dir / "evidence_maintenance"
        root.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
        timestamped = root / f"{report.completed_at.replace(':', '').replace('-', '')}.json"
        timestamped.write_text(payload, encoding="utf-8")
        latest = root / "latest.json"
        latest.write_text(payload, encoding="utf-8")
        return latest


def _snapshot_directory_status(root: Path) -> dict:
    if not root.exists():
        return {"count": 0, "groups": [], "latest_mtime": None}
    files = [path for path in root.rglob("*.json") if path.name != "index.json"]
    groups = sorted({
        path.relative_to(root).parts[0]
        for path in files
        if len(path.relative_to(root).parts) > 1
    })
    latest = max((path.stat().st_mtime for path in files), default=0.0)
    return {
        "count": len(files),
        "groups": groups,
        "latest_mtime": datetime.fromtimestamp(latest).isoformat() if latest else None,
    }
