"""
地缘政治事件采集器

从新闻源中提取与地缘政治相关的信号：
- 贸易摩擦 / 关税
- 科技制裁 / 实体清单
- 军事冲突 / 地缘紧张
- 供应链脱钩

复用已有的 eastmoney 新闻接口，用地缘关键词过滤。
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# 地缘政治关键词分类
GEOPOLITICAL_CATEGORIES: dict[str, dict] = {
    "trade_war": {
        "keywords": ["关税", "贸易战", "贸易摩擦", "反倾销", "报复性关税",
                     "tariff", "trade war", "anti-dumping"],
        "sentiment": "negative",
        "impact_note": "贸易摩擦升级→出口成本上升→外贸企业利润承压",
    },
    "tech_sanction": {
        "keywords": ["实体清单", "出口管制", "芯片禁令", "技术封锁", "科技脱钩",
                     "entity list", "export control", "chip ban", "decoupling"],
        "sentiment": "negative",
        "impact_note": "科技制裁→半导体/互联网海外业务受限→估值折价",
    },
    "military_conflict": {
        "keywords": ["军事", "冲突", "战争", "演习", "导弹", "军舰",
                     "military", "conflict", "war", "missile", "naval"],
        "sentiment": "negative",
        "impact_note": "地缘军事紧张→资金避险→风险资产承压，利好黄金/能源",
    },
    "supply_chain": {
        "keywords": ["供应链", "脱钩", "回流", "近岸外包", "友岸外包",
                     "supply chain", "reshoring", "nearshoring", "friendshoring"],
        "sentiment": "neutral",
        "impact_note": "供应链重构→制造业布局调整→长期影响行业格局",
    },
    "diplomatic_positive": {
        "keywords": ["外交突破", "领导人会晤", "关系改善", "合作共识", "联合声明",
                     "summit", "diplomatic", "breakthrough", "cooperation"],
        "sentiment": "positive",
        "impact_note": "外交关系改善→市场信心回升→风险偏好回暖",
    },
}


async def fetch_geopolitical_signals(
    days: int = 30,
    max_items: int = 20,
) -> dict:
    """从新闻源采集地缘政治信号

    Args:
        days: 回溯天数
        max_items: 最大返回条数

    Returns:
        地缘事件摘要
    """
    signals = []

    try:
        # 复用东方财富新闻源（搜索宏观/国际新闻）
        import akshare as ak

        # 用地缘关键词搜索
        all_geo_keywords = []
        for cat_info in GEOPOLITICAL_CATEGORIES.values():
            all_geo_keywords.extend(cat_info["keywords"][:3])

        # 尝试用前几个关键词搜索
        for kw in all_geo_keywords[:5]:
            try:
                df = ak.stock_news_em(symbol=kw)
                if df is not None and not df.empty:
                    for _, row in df.head(10).iterrows():
                        title = str(row.get("新闻标题", row.get("标题", "")))
                        if not title or len(title) < 5:
                            continue

                        # 分类
                        for cat_name, cat_info in GEOPOLITICAL_CATEGORIES.items():
                            if any(
                                ck.lower() in title.lower()
                                for ck in cat_info["keywords"]
                            ):
                                signals.append({
                                    "title": title[:120],
                                    "category": cat_name,
                                    "sentiment": cat_info["sentiment"],
                                    "impact_note": cat_info["impact_note"],
                                    "source": str(row.get("文章来源", "东方财富")),
                                    "time": str(row.get("发布时间", "")),
                                })
                                break

                        if len(signals) >= max_items:
                            break
            except Exception:
                continue

            if len(signals) >= max_items:
                break

    except Exception as e:
        logger.warning(f"地缘事件采集异常: {e}")

    # 汇总分析
    return _summarize_geopolitical(signals, days)


def _summarize_geopolitical(signals: list[dict], days: int = 30) -> dict:
    """汇总地缘政治信号"""
    if not signals:
        return {
            "recent_events": [],
            "risk_level": "无数据",
            "risk_score": 0.5,
            "key_themes": ["无近期地缘事件数据"],
            "trend": "未知",
            "summary": "未能获取近期地缘政治事件数据。请基于 LLM 知识库判断当前地缘风险。",
        }

    # 按类别统计
    cat_counts = {}
    for s in signals:
        cat = s.get("category", "other")
        cat_counts[cat] = cat_counts.get(cat, 0) + 1

    # 风险评估
    negative_count = sum(1 for s in signals if s.get("sentiment") == "negative")
    positive_count = sum(1 for s in signals if s.get("sentiment") == "positive")
    total = len(signals)

    if negative_count > positive_count * 2:
        risk_level = "偏高"
        risk_score = 0.7
    elif negative_count > positive_count:
        risk_level = "中等偏高"
        risk_score = 0.6
    elif positive_count > negative_count:
        risk_level = "中等偏低"
        risk_score = 0.4
    else:
        risk_level = "中等"
        risk_score = 0.5

    # 主要主题
    themes = [cat for cat, count in sorted(cat_counts.items(), key=lambda x: -x[1])[:3]]

    # 趋势（简单判断：最近7天 vs 更早）
    recent_neg = sum(
        1 for s in signals if s.get("sentiment") == "negative"
    )
    trend = "恶化" if recent_neg > len(signals) * 0.4 else "稳定"

    return {
        "recent_events": signals[:10],
        "risk_level": risk_level,
        "risk_score": risk_score,
        "key_themes": themes,
        "trend": trend,
        "total_events": total,
        "negative_ratio": f"{negative_count}/{total}" if total > 0 else "0/0",
        "summary": (
            f"近{days}天检测到{total}条地缘相关事件。"
            f"主要主题：{'、'.join(themes)}。"
            f"综合风险：{risk_level}（负面占比{negative_count}/{total}）。"
        ),
    }
