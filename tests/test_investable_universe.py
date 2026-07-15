from src.data.investable_universe import InvestableUniverseStore, UniverseInstrument


def test_universe_eligibility_uses_listing_and_delisting_intervals(tmp_path):
    store = InvestableUniverseStore(tmp_path / "universe.db")
    store.upsert_many([
        UniverseInstrument("A", "000001", "长期上市", "SZSE", "1991-04-03", source="fixture"),
        UniverseInstrument("A", "000002", "后来上市", "SZSE", "2025-06-01", source="fixture"),
        UniverseInstrument("A", "600001", "已退市", "SSE", "1998-01-01", "2024-01-10", source="fixture"),
    ])

    symbols = [row["symbol"] for row in store.eligible_on(
        "2024-06-01", min_listing_days=120,
    )]

    assert symbols == ["000001"]


def test_universe_includes_delisted_name_before_effective_delist_date(tmp_path):
    store = InvestableUniverseStore(tmp_path / "universe.db")
    store.upsert_many([
        UniverseInstrument("A", "600001", "历史公司", "SSE", "1998-01-01", "2024-01-10", source="fixture"),
    ])

    assert store.eligible_on("2023-06-01", min_listing_days=120)[0]["symbol"] == "600001"
    assert store.eligible_on("2024-06-01", min_listing_days=120) == []


def test_universe_seeded_limit_is_reproducible_and_not_symbol_head(tmp_path):
    store = InvestableUniverseStore(tmp_path / "universe.db")
    store.upsert_many([
        UniverseInstrument("A", f"{index:06d}", str(index), "SZSE", "2000-01-01", source="fixture")
        for index in range(1, 21)
    ])

    first = store.eligible_on("2025-01-01", limit=5, sample_seed="fixed")
    second = store.eligible_on("2025-01-01", limit=5, sample_seed="fixed")

    assert [row["symbol"] for row in first] == [row["symbol"] for row in second]
    assert [row["symbol"] for row in first] != [f"{index:06d}" for index in range(1, 6)]


def test_universe_stratified_sample_spans_exchange_and_board(tmp_path):
    store = InvestableUniverseStore(tmp_path / "universe.db")
    records = []
    for index in range(10):
        records.append(UniverseInstrument(
            "A", f"60{index:04d}", f"沪{index}", "SSE", "2000-01-01",
            board="MAIN", source="fixture",
        ))
        records.append(UniverseInstrument(
            "A", f"30{index:04d}", f"创{index}", "SZSE", "2015-01-01",
            board="创业板", source="fixture",
        ))

    store.upsert_many(records)
    sample = store.eligible_on(
        "2025-01-01", limit=6, sample_seed="fixed", stratify=True,
    )

    assert len(sample) == 6
    assert {row["exchange"] for row in sample} == {"SSE", "SZSE"}
    assert {row["board"] for row in sample} == {"MAIN", "创业板"}


def test_sampled_union_includes_members_across_historical_dates(tmp_path):
    store = InvestableUniverseStore(tmp_path / "universe.db")
    store.upsert_many([
        UniverseInstrument("A", "600001", "旧成员", "SSE", "2000-01-01", "2024-02-01", board="MAIN", source="fixture"),
        UniverseInstrument("A", "300001", "新成员", "SZSE", "2024-02-01", board="创业板", source="fixture"),
    ])

    members = store.sampled_union(
        "2024-01-01", "2024-05-01", interval_days=30,
        min_listing_days=0, limit=1, sample_seed="fixed", stratify=True,
    )

    assert {row["symbol"] for row in members} == {"600001", "300001"}


def test_a_share_store_removes_b_share_codes(tmp_path):
    store = InvestableUniverseStore(tmp_path / "universe.db")
    store.upsert_many([
        UniverseInstrument("A", "200001", "深B", "SZSE", "2000-01-01", source="fixture"),
        UniverseInstrument("A", "900001", "沪B", "SSE", "2000-01-01", source="fixture"),
        UniverseInstrument("A", "000001", "深A", "SZSE", "2000-01-01", source="fixture"),
    ])
    # Cleanup is also enforced when the generated universe database is reopened.
    reopened = InvestableUniverseStore(tmp_path / "universe.db")

    assert [row["symbol"] for row in reopened.eligible_on("2025-01-01")] == ["000001"]
