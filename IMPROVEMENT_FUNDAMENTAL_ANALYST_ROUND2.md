# 🏢 公司前景分析师改进方案 — Round 2

> **版本**: v2.0 | **日期**: 2026-07-03 | **前置**: IMPROVEMENT_FUNDAMENTAL_ANALYST_ROUND1.md（已完成 Phase A）

---

## 目录

1. [Round 1 实施回顾](#1-round-1-实施回顾)
2. [Round 2 改进总览](#2-round-2-改进总览)
3. [港股财务数据补全](#3-港股财务数据补全)
4. [Agent 自进化闭环](#4-agent-自进化闭环)
5. [评分卡权重优化](#5-评分卡权重优化)
6. [推理质量升级](#6-推理质量升级)
7. [性能与可靠性](#7-性能与可靠性)
8. [实施路线图](#8-实施路线图)

---

## 1. Round 1 实施回顾

### 1.1 已完成内容（Phase A）

| 模块 | 文件 | 核心成果 | 状态 |
|------|------|---------|------|
| 📊 历史估值分位 | `valuation_history.py` | 3年/5年 PE 分位计算，解读文字生成 | ✅ |
| 🔧 预处理管线 | `fundamental_preprocessor.py` | 趋势提取 + 评分卡 + 价值陷阱检测 + 质量评估 | ✅ |
| 📈 评分卡系统 | `fundamental_preprocessor.py` | 4 维度 100 分量化评分（盈利/成长/估值/健康） | ✅ |
| 🪤 价值陷阱检测 | `fundamental_preprocessor.py` | 低PE + ROE下滑 → 自动警告 | ✅ |
| 📝 Prompt 增强 | `fundamental_prompts.py` | 3 个 few-shot + 置信度锚定表 + 估值-质量矩阵 + 市场区分 | ✅ |
| 🧠 Agent 升级 | `fundamental_analyst.py` | 两步 CoT（评估→综合+反思）+ 置信度校准 + 一致性校验 | ✅ |
| 🔄 Fetcher 增强 | `fundamental_fetcher.py` | `fetch_enhanced()` 集成预处理管线 | ✅ |
| 🧪 测试 | `test_fundamental_v2.py` | 38 个单元测试（趋势/评分卡/陷阱/分位/质量/集成） | ✅ |

### 1.2 架构现状

```
┌─────────────────────────────────────────────────────────┐
│                  Round 1 实现后的架构                     │
│                                                         │
│  ┌──────────────────┐    ┌──────────────────┐          │
│  │ 腾讯/新浪/akshare │    │ akshare PE历史    │          │
│  │ (实时PE/财务)     │    │ (分位计算)        │          │
│  └────────┬─────────┘    └────────┬─────────┘          │
│           │        并发采集       │                     │
│           └──────────┬───────────┘                     │
│                      ▼                                 │
│  ┌──────────────────────────────────────┐              │
│  │  FundamentalFetcher.fetch_enhanced() │              │
│  │  → 原始数据 + PE历史                 │              │
│  └────────────────┬─────────────────────┘              │
│                   ▼                                    │
│  ┌──────────────────────────────────────┐              │
│  │  to_agent_dict_enhanced()            │              │
│  │  ① 趋势提取 (judge_trend)            │              │
│  │  ② 估值分位 (percentile)             │              │
│  │  ③ 质量评分卡 (scorecard)            │              │
│  │  ④ 数据质量评估 (quality)            │              │
│  │  ⑤ 价值陷阱检测 (value_trap)         │              │
│  └────────────────┬─────────────────────┘              │
│                   ▼                                    │
│  ┌──────────────────────────────────────┐              │
│  │  FundamentalAnalyst.analyze()        │              │
│  │  Step A: 综合评估 (LLM call 1)       │              │
│  │  Step B: 综合判断+反思 (LLM call 2)  │              │
│  │  → _calibrate_confidence()           │              │
│  │  → _validate_consistency()           │              │
│  └────────────────┬─────────────────────┘              │
│                   ▼                                    │
│           AnalysisResult (方向+幅度+置信度)              │
└─────────────────────────────────────────────────────────┘
```

### 1.3 遗留问题（Round 2 解决）

| # | 问题 | 影响 | 优先级 |
|---|------|------|--------|
| 1 | 港股财务数据仍为 0 | 港股分析能力无实质提升 | 🔴 P0 |
| 2 | 评分卡权重固定（30/25/25/20） | 实际预测力未验证 | 🟡 P1 |
| 3 | 无自进化闭环 | confidence 不能随历史数据自动校准 | 🟡 P1 |
| 4 | 评分卡未按行业差异化 | 银行和半导体用同一把尺子 | 🟢 P2 |
| 5 | 无历史案例 RAG | 纯靠 LLM 知识库 | 🟢 P2 |

---

## 2. Round 2 改进总览

### 2.1 目标

Round 1 完成了"基础设施"（预处理管线、评分卡、两步推理）。Round 2 聚焦于：

1. **数据补全**：从 0 到 1 解决港股财务数据问题
2. **自进化**：让 Agent 的置信度随历史验证数据自动校准
3. **评分卡调优**：基于回测自动优化维度权重
4. **行业差异化**：不同行业使用不同的评分标准

### 2.2 改进维度一览

| 维度 | 改进项 | 优先级 | 预期收益 |
|------|--------|--------|---------|
| 📡 数据源 | 港股财务数据（AASTOCKS/东方财富F10） | 🔴 P0 | 港股分析从"只看PE"升级为完整基本面分析 |
| 📡 数据源 | 同业对标数据动态获取 | 🟡 P1 | 相对排名量化 |
| 🧬 自进化 | 按数据完整度的准确率分桶统计 | 🟡 P1 | 高数据质量时自动提升 ceiling |
| 🧬 自进化 | 置信度校准曲线（isotonic regression） | 🟡 P1 | confidence 从"感觉"变成"统计" |
| 🔬 质量 | 评分卡按行业差异化 | 🟢 P2 | 银行/SaaS/半导体的合理指标不同 |
| 🔬 质量 | 评分卡权重回测优化 | 🟡 P1 | 最优权重自动搜索 |
| 🧠 架构 | 历史案例 RAG | 🟢 P2 | 利用历史规律辅助判断 |
| 🧠 架构 | 行业景气度判断 | 🟢 P2 | 将宏观/行业数据引入基本面 |

---

## 3. 港股财务数据补全

### 3.1 方案选择

| 方案 | 数据源 | 复杂度 | 数据完整度 | 推荐 |
|------|--------|--------|-----------|------|
| A | AASTOCKS 爬取 | 中 | 高（营收/利润/ROE/现金流） | ✅ 首选 |
| B | 东方财富港股F10 API | 低 | 中（akshare接口） | ⚠️ 需测试网络 |
| C | Alpha Vantage | 低 | 中（免费25次/天） | 备选 |
| D | yfinance 增强 | 低 | 低-中 | 已有 |

### 3.2 方案 A 实现：AASTOCKS 爬取

```python
# src/data/hk_financial_source.py

"""
港股财务数据获取器

从 AASTOCKS 爬取港股财务指标。
"""

import logging
import requests
from bs4 import BeautifulSoup
from typing import Optional

logger = logging.getLogger(__name__)


async def fetch_hk_financials_aastocks(symbol: str) -> dict:
    """
    从 AASTOCKS 获取港股财务数据。

    URL 示例:
    - 损益表: https://www.aastocks.com/sc/stocks/analysis/company-fundamental/profit-loss?symbol=00700
    - 财务比率: https://www.aastocks.com/sc/stocks/analysis/company-fundamental/financial-ratios?symbol=00700
    - 现金流: https://www.aastocks.com/sc/stocks/analysis/company-fundamental/cash-flow?symbol=00700

    Args:
        symbol: 港股代码（不含HK后缀，如 "0700"）

    Returns:
        {
            "revenue_series": [2023年度营收, 2022年度营收, ...],
            "profit_series": [...],
            "roe": 最新年度ROE,
            "gross_margin": ...,
            "net_margin": ...,
            "data_source": "aastocks"
        }
    """
    code = symbol.zfill(5)
    result = {"data_source": "none"}

    try:
        # 获取财务比率页
        url = f"https://www.aastocks.com/sc/stocks/analysis/company-fundamental/financial-ratios?symbol={code}"
        resp = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        })
        resp.encoding = "utf-8"

        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            # 解析表格（AASTOCKS 使用 HTML table）
            tables = soup.find_all("table")
            for table in tables:
                rows = table.find_all("tr")
                for row in rows:
                    cells = row.find_all(["td", "th"])
                    if len(cells) >= 2:
                        label = cells[0].get_text(strip=True)
                        # 匹配关键指标
                        if "股东权益" in label or "ROE" in label:
                            # 提取最新一期 ROE
                            pass
                        elif "毛利率" in label:
                            pass
                        elif "纯利率" in label or "净利率" in label:
                            pass

            result["data_source"] = "aastocks"
            logger.info(f"AASTOCKS 财务数据获取成功: {code}")

    except requests.exceptions.Timeout:
        logger.warning(f"AASTOCKS 超时: {code}")
        result["data_source"] = "none"
    except Exception as e:
        logger.debug(f"AASTOCKS 获取失败 ({code}): {e}")
        result["data_source"] = "none"

    return result
```

### 3.3 方案 B 备选：东方财富港股F10

```python
async def fetch_hk_financials_eastmoney(symbol: str) -> dict:
    """
    东方财富港股 F10 接口（akshare）
    注：此前因网络限制不可用，当前环境需重试。
    """
    try:
        import akshare as ak
        df = ak.stock_hk_financial_analysis_indicator(
            symbol=symbol, indicator="按报告期"
        )
        if df is not None and not df.empty:
            latest = df.iloc[-1]
            return {
                "revenue_series": df.get("营业总收入", []).tolist() if "营业总收入" in df.columns else [],
                "net_profit": latest.get("净利润"),
                "roe": latest.get("净资产收益率"),
                "data_source": "eastmoney_hk",
            }
    except Exception as e:
        logger.debug(f"东方财富港股F10 失败: {e}")
    return {"data_source": "none"}


async def fetch_hk_financials_alphavantage(symbol: str) -> dict:
    """
    Alpha Vantage INCOME_STATEMENT / BALANCE_SHEET API
    免费 25次/天，需注册 API key
    """
    import os
    api_key = os.environ.get("ALPHA_VANTAGE_API_KEY")
    if not api_key:
        return {"data_source": "none"}

    try:
        # 损益表
        url = f"https://www.alphavantage.co/query?function=INCOME_STATEMENT&symbol={symbol}.HK&apikey={api_key}"
        resp = requests.get(url, timeout=15)
        data = resp.json()
        if "annualReports" in data:
            reports = data["annualReports"]
            return {
                "revenue_series": [float(r.get("totalRevenue", 0)) for r in reports[:5]],
                "net_profit": float(reports[0].get("netIncome", 0)) if reports else None,
                "data_source": "alphavantage",
            }
    except Exception as e:
        logger.debug(f"Alpha Vantage 港股 失败: {e}")
    return {"data_source": "none"}
```

### 3.4 集成到 FundamentalFetcher

```python
# 在 FundamentalFetcher._fetch_hk_tencent 中补充
async def _fetch_hk_financials_supplement(self, result: FundamentalData, symbol: str):
    """补充港股财务数据（AASTOCKS/东方财富/Alpha Vantage 降级链）"""
    from src.data.hk_financial_source import (
        fetch_hk_financials_aastocks,
        fetch_hk_financials_eastmoney,
    )

    # 方案 A: AASTOCKS
    try:
        aastocks_data = await fetch_hk_financials_aastocks(symbol)
        if aastocks_data.get("data_source") == "aastocks":
            result.latest_revenue = aastocks_data.get("revenue")
            result.latest_net_profit = aastocks_data.get("net_profit")
            result.roe = aastocks_data.get("roe")
            result.gross_margin = aastocks_data.get("gross_margin")
            result.net_margin = aastocks_data.get("net_margin")
            logger.info(f"港股财务补充(AASTOCKS): {result.company_name}")
            return
    except Exception:
        pass

    # 方案 B: 东方财富
    try:
        em_data = await fetch_hk_financials_eastmoney(symbol)
        if em_data.get("data_source") == "eastmoney_hk":
            result.latest_revenue = em_data.get("revenue")
            result.roe = em_data.get("roe")
            logger.info(f"港股财务补充(东方财富): {result.company_name}")
            return
    except Exception:
        pass

    logger.warning(f"港股财务数据全部获取失败: {symbol}")
```

---

## 4. Agent 自进化闭环

### 4.1 核心思路

Round 1 实现了"工具层面"的改进（数据→预处理→推理）。Round 2 实现"系统层面"的改进：让 Agent 的决策质量随验证数据积累而持续提升。

```
预测时刻 → 验证时刻 → 反馈时刻 → 影响下次预测
```

### 4.2 置信度校准曲线

```python
# src/utils/fundamental_calibrator.py

"""
基本面分析师专用置信度校准器

基于历史验证数据，构建"预测置信度 → 实际准确率"的映射。
使用 isotonic regression 做非参数校准。
"""

import logging
from typing import Optional
import math

logger = logging.getLogger(__name__)


class FundamentalConfidenceCalibrator:
    """基本面分析师置信度校准器"""

    def __init__(self, prediction_store=None):
        self.store = prediction_store
        # 置信度桶: 统计每个区间的实际准确率
        self._calibration_bins = {
            "0.0-0.2": {"total": 0, "correct": 0},
            "0.2-0.4": {"total": 0, "correct": 0},
            "0.4-0.6": {"total": 0, "correct": 0},
            "0.6-0.8": {"total": 0, "correct": 0},
            "0.8-1.0": {"total": 0, "correct": 0},
        }

    def calibrate(self, raw_confidence: float, data_quality_bucket: str = "medium") -> float:
        """
        校准原始置信度。

        Args:
            raw_confidence: LLM 原始判断的置信度
            data_quality_bucket: 数据质量分桶（high/medium/low）

        Returns:
            校准后的置信度
        """
        # 基础校准：向该桶的历史准确率回归
        bin_key = self._get_bin_key(raw_confidence)
        bin_stats = self._calibration_bins.get(bin_key)

        if bin_stats and bin_stats["total"] >= 10:
            historical_acc = bin_stats["correct"] / bin_stats["total"]
            # 贝叶斯收缩: 70% 原始判断 + 30% 历史准确率
            calibrated = raw_confidence * 0.7 + historical_acc * 0.3
            logger.debug(
                f"置信度校准: {raw_confidence:.2f} → {calibrated:.2f} "
                f"(历史准确率={historical_acc:.2f}, 样本={bin_stats['total']})"
            )
            return min(max(calibrated, 0.05), 0.95)

        # 样本不足时：仅做基础调整
        return raw_confidence

    def update_from_validation(self, predicted_conf: float, was_correct: bool):
        """从验证结果中学习"""
        bin_key = self._get_bin_key(predicted_conf)
        if bin_key in self._calibration_bins:
            self._calibration_bins[bin_key]["total"] += 1
            if was_correct:
                self._calibration_bins[bin_key]["correct"] += 1

    def _get_bin_key(self, confidence: float) -> str:
        if confidence < 0.2: return "0.0-0.2"
        elif confidence < 0.4: return "0.2-0.4"
        elif confidence < 0.6: return "0.4-0.6"
        elif confidence < 0.8: return "0.6-0.8"
        else: return "0.8-1.0"

    def get_calibration_stats(self) -> dict:
        """获取校准统计"""
        stats = {}
        for bin_key, bin_data in self._calibration_bins.items():
            if bin_data["total"] > 0:
                stats[bin_key] = {
                    "total": bin_data["total"],
                    "accuracy": round(bin_data["correct"] / bin_data["total"], 3),
                }
        return stats
```

### 4.3 按数据完整度的准确率分桶

```python
# 扩展 PredictionStore 的 schema
"""
新增表: fundamental_accuracy_detail

CREATE TABLE IF NOT EXISTS fundamental_accuracy_detail (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    target TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    direction TEXT NOT NULL,
    magnitude_min_pct REAL,
    magnitude_max_pct REAL,
    confidence REAL NOT NULL,
    data_completeness REAL,        -- 数据完整度评分
    data_quality_bucket TEXT,       -- high/medium/low
    scorecard_total REAL,           -- 评分卡总分
    scorecard_rating TEXT,          -- 评分卡等级
    pe_percentile REAL,             -- PE分位
    value_trap_flag INTEGER,        -- 是否触发价值陷阱警告
    was_correct INTEGER,            -- 事后验证：方向是否正确
    actual_change_pct REAL,         -- 实际涨跌幅
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

class FundamentalAccuracyTracker:
    """按数据完整度分桶追踪准确率"""

    def record_prediction(self, result: AnalysisResult, data: dict):
        """记录预测详情"""
        quality = data.get("data_quality", {})
        scorecard = data.get("quality_scorecard", {})
        va = data.get("valuation_analysis", {})

        completeness = quality.get("overall_quality", 0.5)
        bucket = "high" if completeness > 0.7 else "medium" if completeness > 0.4 else "low"

        # 存入数据库...
        pass

    def get_accuracy_by_bucket(self) -> dict:
        """
        Returns:
            {
                "high": {"total": 50, "direction_accuracy": 0.62},
                "medium": {"total": 30, "direction_accuracy": 0.53},
                "low": {"total": 20, "direction_accuracy": 0.45},
            }
        """
        pass

    def get_accuracy_by_scorecard_rating(self) -> dict:
        """
        按评分卡等级看准确率:
        - excellent 的预测准确率是否更高?
        - weak 的预测准确率是否更低?
        """
        pass
```

### 4.4 估值分位预测力回测

```python
class ValuationPercentileBacktester:
    """回测估值分位的预测力"""

    def backtest(self, symbol: str, market: str, lookback_months: int = 12):
        """
        回测:

        历史上当 PE 处于 <20% 分位时:
        - 未来 1 周正收益比例?
        - 未来 1 月正收益比例?
        - 平均收益率?

        这些数据用于:
        1. 校准"估值极端时 confidence 应该更高"
        2. 在 prompt 中注入统计依据
        3. 评分卡中"估值"维度的权重调整
        """
        pass
```

### 4.5 评分卡权重优化

```python
class ScorecardWeightOptimizer:
    """
    通过回测数据优化评分卡的 4 个维度权重。

    当前固定权重: profitability=30, growth=25, valuation=25, health=20
    优化目标: 找到使"评分卡总分"与"未来收益"相关性最高的权重组合
    """

    def optimize(self, backtest_data: list[dict]) -> dict:
        """
        Args:
            backtest_data: [{"scorecard": {...}, "future_return_pct": float}, ...]

        Returns:
            最优权重: {"profitability": 0.35, "growth": 0.20, "valuation": 0.30, "health": 0.15}
        """
        best_weights = {"profitability": 0.30, "growth": 0.25, "valuation": 0.25, "health": 0.20}
        best_correlation = 0

        # 网格搜索权重组合
        for p_w in range(20, 45, 5):
            for g_w in range(15, 40, 5):
                for v_w in range(15, 40, 5):
                    h_w = 100 - p_w - g_w - v_w
                    if h_w < 5 or h_w > 30:
                        continue

                    # 计算该权重下的加权分与未来收益的相关系数
                    correlation = self._calculate_correlation(
                        backtest_data, p_w / 100, g_w / 100, v_w / 100, h_w / 100
                    )
                    if correlation > best_correlation:
                        best_correlation = correlation
                        best_weights = {
                            "profitability": p_w / 100,
                            "growth": g_w / 100,
                            "valuation": v_w / 100,
                            "health": h_w / 100,
                        }

        logger.info(f"评分卡最优权重: {best_weights} (相关系数={best_correlation:.3f})")
        return best_weights

    def _calculate_correlation(self, data, p_w, g_w, v_w, h_w) -> float:
        """计算加权分与未来收益的 Pearson 相关系数"""
        scores = []
        returns = []
        for item in data:
            sc = item.get("scorecard", {}).get("breakdown", {})
            score = (
                sc.get("profitability", {}).get("score", 0) * p_w +
                sc.get("growth", {}).get("score", 0) * g_w +
                sc.get("valuation", {}).get("score", 0) * v_w +
                sc.get("health", {}).get("score", 0) * h_w
            )
            scores.append(score)
            returns.append(item.get("future_return_pct", 0))

        if len(scores) < 5:
            return 0

        # Pearson correlation
        n = len(scores)
        mean_s = sum(scores) / n
        mean_r = sum(returns) / n
        cov = sum((s - mean_s) * (r - mean_r) for s, r in zip(scores, returns)) / n
        std_s = (sum((s - mean_s) ** 2 for s in scores) / n) ** 0.5
        std_r = (sum((r - mean_r) ** 2 for r in returns) / n) ** 0.5

        if std_s == 0 or std_r == 0:
            return 0
        return cov / (std_s * std_r)
```

---

## 5. 评分卡按行业差异化

### 5.1 问题

当前评分卡对所有行业使用同一标准：
- ROE > 15% → "优秀"（但银行 ROE 10% 就算好，半导体 ROE 20% 可能只是平均）
- 净利率 > 20% → "优秀"（但电商净利率 5% 就算好）

### 5.2 行业差异化评分

```python
# 在 fundamental_preprocessor.py 中扩展

INDUSTRY_BENCHMARKS = {
    "银行": {"good_roe": 10, "good_margin": 30, "good_growth": 5},
    "白酒": {"good_roe": 20, "good_margin": 40, "good_growth": 15},
    "证券": {"good_roe": 7, "good_margin": 25, "good_growth": 10},
    "保险": {"good_roe": 12, "good_margin": 15, "good_growth": 10},
    "医药": {"good_roe": 15, "good_margin": 20, "good_growth": 15},
    "新能源": {"good_roe": 12, "good_margin": 15, "good_growth": 25},
    "家电": {"good_roe": 18, "good_margin": 12, "good_growth": 10},
    "电子": {"good_roe": 12, "good_margin": 10, "good_growth": 20},
    "半导体": {"good_roe": 15, "good_margin": 20, "good_growth": 25},
    "互联网": {"good_roe": 15, "good_margin": 20, "good_growth": 20},
    "计算机": {"good_roe": 12, "good_margin": 15, "good_growth": 20},
    "房地产": {"good_roe": 8, "good_margin": 15, "good_growth": 5},
    "电力": {"good_roe": 10, "good_margin": 20, "good_growth": 5},
    "汽车": {"good_roe": 10, "good_margin": 8, "good_growth": 15},
    "食品饮料": {"good_roe": 18, "good_margin": 20, "good_growth": 12},
    "军工": {"good_roe": 8, "good_margin": 10, "good_growth": 20},
    "传媒": {"good_roe": 8, "good_margin": 15, "good_growth": 15},
    "通信": {"good_roe": 10, "good_margin": 12, "good_growth": 15},
    "化工": {"good_roe": 12, "good_margin": 10, "good_growth": 10},
    "钢铁": {"good_roe": 6, "good_margin": 5, "good_growth": 5},
    "煤炭": {"good_roe": 12, "good_margin": 20, "good_growth": 5},
    "有色金属": {"good_roe": 10, "good_margin": 10, "good_growth": 10},
    "水泥": {"good_roe": 10, "good_margin": 15, "good_growth": 5},
}


def generate_quality_scorecard_industry_aware(
    financials: dict,
    valuation: dict,
    pe_percentile: float = None,
    industry: str = None,
) -> "QualityScorecard":
    """行业差异化的质量评分卡"""
    from src.data.fundamental_preprocessor import generate_quality_scorecard

    # 获取行业基准
    benchmark = INDUSTRY_BENCHMARKS.get(industry, {})

    if not benchmark:
        # 无行业基准时使用默认
        return generate_quality_scorecard(financials, valuation, pe_percentile)

    # 使用行业基准调整评分
    # ... (实现略，核心逻辑: 用行业基准替代固定阈值)
    pass
```

---

## 6. 推理质量升级

### 6.1 历史案例 RAG

```python
class FundamentalCaseRetriever:
    """基本面分析历史案例检索"""

    def retrieve_similar_cases(self, data: dict, top_k: int = 3) -> list[dict]:
        """
        检索与当前标的最相似的历史案例。

        相似度维度:
        - 同行业
        - 相似估值分位
        - 相似评分卡总分
        - 相似趋势方向

        Returns:
            [
                {
                    "target": "600519",
                    "date": "2025-06-15",
                    "scorecard_total": 78,
                    "pe_percentile": 0.25,
                    "prediction": "bullish",
                    "actual_outcome": "+8.2%",
                    "was_correct": true,
                    "key_lesson": "低分位+好公司在6个月内修复"
                },
                ...
            ]
        """
        pass
```

### 6.2 行业景气度判断

```python
def assess_industry_cycle_for_fundamental(industry: str, market: str) -> dict:
    """
    评估行业景气度，为基本面分析提供"行业环境"上下文。

    数据来源:
    - 行业近期涨跌幅（来自 industry_fetcher）
    - 行业估值分位
    - 行业资金流向（可选）

    Returns:
        {
            "cycle": "recovery/boom/slowdown/depression",
            "industry_momentum": "positive/negative/neutral",
            "implication_for_stock": "行业景气向上，公司盈利有支撑"
        }
    """
    pass
```

---

## 7. 性能与可靠性

### 7.1 数据缓存策略

```python
class FundamentalDataCache:
    """
    基本面数据缓存。

    缓存策略:
    - PE/PB 实时数据: 1 小时有效
    - 财务数据（季度）: 24 小时有效
    - PE 历史序列: 7 天有效
    - 评分卡结果: 与财务数据同缓存
    """

    def __init__(self):
        self._cache = {}
        self._ttl = {}

    def get(self, key: str) -> Optional[dict]:
        if key in self._cache:
            if time.time() < self._ttl.get(key, 0):
                return self._cache[key]
            else:
                del self._cache[key]
        return None

    def put(self, key: str, data: dict, ttl_seconds: int = 3600):
        self._cache[key] = data
        self._ttl[key] = time.time() + ttl_seconds
```

### 7.2 失败案例分析自动化

```python
class FundamentalFailureAnalyzer:
    """自动分析基本面预测失败的原因"""

    def analyze(self, prediction_id: str):
        """
        对于方向判断错误的预测，分析:

        1. 数据质量问题?
           → 数据完整度 < 50% 但 confidence > 0.6 → 标记为"过度自信"
        2. 价值陷阱误判?
           → 低PE + 看涨 + 实际下跌 → 标记为"价值陷阱漏检"
        3. 行业周期误判?
           → 行业在衰退但判断为看涨 → 标记为"周期误判"
        4. 黑天鹅事件?
           → 无法归因 → 标记为"正常失败"

        输出: 失败类型 + 改进建议
        """
        pass
```

---

## 8. 实施路线图

### Phase B: 港股数据 + 自进化（2 周）🟡 P1

| 任务 | 文件 | 工作量 |
|------|------|--------|
| ① 港股财务数据（AASTOCKS） | 新增 `hk_financial_source.py` | 2 天 |
| ② 集成到 Fetcher | `fundamental_fetcher.py` | 0.5 天 |
| ③ 置信度校准器 | 新增 `fundamental_calibrator.py` | 1.5 天 |
| ④ 按数据完整度的准确率追踪 | `prediction_store.py` 扩展 | 1 天 |
| ⑤ 端到端测试 | `tests/` | 1 天 |

**预期效果**：
- 港股财务数据从 0 到 1
- 置信度随历史数据自动校准
- 高数据完整度时的准确率可量化

### Phase C: 评分卡优化 + 行业差异化（2 周）🟢 P2

| 任务 | 文件 | 工作量 |
|------|------|--------|
| ① 评分卡权重回测优化 | 新增 `scorecard_optimizer.py` | 2 天 |
| ② 行业差异化评分 | `fundamental_preprocessor.py` 扩展 | 1.5 天 |
| ③ 估值分位预测力回测 | 回测脚本增强 | 1 天 |
| ④ 历史案例 RAG | 新增 `fundamental_case_retriever.py` | 1.5 天 |
| ⑤ 行业景气度判断 | 复用 industry_fetcher 数据 | 1 天 |

### Phase D: 深度优化（1 月+）🟢 P3

| 任务 | 说明 |
|------|------|
| 自由现金流估算 | 基于有限数据估算代理自由现金流 |
| 多模型 DeepDive | 用 Qwen/DeepSeek 分别做盈利预测，取共识 |
| 实时盈利预测整合 | 整合卖方一致预期数据 |
| 行业景气度指标 | 将宏观/行业数据引入基本面判断 |
| 预测区间概率化 | 输出概率分布而非单点区间 |

---

## 附录：效果度量

### 关键指标

| 指标 | Round 1 后 | Phase B 目标 | Phase C 目标 | 度量方式 |
|------|-----------|-------------|-------------|---------|
| 方向准确率（A股） | ~55% | ≥60% | ≥65% | PredictionStore |
| 方向准确率（港股） | ~40% | ≥50% | ≥58% | PredictionStore |
| 置信度校准误差 | 未知 | ≤0.15 | ≤0.10 | \|confidence - actual_acc\| |
| 数据可用率（港股） | ~20% | ≥50% | ≥75% | AASTOCKS 补充后 |
| 价值陷阱识别率 | ≥30% | ≥45% | ≥60% | 低PE+下跌case |
| 评分卡预测力 | 未量化 | 相关系数>0.3 | 相关系数>0.5 | 回测分析 |

### 实验设计

```bash
# 同标的、同时间区间，新老版本对比
python scripts/run_backtest.py -t 000001 --start 2026-01-01 --end 2026-06-30 --agent fundamental
python scripts/run_backtest.py -t 0700 --start 2026-01-01 --end 2026-06-30 --agent fundamental

# 重点观察：
# 1. 数据完整度高的 A 股 → 准确率提升幅度
# 2. 数据缺失的港股 → AASTOCKS 补充后的提升
# 3. "低分位+好公司" → 正收益的概率（估值分位预测力）
# 4. 评分卡总分与未来收益的相关性
```

---

## 附录 A：文件变更清单

### Round 1 新增文件

| 文件 | 说明 |
|------|------|
| `src/data/valuation_history.py` | 历史估值分位计算 |
| `src/data/fundamental_preprocessor.py` | 预处理管线（趋势/评分卡/陷阱/质量） |
| `tests/test_fundamental_v2.py` | 38 个单元测试 |

### Round 1 修改文件

| 文件 | 变更 |
|------|------|
| `src/data/fundamental_fetcher.py` | 新增 `fetch_enhanced()` 和 `to_agent_dict_enhanced()` |
| `src/agents/fundamental_analyst.py` | 两步 CoT + 校准 + 校验 |
| `src/prompts/fundamental_prompts.py` | Few-shot + 矩阵 + 锚定 + 市场区分 |

### Round 2 计划新增文件

| 文件 | 说明 |
|------|------|
| `src/data/hk_financial_source.py` | 港股财务数据采集 |
| `src/utils/fundamental_calibrator.py` | 置信度校准器 |
| `src/utils/scorecard_optimizer.py` | 评分卡权重优化 |
| `src/utils/fundamental_case_retriever.py` | 历史案例检索 |

---

> 📌 **核心原则**：Round 1 让 Agent "有能力做更好的分析"，Round 2 让 Agent "从历史中学习如何做得更好"。好的基本面分析 = 好的数据 + 好的推理 + 好的自我修正，三者缺一不可。
