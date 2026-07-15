"""Point-in-time feature store used by statistical baselines.

The store deliberately keeps features and lineage together.  A row captured from
the live application may only be stamped with today's date; historical rows must
come from a replay path that explicitly proves all inputs were available at the
requested ``as_of`` time.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from src.data.point_in_time_lineage import validate_point_in_time_write


FEATURE_SCHEMA_VERSION = "quant_features.v3"


@dataclass
class QuantFeatureRow:
    market: str
    symbol: str
    as_of: str
    horizon: str
    features: dict[str, Any]
    target_version: str = "v3.1"
    feature_version: str = FEATURE_SCHEMA_VERSION
    source_kind: str = "current_capture"
    collected_at: str = field(default_factory=lambda: datetime.now().isoformat())
    target_name: str = ""
    timeframe: str = "短期(1周)"
    prediction_id: Optional[str] = None
    valid_date: Optional[str] = None
    label_direction: Optional[str] = None
    label_return_pct: Optional[float] = None
    label_absolute_return_pct: Optional[float] = None
    label_benchmark_return_pct: Optional[float] = None
    label_market_beta: Optional[float] = None
    label_market_residual_pct: Optional[float] = None
    label_industry_return_pct: Optional[float] = None
    label_industry_residual_pct: Optional[float] = None
    label_threshold_pct: Optional[float] = None
    lineage: dict[str, Any] = field(default_factory=dict)

    @property
    def feature_id(self) -> str:
        raw = "|".join((
            self.market.upper(),
            self.symbol.upper(),
            self.as_of[:10],
            self.horizon,
            self.feature_version,
        ))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["feature_id"] = self.feature_id
        return payload


class QuantFeatureStore:
    """SQLite-backed point-in-time feature table with optional Parquet export."""

    def __init__(self, db_path: Optional[str | Path] = None):
        if db_path is None:
            from config.settings import get_settings

            db_path = get_settings().data_dir / "quant" / "features.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS feature_rows (
                    feature_id TEXT PRIMARY KEY,
                    market TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    target_name TEXT,
                    as_of TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    horizon TEXT NOT NULL,
                    target_version TEXT NOT NULL,
                    feature_version TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    collected_at TEXT NOT NULL,
                    prediction_id TEXT,
                    valid_date TEXT,
                    features_json TEXT NOT NULL,
                    lineage_json TEXT NOT NULL,
                    label_direction TEXT,
                    label_return_pct REAL,
                    label_absolute_return_pct REAL,
                    label_benchmark_return_pct REAL,
                    label_market_beta REAL,
                    label_market_residual_pct REAL,
                    label_industry_return_pct REAL,
                    label_industry_residual_pct REAL,
                    label_threshold_pct REAL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_quant_market_date
                    ON feature_rows(market, as_of);
                CREATE INDEX IF NOT EXISTS idx_quant_symbol_date
                    ON feature_rows(symbol, as_of);
                CREATE INDEX IF NOT EXISTS idx_quant_label
                    ON feature_rows(market, horizon, label_direction);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_quant_prediction
                    ON feature_rows(prediction_id) WHERE prediction_id IS NOT NULL;
                """
            )
            existing = {row[1] for row in conn.execute("PRAGMA table_info(feature_rows)")}
            for name in (
                "label_market_beta", "label_market_residual_pct",
                "label_industry_return_pct", "label_industry_residual_pct",
            ):
                if name not in existing:
                    conn.execute(f"ALTER TABLE feature_rows ADD COLUMN {name} REAL")
            conn.commit()

    def save(self, row: QuantFeatureRow, *, replace: bool = True) -> str:
        self._validate_lineage(row)
        now = datetime.now().isoformat()
        sql = "INSERT OR REPLACE" if replace else "INSERT"
        with self._conn() as conn:
            conn.execute(
                f"""{sql} INTO feature_rows (
                    feature_id, market, symbol, target_name, as_of, timeframe,
                    horizon, target_version, feature_version, source_kind,
                    collected_at, prediction_id, valid_date, features_json,
                    lineage_json, label_direction, label_return_pct,
                    label_absolute_return_pct, label_benchmark_return_pct,
                    label_market_beta, label_market_residual_pct,
                    label_industry_return_pct, label_industry_residual_pct,
                    label_threshold_pct, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    row.feature_id,
                    row.market.upper(),
                    row.symbol,
                    row.target_name,
                    row.as_of[:10],
                    row.timeframe,
                    row.horizon,
                    row.target_version,
                    row.feature_version,
                    row.source_kind,
                    row.collected_at,
                    row.prediction_id,
                    row.valid_date,
                    json.dumps(row.features, ensure_ascii=False, default=str),
                    json.dumps(row.lineage, ensure_ascii=False, default=str),
                    row.label_direction,
                    row.label_return_pct,
                    row.label_absolute_return_pct,
                    row.label_benchmark_return_pct,
                    row.label_market_beta,
                    row.label_market_residual_pct,
                    row.label_industry_return_pct,
                    row.label_industry_residual_pct,
                    row.label_threshold_pct,
                    now,
                ),
            )
            conn.commit()
        return row.feature_id

    @staticmethod
    def _validate_lineage(row: QuantFeatureRow) -> None:
        row.lineage = validate_point_in_time_write(
            as_of=row.as_of,
            collected_at=row.collected_at,
            source_kind=row.source_kind,
            lineage=row.lineage,
        )

    def update_label(
        self,
        feature_id: str,
        *,
        direction: str,
        return_pct: float,
        absolute_return_pct: Optional[float] = None,
        benchmark_return_pct: Optional[float] = None,
        market_beta: Optional[float] = None,
        market_residual_pct: Optional[float] = None,
        industry_return_pct: Optional[float] = None,
        industry_residual_pct: Optional[float] = None,
        threshold_pct: Optional[float] = None,
        valid_date: Optional[str] = None,
    ) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                """UPDATE feature_rows SET
                   label_direction=?, label_return_pct=?,
                   label_absolute_return_pct=?, label_benchmark_return_pct=?,
                   label_market_beta=?, label_market_residual_pct=?,
                   label_industry_return_pct=?, label_industry_residual_pct=?,
                   label_threshold_pct=?, valid_date=COALESCE(?, valid_date),
                   updated_at=? WHERE feature_id=?""",
                (
                    direction,
                    float(return_pct),
                    absolute_return_pct,
                    benchmark_return_pct,
                    market_beta,
                    market_residual_pct,
                    industry_return_pct,
                    industry_residual_pct,
                    threshold_pct,
                    valid_date,
                    datetime.now().isoformat(),
                    feature_id,
                ),
            )
            conn.commit()
            return cur.rowcount > 0

    def apply_industry_neutralization(
        self,
        *,
        market: str,
        horizon: str,
        target_version: str = "v3.1",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        min_group_size: int = 3,
    ) -> dict[str, int]:
        """Subtract same-date industry median residual from label returns."""
        rows = self.rows(
            market=market,
            horizon=horizon,
            target_version=target_version,
            labeled_only=True,
            start_date=start_date,
            end_date=end_date,
            limit=1_000_000,
        )
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in rows:
            industry = str((row.get("features") or {}).get("meta__industry") or "unknown")
            if industry == "unknown":
                continue
            groups.setdefault((row["as_of"], industry), []).append(row)

        updated = 0
        skipped = 0
        from src.core.prediction_target import PredictionTargetSpec, direction_from_return

        with self._conn() as conn:
            for group_rows in groups.values():
                if len(group_rows) < max(2, int(min_group_size)):
                    skipped += len(group_rows)
                    continue
                values = [
                    float(row.get("label_market_residual_pct")
                          if row.get("label_market_residual_pct") is not None
                          else row["label_return_pct"])
                    for row in group_rows
                ]
                industry_return = sorted(values)[len(values) // 2]
                for row, value in zip(group_rows, values):
                    residual = value - industry_return
                    threshold = float(row.get("label_threshold_pct") or 1.5)
                    spec = PredictionTargetSpec(
                        up_threshold_pct=threshold,
                        down_threshold_pct=-threshold,
                    )
                    conn.execute(
                        """UPDATE feature_rows SET
                               label_direction=?, label_return_pct=?,
                               label_industry_return_pct=?,
                               label_industry_residual_pct=?, updated_at=?
                           WHERE feature_id=?""",
                        (
                            direction_from_return(residual, spec), residual,
                            industry_return, residual, datetime.now().isoformat(),
                            row["feature_id"],
                        ),
                    )
                    updated += 1
            conn.commit()
        return {"updated": updated, "skipped": skipped, "groups": len(groups)}

    def update_label_by_prediction(
        self,
        prediction_id: str,
        **label: Any,
    ) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT feature_id FROM feature_rows WHERE prediction_id=?",
                (prediction_id,),
            ).fetchone()
        if not row:
            return False
        return self.update_label(row["feature_id"], **label)

    def rows(
        self,
        *,
        market: Optional[str] = None,
        horizon: Optional[str] = None,
        target_version: Optional[str] = None,
        feature_version: Optional[str] = None,
        labeled_only: bool = False,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 100000,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if market:
            where.append("market=?")
            params.append(market.upper())
        if horizon:
            where.append("horizon=?")
            params.append(horizon)
        if target_version:
            where.append("target_version=?")
            params.append(target_version)
        if feature_version:
            where.append("feature_version=?")
            params.append(feature_version)
        if labeled_only:
            where.append("label_direction IS NOT NULL")
        if start_date:
            where.append("as_of>=?")
            params.append(start_date[:10])
        if end_date:
            where.append("as_of<=?")
            params.append(end_date[:10])
        clause = " WHERE " + " AND ".join(where) if where else ""
        params.append(max(1, int(limit)))
        with self._conn() as conn:
            records = conn.execute(
                f"SELECT * FROM feature_rows{clause} ORDER BY as_of, market, symbol LIMIT ?",
                params,
            ).fetchall()
        return [self._decode_row(dict(record)) for record in records]

    def update_features(
        self,
        feature_id: str,
        updates: dict[str, Any],
        *,
        lineage_updates: Optional[dict[str, Any]] = None,
    ) -> bool:
        """Merge derived PIT-safe features into one existing row."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT features_json, lineage_json FROM feature_rows WHERE feature_id=?",
                (feature_id,),
            ).fetchone()
            if not row:
                return False
            features = json.loads(row["features_json"] or "{}")
            features.update(updates)
            lineage = json.loads(row["lineage_json"] or "{}")
            if lineage_updates:
                lineage.setdefault("derived_features", {}).update(lineage_updates)
            conn.execute(
                """UPDATE feature_rows
                   SET features_json=?, lineage_json=?, updated_at=?
                   WHERE feature_id=?""",
                (
                    json.dumps(features, ensure_ascii=False, default=str),
                    json.dumps(lineage, ensure_ascii=False, default=str),
                    datetime.now().isoformat(),
                    feature_id,
                ),
            )
            conn.commit()
            return True

    def update_features_many(
        self,
        updates: Iterable[tuple[str, dict[str, Any]]],
        *,
        lineage_updates: Optional[dict[str, Any]] = None,
    ) -> int:
        """Merge derived features in one transaction for large research panels."""
        values = list(updates)
        changed = 0
        with self._conn() as conn:
            for feature_id, feature_updates in values:
                row = conn.execute(
                    "SELECT features_json, lineage_json FROM feature_rows WHERE feature_id=?",
                    (feature_id,),
                ).fetchone()
                if not row:
                    continue
                features = json.loads(row["features_json"] or "{}")
                features.update(feature_updates)
                lineage = json.loads(row["lineage_json"] or "{}")
                if lineage_updates:
                    lineage.setdefault("derived_features", {}).update(lineage_updates)
                conn.execute(
                    """UPDATE feature_rows SET features_json=?, lineage_json=?, updated_at=?
                       WHERE feature_id=?""",
                    (
                        json.dumps(features, ensure_ascii=False, default=str),
                        json.dumps(lineage, ensure_ascii=False, default=str),
                        datetime.now().isoformat(),
                        feature_id,
                    ),
                )
                changed += 1
            conn.commit()
        return changed

    @staticmethod
    def _decode_row(row: dict[str, Any]) -> dict[str, Any]:
        row["features"] = json.loads(row.pop("features_json") or "{}")
        row["lineage"] = json.loads(row.pop("lineage_json") or "{}")
        return row

    def status(self) -> dict[str, Any]:
        with self._conn() as conn:
            totals = conn.execute(
                """SELECT COUNT(*) total,
                          SUM(label_direction IS NOT NULL) labeled,
                          SUM(label_market_beta IS NOT NULL) beta_labeled,
                          SUM(label_market_residual_pct IS NOT NULL) residual_labeled,
                          COUNT(DISTINCT as_of) unique_dates,
                          COUNT(DISTINCT symbol) unique_symbols,
                          MIN(as_of) first_date, MAX(as_of) last_date
                   FROM feature_rows"""
            ).fetchone()
            markets = conn.execute(
                """SELECT market, horizon, COUNT(*) total,
                          feature_version,
                          SUM(label_direction IS NOT NULL) labeled,
                          SUM(label_market_beta IS NOT NULL) beta_labeled,
                          SUM(label_market_residual_pct IS NOT NULL) residual_labeled,
                          COUNT(DISTINCT as_of) unique_dates,
                          COUNT(DISTINCT symbol) unique_symbols
                   FROM feature_rows GROUP BY market, horizon, feature_version
                   ORDER BY market, horizon, feature_version"""
            ).fetchall()
            schema_versions = conn.execute(
                """SELECT feature_version, COUNT(*) total,
                          COUNT(DISTINCT symbol) unique_symbols,
                          COUNT(DISTINCT as_of) unique_dates
                   FROM feature_rows GROUP BY feature_version
                   ORDER BY feature_version"""
            ).fetchall()
            active = conn.execute(
                """SELECT COUNT(*) total,
                          SUM(label_direction IS NOT NULL) labeled,
                          SUM(label_market_residual_pct IS NOT NULL) residual_labeled,
                          COUNT(DISTINCT as_of) unique_dates,
                          COUNT(DISTINCT symbol) unique_symbols
                   FROM feature_rows WHERE feature_version=?""",
                (FEATURE_SCHEMA_VERSION,),
            ).fetchone()
        return {
            "schema_version": FEATURE_SCHEMA_VERSION,
            "db_path": str(self.db_path),
            "total": int(totals["total"] or 0),
            "labeled": int(totals["labeled"] or 0),
            "beta_labeled": int(totals["beta_labeled"] or 0),
            "residual_labeled": int(totals["residual_labeled"] or 0),
            "residual_coverage": round(
                int(totals["residual_labeled"] or 0) / max(1, int(totals["labeled"] or 0)),
                4,
            ),
            "unique_dates": int(totals["unique_dates"] or 0),
            "unique_symbols": int(totals["unique_symbols"] or 0),
            "first_date": totals["first_date"],
            "last_date": totals["last_date"],
            "partitions": [dict(row) for row in markets],
            "schema_versions": [dict(row) for row in schema_versions],
            "active_version": {
                "feature_version": FEATURE_SCHEMA_VERSION,
                "total": int(active["total"] or 0),
                "labeled": int(active["labeled"] or 0),
                "residual_labeled": int(active["residual_labeled"] or 0),
                "residual_coverage": round(
                    int(active["residual_labeled"] or 0)
                    / max(1, int(active["labeled"] or 0)),
                    4,
                ),
                "unique_dates": int(active["unique_dates"] or 0),
                "unique_symbols": int(active["unique_symbols"] or 0),
            },
        }

    def delete_partition(
        self,
        *,
        market: str,
        horizon: str,
        target_version: str,
        feature_version: str = FEATURE_SCHEMA_VERSION,
        start_date: str,
        end_date: str,
    ) -> int:
        """Delete one reproducible build partition before replacing it."""
        with self._conn() as conn:
            cursor = conn.execute(
                """DELETE FROM feature_rows
                   WHERE market=? AND horizon=? AND target_version=? AND feature_version=?
                     AND as_of>=? AND as_of<=?""",
                (
                    market.upper(), horizon, target_version, feature_version,
                    start_date[:10], end_date[:10],
                ),
            )
            conn.commit()
            return int(cursor.rowcount or 0)

    def export_parquet(
        self,
        output_root: str | Path,
        *,
        feature_version: Optional[str] = None,
    ) -> list[str]:
        """Export market/date partitions. Requires pandas and pyarrow."""
        import pandas as pd

        rows = self.rows(feature_version=feature_version, limit=1_000_000)
        if not rows:
            return []
        flat = []
        for row in rows:
            base = {key: value for key, value in row.items() if key not in {"features", "lineage"}}
            base.update({f"feature__{key}": value for key, value in row["features"].items()})
            flat.append(base)
        frame = pd.DataFrame(flat)
        root = Path(output_root)
        written: list[str] = []
        for (market, as_of), group in frame.groupby(["market", "as_of"]):
            path = root / f"market={market}" / f"date={as_of}" / "features.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            group.to_parquet(path, index=False)
            written.append(str(path))
        return written

    def import_bootstrap_reports(self, paths: Iterable[str | Path]) -> dict[str, int]:
        """Import full technical bootstrap samples as verified historical rows."""
        imported = 0
        skipped = 0
        seen: set[tuple[str, str, str]] = set()
        for raw_path in paths:
            path = Path(raw_path)
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                skipped += 1
                continue
            reports = payload.get("reports") if isinstance(payload, dict) else None
            reports = reports if isinstance(reports, list) else [payload]
            for report in reports:
                for sample in (report or {}).get("samples", []):
                    target = str(sample.get("target") or "")
                    as_of = str(sample.get("as_of") or "")[:10]
                    timeframe = str(sample.get("timeframe") or "短期(1周)")
                    if not target or not as_of:
                        skipped += 1
                        continue
                    key = (target, as_of, timeframe)
                    if key in seen:
                        continue
                    seen.add(key)
                    target_spec = sample.get("prediction_target") or {}
                    market = _market_from_target(target)
                    features = {
                        "agent__technical_direction": _direction_value(sample.get("predicted_direction")),
                        "agent__technical_confidence": _number(sample.get("predicted_confidence")),
                    }
                    for name, value in (sample.get("buckets") or {}).items():
                        features[f"technical_bucket__{name}"] = value
                    try:
                        row = QuantFeatureRow(
                            market=market,
                            symbol=target,
                            as_of=as_of,
                            horizon=str(target_spec.get("horizon") or _horizon_from_timeframe(timeframe)),
                            timeframe=timeframe,
                            features=features,
                            target_version=str(target_spec.get("target_version") or "v2_import"),
                            source_kind="historical_replay",
                            collected_at=datetime.now().isoformat(),
                            valid_date=str(sample.get("valid_date") or "")[:10] or None,
                            label_direction=sample.get("actual_direction"),
                            label_return_pct=_optional_number(
                                sample.get("effective_fixed_return_pct", sample.get("actual_change_pct"))
                            ),
                            label_absolute_return_pct=_optional_number(sample.get("fixed_horizon_return_pct")),
                            label_benchmark_return_pct=_optional_number(sample.get("benchmark_return_pct")),
                            label_threshold_pct=_optional_number(target_spec.get("up_threshold_pct")),
                            lineage={
                                "point_in_time_verified": True,
                                "source": "technical_kline_bootstrap",
                                "source_report": str(path),
                                "source_timestamps": [as_of],
                                "legacy_target_version": target_spec.get("target_version", "v2"),
                            },
                        )
                        self.save(row)
                        imported += 1
                    except Exception:
                        skipped += 1
        return {"imported": imported, "skipped": skipped}


def extract_prediction_features(agent_results: Iterable[Any], report: Any) -> dict[str, Any]:
    """Flatten stable numeric/categorical features from the five agents."""
    features: dict[str, Any] = {}
    for result in agent_results:
        name = _agent_slug(getattr(result, "agent_name", "unknown"))
        features[f"agent__{name}__direction"] = _direction_value(getattr(result, "direction", None))
        features[f"agent__{name}__confidence"] = _number(getattr(result, "confidence", 0.0))
        features[f"agent__{name}__quality"] = _number(getattr(result, "data_quality_score", 0.0))
        features[f"agent__{name}__degraded"] = int(getattr(result, "status", "ok") != "ok")
        prediction_target = getattr(result, "prediction_target", None)
        if prediction_target is not None:
            features[f"agent__{name}__expected_return_pct"] = _number(
                getattr(prediction_target, "expected_return_pct", None)
            )
            features[f"agent__{name}__prob_up"] = _number(getattr(prediction_target, "prob_up", None))
            features[f"agent__{name}__prob_down"] = _number(getattr(prediction_target, "prob_down", None))
            features[f"agent__{name}__prob_no_edge"] = _number(
                getattr(prediction_target, "prob_neutral", None)
            )
        summary = getattr(result, "data_summary", None) or {}
        if name == "technical":
            features.update(_technical_features(summary.get("technical_snapshot") or {}))

    features.update({
        "final__expected_excess_return_pct": _number(getattr(report, "expected_excess_return_pct", 0.0)),
        "final__prob_up": _number(getattr(report, "prob_up", 0.0)),
        "final__prob_down": _number(getattr(report, "prob_down", 0.0)),
        "final__prob_no_edge": _number(getattr(report, "prob_no_edge", 0.0)),
        "final__edge_score": _number(getattr(report, "edge_score", 0.0)),
    })
    return features


def extract_technical_features(data: dict[str, Any]) -> dict[str, Any]:
    return _technical_features(data.get("technical_snapshot") or data)


def _technical_features(snapshot: dict[str, Any]) -> dict[str, Any]:
    close = snapshot.get("close_series_summary") or {}
    trend = snapshot.get("trend_regime") or {}
    momentum = snapshot.get("momentum_signals") or {}
    volume = snapshot.get("volume_signals") or {}
    volatility = snapshot.get("volatility_signals") or {}
    sr = snapshot.get("support_resistance") or {}
    risk = snapshot.get("risk_levels") or {}
    quality = snapshot.get("data_quality") or {}
    return {
        "technical__return_5d_pct": _number(close.get("change_5d_pct")),
        "technical__return_20d_pct": _number(close.get("change_20d_pct")),
        "technical__trend_score": _number(trend.get("slope_score")),
        "technical__momentum_score": _number(momentum.get("score")),
        "technical__volume_score": _number(volume.get("score")),
        "technical__volume_ratio_20d": _number(volume.get("volume_ratio_20d")),
        "technical__daily_volatility_20d_pct": _number(volatility.get("daily_volatility_20d_pct")),
        "technical__atr_pct": _number(volatility.get("atr_pct")),
        "technical__support_distance_pct": _number(sr.get("support_distance_pct")),
        "technical__resistance_distance_pct": _number(sr.get("resistance_distance_pct")),
        "technical__risk_reward_ratio": _number(risk.get("risk_reward_ratio")),
        "technical__data_quality": _number(quality.get("score")),
        "technical__regime": trend.get("market_regime") or trend.get("short_term") or "unknown",
        "technical__volume_trend": volume.get("volume_trend") or "unknown",
        "technical__volatility_state": volatility.get("volatility_state") or "unknown",
    }


def _agent_slug(name: str) -> str:
    mapping = {
        "近期股价分析师": "technical",
        "最新新闻分析师": "news",
        "公司前景分析师": "fundamental",
        "国际形势分析师": "macro",
        "行业对比分析师": "industry",
    }
    return mapping.get(str(name), str(name).lower().replace(" ", "_"))


def _direction_value(value: Any) -> int:
    text = str(getattr(value, "value", value) or "neutral")
    return {"bearish": -1, "neutral": 0, "bullish": 1}.get(text, 0)


def _number(value: Any) -> float:
    try:
        if value in (None, "", "N/A"):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _optional_number(value: Any) -> Optional[float]:
    if value in (None, "", "N/A"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _horizon_from_timeframe(timeframe: str) -> str:
    if "长期" in timeframe or "季" in timeframe:
        return "60d"
    if "中期" in timeframe or "月" in timeframe:
        return "20d"
    return "5d"


def _market_from_target(target: str) -> str:
    try:
        from src.data.symbol_resolver import resolve_symbol

        return resolve_symbol(target).market
    except Exception:
        return "A" if target.isdigit() and len(target) == 6 else "US"
