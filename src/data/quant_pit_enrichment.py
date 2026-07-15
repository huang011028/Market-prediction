"""Point-in-time fundamental, announcement and industry features for Quant."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional


_INDUSTRY_SOURCE_LOCK = threading.Lock()


@dataclass
class FundamentalEvent:
    market: str
    symbol: str
    effective_date: str
    report_date: str
    features: dict[str, Any]
    source: str = "eastmoney_profit_statement"
    source_update_date: str = ""
    collected_at: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def event_id(self) -> str:
        raw = f"{self.market}|{self.symbol}|{self.effective_date}|{self.report_date}|{self.source}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


@dataclass
class PerformanceEvent:
    market: str
    symbol: str
    event_kind: str
    published_at: str
    report_date: str
    features: dict[str, Any]
    source: str
    collected_at: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def event_id(self) -> str:
        raw = (
            f"{self.market}|{self.symbol}|{self.event_kind}|{self.published_at}|"
            f"{self.report_date}|{self.source}"
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


@dataclass
class AnnouncementEvent:
    market: str
    symbol: str
    published_at: str
    title: str
    category: str
    source_url: str = ""
    source: str = "cninfo_disclosure"
    collected_at: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def event_id(self) -> str:
        raw = f"{self.market}|{self.symbol}|{self.published_at}|{self.title}|{self.source}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


@dataclass
class IndustryMembership:
    market: str
    symbol: str
    standard: str
    industry_l1: str
    industry_l2: str
    industry_l3: str
    effective_from: str
    effective_to: Optional[str] = None
    source: str = "cninfo_industry_change"
    collected_at: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def membership_id(self) -> str:
        raw = f"{self.market}|{self.symbol}|{self.standard}|{self.effective_from}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


class QuantPitEnrichmentStore:
    DEFAULT_INDUSTRY_STANDARD = "申银万国行业分类标准"

    def __init__(self, db_path: Optional[str | Path] = None):
        if db_path is None:
            from config.settings import get_settings

            db_path = get_settings().data_dir / "quant" / "pit_enrichment.db"
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
                CREATE TABLE IF NOT EXISTS fundamental_events (
                    event_id TEXT PRIMARY KEY, market TEXT NOT NULL, symbol TEXT NOT NULL,
                    effective_date TEXT NOT NULL, report_date TEXT NOT NULL,
                    source_update_date TEXT, features_json TEXT NOT NULL,
                    source TEXT NOT NULL, collected_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_fundamental_pit
                    ON fundamental_events(market, symbol, effective_date, report_date);
                CREATE TABLE IF NOT EXISTS performance_events (
                    event_id TEXT PRIMARY KEY, market TEXT NOT NULL, symbol TEXT NOT NULL,
                    event_kind TEXT NOT NULL, published_at TEXT NOT NULL,
                    report_date TEXT NOT NULL, features_json TEXT NOT NULL,
                    source TEXT NOT NULL, collected_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_performance_pit
                    ON performance_events(market, symbol, published_at, report_date, event_kind);
                CREATE TABLE IF NOT EXISTS announcement_events (
                    event_id TEXT PRIMARY KEY, market TEXT NOT NULL, symbol TEXT NOT NULL,
                    published_at TEXT NOT NULL, title TEXT NOT NULL, category TEXT NOT NULL,
                    source_url TEXT, source TEXT NOT NULL, collected_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_announcement_pit
                    ON announcement_events(market, symbol, published_at);
                CREATE TABLE IF NOT EXISTS industry_memberships (
                    membership_id TEXT PRIMARY KEY, market TEXT NOT NULL, symbol TEXT NOT NULL,
                    standard TEXT NOT NULL, industry_l1 TEXT, industry_l2 TEXT, industry_l3 TEXT,
                    effective_from TEXT NOT NULL, effective_to TEXT,
                    source TEXT NOT NULL, collected_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_industry_pit
                    ON industry_memberships(market, symbol, standard, effective_from, effective_to);
                """
            )

    def upsert_fundamentals(self, events: Iterable[FundamentalEvent]) -> int:
        values = list(events)
        with self._conn() as conn:
            for item in values:
                conn.execute(
                    """INSERT OR REPLACE INTO fundamental_events VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        item.event_id, item.market.upper(), item.symbol, item.effective_date,
                        item.report_date, item.source_update_date,
                        json.dumps(item.features, ensure_ascii=False, default=str),
                        item.source, item.collected_at,
                    ),
                )
            conn.commit()
        return len(values)

    def upsert_announcements(self, events: Iterable[AnnouncementEvent]) -> int:
        values = list(events)
        with self._conn() as conn:
            for item in values:
                conn.execute(
                    """INSERT OR REPLACE INTO announcement_events VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        item.event_id, item.market.upper(), item.symbol, item.published_at,
                        item.title, item.category, item.source_url, item.source, item.collected_at,
                    ),
                )
            conn.commit()
        return len(values)

    def upsert_performance_events(self, events: Iterable[PerformanceEvent]) -> int:
        values = list(events)
        with self._conn() as conn:
            for item in values:
                conn.execute(
                    """INSERT OR REPLACE INTO performance_events VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        item.event_id, item.market.upper(), item.symbol, item.event_kind,
                        item.published_at, item.report_date,
                        json.dumps(item.features, ensure_ascii=False, default=str),
                        item.source, item.collected_at,
                    ),
                )
            conn.commit()
        return len(values)

    def replace_industry_memberships(
        self,
        symbol: str,
        market: str,
        memberships: Iterable[IndustryMembership],
    ) -> int:
        values = list(memberships)
        standards = sorted({item.standard for item in values})
        with self._conn() as conn:
            for standard in standards:
                conn.execute(
                    "DELETE FROM industry_memberships WHERE market=? AND symbol=? AND standard=?",
                    (market.upper(), symbol, standard),
                )
            for item in values:
                conn.execute(
                    """INSERT OR REPLACE INTO industry_memberships VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        item.membership_id, item.market.upper(), item.symbol, item.standard,
                        item.industry_l1, item.industry_l2, item.industry_l3,
                        item.effective_from, item.effective_to, item.source, item.collected_at,
                    ),
                )
            conn.commit()
        return len(values)

    def features_as_of(
        self,
        symbol: str,
        as_of: str | date | datetime,
        *,
        market: str = "A",
        fundamental_max_age_days: int = 550,
        announcement_lookback_days: int = 90,
        industry_standard: str = DEFAULT_INDUSTRY_STANDARD,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        cutoff = _iso_date(as_of)
        cutoff_date = date.fromisoformat(cutoff)
        features: dict[str, Any] = {}
        lineage: dict[str, Any] = {
            "fundamental": None,
            "performance": None,
            "fundamental_quality": None,
            "announcements": {"events": 0, "latest_date": None},
            "industry": None,
        }
        with self._conn() as conn:
            fundamental = conn.execute(
                """SELECT * FROM fundamental_events
                   WHERE market=? AND symbol=? AND effective_date<=? AND report_date<=?
                   ORDER BY effective_date DESC, report_date DESC LIMIT 1""",
                (market.upper(), symbol, cutoff, cutoff),
            ).fetchone()
            if fundamental:
                age = (cutoff_date - date.fromisoformat(fundamental["effective_date"])).days
                if 0 <= age <= max(1, int(fundamental_max_age_days)):
                    payload = json.loads(fundamental["features_json"] or "{}")
                    features.update({f"fundamental__{key}": value for key, value in payload.items()})
                    features["fundamental__notice_age_days"] = age
                    features["fundamental__report_age_days"] = (
                        cutoff_date - date.fromisoformat(fundamental["report_date"])
                    ).days
                    features["fundamental__available"] = 1
                    lineage["fundamental"] = {
                        "effective_date": fundamental["effective_date"],
                        "report_date": fundamental["report_date"],
                        "source": fundamental["source"],
                    }

                    performance_rows = conn.execute(
                        """SELECT * FROM performance_events
                           WHERE market=? AND symbol=? AND report_date=?
                             AND published_at<=?
                           ORDER BY published_at DESC""",
                        (market.upper(), symbol, fundamental["report_date"], cutoff),
                    ).fetchall()
                    by_kind = {}
                    for row in performance_rows:
                        by_kind.setdefault(row["event_kind"], row)
                    guidance = by_kind.get("guidance")
                    flash = by_kind.get("flash")
                    actual_netprofit_yoy = _finite(payload.get("netprofit_yoy_pct"))
                    actual_eps = _finite(payload.get("basic_eps"))
                    if guidance:
                        guidance_payload = json.loads(guidance["features_json"] or "{}")
                        guidance_yoy = _finite(guidance_payload.get("netprofit_yoy_pct"))
                        features.update({
                            f"fundamental__guidance_{key}": value
                            for key, value in guidance_payload.items() if value is not None
                        })
                        features["fundamental__guidance_age_days"] = (
                            cutoff_date - date.fromisoformat(guidance["published_at"][:10])
                        ).days
                        if actual_netprofit_yoy is not None and guidance_yoy is not None:
                            features["fundamental__netprofit_surprise_vs_guidance_pct"] = round(
                                actual_netprofit_yoy - guidance_yoy, 4
                            )
                    if flash:
                        flash_payload = json.loads(flash["features_json"] or "{}")
                        flash_eps = _finite(flash_payload.get("eps"))
                        features.update({
                            f"fundamental__flash_{key}": value
                            for key, value in flash_payload.items() if value is not None
                        })
                        if actual_eps is not None and flash_eps not in (None, 0.0):
                            features["fundamental__eps_surprise_vs_flash_pct"] = round(
                                (actual_eps - flash_eps) / abs(flash_eps) * 100.0, 4
                            )
                    if guidance or flash:
                        lineage["performance"] = {
                            "report_date": fundamental["report_date"],
                            "guidance_published_at": guidance["published_at"] if guidance else None,
                            "flash_published_at": flash["published_at"] if flash else None,
                        }

            latest_performance = conn.execute(
                """SELECT * FROM performance_events
                   WHERE market=? AND symbol=? AND published_at<=? AND report_date<=?
                   ORDER BY published_at DESC, report_date DESC LIMIT 1""",
                (market.upper(), symbol, cutoff, cutoff),
            ).fetchone()
            if latest_performance:
                performance_payload = json.loads(latest_performance["features_json"] or "{}")
                prefix = "guidance" if latest_performance["event_kind"] == "guidance" else "flash"
                features.update({
                    f"fundamental__{prefix}_{key}": value
                    for key, value in performance_payload.items() if value is not None
                })
                features[f"fundamental__{prefix}_age_days"] = (
                    cutoff_date - date.fromisoformat(latest_performance["published_at"][:10])
                ).days
                features["fundamental__performance_available"] = 1
                if latest_performance["event_kind"] == "flash":
                    # A published earnings flash is valid PIT evidence and can fill fields
                    # until the formal statement becomes available.
                    flash_fallbacks = {
                        "revenue_yoy_pct": "revenue_yoy_pct",
                        "netprofit_yoy_pct": "netprofit_yoy_pct",
                        "basic_eps": "eps",
                        "book_value_per_share": "book_value_per_share",
                    }
                    for target_key, source_key in flash_fallbacks.items():
                        value = performance_payload.get(source_key)
                        if value is not None:
                            features.setdefault(f"fundamental__{target_key}", value)
                    features["fundamental__available"] = 1
                lineage["performance"] = lineage["performance"] or {
                    "event_kind": latest_performance["event_kind"],
                    "published_at": latest_performance["published_at"],
                    "report_date": latest_performance["report_date"],
                    "source": latest_performance["source"],
                }

            quality_fields = (
                "revenue_yoy_pct",
                "operating_profit_yoy_pct",
                "netprofit_yoy_pct",
                "deducted_netprofit_yoy_pct",
                "basic_eps",
                "book_value_per_share",
            )
            present_fields = [
                key for key in quality_fields
                if _finite(features.get(f"fundamental__{key}")) is not None
            ]
            age_candidates = [
                _finite(features.get("fundamental__notice_age_days")),
                _finite(features.get("fundamental__flash_age_days")),
            ]
            valid_ages = [value for value in age_candidates if value is not None and value >= 0]
            evidence_age = min(valid_ages) if valid_ages else None
            completeness = len(present_fields) / len(quality_fields)
            freshness = (
                max(0.0, 1.0 - float(evidence_age) / max(1, fundamental_max_age_days))
                if evidence_age is not None else 0.0
            )
            quality_score = 0.7 * completeness + 0.3 * freshness
            surprise_available = int(any(
                key in features for key in (
                    "fundamental__netprofit_surprise_vs_guidance_pct",
                    "fundamental__eps_surprise_vs_flash_pct",
                )
            ))
            features.update({
                "fundamental__field_completeness": round(completeness, 6),
                "fundamental__freshness_score": round(freshness, 6),
                "fundamental__quality_score": round(quality_score, 6),
                "fundamental__high_quality": int(
                    bool(features.get("fundamental__available"))
                    and completeness >= 0.5
                    and freshness >= 0.2
                ),
                "fundamental__surprise_available": surprise_available,
            })
            lineage["fundamental_quality"] = {
                "present_fields": present_fields,
                "field_completeness": round(completeness, 6),
                "evidence_age_days": evidence_age,
                "freshness_score": round(freshness, 6),
                "quality_score": round(quality_score, 6),
                "high_quality": bool(features["fundamental__high_quality"]),
                "surprise_available": bool(surprise_available),
            }

            announcement_start = (
                cutoff_date - timedelta(days=max(1, int(announcement_lookback_days)))
            ).isoformat()
            announcements = conn.execute(
                """SELECT published_at, category FROM announcement_events
                   WHERE market=? AND symbol=? AND published_at>=? AND published_at<=?
                   ORDER BY published_at""",
                (market.upper(), symbol, announcement_start, cutoff),
            ).fetchall()
            if announcements:
                dates = [date.fromisoformat(row["published_at"][:10]) for row in announcements]
                for window in (7, 30, 90):
                    features[f"news__announcement_count_{window}d"] = sum(
                        (cutoff_date - value).days <= window for value in dates
                    )
                categories = ("earnings", "dividend", "buyback", "capital", "risk", "operations", "governance")
                for category in categories:
                    features[f"news__{category}_count_30d"] = sum(
                        row["category"] == category
                        and (cutoff_date - date.fromisoformat(row["published_at"][:10])).days <= 30
                        for row in announcements
                    )
                features["news__latest_announcement_age_days"] = (
                    cutoff_date - max(dates)
                ).days
                weighted = []
                risk_weighted = []
                catalyst_weighted = []
                category_weights = {
                    "risk": 1.5, "earnings": 1.2, "buyback": 1.2,
                    "capital": 1.0, "operations": 1.0, "dividend": 0.8,
                    "governance": 0.6, "other": 0.4,
                }
                for row in announcements:
                    age = (cutoff_date - date.fromisoformat(row["published_at"][:10])).days
                    if age > 30:
                        continue
                    decay = math.exp(-max(0, age) / 14.0)
                    value = category_weights.get(row["category"], 0.4) * decay
                    weighted.append(value)
                    if row["category"] == "risk":
                        risk_weighted.append(value)
                    if row["category"] in {"buyback", "operations", "dividend", "earnings"}:
                        catalyst_weighted.append(value)
                count_7d = features.get("news__announcement_count_7d", 0)
                count_30d = features.get("news__announcement_count_30d", 0)
                features.update({
                    "news__event_intensity_30d": round(sum(weighted), 6),
                    "news__risk_intensity_30d": round(sum(risk_weighted), 6),
                    "news__catalyst_intensity_30d": round(sum(catalyst_weighted), 6),
                    "news__announcement_velocity": round(
                        float(count_7d) / max(1.0, float(count_30d) / 4.2857), 6
                    ),
                })
                features["news__official_available"] = 1
                lineage["announcements"] = {
                    "events": len(announcements),
                    "latest_date": max(dates).isoformat(),
                    "source": "cninfo_disclosure",
                }

            industry = conn.execute(
                """SELECT * FROM industry_memberships
                   WHERE market=? AND symbol=? AND standard=? AND effective_from<=?
                     AND (effective_to IS NULL OR effective_to>?)
                   ORDER BY effective_from DESC LIMIT 1""",
                (market.upper(), symbol, industry_standard, cutoff, cutoff),
            ).fetchone()
            if industry:
                chosen = industry["industry_l2"] or industry["industry_l1"] or industry["industry_l3"]
                features.update({
                    "meta__industry": chosen or "unknown",
                    "meta__industry_standard": industry["standard"],
                    "meta__industry_pit_verified": True,
                    "industry__l1": industry["industry_l1"] or "unknown",
                    "industry__l2": industry["industry_l2"] or "unknown",
                    "industry__l3": industry["industry_l3"] or "unknown",
                })
                lineage["industry"] = {
                    "standard": industry["standard"],
                    "effective_from": industry["effective_from"],
                    "effective_to": industry["effective_to"],
                    "source": industry["source"],
                }

        features.setdefault("fundamental__available", 0)
        features.setdefault("fundamental__performance_available", 0)
        features.setdefault("fundamental__high_quality", 0)
        features.setdefault("fundamental__surprise_available", 0)
        features.setdefault("news__official_available", 0)
        features.setdefault("meta__industry_pit_verified", False)
        return features, lineage

    def status(self) -> dict[str, Any]:
        with self._conn() as conn:
            result = {}
            for table, date_column in (
                ("fundamental_events", "effective_date"),
                ("performance_events", "published_at"),
                ("announcement_events", "published_at"),
                ("industry_memberships", "effective_from"),
            ):
                row = conn.execute(
                    f"""SELECT COUNT(*) records, COUNT(DISTINCT symbol) symbols,
                               MIN({date_column}) first_date, MAX({date_column}) last_date
                        FROM {table}"""
                ).fetchone()
                result[table] = dict(row)
        return {"db_path": str(self.db_path), **result}


@dataclass
class QuantPitRefreshConfig:
    symbols: list[str]
    start_date: str
    end_date: str
    market: str = "A"
    concurrency: int = 3
    include_fundamental: bool = True
    include_performance: bool = True
    include_announcements: bool = True
    include_industry: bool = True


@dataclass
class QuantPitRefreshReport:
    generated_at: str
    config: dict[str, Any]
    saved: dict[str, int]
    errors: list[dict[str, str]]
    status: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class QuantPitEnrichmentRefresher:
    def __init__(self, store: Optional[QuantPitEnrichmentStore] = None):
        self.store = store or QuantPitEnrichmentStore()

    async def run(self, config: QuantPitRefreshConfig) -> QuantPitRefreshReport:
        semaphore = asyncio.Semaphore(max(1, int(config.concurrency)))
        totals = {"fundamental": 0, "performance": 0, "announcements": 0, "industry": 0}
        errors: list[dict[str, str]] = []

        async def refresh_symbol(symbol: str) -> None:
            async with semaphore:
                result, item_errors = await asyncio.to_thread(self._refresh_symbol, symbol, config)
                for key, value in result.items():
                    totals[key] += value
                errors.extend(item_errors)

        await asyncio.gather(*(refresh_symbol(symbol) for symbol in sorted(set(config.symbols))))
        if config.include_performance:
            try:
                events = await asyncio.to_thread(self._performance_events, config)
                totals["performance"] = self.store.upsert_performance_events(events)
            except Exception as exc:
                errors.append({"symbol": "*", "source": "performance", "reason": str(exc)})
        return QuantPitRefreshReport(
            generated_at=datetime.now().isoformat(),
            config=asdict(config),
            saved=totals,
            errors=errors,
            status=self.store.status(),
        )

    def _refresh_symbol(
        self,
        symbol: str,
        config: QuantPitRefreshConfig,
    ) -> tuple[dict[str, int], list[dict[str, str]]]:
        import akshare as ak

        saved = {"fundamental": 0, "announcements": 0, "industry": 0}
        errors: list[dict[str, str]] = []
        if config.include_fundamental:
            try:
                try:
                    frame = _retry_source_call(
                        lambda: ak.stock_profit_sheet_by_report_em(
                            symbol=_eastmoney_symbol(symbol)
                        )
                    )
                except KeyError as exc:
                    if str(exc).strip("'") != "data":
                        raise
                    frame = _retry_source_call(
                        lambda: ak.stock_profit_sheet_by_report_delisted_em(
                            symbol=_eastmoney_symbol(symbol)
                        )
                    )
                events = _normalize_fundamental_events(frame, symbol, config)
                saved["fundamental"] = self.store.upsert_fundamentals(events)
            except Exception as exc:
                errors.append({"symbol": symbol, "source": "fundamental", "reason": str(exc)})
        if config.include_announcements:
            try:
                frame = _retry_source_call(
                    lambda: ak.stock_zh_a_disclosure_report_cninfo(
                        symbol=symbol,
                        market="沪深京",
                        start_date=config.start_date.replace("-", ""),
                        end_date=config.end_date.replace("-", ""),
                    )
                )
                events = _normalize_announcement_events(frame, symbol, config.market)
                saved["announcements"] = self.store.upsert_announcements(events)
            except Exception as exc:
                errors.append({"symbol": symbol, "source": "announcements", "reason": str(exc)})
        if config.include_industry:
            try:
                with _INDUSTRY_SOURCE_LOCK:
                    try:
                        frame = _retry_source_call(
                            lambda: ak.stock_industry_change_cninfo(
                                symbol=symbol,
                                start_date="19900101",
                                end_date=config.end_date.replace("-", ""),
                            )
                        )
                    except KeyError as exc:
                        if str(exc).strip("'") == "变更日期":
                            frame = None
                        else:
                            raise
                if frame is None:
                    return saved, errors
                events = _normalize_industry_memberships(frame, symbol, config.market)
                saved["industry"] = self.store.replace_industry_memberships(
                    symbol, config.market, events,
                )
            except Exception as exc:
                errors.append({"symbol": symbol, "source": "industry", "reason": str(exc)})
        return saved, errors

    @staticmethod
    def _performance_events(config: QuantPitRefreshConfig) -> list[PerformanceEvent]:
        import akshare as ak

        symbols = set(config.symbols)
        events: list[PerformanceEvent] = []
        successful_calls = 0
        failures: list[str] = []
        for report_date in _quarter_end_dates(config.start_date, config.end_date):
            compact = report_date.replace("-", "")
            try:
                guidance = _retry_source_call(lambda: ak.stock_yjyg_em(date=compact))
                successful_calls += 1
                events.extend(_normalize_guidance_events(
                    guidance, report_date, symbols, config.market, config.end_date,
                ))
            except Exception as exc:
                failures.append(f"guidance/{report_date}: {exc}")
            try:
                flash = _retry_source_call(lambda: ak.stock_yjkb_em(date=compact))
                successful_calls += 1
                events.extend(_normalize_flash_events(
                    flash, report_date, symbols, config.market, config.end_date,
                ))
            except Exception as exc:
                failures.append(f"flash/{report_date}: {exc}")
        if successful_calls == 0:
            preview = "; ".join(failures[:3]) or "unknown error"
            raise RuntimeError(f"业绩预告/快报接口全部失败: {preview}")
        return events


def _normalize_fundamental_events(frame, symbol: str, config: QuantPitRefreshConfig) -> list[FundamentalEvent]:
    events = []
    earliest = date.fromisoformat(config.start_date) - timedelta(days=730)
    latest = date.fromisoformat(config.end_date)
    for _, row in frame.iterrows():
        notice = _safe_date(row.get("NOTICE_DATE"))
        update = _safe_date(row.get("UPDATE_DATE"))
        report = _safe_date(row.get("REPORT_DATE"))
        effective = max(value for value in (notice, update) if value) if (notice or update) else ""
        if not effective or not report:
            continue
        if date.fromisoformat(effective) < earliest or date.fromisoformat(effective) > latest:
            continue
        payload = {
            "revenue_yoy_pct": _finite(row.get("TOTAL_OPERATE_INCOME_YOY")),
            "operating_profit_yoy_pct": _finite(row.get("OPERATE_PROFIT_YOY")),
            "netprofit_yoy_pct": _finite(row.get("PARENT_NETPROFIT_YOY")),
            "deducted_netprofit_yoy_pct": _finite(row.get("DEDUCT_PARENT_NETPROFIT_YOY")),
            "basic_eps": _finite(row.get("BASIC_EPS")),
            "report_type": str(row.get("REPORT_DATE_NAME") or row.get("REPORT_TYPE") or "unknown"),
        }
        events.append(FundamentalEvent(
            market=config.market,
            symbol=symbol,
            effective_date=effective,
            report_date=report,
            source_update_date=update,
            features={key: value for key, value in payload.items() if value is not None},
        ))
    return events


def _normalize_guidance_events(
    frame,
    report_date: str,
    symbols: set[str],
    market: str,
    end_date: str,
) -> list[PerformanceEvent]:
    events = []
    for _, row in frame.iterrows():
        symbol = str(row.get("股票代码") or "").strip().zfill(6)
        metric = str(row.get("预测指标") or "")
        published = _safe_date(row.get("公告日期"))
        if (
            symbol not in symbols
            or not published
            or published > end_date[:10]
            or "归属于上市公司股东的净利润" not in metric
            or "扣除" in metric
        ):
            continue
        payload = {
            "netprofit_value": _finite(row.get("预测数值")),
            "netprofit_yoy_pct": _finite(row.get("业绩变动幅度")),
            "prior_netprofit_value": _finite(row.get("上年同期值")),
            "forecast_type": str(row.get("预告类型") or "unknown"),
        }
        events.append(PerformanceEvent(
            market=market,
            symbol=symbol,
            event_kind="guidance",
            published_at=published,
            report_date=report_date,
            features={key: value for key, value in payload.items() if value is not None},
            source="eastmoney_performance_guidance",
        ))
    return events


def _normalize_flash_events(
    frame,
    report_date: str,
    symbols: set[str],
    market: str,
    end_date: str,
) -> list[PerformanceEvent]:
    events = []
    for _, row in frame.iterrows():
        symbol = str(row.get("股票代码") or "").strip().zfill(6)
        published = _safe_date(row.get("公告日期"))
        if symbol not in symbols or not published or published > end_date[:10]:
            continue
        payload = {
            "eps": _finite(row.get("每股收益")),
            "revenue": _finite(row.get("营业收入-营业收入")),
            "revenue_yoy_pct": _finite(row.get("营业收入-同比增长")),
            "netprofit": _finite(row.get("净利润-净利润")),
            "netprofit_yoy_pct": _finite(row.get("净利润-同比增长")),
            "book_value_per_share": _finite(row.get("每股净资产")),
            "roe_pct": _finite(row.get("净资产收益率")),
        }
        events.append(PerformanceEvent(
            market=market,
            symbol=symbol,
            event_kind="flash",
            published_at=published,
            report_date=report_date,
            features={key: value for key, value in payload.items() if value is not None},
            source="eastmoney_performance_flash",
        ))
    return events


def _normalize_announcement_events(frame, symbol: str, market: str) -> list[AnnouncementEvent]:
    events = []
    for _, row in frame.iterrows():
        published = _safe_date(row.get("公告时间"))
        title = str(row.get("公告标题") or "").strip()
        if not published or not title:
            continue
        events.append(AnnouncementEvent(
            market=market,
            symbol=symbol,
            published_at=published,
            title=title,
            category=_announcement_category(title),
            source_url=str(row.get("公告链接") or ""),
        ))
    return events


def _normalize_industry_memberships(frame, symbol: str, market: str) -> list[IndustryMembership]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for _, row in frame.iterrows():
        standard = str(row.get("分类标准") or "").strip()
        effective = _safe_date(row.get("变更日期"))
        if not standard or not effective:
            continue
        grouped.setdefault(standard, []).append({
            "effective": effective,
            "l1": str(row.get("行业门类") or "").strip(),
            "l2": str(row.get("行业次类") or row.get("行业大类") or "").strip(),
            "l3": str(row.get("行业中类") or "").strip(),
        })
    result = []
    for standard, rows in grouped.items():
        rows.sort(key=lambda item: item["effective"])
        for index, item in enumerate(rows):
            result.append(IndustryMembership(
                market=market,
                symbol=symbol,
                standard=standard,
                industry_l1=item["l1"],
                industry_l2=item["l2"],
                industry_l3=item["l3"],
                effective_from=item["effective"],
                effective_to=(rows[index + 1]["effective"] if index + 1 < len(rows) else None),
            ))
    return result


def _announcement_category(title: str) -> str:
    rules = (
        ("risk", ("立案", "处罚", "问询", "诉讼", "退市", "风险提示", "违规")),
        ("earnings", ("年报", "半年报", "季报", "业绩预告", "业绩快报", "经营数据")),
        ("dividend", ("权益分派", "利润分配", "分红", "派息")),
        ("buyback", ("回购", "增持", "减持")),
        ("capital", ("定向增发", "非公开发行", "可转债", "融资", "发行股份")),
        ("operations", ("中标", "合同", "项目", "产品", "投资建设", "战略合作")),
        ("governance", ("董事", "监事", "高管", "股东大会", "公司章程")),
    )
    for category, keywords in rules:
        if any(keyword in title for keyword in keywords):
            return category
    return "other"


def _quarter_end_dates(start_date: str, end_date: str) -> list[str]:
    start = date.fromisoformat(start_date[:10])
    end = date.fromisoformat(end_date[:10])
    result = []
    for year in range(start.year - 1, end.year + 1):
        for month, day in ((3, 31), (6, 30), (9, 30), (12, 31)):
            value = date(year, month, day)
            if value >= date(start.year - 1, 1, 1) and value <= end:
                result.append(value.isoformat())
    return result


def _eastmoney_symbol(symbol: str) -> str:
    if str(symbol).startswith(("6", "68")):
        return f"SH{symbol}"
    if str(symbol).startswith(("4", "8", "92")):
        return f"BJ{symbol}"
    return f"SZ{symbol}"


def _safe_date(value: Any) -> str:
    if value is None:
        return ""
    try:
        return datetime.fromisoformat(str(value)[:19]).date().isoformat()
    except (TypeError, ValueError):
        try:
            return date.fromisoformat(str(value)[:10].replace("/", "-")).isoformat()
        except (TypeError, ValueError):
            return ""


def _iso_date(value: str | date | datetime) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return date.fromisoformat(str(value)[:10]).isoformat()


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _retry_source_call(callable_, attempts: int = 3):
    last_error = None
    for attempt in range(max(1, attempts)):
        try:
            return callable_()
        except KeyError:
            raise
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.8 * (attempt + 1))
    raise last_error
