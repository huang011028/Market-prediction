from src.core.quant_feature_audit import FeatureAuditConfig, QuantFeatureAuditor
from src.data.quant_feature_store import QuantFeatureRow, QuantFeatureStore


def test_feature_audit_reports_coverage_staleness_and_lineage(tmp_path):
    store = QuantFeatureStore(tmp_path / "features.db")
    for index in range(24):
        as_of = f"2025-01-{index + 1:02d}"
        store.save(QuantFeatureRow(
            market="A", symbol=f"{index % 6 + 1:06d}", as_of=as_of, horizon="5d",
            features={
                "technical__return_20d_pct": float(index),
                "balance__available": 1,
                "balance__notice_age_days": 100,
                "cashflow__available": 1,
                "consensus__available": 1,
                "industry__member_count": 6,
            },
            source_kind="historical_replay",
            lineage={"point_in_time_verified": True, "source_timestamps": [as_of]},
        ))

    report = QuantFeatureAuditor(store).run(FeatureAuditConfig(
        required_family_coverage={
            "technical": 1.0, "balance": 1.0, "cashflow": 1.0,
            "consensus": 1.0, "industry": 1.0,
        },
        max_drift_score=100.0,
    ))

    assert report["rows"] == 24
    assert report["family_coverage"]["cashflow"] == 1.0
    assert report["lineage"]["violations"] == 0
    assert report["staleness"]["rate"] == 0.0
    assert report["gate"]["passed"] is True
