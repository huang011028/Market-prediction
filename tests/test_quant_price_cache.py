import pandas as pd

from src.data.quant_price_cache import QuantPriceCache


def test_price_cache_returns_only_fully_covered_windows(tmp_path):
    cache = QuantPriceCache(tmp_path / "cache")
    frame = pd.DataFrame(
        {"open": [1.0, 2.0, 3.0], "close": [1.1, 2.1, 3.1]},
        index=pd.to_datetime(["2025-01-01", "2025-01-02", "2025-01-03"]),
    )
    cache.save("000001", "A", frame, source="fixture")

    loaded = cache.load("000001", "A", "2025-01-01", "2025-01-03")

    assert loaded is not None
    assert loaded["close"].tolist() == [1.1, 2.1, 3.1]
    assert cache.load("000001", "A", "2024-12-31", "2025-01-03") is None
    partial = cache.load(
        "000001", "A", "2024-12-31", "2025-01-05", allow_partial=True,
    )
    assert partial is not None
    assert partial["close"].tolist() == [1.1, 2.1, 3.1]
    assert cache.status()["symbols"] == 1


def test_price_cache_merges_instead_of_shrinking_history(tmp_path):
    cache = QuantPriceCache(tmp_path / "cache")
    old = pd.DataFrame(
        {"close": [1.0, 2.0]},
        index=pd.to_datetime(["2024-01-01", "2024-01-02"]),
    )
    new = pd.DataFrame(
        {"close": [3.0, 4.0]},
        index=pd.to_datetime(["2024-01-02", "2024-01-03"]),
    )
    cache.save("000001", "A", old, source="first")
    cache.save("000001", "A", new, source="second")

    loaded = cache.load("000001", "A", "2024-01-01", "2024-01-03")

    assert loaded is not None
    assert loaded["close"].tolist() == [1.0, 3.0, 4.0]
