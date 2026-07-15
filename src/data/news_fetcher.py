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
from email.utils import parsedate_to_datetime
import re
from typing import Optional
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET
import logging

from src.data import us_fallbacks
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

    def __init__(self, max_items: int = 20, source_timeout_seconds: float = 18):
        self.max_items = max_items
        self.source_timeout_seconds = source_timeout_seconds

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
        symbol_clean = (
            symbol.replace(".HK", "")
            .replace(".SZ", "")
            .replace(".SS", "")
            .replace(".SH", "")
        )
        canonical_symbol = symbol_clean if market in ("A", "HK") else original_symbol

        today = datetime.now()
        start_date = today - timedelta(days=max(days, 1))
        date_range = f"{start_date.strftime('%Y-%m-%d')} ~ {today.strftime('%Y-%m-%d')}"
        company_name = self._resolve_company_name(canonical_symbol, market) or canonical_symbol
        if market == "US":
            company_name = (
                us_fallbacks.get_us_company_reference(symbol).get("name")
                or original_symbol
            )

        # === 多源并发采集 ===
        all_items = []
        sources_used = []

        if market in ("A", "HK"):
            # A 股和港股：并发调用东方财富 + 新浪
            results = await asyncio.gather(
                self._fetch_with_timeout(
                    "东方财富",
                    self._fetch_from_eastmoney(symbol_clean, market),
                ),
                self._fetch_with_timeout(
                    "新浪财经",
                    self._fetch_from_sina(symbol_clean, market),
                ),
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

            if not all_items:
                google_items = await self._fetch_with_timeout(
                    "Google News RSS",
                    asyncio.to_thread(
                        self._fetch_from_regional_google_news,
                        canonical_symbol,
                        market,
                        company_name,
                        days,
                    ),
                )
                if google_items:
                    all_items.extend(google_items)
                    sources_used.append("google_news_rss")
                    logger.info(f"Google News RSS({market}): {len(google_items)} 条")

        elif market == "US":
            # 美股：yfinance → Google News RSS
            try:
                yf_items = await asyncio.wait_for(
                    asyncio.to_thread(self._fetch_from_yfinance, symbol),
                    timeout=self.source_timeout_seconds,
                )
                if yf_items:
                    all_items.extend(yf_items)
                    sources_used.append("yfinance")
            except asyncio.TimeoutError:
                logger.warning(f"yfinance 新闻获取超时: {symbol}")
            except Exception as e:
                logger.warning(f"yfinance 新闻获取失败: {e}")

            if not all_items:
                try:
                    google_items = await asyncio.wait_for(
                        asyncio.to_thread(
                            us_fallbacks.fetch_us_news_google,
                            symbol,
                            company_name=company_name if company_name != original_symbol else "",
                            days=days,
                            max_items=self.max_items * 2,
                        ),
                        timeout=self.source_timeout_seconds,
                    )
                    if google_items:
                        all_items.extend(google_items)
                        sources_used.append("google_news_rss")
                        logger.info(f"Google News RSS: {len(google_items)} 条")
                except asyncio.TimeoutError:
                    logger.warning(f"Google News RSS 获取超时: {symbol}")
                except Exception as e:
                    logger.warning(f"Google News RSS 获取失败: {e}")

        # === 预处理管线 ===
        if all_items:
            # 去重 + 相关度过滤 + 情感标注 + 分类 + 时间衰减
            preproc = process_news_pipeline(
                all_items,
                symbol=canonical_symbol,
                market=market,
                reference_date=today,
                max_output=self.max_items,
            )

            # 预处理后的 top news
            processed_items = preproc.get("top_news", [])
            if not processed_items:
                processed_items = self._fallback_processed_items(all_items)
                if processed_items:
                    preproc["top_news"] = processed_items
                    preproc["after_relevance_filter"] = len(processed_items)
                    preproc.setdefault("anomaly_flags", {})[
                        "relevance_filter_empty_fallback"
                    ] = True
                    preproc["fallback_reason"] = (
                        "相关度过滤后为空，已保留原始来源新闻供模型低置信度判断"
                    )
            news_source = "+".join(sources_used) if sources_used else "unavailable"

            logger.info(
                f"预处理完成: {preproc['total_fetched']}条→"
                f"去重{preproc['after_dedup']}→"
                f"过滤{preproc['after_relevance_filter']}→"
                f"输出{len(processed_items)}条"
            )

            return NewsData(
                symbol=canonical_symbol,
                company_name=company_name,
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
                symbol=canonical_symbol,
                company_name=company_name,
                news_count=0,
                date_range=date_range,
                news_source="unavailable",
                news_items=[],
                sources_used=[],
            )

    async def _fetch_with_timeout(self, source_name: str, coro) -> Optional[list[dict]]:
        """给单个新闻源设置独立超时，避免拖垮整个新闻 Agent。"""
        try:
            return await asyncio.wait_for(coro, timeout=self.source_timeout_seconds)
        except asyncio.TimeoutError:
            logger.warning(f"{source_name} 新闻获取超时")
            return None
        except Exception as e:
            logger.warning(f"{source_name} 新闻获取失败: {e}")
            return None

    @staticmethod
    def _resolve_company_name(symbol: str, market: str) -> str:
        if market == "US":
            return ""
        try:
            keywords = get_stock_keywords(symbol, market)
        except Exception:
            return ""
        clean_symbol = symbol.replace(".", "").upper()
        for keyword in keywords:
            text = str(keyword).strip()
            normalized = text.replace(".", "").upper()
            if text and not normalized.isdigit() and normalized != clean_symbol:
                return text
        return ""

    def _fallback_processed_items(self, items: list[dict]) -> list[dict]:
        """相关度过滤误杀时，保留少量原始新闻并打标给后续校验降权。"""
        fallback_items = []
        for item in items[: self.max_items]:
            copied = dict(item)
            copied.setdefault("_sentiment", "unknown")
            copied.setdefault("_category", "uncategorized")
            copied.setdefault("_time_weight", 0.3)
            copied["_relevance_fallback"] = True
            fallback_items.append(copied)
        return fallback_items

    def _fetch_from_regional_google_news(
        self,
        symbol: str,
        market: str,
        company_name: str,
        days: int,
    ) -> list[dict]:
        """A/HK 同市场 Google News RSS 兜底。"""
        import requests

        locale = {
            "A": ("zh-CN", "CN", "CN:zh-Hans", "股票"),
            "HK": ("zh-HK", "HK", "HK:zh-Hant", "港股"),
        }.get(market)
        if not locale:
            return []

        hl, gl, ceid, market_keyword = locale
        query_parts = []
        if company_name and company_name != symbol:
            query_parts.append(f'"{company_name}"')
        query_parts.extend([symbol, market_keyword, f"when:{max(1, days)}d"])
        url = (
            "https://news.google.com/rss/search?"
            f"q={quote_plus(' '.join(query_parts))}&hl={hl}&gl={gl}&ceid={ceid}"
        )

        try:
            resp = requests.get(url, timeout=min(12, self.source_timeout_seconds))
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
        except Exception as e:
            logger.debug(f"Google News RSS({market}:{symbol}) 获取失败: {e}")
            return []

        items = []
        for item in root.findall(".//item")[: self.max_items]:
            title = self._rss_text(item, "title")
            if not title:
                continue
            items.append({
                "title": title,
                "summary": self._strip_html(self._rss_text(item, "description"))[:500],
                "source": self._rss_text(item, "source") or "Google News",
                "time": self._normalize_rss_time(self._rss_text(item, "pubDate")),
                "url": self._rss_text(item, "link"),
            })
        return items

    @staticmethod
    def _rss_text(node: ET.Element, child: str) -> str:
        found = node.find(child)
        return (found.text or "").strip() if found is not None else ""

    @staticmethod
    def _strip_html(value: str) -> str:
        return re.sub(r"<[^>]+>", "", value or "").strip()

    @staticmethod
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
