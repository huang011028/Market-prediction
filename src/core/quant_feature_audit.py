"""Quality and point-in-time audit for Quant feature datasets."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np

from src.data.quant_feature_store import FEATURE_SCHEMA_VERSION, QuantFeatureStore


@dataclass
class FeatureAuditConfig:
    market: str = "A"
    horizon: str = "5d"
    target_version: str = "v3.1"
    feature_version: str = FEATURE_SCHEMA_VERSION
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    required_family_coverage: dict[str, float] = field(default_factory=lambda: {
        "technical": 0.95,
        "industry": 0.50,
        "balance": 0.30,
        "cashflow": 0.30,
        "consensus": 0.05,
    })
    max_lineage_violation_rate: float = 0.0
    max_stale_rate: float = 0.50
    max_drift_score: float = 1.0
    stale_days: int = 550


class QuantFeatureAuditor:
    def __init__(self, store: Optional[QuantFeatureStore] = None):
        self.store = store or QuantFeatureStore()

    def run(
        self,
        config: Optional[FeatureAuditConfig] = None,
        output_dir: Optional[str | Path] = None,
    ) -> dict[str, Any]:
        config = config or FeatureAuditConfig()
        rows = self.store.rows(
            market=config.market,
            horizon=config.horizon,
            target_version=config.target_version,
            feature_version=config.feature_version,
            start_date=config.start_date,
            end_date=config.end_date,
            limit=1_000_000,
        )
        if not rows:
            raise ValueError("没有可审计的 Quant 特征样本")
        family_counts: dict[str, int] = {}
        feature_values: dict[str, list[tuple[str, float]]] = {}
        stale = 0
        age_observations = 0
        lineage_violations: list[dict[str, Any]] = []
        for row in rows:
            features = row.get("features") or {}
            families = {key.split("__", 1)[0] for key, value in features.items() if value is not None}
            for family in families:
                available_key = f"{family}__available"
                if available_key in features and not bool(features.get(available_key)):
                    continue
                family_counts[family] = family_counts.get(family, 0) + 1
            for key, value in features.items():
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                number = float(value)
                if math.isfinite(number):
                    feature_values.setdefault(key, []).append((row["as_of"], number))
                if key.endswith("_age_days") and math.isfinite(number):
                    age_observations += 1
                    stale += int(number > config.stale_days)
            self._audit_lineage(row, lineage_violations)

        total = len(rows)
        coverage = {
            family: round(family_counts.get(family, 0) / total, 6)
            for family in sorted(set(family_counts) | set(config.required_family_coverage))
        }
        feature_quality = {
            key: _feature_quality(values, total)
            for key, values in feature_values.items()
            if len(values) >= 20
        }
        drift_features = {
            key: item for key, item in feature_quality.items()
            if item["drift_score"] > config.max_drift_score
        }
        outlier_features = {
            key: item for key, item in feature_quality.items()
            if item["outlier_rate"] > 0.05
        }
        stale_rate = stale / max(1, age_observations)
        lineage_rate = len(lineage_violations) / total
        checks = {
            **{
                f"coverage_{family}": coverage.get(family, 0.0) >= minimum
                for family, minimum in config.required_family_coverage.items()
            },
            "lineage_clean": lineage_rate <= config.max_lineage_violation_rate,
            "staleness_acceptable": stale_rate <= config.max_stale_rate,
            "drift_acceptable": not drift_features,
        }
        report = {
            "generated_at": datetime.now().isoformat(),
            "config": asdict(config),
            "rows": total,
            "unique_symbols": len({row["symbol"] for row in rows}),
            "unique_dates": len({row["as_of"] for row in rows}),
            "family_coverage": coverage,
            "staleness": {
                "observations": age_observations,
                "stale": stale,
                "rate": round(stale_rate, 6),
            },
            "lineage": {
                "violations": len(lineage_violations),
                "rate": round(lineage_rate, 6),
                "examples": lineage_violations[:20],
            },
            "drift_features": drift_features,
            "outlier_features": outlier_features,
            "feature_quality": feature_quality,
            "gate": {
                "passed": all(checks.values()),
                "checks": checks,
                "note": "质量门禁仅决定特征能否进入研究，不证明预测能力。",
            },
        }
        if output_dir:
            path = Path(output_dir) / "quant_feature_audit.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            report["report_path"] = str(path)
        return report

    @staticmethod
    def _audit_lineage(row: dict[str, Any], violations: list[dict[str, Any]]) -> None:
        lineage = row.get("lineage") or {}
        as_of = str(row.get("as_of") or "")[:10]
        if not lineage.get("point_in_time_verified"):
            violations.append({"feature_id": row.get("feature_id"), "reason": "pit_not_verified"})
            return
        enrichment = lineage.get("pit_enrichment") or {}
        for family, payload in enrichment.items():
            if not isinstance(payload, dict):
                continue
            for field in ("effective_date", "published_at", "latest_date"):
                value = str(payload.get(field) or "")[:10]
                if value and value > as_of:
                    violations.append({
                        "feature_id": row.get("feature_id"), "family": family,
                        "field": field, "value": value, "as_of": as_of,
                    })


def _feature_quality(values: list[tuple[str, float]], total_rows: int) -> dict[str, float | int]:
    ordered = sorted(values, key=lambda item: item[0])
    data = np.asarray([item[1] for item in ordered], dtype=float)
    q1, q3 = np.quantile(data, [0.25, 0.75])
    iqr = float(q3 - q1)
    if iqr > 0:
        outliers = (data < q1 - 3.0 * iqr) | (data > q3 + 3.0 * iqr)
    else:
        outliers = np.zeros(len(data), dtype=bool)
    midpoint = max(1, len(data) // 2)
    early = data[:midpoint]
    late = data[midpoint:]
    unique = set(np.unique(data).tolist())
    if unique.issubset({0.0, 1.0}):
        drift = abs(float(np.mean(late)) - float(np.mean(early))) if len(late) else 0.0
    else:
        scale = float(np.std(early))
        drift = abs(float(np.mean(late)) - float(np.mean(early))) / max(scale, 1e-9) if len(late) else 0.0
    return {
        "observations": len(data),
        "missing_rate": round(1.0 - len(data) / max(1, total_rows), 6),
        "outlier_rate": round(float(outliers.mean()), 6),
        "drift_score": round(drift, 6),
        "min": round(float(np.min(data)), 6),
        "median": round(float(np.median(data)), 6),
        "max": round(float(np.max(data)), 6),
    }
