"""
宏观数据获取器 v2

Phase A 升级：
- LPR/M2 通过 akshare 实时获取（消除硬编码）
- DXY/US10Y/VIX 多源尝试（FRED → Yahoo → scrape → 参考值）
- 每个指标标注数据新鲜度和来源
- Fed利率补充
"""

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 参考值存储文件（持续更新的兜底值）
_REFERENCE_CACHE_PATH = Path(__file__).parent / ".macro_reference_cache.json"


@dataclass
class MacroDataV2:
    """宏观数据封装 v2 — 每个指标附带新鲜度信息"""

    # === 中国市场（实时） ===
    cn_cpi_yoy: Optional[float] = None
    cn_cpi_date: str = ""
    cn_pmi_manufacturing: Optional[float] = None
    cn_pmi_date: str = ""
    cn_gdp_yoy: Optional[float] = None
    cn_gdp_date: str = ""
    cn_lpr_1y: Optional[float] = None
    cn_lpr_date: str = ""
    cn_m2_yoy: Optional[float] = None
    cn_m2_date: str = ""

    # === 汇率（实时） ===
    usd_cny: Optional[float] = None
    usd_cnh: Optional[float] = None

    # === 美国市场（多源尝试） ===
    us_10y_yield: Optional[float] = None
    us_10y_date: str = ""
    us_10y_source: str = ""  # "fred" / "yfinance" / "scrape" / "reference"
    dxy: Optional[float] = None
    dxy_date: str = ""
    dxy_source: str = ""
    vix: Optional[float] = None
    vix_date: str = ""
    vix_source: str = ""
    fed_funds_rate: Optional[float] = None
    fed_funds_date: str = ""
    fed_funds_source: str = ""

    # === 元信息 ===
    data_source: str = "none"
    fetch_timestamp: str = ""
    reference_fields: list = field(default_factory=list)

    def to_agent_dict(self) -> dict:
        """转为 Agent 可用的字典格式（v2：每个指标附带新鲜度）"""

        def fmt(v):
            if v is None:
                return "N/A"
            return round(float(v), 2)

        def freshness(date_str: str, source: str = "") -> str:
            """返回数据新鲜度标签"""
            if not date_str:
                return "未知"
            if "reference" in source:
                return f"参考值(最近更新:{date_str})"
            if "scrape" in source:
                return f"实时爬取({date_str})"
            if source in ("fred", "yfinance"):
                return f"实时API({date_str})"
            return f"实时({date_str})"

        return {
            "data_source": self.data_source,
            "fetch_time": self.fetch_timestamp,
            "reference_fields": self.reference_fields,
            "china": {
                "cpi_yoy_pct": fmt(self.cn_cpi_yoy),
                "cpi_freshness": freshness(self.cn_cpi_date),
                "pmi_manufacturing": fmt(self.cn_pmi_manufacturing),
                "pmi_freshness": freshness(self.cn_pmi_date),
                "gdp_yoy_pct": fmt(self.cn_gdp_yoy),
                "gdp_freshness": freshness(self.cn_gdp_date),
                "lpr_1y_pct": fmt(self.cn_lpr_1y),
                "lpr_freshness": freshness(self.cn_lpr_date),
                "m2_yoy_pct": fmt(self.cn_m2_yoy),
                "m2_freshness": freshness(self.cn_m2_date),
            },
            "forex": {
                "usd_cny": fmt(self.usd_cny),
                "usd_cnh": fmt(self.usd_cnh),
                "dxy": fmt(self.dxy),
                "dxy_freshness": freshness(self.dxy_date, self.dxy_source),
            },
            "us": {
                "fed_funds_rate_pct": fmt(self.fed_funds_rate),
                "fed_funds_freshness": freshness(self.fed_funds_date, self.fed_funds_source),
                "10y_yield_pct": fmt(self.us_10y_yield),
                "10y_freshness": freshness(self.us_10y_date, self.us_10y_source),
                "vix": fmt(self.vix),
                "vix_freshness": freshness(self.vix_date, self.vix_source),
            },
            "data_quality": {
                "realtime_count": self._count_realtime(),
                "reference_count": len(self.reference_fields),
                "overall_freshness": f"{self._freshness_score():.0%}",
            },
        }

    def _count_realtime(self) -> int:
        """计算实时数据指标数"""
        count = 0
        for field in ["cn_cpi_yoy", "cn_pmi_manufacturing", "cn_gdp_yoy",
                       "cn_lpr_1y", "cn_m2_yoy", "usd_cny", "usd_cnh"]:
            if getattr(self, field) is not None:
                count += 1
        for field, src_field in [("us_10y_yield", "us_10y_source"),
                                  ("dxy", "dxy_source"),
                                  ("vix", "vix_source"),
                                  ("fed_funds_rate", "fed_funds_source")]:
            src = getattr(self, src_field, "")
            if getattr(self, field) is not None and "reference" not in src:
                count += 1
        return count

    def _freshness_score(self) -> float:
        """综合数据新鲜度评分"""
        # 实时数据占比 + 美国数据是否新鲜
        total = 11  # 总指标数
        realtime = self._count_realtime()
        base = realtime / total

        # 美国关键指标惩罚：如果是参考值，大打折
        us_fresh = 0
        if self.us_10y_source and "reference" not in self.us_10y_source:
            us_fresh += 1
        if self.dxy_source and "reference" not in self.dxy_source:
            us_fresh += 1
        if self.vix_source and "reference" not in self.vix_source:
            us_fresh += 1
        base = base * 0.7 + (us_fresh / 3) * 0.3

        return min(1.0, base)


class MacroFetcherV2:
    """宏观数据获取器 v2 — 消除硬编码，多源实时化"""

    def __init__(self):
        self._ref_cache = self._load_reference_cache()

    # ================================================================
    # 主入口
    # ================================================================

    async def fetch(self, target: str, market: str) -> MacroDataV2:
        """获取宏观数据（v2：全实时化尝试）"""
        result = MacroDataV2()
        result.fetch_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        today_str = datetime.now().strftime("%Y-%m-%d")

        # === 中国市场实时数据 ===
        await self._fetch_china_data(result, today_str)

        # === 汇率（新浪实时） ===
        await self._fetch_forex(result)

        # === 美国市场数据（多源尝试） ===
        await self._fetch_us_data(result, today_str)

        # === 统计 ===
        result.data_source = "eastmoney+sina+akshare" if result._count_realtime() > 3 else "partial"
        result.reference_fields = [
            f for f, s in [
                ("DXY", result.dxy_source),
                ("VIX", result.vix_source),
                ("US10Y", result.us_10y_source),
                ("Fed利率", result.fed_funds_source),
            ] if "reference" in s
        ]

        # 更新参考值缓存
        self._update_reference_cache(result)

        logger.info(
            f"宏观数据 v2: 实时{result._count_realtime()}/11项, "
            f"参考{len(result.reference_fields)}项, "
            f"新鲜度={result._freshness_score():.0%}"
        )
        return result

    # ================================================================
    # 中国市场
    # ================================================================

    async def _fetch_china_data(self, result: MacroDataV2, today_str: str):
        """中国宏观数据（东方财富 DataCenter + akshare）"""
        import requests

        # CPI/PMI/GDP（东方财富 DataCenter — 保持原有逻辑）
        try:
            cpi = self._fetch_em("RPT_ECONOMY_CPI", "NATIONAL_SAME")
            if cpi is not None:
                result.cn_cpi_yoy = cpi
                result.cn_cpi_date = today_str

            pmi = self._fetch_em("RPT_ECONOMY_PMI", "MAKE_INDEX")
            if pmi is not None:
                result.cn_pmi_manufacturing = pmi
                result.cn_pmi_date = today_str

            gdp = self._fetch_em("RPT_ECONOMY_GDP", "SUM_SAME")
            if gdp is not None:
                result.cn_gdp_yoy = gdp
                result.cn_gdp_date = today_str

        except Exception as e:
            logger.warning(f"东方财富 DataCenter 异常: {e}")

        # LPR（akshare — 新增实时化）
        try:
            lpr_val, lpr_date = self._fetch_lpr()
            if lpr_val is not None:
                result.cn_lpr_1y = lpr_val
                result.cn_lpr_date = lpr_date
                logger.info(f"LPR 1Y: {lpr_val}% ({lpr_date})")
        except Exception as e:
            logger.warning(f"LPR 获取失败: {e}")

        # M2（akshare — 新增实时化）
        try:
            m2_val, m2_date = self._fetch_m2()
            if m2_val is not None:
                result.cn_m2_yoy = m2_val
                result.cn_m2_date = m2_date
                logger.info(f"M2 同比: {m2_val}% ({m2_date})")
        except Exception as e:
            logger.warning(f"M2 获取失败: {e}")

    # ================================================================
    # 汇率
    # ================================================================

    async def _fetch_forex(self, result: MacroDataV2):
        """汇率数据（新浪实时）"""
        import requests

        try:
            resp = requests.get(
                "https://hq.sinajs.cn/list=fx_susdcny",
                headers={"Referer": "https://finance.sina.com.cn"},
                timeout=10, verify=False,
            )
            if "var" in resp.text:
                fields = resp.text.split("=")[1].strip('";\n ').split(",")
                if len(fields) >= 2:
                    result.usd_cny = self._safe_float(fields[1])
        except Exception as e:
            logger.debug(f"USDCNY 失败: {e}")

        try:
            resp = requests.get(
                "https://hq.sinajs.cn/list=fx_susdcnh",
                headers={"Referer": "https://finance.sina.com.cn"},
                timeout=10, verify=False,
            )
            if "var" in resp.text:
                fields = resp.text.split("=")[1].strip('";\n ').split(",")
                if len(fields) >= 2:
                    result.usd_cnh = self._safe_float(fields[1])
        except Exception as e:
            logger.debug(f"USDCNH 失败: {e}")

    # ================================================================
    # 美国市场（多源尝试）
    # ================================================================

    async def _fetch_us_data(self, result: MacroDataV2, today_str: str):
        """美国市场数据：多源尝试 → 参考值兜底"""

        # --- US 10Y 收益率 ---
        us10y = await self._try_fetch_us10y()
        if us10y:
            result.us_10y_yield, result.us_10y_source, result.us_10y_date = us10y
        else:
            ref = self._get_reference("us_10y")
            result.us_10y_yield = ref
            result.us_10y_source = "reference"
            result.us_10y_date = self._ref_cache.get("us_10y_date", "未知")

        # --- DXY ---
        dxy = await self._try_fetch_dxy()
        if dxy:
            result.dxy, result.dxy_source, result.dxy_date = dxy
        else:
            ref = self._get_reference("dxy")
            result.dxy = ref
            result.dxy_source = "reference"
            result.dxy_date = self._ref_cache.get("dxy_date", "未知")

        # --- VIX ---
        vix = await self._try_fetch_vix()
        if vix:
            result.vix, result.vix_source, result.vix_date = vix
        else:
            ref = self._get_reference("vix")
            result.vix = ref
            result.vix_source = "reference"
            result.vix_date = self._ref_cache.get("vix_date", "未知")

        # --- Fed Funds Rate ---
        fed = await self._try_fetch_fed_rate()
        if fed:
            result.fed_funds_rate, result.fed_funds_source, result.fed_funds_date = fed
        else:
            ref = self._get_reference("fed_funds")
            result.fed_funds_rate = ref
            result.fed_funds_source = "reference"
            result.fed_funds_date = self._ref_cache.get("fed_funds_date", "未知")

    # ================================================================
    # US10Y 多源尝试
    # ================================================================

    async def _try_fetch_us10y(self) -> Optional[tuple]:
        """尝试获取美国10年期国债收益率"""
        # 1. Yahoo Finance ^TNX
        try:
            val = self._fetch_yf_quote("^TNX")
            if val is not None:
                return (val, "yfinance", datetime.now().strftime("%Y-%m-%d"))
        except Exception as e:
            logger.debug(f"^TNX yfinance: {e}")

        # 2. FRED API（如果配置了 key）
        fred_key = os.getenv("FRED_API_KEY", "")
        if fred_key:
            try:
                val = self._fetch_fred("DGS10", fred_key)
                if val is not None:
                    return (val, "fred", datetime.now().strftime("%Y-%m-%d"))
            except Exception as e:
                logger.debug(f"FRED DGS10: {e}")

        return None

    # ================================================================
    # DXY 多源尝试
    # ================================================================

    async def _try_fetch_dxy(self) -> Optional[tuple]:
        """尝试获取美元指数"""
        # 1. Yahoo Finance DX-Y.NYB
        try:
            val = self._fetch_yf_quote("DX-Y.NYB")
            if val is not None:
                return (val, "yfinance", datetime.now().strftime("%Y-%m-%d"))
        except Exception as e:
            logger.debug(f"DXY yfinance: {e}")

        # 2. FRED
        fred_key = os.getenv("FRED_API_KEY", "")
        if fred_key:
            try:
                val = self._fetch_fred("DTWEXBGS", fred_key)
                if val is not None:
                    return (val, "fred", datetime.now().strftime("%Y-%m-%d"))
            except Exception as e:
                logger.debug(f"FRED DTWEXBGS: {e}")

        return None

    # ================================================================
    # VIX 多源尝试
    # ================================================================

    async def _try_fetch_vix(self) -> Optional[tuple]:
        """尝试获取 VIX（FRED VIXCLS 日频 → Yahoo Finance 实时）"""
        # 1. Yahoo Finance ^VIX（优先，因为 VIX 日内波动有意义）
        try:
            val = self._fetch_yf_quote("^VIX")
            if val is not None:
                return (val, "yfinance", datetime.now().strftime("%Y-%m-%d"))
        except Exception as e:
            logger.debug(f"VIX yfinance: {e}")

        # 2. FRED VIXCLS（CBOE VIX 日收盘价，足以满足宏观分析）
        fred_key = os.getenv("FRED_API_KEY", "")
        if fred_key:
            try:
                val = self._fetch_fred("VIXCLS", fred_key)
                if val is not None:
                    return (val, "fred(daily)", datetime.now().strftime("%Y-%m-%d"))
            except Exception as e:
                logger.debug(f"FRED VIXCLS: {e}")

        return None

    # ================================================================
    # Fed 利率多源尝试
    # ================================================================

    async def _try_fetch_fed_rate(self) -> Optional[tuple]:
        """尝试获取联邦基金利率"""
        fred_key = os.getenv("FRED_API_KEY", "")
        if fred_key:
            try:
                val = self._fetch_fred("FEDFUNDS", fred_key)
                if val is not None:
                    return (val, "fred", datetime.now().strftime("%Y-%m-%d"))
            except Exception as e:
                logger.debug(f"FRED FEDFUNDS: {e}")

        return None

    # ================================================================
    # LPR / M2（akshare）
    # ================================================================

    @staticmethod
    def _fetch_lpr() -> tuple:
        """获取最新 LPR 1年期"""
        import akshare as ak
        df = ak.macro_china_lpr()
        if df is not None and not df.empty:
            latest = df.iloc[-1]
            return (float(latest["LPR1Y"]), str(latest["TRADE_DATE"]))
        return (None, "")

    @staticmethod
    def _fetch_m2() -> tuple:
        """获取最新 M2 同比增速"""
        import akshare as ak
        df = ak.macro_china_money_supply()
        if df is not None and not df.empty:
            # 按日期降序排列，取最新
            if "月份" in df.columns:
                df = df.sort_values("月份", ascending=False)
            latest = df.iloc[0]
            m2_col = "货币和准货币(M2)-同比增长"
            if m2_col in df.columns:
                return (float(latest[m2_col]), str(latest["月份"]))
        return (None, "")

    # ================================================================
    # 通用工具方法
    # ================================================================

    def _fetch_em(self, report_name: str, field: str) -> Optional[float]:
        """东方财富 DataCenter 通用获取"""
        import requests
        try:
            url = (
                f"https://datacenter.eastmoney.com/securities/api/data/v1/get"
                f"?reportName={report_name}&columns=ALL&pageSize=2"
                f"&sortColumns=REPORT_DATE&sortTypes=-1"
            )
            resp = requests.get(url, timeout=10, verify=False,
                                headers={"User-Agent": "Mozilla/5.0"})
            data = resp.json()
            if data.get("success") is not False and data.get("result", {}).get("data"):
                latest = data["result"]["data"][0]
                return self._safe_float(latest.get(field))
        except Exception as e:
            logger.debug(f"东方财富 {report_name}/{field}: {e}")
        return None

    @staticmethod
    def _fetch_yf_quote(symbol: str, max_retries: int = 2) -> Optional[float]:
        """Yahoo Finance 获取最新报价（带重试）"""
        import yfinance as yf
        import time

        for attempt in range(max_retries):
            try:
                ticker = yf.Ticker(symbol)
                hist = ticker.history(period="5d")
                if not hist.empty:
                    return float(hist["Close"].iloc[-1])
            except Exception as e:
                if "Rate limited" in str(e) or "Too Many" in str(e):
                    time.sleep(3 * (attempt + 1))
                else:
                    break
        return None

    @staticmethod
    def _fetch_fred(series_id: str, api_key: str) -> Optional[float]:
        """FRED API 获取最新数据"""
        import requests
        url = (
            f"https://api.stlouisfed.org/fred/series/observations"
            f"?series_id={series_id}&api_key={api_key}"
            f"&sort_order=desc&limit=1&file_type=json"
        )
        resp = requests.get(url, timeout=20)
        data = resp.json()
        observations = data.get("observations", [])
        if observations:
            val = observations[0].get("value", "")
            if val and val != ".":
                return float(val)
        return None

    @staticmethod
    def _safe_float(value) -> Optional[float]:
        if value is None:
            return None
        try:
            v = float(value)
            return v if v == v else None
        except (ValueError, TypeError):
            return None

    # ================================================================
    # 参考值缓存管理
    # ================================================================

    def _load_reference_cache(self) -> dict:
        """加载参考值缓存文件"""
        if _REFERENCE_CACHE_PATH.exists():
            try:
                return json.loads(_REFERENCE_CACHE_PATH.read_text())
            except Exception:
                pass
        # 初始参考值（2026年7月近似值，标注为参考）
        return {
            "us_10y": 4.4,
            "us_10y_date": "2026-07 (参考值)",
            "dxy": 98.0,
            "dxy_date": "2026-07 (参考值)",
            "vix": 16.0,
            "vix_date": "2026-07 (参考值)",
            "fed_funds": 4.33,
            "fed_funds_date": "2026-07 (参考值)",
        }

    def _get_reference(self, key: str) -> Optional[float]:
        """从缓存获取参考值"""
        return self._ref_cache.get(key)

    def _update_reference_cache(self, data: MacroDataV2):
        """如果获取到实时数据，更新参考值缓存"""
        updated = False
        updates = {
            "us_10y": (data.us_10y_yield, data.us_10y_date, "us_10y_source"),
            "dxy": (data.dxy, data.dxy_date, "dxy_source"),
            "vix": (data.vix, data.vix_date, "vix_source"),
            "fed_funds": (data.fed_funds_rate, data.fed_funds_date, "fed_funds_source"),
        }

        for key, (val, date_str, src_field) in updates.items():
            src = getattr(data, src_field, "")
            if val is not None and "reference" not in src:
                self._ref_cache[key] = val
                self._ref_cache[f"{key}_date"] = date_str
                updated = True

        if updated:
            try:
                _REFERENCE_CACHE_PATH.write_text(
                    json.dumps(self._ref_cache, ensure_ascii=False, indent=2)
                )
            except Exception:
                pass
