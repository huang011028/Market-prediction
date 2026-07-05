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
    "贵州茅台": "600519",
    "美的集团": "000333",
    "宁德时代": "300750",
    "比亚迪": "002594",
    "中兴通讯": "000063",
    "海康威视": "002415",
    "东方财富": "300059",
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


def identify_market(value: str) -> str:
    info = resolve_symbol(value)
    return info.market


def resolve_symbol(value: str) -> SymbolInfo:
    raw = value.strip()
    if not raw:
        return SymbolInfo(raw=value, symbol="", market="US", source="empty")

    code, suffix = _strip_suffix(raw)
    if suffix == ".HK":
        return SymbolInfo(raw=raw, symbol=code.zfill(4), market="HK", exchange_suffix=suffix, source="suffix")
    if suffix in {".SZ", ".SS", ".SH"}:
        return SymbolInfo(raw=raw, symbol=code.zfill(6), market="A", exchange_suffix=suffix, source="suffix")

    if code.isdigit():
        if len(code) == 6:
            return SymbolInfo(raw=raw, symbol=code, market="A", source="numeric")
        if len(code) <= 5:
            return SymbolInfo(raw=raw, symbol=code.zfill(4 if len(code) <= 4 else 5), market="HK", source="numeric")

    if raw in A_SHARE_NAME_TO_CODE:
        return SymbolInfo(raw=raw, symbol=A_SHARE_NAME_TO_CODE[raw], market="A", name=raw, source="builtin_a")
    if raw in HK_NAME_TO_CODE:
        return SymbolInfo(raw=raw, symbol=HK_NAME_TO_CODE[raw], market="HK", name=raw, source="builtin_hk")

    upper = raw.upper()
    if upper in HK_NAME_TO_CODE:
        return SymbolInfo(raw=raw, symbol=HK_NAME_TO_CODE[upper], market="HK", name=raw, source="builtin_hk")

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
    return None
