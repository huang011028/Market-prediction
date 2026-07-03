"""
产业链上下游分析 + 催化剂日历

为行业对比分析师提供:
1. 产业链景气度传导分析
2. 行业催化剂日历（近期可能影响行业的催化事件）
"""

import logging
from typing import Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# 产业链上下游映射
INDUSTRY_CHAIN = {
    "新能源": {
        "upstream": ["有色金属", "化工"],
        "midstream": ["电子", "电力"],
        "downstream": ["汽车", "家电"],
        "description": "锂矿/电解液 → 电池/储能 → 电动车/储能应用",
    },
    "半导体": {
        "upstream": ["电子", "化工"],
        "midstream": ["半导体"],
        "downstream": ["电子", "通信", "计算机"],
        "description": "硅片/光刻胶 → 设计/制造 → 消费电子/5G/AI",
    },
    "房地产": {
        "upstream": ["银行", "水泥", "钢铁"],
        "midstream": ["房地产"],
        "downstream": ["家电", "建材"],
        "description": "融资/建材 → 开发/销售 → 装修/家电",
    },
    "汽车": {
        "upstream": ["钢铁", "化工", "电子"],
        "midstream": ["汽车"],
        "downstream": ["银行", "保险"],
        "description": "钢材/化工/电子 → 整车制造 → 汽车金融/保险",
    },
    "医药": {
        "upstream": ["化工"],
        "midstream": ["医药"],
        "downstream": ["银行", "保险"],
        "description": "化工原料 → 制药/器械 → 医保/商业保险",
    },
    "银行": {
        "upstream": [],
        "midstream": ["银行"],
        "downstream": ["房地产", "汽车", "家电"],
        "description": "央行/货币政策 → 银行 → 房贷/消费贷/企业贷",
    },
    "家电": {
        "upstream": ["电子", "化工", "有色金属"],
        "midstream": ["家电"],
        "downstream": ["房地产"],
        "description": "电子元器件/化工/有色 → 家电制造 → 新房装修",
    },
    "食品饮料": {
        "upstream": ["农业"],
        "midstream": ["食品饮料"],
        "downstream": ["银行"],
        "description": "农产品 → 食品加工 → 零售/餐饮",
    },
    "计算机": {
        "upstream": ["电子", "半导体"],
        "midstream": ["计算机"],
        "downstream": ["通信", "银行", "传媒"],
        "description": "芯片/电子 → 计算机/软件 → 云计算/AI/金融IT",
    },
    "通信": {
        "upstream": ["电子", "半导体"],
        "midstream": ["通信"],
        "downdown": ["计算机", "传媒"],
        "description": "芯片/电子 → 通信设备 → 5G/物联网/数据中心",
    },
}

# 行业催化剂日历（季节性/周期性事件）
INDUSTRY_CATALOGS = {
    "银行": [
        {"event": "年报披露期", "months": [3, 4], "impact": "neutral",
         "description": "银行年报集中披露，关注不良率和净息差变化"},
        {"event": "中期报告披露", "months": [8, 9], "impact": "neutral",
         "description": "中报披露，关注上半年信贷投放和资产质量"},
    ],
    "白酒": [
        {"event": "春节旺季", "months": [1, 2], "impact": "positive",
         "description": "春节消费旺季，白酒销量通常较好"},
        {"event": "中秋旺季", "months": [9, 10], "impact": "positive",
         "description": "中秋国庆消费旺季"},
    ],
    "新能源": [
        {"event": "补贴政策窗口", "months": [1, 2, 12], "impact": "positive",
         "description": "新能源补贴政策通常在此期间发布"},
        {"event": "半年报披露", "months": [7, 8], "impact": "neutral",
         "description": "关注装机量和出货量数据"},
    ],
    "房地产": [
        {"event": "金九银十", "months": [9, 10], "impact": "positive",
         "description": "传统销售旺季"},
        {"event": "年报披露", "months": [3, 4], "impact": "neutral",
         "description": "关注销售数据和土地储备"},
    ],
    "医药": [
        {"event": "医保谈判", "months": [11, 12], "impact": "mixed",
         "description": "医保目录谈判，降价压力vs纳入医保"},
        {"event": "年报披露", "months": [3, 4], "impact": "neutral",
         "description": "关注研发管线和业绩"},
    ],
    "半导体": [
        {"event": "消费电子新品季", "months": [8, 9, 10], "impact": "positive",
         "description": "苹果/华为新品发布，拉动芯片需求"},
        {"event": "年报披露", "months": [3, 4], "impact": "neutral",
         "description": "关注资本开支和产能利用率"},
    ],
}


def analyze_industry_chain(industry: str) -> dict:
    """
    分析产业链上下游的景气度传导。

    Args:
        industry: 行业名称

    Returns:
        {
            "upstream": ["上游行业1", ...],
            "downstream": ["下游行业1", ...],
            "description": "产业链描述",
            "implication": "景气度传导分析"
        }
    """
    chain = INDUSTRY_CHAIN.get(industry)
    if not chain:
        return {"note": "产业链数据不可用"}

    return {
        "upstream": chain.get("upstream", []),
        "downstream": chain.get("downstream", []),
        "description": chain.get("description", ""),
        "implication": (
            f"如果{industry}景气度变化，"
            f"上游{', '.join(chain.get('upstream', []))}和"
            f"下游{', '.join(chain.get('downstream', []))}可能受到传导影响"
        ),
    }


def get_upcoming_catalysts(industry: str, months_ahead: int = 2) -> list[dict]:
    """
    获取行业近期催化剂。

    Args:
        industry: 行业名称
        months_ahead: 提前月数

    Returns:
        催化剂列表
    """
    catalogs = INDUSTRY_CATALOGS.get(industry, [])
    if not catalogs:
        return []

    now = datetime.now()
    upcoming = []

    for catalog in catalogs:
        for month in catalog.get("months", []):
            # 计算该月距离现在的时间
            target_date = datetime(now.year, month, 15)
            if target_date < now:
                target_date = datetime(now.year + 1, month, 15)

            months_until = (target_date.year - now.year) * 12 + (target_date.month - now.month)

            if 0 <= months_until <= months_ahead:
                upcoming.append({
                    **catalog,
                    "target_month": f"{target_date.year}-{month:02d}",
                    "months_until": months_until,
                })

    return sorted(upcoming, key=lambda x: x.get("months_until", 99))


def build_catalyst_prompt_appendix(catalysts: list[dict]) -> str:
    """将催化剂信息注入到 prompt 中"""
    if not catalysts:
        return ""

    appendix = "\n\n## 📅 近期行业催化剂\n"
    appendix += "以下事件可能在未来几个月影响该行业:\n\n"

    for i, cat in enumerate(catalysts, 1):
        impact_emoji = {"positive": "📈", "negative": "📉", "mixed": "↔️", "neutral": "➡️"}
        emoji = impact_emoji.get(cat.get("impact", "neutral"), "➡️")
        appendix += f"{i}. {emoji} **{cat['event']}** ({cat.get('target_month', 'N/A')})\n"
        appendix += f"   {cat['description']}\n\n"

    return appendix
