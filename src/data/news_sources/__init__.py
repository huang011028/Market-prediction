"""
新闻源采集模块

支持的新闻源：
- eastmoney: 东方财富新闻（主力源，覆盖 A 股 + 港股）
- sina: 新浪财经新闻（补充源，覆盖 A 股 + 港股）
"""

from .eastmoney import fetch_from_eastmoney
from .sina import fetch_from_sina

__all__ = ["fetch_from_eastmoney", "fetch_from_sina"]
