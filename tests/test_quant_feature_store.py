from datetime import datetime, timedelta

import pytest

from src.data.quant_feature_store import FEATURE_SCHEMA_VERSION, QuantFeatureRow, QuantFeatureStore


def test_current_capture_cannot_be_backdated(tmp_path):
    store = QuantFeatureStore(tmp_path / "features.db")
    yesterday = (datetime.now() - timedelta(days=1)).date().isoformat()
    row = QuantFeatureRow(
        market="A",
        symbol="000001",
        as_of=yesterday,
        horizon="5d",
        features={"x": 1.0},
        source_kind="current_capture",
    )

    with pytest.raises(ValueError, match="不能回填"):
        store.save(row)


def test_historical_replay_rejects_future_source_timestamp(tmp_path):
    store = QuantFeatureStore(tmp_path / "features.db")
    row = QuantFeatureRow(
        market="A",
        symbol="000001",
        as_of="2025-01-01",
        horizon="5d",
        features={"x": 1.0},
        source_kind="historical_replay",
        lineage={
            "point_in_time_verified": True,
            "source_timestamps": ["2025-01-02"],
        },
    )

    with pytest.raises(ValueError, match="晚于 as_of"):
        store.save(row)


def test_store_partitions_and_prediction_label_backfill(tmp_path):
    store = QuantFeatureStore(tmp_path / "features.db")
    row = QuantFeatureRow(
        market="A",
        symbol="000001",
        as_of="2025-01-01",
        horizon="5d",
        prediction_id="pred-1",
        features={"technical__return_5d_pct": 2.0},
        source_kind="historical_replay",
        lineage={"point_in_time_verified": True, "source_timestamps": ["2025-01-01"]},
    )
    store.save(row)

    assert store.update_label_by_prediction(
        "pred-1",
        direction="bullish",
        return_pct=2.2,
        threshold_pct=1.5,
    )
    status = store.status()
    assert status["total"] == 1
    assert status["labeled"] == 1
    assert store.rows(labeled_only=True)[0]["label_direction"] == "bullish"


def test_rows_can_isolate_target_versions(tmp_path):
    store = QuantFeatureStore(tmp_path / "features.db")
    for version, symbol in (("v2", "000001"), ("v3", "000002")):
        store.save(QuantFeatureRow(
            market="A",
            symbol=symbol,
            as_of="2026-07-01",
            horizon="5d",
            target_version=version,
            features={"technical__return_5d_pct": 1.0},
            source_kind="historical_replay",
            label_direction="bullish",
            lineage={"point_in_time_verified": True, "source_timestamps": ["2026-07-01"]},
        ))

    rows = store.rows(target_version="v3", labeled_only=True)

    assert [row["symbol"] for row in rows] == ["000002"]


def test_industry_neutralization_updates_cross_sectional_labels(tmp_path):
    store = QuantFeatureStore(tmp_path / "features.db")
    for index, value in enumerate((1.0, 2.0, 6.0), start=1):
        store.save(QuantFeatureRow(
            market="A",
            symbol=f"00000{index}",
            as_of="2025-01-02",
            horizon="5d",
            features={"meta__industry": "银行"},
            source_kind="historical_replay",
            label_direction="bullish",
            label_return_pct=value,
            label_market_residual_pct=value,
            label_threshold_pct=1.5,
            lineage={"point_in_time_verified": True, "source_timestamps": ["2025-01-02"]},
        ))

    report = store.apply_industry_neutralization(market="A", horizon="5d")
    rows = store.rows(market="A", horizon="5d", target_version="v3.1", labeled_only=True)

    assert report["updated"] == 3
    assert [row["label_return_pct"] for row in rows] == [-1.0, 0.0, 4.0]
    assert [row["label_direction"] for row in rows] == ["neutral", "neutral", "bullish"]


def test_delete_partition_replaces_only_requested_window(tmp_path):
    store = QuantFeatureStore(tmp_path / "features.db")
    for as_of in ("2025-01-01", "2025-02-01", "2026-01-01"):
        store.save(QuantFeatureRow(
            market="A",
            symbol="000001",
            as_of=as_of,
            horizon="5d",
            source_kind="historical_replay",
            features={"x": 1.0},
            lineage={"point_in_time_verified": True, "source_timestamps": [as_of]},
        ))

    deleted = store.delete_partition(
        market="A",
        horizon="5d",
        target_version="v3.1",
        start_date="2025-01-01",
        end_date="2025-12-31",
    )

    assert deleted == 2
    assert [row["as_of"] for row in store.rows()] == ["2026-01-01"]


def test_rows_filter_feature_version_and_merge_derived_features(tmp_path):
    store = QuantFeatureStore(tmp_path / "features.db")
    old = QuantFeatureRow(
        market="A", symbol="000001", as_of="2024-01-01", horizon="5d",
        feature_version="quant_features.v2", features={"technical__return_5d_pct": 1.0},
        source_kind="historical_replay",
        lineage={"point_in_time_verified": True, "source_timestamps": ["2024-01-01"]},
    )
    current = QuantFeatureRow(
        market="A", symbol="000001", as_of="2024-01-08", horizon="5d",
        features={"technical__return_5d_pct": 2.0},
        source_kind="historical_replay",
        lineage={"point_in_time_verified": True, "source_timestamps": ["2024-01-08"]},
    )
    store.save(old)
    store.save(current)

    rows = store.rows(feature_version=FEATURE_SCHEMA_VERSION)
    assert [row["feature_id"] for row in rows] == [current.feature_id]
    assert store.update_features(
        current.feature_id,
        {"industry__return_20d_rank": 0.75},
        lineage_updates={"cross_section": "same_as_of_only"},
    )
    updated = store.rows(feature_version=FEATURE_SCHEMA_VERSION)[0]
    assert updated["features"]["industry__return_20d_rank"] == 0.75
    assert updated["lineage"]["derived_features"]["cross_section"] == "same_as_of_only"
    status = store.status()
    assert status["total"] == 2
    assert status["active_version"]["feature_version"] == FEATURE_SCHEMA_VERSION
    assert status["active_version"]["total"] == 1
