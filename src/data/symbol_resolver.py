"""统一标的解析。

把中文名、常见简称、带后缀代码标准化为内部使用的 symbol/market。
网络数据源不可用时仍依赖内置映射保证常用样例可跑。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import logging

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SymbolInfo:
    raw: str
    symbol: str
    market: str
    name: str = ""
    exchange_suffix: str = ""
    source: str = "rule"

    @property
    def display_name(self) -> str:
        if self.name and self.name != self.symbol:
            return f"{self.name}({self.symbol})"
        return self.symbol


A_SHARE_NAME_TO_CODE = {
    "星网锐捷": "002396",
    "平安银行": "000001",
    "万科": "000002",
    "万科A": "000002",
    "贵州茅台": "600519",
    "茅台": "600519",
    "美的集团": "000333",
    "美的": "000333",
    "宁德时代": "300750",
    "宁德": "300750",
    "比亚迪": "002594",
    "中兴通讯": "000063",
    "海康威视": "002415",
    "海康": "002415",
    "东方财富": "300059",
    "格力电器": "000651",
    "格力": "000651",
    "五粮液": "000858",
    "泸州老窖": "000568",
    "长江电力": "600900",
    "长电": "600900",
    "紫金矿业": "601899",
    "紫金": "601899",
    "中信证券": "600030",
    "中信": "600030",
    "中信银行": "601998",
    "招商银行": "600036",
    "招行": "600036",
    "中国平安": "601318",
    "平安": "601318",
    "伊利股份": "600887",
    "伊利": "600887",
    "恒瑞医药": "600276",
    "迈瑞医疗": "300760",
    "立讯精密": "002475",
    "立讯": "002475",
    "隆基绿能": "601012",
    "三一重工": "600031",
    "中国中免": "601888",
    "中免": "601888",
    "山西汾酒": "600809",
    "汾酒": "600809",
    "海天味业": "603288",
    "海天": "603288",
}

HK_NAME_TO_CODE = {
    "美团": "3690",
    "美团-W": "3690",
    "腾讯": "0700",
    "腾讯控股": "0700",
    "阿里巴巴": "9988",
    "阿里": "9988",
    "百度": "9888",
    "京东": "9618",
    "小米": "1810",
    "小米集团": "1810",
    "快手": "1024",
    "网易": "9999",
    "哔哩哔哩": "9626",
    "B站": "9626",
    "商汤": "00020",
    "海底捞": "6862",
    "安踏": "2020",
    "李宁": "2331",
    "华润啤酒": "00291",
    "青岛啤酒": "00168",
    "中芯国际": "00981",
    "中信股份": "00267",
    "中信": "00267",
    "中信银行": "00998",
    "药明生物": "2269",
    "信达生物": "1801",
    "百济神州": "6160",
    "君实生物": "1877",
}


def _strip_suffix(value: str) -> tuple[str, str]:
    s = value.strip().upper()
    for suffix in (".HK", ".SZ", ".SS", ".SH"):
        if s.endswith(suffix):
            return s[: -len(suffix)], suffix
    return s, ""


def _normalize_market_hint(market_hint: str | None) -> str | None:
    """将前端市场模式规整为 A/HK/US。"""
    if not market_hint:
        return None
    market = str(market_hint).strip().upper()
    aliases = {
        "A": "A",
        "ASHARE": "A",
        "A_SHARE": "A",
        "A股": "A",
        "CN": "A",
        "CHINA": "A",
        "沪深": "A",
        "HK": "HK",
        "H": "HK",
        "港股": "HK",
        "US": "US",
        "USA": "US",
        "美股": "US",
    }
    return aliases.get(market)


def identify_market(value: str, market_hint: str | None = None) -> str:
    info = resolve_symbol(value, market_hint=market_hint)
    return info.market


def resolve_symbol(value: str, market_hint: str | None = None) -> SymbolInfo:
    raw = str(value or "").strip()
    hint = _normalize_market_hint(market_hint)
    if not raw:
        return SymbolInfo(raw=value, symbol="", market=hint or "US", source="empty")

    code, suffix = _strip_suffix(raw)
    if suffix == ".HK":
        return SymbolInfo(raw=raw, symbol=code.zfill(4), market="HK", exchange_suffix=suffix, source="suffix")
    if suffix in {".SZ", ".SS", ".SH"}:
        return SymbolInfo(raw=raw, symbol=code.zfill(6), market="A", exchange_suffix=suffix, source="suffix")

    upper = raw.upper()

    if hint == "A":
        if raw in A_SHARE_NAME_TO_CODE:
            return SymbolInfo(raw=raw, symbol=A_SHARE_NAME_TO_CODE[raw], market="A", name=raw, source="hint_a")
        if code.isdigit():
            return SymbolInfo(raw=raw, symbol=code.zfill(6), market="A", source="hint_a")
        if _contains_cjk(raw):
            ak_match = _resolve_a_share_with_akshare(raw)
            if ak_match:
                return SymbolInfo(raw=raw, symbol=ak_match[0], market="A", name=ak_match[1], source="akshare")
        return SymbolInfo(raw=raw, symbol=upper, market="A", name=raw if raw != upper else "", source="hint_a")

    if hint == "HK":
        if raw in HK_NAME_TO_CODE:
            return SymbolInfo(raw=raw, symbol=HK_NAME_TO_CODE[raw], market="HK", name=raw, source="hint_hk")
        if upper in HK_NAME_TO_CODE:
            return SymbolInfo(raw=raw, symbol=HK_NAME_TO_CODE[upper], market="HK", name=raw, source="hint_hk")
        if code.isdigit():
            return SymbolInfo(raw=raw, symbol=code.zfill(4 if len(code) <= 4 else 5), market="HK", source="hint_hk")
        return SymbolInfo(raw=raw, symbol=upper, market="HK", name=raw if raw != upper else "", source="hint_hk")

    if hint == "US":
        return SymbolInfo(raw=raw, symbol=upper, market="US", name=raw if raw != upper else "", source="hint_us")

    if code.isdigit():
        if len(code) == 6:
            return SymbolInfo(raw=raw, symbol=code, market="A", source="numeric")
        if len(code) <= 5:
            return SymbolInfo(raw=raw, symbol=code.zfill(4 if len(code) <= 4 else 5), market="HK", source="numeric")

    if raw in A_SHARE_NAME_TO_CODE:
        return SymbolInfo(raw=raw, symbol=A_SHARE_NAME_TO_CODE[raw], market="A", name=raw, source="builtin_a")
    if raw in HK_NAME_TO_CODE:
        return SymbolInfo(raw=raw, symbol=HK_NAME_TO_CODE[raw], market="HK", name=raw, source="builtin_hk")

    if upper in HK_NAME_TO_CODE:
        return SymbolInfo(raw=raw, symbol=HK_NAME_TO_CODE[upper], market="HK", name=raw, source="builtin_hk")

    if _contains_cjk(raw):
        ak_match = _resolve_a_share_with_akshare(raw)
        if ak_match:
            return SymbolInfo(raw=raw, symbol=ak_match[0], market="A", name=ak_match[1], source="akshare")

    return SymbolInfo(raw=raw, symbol=upper, market="US", name=raw if raw != upper else "", source="fallback_us")


@lru_cache(maxsize=1)
def _a_share_name_table() -> dict[str, tuple[str, str]]:
    try:
        import akshare as ak

        df = ak.stock_info_a_code_name()
    except Exception as e:
        logger.debug(f"A股名称表加载失败: {e}")
        return {}

    table: dict[str, tuple[str, str]] = {}
    if df is None or df.empty:
        return table

    for _, row in df.iterrows():
        code = str(row.get("code", "")).zfill(6)
        name = str(row.get("name", "")).strip()
        if code and name:
            table[name] = (code, name)
    return table


def _resolve_a_share_with_akshare(name: str) -> tuple[str, str] | None:
    table = _a_share_name_table()
    if not table:
        return None
    if name in table:
        return table[name]
    lowered = name.lower()
    for stock_name, item in table.items():
        if lowered == stock_name.lower():
            return item
    query = _normalize_name_key(name)
    if len(query) < 2:
        return None
    matches: list[tuple[int, int, str, tuple[str, str]]] = []
    for stock_name, item in table.items():
        key = _normalize_name_key(stock_name)
        if not key:
            continue
        if key == query:
            return item
        if key.startswith(query):
            matches.append((1, len(key), stock_name, item))
        elif query in key:
            matches.append((2, len(key), stock_name, item))
    if not matches:
        return None
    matches.sort(key=lambda row: (row[0], row[1], row[2]))
    best = matches[0]
    if len(matches) > 1 and matches[1][0] == best[0] and matches[1][1] == best[1]:
        logger.debug(
            "A股名称模糊匹配存在同级歧义: query=%s candidates=%s",
            name,
            [m[2] for m in matches[:5]],
        )
        return None
    return best[3]


def _normalize_name_key(value: str) -> str:
    return (
        str(value or "")
        .strip()
        .lower()
        .replace(" ", "")
        .replace("　", "")
        .replace("-", "")
        .replace("_", "")
        .replace("（", "(")
        .replace("）", ")")
    )


def _contains_cjk(value: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in value)
