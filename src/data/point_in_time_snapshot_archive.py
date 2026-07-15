"""
Point-in-time 历史快照归档。

用于保存基本面、行业、宏观等非价格类 Agent 在某个 as_of 时点可见的
结构化数据。回测时只读取快照中的 data 字段，避免使用当前数据倒灌过去。
"""

from __future__ import annotations

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


class PointInTimeSnapshotArchive:
    """基本面/行业/宏观 point-in-time 快照读写器。"""

    SCHEMA_VERSION = "point_in_time_snapshot.v2"

    AGENT_ALIASES = {
        "fundamental": "公司前景分析师",
        "company": "公司前景分析师",
        "公司前景": "公司前景分析师",
        "公司前景分析师": "公司前景分析师",
        "industry": "行业对比分析师",
        "行业对比": "行业对比分析师",
        "行业对比分析师": "行业对比分析师",
        "macro": "国际形势分析师",
        "international": "国际形势分析师",
        "国际形势": "国际形势分析师",
        "国际形势分析师": "国际形势分析师",
    }

    def __init__(self, root_dir: Optional[str | Path] = None, enabled: Optional[bool] = None):
        if root_dir is None:
            from config.settings import get_settings

            root_dir = get_settings().data_dir / "point_in_time_snapshots"
        self.root_dir = Path(root_dir)
        if enabled is None:
            enabled = os.getenv("POINT_IN_TIME_SNAPSHOT_ARCHIVE", "true").lower() not in (
                "0",
                "false",
                "no",
            )
        self.enabled = enabled

    def save_snapshot(
        self,
        agent_name: str,
        target: str,
        timeframe: str,
        data: dict,
        market: str = "",
        symbol: str = "",
        name: str = "",
        stock_context: Optional[dict] = None,
        as_of: Optional[str | datetime] = None,
        valid_date: Optional[str] = None,
        analysis_result: Optional[dict] = None,
        predicted_direction: Optional[str] = None,
        predicted_confidence: Optional[float] = None,
        source_kind: str = CURRENT_CAPTURE,
        lineage: Optional[dict] = None,
    ) -> dict:
        """保存一个 point-in-time 数据快照，返回元信息。"""
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
        agent_name = self.normalize_agent_name(agent_name)
        symbol = symbol or data.get("_resolved_symbol") or data.get("symbol") or target
        market = market or data.get("_market") or data.get("market") or ""
        name = name or data.get("_resolved_name") or data.get("company_name") or ""
        analysis_payload = self._sanitize(analysis_result or {})
        prediction_target = (
            analysis_payload.get("prediction_target")
            if isinstance(analysis_payload, dict)
            else None
        ) or default_target_spec(timeframe, target=target, market=market).to_dict()
        valid_date = valid_date or self._valid_date(as_of_dt, timeframe).date().isoformat()
        snapshot_id = self._snapshot_id(agent_name, symbol, as_of_dt, timeframe)

        snapshot = {
            "schema_version": self.SCHEMA_VERSION,
            "snapshot_id": snapshot_id,
            "archived_at": archived_at.isoformat(),
            "agent_name": agent_name,
            "target": target,
            "symbol": symbol,
            "name": name,
            "market": market,
            "timeframe": timeframe,
            "as_of": as_of_dt.isoformat(),
            "date": as_of_dt.date().isoformat(),
            "valid_date": valid_date,
            "prediction_target": self._sanitize(prediction_target),
            "data": self._sanitize(data),
            "stock_context": self._sanitize(stock_context or {}),
            "analysis_result": analysis_payload,
            "predicted_direction": predicted_direction,
            "predicted_confidence": predicted_confidence,
            "source_kind": source_kind,
            "lineage": self._sanitize(lineage_payload),
        }

        path = self._snapshot_path(agent_name, symbol, as_of_dt, snapshot_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2, default=self._json_default),
            encoding="utf-8",
        )
        self._append_index(snapshot, path)
        logger.info("point-in-time 快照已归档: %s", path)
        return {
            "snapshot_id": snapshot_id,
            "path": str(path),
            "agent_name": agent_name,
            "as_of": snapshot["as_of"],
            "valid_date": snapshot["valid_date"],
        }

    def load_snapshots(
        self,
        path: Optional[str | Path] = None,
        agent_name: Optional[str] = None,
        target: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> list[dict]:
        """读取目录、单个 JSON 或 JSONL 中的 point-in-time 快照。"""
        source = Path(path) if path else self.root_dir
        if not source.exists():
            return []

        normalized_agent = self.normalize_agent_name(agent_name) if agent_name else None
        snapshots: list[dict] = []
        for snapshot in self._read_snapshot_source(source):
            if self._matches(snapshot, normalized_agent, target, start_date, end_date):
                snapshots.append(snapshot)
        return sorted(
            snapshots,
            key=lambda item: (
                str(item.get("as_of") or item.get("date") or ""),
                str(item.get("agent_name") or ""),
                str(item.get("target") or item.get("symbol") or ""),
            ),
        )

    def export_jsonl(self, output_path: str | Path, snapshots: Iterable[dict]) -> Path:
        """导出为 JSONL，便于版本化和跨设备传输。"""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as f:
            for snapshot in snapshots:
                f.write(json.dumps(snapshot, ensure_ascii=False, default=self._json_default))
                f.write("\n")
        return output_path

    @classmethod
    def normalize_agent_name(cls, value: Optional[str]) -> str:
        raw = str(value or "").strip()
        return cls.AGENT_ALIASES.get(raw, raw)

    def _append_index(self, snapshot: dict, path: Path) -> None:
        index_path = self.root_dir / "index.jsonl"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "schema_version": snapshot.get("schema_version"),
            "snapshot_id": snapshot.get("snapshot_id"),
            "archived_at": snapshot.get("archived_at"),
            "agent_name": snapshot.get("agent_name"),
            "target": snapshot.get("target"),
            "symbol": snapshot.get("symbol"),
            "market": snapshot.get("market"),
            "timeframe": snapshot.get("timeframe"),
            "as_of": snapshot.get("as_of"),
            "valid_date": snapshot.get("valid_date"),
            "prediction_target": snapshot.get("prediction_target"),
            "predicted_direction": snapshot.get("predicted_direction"),
            "predicted_confidence": snapshot.get("predicted_confidence"),
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
            if isinstance(payload, dict):
                for key in ("snapshots", "items", "data"):
                    if isinstance(payload.get(key), list):
                        return payload[key]
                return [payload]
        except Exception as e:
            logger.debug("point-in-time 快照读取跳过 %s: %s", file, e)
        return []

    def _matches(
        self,
        snapshot: dict,
        agent_name: Optional[str],
        target: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
    ) -> bool:
        if agent_name:
            snapshot_agent = self.normalize_agent_name(snapshot.get("agent_name"))
            if snapshot_agent != agent_name:
                return False
        if target:
            identifiers = {
                str(snapshot.get("target") or "").upper(),
                str(snapshot.get("symbol") or "").upper(),
                str((snapshot.get("data") or {}).get("symbol") or "").upper(),
                str((snapshot.get("data") or {}).get("_resolved_symbol") or "").upper(),
            }
            if target.upper() not in identifiers:
                return False
        as_of = str(snapshot.get("as_of") or snapshot.get("date") or "")[:10]
        if start_date and as_of and as_of < start_date:
            return False
        if end_date and as_of and as_of > end_date:
            return False
        return True

    def _snapshot_path(
        self,
        agent_name: str,
        symbol: str,
        as_of: datetime,
        snapshot_id: str,
    ) -> Path:
        agent_slug = self._agent_slug(agent_name)
        return self.root_dir / agent_slug / str(symbol) / as_of.strftime("%Y%m%d") / f"{snapshot_id}.json"

    @staticmethod
    def _snapshot_id(agent_name: str, symbol: str, as_of: datetime, timeframe: str) -> str:
        raw = f"{agent_name}|{symbol}|{as_of.isoformat()}|{timeframe}"
        return uuid.uuid5(uuid.NAMESPACE_URL, raw).hex[:16]

    @staticmethod
    def _agent_slug(agent_name: str) -> str:
        mapping = {
            "公司前景分析师": "fundamental",
            "行业对比分析师": "industry",
            "国际形势分析师": "macro",
        }
        return mapping.get(agent_name, "unknown")

    @staticmethod
    def _parse_dt(value: str | datetime) -> datetime:
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")[:19])

    @staticmethod
    def _valid_date(as_of: datetime, timeframe: str) -> datetime:
        return as_of + timedelta(days=default_target_spec(timeframe).horizon_calendar_days)

    @classmethod
    def _sanitize(cls, value):
        if is_dataclass(value):
            return cls._sanitize(asdict(value))
        if isinstance(value, dict):
            return {str(k): cls._sanitize(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._sanitize(v) for v in value]
        if isinstance(value, Enum):
            return value.value
        if hasattr(value, "item"):
            try:
                return value.item()
            except Exception:
                pass
        return value

    @staticmethod
    def _json_default(value):
        if isinstance(value, datetime):
            return value.isoformat()
        if hasattr(value, "item"):
            try:
                return value.item()
            except Exception:
                pass
        return str(value)
