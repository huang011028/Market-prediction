from src.core.quant_dataset import QuantHistoricalDatasetBuilder
from src.data.quant_feature_store import FEATURE_SCHEMA_VERSION, QuantFeatureRow, QuantFeatureStore


def test_v4_valuation_builds_strict_pit_market_cap_and_book_to_market():
    values = QuantHistoricalDatasetBuilder._valuation_features(
        {
            "balance__share_capital": 100_000_000,
            "balance__total_equity": 500_000_000,
            "fundamental__basic_eps": 0.5,
        },
        10.0,
        {"fundamental": {"report_date": "2025-03-31"}},
    )

    assert values["meta__market_cap"] == 1_000_000_000
    assert values["valuation__market_cap"] == 1_000_000_000
    assert values["valuation__book_to_market"] == 0.5
    assert values["valuation__pb_proxy"] == 2.0


def test_research_v2_adds_only_same_date_and_expanding_features(tmp_path):
    store = QuantFeatureStore(tmp_path / "features.db")
    symbols = [f"00000{index}" for index in range(1, 6)]
    dates = [f"2025-01-{day:02d}" for day in (2, 9, 16, 23, 30)]
    for date_index, as_of in enumerate(dates):
        for symbol_index, symbol in enumerate(symbols):
            store.save(QuantFeatureRow(
                market="A",
                symbol=symbol,
                as_of=as_of,
                horizon="5d",
                features={
                    "technical__return_20d_pct": symbol_index - 2 + date_index,
                    "fundamental__netprofit_yoy_pct": symbol_index * 10,
                    "valuation__pe_proxy": 10 + symbol_index + date_index,
                    "valuation__pb_proxy": 1 + symbol_index / 10 + date_index / 10,
                    "meta__industry": "银行",
                    "meta__industry_pit_verified": True,
                },
                source_kind="historical_replay",
                label_direction="neutral",
                label_return_pct=0.0,
                lineage={
                    "point_in_time_verified": True,
                    "source_timestamps": [as_of],
                },
            ))

    builder = QuantHistoricalDatasetBuilder.__new__(QuantHistoricalDatasetBuilder)
    builder.store = store
    counts = builder._apply_research_v2_features(
        market="A",
        horizon="5d",
        target_version="v3.1",
        start_date=dates[0],
        end_date=dates[-1],
    )

    rows = store.rows(feature_version=FEATURE_SCHEMA_VERSION, limit=100)
    first = next(row for row in rows if row["as_of"] == dates[0] and row["symbol"] == symbols[0])
    last = next(row for row in rows if row["as_of"] == dates[-1] and row["symbol"] == symbols[-1])
    assert counts["rows_updated"] == 25
    assert "valuation__pe_expanding_percentile" not in first["features"]
    assert last["features"]["valuation__pe_expanding_percentile"] > 0.5
    assert last["features"]["industry__return_20d_rank_pct"] == 1.0
    assert last["features"]["market__breadth_positive_20d"] == 1.0
    assert last["lineage"]["derived_features"]["research_data_v2"]["point_in_time_verified"]
