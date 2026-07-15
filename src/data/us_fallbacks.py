"""Fallback data sources for US-listed stocks.

These helpers are intentionally conservative. They fill obvious gaps caused by
yfinance throttling, but mark their source as fallback/reference so downstream
agents and the aggregator can keep confidence bounded.
"""

from __future__ import annotations

from datetime import datetime
from email.utils import parsedate_to_datetime
from functools import lru_cache
from io import StringIO
import logging
from typing import Optional
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET

import pandas as pd
import requests

logger = logging.getLogger(__name__)


SEC_USER_AGENT = "MarketPrediction/1.0 research@example.com"


US_COMPANY_REFERENCE: dict[str, dict] = {
    "AAPL": {
        "name": "Apple Inc.",
        "industry": "Consumer Electronics",
        "pe": 30.0,
        "pb": 45.0,
        "roe": 140.0,
        "peers": ["MSFT", "GOOGL", "NVDA", "META"],
        "cik": "0000320193",
    },
    "NVDA": {
        "name": "NVIDIA Corporation",
        "industry": "Semiconductors",
        "pe": 55.0,
        "pb": 50.0,
        "roe": 75.0,
        "peers": ["AMD", "AVGO", "INTC", "QCOM"],
        "cik": "0001045810",
    },
    "MSFT": {
        "name": "Microsoft Corporation",
        "industry": "Software",
        "pe": 35.0,
        "pb": 12.0,
        "roe": 35.0,
        "peers": ["AAPL", "GOOGL", "META", "ORCL"],
        "cik": "0000789019",
    },
    "ORCL": {
        "name": "Oracle Corporation",
        "industry": "Software",
        "pe": 32.0,
        "pb": 30.0,
        "roe": 95.0,
        "peers": ["MSFT", "ADBE", "CRM", "IBM"],
        "cik": "0001341439",
    },
    "GOOGL": {
        "name": "Alphabet Inc.",
        "industry": "Internet Content & Information",
        "pe": 25.0,
        "pb": 7.0,
        "roe": 28.0,
        "peers": ["META", "MSFT", "AMZN", "AAPL"],
        "cik": "0001652044",
    },
    "GOOG": {
        "name": "Alphabet Inc.",
        "industry": "Internet Content & Information",
        "pe": 25.0,
        "pb": 7.0,
        "roe": 28.0,
        "peers": ["META", "MSFT", "AMZN", "AAPL"],
        "cik": "0001652044",
    },
    "META": {
        "name": "Meta Platforms, Inc.",
        "industry": "Internet Content & Information",
        "pe": 28.0,
        "pb": 8.0,
        "roe": 30.0,
        "peers": ["GOOGL", "MSFT", "AMZN", "SNAP"],
        "cik": "0001326801",
    },
    "SNAP": {
        "name": "Snap Inc.",
        "industry": "Internet Content & Information",
        "pe": None,
        "pb": 8.0,
        "roe": -25.0,
        "peers": ["META", "GOOGL", "PINS", "RDDT"],
        "cik": "0001564408",
    },
    "AMZN": {
        "name": "Amazon.com, Inc.",
        "industry": "Internet Retail",
        "pe": 42.0,
        "pb": 8.0,
        "roe": 20.0,
        "peers": ["GOOGL", "META", "MSFT", "AAPL"],
        "cik": "0001018724",
    },
    "JD": {
        "name": "JD.com, Inc.",
        "industry": "Internet Retail",
        "pe": 12.0,
        "pb": 1.6,
        "roe": 13.0,
        "peers": ["BABA", "PDD", "AMZN", "WMT"],
        "cik": "0001549802",
    },
    "BABA": {
        "name": "Alibaba Group Holding Limited",
        "industry": "Internet Retail",
        "pe": 14.0,
        "pb": 1.8,
        "roe": 9.0,
        "peers": ["JD", "PDD", "AMZN", "WMT"],
        "cik": "0001577552",
    },
    "PDD": {
        "name": "PDD Holdings Inc.",
        "industry": "Internet Retail",
        "pe": 18.0,
        "pb": 5.0,
        "roe": 40.0,
        "peers": ["BABA", "JD", "AMZN", "WMT"],
        "cik": "0001737806",
    },
    "TSLA": {
        "name": "Tesla, Inc.",
        "industry": "Auto Manufacturers",
        "pe": 65.0,
        "pb": 10.0,
        "roe": 18.0,
        "peers": ["F", "GM", "RIVN", "NIO"],
        "cik": "0001318605",
    },
    "F": {
        "name": "Ford Motor Company",
        "industry": "Auto Manufacturers",
        "pe": 8.0,
        "pb": 1.0,
        "roe": 12.0,
        "peers": ["GM", "TSLA", "RIVN", "NIO"],
        "cik": "0000037996",
    },
    "GM": {
        "name": "General Motors Company",
        "industry": "Auto Manufacturers",
        "pe": 6.0,
        "pb": 0.8,
        "roe": 14.0,
        "peers": ["F", "TSLA", "RIVN", "NIO"],
        "cik": "0001467858",
    },
    "RIVN": {
        "name": "Rivian Automotive, Inc.",
        "industry": "Auto Manufacturers",
        "pe": None,
        "pb": 2.5,
        "roe": -35.0,
        "peers": ["TSLA", "F", "GM", "NIO"],
        "cik": "0001874178",
    },
    "NIO": {
        "name": "NIO Inc.",
        "industry": "Auto Manufacturers",
        "pe": None,
        "pb": 4.0,
        "roe": -75.0,
        "peers": ["TSLA", "F", "GM", "RIVN"],
        "cik": "0001736541",
    },
    "AMD": {
        "name": "Advanced Micro Devices, Inc.",
        "industry": "Semiconductors",
        "pe": 45.0,
        "pb": 4.0,
        "roe": 5.0,
        "peers": ["NVDA", "AVGO", "INTC", "QCOM"],
        "cik": "0000002488",
    },
    "AVGO": {
        "name": "Broadcom Inc.",
        "industry": "Semiconductors",
        "pe": 38.0,
        "pb": 18.0,
        "roe": 35.0,
        "peers": ["NVDA", "AMD", "INTC", "QCOM"],
        "cik": "0001730168",
    },
    "INTC": {
        "name": "Intel Corporation",
        "industry": "Semiconductors",
        "pe": 28.0,
        "pb": 1.1,
        "roe": 2.0,
        "peers": ["NVDA", "AMD", "AVGO", "QCOM"],
        "cik": "0000050863",
    },
    "QCOM": {
        "name": "QUALCOMM Incorporated",
        "industry": "Semiconductors",
        "pe": 18.0,
        "pb": 7.0,
        "roe": 35.0,
        "peers": ["NVDA", "AMD", "AVGO", "INTC"],
        "cik": "0000804328",
    },
    "JPM": {
        "name": "JPMorgan Chase & Co.",
        "industry": "Banks - Diversified",
        "pe": 12.0,
        "pb": 2.0,
        "roe": 16.0,
        "peers": ["BAC", "WFC", "C", "GS"],
        "cik": "0000019617",
    },
    "BAC": {
        "name": "Bank of America Corporation",
        "industry": "Banks - Diversified",
        "pe": 12.0,
        "pb": 1.2,
        "roe": 10.0,
        "peers": ["JPM", "WFC", "C", "GS"],
        "cik": "0000070858",
    },
    "GS": {
        "name": "The Goldman Sachs Group, Inc.",
        "industry": "Capital Markets",
        "pe": 14.0,
        "pb": 1.5,
        "roe": 10.0,
        "peers": ["JPM", "MS", "BAC", "C"],
        "cik": "0000886982",
    },
    "WMT": {
        "name": "Walmart Inc.",
        "industry": "Discount Stores",
        "pe": 34.0,
        "pb": 8.0,
        "roe": 22.0,
        "peers": ["COST", "TGT", "AMZN", "JD"],
        "cik": "0000104169",
    },
    "COST": {
        "name": "Costco Wholesale Corporation",
        "industry": "Discount Stores",
        "pe": 52.0,
        "pb": 15.0,
        "roe": 30.0,
        "peers": ["WMT", "TGT", "AMZN", "JD"],
        "cik": "0000909832",
    },
    "TGT": {
        "name": "Target Corporation",
        "industry": "Discount Stores",
        "pe": 16.0,
        "pb": 4.0,
        "roe": 25.0,
        "peers": ["WMT", "COST", "AMZN", "JD"],
        "cik": "0000027419",
    },
    "XOM": {
        "name": "Exxon Mobil Corporation",
        "industry": "Oil & Gas Integrated",
        "pe": 14.0,
        "pb": 2.0,
        "roe": 14.0,
        "peers": ["CVX", "COP", "SHEL", "BP"],
        "cik": "0000034088",
    },
    "CVX": {
        "name": "Chevron Corporation",
        "industry": "Oil & Gas Integrated",
        "pe": 15.0,
        "pb": 1.8,
        "roe": 13.0,
        "peers": ["XOM", "COP", "SHEL", "BP"],
        "cik": "0000093410",
    },
    "COP": {
        "name": "ConocoPhillips",
        "industry": "Oil & Gas E&P",
        "pe": 13.0,
        "pb": 2.2,
        "roe": 16.0,
        "peers": ["XOM", "CVX", "EOG", "OXY"],
        "cik": "0001163165",
    },
    "JNJ": {
        "name": "Johnson & Johnson",
        "industry": "Drug Manufacturers - General",
        "pe": 18.0,
        "pb": 5.0,
        "roe": 20.0,
        "peers": ["LLY", "PFE", "MRK", "ABBV"],
        "cik": "0000200406",
    },
    "LLY": {
        "name": "Eli Lilly and Company",
        "industry": "Drug Manufacturers - General",
        "pe": 55.0,
        "pb": 45.0,
        "roe": 70.0,
        "peers": ["JNJ", "PFE", "MRK", "ABBV"],
        "cik": "0000059478",
    },
    "PFE": {
        "name": "Pfizer Inc.",
        "industry": "Drug Manufacturers - General",
        "pe": 12.0,
        "pb": 1.6,
        "roe": 8.0,
        "peers": ["JNJ", "LLY", "MRK", "ABBV"],
        "cik": "0000078003",
    },
    "MRK": {
        "name": "Merck & Co., Inc.",
        "industry": "Drug Manufacturers - General",
        "pe": 15.0,
        "pb": 6.0,
        "roe": 35.0,
        "peers": ["JNJ", "LLY", "PFE", "ABBV"],
        "cik": "0000310158",
    },
    "KO": {
        "name": "The Coca-Cola Company",
        "industry": "Beverages - Non-Alcoholic",
        "pe": 24.0,
        "pb": 10.0,
        "roe": 40.0,
        "peers": ["PEP", "MNST", "KDP", "KHC"],
        "cik": "0000021344",
    },
    "PEP": {
        "name": "PepsiCo, Inc.",
        "industry": "Beverages - Non-Alcoholic",
        "pe": 22.0,
        "pb": 12.0,
        "roe": 50.0,
        "peers": ["KO", "MNST", "KDP", "KHC"],
        "cik": "0000077476",
    },
}


US_INDUSTRY_REFERENCE: dict[str, dict] = {
    "Consumer Electronics": {"pe": 28.0, "pb": 18.0, "roe": 40.0, "note": "US reference"},
    "Semiconductors": {"pe": 42.0, "pb": 12.0, "roe": 28.0, "note": "US reference"},
    "Software": {"pe": 34.0, "pb": 10.0, "roe": 30.0, "note": "US reference"},
    "Internet Content & Information": {"pe": 30.0, "pb": 7.5, "roe": 26.0, "note": "US reference"},
    "Internet Retail": {"pe": 38.0, "pb": 7.0, "roe": 18.0, "note": "US reference"},
    "Auto Manufacturers": {"pe": 30.0, "pb": 4.0, "roe": 15.0, "note": "US reference"},
    "Banks - Diversified": {"pe": 12.0, "pb": 1.4, "roe": 12.0, "note": "US reference"},
    "Capital Markets": {"pe": 14.0, "pb": 1.5, "roe": 11.0, "note": "US reference"},
    "Discount Stores": {"pe": 34.0, "pb": 8.0, "roe": 24.0, "note": "US reference"},
    "Oil & Gas Integrated": {"pe": 14.0, "pb": 2.0, "roe": 14.0, "note": "US reference"},
    "Oil & Gas E&P": {"pe": 13.0, "pb": 2.0, "roe": 15.0, "note": "US reference"},
    "Drug Manufacturers - General": {"pe": 20.0, "pb": 7.0, "roe": 22.0, "note": "US reference"},
    "Beverages - Non-Alcoholic": {"pe": 24.0, "pb": 10.0, "roe": 40.0, "note": "US reference"},
}


def get_us_company_reference(symbol: str) -> dict:
    """Return static company/industry reference metadata for common US tickers."""
    return dict(US_COMPANY_REFERENCE.get(symbol.strip().upper(), {}))


def get_us_industry_reference(industry: str) -> Optional[dict]:
    if not industry:
        return None
    return US_INDUSTRY_REFERENCE.get(industry)


def fetch_us_ohlcv_stooq(symbol: str, start_date: str, timeout: int = 12) -> Optional[pd.DataFrame]:
    """Fetch daily OHLCV from Stooq CSV endpoint."""
    code = symbol.strip().lower()
    if not code:
        return None
    if not code.endswith(".us"):
        code = f"{code}.us"
    d1 = _compact_date(start_date)
    d2 = datetime.now().strftime("%Y%m%d")
    url = f"https://stooq.com/q/d/l/?s={code}&d1={d1}&d2={d2}&i=d"
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        text = resp.text.strip()
        if not text or "No data" in text:
            return None
        df = pd.read_csv(StringIO(text))
        if df.empty or "Close" not in df.columns:
            return None
        df = df.rename(columns={
            "Date": "date",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        })
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        return df[["open", "high", "low", "close", "volume"]]
    except Exception as exc:
        logger.debug("Stooq US OHLCV fallback failed for %s: %s", symbol, exc)
        return None


def fetch_us_ohlcv_akshare(symbol: str, start_date: str) -> Optional[pd.DataFrame]:
    """Fetch US daily OHLCV through akshare's stock_us_daily endpoint."""
    symbol = symbol.strip().upper()
    if not symbol:
        return None
    try:
        import akshare as ak

        df = ak.stock_us_daily(symbol=symbol, adjust="")
        if df is None or df.empty:
            return None

        df = df.rename(columns={
            "date": "date",
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "volume": "volume",
        })
        if "date" not in df.columns or "close" not in df.columns:
            return None
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        start_dt = pd.to_datetime(_compact_date(start_date), format="%Y%m%d", errors="coerce")
        if pd.notna(start_dt):
            df = df[df.index >= start_dt]
        if df.empty:
            return None
        return df[["open", "high", "low", "close", "volume"]]
    except Exception as exc:
        logger.debug("akshare US OHLCV fallback failed for %s: %s", symbol, exc)
        return None


def fetch_us_news_google(symbol: str, company_name: str = "", days: int = 14, max_items: int = 20) -> list[dict]:
    """Fetch US stock news from Google News RSS search."""
    query_parts = [symbol.strip().upper(), "stock"]
    if company_name:
        query_parts.insert(0, f'"{company_name}"')
    query = " ".join(query_parts + [f"when:{max(1, days)}d"])
    url = (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
    )
    try:
        resp = requests.get(url, timeout=12)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        items = []
        for item in root.findall(".//item")[:max_items]:
            title = _text(item, "title")
            source = _text(item, "source") or "Google News"
            items.append({
                "title": title,
                "summary": _text(item, "description")[:500],
                "source": source,
                "time": _normalize_rss_time(_text(item, "pubDate")),
                "url": _text(item, "link"),
            })
        return items
    except Exception as exc:
        logger.debug("Google News fallback failed for %s: %s", symbol, exc)
        return []


def fetch_us_fundamental_fallback(symbol: str, latest_price: float | None = None) -> dict:
    """Fetch rough US fundamentals from SEC companyfacts plus static reference."""
    symbol = symbol.strip().upper()
    reference = get_us_company_reference(symbol)
    industry_ref = get_us_industry_reference(reference.get("industry", ""))
    result = {
        "symbol": symbol,
        "company_name": reference.get("name", symbol),
        "industry": reference.get("industry", ""),
        "revenue": None,
        "net_profit": None,
        "revenue_yoy": None,
        "profit_yoy": None,
        "roe": reference.get("roe"),
        "eps": None,
        "pe": reference.get("pe"),
        "pb": reference.get("pb"),
        "industry_pe": industry_ref.get("pe") if industry_ref else None,
        "industry_pb": industry_ref.get("pb") if industry_ref else None,
        "market_cap": None,
        "data_source": "us_reference",
        "missing_fields": [],
    }

    sec_company = _lookup_sec_company(symbol) if not reference.get("cik") else {}
    if sec_company and result["company_name"] == symbol:
        result["company_name"] = sec_company.get("title", symbol)

    cik = reference.get("cik") or sec_company.get("cik")
    if cik:
        facts = _fetch_sec_companyfacts(cik)
        if facts:
            revenue, revenue_yoy = _latest_fact_with_yoy(facts, [
                "RevenueFromContractWithCustomerExcludingAssessedTax",
                "Revenues",
                "SalesRevenueNet",
            ])
            net_income, profit_yoy = _latest_fact_with_yoy(facts, ["NetIncomeLoss"])
            equity = _latest_fact(facts, ["StockholdersEquity"])
            eps = _latest_fact(facts, ["EarningsPerShareDiluted"], unit_hint="USD/shares")
            shares = _latest_fact(facts, [
                "EntityCommonStockSharesOutstanding",
                "WeightedAverageNumberOfDilutedSharesOutstanding",
                "WeightedAverageNumberOfSharesOutstandingBasic",
            ], unit_hint="shares")

            if revenue is not None:
                result["revenue"] = revenue / 1e8
            if net_income is not None:
                result["net_profit"] = net_income / 1e8
            if revenue_yoy is not None:
                result["revenue_yoy"] = revenue_yoy
            if profit_yoy is not None:
                result["profit_yoy"] = profit_yoy
            if equity and net_income is not None:
                result["roe"] = (net_income / equity) * 100
            if eps is not None:
                result["eps"] = eps
                if latest_price and eps > 0:
                    result["pe"] = latest_price / eps
            if latest_price is None:
                latest_price = _fetch_us_latest_close(symbol)
            if latest_price and shares:
                result["market_cap"] = latest_price * shares / 1e8
            elif result["pe"] and result["net_profit"] and result["net_profit"] > 0:
                result["market_cap"] = result["pe"] * result["net_profit"]
            result["data_source"] = (
                "sec_companyfacts+us_reference"
                if reference
                else "sec_companyfacts"
            )

    for field in ("revenue", "net_profit", "revenue_yoy", "profit_yoy", "roe", "pe", "pb", "market_cap"):
        if result.get(field) is None:
            result["missing_fields"].append(field)
    return result


def build_us_industry_peers(symbol: str) -> tuple[str, list[dict], Optional[dict]]:
    """Build reference peer rows for US industry analysis."""
    symbol = symbol.strip().upper()
    ref = get_us_company_reference(symbol)
    industry = ref.get("industry", "")
    peer_symbols = ref.get("peers", [])
    peers = []
    for peer_symbol in peer_symbols:
        peer_ref = get_us_company_reference(peer_symbol)
        if not peer_ref:
            continue
        peers.append({
            "code": peer_symbol,
            "name": peer_ref.get("name", peer_symbol),
            "pe": peer_ref.get("pe"),
            "pb": peer_ref.get("pb"),
            "roe": peer_ref.get("roe"),
            "source": "us_reference",
        })
    return industry, peers, get_us_industry_reference(industry)


def _compact_date(value: str) -> str:
    value = str(value or "").strip()
    if "-" in value:
        return value.replace("-", "")
    return value or datetime.now().strftime("%Y%m%d")


def _text(node: ET.Element, child: str) -> str:
    found = node.find(child)
    return (found.text or "").strip() if found is not None else ""


def _normalize_rss_time(value: str) -> str:
    if not value:
        return ""
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return value


def _lookup_sec_company(symbol: str) -> dict:
    index = _fetch_sec_ticker_index()
    return index.get(symbol.strip().upper(), {})


@lru_cache(maxsize=1)
def _fetch_sec_ticker_index() -> dict[str, dict]:
    url = "https://www.sec.gov/files/company_tickers.json"
    try:
        resp = requests.get(url, headers={"User-Agent": SEC_USER_AGENT}, timeout=15)
        resp.raise_for_status()
        raw = resp.json()
        companies = raw.values() if isinstance(raw, dict) else raw
        result: dict[str, dict] = {}
        for row in companies:
            ticker = str(row.get("ticker", "")).upper()
            cik = row.get("cik_str")
            if ticker and cik:
                result[ticker] = {
                    "ticker": ticker,
                    "cik": str(cik).zfill(10),
                    "title": row.get("title", ""),
                }
        return result
    except Exception as exc:
        logger.debug("SEC ticker index fallback failed: %s", exc)
        return {}


def _fetch_sec_companyfacts(cik: str) -> dict:
    cik10 = str(cik).zfill(10)
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"
    try:
        resp = requests.get(url, headers={"User-Agent": SEC_USER_AGENT}, timeout=15)
        resp.raise_for_status()
        facts = resp.json().get("facts", {})
        merged = dict(facts.get("us-gaap", {}))
        merged.update(facts.get("dei", {}))
        return merged
    except Exception as exc:
        logger.debug("SEC companyfacts fallback failed for CIK %s: %s", cik, exc)
        return {}


def _fetch_us_latest_close(symbol: str) -> Optional[float]:
    start = pd.Timestamp.now().normalize() - pd.Timedelta(days=45)
    df = fetch_us_ohlcv_akshare(symbol, start_date=start.strftime("%Y%m%d"))
    if df is None or df.empty or "close" not in df.columns:
        return None
    try:
        return float(df["close"].dropna().iloc[-1])
    except Exception:
        return None


def _latest_fact(facts: dict, tags: list[str], unit_hint: str = "USD") -> Optional[float]:
    for tag in tags:
        candidates = _fact_candidates(facts, tag, unit_hint)
        if not candidates:
            continue
        try:
            return float(candidates[0]["val"])
        except (TypeError, ValueError):
            continue
    return None


def _latest_fact_with_yoy(
    facts: dict,
    tags: list[str],
    unit_hint: str = "USD",
) -> tuple[Optional[float], Optional[float]]:
    for tag in tags:
        candidates = _fact_candidates(facts, tag, unit_hint)
        if not candidates:
            continue
        latest = candidates[0]
        latest_value = _row_float(latest)
        prior = _find_prior_yoy_row(latest, candidates[1:])
        prior_value = _row_float(prior) if prior else None
        yoy = None
        if latest_value is not None and prior_value not in (None, 0):
            yoy = (latest_value / prior_value - 1) * 100
        return latest_value, yoy
    return None, None


def _fact_candidates(facts: dict, tag: str, unit_hint: str) -> list[dict]:
    fact = facts.get(tag)
    if not fact:
        return []
    units = fact.get("units", {})
    rows = units.get(unit_hint) or next(iter(units.values()), [])
    candidates = [
        row for row in rows
        if row.get("val") is not None and row.get("end")
    ]
    candidates.sort(key=lambda row: (row.get("filed", ""), row.get("end", "")), reverse=True)
    return candidates


def _find_prior_yoy_row(latest: dict, candidates: list[dict]) -> Optional[dict]:
    latest_end = _parse_iso_date(latest.get("end", ""))
    if latest_end is None:
        return None
    latest_fp = str(latest.get("fp", ""))
    latest_duration = _row_duration_days(latest)

    scored = []
    for row in candidates:
        end = _parse_iso_date(row.get("end", ""))
        if end is None or end >= latest_end:
            continue
        day_gap = (latest_end - end).days
        if day_gap < 250 or day_gap > 460:
            continue
        duration = _row_duration_days(row)
        duration_penalty = abs((duration or latest_duration or 0) - (latest_duration or duration or 0))
        if duration_penalty > 45:
            continue
        fp_penalty = 0 if latest_fp and str(row.get("fp", "")) == latest_fp else 30
        gap_penalty = abs(day_gap - 365)
        scored.append((duration_penalty + fp_penalty + gap_penalty, row))

    if not scored:
        return None
    scored.sort(key=lambda item: item[0])
    return scored[0][1]


def _row_float(row: Optional[dict]) -> Optional[float]:
    if not row:
        return None
    try:
        return float(row["val"])
    except (KeyError, TypeError, ValueError):
        return None


def _row_duration_days(row: dict) -> Optional[int]:
    start = _parse_iso_date(row.get("start", ""))
    end = _parse_iso_date(row.get("end", ""))
    if start is None or end is None:
        return None
    return max((end - start).days, 0)


def _parse_iso_date(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
