import asyncio

import pandas as pd

from src.data import news_fetcher as news_fetcher_module
from src.data import us_fallbacks
from src.data.fundamental_fetcher import FundamentalFetcher
from src.data.industry_fetcher import IndustryFetcher
from src.data.news_fetcher import NewsFetcher
from src.data.price_fetcher import PriceFetcher


def make_us_price_df(days: int = 80) -> pd.DataFrame:
    dates = pd.date_range("2026-03-16", periods=days, freq="B")
    close = [180 + i * 0.5 for i in range(days)]
    return pd.DataFrame({
        "open": [c - 0.2 for c in close],
        "high": [c + 0.8 for c in close],
        "low": [c - 0.8 for c in close],
        "close": close,
        "volume": [20_000_000 + i * 10_000 for i in range(days)],
    }, index=dates)


def test_us_price_fetcher_uses_akshare_before_yfinance(monkeypatch):
    fetcher = PriceFetcher()
    calls = []

    monkeypatch.setattr(fetcher, "_fetch_from_tencent", lambda symbol, market: None)

    def fake_akshare(symbol, start_date):
        calls.append((symbol, start_date))
        return make_us_price_df()

    def fail_later(*args, **kwargs):
        raise AssertionError("later fallback should not be called when akshare has data")

    monkeypatch.setattr(us_fallbacks, "fetch_us_ohlcv_akshare", fake_akshare)
    monkeypatch.setattr(us_fallbacks, "fetch_us_ohlcv_stooq", fail_later)
    monkeypatch.setattr(fetcher, "_fetch_yfinance", fail_later)

    df = fetcher._fetch_us_share("AAPL", "3mo")

    assert calls and calls[0][0] == "AAPL"
    assert not df.empty
    assert {"open", "high", "low", "close", "volume"} <= set(df.columns)


def test_us_price_fetcher_uses_stooq_when_akshare_empty(monkeypatch):
    fetcher = PriceFetcher()
    calls = []

    monkeypatch.setattr(fetcher, "_fetch_from_tencent", lambda symbol, market: None)
    monkeypatch.setattr(us_fallbacks, "fetch_us_ohlcv_akshare", lambda symbol, start_date: None)

    def fake_stooq(symbol, start_date, timeout=12):
        calls.append((symbol, start_date))
        return make_us_price_df()

    def fail_yfinance(symbol, period):
        raise AssertionError("yfinance should not be called when Stooq has data")

    monkeypatch.setattr(us_fallbacks, "fetch_us_ohlcv_stooq", fake_stooq)
    monkeypatch.setattr(fetcher, "_fetch_yfinance", fail_yfinance)

    df = fetcher._fetch_us_share("AAPL", "3mo")

    assert calls and calls[0][0] == "AAPL"
    assert not df.empty
    assert {"open", "high", "low", "close", "volume"} <= set(df.columns)


def test_us_news_fetcher_uses_google_news_when_yfinance_empty(monkeypatch):
    fetcher = NewsFetcher(max_items=5)
    google_items = [{
        "title": "Apple stock rises after earnings beat",
        "summary": "AAPL shares rose as Apple reported stronger revenue.",
        "source": "Example News",
        "time": "2026-07-06 10:00:00",
        "url": "https://example.com/aapl",
    }]

    monkeypatch.setattr(fetcher, "_fetch_from_yfinance", lambda symbol: None)
    monkeypatch.setattr(
        us_fallbacks,
        "fetch_us_news_google",
        lambda symbol, company_name="", days=14, max_items=20: google_items,
    )
    monkeypatch.setattr(
        news_fetcher_module,
        "process_news_pipeline",
        lambda items, symbol, market, reference_date, max_output: {
            "total_fetched": len(items),
            "after_dedup": len(items),
            "after_relevance_filter": len(items),
            "sentiment_stats": {},
            "category_breakdown": {},
            "top_news": items[:max_output],
            "anomaly_flags": {},
        },
    )

    data = asyncio.run(fetcher.fetch("AAPL", "US", days=7))

    assert data.news_count == 1
    assert data.news_source == "google_news_rss"
    assert data.sources_used == ["google_news_rss"]
    assert data.company_name == "Apple Inc."


def test_us_fundamental_fetcher_uses_sec_reference_fallback(monkeypatch):
    async def fake_yfinance(self, result, symbol, market):
        result.data_source = "none"
        result.missing_fields.append("yfinance 返回空数据")

    monkeypatch.setattr(FundamentalFetcher, "_fetch_yfinance", fake_yfinance)
    monkeypatch.setattr(
        us_fallbacks,
        "fetch_us_fundamental_fallback",
        lambda symbol, latest_price=None: {
            "symbol": symbol,
            "company_name": "Apple Inc.",
            "industry": "Consumer Electronics",
            "revenue": 1234.5,
            "net_profit": 234.5,
            "revenue_yoy": None,
            "profit_yoy": None,
            "roe": 140.0,
            "eps": 6.0,
            "pe": 30.0,
            "pb": 45.0,
            "market_cap": None,
            "industry_pe": 28.0,
            "industry_pb": 18.0,
            "data_source": "sec_companyfacts+us_reference",
            "missing_fields": [],
        },
    )

    data = asyncio.run(FundamentalFetcher().fetch("AAPL", "US"))

    assert data.data_source == "sec_companyfacts+us_reference"
    assert data.company_name == "Apple Inc."
    assert data.latest_revenue == 1234.5
    assert data.pe == 30.0
    assert data.industry_pe == 28.0


def test_us_industry_fetcher_uses_reference_peers(monkeypatch):
    async def fake_yfinance_stock(self, result, symbol, market):
        return None

    monkeypatch.setattr(IndustryFetcher, "_fetch_yfinance_stock", fake_yfinance_stock)

    data = asyncio.run(IndustryFetcher().fetch_enhanced("AAPL", "US"))

    assert data["data_source"] == "us_peer_reference"
    assert data["company_name"] == "Apple Inc."
    assert data["industry_name"] == "Consumer Electronics"
    assert data["stock_metrics"]["pe"] == 30.0
    assert data["industry_average"]["stock_count"] > 0
    assert data["industry_peers_top"]


def test_sec_ticker_index_fills_unknown_us_ticker(monkeypatch):
    facts = {
        "Revenues": {"units": {"USD": [{"val": 1_000_000_000, "filed": "2026-02-01", "end": "2025-12-31"}]}},
        "NetIncomeLoss": {"units": {"USD": [{"val": 100_000_000, "filed": "2026-02-01", "end": "2025-12-31"}]}},
        "StockholdersEquity": {"units": {"USD": [{"val": 500_000_000, "filed": "2026-02-01", "end": "2025-12-31"}]}},
        "EarningsPerShareDiluted": {"units": {"USD/shares": [{"val": 2.5, "filed": "2026-02-01", "end": "2025-12-31"}]}},
    }

    monkeypatch.setattr(
        us_fallbacks,
        "_fetch_sec_ticker_index",
        lambda: {"XYZ": {"ticker": "XYZ", "cik": "0000000001", "title": "XYZ Corporation"}},
    )
    monkeypatch.setattr(us_fallbacks, "_fetch_sec_companyfacts", lambda cik: facts)

    data = us_fallbacks.fetch_us_fundamental_fallback("XYZ", latest_price=50.0)

    assert data["company_name"] == "XYZ Corporation"
    assert data["data_source"] == "sec_companyfacts"
    assert data["revenue"] == 10.0
    assert data["net_profit"] == 1.0
    assert data["roe"] == 20.0
    assert data["eps"] == 2.5
    assert data["pe"] == 20.0


def test_sec_fallback_calculates_yoy_and_market_cap(monkeypatch):
    facts = {
        "Revenues": {"units": {"USD": [
            {
                "val": 12_000_000_000,
                "start": "2025-01-01",
                "end": "2025-12-31",
                "filed": "2026-02-01",
                "fy": 2025,
                "fp": "FY",
            },
            {
                "val": 10_000_000_000,
                "start": "2024-01-01",
                "end": "2024-12-31",
                "filed": "2025-02-01",
                "fy": 2024,
                "fp": "FY",
            },
        ]}},
        "NetIncomeLoss": {"units": {"USD": [
            {
                "val": 2_000_000_000,
                "start": "2025-01-01",
                "end": "2025-12-31",
                "filed": "2026-02-01",
                "fy": 2025,
                "fp": "FY",
            },
            {
                "val": 1_000_000_000,
                "start": "2024-01-01",
                "end": "2024-12-31",
                "filed": "2025-02-01",
                "fy": 2024,
                "fp": "FY",
            },
        ]}},
        "StockholdersEquity": {"units": {"USD": [
            {"val": 8_000_000_000, "filed": "2026-02-01", "end": "2025-12-31"}
        ]}},
        "EarningsPerShareDiluted": {"units": {"USD/shares": [
            {"val": 5.0, "filed": "2026-02-01", "end": "2025-12-31"}
        ]}},
        "EntityCommonStockSharesOutstanding": {"units": {"shares": [
            {"val": 10_000_000, "filed": "2026-02-01", "end": "2025-12-31"}
        ]}},
    }

    monkeypatch.setattr(
        us_fallbacks,
        "_fetch_sec_ticker_index",
        lambda: {"XYZ": {"ticker": "XYZ", "cik": "0000000001", "title": "XYZ Corporation"}},
    )
    monkeypatch.setattr(us_fallbacks, "_fetch_sec_companyfacts", lambda cik: facts)

    data = us_fallbacks.fetch_us_fundamental_fallback("XYZ", latest_price=100.0)

    assert data["revenue"] == 120.0
    assert data["net_profit"] == 20.0
    assert round(data["revenue_yoy"], 2) == 20.0
    assert round(data["profit_yoy"], 2) == 100.0
    assert round(data["roe"], 2) == 25.0
    assert data["pe"] == 20.0
    assert data["market_cap"] == 10.0
