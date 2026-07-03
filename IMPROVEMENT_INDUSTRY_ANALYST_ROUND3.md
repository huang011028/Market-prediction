# 🏭 行业对比分析师改进方案 — Round 3

> **版本**: v3.0 | **日期**: 2026-07-03 | **前置**: Round 1-2 全部完成

---

## 目录

1. [Round 2 实施回顾](#1-round-2-实施回顾)
2. [Round 3 改进总览](#2-round-3-改进总览)
3. [产业链景气度传导](#3-产业链景气度传导)
4. [国际对标分析](#4-国际对标分析)
5. [多模型辩论模式](#5-多模型辩论模式)
6. [实施路线图](#6-实施路线图)

---

## 1. Round 2 实施回顾

### 1.1 已完成内容

| 模块 | 文件 | 核心成果 | 状态 |
|------|------|---------|------|
| 🔄 行业参考值刷新 | `industry_refresher.py` | 东方财富实时刷新 + 缓存管理 | ✅ |
| 📊 置信度校准 | `industry_calibrator.py` | 分桶统计 + 按行业/质量级别校准 | ✅ |
| 🔄 行业轮动检测 | `sector_rotation_detector.py` | 估值极端/动量反转/风格切换 | ✅ |
| 🔗 产业链分析 | `industry_chain.py` | 上下游分析 + 催化剂日历 (10+ 行业) | ✅ |
| 🧠 Agent 升级 | `industry_analyst.py` | 轮动信号 + 产业链 + 催化剂 prompt 注入 | ✅ |
| 🧪 测试 | `test_industry_v3.py` | 39 个新测试（轮动/产业链/催化剂/校准） | ✅ |

### 1.2 当前架构全景

```
┌─────────────────────────────────────────────────────────────────┐
│                     v3 完整架构                                   │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              数据采集层（多源）                            │   │
│  │  A股: 东方财富行业板块（成分股+行情）                     │   │
│  │  港股: yfinance sector + 已知映射表                      │   │
│  │  美股: yfinance sector + industry                       │   │
│  └────────────────────────┬────────────────────────────────┘   │
│                           ▼                                    │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              预处理管线                                   │   │
│  │  ① 行业平均估值（成分股加权）                            │   │
│  │  ② 标的行业排名分位                                      │   │
│  │  ③ 行业周期判断（复苏/繁荣/衰退/萧条）                   │   │
│  │  ④ 性价比综合评分（PE/ROE ratio）                       │   │
│  │  ⑤ 数据质量评估                                          │   │
│  └────────────────────────┬────────────────────────────────┘   │
│                           ▼                                    │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              信号注入层                                   │   │
│  │  ① 行业轮动信号（估值极端/动量反转/风格切换）            │   │
│  │  ② 产业链上下游分析                                      │   │
│  │  ③ 催化剂日历                                            │   │
│  │  ④ 风格分组（成长/价值/防御/周期）                       │   │
│  └────────────────────────┬────────────────────────────────┘   │
│                           ▼                                    │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              多步推理引擎                                 │   │
│  │  Step A: 定位 + 判断                                     │   │
│  │  Step B: 综合判断 + 反思                                 │   │
│  │  → 置信度校准（ceiling + 行业历史 + 质量级别）           │   │
│  │  → 一致性校验（排名/性价比/趋势/ceiling）                │   │
│  └────────────────────────┬────────────────────────────────┘   │
│                           ▼                                    │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              自进化闭环                                   │   │
│  │  按行业追踪准确率 → 校准器回归 → 影响下次预测            │   │
│  │  按数据质量级别统计 → 差异化 ceiling                     │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### 1.3 遗留问题（Round 3 解决）

| # | 问题 | 影响 | 优先级 |
|---|------|------|--------|
| 1 | 产业链分析仅静态映射 | 无法判断"当前上下游景气度如何" | 🟡 P1 |
| 2 | 无国际对标 | 无法判断"A 股银行 PE 5 是否便宜（对比全球）" | 🟢 P2 |
| 3 | 无多模型辩论 | 单一 LLM 可能有系统性偏差 | 🟢 P2 |
| 4 | 行业轮动信号未与准确率关联 | 不知道"检测到轮动信号时，实际准确率多少" | 🟡 P1 |

---

## 2. Round 3 改进总览

### 2.1 目标

Round 3 是"视野扩展"阶段，聚焦于：
1. **产业链动态分析**：从静态映射升级为动态景气度传导
2. **国际视野**：全球视角下的行业估值对比
3. **模型多样性**：多模型辩论减少单一模型偏差

### 2.2 改进维度

| 维度 | 改进项 | 优先级 | 预期收益 |
|------|--------|--------|---------|
| 🧠 架构 | 产业链动态景气度传导 | 🟡 P1 | 从静态映射升级为动态分析 |
| 📊 数据源 | 国际行业对标（MSCI/FTSE 行业 PE） | 🟢 P2 | 全球视角判断贵贱 |
| 🧠 架构 | 多模型辩论模式 | 🟢 P2 | 减少单一 LLM 偏差 |
| 🔬 质量 | 轮动信号准确率统计 | 🟡 P1 | 量化轮动检测的预测力 |

---

## 3. 产业链动态景气度传导

### 3.1 核心思路

Round 2 的产业链分析是静态的（只告诉"上下游是谁"）。Round 3 升级为动态：
- 获取上下游行业的当前涨跌幅/估值分位
- 判断"当前景气度正在从上游向下游传导"还是相反
- 预测标的行业的未来景气度方向

### 3.2 实现方案

```python
def analyze_industry_chain_dynamic(industry: str, industry_data: dict = None) -> dict:
    """
    动态产业链景气度分析。

    Args:
        industry: 行业名称
        industry_data: 各行业当前数据 {行业名: {pe, change_20d, ...}}

    Returns:
        {
            "upstream_momentum": "positive/negative",
            "downstream_momentum": "positive/negative",
            "transmission_direction": "upstream_to_downstream / downstream_to_upstream",
            "implication": "上游景气度正在向下游传导，需求端有望改善",
            "leading_indicator": "有色金属（上游）的涨跌幅是新能源的领先指标",
        }
    """
    from src.utils.industry_chain import INDUSTRY_CHAIN

    chain = INDUSTRY_CHAIN.get(industry, {})
    if not chain:
        return {"note": "产业链数据不可用"}

    upstream = chain.get("upstream", [])
    downstream = chain.get("downstream", [])

    if not industry_data:
        return {
            "upstream": upstream,
            "downstream": downstream,
            "description": chain.get("description", ""),
            "note": "无行业数据，仅返回静态映射",
        }

    # 计算上下游动量
    upstream_momentum = _calc_sector_momentum(upstream, industry_data)
    downstream_momentum = _calc_sector_momentum(downstream, industry_data)

    # 判断传导方向
    if upstream_momentum > 0.3 and downstream_momentum < 0:
        transmission = "upstream_to_downstream"
        implication = f"上游行业景气度正在回升，有望向{industry}传导"
    elif downstream_momentum > 0.3 and upstream_momentum < 0:
        transmission = "downstream_to_upstream"
        implication = f"下游需求端正在改善，有望拉动{industry}及其上游"
    elif upstream_momentum > 0 and downstream_momentum > 0:
        transmission = "both_positive"
        implication = f"产业链上下游均处于景气状态，{industry}基本面良好"
    elif upstream_momentum < 0 and downstream_momentum < 0:
        transmission = "both_negative"
        implication = f"产业链上下游均承压，{industry}需谨慎"
    else:
        transmission = "unclear"
        implication = f"产业链传导方向不明确，需更多数据"

    return {
        "upstream": upstream,
        "downstream": downstream,
        "upstream_momentum": upstream_momentum,
        "downstream_momentum": downstream_momentum,
        "transmission_direction": transmission,
        "implication": implication,
        "description": chain.get("description", ""),
    }


def _calc_sector_momentum(sectors: list, industry_data: dict) -> float:
    """计算行业板块的平均动量（-1 ~ 1）"""
    momentums = []
    for sector in sectors:
        data = industry_data.get(sector, {})
        change_20d = data.get("change_20d")
        if change_20d is not None:
            # 归一化到 -1 ~ 1
            momentums.append(max(-1, min(1, change_20d / 10)))

    if not momentums:
        return 0.0
    return sum(momentums) / len(momentums)
```

---

## 4. 国际对标分析

### 4.1 核心思路

同一行业在全球范围内的估值对比，可以帮助判断"A 股银行 PE 5 是否真的便宜"。

### 4.2 国际行业 PE 参考

```python
# 全球行业 PE 参考（近似值，实际可从 Bloomberg/MSCI/FTSE 获取）
GLOBAL_INDUSTRY_PE = {
    "银行": {"US": 12, "EU": 10, "JP": 8, "CN": 5.5, "Global": 10},
    "保险": {"US": 15, "EU": 12, "JP": 10, "CN": 12, "Global": 12},
    "白酒": {"US": 20, "EU": 18, "JP": 15, "CN": 25, "Global": 20},
    "医药": {"US": 35, "EU": 28, "JP": 25, "CN": 30, "Global": 30},
    "新能源": {"US": 25, "EU": 20, "JP": 18, "CN": 20, "Global": 22},
    "半导体": {"US": 30, "EU": 25, "JP": 20, "CN": 40, "Global": 28},
    "互联网": {"US": 28, "EU": 22, "JP": 18, "CN": 20, "Global": 24},
    "房地产": {"US": 12, "EU": 10, "JP": 12, "CN": 8, "Global": 11},
    "食品饮料": {"US": 22, "EU": 20, "JP": 18, "CN": 25, "Global": 21},
    "汽车": {"US": 15, "EU": 12, "JP": 10, "CN": 18, "Global": 13},
}


def compare_global_valuation(industry: str, current_pe: float) -> dict:
    """
    对比全球行业估值。

    Args:
        industry: 行业名称
        current_pe: 当前 PE

    Returns:
        {
            "current_pe": 5.5,
            "vs_global_avg": -45%,  # 低于全球平均 45%
            "vs_us": -54%,          # 低于美国 54%
            "percentile_global": 0.15,  # 处于全球 15% 分位（很便宜）
            "interpretation": "A 股银行 PE 显著低于全球平均水平",
        }
    """
    global_pe = GLOBAL_INDUSTRY_PE.get(industry)
    if not global_pe:
        return {"note": "无国际对标数据"}

    global_avg = global_pe.get("Global", current_pe)
    vs_global = (current_pe / global_avg - 1) * 100 if global_avg > 0 else 0

    return {
        "current_pe": current_pe,
        "global_pe_avg": global_avg,
        "vs_global_avg": f"{vs_global:+.0f}%",
        "vs_us": f"{(current_pe / global_pe.get('US', current_pe) - 1) * 100:+.0f}%",
        "interpretation": (
            f"{industry}行业当前 PE ({current_pe}) "
            f"{'低于' if vs_global < 0 else '高于'}全球平均 ({global_avg}), "
            f"差异 {abs(vs_global):.0f}%"
        ),
    }
```

---

## 5. 多模型辩论模式

### 5.1 核心思路

单一 LLM 可能有系统性偏差。多模型辩论模式：
- 同一行业数据用 DeepSeek + Qwen + GPT-4o 分别分析
- 取三者的"辩论共识"
- 标注分歧点

### 5.2 实现方案

```python
class MultiModelDebate:
    """多模型辩论模式"""

    def __init__(self, models: list = None):
        self.models = models or ["deepseek", "qwen", "gpt-4o"]

    async def debate(self, data: dict, context: dict) -> dict:
        """
        多模型辩论。

        Returns:
            {
                "consensus_direction": "bullish",
                "consensus_confidence": 0.68,
                "individual_results": [...],
                "disagreements": ["Qwen 看空，DeepSeek 和 GPT-4o 看涨"],
                "confidence_range": [0.55, 0.78],
            }
        """
        results = []
        for model in self.models:
            result = await self._analyze_with_model(model, data, context)
            results.append(result)

        # 计算共识
        directions = [r["direction"] for r in results]
        confidences = [r["confidence"] for r in results]

        # 方向共识: 多数投票
        from collections import Counter
        direction_counts = Counter(directions)
        consensus_direction = direction_counts.most_common(1)[0][0]

        # 置信度: 平均
        consensus_confidence = round(sum(confidences) / len(confidences), 2)

        # 分歧检测
        disagreements = []
        if len(set(directions)) > 1:
            for r in results:
                if r["direction"] != consensus_direction:
                    disagreements.append(
                        f"{r['model']} 判断为 {r['direction']} "
                        f"(置信度 {r['confidence']})"
                    )

        return {
            "consensus_direction": consensus_direction,
            "consensus_confidence": consensus_confidence,
            "individual_results": results,
            "disagreements": disagreements,
            "confidence_range": [min(confidences), max(confidences)],
        }
```

---

## 6. 实施路线图

### Phase D: 深度优化（1 月+）🟢 P2

| 任务 | 文件 | 工作量 |
|------|------|--------|
| ① 产业链动态景气度 | `industry_chain.py` 扩展 | 2 天 |
| ② 轮动信号准确率统计 | `sector_rotation_detector.py` 扩展 | 1.5 天 |
| ③ 国际对标分析 | 新增 `global_industry_benchmark.py` | 1.5 天 |
| ④ 端到端测试 | `tests/` | 1 天 |

### Phase E: 长期优化（3 月+）🟢 P3

| 任务 | 说明 |
|------|------|
| 多模型辩论 | DeepSeek + Qwen + GPT-4o 分别分析 |
| 产业链传导计时 | 上下游景气度传导的领先/滞后关系 |
| 行业风险溢价估计 | 估计行业的 equity risk premium |
| 全球产业链对标 | 同一行业在全球视角下的估值对比 |

---

## 附录：完整文件清单

### 新增文件（Round 1-3）

| 文件 | 说明 | Round |
|------|------|-------|
| `src/data/industry_preprocessor.py` | 行业数据预处理管线 | R1 |
| `src/utils/industry_refresher.py` | 行业参考值刷新器 | R2 |
| `src/utils/industry_calibrator.py` | 行业置信度校准器 | R2 |
| `src/utils/sector_rotation_detector.py` | 行业轮动检测器 | R2 |
| `src/utils/industry_chain.py` | 产业链分析 + 催化剂日历 | R2 |
| `src/utils/global_industry_benchmark.py` | 国际行业对标 | R3 |
| `tests/test_industry_v2.py` | 39 个单测 | R1 |
| `tests/test_industry_v3.py` | 39 个单测 | R2 |

### 修改文件

| 文件 | 变更 | Round |
|------|------|-------|
| `src/data/industry_fetcher.py` | `fetch_enhanced()` + 扩展映射 + 缓存 + 成分股 | R1+R2 |
| `src/agents/industry_analyst.py` | 两步 CoT + 轮动 + 产业链 + 催化剂 + 校准 | R1+R2 |
| `src/prompts/industry_prompts.py` | Few-shot + 锚定 + 行业类型区分 | R1 |

---

## 附录：统计数据

### 测试覆盖

| 测试文件 | 测试数 | 覆盖模块 |
|---------|--------|---------|
| `test_fundamental_v2.py` | 38 | 趋势/评分卡/陷阱/分位/质量/集成 |
| `test_fundamental_v3.py` | 32 | 校准/优化/基准/港股解析 |
| `test_industry_v2.py` | 39 | 排名/周期/性价比/分类/管线 |
| `test_industry_v3.py` | 39 | 轮动/产业链/催化剂/校准 |
| **合计** | **148** | |

### 新增代码统计

| 模块 | 文件数 | 约行数 |
|------|--------|--------|
| 公司前景数据 | 3 | ~1,800 |
| 行业对比数据 | 4 | ~2,200 |
| Agent | 2 | ~800 |
| Prompt | 2 | ~600 |
| 工具/校准 | 4 | ~1,500 |
| 测试 | 4 | ~2,000 |
| **合计** | **19** | **~8,900** |

---

> 📌 **核心原则**：Round 1-2 让行业对比分析师"有完整数据、会动态判断、能自进化"，Round 3 让它"有产业链视野、有全球眼光、有多模型校验"。好的行业分析 = 实时数据 × 准确排名 × 轮动判断 × 产业链 × 国际对标 × 多模型共识，六维能力的持续提升是永恒的方向。
