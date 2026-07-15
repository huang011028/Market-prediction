"""Point-in-time investable universe backed by listing effective intervals."""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional


@dataclass
class UniverseInstrument:
    market: str
    symbol: str
    name: str
    exchange: str
    list_date: str
    delist_date: Optional[str] = None
    board: str = ""
    industry: str = ""
    source: str = ""
    source_timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class InvestableUniverseStore:
    def __init__(self, db_path: Optional[str | Path] = None):
        if db_path is None:
            from config.settings import get_settings

            db_path = get_settings().data_dir / "quant" / "universe.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS instruments (
                    market TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    board TEXT,
                    industry TEXT,
                    list_date TEXT NOT NULL,
                    delist_date TEXT,
                    source TEXT NOT NULL,
                    source_timestamp TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (market, symbol)
                );
                CREATE INDEX IF NOT EXISTS idx_universe_effective
                    ON instruments(market, list_date, delist_date);
                CREATE TABLE IF NOT EXISTS refresh_runs (
                    run_id TEXT PRIMARY KEY,
                    market TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    records INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    details TEXT
                );
                """
            )
            conn.execute(
                """DELETE FROM instruments
                   WHERE market='A' AND (
                       (exchange='SSE' AND symbol LIKE '900%') OR
                       (exchange='SZSE' AND symbol LIKE '200%')
                   )"""
            )
            conn.commit()

    def upsert_many(self, records: Iterable[UniverseInstrument]) -> int:
        values = list(records)
        now = datetime.now().isoformat()
        with self._conn() as conn:
            for item in values:
                conn.execute(
                    """INSERT INTO instruments (
                           market, symbol, name, exchange, board, industry,
                           list_date, delist_date, source, source_timestamp, updated_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(market, symbol) DO UPDATE SET
                           name=excluded.name,
                           exchange=excluded.exchange,
                           board=CASE WHEN excluded.board<>'' THEN excluded.board ELSE instruments.board END,
                           industry=CASE WHEN excluded.industry<>'' THEN excluded.industry ELSE instruments.industry END,
                           list_date=CASE WHEN excluded.list_date<>'' THEN excluded.list_date ELSE instruments.list_date END,
                           delist_date=COALESCE(excluded.delist_date, instruments.delist_date),
                           source=excluded.source,
                           source_timestamp=excluded.source_timestamp,
                           updated_at=excluded.updated_at""",
                    (
                        item.market.upper(), item.symbol, item.name, item.exchange,
                        item.board, item.industry, item.list_date, item.delist_date,
                        item.source, item.source_timestamp or now, now,
                    ),
                )
            conn.commit()
        return len(values)

    def eligible_on(
        self,
        as_of: str | date | datetime,
        *,
        market: str = "A",
        min_listing_days: int = 120,
        limit: int = 0,
        sample_seed: Optional[str] = None,
        stratify: bool = False,
    ) -> list[dict[str, Any]]:
        cutoff = _iso_date(as_of)
        listing_cutoff = (
            date.fromisoformat(cutoff) - timedelta(days=max(0, int(min_listing_days)))
        ).isoformat()
        sql = """SELECT * FROM instruments
                 WHERE market=? AND list_date<=?
                   AND (delist_date IS NULL OR delist_date>?)
                 ORDER BY symbol"""
        params: list[Any] = [market.upper(), listing_cutoff, cutoff]
        if limit and not sample_seed:
            sql += " LIMIT ?"
            params.append(max(1, int(limit)))
        with self._conn() as conn:
            rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
        if limit and sample_seed:
            if stratify:
                rows = _stratified_sample(rows, cutoff, max(1, int(limit)), sample_seed)
            else:
                rows.sort(key=lambda row: hashlib.sha256(
                    f"{sample_seed}|{row['symbol']}".encode("utf-8")
                ).hexdigest())
                rows = rows[:max(1, int(limit))]
        return rows

    def status(self) -> dict[str, Any]:
        with self._conn() as conn:
            total = conn.execute(
                """SELECT COUNT(*) total,
                          SUM(delist_date IS NOT NULL) delisted,
                          MIN(list_date) first_list_date,
                          MAX(source_timestamp) last_refresh
                   FROM instruments"""
            ).fetchone()
            exchanges = conn.execute(
                """SELECT exchange, COUNT(*) records,
                          SUM(delist_date IS NOT NULL) delisted
                   FROM instruments GROUP BY exchange ORDER BY exchange"""
            ).fetchall()
        return {
            "db_path": str(self.db_path),
            "total": int(total["total"] or 0),
            "delisted": int(total["delisted"] or 0),
            "first_list_date": total["first_list_date"],
            "last_refresh": total["last_refresh"],
            "exchanges": [dict(row) for row in exchanges],
        }

    def sampled_union(
        self,
        start_date: str,
        end_date: str,
        *,
        interval_days: int = 7,
        market: str = "A",
        min_listing_days: int = 120,
        limit: int = 60,
        sample_seed: str = "quant-v3.1-a-share",
        stratify: bool = True,
    ) -> list[dict[str, Any]]:
        start = date.fromisoformat(start_date[:10])
        end = date.fromisoformat(end_date[:10])
        if end < start:
            raise ValueError("end_date 不能早于 start_date")
        by_symbol: dict[str, dict[str, Any]] = {}
        current = start
        while current <= end:
            for row in self.eligible_on(
                current,
                market=market,
                min_listing_days=min_listing_days,
                limit=limit,
                sample_seed=sample_seed,
                stratify=stratify,
            ):
                by_symbol[row["symbol"]] = row
            current += timedelta(days=max(1, int(interval_days)))
        return [by_symbol[symbol] for symbol in sorted(by_symbol)]


class AShareUniverseBuilder:
    """Build an A-share instrument master from exchange lists and delist lists."""

    def __init__(self, store: Optional[InvestableUniverseStore] = None):
        self.store = store or InvestableUniverseStore()

    async def refresh(self, *, include_bse: bool = False) -> dict[str, Any]:
        records, errors = await asyncio.to_thread(self._fetch_records, include_bse)
        saved = self.store.upsert_many(records)
        return {
            "saved": saved,
            "errors": errors,
            "coverage": {"SSE": True, "SZSE": True, "BSE": include_bse},
            "status": self.store.status(),
        }

    @staticmethod
    def _fetch_records(include_bse: bool = False) -> tuple[list[UniverseInstrument], list[dict[str, str]]]:
        import akshare as ak

        collected = datetime.now().replace(microsecond=0).isoformat()
        records: dict[str, UniverseInstrument] = {}
        errors: list[dict[str, str]] = []

        def load(name: str, kwargs: dict, normalizer) -> None:
            try:
                frame = getattr(ak, name)(**kwargs)
                for item in normalizer(frame, collected):
                    existing = records.get(item.symbol)
                    if existing and item.delist_date:
                        item.industry = item.industry or existing.industry
                        item.board = item.board or existing.board
                    records[item.symbol] = item
            except Exception as exc:
                errors.append({"source": name, "reason": str(exc)})

        load("stock_info_sh_name_code", {"symbol": "主板A股"}, _normalize_sh_list)
        load("stock_info_sh_name_code", {"symbol": "科创板"}, _normalize_sh_list)
        load("stock_info_sz_name_code", {"symbol": "A股列表"}, _normalize_sz_list)
        if include_bse:
            load("stock_info_bj_name_code", {}, _normalize_bj_list)
        else:
            errors.append({"source": "stock_info_bj_name_code", "reason": "默认跳过不稳定的 BSE 接口"})
        load("stock_info_sh_delist", {"symbol": "全部"}, _normalize_sh_delist)
        load("stock_info_sz_delist", {"symbol": "终止上市公司"}, _normalize_sz_delist)
        return list(records.values()), errors


def _normalize_sh_list(frame, collected: str) -> list[UniverseInstrument]:
    result = []
    for _, row in frame.iterrows():
        symbol = _symbol(row.get("证券代码"))
        if not symbol or not _is_a_share_symbol(symbol, "SSE"):
            continue
        result.append(UniverseInstrument(
            market="A", symbol=symbol, name=str(row.get("证券简称") or ""),
            exchange="SSE", board="STAR" if symbol.startswith("688") else "MAIN",
            list_date=_iso_date(row.get("上市日期")), source="sse_stock_list",
            source_timestamp=collected,
        ))
    return result


def _normalize_sz_list(frame, collected: str) -> list[UniverseInstrument]:
    result = []
    for _, row in frame.iterrows():
        symbol = _symbol(row.get("A股代码"))
        if not symbol or not _is_a_share_symbol(symbol, "SZSE"):
            continue
        result.append(UniverseInstrument(
            market="A", symbol=symbol, name=str(row.get("A股简称") or ""),
            exchange="SZSE", board=str(row.get("板块") or ""),
            industry=str(row.get("所属行业") or ""),
            list_date=_iso_date(row.get("A股上市日期")), source="szse_stock_list",
            source_timestamp=collected,
        ))
    return result


def _normalize_bj_list(frame, collected: str) -> list[UniverseInstrument]:
    result = []
    code_column = next((name for name in frame.columns if "代码" in str(name)), None)
    name_column = next((name for name in frame.columns if "简称" in str(name)), None)
    date_column = next((name for name in frame.columns if "上市日期" in str(name)), None)
    if not code_column or not date_column:
        return result
    for _, row in frame.iterrows():
        symbol = _symbol(row.get(code_column))
        if not symbol:
            continue
        result.append(UniverseInstrument(
            market="A", symbol=symbol, name=str(row.get(name_column) or ""),
            exchange="BSE", board="BSE", list_date=_iso_date(row.get(date_column)),
            source="bse_stock_list", source_timestamp=collected,
        ))
    return result


def _normalize_sh_delist(frame, collected: str) -> list[UniverseInstrument]:
    return _normalize_delist(
        frame, "公司代码", "公司简称", "上市日期", "暂停上市日期",
        "SSE", "sse_delist_list", collected,
    )


def _normalize_sz_delist(frame, collected: str) -> list[UniverseInstrument]:
    return _normalize_delist(
        frame, "证券代码", "证券简称", "上市日期", "终止上市日期",
        "SZSE", "szse_delist_list", collected,
    )


def _normalize_delist(frame, code_col, name_col, list_col, delist_col, exchange, source, collected):
    result = []
    for _, row in frame.iterrows():
        symbol = _symbol(row.get(code_col))
        if not symbol or not _is_a_share_symbol(symbol, exchange):
            continue
        result.append(UniverseInstrument(
            market="A", symbol=symbol, name=str(row.get(name_col) or ""),
            exchange=exchange, list_date=_iso_date(row.get(list_col)),
            delist_date=_iso_date(row.get(delist_col)), source=source,
            source_timestamp=collected,
        ))
    return result


def _symbol(value: Any) -> str:
    text = str(value or "").strip().split(".")[0]
    return text.zfill(6) if text.isdigit() else ""


def _iso_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()[:10].replace("/", "-")
    if not text or text.lower() in {"nat", "none", "nan"}:
        return ""
    return date.fromisoformat(text).isoformat()


def _stratified_sample(
    rows: list[dict[str, Any]],
    as_of: str,
    limit: int,
    seed: str,
) -> list[dict[str, Any]]:
    """Round-robin deterministic exchange/board/listing-age strata."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        board = str(row.get("board") or "unknown").strip().upper()
        stratum = f"{row.get('exchange') or 'unknown'}|{board}"
        groups.setdefault(stratum, []).append(row)
    for stratum, values in groups.items():
        values.sort(key=lambda row: hashlib.sha256(
            f"{seed}|{stratum}|{row['symbol']}".encode("utf-8")
        ).hexdigest())
    stratum_order = sorted(groups, key=lambda value: hashlib.sha256(
        f"{seed}|{value}".encode("utf-8")
    ).hexdigest())
    selected: list[dict[str, Any]] = []
    offset = 0
    while len(selected) < min(limit, len(rows)):
        added = False
        for stratum in stratum_order:
            values = groups[stratum]
            if offset < len(values):
                selected.append(values[offset])
                added = True
                if len(selected) >= limit:
                    break
        if not added:
            break
        offset += 1
    return selected


def _is_a_share_symbol(symbol: str, exchange: str) -> bool:
    if exchange == "SSE":
        return symbol.startswith(("600", "601", "603", "605", "688", "689"))
    if exchange == "SZSE":
        return symbol.startswith(("000", "001", "002", "003", "300", "301"))
    if exchange == "BSE":
        return symbol.startswith(("4", "8", "92"))
    return False
