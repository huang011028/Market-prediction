"""Persistent raw-history cache used by point-in-time Quant dataset builds."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd


class QuantPriceCache:
    """Cache normalized OHLCV frames without changing their adjustment basis."""

    def __init__(self, root_dir: Optional[str | Path] = None):
        if root_dir is None:
            from config.settings import get_settings

            root_dir = get_settings().data_dir / "quant" / "price_cache"
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def load(
        self,
        symbol: str,
        market: str,
        start_date,
        end_date,
        *,
        allow_partial: bool = False,
    ) -> Optional[pd.DataFrame]:
        data_path, metadata_path = self._paths(symbol, market)
        if not data_path.exists() or not metadata_path.exists():
            return None
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            requested_start = pd.Timestamp(start_date).normalize()
            requested_end = pd.Timestamp(end_date).normalize()
            cached_start = pd.Timestamp(metadata["first_date"]).normalize()
            cached_end = pd.Timestamp(metadata["last_date"]).normalize()
            if not allow_partial and (
                cached_start > requested_start or cached_end < requested_end
            ):
                return None
            frame = pd.read_parquet(data_path)
            if "date" in frame.columns:
                frame["date"] = pd.to_datetime(frame["date"])
                frame = frame.set_index("date")
            frame.index = pd.to_datetime(frame.index).tz_localize(None)
            sliced = frame[
                (frame.index >= requested_start) & (frame.index <= requested_end)
            ].sort_index()
            return sliced if not sliced.empty else None
        except Exception:
            return None

    def save(self, symbol: str, market: str, frame: pd.DataFrame, *, source: str) -> None:
        if frame is None or frame.empty:
            return
        data_path, metadata_path = self._paths(symbol, market)
        data_path.parent.mkdir(parents=True, exist_ok=True)
        normalized = frame.copy().sort_index()
        normalized.index = pd.to_datetime(normalized.index).tz_localize(None)
        if data_path.exists():
            try:
                existing = pd.read_parquet(data_path)
                if "date" in existing.columns:
                    existing["date"] = pd.to_datetime(existing["date"])
                    existing = existing.set_index("date")
                existing.index = pd.to_datetime(existing.index).tz_localize(None)
                normalized = pd.concat([existing, normalized]).sort_index()
                normalized = normalized[~normalized.index.duplicated(keep="last")]
            except Exception:
                pass
        normalized.index.name = "date"
        normalized.reset_index().to_parquet(data_path, index=False)
        metadata_path.write_text(
            json.dumps({
                "symbol": str(symbol).upper(),
                "market": str(market).upper(),
                "first_date": normalized.index.min().date().isoformat(),
                "last_date": normalized.index.max().date().isoformat(),
                "rows": len(normalized),
                "source": source,
                "cached_at": datetime.now().isoformat(),
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def status(self) -> dict:
        metadata_files = list(self.root_dir.rglob("*.json"))
        rows = 0
        latest = ""
        for path in metadata_files:
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
                rows += int(item.get("rows") or 0)
                latest = max(latest, str(item.get("cached_at") or ""))
            except Exception:
                continue
        return {
            "root_dir": str(self.root_dir),
            "symbols": len(metadata_files),
            "rows": rows,
            "latest_cache_at": latest or None,
        }

    def _paths(self, symbol: str, market: str) -> tuple[Path, Path]:
        safe_symbol = "".join(ch for ch in str(symbol).upper() if ch.isalnum() or ch in "-_")
        root = self.root_dir / str(market).upper()
        return root / f"{safe_symbol}.parquet", root / f"{safe_symbol}.json"
