"""
行业轮动检测器

检测 A 股市场的行业轮动信号，为行业对比分析师提供"动态择时"能力。

检测以下轮动信号:
1. 估值差异极端化: 行业间 PE 差异达到历史极端 → 均值回归概率大
2. 动量反转: 前期强势板块动量衰减 → 资金可能切换
3. 风格切换: 成长↔价值、大盘↔小盘的风格轮动
"""

import logging
from typing import Optional
from collections import defaultdict

logger = logging.getLogger(__name__)


class SectorRotationDetector:
    """行业轮动检测器"""

    # 风格分组
    GROWTH_INDUSTRIES = [
        "科技", "半导体", "新能源", "生物医药", "计算机",
        "电子", "通信", "传媒", "军工",
    ]
    VALUE_INDUSTRIES = [
        "银行", "保险", "房地产", "电力",
    ]
    DEFENSIVE_INDUSTRIES = [
        "食品饮料", "医药", "电力", "家电", "银行",
    ]
    CYCLICAL_INDUSTRIES = [
        "钢铁", "有色金属", "煤炭", "化工",
        "水泥", "汽车", "证券",
    ]

    def detect_rotation_signals(self, industry_data: dict = None) -> list[dict]:
        """
        检测当前是否出现行业轮动信号。

        Args:
            industry_data: 行业数据字典（可选，若提供则使用实时数据）
                {"银行": {"pe": 5.8, "change_20d": 3.5}, ...}

        Returns:
            轮动信号列表
        """
        signals = []

        if not industry_data:
            return signals

        # 1. 估值差异极端化检测
        valuation_signal = self._check_valuation_extremes(industry_data)
        if valuation_signal:
            signals.append(valuation_signal)

        # 2. 动量反转检测
        momentum_signal = self._check_momentum_reversal(industry_data)
        if momentum_signal:
            signals.append(momentum_signal)

        # 3. 风格切换检测
        style_signal = self._check_style_rotation(industry_data)
        if style_signal:
            signals.append(style_signal)

        return signals

    def _check_valuation_extremes(self, industry_data: dict) -> Optional[dict]:
        """检测行业间估值差异是否达到极端"""
        pe_values = {}
        for name, data in industry_data.items():
            pe = data.get("pe")
            if pe and pe > 0:
                pe_values[name] = pe

        if len(pe_values) < 5:
            return None

        sorted_pe = sorted(pe_values.items(), key=lambda x: x[1])
        cheapest = sorted_pe[:3]
        most_expensive = sorted_pe[-3:]

        # 如果最贵/最便宜比值 > 5 → 极端分化
        if cheapest[0][1] > 0:
            ratio = most_expensive[-1][1] / cheapest[0][1]
            if ratio > 5:
                return {
                    "signal_type": "valuation_extreme",
                    "description": (
                        f"行业估值极端分化: {cheapest[0][0]}(PE={cheapest[0][1]:.1f}) "
                        f"vs {most_expensive[-1][0]}(PE={most_expensive[-1][1]:.1f}), "
                        f"比值={ratio:.1f}x"
                    ),
                    "rotation_direction": "高估值→低估值（均值回归）",
                    "strength": min(0.9, ratio / 10),
                    "cheapest": [name for name, _ in cheapest],
                    "most_expensive": [name for name, _ in most_expensive],
                }

        return None

    def _check_momentum_reversal(self, industry_data: dict) -> Optional[dict]:
        """检测前期强势板块是否出现动量衰减"""
        # 获取近20日涨幅
        momentum = {}
        for name, data in industry_data.items():
            change_20d = data.get("change_20d")
            change_5d = data.get("change_5d")
            if change_20d is not None and change_5d is not None:
                momentum[name] = {"change_20d": change_20d, "change_5d": change_5d}

        if len(momentum) < 5:
            return None

        # 找前期最强（近20日涨幅最大）的行业
        strongest = max(momentum.items(), key=lambda x: x[1]["change_20d"])
        weakest = min(momentum.items(), key=lambda x: x[1]["change_20d"])

        # 如果前期最强的行业近5日转跌 → 动量反转信号
        if strongest[1]["change_20d"] > 5 and strongest[1]["change_5d"] < -2:
            return {
                "signal_type": "momentum_reversal",
                "description": (
                    f"前期强势行业 {strongest[0]} 出现动量衰减: "
                    f"近20日+{strongest[1]['change_20d']:.1f}%, "
                    f"近5日{strongest[1]['change_5d']:.1f}%"
                ),
                "rotation_direction": f"{strongest[0]}→{weakest[0]}（资金切换）",
                "strength": 0.6,
            }

        return None

    def _check_style_rotation(self, industry_data: dict) -> Optional[dict]:
        """检测风格切换（成长↔价值）"""
        growth_momentum = []
        value_momentum = []

        for name, data in industry_data.items():
            change_20d = data.get("change_20d")
            if change_20d is None:
                continue

            if any(g in name for g in self.GROWTH_INDUSTRIES):
                growth_momentum.append(change_20d)
            elif any(v in name for v in self.VALUE_INDUSTRIES):
                value_momentum.append(change_20d)

        if len(growth_momentum) < 2 or len(value_momentum) < 2:
            return None

        avg_growth = sum(growth_momentum) / len(growth_momentum)
        avg_value = sum(value_momentum) / len(value_momentum)

        # 如果成长和价值动量差异显著 → 风格可能切换
        diff = avg_growth - avg_value
        if abs(diff) > 5:
            if diff > 0:
                direction = "价值→成长（成长占优）"
                description = (
                    f"成长板块近20日平均涨幅({avg_growth:.1f}%) "
                    f"显著高于价值板块({avg_value:.1f}%), "
                    f"风格偏向成长"
                )
            else:
                direction = "成长→价值（价值占优）"
                description = (
                    f"价值板块近20日平均涨幅({avg_value:.1f}%) "
                    f"显著高于成长板块({avg_growth:.1f}%), "
                    f"风格偏向价值"
                )

            return {
                "signal_type": "style_rotation",
                "description": description,
                "rotation_direction": direction,
                "strength": min(0.8, abs(diff) / 10),
            }

        return None

    def get_industry_style(self, industry_name: str) -> str:
        """获取行业所属风格（优先级: cyclical > growth > value > defensive）"""
        # 周期性行业优先判断（因为"价值"和"周期"有重叠）
        if any(c in industry_name for c in self.CYCLICAL_INDUSTRIES):
            return "cyclical"
        if any(g in industry_name for g in self.GROWTH_INDUSTRIES):
            return "growth"
        if any(v in industry_name for v in self.VALUE_INDUSTRIES):
            return "value"
        if any(d in industry_name for d in self.DEFENSIVE_INDUSTRIES):
            return "defensive"
        return "unknown"


def build_rotation_prompt_appendix(rotation_signals: list[dict]) -> str:
    """将轮动信号注入到 prompt 中"""
    if not rotation_signals:
        return ""

    appendix = "\n\n## ⚠️ 行业轮动预警\n"
    appendix += "系统检测到以下行业轮动信号，请在分析中考虑:\n\n"

    for i, signal in enumerate(rotation_signals, 1):
        appendix += f"{i}. **{signal['description']}**\n"
        appendix += f"   轮动方向: {signal['rotation_direction']}\n"
        appendix += f"   信号强度: {signal['strength']:.0%}\n\n"

    appendix += "请判断: 这些轮动信号对标的行业的影响是正面还是负面？\n"

    return appendix
