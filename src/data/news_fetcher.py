"""
新闻数据获取器 v2

支持多源并发采集 + 预处理管线：
1. 东方财富（主力源，akshare）
2. 新浪财经（补充源，HTML 爬取）
3. yfinance（美股备选）

采集后经预处理管线：去重 → 相关度过滤 → 情感预标注 → 事件分类 → 时间衰减
"""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
import logging

from .news_preprocessor import process_news_pipeline, get_stock_keywords

logger = logging.getLogger(__name__)


@dataclass
class NewsItem:
    """单条新闻"""
    title: str
    summary: str
    source: str
    publish_time: str
    url: str = ""


@dataclass
class NewsData:
    """新闻数据封装（v2：含预处理信息）"""
    symbol: str
    company_name: str
    news_count: int
    date_range: str
    news_source: str  # "eastmoney+sina" / "eastmoney" / "yfinance" / "knowledge_base" / "unavailable"

    # 原始新闻（向后兼容）
    news_items: list[dict] = field(default_factory=list)

    # v2 新增：预处理结果
    preprocessing_summary: dict = field(default_factory=dict)
    sources_used: list[str] = field(default_factory=list)

    def to_agent_dict(self) -> dict:
        """转为 Agent 可用的字典格式"""
        result = {
            "symbol": self.symbol,
            "company_name": self.company_name,
            "news_count": self.news_count,
            "date_range": self.date_range,
            "news_source": self.news_source,
            "sources_used": self.sources_used,
        }

        # 包含预处理摘要（如果有）
        if self.preprocessing_summary:
            result["preprocessing"] = self.preprocessing_summary

        # 保留原始新闻列表（向后兼容，可能被截断）
        if self.news_items:
            result["news_items"] = self.news_items

        return result


class NewsFetcher:
    """新闻数据获取器 v2（多源 + 预处理）"""

    def __init__(self, max_items: int = 20):
        self.max_items = max_items

    async def fetch(self, symbol: str, market: str = "A", days: int = 14) -> NewsData:
        """获取标的相关的近期新闻（多源并发 + 预处理管线）

        Args:
            symbol: 股票代码
            market: 市场（A/HK/US）
            days: 回溯天数

        Returns:
            NewsData（含预处理结果）
        """
        symbol = symbol.strip().upper()
        original_symbol = symbol
        symbol_clean = symbol.replace(".HK", "").replace(".SZ", "").replace(".SS", "")

        today = datetime.now()
        start_date = today - timedelta(days=max(days, 1))
        date_range = f"{start_date.strftime('%Y-%m-%d')} ~ {today.strftime('%Y-%m-%d')}"

        # === 多源并发采集 ===
        all_items = []
        sources_used = []

        if market in ("A", "HK"):
            # A 股和港股：并发调用东方财富 + 新浪
            results = await asyncio.gather(
                self._fetch_from_eastmoney(symbol_clean, market),
                self._fetch_from_sina(symbol_clean, market),
                return_exceptions=True,
            )

            if not isinstance(results[0], Exception) and results[0]:
                all_items.extend(results[0])
                sources_used.append("eastmoney")
                logger.info(f"东方财富: {len(results[0])} 条")

            if not isinstance(results[1], Exception) and results[1]:
                all_items.extend(results[1])
                sources_used.append("sina")
                logger.info(f"新浪财经: {len(results[1])} 条")

        elif market == "US":
            # 美股：尝试 yfinance
            try:
                yf_items = self._fetch_from_yfinance(symbol)
                if yf_items:
                    all_items.extend(yf_items)
                    sources_used.append("yfinance")
            except Exception as e:
                logger.warning(f"yfinance 新闻获取失败: {e}")

        # === 预处理管线 ===
        if all_items:
            # 去重 + 相关度过滤 + 情感标注 + 分类 + 时间衰减
            preproc = process_news_pipeline(
                all_items,
                symbol=original_symbol,
                market=market,
                reference_date=today,
                max_output=self.max_items,
            )

            # 预处理后的 top news
            processed_items = preproc.get("top_news", [])
            news_source = "+".join(sources_used) if sources_used else "unavailable"

            logger.info(
                f"预处理完成: {preproc['total_fetched']}条→"
                f"去重{preproc['after_dedup']}→"
                f"过滤{preproc['after_relevance_filter']}→"
                f"输出{len(processed_items)}条"
            )

            return NewsData(
                symbol=original_symbol,
                company_name=original_symbol,
                news_count=len(processed_items),
                date_range=date_range,
                news_source=news_source,
                news_items=processed_items,
                preprocessing_summary=preproc,
                sources_used=sources_used,
            )
        else:
            # 无可用新闻
            logger.warning(f"未能获取 {original_symbol} 的新闻数据")
            return NewsData(
                symbol=original_symbol,
                company_name=original_symbol,
                news_count=0,
                date_range=date_range,
                news_source="unavailable",
                news_items=[],
                sources_used=[],
            )

    # ================================================================
    # 各源采集（委托给 news_sources 模块）
    # ================================================================

    async def _fetch_from_eastmoney(self, symbol: str, market: str) -> Optional[list[dict]]:
        """东方财富新闻"""
        try:
            from .news_sources.eastmoney import fetch_from_eastmoney

            # 获取公司名关键词（港股优先用公司名搜索）
            keywords = get_stock_keywords(symbol, market)
            # 过滤掉纯数字代码，只保留公司名
            company_names = [kw for kw in keywords if not kw.replace(".", "").isdigit()]

            return await fetch_from_eastmoney(
                symbol,
                market,
                max_items=self.max_items * 2,
                company_names=company_names if company_names else None,
            )
        except Exception as e:
            logger.warning(f"东方财富新闻异常: {e}")
            return None

    async def _fetch_from_sina(self, symbol: str, market: str) -> Optional[list[dict]]:
        """新浪财经新闻"""
        try:
            from .news_sources.sina import fetch_from_sina

            return await fetch_from_sina(symbol, market, max_items=self.max_items)
        except Exception as e:
            logger.warning(f"新浪新闻异常: {e}")
            return None

    # ================================================================
    # yfinance 新闻（美股）
    # ================================================================

    def _fetch_from_yfinance(self, symbol: str) -> Optional[list[dict]]:
        """通过 yfinance 获取美股新闻"""
        try:
            import yfinance as yf

            ticker = yf.Ticker(symbol)
            news = ticker.news

            if not news:
                return None

            items = []
            for item in news[: self.max_items]:
                content = item.get("content", {})
                items.append({
                    "title": content.get("title", ""),
                    "summary": content.get("summary", "")[:300],
                    "source": content.get("provider", {}).get("displayName", "yfinance"),
                    "time": content.get("pubDate", ""),
                    "url": content.get("canonicalUrl", {}).get("url", ""),
                })

            return items

        except Exception as e:
            logger.warning(f"yfinance 新闻获取异常: {e}")
            return None
