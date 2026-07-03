# 📊 近期股价分析师 — Round 3 深度优化设计文档

> **Round 2 回顾**：实现了置信度/幅度校准器（`confidence_calibrator.py`）和标的个性化参数（`stock_profiles.py`），174 个测试全过。
>
> **Round 3 核心命题**：从"校准预测结果"升级到"从历史中学习"。让系统记住每次预测的对错，在下次类似情况出现时自动引用。

---

## 1. Round 2 实际交付 vs 计划

| 计划 | 状态 | 文件 |
|------|------|------|
| ⭐⭐⭐ 置信度与幅度校准 | ✅ 完成 | `src/core/confidence_calibrator.py` |
| ⭐⭐⭐ 两阶段分析流程 | 📅 推迟 | 双倍 LLM 成本，ROI 待评估 |
| ⭐⭐ 标的个性化参数 | ✅ 完成 | `src/data/stock_profiles.py` (7个标的) |
| ⭐⭐ 历史相似形态匹配 | 📅 推迟 | 需验证数据积累 |
| ⭐⭐ 板块联动分析 | 📅 推迟 | 单独评估 |
| ⭐ 预测事后分析(自进化) | 📅 推迟 | 需验证数据积累 |

### Round 2 生效条件

| 功能 | 当前状态 | 生效条件 |
|------|---------|---------|
| 幅度校准 | ✅ 立即生效 | ATR 数据始终可用 |
| 标的个性化 | ✅ 立即生效 | 7 个标的硬编码 + 默认兜底 |
| 置信度校准 | ⏳ 待激活 | 该 Agent 需积累 ≥5 次验证预测 |

---

## 2. Round 3 目标清单

| # | 优化 | 产出 | 优先级 |
|---|------|------|--------|
| 3.1 | 历史形态匹配引擎 | `src/core/pattern_matcher.py` | ⭐⭐⭐ |
| 3.2 | 预测事后分析 | `src/core/post_mortem.py` | ⭐⭐ |
| 3.3 | 标的 profile 自动学习 | `src/data/stock_profiles.py` 升级 | ⭐⭐ |
| 3.4 | 指标有效性追踪 | `src/core/indicator_tracker.py` | ⭐⭐ |
| 3.5 | 汇总 Agent 接入技术面校准结果 | `src/agents/aggregator.py` | ⭐ |

---

## 3. 优化一：历史形态匹配引擎

### 3.1 价值

Round 2 的校准器告诉 LLM "你历史上准确率如何"，但没告诉它"上次这种形态出现时结果如何"。

```
当前: "我判断这是看跌，置信度 60%"
      → 校准器: "你历史上平均偏乐观 8%，修正为 55%"

Round3: "我判断这是看跌，置信度 60%"
        → 校准器: "你历史上平均偏乐观 8%，修正为 55%"
        → 形态匹配: "上次类似形态(空头排列+KDJ金叉+OBV背离)，3次中2次正确"
        → 最终置信度: 参考历史，维持 55%
```

### 3.2 技术方案

利用已积累的 `PredictionStore` 数据：

```
每次预测时保存技术特征向量 → 存储在 agent_results 的 data_summary 中
  ↓
新预测时 → 提取当前特征 → 计算余弦相似度 → 检索 Top-K 相似历史案例
  ↓
注入 Analyst Prompt: "历史相似形态参考"
```

### 3.3 特征向量设计

```python
# 技术特征向量——数值化当前市场状态
FEATURE_VECTOR = [
    ma5 / price,          # MA5 相对价格
    ma20 / price,         # MA20 相对价格
    ma5 / ma20,           # 短期/中期均线比值
    rsi / 100,            # RSI 归一化
    macd_bar / price,     # MACD 柱状线归一化
    adx / 100,            # ADX 归一化
    atr_pct,              # ATR 百分比
    vol_ratio,            # 量比
    change_5d / 10,       # 5日涨跌归一化
    change_20d / 10,      # 20日涨跌归一化
]
```

### 3.4 注入 Prompt 格式

```markdown
## 📚 历史相似形态参考（基于系统已验证预测）

当前技术特征与历史上 3 次情况最相似：

### 案例 1: 2026-03-15, 000001 (相似度 82%)
- 当时特征: MA空头、ADX=27、KDJ超卖金叉、缩量
- 当时预测: 📉 看跌 -3%~+1%，置信度 62%
- 实际结果: 📉 -1.8% ✅ 正确
- 经验: 趋势市中超卖反弹力度有限

### 案例 2: 2026-05-20, 000001 (相似度 71%)
- 当时特征: MA空头、ADX=30、KDJ金叉+OBV底背离
- 当时预测: 📉 看跌 -2%~+2%，置信度 55%
- 实际结果: 📈 +2.3% ❌ 错误
- 经验: KDJ+OBV双底背离可能预示阶段性反转

> ⚠️ 历史不重复，仅供参考。请结合当前实际判断。
```

---

## 4. 优化二：预测事后分析（自进化）

### 4.1 触发时机

每次运行 `track_predictions.py` 验证预测后，对每个新验证的预测：

```
预测被验证 → 提取预测时的技术特征 + 预测内容 + 实际结果
  → LLM 复盘分析 → 生成一条"经验教训"
  → 存入 post_mortems 表
  → 下次形态匹配时随案例一起返回
```

### 4.2 经验教训格式

```json
{
  "prediction_id": "abc123",
  "root_cause": "忽略了周线级别压力位，仅关注了日线反弹信号",
  "missed_signal": "周线MA20向下且价格在其下方，中期趋势未反转",
  "lesson": "日线金叉在周线空头背景下不可靠，需等待周线确认",
  "confidence_advice": "类似形态建议 confidence 上限 0.55（原给 0.65）",
  "morphology_tags": ["ma_bearish", "kdj_golden_cross", "weekly_bearish"]
}
```

### 4.3 数据库扩展

```sql
CREATE TABLE IF NOT EXISTS post_mortems (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    root_cause TEXT,
    missed_signal TEXT,
    lesson TEXT NOT NULL,
    confidence_advice TEXT,
    morphology_tags TEXT,  -- JSON 数组，用于相似检索
    created_at TEXT,
    FOREIGN KEY (prediction_id) REFERENCES predictions(id)
);
```

---

## 5. 优化三：标的 Profile 自动学习

### 5.1 当前限制

Round 2 的 `stock_profiles.py` 硬编码了 7 个标的。每新增一个标的都需要手动添加。

### 5.2 自动学习

从 `PredictionStore` 中提取已验证预测，自动计算：

```python
class ProfileLearner:
    """从验证数据中学习标的特点"""
    
    def learn_from_history(self, symbol: str, min_samples: int = 10) -> dict:
        """
        基于该标的历史预测+验证数据，自动学习：
        - avg_weekly_move: 平均周涨跌幅(绝对值)
        - trend_following: MA金叉信号准确率
        - reversal_tendency: RSI超卖反弹概率
        - best_timeframe: 哪个时间维度预测最准
        """
```

### 5.3 混合策略

```
预测时:
  if 该标的验证数据 >= 10次:
      使用自动学习的 profile
  elif 在已知列表中:
      使用硬编码 profile
  else:
      使用默认 profile
```

---

## 6. 优化四：指标有效性追踪

### 6.1 问题

当前所有技术指标一视同仁，但实际不同标的上指标有效性差异很大：
- 银行股：均线信号可靠，RSI 几乎没用（很少超买超卖）
- 科技股：MACD 金叉经常是假突破，成交量更重要

### 6.2 方案

每次验证后，记录该次预测中出现了哪些信号以及最终是否正确：

```python
class IndicatorTracker:
    """追踪每个技术指标的历史预测效果"""
    
    SIGNALS = [
        "ma_golden_cross", "ma_death_cross", "ma_bullish_alignment",
        "macd_golden_cross", "macd_death_cross",
        "rsi_oversold", "rsi_overbought",
        "kdj_golden_cross", "kdj_death_cross",
        "obv_bullish_divergence", "obv_bearish_divergence",
        "adx_strong_trend", "adx_ranging",
        "vol_expanding", "vol_contracting",
    ]
    
    def track_signals(self, prediction_id, active_signals, was_correct):
        """记录: 出现了哪些信号 → 结果对/错"""
    
    def get_signal_accuracy(self, symbol, signal_name) -> dict:
        """查询: MA金叉在000001上准确率? 
           → {total: 15, correct: 10, accuracy: 0.67}
        """
```

### 6.3 注入 Prompt

```markdown
## 📊 信号历史准确率（该标的）
- MA金叉: 67%准确 (10/15次)
- KDJ金叉: 55%准确 (8/14次)  
- ADX>25强趋势: 72%准确 (13/18次)
- RSI超卖: 40%准确 (4/10次) ← 参考价值低
```

---

## 7. 优化五：汇总 Agent 接入技术面校准

### 7.1 问题

Round 2 的校准结果（幅度警告、信度修正）只在技术面 Analyst 的 reasoning 末尾加了一行备注。汇总 Agent 不知道这些校准信息。

### 7.2 方案

在 `AnalysisResult` 的 `data_summary` 中加入校准信息，让汇总 Agent 能读取：

```python
result.data_summary = {
    "calibration": {
        "confidence_original": 0.65,
        "confidence_calibrated": 0.58,
        "adjustments": ["历史偏差: 68%→62%", "信号矛盾: -15%"],
        "magnitude_warning": None,  # or "震荡市幅度偏宽"
    },
    "stock_profile": {
        "type": "large_cap_bank",
        "avg_weekly_move": 3.0,
    }
}
```

汇总 Agent 的 prompt 中增加一段：

```markdown
## 🔧 技术面校准信息
- 置信度原始值 65%，经系统校准为 58%（历史偏差+信号矛盾）
- 幅度: 无异常
- 标的特点: 大型银行股，历史周波动 < 3%
```

---

## 8. 实施优先级与工作量

```
优先级  优化项                          预期收益  工作量  依赖
────── ──────────────────────────────── ──────  ─────  ────────────
 ⭐⭐⭐  3.1 历史形态匹配引擎               高      中     PredictionStore数据
 ⭐⭐⭐  3.5 汇总接入校准信息                高      低     无
 ⭐⭐    3.2 预测事后分析(自进化)           高(长期) 中     PredictionStore数据
 ⭐⭐    3.3 标的Profile自动学习            中      中     PredictionStore数据
 ⭐⭐    3.4 指标有效性追踪                 中      高     PredictionStore数据
 ⭐      3.6 两阶段分析(从R2推迟)           中      中     LLM成本翻倍

建议路线:
  Day 1:   3.5 汇总接入校准 → 快速见效，让校准信息被汇总Agent利用
  Day 2-3: 3.1 历史形态匹配 → 核心功能，需要特征向量+相似检索
  Day 4:   3.2 预测事后分析 → 配合形态匹配，形成"经验库"
  Day 5+:  3.3 + 3.4 → 自动化学习（需要更多验证数据）
```

---

## 附录：Round 1→2→3 进化路线图

```
Round 0 (Phase 1):
  基础K线+4指标 → LLM 一次性分析 → 输出

Round 1 (已完成):
  12指标+周线+6步框架+信号表格 → LLM 分析 → 输出

Round 2 (已完成):
  12指标+周线+6步框架+信号表格 → LLM 分析 
    → [校准器] 置信度修正+幅度检查 
    → [个性化] 标的特点注入Prompt 
    → 输出（校准后）

Round 3 (本阶段):
  12指标+周线+6步框架+信号表格 → LLM 分析
    → [校准器] 置信度+幅度矫正
    → [个性化] 标的Profile（硬编码+自动学习）
    → [形态匹配] 检索历史相似案例 → 注入Prompt
    → [汇总] 校准信息传递给汇总Agent
    → 输出（校准后+历史参考）
    ↓
  验证后 → [事后分析] 总结经验教训 → 入库
          → [Profile学习] 更新标的特点
          → [指标追踪] 更新信号准确率
```

---

> 📌 **Round 3 的核心思想**：让系统形成"预测→验证→学习→改进预测"的完整闭环。Round 1 让数据更丰富，Round 2 让输出更准确，Round 3 让系统开始从经验中成长。
