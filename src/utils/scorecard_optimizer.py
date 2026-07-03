"""
评分卡权重优化器

通过回测数据优化基本面评分卡的 4 个维度权重。
当前固定权重: profitability=30, growth=25, valuation=25, health=20
优化目标: 找到使"评分卡总分"与"未来收益"相关性最高的权重组合
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class ScorecardWeightOptimizer:
    """评分卡权重优化器"""

    def optimize(self, backtest_data: list[dict]) -> dict:
        """
        通过网格搜索找到最优权重组合。

        Args:
            backtest_data: 回测数据列表
                [{"scorecard": {"breakdown": {"profitability": {"score": 25}, ...}},
                  "future_return_pct": 8.5}, ...]

        Returns:
            最优权重: {"profitability": 0.35, "growth": 0.20, "valuation": 0.30, "health": 0.15}
        """
        if len(backtest_data) < 10:
            logger.warning(f"回测数据不足 ({len(backtest_data)} < 10)，使用默认权重")
            return {"profitability": 0.30, "growth": 0.25, "valuation": 0.25, "health": 0.20}

        best_weights = {"profitability": 0.30, "growth": 0.25, "valuation": 0.25, "health": 0.20}
        best_correlation = 0.0

        # 网格搜索: 遍历权重组合
        for p_w in range(15, 50, 5):  # 15% ~ 45%
            for g_w in range(10, 45, 5):  # 10% ~ 40%
                for v_w in range(10, 45, 5):  # 10% ~ 40%
                    h_w = 100 - p_w - g_w - v_w
                    if h_w < 5 or h_w > 30:
                        continue

                    correlation = self._calculate_correlation(
                        backtest_data,
                        p_w / 100,
                        g_w / 100,
                        v_w / 100,
                        h_w / 100,
                    )

                    if abs(correlation) > abs(best_correlation):
                        best_correlation = correlation
                        best_weights = {
                            "profitability": p_w / 100,
                            "growth": g_w / 100,
                            "valuation": v_w / 100,
                            "health": h_w / 100,
                        }

        logger.info(
            f"评分卡最优权重: {best_weights} "
            f"(相关系数={best_correlation:.3f}, 样本={len(backtest_data)})"
        )
        return best_weights

    def _calculate_correlation(self, data: list[dict],
                                p_w: float, g_w: float,
                                v_w: float, h_w: float) -> float:
        """计算加权分与未来收益的 Pearson 相关系数"""
        scores = []
        returns = []

        for item in data:
            sc = item.get("scorecard", {}).get("breakdown", {})
            if not sc:
                continue

            score = (
                sc.get("profitability", {}).get("score", 0) * p_w +
                sc.get("growth", {}).get("score", 0) * g_w +
                sc.get("valuation", {}).get("score", 0) * v_w +
                sc.get("health", {}).get("score", 0) * h_w
            )
            scores.append(score)
            returns.append(item.get("future_return_pct", 0))

        if len(scores) < 5:
            return 0.0

        # Pearson correlation
        n = len(scores)
        mean_s = sum(scores) / n
        mean_r = sum(returns) / n

        cov = sum((s - mean_s) * (r - mean_r) for s, r in zip(scores, returns)) / n
        std_s = (sum((s - mean_s) ** 2 for s in scores) / n) ** 0.5
        std_r = (sum((r - mean_r) ** 2 for r in returns) / n) ** 0.5

        if std_s == 0 or std_r == 0:
            return 0.0

        return cov / (std_s * std_r)


# 行业差异化评分基准
INDUSTRY_BENCHMARKS = {
    "银行": {"good_roe": 10, "good_margin": 30, "good_growth": 5, "good_pb": 0.8},
    "白酒": {"good_roe": 20, "good_margin": 40, "good_growth": 15, "good_pb": 5.0},
    "证券": {"good_roe": 7, "good_margin": 25, "good_growth": 10, "good_pb": 1.2},
    "保险": {"good_roe": 12, "good_margin": 15, "good_growth": 10, "good_pb": 1.0},
    "医药": {"good_roe": 15, "good_margin": 20, "good_growth": 15, "good_pb": 3.5},
    "新能源": {"good_roe": 12, "good_margin": 15, "good_growth": 25, "good_pb": 2.5},
    "家电": {"good_roe": 18, "good_margin": 12, "good_growth": 10, "good_pb": 2.0},
    "电子": {"good_roe": 12, "good_margin": 10, "good_growth": 20, "good_pb": 3.0},
    "半导体": {"good_roe": 15, "good_margin": 20, "good_growth": 25, "good_pb": 4.0},
    "互联网": {"good_roe": 15, "good_margin": 20, "good_growth": 20, "good_pb": 3.5},
    "计算机": {"good_roe": 12, "good_margin": 15, "good_growth": 20, "good_pb": 3.5},
    "房地产": {"good_roe": 8, "good_margin": 15, "good_growth": 5, "good_pb": 0.8},
    "电力": {"good_roe": 10, "good_margin": 20, "good_growth": 5, "good_pb": 1.2},
    "汽车": {"good_roe": 10, "good_margin": 8, "good_growth": 15, "good_pb": 1.5},
    "食品饮料": {"good_roe": 18, "good_margin": 20, "good_growth": 12, "good_pb": 4.0},
    "军工": {"good_roe": 8, "good_margin": 10, "good_growth": 20, "good_pb": 2.5},
    "传媒": {"good_roe": 8, "good_margin": 15, "good_growth": 15, "good_pb": 2.0},
    "通信": {"good_roe": 10, "good_margin": 12, "good_growth": 15, "good_pb": 2.5},
    "化工": {"good_roe": 12, "good_margin": 10, "good_growth": 10, "good_pb": 1.5},
    "钢铁": {"good_roe": 6, "good_margin": 5, "good_growth": 5, "good_pb": 0.6},
    "煤炭": {"good_roe": 12, "good_margin": 20, "good_growth": 5, "good_pb": 1.0},
    "有色金属": {"good_roe": 10, "good_margin": 10, "good_growth": 10, "good_pb": 1.5},
    "水泥": {"good_roe": 10, "good_margin": 15, "good_growth": 5, "good_pb": 1.0},
}


def get_industry_benchmark(industry: str) -> Optional[dict]:
    """获取行业基准"""
    return INDUSTRY_BENCHMARKS.get(industry)
