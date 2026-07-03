"""
基本面数据获取器 v2

获取公司的财务指标、估值数据、机构评级。
支持 A股（akshare）、港股/美股（yfinance）。

v2 改进:
- 集成预处理管线（评分卡、趋势提取、质量评估、价值陷阱检测）
- 历史估值分位计算
- 结构化摘要输出（替代原始数据字典）
"""

from dataclasses import dataclass, field
from typing import Optional
import logging
import json

from src.data.fundamental_preprocessor import (
    extract_financial_trend,
    generate_quality_scorecard,
    assess_data_quality,
    detect_value_trap,
)
from src.data.valuation_history import (
    calculate_valuation_percentile,
    fetch_pe_history_akshare,
)

logger = logging.getLogger(__name__)


@dataclass
class FundamentalData:
    """基本面数据封装"""
    symbol: str
    company_name: str = ""
    market: str = "A"
    industry: str = ""

    # 最新财务数据
    latest_revenue: Optional[float] = None       # 最新季度营收(亿)
    latest_net_profit: Optional[float] = None     # 最新季度净利润(亿)
    revenue_yoy: Optional[float] = None           # 营收同比(%)
    profit_yoy: Optional[float] = None            # 利润同比(%)
    gross_margin: Optional[float] = None          # 毛利率(%)
    net_margin: Optional[float] = None            # 净利率(%)
    roe: Optional[float] = None                   # ROE(%)
    eps: Optional[float] = None                   # 每股收益

    # 估值指标
    pe: Optional[float] = None                    # 市盈率
    pb: Optional[float] = None                    # 市净率
    ps: Optional[float] = None                    # 市销率
    market_cap: Optional[float] = None            # 总市值(亿)
    dividend_yield: Optional[float] = None        # 股息率(%)

    # 行业对比
    industry_pe: Optional[float] = None           # 行业平均PE
    industry_pb: Optional[float] = None           # 行业平均PB

    # 机构评级
    analyst_rating: str = "unknown"               # buy/overweight/hold/underweight/sell
    target_price_high: Optional[float] = None
    target_price_low: Optional[float] = None
    analyst_count: int = 0

    # 数据来源
    data_source: str = "none"                     # akshare / yfinance / partial / none
    missing_fields: list = field(default_factory=list)

    def to_agent_dict(self) -> dict:
        """输出给 Agent 的字典格式（v2：含预处理结果）"""
        def fmt(v):
            if v is None:
                return "N/A"
            if isinstance(v, float):
                return round(v, 2)
            return v

        # 基础数据
        base_result = {
            "symbol": self.symbol,
            "company_name": self.company_name,
            "market": self.market,
            "industry": self.industry,
            "data_source": self.data_source,
            "missing_fields": self.missing_fields,

            "financials": {
                "latest_revenue_100m": fmt(self.latest_revenue),
                "latest_net_profit_100m": fmt(self.latest_net_profit),
                "revenue_yoy_pct": fmt(self.revenue_yoy),
                "profit_yoy_pct": fmt(self.profit_yoy),
                "gross_margin_pct": fmt(self.gross_margin),
                "net_margin_pct": fmt(self.net_margin),
                "roe_pct": fmt(self.roe),
                "eps": fmt(self.eps),
            },

            "valuation": {
                "pe": fmt(self.pe),
                "pb": fmt(self.pb),
                "ps": fmt(self.ps),
                "market_cap_100m": fmt(self.market_cap),
                "dividend_yield_pct": fmt(self.dividend_yield),
                "industry_pe": fmt(self.industry_pe),
                "industry_pb": fmt(self.industry_pb),
            },

            "analyst": {
                "rating": self.analyst_rating,
                "target_price_high": fmt(self.target_price_high),
                "target_price_low": fmt(self.target_price_low),
                "analyst_count": self.analyst_count,
            },
        }

        return base_result

    def to_agent_dict_enhanced(self, pe_history: list[float] = None) -> dict:
        """增强版输出：包含预处理管线结果

        Args:
            pe_history: PE历史序列（可选，若提供则计算分位）

        Returns:
            包含评分卡、趋势、质量评估的完整数据字典
        """
        base = self.to_agent_dict()
        financials = base["financials"]
        valuation = base["valuation"]

        # --- 1. 财务趋势提取 ---
        trend = extract_financial_trend(financials)
        financials["_trend"] = {
            "revenue_trend": trend.revenue_trend,
            "profit_trend": trend.profit_trend,
            "margin_trend": trend.margin_trend,
            "roe_trend": trend.roe_trend,
            "earnings_quality": trend.earnings_quality,
            "quarterly_revenue_yoy": trend.quarterly_revenue_yoy,
            "quarterly_profit_yoy": trend.quarterly_profit_yoy,
            "quarterly_roe": trend.quarterly_roe,
            "summary": trend.summary,
        }

        # --- 2. 估值分位计算 ---
        pe_val = FundamentalFetcher._safe_float(valuation.get("pe"))
        pe_percentile = None
        valuation_analysis = {}

        if pe_val and pe_val > 0:
            p3 = None
            p5 = None
            if pe_history and len(pe_history) >= 30:
                from src.data.valuation_history import percentile_of_score
                p3 = percentile_of_score(pe_history, pe_val)
                p5 = p3  # 如果没有5年数据，用3年代替
                pe_percentile = p3

            # 生成分位解读
            vp = calculate_valuation_percentile(
                current_value=pe_val,
                history_3yr=pe_history or [],
                metric="PE",
            )
            valuation_analysis = {
                "current_pe": pe_val,
                "pe_percentile_3yr": round(p3, 3) if p3 is not None else None,
                "pe_percentile_5yr": round(p5, 3) if p5 is not None else None,
                "interpretation": vp.interpretation,
                "historical_low": vp.historical_low,
                "historical_high": vp.historical_high,
                "historical_median": vp.historical_median,
                "data_points": vp.data_points,
            }

        # --- 3. 质量评分卡 ---
        scorecard = generate_quality_scorecard(
            financials=financials,
            valuation=valuation,
            pe_percentile=pe_percentile,
        )
        base["quality_scorecard"] = {
            "total": scorecard.total,
            "rating": scorecard.rating,
            "breakdown": {
                "profitability": scorecard.profitability,
                "growth": scorecard.growth,
                "valuation": scorecard.valuation,
                "health": scorecard.health,
            },
        }

        # --- 4. 数据质量评估 ---
        quality_report = assess_data_quality(financials, valuation)
        base["data_quality"] = {
            "completeness": quality_report.completeness,
            "freshness": quality_report.freshness,
            "overall_quality": quality_report.overall_quality,
            "financial_fields_filled": quality_report.financial_fields_filled,
            "valuation_fields_filled": quality_report.valuation_fields_filled,
            "data_gaps": quality_report.data_gaps,
            "confidence_ceiling": quality_report.confidence_ceiling,
        }

        # --- 5. 价值陷阱检测 ---
        base["value_trap_analysis"] = detect_value_trap(financials, pe_percentile)

        # --- 6. 估值分析汇总 ---
        if valuation_analysis:
            base["valuation_analysis"] = valuation_analysis

        # --- 7. 异常标志 ---
        base["anomaly_flags"] = {
            "value_trap_warning": base["value_trap_analysis"]["is_trap"],
            "earnings_momentum_positive": (
                trend.profit_trend in ("accelerating", "growing") and
                trend.earnings_quality != "deteriorating"
            ),
            "data_heavily_missing": quality_report.completeness < 0.4,
        }

        return base


class FundamentalFetcher:
    """基本面数据获取器"""

    async def fetch(self, symbol: str, market: str) -> FundamentalData:
        """获取基本面数据

        策略：
        1. A股 → akshare 财务 + 腾讯实时 PE
        2. 港股 → 腾讯实时行情（PE/市值/名称）
        3. 其他 → yfinance（带重试）
        """
        symbol = symbol.strip().upper()
        result = FundamentalData(symbol=symbol, market=market)

        try:
            if market == "A":
                await self._fetch_a_share(result, symbol)
            elif market == "HK":
                await self._fetch_hk_tencent(result, symbol)
            else:
                await self._fetch_yfinance(result, symbol, market)

        except Exception as e:
            logger.warning(f"基本面数据获取异常: {e}")
            result.data_source = "partial" if result.data_source != "none" else "none"
            result.missing_fields.append(str(e))

        return result

    async def fetch_enhanced(self, symbol: str, market: str) -> dict:
        """获取增强版基本面数据（含预处理管线）

        返回结构化字典，包含评分卡、趋势、质量评估、分位等信息。
        供 FundamentalAnalyst v2 使用。

        Args:
            symbol: 股票代码
            market: "A" / "HK" / "US"

        Returns:
            增强版数据字典
        """
        # 获取基础数据
        fundamental_data = await self.fetch(symbol, market)

        # 港股：补充财务数据
        if market == "HK":
            await self._fetch_hk_financials_supplement(fundamental_data, symbol)

        # 获取PE历史序列（仅A股）
        pe_history = None
        if market == "A" and fundamental_data.pe:
            pe_history = await fetch_pe_history_akshare(symbol, market)
            if not pe_history:
                logger.debug(f"PE历史数据不足，跳过分位计算")

        # 生成增强输出
        return fundamental_data.to_agent_dict_enhanced(pe_history=pe_history)

    async def _fetch_hk_financials_supplement(self, result: FundamentalData, symbol: str):
        """补充港股财务数据（东方财富数据源）"""
        try:
            from src.data.hk_financial_fetcher import fetch_hk_financials_em

            hk_fin = await fetch_hk_financials_em(symbol)

            if hk_fin.get("data_source") != "none":
                # 仅填充缺失的字段
                if result.latest_revenue is None:
                    result.latest_revenue = hk_fin.get("revenue")
                if result.latest_net_profit is None:
                    result.latest_net_profit = hk_fin.get("net_profit")
                if result.revenue_yoy is None:
                    result.revenue_yoy = hk_fin.get("revenue_yoy")
                if result.profit_yoy is None:
                    result.profit_yoy = hk_fin.get("profit_yoy")
                if result.roe is None:
                    result.roe = hk_fin.get("roe")
                if result.gross_margin is None:
                    result.gross_margin = hk_fin.get("gross_margin")
                if result.net_margin is None:
                    result.net_margin = hk_fin.get("net_margin")
                if result.eps is None:
                    result.eps = hk_fin.get("eps")
                if result.pe is None:
                    result.pe = hk_fin.get("pe")
                if result.pb is None:
                    result.pb = hk_fin.get("pb")
                if result.dividend_yield is None:
                    result.dividend_yield = hk_fin.get("dividend_yield")
                if result.company_name is None:
                    result.company_name = hk_fin.get("company_name") or result.company_name
                if result.industry is None:
                    result.industry = hk_fin.get("industry") or result.industry

                logger.info(
                    f"港股财务补充(东方财富): "
                    f"{result.company_name}, ROE={result.roe}%, 营收={result.latest_revenue}亿, "
                    f"毛利率={result.gross_margin}%"
                )
            else:
                logger.warning(f"港股财务数据获取失败: {symbol}")

        except Exception as e:
            logger.debug(f"港股财务补充失败: {e}")

    # ================================================================
    # A股数据
    # ================================================================

    async def _fetch_a_share(self, result: FundamentalData, symbol: str):
        """从 akshare + 新浪 获取 A 股基本面数据"""
        import akshare as ak

        fetched_any = False

        # --- 公司名称 + 实时估值（新浪/腾讯 API，亲测可用） ---
        try:
            import requests
            code = symbol.zfill(6)
            market_prefix = "sz" if code.startswith(("000", "001", "002", "003", "300", "301")) else "sh"
            
            # 新浪实时行情
            resp = requests.get(
                f"https://hq.sinajs.cn/list={market_prefix}{code}",
                headers={"Referer": "https://finance.sina.com.cn"},
                timeout=10,
                verify=False,
            )
            resp.encoding = "gbk"
            text = resp.text
            if "var" in text and "=" in text:
                fields = text.split("=")[1].strip('";\n ').split(",")
                if len(fields) >= 2:
                    result.company_name = fields[0]
                    logger.info(f"公司名称: {result.company_name}")

            # 腾讯实时行情（含 PE）
            resp2 = requests.get(
                f"https://qt.gtimg.cn/q={market_prefix}{code}",
                timeout=10,
                verify=False,
            )
            text2 = resp2.text
            if "~" in text2:
                fields2 = text2.split("~")
                if len(fields2) >= 40:
                    if result.pe is None:
                        result.pe = self._safe_float(fields2[39])  # PE
                    if not result.company_name and len(fields2) >= 2:
                        result.company_name = fields2[1]
                    logger.info(f"腾讯行情: PE={result.pe}")
        except Exception as e:
            logger.debug(f"实时行情获取失败: {e}")

        # --- 财务指标 ---
        try:
            df = ak.stock_financial_abstract_ths(symbol=symbol, indicator="按报告期")
            if df is not None and not df.empty:
                latest = df.iloc[-1]

                result.latest_revenue = self._parse_financial_value(latest.get("营业总收入"))
                result.latest_net_profit = self._parse_financial_value(latest.get("净利润"))
                result.revenue_yoy = self._parse_pct_value(latest.get("营业总收入同比增长率"))
                result.profit_yoy = self._parse_pct_value(latest.get("净利润同比增长率"))
                result.eps = self._parse_financial_value(latest.get("基本每股收益"))
                result.net_margin = self._parse_pct_value(latest.get("销售净利率"))
                result.roe = self._parse_pct_value(latest.get("净资产收益率"))

                # PB = 股价 / 每股净资产
                bvps = self._parse_financial_value(latest.get("每股净资产"))
                if bvps and bvps > 0 and result.pe is not None and result.pe > 0:
                    # 从 PE 反推股价: price = PE * EPS (需要 TTM EPS)
                    # 这里用每股净资产直接算 PB 更准确
                    pass

                fetched_any = True
                logger.info(f"财务数据: 报告期={latest.get('报告期')}, "
                           f"营收={result.latest_revenue}亿, 利润={result.latest_net_profit}亿, "
                           f"ROE={result.roe}%, EPS={result.eps}")
        except Exception as e:
            logger.debug(f"财务指标获取失败: {e}")
            result.missing_fields.append("财务指标")

        if fetched_any:
            result.data_source = "akshare"
        else:
            result.data_source = "none"
            result.missing_fields.append("akshare 所有接口均失败")

        if fetched_any:
            result.data_source = "akshare"
        else:
            result.data_source = "none"
            result.missing_fields.append("akshare 所有接口均失败")

    # ================================================================
    # yfinance 数据（港股/美股）
    # ================================================================

    async def _fetch_hk_tencent(self, result: FundamentalData, symbol: str):
        """从腾讯实时行情 + Sina 获取港股基本面数据"""
        import requests

        code = symbol.zfill(5)

        # === 腾讯实时行情（PE、市值） ===
        try:
            resp = requests.get(f"https://qt.gtimg.cn/q=hk{code}", timeout=10, verify=False)
            if "~" in resp.text:
                fields = resp.text.split("~")
                if len(fields) >= 50:
                    result.company_name = fields[1]
                    result.pe = self._safe_float(fields[39])
                    result.market_cap = self._safe_float(fields[37])
                    if result.market_cap:
                        result.market_cap = result.market_cap / 1e8
                    result.pb = self._safe_float(fields[43]) if fields[43] else None
                    result.dividend_yield = self._safe_float(fields[56]) if len(fields) > 56 else None
                    result.data_source = "tencent"
        except Exception as e:
            logger.debug(f"腾讯港股失败: {e}")

        # === Sina 行情（52周高低） ===
        try:
            resp = requests.get(
                f"https://hq.sinajs.cn/list=hk{code}",
                headers={"Referer": "https://finance.sina.com.cn"},
                timeout=10, verify=False,
            )
            resp.encoding = "gbk"
            if "var" in resp.text:
                fields = resp.text.split("=")[1].strip('";\n ').split(",")
                if len(fields) >= 17:
                    if not result.company_name:
                        result.company_name = fields[1]
                    # 52周高/低
                    result.target_price_high = self._safe_float(fields[15])  # 52周最高
                    result.target_price_low = self._safe_float(fields[16])   # 52周最低
        except Exception as e:
            logger.debug(f"Sina港股失败: {e}")

        if result.pe is not None:
            logger.info(f"港股基本面: {result.company_name}, PE={result.pe}, "
                       f"52周={result.target_price_low}~{result.target_price_high}")
        else:
            # 降级 yfinance
            await self._fetch_yfinance(result, symbol, "HK")

    async def _fetch_yfinance(self, result: FundamentalData, symbol: str, market: str = "US"):
        """从 yfinance 获取基本面数据（带重试）"""
        import time
        import yfinance as yf

        # 港股需要加 .HK 后缀
        if result.market == "HK" and not ".HK" in symbol.upper():
            symbol = symbol + ".HK"

        for attempt in range(3):
            try:
                ticker = yf.Ticker(symbol)
                info = ticker.info

                if not info or info.get("regularMarketPrice") is None:
                    result.data_source = "none"
                    result.missing_fields.append("yfinance 返回空数据")
                    return

                result.company_name = info.get("longName", info.get("shortName", ""))
                result.industry = info.get("industry", "")
                result.pe = self._safe_float(info.get("trailingPE") or info.get("forwardPE"))
                result.pb = self._safe_float(info.get("priceToBook"))
                result.ps = self._safe_float(info.get("priceToSalesTrailing12Months"))
                result.market_cap = self._safe_float(info.get("marketCap"))
                if result.market_cap: result.market_cap = result.market_cap / 1e8
                result.dividend_yield = self._safe_float(info.get("dividendYield"))
                if result.dividend_yield: result.dividend_yield *= 100
                result.latest_revenue = self._safe_float(info.get("totalRevenue"))
                if result.latest_revenue: result.latest_revenue = result.latest_revenue / 1e8
                result.latest_net_profit = self._safe_float(info.get("netIncomeToCommon"))
                if result.latest_net_profit: result.latest_net_profit = result.latest_net_profit / 1e8
                result.revenue_yoy = self._safe_float(info.get("revenueGrowth"))
                if result.revenue_yoy: result.revenue_yoy *= 100
                result.gross_margin = self._safe_float(info.get("grossMargins"))
                if result.gross_margin: result.gross_margin *= 100
                result.net_margin = self._safe_float(info.get("profitMargins"))
                if result.net_margin: result.net_margin *= 100
                result.roe = self._safe_float(info.get("returnOnEquity"))
                if result.roe: result.roe *= 100
                result.eps = self._safe_float(info.get("trailingEps"))
                result.analyst_rating = info.get("recommendationKey", "unknown")
                result.target_price_high = self._safe_float(info.get("targetHighPrice"))
                result.target_price_low = self._safe_float(info.get("targetLowPrice"))
                result.analyst_count = info.get("numberOfAnalystOpinions", 0) or 0
                result.industry_pe = self._safe_float(info.get("industryPE"))
                result.industry_pb = self._safe_float(info.get("industryPB"))
                result.data_source = "yfinance"
                return

            except Exception as e:
                err = str(e)
                if "Rate limited" in err or "Too Many Requests" in err:
                    wait = 5 * (2 ** attempt)
                    logger.debug(f"yfinance 限流，{wait}s 后重试 ({attempt+1}/3)...")
                    time.sleep(wait)
                elif attempt < 2:
                    time.sleep(2)
                else:
                    raise

        logger.warning(f"yfinance 基本面获取失败（重试3次后）")
        result.data_source = "none"
        result.missing_fields.append("yfinance: rate limited")

    # ================================================================
    # 工具方法
    # ================================================================

    @staticmethod
    def _safe_float(value) -> Optional[float]:
        """安全转换为 float"""
        if value is None:
            return None
        try:
            v = float(value)
            if v != v:
                return None
            return v
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _to_yfinance_symbol(symbol: str) -> str:
        """A股代码转 yfinance 格式"""
        code = symbol.zfill(6)
        if code.startswith(("000", "001", "002", "003", "300", "301")):
            return f"{code}.SZ"
        elif code.startswith(("600", "601", "603", "605", "688")):
            return f"{code}.SS"
        return f"{code}.SZ"

    @staticmethod
    def _parse_financial_value(value) -> Optional[float]:
        """解析 akshare 财务值，如 '352.77亿', '145.23亿', '0.6700', '4302.00万'"""
        if value is None:
            return None
        s = str(value).strip()
        if s in ("", "False", "None", "N/A"):
            return None
        try:
            # 去掉单位和百分号
            multiplier = 1.0
            if "亿" in s:
                multiplier = 1.0  # 保持亿单位
                s = s.replace("亿", "")
            elif "万" in s:
                multiplier = 0.0001  # 万→亿
                s = s.replace("万", "")
            elif "%" in s:
                s = s.replace("%", "")

            v = float(s) * multiplier
            return v if v == v else None
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _parse_pct_value(value) -> Optional[float]:
        """解析百分比值，如 '3.03%', '41.17%'"""
        if value is None:
            return None
        s = str(value).strip()
        if s in ("", "False", "None", "N/A"):
            return None
        try:
            s = s.replace("%", "")
            v = float(s)
            return v if v == v else None
        except (ValueError, TypeError):
            return None
