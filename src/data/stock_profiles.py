"""
标的个性化参数配置（Round2）

不同股票的技术特征差异很大：
- 银行股：低波动、趋势跟随性强
- 科技股：高波动、反转倾向高
- 蓝筹股：金叉信号可靠性高

为 LLM 提供标的级别的上下文，帮助它做出更准确的判断。
"""

from typing import Optional
import logging

logger = logging.getLogger(__name__)


# 已知标的特征（硬编码，后续可从验证数据中学习）
STOCK_PROFILES = {
    "000001": {
        "name": "平安银行",
        "type": "large_cap_bank",
        "avg_atr_pct": 1.5,
        "trend_following": 0.7,
        "reversal_tendency": 0.3,
        "ma_reliability": "中等偏高",
        "typical_weekly_move": 3.0,
        "rsi_effective_range": (25, 75),
        "notes": [
            "大型银行股，低波动，单周涨跌通常<3%",
            "趋势跟随性强，金叉/死叉信号可信度较高",
            "RSI很少进入极端超买超卖区(25-75)",
            "对宏观政策(利率/准备金)敏感，技术面需结合宏观",
        ]
    },
    "600519": {
        "name": "贵州茅台",
        "type": "large_cap_consumer",
        "avg_atr_pct": 2.0,
        "trend_following": 0.8,
        "reversal_tendency": 0.2,
        "ma_reliability": "高",
        "typical_weekly_move": 4.0,
        "rsi_effective_range": (30, 80),
        "notes": [
            "白酒龙头，趋势性极强，回调多为买入机会",
            "均线支撑可靠，MA60是强支撑位",
            "成交量在节假日前后有明显季节性",
        ]
    },
    "300750": {
        "name": "宁德时代",
        "type": "large_cap_growth",
        "avg_atr_pct": 3.0,
        "trend_following": 0.5,
        "reversal_tendency": 0.5,
        "ma_reliability": "中等",
        "typical_weekly_move": 6.0,
        "rsi_effective_range": (20, 85),
        "notes": [
            "高波动成长股，单周涨跌可达5-8%",
            "受行业政策和新能源板块情绪影响大",
            "技术指标在震荡市可靠性下降",
        ]
    },
    "0700": {
        "name": "腾讯控股",
        "type": "large_cap_tech_hk",
        "avg_atr_pct": 2.5,
        "trend_following": 0.6,
        "reversal_tendency": 0.4,
        "ma_reliability": "中等",
        "typical_weekly_move": 5.0,
        "rsi_effective_range": (25, 80),
        "notes": [
            "港股科技龙头，受南向资金和美股中概情绪影响",
            "回购行为常构成阶段性底部支撑",
            "大股东减持(Naspers)期间技术面信号可能失效",
        ]
    },
    "3690": {
        "name": "美团",
        "type": "large_cap_tech_hk",
        "avg_atr_pct": 3.0,
        "trend_following": 0.5,
        "reversal_tendency": 0.6,
        "ma_reliability": "中等偏低",
        "typical_weekly_move": 6.0,
        "rsi_effective_range": (20, 85),
        "notes": [
            "高波动港股科技股，对政策和竞争格局敏感",
            "PE为负时技术面信号需打折，缺乏价值锚",
            "与抖音竞争新闻常引发短期剧烈波动",
        ]
    },
    "9988": {
        "name": "阿里巴巴",
        "type": "large_cap_tech_hk",
        "avg_atr_pct": 2.8,
        "trend_following": 0.55,
        "reversal_tendency": 0.5,
        "ma_reliability": "中等",
        "typical_weekly_move": 5.5,
        "rsi_effective_range": (20, 85),
        "notes": [
            "港股科技龙头，受监管政策和消费复苏预期影响",
            "回购+分红提供底部支撑",
        ]
    },
}


def get_stock_profile(symbol: str) -> dict:
    """获取标的个性化参数"""
    code = symbol.replace(".HK", "").replace(".SZ", "").replace(".SS", "").strip().upper()
    code = code.zfill(5) if len(code) <= 5 else code.zfill(6)

    if code in STOCK_PROFILES:
        return STOCK_PROFILES[code]

    # 默认参数
    return {
        "name": symbol,
        "type": "unknown",
        "avg_atr_pct": 2.5,
        "trend_following": 0.6,
        "reversal_tendency": 0.4,
        "ma_reliability": "中等（默认）",
        "typical_weekly_move": 5.0,
        "rsi_effective_range": (30, 70),
        "notes": ["未知标的，使用默认参数，技术信号可靠性待验证"],
    }


def build_profile_context(symbol: str) -> str:
    """构建标的信息文本，注入 Agent prompt"""
    profile = get_stock_profile(symbol)

    lines = [
        "",
        "## 🏷️ 标的信息（个性化参考）",
        "",
        f"- 名称: {profile['name']}",
        f"- 类型: {profile['type']}",
        f"- 日均波幅(ATR): ~{profile['avg_atr_pct']}%",
        f"- 历史周波动: 通常 < {profile['typical_weekly_move']}%",
        f"- 趋势跟随性: {profile['trend_following']} (高=趋势信号更可靠)",
        f"- 反转倾向: {profile['reversal_tendency']} (高=超买超卖信号更可靠)",
        f"- 均线信号可靠性: {profile['ma_reliability']}",
        f"- RSI 有效区间: {profile['rsi_effective_range'][0]}-{profile['rsi_effective_range'][1]}",
        "",
    ]

    if profile["notes"]:
        lines.append("**分析提示**:")
        for note in profile["notes"]:
            lines.append(f"- {note}")
        lines.append("")

    return "\n".join(lines)
