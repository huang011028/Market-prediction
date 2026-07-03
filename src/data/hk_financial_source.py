"""
港股财务数据获取器

从多个数据源获取港股财务指标，解决港股财务数据缺失问题。
降级链: AASTOCKS → 东方财富港股F10 → Alpha Vantage → yfinance(部分)
"""

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


async def fetch_hk_financials(symbol: str) -> dict:
    """
    获取港股财务数据（多源降级链）

    Args:
        symbol: 港股代码（不含 HK 后缀，如 "0700"）

    Returns:
        {
            "revenue": 最新年度营收(亿),
            "revenue_yoy": 营收同比(%),
            "net_profit": 最新年度净利润(亿),
            "profit_yoy": 利润同比(%),
            "roe": ROE(%),
            "gross_margin": 毛利率(%),
            "net_margin": 净利率(%),
            "eps": 每股盈利,
            "data_source": "aastocks/eastmoney/yfinance/none",
        }
    """
    result = {
        "revenue": None, "revenue_yoy": None,
        "net_profit": None, "profit_yoy": None,
        "roe": None, "gross_margin": None, "net_margin": None,
        "eps": None, "data_source": "none",
    }

    # 方案 A: AASTOCKS 爬取
    aastocks_result = await _fetch_from_aastocks(symbol)
    if aastocks_result and aastocks_result.get("data_source") == "aastocks":
        result.update(aastocks_result)
        logger.info(f"港股财务(AASTOCKS): {symbol}")
        return result

    # 方案 B: 东方财富港股 F10
    eastmoney_result = await _fetch_from_eastmoney_hk(symbol)
    if eastmoney_result and eastmoney_result.get("data_source") == "eastmoney_hk":
        result.update(eastmoney_result)
        logger.info(f"港股财务(东方财富): {symbol}")
        return result

    # 方案 C: Alpha Vantage
    av_result = await _fetch_from_alphavantage(symbol)
    if av_result and av_result.get("data_source") == "alphavantage":
        result.update(av_result)
        logger.info(f"港股财务(Alpha Vantage): {symbol}")
        return result

    logger.warning(f"港股财务数据全部获取失败: {symbol}")
    return result


async def _fetch_from_aastocks(symbol: str) -> Optional[dict]:
    """
    从 AASTOCKS 爬取港股财务数据

    URL: https://www.aastocks.com/sc/stocks/analysis/company-fundamental/financial-ratios?symbol={code}
    """
    try:
        import requests
        from bs4 import BeautifulSoup

        code = symbol.zfill(5)
        result = {"data_source": "none"}

        # 获取财务比率页
        url = f"https://www.aastocks.com/sc/stocks/analysis/company-fundamental/financial-ratios?symbol={code}"
        resp = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        })

        if resp.status_code != 200:
            return result

        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        # 解析表格 — AASTOCKS 使用 class 为"table"的表格
        tables = soup.find_all("table")

        for table in tables:
            rows = table.find_all("tr")
            for row in rows:
                cells = row.find_all(["td", "th"])
                if len(cells) < 2:
                    continue

                label = cells[0].get_text(strip=True)

                # 提取最新一期数值（通常是最后一列）
                if len(cells) >= 3:
                    value_text = cells[-1].get_text(strip=True)
                    value = _parse_aastocks_value(value_text)
                else:
                    continue

                # 匹配关键指标
                if "股东回报率" in label or "ROE" in label.upper():
                    result["roe"] = value
                elif "毛利率" in label:
                    result["gross_margin"] = value
                elif "纯利率" in label or "净利率" in label or "Profit Margin" in label:
                    result["net_margin"] = value

        # 获取损益表页（营收/利润）
        url_pl = f"https://www.aastocks.com/sc/stocks/analysis/company-fundamental/profit-loss?symbol={code}"
        resp_pl = requests.get(url_pl, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        })

        if resp_pl.status_code == 200:
            resp_pl.encoding = "utf-8"
            soup_pl = BeautifulSoup(resp_pl.text, "html.parser")
            tables_pl = soup_pl.find_all("table")

            for table in tables_pl:
                rows = table.find_all("tr")
                for row in rows:
                    cells = row.find_all(["td", "th"])
                    if len(cells) < 3:
                        continue

                    label = cells[0].get_text(strip=True)
                    value_text = cells[-1].get_text(strip=True)
                    value = _parse_aastocks_value(value_text)

                    if "营业额" in label or "营收" in label or "Revenue" in label or "Turnover" in label:
                        result["revenue"] = value
                    elif "盈利" in label or "Profit" in label or "净利润" in label:
                        if "股东" in label or "attributable" in label.lower():
                            result["net_profit"] = value
                    elif "每股盈利" in label or "EPS" in label.upper():
                        result["eps"] = value

        # 判断是否获取到有效数据
        if any(v is not None for k, v in result.items() if k != "data_source"):
            result["data_source"] = "aastocks"
            logger.info(f"AASTOCKS 财务数据: ROE={result.get('roe')}, 营收={result.get('revenue')}")
        else:
            result["data_source"] = "none"

        return result

    except requests.exceptions.Timeout:
        logger.debug(f"AASTOCKS 超时: {symbol}")
        return {"data_source": "none"}
    except Exception as e:
        logger.debug(f"AASTOCKS 获取失败 ({symbol}): {e}")
        return {"data_source": "none"}


async def _fetch_from_eastmoney_hk(symbol: str) -> Optional[dict]:
    """
    东方财富港股 F10 接口（akshare）
    注：此前因网络限制不可用，当前环境需重试
    """
    try:
        import akshare as ak

        result = {"data_source": "none"}

        try:
            df = ak.stock_hk_financial_analysis_indicator(
                symbol=symbol, indicator="按报告期"
            )
            if df is not None and not df.empty:
                latest = df.iloc[-1]

                result["revenue"] = _safe_float(latest.get("营业总收入"))
                result["net_profit"] = _safe_float(latest.get("净利润"))
                result["roe"] = _safe_float(latest.get("净资产收益率"))
                result["gross_margin"] = _safe_float(latest.get("毛利率"))
                result["net_margin"] = _safe_float(latest.get("净利率"))
                result["eps"] = _safe_float(latest.get("基本每股收益"))

                if len(df) >= 2:
                    prev = df.iloc[-2]
                    curr_rev = _safe_float(latest.get("营业总收入"))
                    prev_rev = _safe_float(prev.get("营业总收入"))
                    if curr_rev and prev_rev and prev_rev > 0:
                        result["revenue_yoy"] = round((curr_rev / prev_rev - 1) * 100, 2)

                result["data_source"] = "eastmoney_hk"
                logger.info(f"东方财富港股F10: {symbol}")
        except Exception as e:
            logger.debug(f"东方财富港股F10 失败: {e}")

        return result

    except Exception as e:
        logger.debug(f"东方财富港股F10 异常: {e}")
        return {"data_source": "none"}


async def _fetch_from_alphavantage(symbol: str) -> Optional[dict]:
    """
    Alpha Vantage INCOME_STATEMENT / BALANCE_SHEET API
    免费 25次/天，需注册 API key
    """
    import os
    api_key = os.environ.get("ALPHA_VANTAGE_API_KEY")
    if not api_key:
        return {"data_source": "none"}

    try:
        import requests

        result = {"data_source": "none"}

        # 损益表
        url = (
            f"https://www.alphavantage.co/query"
            f"?function=INCOME_STATEMENT&symbol={symbol}.HK&apikey={api_key}"
        )
        resp = requests.get(url, timeout=15)
        data = resp.json()

        if "annualReports" in data and data["annualReports"]:
            reports = data["annualReports"]
            latest = reports[0]

            revenue = _safe_float(latest.get("totalRevenue"))
            if revenue:
                result["revenue"] = revenue / 1e8  # 转换为亿

            net_profit = _safe_float(latest.get("netIncome"))
            if net_profit:
                result["net_profit"] = net_profit / 1e8

            # 计算同比
            if len(reports) >= 2:
                prev_rev = _safe_float(reports[1].get("totalRevenue"))
                if revenue and prev_rev and prev_rev > 0:
                    result["revenue_yoy"] = round((revenue / prev_rev - 1) * 100, 2)

            result["data_source"] = "alphavantage"
            logger.info(f"Alpha Vantage 港股: {symbol}")

        return result

    except Exception as e:
        logger.debug(f"Alpha Vantage 港股 失败: {e}")
        return {"data_source": "none"}


def _parse_aastocks_value(text: str) -> Optional[float]:
    """解析 AASTOCKS 的数值文本"""
    if not text or text in ("--", "N/A", ""):
        return None

    try:
        # 去掉千分位逗号
        text = text.replace(",", "")

        # 处理单位: 亿/万/百万
        multiplier = 1.0
        if "亿" in text:
            multiplier = 1.0
            text = text.replace("亿", "")
        elif "百万" in text:
            multiplier = 0.01  # 百万→亿
            text = text.replace("百万", "")
        elif "万" in text:
            multiplier = 0.0001  # 万→亿
            text = text.replace("万", "")

        # 处理百分比
        if "%" in text:
            text = text.replace("%", "")

        value = float(text) * multiplier
        return value if value == value else None

    except (ValueError, TypeError):
        return None


def _safe_float(value) -> Optional[float]:
    """安全转换为 float"""
    if value is None:
        return None
    try:
        v = float(value)
        return v if v == v else None
    except (ValueError, TypeError):
        return None
