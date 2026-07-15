"""
新浪财经新闻源

通过爬取新浪财经个股新闻页获取 A 股和港股新闻。
"""
import asyncio
import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 新浪财经个股新闻页 URL
SINA_A_SHARE_URL = "https://vip.stock.finance.sina.com.cn/corp/go.php/vCB_AllNewsStock/symbol/{code}.phtml"
SINA_HK_SHARE_URL = "https://vip.stock.finance.sina.com.cn/corp/go.php/vCB_AllNewsStock/symbol/{code}.phtml"


async def fetch_from_sina(
    symbol: str,
    market: str = "A",
    days: int = 14,
    max_items: int = 15,
) -> Optional[list[dict]]:
    """从新浪财经获取个股新闻

    Args:
        symbol: 股票代码
        market: 市场（'A' / 'HK'）
        days: 回溯天数（暂用于过滤，解析时只保留最近 N 天的）
        max_items: 最大返回条数

    Returns:
        新闻列表，失败返回 None
    """
    try:
        if market == "A":
            return await asyncio.to_thread(_fetch_a_share_news, symbol, max_items)
        elif market == "HK":
            return await asyncio.to_thread(_fetch_hk_share_news, symbol, max_items)
        else:
            return None
    except Exception as e:
        logger.warning(f"新浪新闻获取失败 ({market}:{symbol}): {e}")
        return None


def _fetch_a_share_news(symbol: str, max_items: int) -> Optional[list[dict]]:
    """爬取 A 股新浪财经个股新闻页"""
    # 新浪 A 股代码格式：上交所需要 sh 前缀，深交所需要 sz 前缀
    code = symbol.zfill(6)
    if code.startswith(("6", "5", "9")):
        sina_symbol = f"sh{code}"
    else:
        sina_symbol = f"sz{code}"

    try:
        html = _fetch_page(sina_symbol)
        if not html:
            return None
        return _parse_sina_news_html(html, max_items, market="A")
    except Exception as e:
        logger.debug(f"新浪 A 股新闻解析失败 ({symbol}): {e}")
        return None


def _fetch_hk_share_news(symbol: str, max_items: int) -> Optional[list[dict]]:
    """爬取港股新浪财经个股新闻页"""
    # 港股代码格式：hk + 代码（4位补零）
    code = symbol.zfill(4)
    sina_symbol = f"hk{code}"

    try:
        html = _fetch_page(sina_symbol)
        if not html:
            return None
        return _parse_sina_news_html(html, max_items, market="HK")
    except Exception as e:
        logger.debug(f"新浪港股新闻解析失败 ({symbol}): {e}")
        return None


def _fetch_page(sina_symbol: str) -> Optional[str]:
    """获取新浪个股新闻页面 HTML"""
    import requests

    url = SINA_A_SHARE_URL.format(code=sina_symbol)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://finance.sina.com.cn",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=15, verify=False)
        resp.encoding = "gbk"
        if resp.status_code == 200 and len(resp.text) > 500:
            return resp.text
        logger.debug(f"新浪新闻页返回异常: status={resp.status_code}, len={len(resp.text)}")
        return None
    except Exception as e:
        logger.debug(f"新浪新闻页请求失败: {e}")
        return None


def _parse_sina_news_html(html: str, max_items: int, market: str = "A") -> Optional[list[dict]]:
    """从新浪新闻页 HTML 中提取新闻列表

    页面结构大致为：
    <div class="datelist">
      <ul>
        <li><a href="...">标题</a> &nbsp;来源 &nbsp;日期</li>
      </ul>
    </div>
    """
    # 尝试用 BeautifulSoup（如果可用），否则用正则
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        items = []

        # 查找新闻列表区域
        datelist = soup.find("div", class_="datelist")
        if not datelist:
            datelist = soup.find("div", id="con02_content")
        if not datelist:
            # 尝试其他可能的容器
            datelist = soup

        # 提取所有链接
        for tag in datelist.find_all("a"):
            title = tag.get_text(strip=True)
            href = tag.get("href", "")

            if not title or len(title) < 4:
                continue

            # 获取整行文本（包含来源和时间）
            parent_text = ""
            if tag.parent:
                parent_text = tag.parent.get_text(strip=True)

            # 尝试提取来源和时间
            source = "新浪财经"
            pub_time = ""

            # 时间提取模式
            time_patterns = [
                r"(\d{4}-\d{2}-\d{2}\s*\d{2}:\d{2})",
                r"(\d{4}-\d{2}-\d{2})",
                r"(\d{2}-\d{2}\s*\d{2}:\d{2})",
                r"(\d{2}:\d{2})",
            ]
            for pattern in time_patterns:
                match = re.search(pattern, parent_text)
                if match:
                    pub_time = match.group(1)
                    break

            # 来源提取：常见格式 "来源：XXX" / "XXX网"
            source_match = re.search(r"来源[：:]\s*(\S+)", parent_text)
            if source_match:
                source = source_match.group(1)

            items.append({
                "title": title,
                "summary": title,  # 新浪列表页通常无摘要
                "source": f"新浪-{source}" if source != "新浪财经" else "新浪财经",
                "time": pub_time,
                "url": href,
            })

            if len(items) >= max_items:
                break

    except ImportError:
        # BeautifulSoup 不可用，用正则降级方案
        items = _parse_sina_news_regex(html, max_items)

    if items:
        # v2: 过滤明显的 A 股大盘噪音（对港股标的尤其重要）
        items = _filter_market_noise(items, market)
        logger.info(f"新浪财经新闻: {len(items)} 条 (过滤后)")
        return items

    # 如果 BeautifulSoup 解析为空，尝试正则
    if 'bs4' not in str(type(None)):
        items = _parse_sina_news_regex(html, max_items)

    return items if items else None


def _parse_sina_news_regex(html: str, max_items: int) -> list[dict]:
    """正则降级方案：从 HTML 中提取链接和时间"""
    items = []

    # 匹配 <a href="...">标题</a> 的模式
    link_pattern = re.compile(
        r'<a\s+[^>]*href="([^"]*)"[^>]*>\s*(.*?)\s*</a>',
        re.DOTALL,
    )

    matches = link_pattern.findall(html)
    for href, raw_title in matches:
        # 清理标题中的 HTML 标签
        title = re.sub(r"<[^>]+>", "", raw_title).strip()
        if not title or len(title) < 4:
            continue

        # 过滤非新闻链接
        if any(skip in href.lower() for skip in ["javascript", "mailto", "#"]):
            continue

        # 提取时间
        pub_time = ""
        time_match = re.search(r"(\d{4}-\d{2}-\d{2})", html[html.find(title):html.find(title) + 200] if title in html else "")
        if time_match:
            pub_time = time_match.group(1)

        items.append({
            "title": title,
            "summary": title,
            "source": "新浪财经",
            "time": pub_time,
            "url": href if href.startswith("http") else f"https://finance.sina.com.cn{href}",
        })

        if len(items) >= max_items:
            break

    return items


# ================================================================
# 新浪新闻后过滤：去除明显不相关的市场噪音
# ================================================================

# A 股大盘关键词（当查询港股标的时，这些内容大概率不相关）
A_SHARE_NOISE_KEYWORDS = [
    "沪指", "深成指", "创业板", "科创板", "北交所",
    "上证", "深证", "A股", "涨停", "跌停", "连板",
    "沪深", "中小板", "新三板",
]

# 通用市场噪声（与个股无关的宏观新闻）
GENERAL_NOISE_KEYWORDS = [
    "央行操作", "逆回购到期", "MLF", "LPR", "社融数据",
    "人民币中间价", "外汇储备",
]


def _filter_market_noise(items: list[dict], market: str) -> list[dict]:
    """过滤新浪新闻中的市场噪音（尤其针对港股标的）

    新浪的港股个股新闻页有时会混入 A 股大盘新闻，
    这些新闻与港股标的无关，需要过滤。
    """
    if not items:
        return items

    filtered = []
    for item in items:
        title = item.get("title", "")

        # 港股标的：过滤 A 股大盘新闻
        if market == "HK":
            if any(kw in title for kw in A_SHARE_NOISE_KEYWORDS):
                logger.debug(f"过滤 A 股噪音: {title[:40]}...")
                continue

        # 通用过滤：过于宏观的新闻（除非标题同时包含公司名）
        general_hit = any(kw in title for kw in GENERAL_NOISE_KEYWORDS)
        if general_hit and len(title) < 20:
            # 纯宏观短标题（如"央行开展XXX操作"），大概率不相关
            logger.debug(f"过滤通用噪音: {title[:40]}...")
            continue

        filtered.append(item)

    return filtered
