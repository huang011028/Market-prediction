"""
港股财务数据获取器 — 东方财富数据源

从东方财富获取港股财务指标，替代 yfinance 和不稳定的 AASTOCKS 爬取。
数据源:
- stock_hk_financial_indicator_em: 估值/盈利/ROE/股息等
- stock_financial_hk_analysis_indicator_em: 营收/利润/毛利率/ROA 等
- stock_hk_company_profile_em: 公司概况

使用 akshare 封装的东方财富 API，免费、稳定、数据完整。
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def fetch_hk_financials_em(symbol: str) -> dict:
    """
    从东方财富获取港股财务数据。

    Args:
        symbol: 港股代码（如 "03690" 或 "3690"）

    Returns:
        {
            "revenue": 营收(亿),
            "revenue_yoy": 营收同比(%),
            "net_profit": 净利润(亿),
            "profit_yoy": 利润同比(%),
            "roe": ROE(%),
            "roa": ROA(%),
            "gross_margin": 毛利率(%),
            "net_margin": 净利率(%),
            "eps": 每股收益,
            "bps": 每股净资产,
            "pe": 市盈率,
            "pb": 市净率,
            "dividend_yield": 股息率(%),
            "market_cap": 总市值(亿),
            "company_name": 公司名称,
            "industry": 所属行业,
            "data_source": "eastmoney_em",
        }
    """
    result = {
        "revenue": None, "revenue_yoy": None,
        "net_profit": None, "profit_yoy": None,
        "roe": None, "roa": None,
        "gross_margin": None, "net_margin": None,
        "eps": None, "bps": None,
        "pe": None, "pb": None,
        "dividend_yield": None, "market_cap": None,
        "company_name": "", "industry": "",
        "data_source": "none",
    }

    # 确保代码格式正确（5位数字，不足补零）
    code = symbol.strip().upper().replace(".HK", "")
    if code.isdigit():
        code = code.zfill(5)

    fetched_any = False

    # === 1. 估值/盈利/ROE ===
    try:
        import akshare as ak

        df_val = ak.stock_hk_financial_indicator_em(symbol=code)
        if df_val is not None and not df_val.empty:
            row = df_val.iloc[0]

            # 营收
            revenue = _safe_float(row.get("营业总收入"))
            if revenue:
                # 原始数据单位是港元，转换为亿
                result["revenue"] = round(revenue / 1e8, 2) if revenue > 1e6 else revenue
                fetched_any = True

            # 营收同比
            result["revenue_yoy"] = _safe_float(row.get("营业总收入滚动环比增长(%)"))

            # 净利润
            net_profit = _safe_float(row.get("净利润"))
            if net_profit:
                result["net_profit"] = round(net_profit / 1e8, 2) if abs(net_profit) > 1e6 else net_profit
                fetched_any = True

            # 净利润环比
            result["profit_yoy"] = _safe_float(row.get("净利润滚动环比增长(%)"))

            # ROE
            result["roe"] = _safe_float(row.get("股东权益回报率(%)"))

            # ROA
            result["roa"] = _safe_float(row.get("总资产回报率(%)"))

            # 净利率
            result["net_margin"] = _safe_float(row.get("销售净利率(%)"))

            # 每股净资产
            result["bps"] = _safe_float(row.get("每股净资产(元)"))

            # 每股收益
            result["eps"] = _safe_float(row.get("基本每股收益(元)"))

            # 市盈率（东方财富的计算）
            pe = _safe_float(row.get("市盈率"))
            if pe:
                result["pe"] = pe

            # 市净率
            pb = _safe_float(row.get("市净率"))
            if pb:
                result["pb"] = pb

            # 股息率
            result["dividend_yield"] = _safe_float(row.get("股息率TTM(%)"))

            # 总市值
            mkt_cap = _safe_float(row.get("总市值(港元)"))
            if mkt_cap:
                result["market_cap"] = round(mkt_cap / 1e8, 2)

            logger.info(
                f"港股财务(东方财富): {code}, 营收={result['revenue']}亿, "
                f"ROE={result['roe']}%, PE={result['pe']}"
            )
    except Exception as e:
        logger.debug(f"东方财富财务指标获取失败 ({code}): {e}")

    # === 2. 运营数据（营收/毛利/毛利率） ===
    try:
        import akshare as ak

        df_ops = ak.stock_financial_hk_analysis_indicator_em(symbol=code)
        if df_ops is not None and not df_ops.empty:
            row = df_ops.iloc[0]

            # 营业额（如果第1步没获取到）
            if result["revenue"] is None:
                revenue = _safe_float(row.get("OPERATE_INCOME"))
                if revenue:
                    result["revenue"] = round(revenue / 1e8, 2) if revenue > 1e6 else revenue
                    fetched_any = True

            # 营收同比
            if result["revenue_yoy"] is None:
                result["revenue_yoy"] = _safe_float(row.get("OPERATE_INCOME_YOY"))

            # 毛利率（优先使用 API 直接计算的比例，不自己计算）
            gross_margin = _safe_float(row.get("GROSS_PROFIT_RATIO"))
            if gross_margin is not None:
                result["gross_margin"] = gross_margin

            # 股东应占利润
            if result["net_profit"] is None:
                holder_profit = _safe_float(row.get("HOLDER_PROFIT"))
                if holder_profit:
                    result["net_profit"] = round(holder_profit / 1e8, 2) if abs(holder_profit) > 1e6 else holder_profit
                    fetched_any = True

            # 利润同比
            if result["profit_yoy"] is None:
                result["profit_yoy"] = _safe_float(row.get("HOLDER_PROFIT_YOY"))

            # ROE（平均）
            if result["roe"] is None:
                result["roe"] = _safe_float(row.get("ROE_AVG"))

            # ROA
            if result["roa"] is None:
                result["roa"] = _safe_float(row.get("ROA"))

            # 净利率
            if result["net_margin"] is None:
                result["net_margin"] = _safe_float(row.get("NET_PROFIT_RATIO"))

            # EPS
            if result["eps"] is None:
                result["eps"] = _safe_float(row.get("EPS_TTM"))

            logger.info(
                f"港股运营数据(东方财富): {code}, 毛利率={result['gross_margin']}%"
            )
    except Exception as e:
        logger.debug(f"东方财富运营数据获取失败 ({code}): {e}")

    # === 3. 公司概况 ===
    try:
        import akshare as ak

        df_profile = ak.stock_hk_company_profile_em(symbol=code)
        if df_profile is not None and not df_profile.empty:
            row = df_profile.iloc[0]
            company_name = str(row.get("公司名称", ""))
            if company_name:
                result["company_name"] = company_name

            industry = str(row.get("所属行业", ""))
            if industry:
                result["industry"] = industry

            logger.info(f"港股公司概况: {company_name}, 行业={industry}")
    except Exception as e:
        logger.debug(f"东方财富公司概况获取失败 ({code}): {e}")

    if fetched_any:
        result["data_source"] = "eastmoney_em"
    else:
        result["data_source"] = "none"

    return result


def _safe_float(value) -> Optional[float]:
    """安全转换为 float"""
    if value is None:
        return None
    try:
        v = float(value)
        # 处理 NaN / inf
        if v != v or v == float('inf') or v == float('-inf'):
            return None
        return v
    except (ValueError, TypeError):
        return None
