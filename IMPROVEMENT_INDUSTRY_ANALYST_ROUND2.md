# 🏭 行业对比分析师改进方案 — Round 2

> **版本**: v2.0 | **日期**: 2026-07-03 | **前置**: IMPROVEMENT_INDUSTRY_ANALYST_ROUND1.md（已完成 Phase A）

---

## 目录

1. [Round 1 实施回顾](#1-round-1-实施回顾)
2. [Round 2 改进总览](#2-round-2-改进总览)
3. [行业数据深化](#3-行业数据深化)
4. [Agent 自进化闭环](#4-agent-自进化闭环)
5. [行业轮动检测](#5-行业轮动检测)
6. [推理质量升级](#6-推理质量升级)
7. [实施路线图](#7-实施路线图)

---

## 1. Round 1 实施回顾

### 1.1 已完成内容（Phase A）

| 模块 | 文件 | 核心成果 | 状态 |
|------|------|---------|------|
| 📊 行业板块数据 | `industry_fetcher.py` | 东方财富行业板块 API 替代硬编码常量 | ✅ |
| 🔧 预处理管线 | `industry_preprocessor.py` | 排名分位 + 周期判断 + 性价比评分 + 质量评估 | ✅ |
| 🏷️ 行业分类扩展 | `industry_preprocessor.py` | 100+ 已知映射 + 名称推断 + 分类缓存 | ✅ |
| 🇭🇰 港股行业映射 | `industry_preprocessor.py` | 20+ 港股行业映射表 | ✅ |
| 📈 参考值缓存 | `industry_preprocessor.py` | IndustryReferenceCache（7天有效期） | ✅ |
| 📝 Prompt 增强 | `industry_prompts.py` | 3 个 few-shot + 置信度锚定 + 行业类型区分 | ✅ |
| 🧠 Agent 升级 | `industry_analyst.py` | 两步 CoT（定位+判断 → 综合+反思）+ 校准 + 校验 | ✅ |
| 🧪 测试 | `test_industry_v2.py` | 39 个单元测试（排名/周期/性价比/分类/集成） | ✅ |

### 1.2 架构现状

```
┌─────────────────────────────────────────────────────────┐
│                  Round 1 实现后的架构                     │
│                                                         │
│  ┌──────────────────┐    ┌──────────────────┐          │
│  │ 东方财富行业板块  │    │ 腾讯行情(港股)    │          │
│  │ (成分股+行情)     │    │ (PE/市值)        │          │
│  └────────┬─────────┘    └────────┬─────────┘          │
│           │        并发采集       │                     │
│           └──────────┬───────────┘                     │
│                      ▼                                 │
│  ┌──────────────────────────────────────┐              │
│  │  IndustryFetcher.fetch_enhanced()    │              │
│  │  → 标的数据 + 行业成分股 + 行业趋势   │              │
│  └────────────────┬─────────────────────┘              │
│                   ▼                                    │
│  ┌──────────────────────────────────────┐              │
│  │  process_industry_data()             │              │
│  │  ① 行业平均估值计算                  │              │
│  │  ② 标的行业排名分位                  │              │
│  │  ③ 行业周期判断                      │              │
│  │  ④ 性价比综合评分                    │              │
│  │  ⑤ 数据质量评估                      │              │
│  └────────────────┬─────────────────────┘              │
│                   ▼                                    │
│  ┌──────────────────────────────────────┐              │
│  │  IndustryAnalyst.analyze()           │              │
│  │  Step A: 定位+判断 (LLM call 1)      │              │
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
| 1 | 行业参考值缓存未自动刷新 | 缓存数据可能过时 | 🟡 P1 |
| 2 | 无自进化闭环 | confidence 不能随历史数据自动校准 | 🟡 P1 |
| 3 | 无行业轮动检测 | 错过风格切换信号 | 🟡 P1 |
| 4 | 港股行业数据仍依赖硬映射 | 覆盖率有限 | 🟡 P1 |
| 5 | 无按行业的准确率分桶 | 不知道哪个行业判断更准 | 🟢 P2 |
| 6 | 无产业链上下游分析 | 景气度传导链缺失 | 🟢 P2 |

---

## 2. Round 2 改进总览

### 2.1 目标

Round 1 完成了"从硬编码到实时数据"的跨越。Round 2 聚焦于：

1. **数据深化**：行业参考值自动刷新、港股行业数据扩展
2. **自进化**：按行业追踪准确率、置信度校准
3. **行业轮动**：检测风格切换信号
4. **推理升级**：产业链分析、催化剂日历

### 2.2 改进维度一览

| 维度 | 改进项 | 优先级 | 预期收益 |
|------|--------|--------|---------|
| 📡 数据源 | 行业参考值自动刷新（cron/手动） | 🟡 P1 | 行业均值始终新鲜 |
| 📡 数据源 | 港股行业数据扩展（yfinance sector） | 🟡 P1 | 港股覆盖率提升 |
| 🧬 自进化 | 按行业的准确率追踪 | 🟡 P1 | 知道哪个行业判断更准 |
| 🧬 自进化 | 置信度校准曲线 | 🟡 P1 | confidence 更诚实 |
| 🔬 质量 | 行业轮动检测 | 🟡 P1 | 捕捉风格切换信号 |
| 🔬 质量 | 产业链上下游分析 | 🟢 P2 | 景气度传导判断 |
| 🧠 架构 | 催化剂日历 | 🟢 P2 | 事件驱动判断 |
| 🧠 架构 | 国际对标 | 🟢 P3 | 全球视角估值 |

---

## 3. 行业数据深化

### 3.1 行业参考值自动刷新

```python
# src/utils/industry_refresher.py

"""
行业参考值定期刷新器

定期从东方财富行业板块获取所有行业实时估值数据，
更新缓存文件，替代可能过时的硬编码常量。
"""

import logging
import json
import os
from datetime import datetime

logger = logging.getLogger(__name__)


class IndustryReferenceRefresher:
    """行业参考值刷新器"""

    CACHE_FILE = "config/industry_reference_cache.json"

    async def refresh(self) -> dict:
        """
        从东方财富行业板块获取所有行业实时估值。

        Returns:
            所有行业的参考值字典
        """
        try:
            import akshare as ak

            # 获取所有行业板块名称
            df_names = ak.stock_board_industry_name_em()
            if df_names is None or df_names.empty:
                logger.warning("获取行业板块列表失败")
                return {}

            industry_names = df_names["板块名称"].tolist()
            reference = {}

            for ind_name in industry_names:
                try:
                    df = ak.stock_board_industry_cons_em(symbol=ind_name)
                    if df is not None and not df.empty:
                        # 提取PE/PB
                        pe_values = []
                        pb_values = []
                        for _, row in df.iterrows():
                            pe = self._safe_float(row.get("市盈率-动态"))
                            pb = self._safe_float(row.get("市净率"))
                            if pe and pe > 0:
                                pe_values.append(pe)
                            if pb and pb > 0:
                                pb_values.append(pb)

                        if pe_values:
                            reference[ind_name] = {
                                "pe": round(sum(pe_values) / len(pe_values), 2),
                                "pe_median": round(
                                    sorted(pe_values)[len(pe_values) // 2], 2
                                ),
                                "pb": round(
                                    sum(pb_values) / len(pb_values), 2
                                ) if pb_values else None,
                                "stock_count": len(pe_values),
                            }
                except Exception as e:
                    logger.debug(f"刷新行业 {ind_name} 失败: {e}")
                    continue

            # 保存缓存
            cache_data = {
                "updated_at": datetime.now().isoformat(),
                "data": reference,
            }
            cache_dir = os.path.dirname(self.CACHE_FILE)
            if cache_dir and not os.path.exists(cache_dir):
                os.makedirs(cache_dir, exist_ok=True)
            with open(self.CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)

            logger.info(f"行业参考值刷新完成: {len(reference)} 个行业")
            return reference

        except Exception as e:
            logger.error(f"行业参考值刷新异常: {e}")
            return {}

    @staticmethod
    def _safe_float(value) -> Optional[float]:
        if value is None:
            return None
        try:
            v = float(value)
            return v if v == v else None
        except (ValueError, TypeError):
            return None
```

### 3.2 港股行业数据扩展

```python
async def fetch_hk_industry_from_yfinance(symbol: str) -> Optional[dict]:
    """
    从 yfinance 获取港股行业分类。

    yfinance info 中的 sector/industry 字段提供
    全球统一的行业分类（英文）。

    Returns:
        {"sector": "Technology", "industry": "Internet Content & Information"}
    """
    try:
        import yfinance as yf

        ticker = yf.Ticker(f"{symbol.zfill(5)}.HK")
        info = ticker.info

        if info:
            sector = info.get("sector", "")
            industry = info.get("industry", "")

            # 映射到中文行业
            sector_mapping = {
                "Technology": "科技",
                "Financial Services": "金融",
                "Healthcare": "医药",
                "Consumer Cyclical": "消费",
                "Consumer Defensive": "消费",
                "Real Estate": "房地产",
                "Communication Services": "通信",
                "Industrials": "工业",
                "Energy": "能源",
                "Utilities": "公用事业",
            }

            cn_sector = sector_mapping.get(sector, sector)

            return {
                "sector": sector,
                "industry": industry,
                "cn_sector": cn_sector,
                "data_source": "yfinance",
            }
    except Exception as e:
        logger.debug(f"yfinance 港股行业获取失败: {e}")

    return None
```

---

## 4. Agent 自进化闭环

### 4.1 按行业的准确率追踪

```python
class IndustryAccuracyTracker:
    """
    按行业追踪行业对比分析师的准确率。

    核心假设: 分析师在某些行业(如银行/白酒)的判断
    可能一直比其他行业(如半导体)更准确。
    → 如果某行业判断准确率高，下次对该行业的预测可以给更高权重。
    """

    def record_prediction(self, result: AnalysisResult, data: dict):
        """记录预测详情"""
        industry = data.get("industry_name", "unknown")
        dq = data.get("data_quality", {})

        # 存入数据库...
        # INSERT INTO industry_accuracy_detail ...

    def get_accuracy_by_industry(self) -> dict:
        """
        Returns:
            {
                "银行": {"total": 20, "direction_accuracy": 0.65},
                "白酒": {"total": 15, "direction_accuracy": 0.73},
                "半导体": {"total": 10, "direction_accuracy": 0.40},
            }
        """
        pass

    def get_industry_confidence_adjustment(self, industry: str) -> float:
        """
        根据该行业的历史准确率，返回置信度调整系数。

        - 历史准确率 > 60% → 1.0 (不调整)
        - 历史准确率 50-60% → 0.9
        - 历史准确率 < 50% → 0.8
        - 样本不足 → 1.0
        """
        pass
```

### 4.2 置信度校准

```python
class IndustryConfidenceCalibrator:
    """行业对比分析师置信度校准器"""

    def __init__(self):
        self._calibration_bins = {
            "0.0-0.2": {"total": 0, "correct": 0},
            "0.2-0.4": {"total": 0, "correct": 0},
            "0.4-0.6": {"total": 0, "correct": 0},
            "0.6-0.8": {"total": 0, "correct": 0},
            "0.8-1.0": {"total": 0, "correct": 0},
        }

    def calibrate(self, raw_confidence: float, industry: str = None) -> float:
        """校准原始置信度"""
        bin_key = self._get_bin_key(raw_confidence)
        bin_stats = self._calibration_bins.get(bin_key)

        if bin_stats and bin_stats["total"] >= 10:
            historical_acc = bin_stats["correct"] / bin_stats["total"]
            calibrated = raw_confidence * 0.7 + historical_acc * 0.3
            return min(max(calibrated, 0.05), 0.95)

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
```

---

## 5. 行业轮动检测

### 5.1 核心思路

行业轮动是 A 股和港股中期最重要的超额收益来源之一。
检测轮动的早期信号，可以让行业对比分析师的判断从"静态对比"升级为"动态择时"。

### 5.2 轮动因子模型

```python
class SectorRotationDetector:
    """
    行业轮动检测器。

    检测以下轮动信号:
    1. 估值差异极端化: 行业间 PE 差异达到历史极端 → 均值回归概率大
    2. 动量反转: 前期强势板块动量衰减 → 资金可能切换
    3. 宏观周期切换: 经济周期阶段变化 → 行业偏好变化
    4. 政策驱动: 产业政策变化 → 资金重新配置
    """

    def detect_rotation_signals(self) -> list[dict]:
        """
        检测当前是否出现行业轮动信号。

        Returns:
            [
                {
                    "signal_type": "valuation_extreme",
                    "description": "新能源PE处于3年95%分位，银行PE处于3年5%分位",
                    "rotation_direction": "成长→价值",
                    "strength": 0.7,
                },
                ...
            ]
        """
        signals = []

        # 1. 估值差异极端化检测
        signals.extend(self._check_valuation_extremes())

        # 2. 动量反转检测
        signals.extend(self._check_momentum_reversal())

        return signals

    def _check_valuation_extremes(self) -> list[dict]:
        """检测行业间估值差异是否达到极端"""
        # 获取所有行业的当前PE分位
        # 如果最高分位 > 0.9 且最低分位 < 0.1 → 极端分化
        # → 均值回归概率大
        pass

    def _check_momentum_reversal(self) -> list[dict]:
        """检测前期强势板块是否出现动量衰减"""
        # 获取所有行业的近20日涨跌幅
        # 如果前期涨幅最大的行业近5日转跌 → 动量反转信号
        pass
```

### 5.3 轮动信号注入 Prompt

```python
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
```

---

## 6. 推理质量升级

### 6.1 产业链上下游分析

```python
INDUSTRY_CHAIN = {
    "新能源": {
        "upstream": ["有色金属", "化工"],  # 锂矿、电解液
        "midstream": ["电子", "电力"],     # 电池、储能
        "downstream": ["汽车", "家电"],    # 电动车、储能应用
    },
    "半导体": {
        "upstream": ["电子", "化工"],      # 硅片、光刻胶
        "midstream": ["半导体"],           # 设计、制造
        "downstream": ["电子", "通信", "计算机"],  # 消费电子、5G、AI
    },
    "房地产": {
        "upstream": ["银行", "水泥", "钢铁"],
        "midstream": ["房地产"],
        "downstream": ["家电", "建材"],
    },
}


def analyze_industry_chain(industry: str) -> dict:
    """
    分析产业链上下游的景气度传导。

    例如: 如果房地产销售回暖 → 家电需求可能在 6 个月后回升
    """
    chain = INDUSTRY_CHAIN.get(industry, {})
    if not chain:
        return {"note": "产业链数据不可用"}

    return {
        "upstream": chain.get("upstream", []),
        "downstream": chain.get("downstream", []),
        "implication": f"如果{industry}景气度变化，上下游可能受到传导影响",
    }
```

### 6.2 催化剂日历

```python
class IndustryCatalystCalendar:
    """
    行业催化剂日历。

    识别近期可能影响行业的催化事件:
    - 政策窗口（如年度经济工作会议、行业政策发布）
    - 财报季（行业集中披露期）
    - 行业展会/事件
    - 季节性因素（如白酒旺季、空调旺季）
    """

    def get_upcoming_catalysts(self, industry: str) -> list[dict]:
        """
        Returns:
            [
                {
                    "event": "半年报集中披露期",
                    "date_range": "2026-07-15 ~ 2026-08-31",
                    "impact": "positive",
                    "description": "行业半年报集中披露，业绩超预期可能推动估值修复"
                },
            ]
        """
        pass
```

---

## 7. 实施路线图

### Phase B: 数据深化 + 自进化（2 周）🟡 P1

| 任务 | 文件 | 工作量 |
|------|------|--------|
| ① 行业参考值自动刷新 | 新增 `industry_refresher.py` | 1.5 天 |
| ② 港股行业数据扩展 | `industry_fetcher.py` 扩展 | 1 天 |
| ③ 按行业的准确率追踪 | `prediction_store.py` 扩展 | 1 天 |
| ④ 置信度校准器 | 新增 `industry_calibrator.py` | 1 天 |
| ⑤ 端到端测试 | `tests/` | 1 天 |

**预期效果**：
- 行业参考值自动刷新（不再依赖硬编码）
- 港股行业覆盖率提升
- 按行业准确率分桶统计

### Phase C: 行业轮动 + 产业链（2 周）🟢 P2

| 任务 | 文件 | 工作量 |
|------|------|--------|
| ① 行业轮动检测器 | 新增 `sector_rotation_detector.py` | 2 天 |
| ② 轮动信号注入 prompt | `industry_analyst.py` 扩展 | 0.5 天 |
| ③ 产业链上下游分析 | `industry_preprocessor.py` 扩展 | 1.5 天 |
| ④ 催化剂日历 | 新增 `industry_catalyst_calendar.py` | 1.5 天 |
| ⑤ 失败案例分析 | 新增 `failure_analyzer.py` | 1 天 |

### Phase D: 深度优化（1 月+）🟢 P3

| 任务 | 说明 |
|------|------|
| 国际对标 | 同一行业在全球视角下的估值对比 |
| 行业风险溢价估计 | 估计行业的 equity risk premium |
| 行业轮动模型 | 构建轮动因子模型（估值差异 + 动量 + 宏观） |
| 多模型辩论 | 同一行业数据用 DeepSeek + Qwen 分别分析 |

---

## 附录：效果度量

### 关键指标

| 指标 | Round 1 后 | Phase B 目标 | Phase C 目标 | 度量方式 |
|------|-----------|-------------|-------------|---------|
| 方向准确率（A股） | ~55% | ≥60% | ≥65% | PredictionStore |
| 方向准确率（港股） | ~40% | ≥48% | ≥55% | PredictionStore |
| 置信度校准误差 | 未知 | ≤0.18 | ≤0.12 | \|confidence - actual_acc\| |
| 行业数据可用率（A股） | ~80% | ≥90% | ≥95% | 行业分类成功率 |
| 行业数据可用率（港股） | ~30% | ≥50% | ≥65% | 港股映射覆盖率 |
| 排名分位准确率 | 未量化 | 统计建立 | ≥60% | 排名 vs 实际表现 |
| 行业轮动检测率 | 0% | ≥30% | ≥50% | 轮动信号 vs 实际切换 |

### 实验设计

```bash
# 改进前后对比
python scripts/run_backtest.py -t 000001 --start 2026-01-01 --end 2026-06-30 --agent industry
python scripts/run_backtest.py -t 0700 --start 2026-01-01 --end 2026-06-30 --agent industry

# 重点观察:
# 1. 行业分类成功率: 从 40% → ?
# 2. 有完整行业数据时 → 准确率提升多少
# 3. "低PE+高ROE" 类型判断的准确率
# 4. 行业衰退期判断的领先性
# 5. 行业轮动信号的预测力
```

---

## 附录 A：文件变更清单

### Round 1 新增文件

| 文件 | 说明 |
|------|------|
| `src/data/industry_preprocessor.py` | 行业数据预处理管线（排名/周期/性价比/质量/分类/缓存） |
| `tests/test_industry_v2.py` | 39 个单元测试 |

### Round 1 修改文件

| 文件 | 变更 |
|------|------|
| `src/data/industry_fetcher.py` | 新增 `fetch_enhanced()` + 扩展行业映射 + 分类缓存 + 成分股获取 |
| `src/agents/industry_analyst.py` | 两步 CoT + 校准 + 校验 |
| `src/prompts/industry_prompts.py` | Few-shot + 锚定 + 行业类型区分 |

### Round 2 计划新增文件

| 文件 | 说明 |
|------|------|
| `src/utils/industry_refresher.py` | 行业参考值定期刷新 |
| `src/utils/industry_calibrator.py` | 置信度校准器 |
| `src/utils/sector_rotation_detector.py` | 行业轮动检测 |
| `src/utils/industry_catalyst_calendar.py` | 催化剂日历 |

---

> 📌 **核心原则**：Round 1 让行业对比分析师"有数据可用"，Round 2 让它"从历史中学习如何更好地使用数据"。好的行业分析 = 实时数据 + 准确排名 + 轮动判断 + 诚实评估，四者缺一不可。行业对比是连接"公司基本面"和"市场定价"的桥梁——桥越宽，分析越准。
