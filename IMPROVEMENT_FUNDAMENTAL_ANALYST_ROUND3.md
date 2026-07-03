# 🏢 公司前景分析师改进方案 — Round 3

> **版本**: v3.0 | **日期**: 2026-07-03 | **前置**: Round 1-2 全部完成

---

## 目录

1. [Round 2 实施回顾](#1-round-2-实施回顾)
2. [Round 3 改进总览](#2-round-3-改进总览)
3. [历史案例 RAG](#3-历史案例-rag)
4. [行业景气度整合](#4-行业景气度整合)
5. [预测区间概率化](#5-预测区间概率化)
6. [实施路线图](#6-实施路线图)

---

## 1. Round 2 实施回顾

### 1.1 已完成内容

| 模块 | 文件 | 核心成果 | 状态 |
|------|------|---------|------|
| 🇭🇰 港股财务 | `hk_financial_source.py` | AASTOCKS/东方财富/Alpha Vantage 降级链 | ✅ |
| 📊 置信度校准 | `fundamental_calibrator.py` | 分桶统计 + 贝叶斯收缩 + 分位预测力 | ✅ |
| ⚖️ 评分卡优化 | `scorecard_optimizer.py` | 网格搜索最优权重 + 行业基准(23个) | ✅ |
| 🔄 Fetcher 集成 | `fundamental_fetcher.py` | `_fetch_hk_financials_supplement()` | ✅ |
| 🧠 Agent 升级 | `fundamental_analyst.py` | 校准器集成 + 数据质量分桶 | ✅ |
| 🧪 测试 | `test_fundamental_v3.py` | 32 个新测试（校准/优化/基准/集成） | ✅ |

### 1.2 当前架构全景

```
┌─────────────────────────────────────────────────────────────────┐
│                     v3 完整架构                                   │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              数据采集层（多源降级）                        │   │
│  │  A股: akshare财务 + 腾讯PE + akshare PE历史              │   │
│  │  港股: 腾讯PE + AASTOCKS/东方财富/Alpha Vantage 财务     │   │
│  │  美股: yfinance + Alpha Vantage                         │   │
│  └────────────────────────┬────────────────────────────────┘   │
│                           ▼                                    │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              预处理管线                                   │   │
│  │  ① 趋势提取 (8季度序列)                                  │   │
│  │  ② 估值分位 (3年/5年 PE 百分位)                          │   │
│  │  ③ 质量评分卡 (4维度100分, 行业差异化)                    │   │
│  │  ④ 价值陷阱检测 (低PE+盈利恶化)                          │   │
│  │  ⑤ 数据质量评估 (completeness + freshness)              │   │
│  └────────────────────────┬────────────────────────────────┘   │
│                           ▼                                    │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              多步推理引擎                                 │   │
│  │  Step A: 综合评估 (质量+估值+催化+风险)                   │   │
│  │  Step B: 综合判断 + 反思                                 │   │
│  │  → 置信度校准 (ceiling + 历史回归 + 评分卡调整)          │   │
│  │  → 一致性校验 (方向/分位/陷阱/ceiling)                   │   │
│  └────────────────────────┬────────────────────────────────┘   │
│                           ▼                                    │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              自进化闭环                                   │   │
│  │  预测 → 存储 → 事后验证 → 更新校准统计 → 影响下次预测    │   │
│  │  按数据完整度/评分卡等级/估值分位 分桶统计                 │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### 1.3 遗留问题（Round 3 解决）

| # | 问题 | 影响 | 优先级 |
|---|------|------|--------|
| 1 | 无历史案例 RAG | 纯靠 LLM 知识库，无法利用项目自身积累 | 🟡 P1 |
| 2 | 无行业景气度整合 | 基本面分析缺少"行业环境"维度 | 🟡 P1 |
| 3 | 预测输出为固定区间 | 无法表达"70%概率在 2-8%，30%概率在 -5-0%" | 🟢 P2 |
| 4 | 评分卡权重未实际优化 | 当前仍用固定权重，回测优化接口已就绪但未执行 | 🟡 P1 |

---

## 2. Round 3 改进总览

### 2.1 目标

Round 3 是"深度优化"阶段，聚焦于：
1. **知识复用**：利用历史案例辅助当前判断
2. **宏观-行业-公司联动**：将行业景气度引入基本面分析
3. **输出升级**：从固定区间到概率分布

### 2.2 改进维度

| 维度 | 改进项 | 优先级 | 预期收益 |
|------|--------|--------|---------|
| 🧠 架构 | 历史案例 RAG 检索 | 🟡 P1 | 利用项目积累的历史规律 |
| 🧠 架构 | 行业景气度整合 | 🟡 P1 | 宏观-行业-公司联动 |
| 📊 输出 | 预测区间概率化 | 🟢 P2 | 更精确的风险表达 |
| 🔬 质量 | 评分卡权重实际优化 | 🟡 P1 | 数据驱动权重调优 |

---

## 3. 历史案例 RAG

### 3.1 核心思路

每次预测都被 `PredictionStore` 记录并在事后验证。利用这些历史数据：
- 找到与当前标的相似的历史案例
- 提取"当时怎么判断的 → 实际发生了什么"的经验
- 注入到 prompt 中作为参考

### 3.2 实现方案

```python
# src/utils/fundamental_case_retriever.py

"""
基本面分析历史案例检索器

从 PredictionStore 中检索与当前标的最相似的历史案例，
提取经验教训，注入到分析 prompt 中。
"""

import logging
from typing import Optional
from collections import defaultdict

logger = logging.getLogger(__name__)


class FundamentalCaseRetriever:
    """基本面分析历史案例检索"""

    def __init__(self, prediction_store=None):
        self.store = prediction_store

    def retrieve_similar_cases(self, data: dict, top_k: int = 3) -> list[dict]:
        """
        检索与当前标的最相似的历史案例。

        相似度维度:
        - 同行业
        - 相似估值分位 (±10%)
        - 相似评分卡总分 (±10分)
        - 相似趋势方向

        Args:
            data: 当前标的的预处理数据
            top_k: 返回案例数

        Returns:
            相似案例列表
        """
        if not self.store:
            return []

        # 从 PredictionStore 获取历史预测
        historical = self._get_historical_predictions()
        if not historical:
            return []

        # 计算相似度
        current_industry = data.get("industry", "")
        current_pe_pct = data.get("valuation_analysis", {}).get("pe_percentile_3yr")
        current_score = data.get("quality_scorecard", {}).get("total", 0)
        current_trend = data.get("financials", {}).get("_trend", {}).get("revenue_trend", "")

        scored_cases = []
        for case in historical:
            score = 0.0

            # 同行业: +3 分
            if case.get("industry") == current_industry:
                score += 3.0

            # 估值分位相似: +2 分
            case_pe_pct = case.get("pe_percentile")
            if case_pe_pct is not None and current_pe_pct is not None:
                if abs(case_pe_pct - current_pe_pct) < 0.1:
                    score += 2.0
                elif abs(case_pe_pct - current_pe_pct) < 0.2:
                    score += 1.0

            # 评分卡相似: +2 分
            case_score = case.get("scorecard_total", 0)
            if abs(case_score - current_score) < 10:
                score += 2.0
            elif abs(case_score - current_score) < 20:
                score += 1.0

            # 趋势方向相同: +1 分
            if case.get("revenue_trend") == current_trend:
                score += 1.0

            if score >= 3.0:  # 至少要有 3 分相似度
                scored_cases.append((score, case))

        # 按相似度排序，取 top_k
        scored_cases.sort(key=lambda x: x[0], reverse=True)
        return [case for _, case in scored_cases[:top_k]]

    def _get_historical_predictions(self) -> list[dict]:
        """从 PredictionStore 获取历史预测"""
        try:
            # 查询已验证的历史预测
            # 这里需要 PredictionStore 提供按 agent_name 查询的接口
            return []
        except Exception:
            return []

    def format_cases_for_prompt(self, cases: list[dict]) -> str:
        """将案例格式化为 prompt 注入文本"""
        if not cases:
            return ""

        appendix = "\n\n## 📚 历史相似案例参考\n"
        appendix += "以下是与当前标的相似的历史分析案例，请参考:\n\n"

        for i, case in enumerate(cases, 1):
            appendix += f"### 案例 {i}: {case.get('target', 'N/A')} ({case.get('date', 'N/A')})\n"
            appendix += f"- 行业: {case.get('industry', 'N/A')}\n"
            appendix += f"- 估值分位: {case.get('pe_percentile', 'N/A')}\n"
            appendix += f"- 评分卡: {case.get('scorecard_total', 'N/A')}分\n"
            appendix += f"- 当时判断: {case.get('direction', 'N/A')} (置信度: {case.get('confidence', 'N/A')})\n"
            appendix += f"- 实际结果: {case.get('actual_change_pct', 'N/A')}%\n"
            appendix += f"- 方向判断: {'✅ 正确' if case.get('was_correct') else '❌ 错误'}\n"
            if case.get("lesson"):
                appendix += f"- 经验教训: {case['lesson']}\n"
            appendix += "\n"

        return appendix
```

---

## 4. 行业景气度整合

### 4.1 核心思路

基本面分析不能只看公司自身，还需要知道"当前处于行业周期的什么阶段"。

数据来源:
- 行业近期涨跌幅（来自 industry_fetcher）
- 行业估值分位
- 行业资金流向（可选）

### 4.2 实现方案

```python
def assess_industry_cycle_for_fundamental(industry_name: str, market: str = "A") -> dict:
    """
    评估行业景气度，为基本面分析提供"行业环境"上下文。

    Returns:
        {
            "cycle": "recovery/boom/slowdown/depression",
            "industry_momentum": "positive/negative/neutral",
            "implication": "行业景气向上，公司盈利有支撑",
            "confidence_adjustment": 0.05,  // 对 confidence 的调整
        }
    """
    # 从 industry_preprocessor 复用周期判断逻辑
    from src.data.industry_preprocessor import classify_industry_cycle

    # 获取行业趋势数据
    trend = _get_industry_trend_data(industry_name, market)
    pe_percentile = _get_industry_pe_percentile(industry_name, market)

    cycle = classify_industry_cycle(trend, pe_percentile)

    # 生成对 confidence 的调整
    adjustment = 0.0
    if cycle.cycle == "recovery":
        adjustment = 0.05  # 复苏期: 轻微提升
    elif cycle.cycle == "boom":
        adjustment = -0.03  # 繁荣期: 轻微降低（可能已定价）
    elif cycle.cycle == "slowdown":
        adjustment = -0.05  # 衰退期: 降低
    elif cycle.cycle == "depression":
        adjustment = 0.02  # 萧条期: 轻微提升（可能过度悲观）

    return {
        "cycle": cycle.cycle,
        "phase_name": cycle.phase_name,
        "signal": cycle.signal,
        "implication": _generate_implication(cycle, industry_name),
        "confidence_adjustment": adjustment,
    }


def _generate_implication(cycle, industry_name: str) -> str:
    """生成行业景气度对标的的影响"""
    if cycle.cycle == "recovery":
        return f"{industry_name}行业处于复苏期，公司盈利有望改善，基本面分析可偏积极"
    elif cycle.cycle == "boom":
        return f"{industry_name}行业处于繁荣期，需警惕高估和周期拐点"
    elif cycle.cycle == "slowdown":
        return f"{industry_name}行业处于衰退期，公司盈利可能承压，基本面分析应偏保守"
    elif cycle.cycle == "depression":
        return f"{industry_name}行业处于萧条期，关注拐点信号，可能存在反转机会"
    else:
        return f"{industry_name}行业处于常态期，无特殊行业环境因素"
```

---

## 5. 预测区间概率化

### 5.1 核心思路

当前输出为固定区间 `{"min_pct": 2.0, "max_pct": 8.0}`，无法表达不确定性。

升级方案: 输出概率分布

```json
{
  "direction": "bullish",
  "magnitude": {"min_pct": 2.0, "max_pct": 8.0},
  "confidence": 0.72,
  "probability_distribution": {
    "strong_positive_>10%": 0.15,
    "positive_5_10%": 0.35,
    "moderate_positive_2_5%": 0.25,
    "flat_-2_2%": 0.15,
    "negative_-5_-2%": 0.08,
    "strong_negative_<-5%": 0.02
  }
}
```

### 5.2 实现方案

```python
def generate_probability_distribution(direction: str, confidence: float,
                                      magnitude: dict) -> dict:
    """
    基于方向和置信度生成概率分布。

    逻辑:
    - 高置信度 + 看涨 → 正收益概率高
    - 低置信度 + 中性 → 集中在 flat 区间
    """
    min_pct = magnitude.get("min_pct", -3.0)
    max_pct = magnitude.get("max_pct", 3.0)

    if direction == "bullish":
        if confidence > 0.7:
            return {
                "strong_positive_>10%": round(confidence * 0.2, 2),
                "positive_5_10%": round(confidence * 0.4, 2),
                "moderate_positive_2_5%": round(confidence * 0.3, 2),
                "flat_-2_2%": round((1 - confidence) * 0.6, 2),
                "negative_-5_-2%": round((1 - confidence) * 0.3, 2),
                "strong_negative_<-5%": round((1 - confidence) * 0.1, 2),
            }
        else:
            return {
                "strong_positive_>10%": round(confidence * 0.1, 2),
                "positive_5_10%": round(confidence * 0.25, 2),
                "moderate_positive_2_5%": round(confidence * 0.3, 2),
                "flat_-2_2%": round((1 - confidence) * 0.5, 2),
                "negative_-5_-2%": round((1 - confidence) * 0.35, 2),
                "strong_negative_<-5%": round((1 - confidence) * 0.15, 2),
            }
    elif direction == "bearish":
        # 对称处理
        pass
    else:
        # 中性: 集中在 flat
        return {
            "strong_positive_>10%": 0.05,
            "positive_5_10%": 0.10,
            "moderate_positive_2_5%": 0.20,
            "flat_-2_2%": round(confidence, 2),
            "negative_-5_-2%": 0.20,
            "strong_negative_<-5%": 0.05,
        }
```

---

## 6. 实施路线图

### Phase D: 深度优化（1 月+）🟢 P2

| 任务 | 文件 | 工作量 |
|------|------|--------|
| ① 历史案例 RAG | 新增 `fundamental_case_retriever.py` | 2 天 |
| ② 行业景气度整合 | `fundamental_analyst.py` 扩展 | 1 天 |
| ③ 预测区间概率化 | `fundamental_analyst.py` 输出格式升级 | 1 天 |
| ④ 评分卡权重实际优化 | 回测脚本 + 权重更新 | 1.5 天 |
| ⑤ 端到端测试 | `tests/` | 1 天 |

### Phase E: 长期优化（3 月+）🟢 P3

| 任务 | 说明 |
|------|------|
| 自由现金流估算 | 基于有限数据估算代理自由现金流 |
| 多模型 DeepDive | 用 Qwen/DeepSeek 分别做盈利预测，取共识 |
| 实时盈利预测整合 | 整合卖方一致预期数据 |
| 管理层/治理分析 | 从公告/新闻中提取治理变化信号 |
| ESG 因子 | 环境/社会/治理因子纳入评分 |

---

## 附录：完整文件清单

### 新增文件（Round 1-3）

| 文件 | 说明 | Round |
|------|------|-------|
| `src/data/valuation_history.py` | 历史估值分位计算 | R1 |
| `src/data/fundamental_preprocessor.py` | 预处理管线 | R1 |
| `src/data/hk_financial_source.py` | 港股财务数据 | R2 |
| `src/utils/fundamental_calibrator.py` | 置信度校准器 | R2 |
| `src/utils/scorecard_optimizer.py` | 评分卡权重优化 + 行业基准 | R2 |
| `src/utils/fundamental_case_retriever.py` | 历史案例检索 | R3 |
| `tests/test_fundamental_v2.py` | 38 个单测 | R1 |
| `tests/test_fundamental_v3.py` | 32 个单测 | R2 |

### 修改文件

| 文件 | 变更 | Round |
|------|------|-------|
| `src/data/fundamental_fetcher.py` | `fetch_enhanced()` + 港股补充 | R1+R2 |
| `src/agents/fundamental_analyst.py` | 两步 CoT + 校准 + 校验 | R1+R2 |
| `src/prompts/fundamental_prompts.py` | Few-shot + 矩阵 + 锚定 + 市场区分 | R1 |

---

> 📌 **核心原则**：Round 1-2 让 Agent "有数据、会推理、能自进化"，Round 3 让它"有记忆、懂环境、能量化不确定性"。好的基本面分析 = 数据 × 推理 × 自进化 × 环境感知 × 不确定性表达，五维能力的持续提升是永恒的方向。
