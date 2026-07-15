"""
新闻快照归档

把新闻分析师每次运行时看到的原始新闻、预处理结果、证据包和最终预测
保存为可回放 JSON 快照，供新闻面历史校准使用。
"""

import json
import logging
import os
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Iterable, Optional

from src.core.prediction_target import default_target_spec
from src.data.point_in_time_lineage import CURRENT_CAPTURE, validate_point_in_time_write

logger = logging.getLogger(__name__)


class NewsSnapshotArchive:
    """新闻快照读写器。"""

    SCHEMA_VERSION = "news_snapshot.v2"

    def __init__(self, root_dir: Optional[str | Path] = None, enabled: Optional[bool] = None):
        if root_dir is None:
            from config.settings import get_settings

            root_dir = get_settings().data_dir / "news_snapshots"
        self.root_dir = Path(root_dir)
        if enabled is None:
            enabled = os.getenv("NEWS_SNAPSHOT_ARCHIVE", "true").lower() not in (
                "0",
                "false",
                "no",
            )
        self.enabled = enabled

    def save_analysis_snapshot(
        self,
        target: str,
        timeframe: str,
        news_data: dict,
        result=None,
        step_signals: Optional[dict] = None,
        prediction_id: Optional[str] = None,
        as_of: Optional[str | datetime] = None,
        source_kind: str = CURRENT_CAPTURE,
        lineage: Optional[dict] = None,
    ) -> dict:
        """保存一次新闻分析快照，返回快照元信息。"""
        if not self.enabled:
            return {}

        archived_at = datetime.now().replace(microsecond=0)
        as_of_dt = self._parse_dt(as_of) if as_of else archived_at
        lineage_payload = validate_point_in_time_write(
            as_of=as_of_dt,
            collected_at=archived_at,
            source_kind=source_kind,
            lineage=lineage,
        )
        symbol = (
            news_data.get("_resolved_symbol")
            or news_data.get("symbol")
            or target
            or "unknown"
        )
        market = news_data.get("_market") or "unknown"
        snapshot_id = self._snapshot_id(symbol, as_of_dt, timeframe)
        analysis_payload = self._result_payload(result)
        data_summary = analysis_payload.get("data_summary") or {}
        evidence = data_summary.get("evidence") or {}
        predicted_direction = analysis_payload.get("direction")
        predicted_confidence = analysis_payload.get("confidence")
        prediction_target = (
            analysis_payload.get("prediction_target")
            or default_target_spec(timeframe, target=target, market=market).to_dict()
        )

        snapshot = {
            "schema_version": self.SCHEMA_VERSION,
            "snapshot_id": snapshot_id,
            "archived_at": archived_at.isoformat(),
            "target": target,
            "symbol": symbol,
            "name": news_data.get("_resolved_name") or news_data.get("company_name"),
            "market": market,
            "timeframe": timeframe,
            "as_of": as_of_dt.isoformat(),
            "date": as_of_dt.date().isoformat(),
            "valid_date": self._valid_date(as_of_dt, timeframe).date().isoformat(),
            "prediction_target": self._sanitize(prediction_target),
            "prediction_id": prediction_id,
            "news_data": self._sanitize(news_data),
            "step_signals": self._sanitize(step_signals or {}),
            "analysis_result": analysis_payload,
            "evidence": self._sanitize(evidence),
            "predicted_direction": predicted_direction,
            "predicted_confidence": predicted_confidence,
            "news_count": news_data.get(
                "news_count",
                (news_data.get("_data_quality") or {}).get("news_count", 0),
            ),
            "sources_used": news_data.get(
                "sources_used",
                (news_data.get("_data_quality") or {}).get("sources", []),
            ),
            "source_kind": source_kind,
            "lineage": self._sanitize(lineage_payload),
        }

        path = self._snapshot_path(symbol, as_of_dt, snapshot_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2, default=self._json_default),
            encoding="utf-8",
        )
        self._append_index(snapshot, path)
        logger.info("新闻快照已归档: %s", path)
        return {
            "snapshot_id": snapshot_id,
            "path": str(path),
            "as_of": snapshot["as_of"],
            "valid_date": snapshot["valid_date"],
        }

    def attach_prediction_id(
        self,
        snapshot_ref: str | Path | dict,
        prediction_id: str,
    ) -> Optional[dict]:
        """把 PredictionStore id 追加回快照文件。"""
        path = self._resolve_snapshot_path(snapshot_ref)
        if not path or not path.exists():
            return None
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        snapshot["prediction_id"] = prediction_id
        snapshot["prediction_attached_at"] = datetime.now().replace(microsecond=0).isoformat()
        path.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2, default=self._json_default),
            encoding="utf-8",
        )
        return snapshot

    def load_snapshots(
        self,
        path: Optional[str | Path] = None,
        target: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> list[dict]:
        """读取目录、单个 JSON 或 JSONL 中的新闻快照。"""
        source = Path(path) if path else self.root_dir
        if not source.exists():
            return []

        snapshots: list[dict] = []
        for snapshot in self._read_snapshot_source(source):
            if self._matches(snapshot, target, start_date, end_date):
                snapshots.append(snapshot)
        return sorted(
            snapshots,
            key=lambda item: (
                str(item.get("as_of") or item.get("date") or ""),
                str(item.get("target") or item.get("symbol") or ""),
            ),
        )

    def export_jsonl(
        self,
        output_path: str | Path,
        snapshots: Iterable[dict],
    ) -> Path:
        """把快照集合导出为 JSONL，便于批量回放和版本保存。"""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as f:
            for snapshot in snapshots:
                f.write(json.dumps(snapshot, ensure_ascii=False, default=self._json_default))
                f.write("\n")
        return output_path

    def _append_index(self, snapshot: dict, path: Path) -> None:
        index_path = self.root_dir / "index.jsonl"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "schema_version": snapshot.get("schema_version"),
            "snapshot_id": snapshot.get("snapshot_id"),
            "archived_at": snapshot.get("archived_at"),
            "target": snapshot.get("target"),
            "symbol": snapshot.get("symbol"),
            "market": snapshot.get("market"),
            "timeframe": snapshot.get("timeframe"),
            "as_of": snapshot.get("as_of"),
            "valid_date": snapshot.get("valid_date"),
            "prediction_target": snapshot.get("prediction_target"),
            "prediction_id": snapshot.get("prediction_id"),
            "predicted_direction": snapshot.get("predicted_direction"),
            "predicted_confidence": snapshot.get("predicted_confidence"),
            "news_count": snapshot.get("news_count"),
            "source_count": len(snapshot.get("sources_used") or []),
            "source_kind": snapshot.get("source_kind"),
            "lineage": snapshot.get("lineage"),
            "path": str(path),
        }
        with index_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=self._json_default))
            f.write("\n")

    def _read_snapshot_source(self, source: Path) -> list[dict]:
        if source.is_dir():
            snapshots: list[dict] = []
            for file in sorted(source.rglob("*.json")):
                if file.name == "index.json":
                    continue
                snapshots.extend(self._read_snapshot_file(file))
            for file in sorted(source.rglob("*.jsonl")):
                if file.name == "index.jsonl":
                    continue
                snapshots.extend(self._read_snapshot_file(file))
            return snapshots
        return self._read_snapshot_file(source)

    def _read_snapshot_file(self, file: Path) -> list[dict]:
        try:
            if file.suffix == ".jsonl":
                snapshots = []
                for line in file.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line:
                        snapshots.append(json.loads(line))
                return snapshots

            payload = json.loads(file.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                return payload
            if isinstance(payload, dict) and isinstance(payload.get("snapshots"), list):
                return payload["snapshots"]
            if isinstance(payload, dict):
                return [payload]
        except Exception as e:
            logger.debug("新闻快照读取跳过 %s: %s", file, e)
        return []

    def _matches(
        self,
        snapshot: dict,
        target: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
    ) -> bool:
        if target:
            identifiers = {
                str(snapshot.get("target") or "").upper(),
                str(snapshot.get("symbol") or "").upper(),
                str((snapshot.get("news_data") or {}).get("symbol") or "").upper(),
            }
            if target.upper() not in identifiers:
                return False
        as_of = str(snapshot.get("as_of") or snapshot.get("date") or "")[:10]
        if start_date and as_of and as_of < start_date:
            return False
        if end_date and as_of and as_of > end_date:
            return False
        return True

    def _resolve_snapshot_path(self, snapshot_ref: str | Path | dict) -> Optional[Path]:
        if isinstance(snapshot_ref, dict):
            path = snapshot_ref.get("path")
            if path:
                return Path(path)
            snapshot_id = snapshot_ref.get("snapshot_id")
        else:
            ref = Path(snapshot_ref)
            if ref.exists():
                return ref
            snapshot_id = str(snapshot_ref)
        if not snapshot_id:
            return None
        matches = list(self.root_dir.rglob(f"{snapshot_id}.json"))
        return matches[0] if matches else None

    @staticmethod
    def _result_payload(result) -> dict:
        if result is None:
            return {}
        if isinstance(result, dict):
            return NewsSnapshotArchive._sanitize(result)
        if hasattr(result, "to_dict"):
            return NewsSnapshotArchive._sanitize(result.to_dict())
        return NewsSnapshotArchive._sanitize(result)

    @staticmethod
    def _sanitize(value):
        try:
            json.dumps(value, ensure_ascii=False, default=NewsSnapshotArchive._json_default)
            return value
        except TypeError:
            return json.loads(
                json.dumps(value, ensure_ascii=False, default=NewsSnapshotArchive._json_default)
            )

    @staticmethod
    def _json_default(value):
        if isinstance(value, (datetime,)):
            return value.isoformat()
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, Enum):
            return value.value
        if is_dataclass(value):
            return asdict(value)
        return str(value)

    @staticmethod
    def _parse_dt(value: str | datetime) -> datetime:
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value)[:19])

    @staticmethod
    def _valid_date(as_of: datetime, timeframe: str) -> datetime:
        return as_of + timedelta(days=default_target_spec(timeframe).horizon_calendar_days)

    @staticmethod
    def _safe_slug(value: str) -> str:
        safe = str(value or "unknown").strip().replace(" ", "_")
        for ch in '/\\:*?"<>|()':
            safe = safe.replace(ch, "_")
        return safe or "unknown"

    @classmethod
    def _snapshot_id(cls, symbol: str, as_of: datetime, timeframe: str) -> str:
        return "_".join([
            cls._safe_slug(symbol),
            as_of.strftime("%Y%m%d_%H%M%S"),
            cls._safe_slug(timeframe),
            uuid.uuid4().hex[:8],
        ])

    def _snapshot_path(self, symbol: str, as_of: datetime, snapshot_id: str) -> Path:
        return (
            self.root_dir
            / self._safe_slug(symbol)
            / as_of.strftime("%Y%m%d")
            / f"{snapshot_id}.json"
        )
