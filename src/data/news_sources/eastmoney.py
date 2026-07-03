"""
东方财富新闻源

通过 akshare 获取东方财富个股新闻，覆盖 A 股和港股。

v2 改进：
- 港股优先用公司名搜索（如"美团"），代码搜索仅作为补充
- 因为 stock_news_em 对港股代码（4 位数字）只能做关键词匹配，
  容易匹配到"3690美元/吨"等无关内容
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def fetch_from_eastmoney(
    symbol: str,
    market: str = "A",
    days: int = 14,
    max_items: int = 20,
    company_names: Optional[list[str]] = None,
) -> Optional[list[dict]]:
    """从东方财富获取个股新闻

    Args:
        symbol: 股票代码（纯数字，如 '000001' 或 '3690'）
        market: 市场（'A' / 'HK'）
        days: 回溯天数
        max_items: 最大返回条数
        company_names: 公司名列表（如 ['美团', 'Meituan']），
                       港股优先用公司名搜索以获得更精准的结果

    Returns:
        新闻列表，失败返回 None
    """
    try:
        if market == "A":
            return _fetch_a_share(symbol, max_items)
        elif market == "HK":
            return _fetch_hk_share(symbol, max_items, company_names)
        else:
            return None
    except Exception as e:
        logger.warning(f"东方财富新闻获取失败 ({market}:{symbol}): {e}")
        return None


def _fetch_a_share(symbol: str, max_items: int) -> Optional[list[dict]]:
    """A 股个股新闻（代码精确匹配，效果好）"""
    try:
        import akshare as ak

        df = ak.stock_news_em(symbol=symbol)
        if df is not None and not df.empty:
            items = _parse_news_em(df, max_items)
            if items:
                logger.info(f"东方财富 A股 {symbol}: {len(items)} 条新闻")
                return items
    except Exception as e:
        logger.debug(f"stock_news_em A股失败: {e}")
    return None


def _fetch_hk_share(
    symbol: str,
    max_items: int,
    company_names: Optional[list[str]] = None,
) -> Optional[list[dict]]:
    """港股个股新闻

    策略：
    1. 优先用公司名搜索（如"美团"），因为 stock_news_em 对公司名的匹配更精准
    2. 代码搜索作为补充（可能匹配到噪声，但后续相关度过滤会处理）
    3. 合并去重
    """
    import akshare as ak

    all_items = []
    tried_methods = []

    # === 方案 1: 公司名搜索（最精准）===
    if company_names:
        for name in company_names[:3]:  # 最多尝试 3 个公司名
            try:
                df = ak.stock_news_em(symbol=name)
                if df is not None and not df.empty:
                    items = _parse_news_em(df, max_items)
                    if items:
                        logger.info(
                            f"东方财富 港股 公司名'{name}': {len(items)} 条新闻"
                        )
                        all_items.extend(items)
                        tried_methods.append(f"name:{name}")
                        break  # 第一个有效的公司名就够了
            except Exception as e:
                logger.debug(f"公司名'{name}'搜索失败: {e}")

    # === 方案 2: 代码搜索（补充）===
    try:
        df = ak.stock_news_em(symbol=symbol)
        if df is not None and not df.empty:
            items = _parse_news_em(df, max_items)
            if items:
                logger.info(
                    f"东方财富 港股 代码'{symbol}': {len(items)} 条新闻 (补充)"
                )
                # 简单去重：排除标题已存在的
                existing_titles = {item["title"][:30] for item in all_items}
                new_items = [
                    item for item in items
                    if item["title"][:30] not in existing_titles
                ]
                all_items.extend(new_items)
                tried_methods.append(f"code:{symbol}")
    except Exception as e:
        logger.debug(f"代码'{symbol}'搜索失败: {e}")

    if all_items:
        logger.info(
            f"东方财富 港股 {symbol}: 共 {len(all_items)} 条 "
            f"(方法: {', '.join(tried_methods)})"
        )
        return all_items[:max_items]

    return None


def _parse_news_em(df, max_items: int) -> list[dict]:
    """统一解析 stock_news_em 返回的 DataFrame"""
    items = []
    for _, row in df.head(max_items).iterrows():
        title = str(row.get("新闻标题", row.get("标题", row.iloc[1] if len(row) > 1 else "")))
        summary = str(row.get("新闻内容", row.get("内容", row.iloc[2][:300] if len(row) > 2 else "")))
        source = str(row.get("文章来源", row.get("来源", "东方财富")))
        pub_time = str(row.get("发布时间", row.get("时间", "")))
        url = str(row.get("新闻链接", row.get("链接", "")))
        items.append({
            "title": title,
            "summary": summary[:300],
            "source": source,
            "time": pub_time,
            "url": url,
        })
    return items
