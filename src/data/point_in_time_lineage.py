"""Shared lineage validation for point-in-time data writes."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional


CURRENT_CAPTURE = "current_capture"
HISTORICAL_REPLAY = "historical_replay"
ALLOWED_SOURCE_KINDS = {CURRENT_CAPTURE, HISTORICAL_REPLAY}


def validate_point_in_time_write(
    *,
    as_of: str | datetime,
    collected_at: str | datetime,
    source_kind: str = CURRENT_CAPTURE,
    lineage: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Validate that a stored observation was available at ``as_of``.

    Current fetches may only use the collection date. Historical replay is a
    separate, explicit path and must carry source timestamps that were no later
    than the requested observation date.
    """
    kind = str(source_kind or CURRENT_CAPTURE).strip().lower()
    if kind not in ALLOWED_SOURCE_KINDS:
        raise ValueError(f"未知 PIT source_kind: {source_kind}")

    observed_date = _as_date(as_of, "as_of")
    collected_date = _as_date(collected_at, "collected_at")
    if observed_date > collected_date:
        raise ValueError("as_of 不能晚于 collected_at")

    payload = dict(lineage or {})
    if kind == CURRENT_CAPTURE:
        if observed_date != collected_date:
            raise ValueError("当前抓取的数据不能回填为历史 as_of")
        payload.setdefault("point_in_time_verified", True)
        payload.setdefault("source_timestamps", [observed_date.isoformat()])
    else:
        if payload.get("point_in_time_verified") is not True:
            raise ValueError("历史回放必须声明 point_in_time_verified=true")
        source_times = [value for value in payload.get("source_timestamps") or [] if value]
        if not source_times:
            raise ValueError("历史回放必须提供 source_timestamps")
        if any(_as_date(value, "source_timestamp") > observed_date for value in source_times):
            raise ValueError("历史回放包含晚于 as_of 的数据源时间")

    payload["source_kind"] = kind
    payload["as_of"] = observed_date.isoformat()
    payload["collected_at"] = collected_date.isoformat()
    return payload


def _as_date(value: str | datetime | date, field: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).replace("Z", "+00:00")[:10])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是 ISO 日期") from exc
