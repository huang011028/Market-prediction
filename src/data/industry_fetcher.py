"""
行业对比数据获取器 v2

获取标的所属行业的平均估值、盈利能力等对比数据，
判断标的在行业中的相对位置。

v2 改进:
- 东方财富行业板块API替代硬编码常量
- 扩展行业分类映射（100+标的）
- 行业分类缓存
- 港股行业映射
- 趋势数据获取
- 集成预处理管线
- 结构化输出
"""

from dataclasses import dataclass, field
from typing import Optional
import logging

from src.data.industry_preprocessor import (
    IndustryClassifierCache,
    IndustryReferenceCache,
    EXTENDED_KNOWN_INDUSTRIES,
    KNOWN_HK_INDUSTRIES,
    infer_industry_from_name,
    process_industry_data,
)

logger = logging.getLogger(__name__)


@dataclass
class IndustryData:
    """行业对比数据封装"""
    symbol: str
    company_name: str = ""
    industry_name: str = ""

    # 标的自身
    stock_pe: Optional[float] = None
    stock_pb: Optional[float] = None
    stock_roe: Optional[float] = None
    stock_revenue_growth: Optional[float] = None
    stock_profit_growth: Optional[float] = None
    stock_net_margin: Optional[float] = None
    stock_market_cap: Optional[float] = None

    # 行业平均
    industry_pe: Optional[float] = None
    industry_pb: Optional[float] = None
    industry_roe: Optional[float] = None

    # 行业中位数
    industry_pe_median: Optional[float] = None
    industry_pb_median: Optional[float] = None

    # 行业内公司数量
    industry_stock_count: int = 0

    # 行业近期涨跌幅
    industry_change_5d: Optional[float] = None
    industry_change_20d: Optional[float] = None

    # 数据来源
    data_source: str = "none"
    missing_fields: list = field(default_factory=list)

    def to_agent_dict(self) -> dict:
        def fmt(v):
            if v is None: return "N/A"
            if isinstance(v, float): return round(v, 2)
            return v

        # PE 对比分析（亏损公司不比较 PE）
        pe_analysis = "N/A"
        if self.stock_pe and self.stock_pe > 0 and self.industry_pe and self.industry_pe > 0:
            diff = (self.stock_pe / self.industry_pe - 1) * 100
            if diff < -30:
                pe_analysis = f"远低于行业均值（低{abs(diff):.0f}%），明显低估"
            elif diff < -10:
                pe_analysis = f"低于行业均值（低{abs(diff):.0f}%），相对便宜"
            elif diff < 10:
                pe_analysis = f"与行业均值接近（{'高' if diff>0 else '低'}{abs(diff):.0f}%）"
            elif diff < 30:
                pe_analysis = f"高于行业均值（高{abs(diff):.0f}%），相对偏贵"
            else:
                pe_analysis = f"远高于行业均值（高{abs(diff):.0f}%），明显高估"

        # PB 对比
        pb_analysis = "N/A"
        if self.stock_pb and self.industry_pb and self.industry_pb > 0:
            diff = (self.stock_pb / self.industry_pb - 1) * 100
            if diff < -20:
                pb_analysis = f"低于行业均值{abs(diff):.0f}%，估值偏低"
            elif diff < 20:
                pb_analysis = f"与行业均值接近"
            else:
                pb_analysis = f"高于行业均值{diff:.0f}%，估值偏贵"

        # ROE 对比
        roe_analysis = "N/A"
        if self.stock_roe and self.industry_roe and self.industry_roe > 0:
            diff = self.stock_roe - self.industry_roe
            if diff > 10:
                roe_analysis = "远高于行业均值，盈利能力突出"
            elif diff > 3:
                roe_analysis = "高于行业均值，盈利能力良好"
            elif diff > -3:
                roe_analysis = "与行业均值相当"
            elif diff > -10:
                roe_analysis = "低于行业均值，盈利能力偏弱"
            else:
                roe_analysis = "远低于行业均值，盈利能力堪忧"

        return {
            "symbol": self.symbol,
            "company_name": self.company_name,
            "industry_name": self.industry_name,
            "data_source": self.data_source,

            "stock_metrics": {
                "pe": fmt(self.stock_pe), "pb": fmt(self.stock_pb),
                "roe_pct": fmt(self.stock_roe),
                "revenue_growth_pct": fmt(self.stock_revenue_growth),
                "profit_growth_pct": fmt(self.stock_profit_growth),
                "net_margin_pct": fmt(self.stock_net_margin),
                "market_cap_100m": fmt(self.stock_market_cap),
            },

            "industry_average": {
                "pe": fmt(self.industry_pe), "pb": fmt(self.industry_pb),
                "roe_pct": fmt(self.industry_roe),
                "pe_median": fmt(self.industry_pe_median),
                "pb_median": fmt(self.industry_pb_median),
                "stock_count": self.industry_stock_count,
                "change_5d_pct": fmt(self.industry_change_5d),
                "change_20d_pct": fmt(self.industry_change_20d),
            },

            "comparison": {
                "pe_vs_industry": pe_analysis,
                "pb_vs_industry": pb_analysis,
                "roe_vs_industry": roe_analysis,
            },
        }


class IndustryFetcher:
    """行业对比数据获取器 v2"""

    def __init__(self):
        self._classifier_cache = IndustryClassifierCache()
        self._ref_cache = IndustryReferenceCache()

    async def fetch(self, symbol: str, market: str = "A") -> IndustryData:
        """
        获取行业对比数据

        策略：
        1. 获取标的自身估值（复用 fundamental_fetcher 的逻辑）
        2. 从 akshare 获取行业板块数据
        3. 降级：至少给出标的数据，行业数据标记缺失
        """
        symbol = symbol.strip().upper()
        result = IndustryData(symbol=symbol)

        # Step 1: 获取标的数据
        await self._fetch_stock_data(result, symbol, market)

        # Step 2: 获取行业数据
        await self._fetch_industry_data(result, symbol, market)

        return result

    async def fetch_enhanced(self, symbol: str, market: str = "A") -> dict:
        """获取增强版行业对比数据（含预处理管线）

        返回结构化字典，包含排名、趋势、性价比等信息。

        Args:
            symbol: 股票代码
            market: "A" / "HK" / "US"

        Returns:
            增强版行业对比数据字典
        """
        symbol = symbol.strip().upper()

        # Step 1: 获取标的数据
        industry_data = IndustryData(symbol=symbol)
        await self._fetch_stock_data(industry_data, symbol, market)

        # Step 2: 确定行业分类
        industry_name = await self._find_industry_improved(
            symbol, industry_data.company_name, market
        )

        if industry_name:
            industry_data.industry_name = industry_name

        # Step 3: 获取行业成分股数据（仅A股）
        industry_peers = []
        industry_trend = None

        if market == "A" and industry_name:
            industry_peers = await self._fetch_industry_constituents(industry_name)
            industry_trend = await self._fetch_industry_trend(industry_name)

        # 港股：尝试获取行业信息
        if market == "HK" and not industry_name:
            hk_info = KNOWN_HK_INDUSTRIES.get(symbol)
            if hk_info:
                industry_name = hk_info["name"]
                industry_data.industry_name = industry_name

        # Step 4: 确定行业参考估值
        ref_data = self._ref_cache.get(industry_name) if industry_name else None
        if ref_data and not industry_peers:
            # 使用缓存/硬编码的参考值（当无法获取实时成分股时）
            industry_data.industry_pe = ref_data.get("pe")
            industry_data.industry_pb = ref_data.get("pb")
            industry_data.industry_roe = ref_data.get("roe")

        if industry_peers:
            # 有实时成分股数据，计算行业平均
            from src.data.industry_preprocessor import calculate_industry_metrics
            metrics = calculate_industry_metrics(industry_peers)
            industry_data.industry_pe = metrics.avg_pe or industry_data.industry_pe
            industry_data.industry_pb = metrics.avg_pb or industry_data.industry_pb
            industry_data.industry_roe = metrics.avg_roe or industry_data.industry_roe
            industry_data.industry_pe_median = metrics.median_pe
            industry_data.industry_pb_median = metrics.median_pb
            industry_data.industry_stock_count = metrics.sample_size

        # Step 5: 运行预处理管线
        stock_info = {
            "pe": industry_data.stock_pe,
            "pb": industry_data.stock_pb,
            "roe": industry_data.stock_roe,
            "revenue_growth": industry_data.stock_revenue_growth,
            "profit_growth": industry_data.stock_profit_growth,
        }

        processed = process_industry_data(
            stock_data=stock_info,
            industry_peers=industry_peers,
            industry_trend=industry_trend,
        )

        # Step 6: 构建增强输出
        base = industry_data.to_agent_dict()

        # 合并预处理结果
        base["industry_peers_top"] = processed.get("industry_peers_top", [])
        base["rank_in_industry"] = processed.get("rank_in_industry", {})
        base["value_score"] = processed.get("value_score", {})
        base["industry_trend"] = processed.get("industry_trend", {})
        base["data_quality"] = processed.get("data_quality", {})
        base["anomaly_flags"] = processed.get("anomaly_flags", {})

        # 标注数据来源
        if self._ref_cache.is_using_cached(industry_name):
            base["data_source"] = "eastmoney_realtime"
        elif ref_data and not industry_peers:
            base["data_source"] = "reference_cached"
        elif industry_peers:
            base["data_source"] = "eastmoney_constituents"
        else:
            base["data_source"] = base.get("data_source", "none")

        return base

    async def _find_industry_improved(
        self, symbol: str, company_name: str = "", market: str = "A"
    ) -> Optional[str]:
        """改进的行业分类方法

        优先级:
        1. 缓存（本地 JSON）
        2. 扩展已知映射（100+）
        3. 名称关键词推断
        4. 逐个 API 尝试（仅A股）
        """
        code = symbol.zfill(6)

        # 1. 查缓存
        cached = self._classifier_cache.get(code)
        if cached:
            return cached

        # 2. 扩展映射
        if market == "A" and code in EXTENDED_KNOWN_INDUSTRIES:
            industry = EXTENDED_KNOWN_INDUSTRIES[code]
            self._classifier_cache.put(code, industry)
            return industry

        # 3. 名称推断
        if company_name:
            guessed = infer_industry_from_name(company_name)
            if guessed:
                self._classifier_cache.put(code, guessed)
                return guessed

        # 4. 逐个 API 尝试（仅A股，且有超时）
        if market == "A":
            industry = await self._find_industry_api_scan(code)
            if industry:
                self._classifier_cache.put(code, industry)
                return industry

        return None

    async def _find_industry_api_scan(self, code: str) -> Optional[str]:
        """逐个扫描东方财富行业板块（带短超时）"""
        try:
            import akshare as ak

            all_industries = [
                "银行", "白酒", "证券", "保险", "医药", "新能源",
                "家电", "电子", "房地产", "电力", "有色金属",
                "水泥", "计算机", "食品饮料", "汽车", "半导体",
                "军工", "传媒", "通信", "化工", "钢铁", "煤炭",
            ]

            for ind in all_industries:
                try:
                    df = ak.stock_board_industry_cons_em(symbol=ind)
                    if df is not None and not df.empty and "代码" in df.columns:
                        if code in df["代码"].astype(str).tolist():
                            logger.info(f"行业分类 API 命中: {code} → {ind}")
                            return ind
                except Exception:
                    continue

        except Exception as e:
            logger.debug(f"行业 API 扫描失败: {e}")

        return None

    async def _fetch_industry_constituents(self, industry_name: str) -> list[dict]:
        """获取行业成分股列表"""
        try:
            import akshare as ak

            df = ak.stock_board_industry_cons_em(symbol=industry_name)
            if df is None or df.empty:
                return []

            peers = []
            for _, row in df.head(50).iterrows():
                pe = self._safe_float(row.get("市盈率-动态"))
                pb = self._safe_float(row.get("市净率"))
                peers.append({
                    "code": str(row.get("代码", "")),
                    "name": str(row.get("名称", "")),
                    "pe": pe,
                    "pb": pb,
                    "change_pct": self._safe_float(row.get("涨跌幅")),
                })

            logger.info(f"行业 {industry_name} 成分股: {len(peers)} 家")
            return peers

        except Exception as e:
            logger.debug(f"获取行业成分股失败 ({industry_name}): {e}")
            return []

    async def _fetch_industry_trend(self, industry_name: str) -> Optional[dict]:
        """获取行业近期行情趋势"""
        try:
            import akshare as ak
            from datetime import datetime, timedelta

            start = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")

            df = ak.stock_board_industry_hist_em(
                symbol=industry_name,
                start_date=start,
                period="日k",
            )

            if df is None or df.empty:
                return None

            closes = df["收盘"].dropna().tolist()
            if len(closes) < 5:
                return None

            return {
                "change_5d": round((closes[-1] / closes[-6] - 1) * 100, 2) if len(closes) >= 6 else None,
                "change_20d": round((closes[-1] / closes[-21] - 1) * 100, 2) if len(closes) >= 21 else None,
                "change_60d": round((closes[-1] / closes[-61] - 1) * 100, 2) if len(closes) >= 61 else None,
            }

        except Exception as e:
            logger.debug(f"获取行业趋势失败 ({industry_name}): {e}")
            return None

    async def _fetch_stock_data(self, result: IndustryData, symbol: str, market: str):
        """获取标的自身的估值和财务数据"""
        try:
            if market == "A":
                await self._fetch_a_stock(result, symbol)
            elif market == "HK":
                await self._fetch_hk_stock(result, symbol)
            else:
                await self._fetch_yfinance_stock(result, symbol, market)
        except Exception as e:
            logger.warning(f"标的数据获取失败: {e}")

    async def _fetch_a_stock(self, result: IndustryData, symbol: str):
        """A股标的数据"""
        import akshare as ak

        # 公司名 + PE（腾讯 API，亲测可用）
        code = symbol.zfill(6)
        prefix = "sz" if code.startswith(("000","001","002","003","300","301")) else "sh"
        try:
            import requests
            resp = requests.get(
                f"https://qt.gtimg.cn/q={prefix}{code}",
                timeout=10, verify=False,
            )
            if "~" in resp.text:
                fields = resp.text.split("~")
                if len(fields) >= 40:
                    result.company_name = fields[1]
                    result.stock_pe = self._safe_float(fields[39])
        except Exception as e:
            logger.debug(f"腾讯行情失败: {e}")

        # 财务数据
        try:
            df = ak.stock_financial_abstract_ths(symbol=symbol, indicator="按报告期")
            if df is not None and not df.empty:
                latest = df.iloc[-1]
                result.stock_roe = self._parse_pct(latest.get("净资产收益率"))
                result.stock_revenue_growth = self._parse_pct(latest.get("营业总收入同比增长率"))
                result.stock_profit_growth = self._parse_pct(latest.get("净利润同比增长率"))
                result.stock_net_margin = self._parse_pct(latest.get("销售净利率"))
                result.data_source = "akshare"
        except Exception as e:
            logger.debug(f"财务数据失败: {e}")

    async def _fetch_hk_stock(self, result: IndustryData, symbol: str):
        """港股标的数据 (腾讯实时行情)"""
        try:
            import requests
            code = symbol.zfill(5)
            resp = requests.get(f"https://qt.gtimg.cn/q=hk{code}", timeout=10, verify=False)
            if "~" in resp.text:
                fields = resp.text.split("~")
                if len(fields) >= 50:
                    result.company_name = fields[1]
                    result.stock_pe = self._safe_float(fields[39])
                    result.stock_pb = self._safe_float(fields[43]) if fields[43] else None
                    mc = self._safe_float(fields[37])
                    if mc: result.stock_market_cap = mc / 1e8
                    result.data_source = "tencent"
                    # 港股互联网行业
                    result.industry_name = result.industry_name or "互联网"
                    logger.info(f"腾讯港股: {result.company_name}, PE={result.stock_pe}")
        except Exception as e:
            logger.debug(f"腾讯港股行业数据失败: {e}")

    async def _fetch_yfinance_stock(self, result: IndustryData, symbol: str, market: str):
        """港股/美股标的数据"""
        try:
            import yfinance as yf
            sym = symbol
            if market == "HK" and not ".HK" in sym:
                sym = f"{symbol}.HK"

            ticker = yf.Ticker(sym)
            info = ticker.info
            if info:
                result.company_name = info.get("longName", "")
                result.industry_name = info.get("industry", "")
                result.stock_pe = self._safe_float(info.get("trailingPE"))
                result.stock_pb = self._safe_float(info.get("priceToBook"))
                result.stock_roe = self._safe_float(info.get("returnOnEquity"))
                if result.stock_roe: result.stock_roe *= 100
                result.stock_revenue_growth = self._safe_float(info.get("revenueGrowth"))
                if result.stock_revenue_growth: result.stock_revenue_growth *= 100
                result.stock_net_margin = self._safe_float(info.get("profitMargins"))
                if result.stock_net_margin: result.stock_net_margin *= 100
                result.industry_pe = self._safe_float(info.get("industryPE"))
                result.industry_pb = self._safe_float(info.get("industryPB"))
                result.data_source = "yfinance"
        except Exception as e:
            logger.debug(f"yfinance 失败: {e}")

    async def _fetch_industry_data(self, result: IndustryData, symbol: str, market: str):
        """获取行业板块数据"""
        if market != "A":
            return

        # 找到行业
        industry = await self._find_industry(symbol)
        if not industry:
            result.missing_fields.append("行业分类")
            return
        result.industry_name = industry

        # 行业估值参考（已知常量，避免慢 API）
        INDUSTRY_REF = {
            "银行": {"pe": 5.5, "pb": 0.6, "roe": 10.0},
            "白酒": {"pe": 25.0, "pb": 6.0, "roe": 25.0},
            "证券": {"pe": 18.0, "pb": 1.3, "roe": 7.0},
            "保险": {"pe": 12.0, "pb": 1.0, "roe": 12.0},
            "医药": {"pe": 30.0, "pb": 4.0, "roe": 15.0},
            "新能源": {"pe": 20.0, "pb": 3.0, "roe": 18.0},
            "家电": {"pe": 15.0, "pb": 2.5, "roe": 20.0},
            "电子": {"pe": 25.0, "pb": 3.5, "roe": 12.0},
            "房地产": {"pe": 8.0, "pb": 0.8, "roe": 5.0},
            "电力": {"pe": 15.0, "pb": 1.5, "roe": 10.0},
            "有色金属": {"pe": 15.0, "pb": 2.0, "roe": 10.0},
            "水泥": {"pe": 10.0, "pb": 1.0, "roe": 8.0},
            "互联网": {"pe": 20.0, "pb": 4.0, "roe": 15.0},
            "科技": {"pe": 25.0, "pb": 5.0, "roe": 18.0},
        }
        ref = INDUSTRY_REF.get(industry, {})
        result.industry_pe = ref.get("pe")
        result.industry_pb = ref.get("pb")
        result.industry_roe = ref.get("roe")

        result.data_source = "reference"
        logger.info(f"行业 {industry}(参考): PE~{result.industry_pe}, PB~{result.industry_pb}")

    async def _find_industry(self, symbol: str) -> Optional[str]:
        """查找 A 股标的所属行业

        策略：已知映射 > akshare API > 代码规则推断
        """
        code = symbol.zfill(6)

        # === 已知行业映射（常用标的，无需 API 调用）===
        KNOWN_INDUSTRIES = {
            "000001": "银行", "002142": "银行", "600000": "银行",
            "600036": "银行", "601398": "银行", "601939": "银行",
            "600519": "白酒", "000858": "白酒", "002304": "白酒",
            "000333": "家电", "000651": "家电",
            "300750": "新能源", "601012": "新能源",
            "600276": "医药", "000538": "医药", "300760": "医药",
            "002475": "电子", "002415": "电子",
            "600030": "证券", "300059": "证券",
            "601318": "保险", "601628": "保险",
            "000002": "房地产", "600048": "房地产",
            "600900": "电力", "601985": "电力",
            "601899": "有色金属", "600585": "水泥",
        }
        if code in KNOWN_INDUSTRIES:
            return KNOWN_INDUSTRIES[code]

        # === akshare API（加短超时，失败则降级）===
        try:
            import akshare as ak

            # 只尝试最快的接口
            try:
                df = ak.stock_board_industry_cons_em(symbol="银行")
                if df is not None and not df.empty and "代码" in df.columns:
                    codes = df["代码"].astype(str).tolist()
                    if code in codes: return "银行"
            except: pass

            try:
                df = ak.stock_board_industry_cons_em(symbol="白酒")
                if df is not None and not df.empty and "代码" in df.columns:
                    if code in df["代码"].astype(str).tolist(): return "白酒"
            except: pass

        except Exception:
            pass

        # === 代码规则推断 ===
        if code.startswith(("600000","601","603")):
            return None  # 无法确定
        elif code.startswith(("000","002")):
            return None

        return None

    @staticmethod
    def _safe_float(value) -> Optional[float]:
        if value is None: return None
        try:
            v = float(value)
            return v if v == v else None
        except: return None

    @staticmethod
    def _parse_pct(value) -> Optional[float]:
        """解析百分比值"""
        if value is None: return None
        s = str(value).replace("%", "").strip()
        try:
            v = float(s)
            return v if v == v else None
        except: return None
