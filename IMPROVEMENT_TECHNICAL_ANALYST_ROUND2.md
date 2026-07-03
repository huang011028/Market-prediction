# 📊 近期股价分析师 — Round 2 深度优化设计文档

> **上一轮回顾**：Round 1 完成了数据源增强（ATR/OBV/KDJ/ADX/周线/K线形态）、6 步分析框架 Prompt、结构化信号摘要表格。
>
> **本轮目标**：让 Agent 从"一次性的分析工具"进化为"会学习、会校准、会反思的技术分析师"。

---

## 目录

1. [Round 1 回顾与 Round 2 定位](#1-round-1-回顾与-round-2-定位)
2. [优化一：两阶段分析流程](#2-优化一两阶段分析流程)
3. [优化二：置信度与幅度校准](#3-优化二置信度与幅度校准)
4. [优化三：历史相似形态匹配](#4-优化三历史相似形态匹配)
5. [优化四：预测事后分析（自进化）](#5-优化四预测事后分析自进化)
6. [优化五：标的个性化参数](#6-优化五标的个性化参数)
7. [优化六：板块联动分析](#7-优化六板块联动分析)
8. [实施优先级与工作量估算](#8-实施优先级与工作量估算)

---

## 1. Round 1 回顾与 Round 2 定位

### Round 1 做了什么

```
数据层: 4个基础指标 → 12个指标（含 ATR/OBV/KDJ/ADX/周线/K线形态）
分析层: 笼统框架 → 6 步分析框架（趋势→动能→量价→多周期→关键位→综合）
呈现层: JSON裸数据 → 结构化信号摘要表格 + ATR波动引导
```

### Round 1 留下了什么

```
✅ 数据多了 → 但 LLM 有时被大量数据淹没，不知道哪个信号最重要
✅ Prompt 好了 → 但 LLM 的 confidence 还是主观判断，缺乏客观校准
✅ 每次分析独立 → 历史对错完全不反馈到新分析中
✅ 所有股票用同一套逻辑 → 不区分银行股(低波动)和科技股(高波动)
```

### Round 2 的核心命题

> **从"告诉 LLM 怎么看"到"让系统记录 LLM 看得对不对，并据此改进"**

```
Round 1: 输入质量 ↑  (更好的数据 + 更好的 Prompt)
Round 2: 输出质量 ↑  (校准 + 反馈 + 个性化)
```

---

## 2. 优化一：两阶段分析流程

### 2.1 当前问题

Round 1 的 6 步框架是一次性 LLM 调用。LLM 需要同时做：
- 识别所有技术信号
- 判断哪些信号重要
- 综合评估方向
- 估算幅度和置信度

一次性完成这么多任务，容易出现：
- 遗漏重要信号（被次要信息淹没）
- 信号权重判断不一致（同一种形态，不同时间给的权重不同）
- 过度关注最近的指标（RSI 41 vs 42 的区别其实不重要）

### 2.2 解决方案：信号清单 → 综合研判

```
阶段1: 信号清单（客观、结构化）
  输入: 完整的技术指标数据
  输出: 标准化的信号清单 JSON
  特点: 不判断方向，只清点信号

阶段2: 综合研判（主观、经验性）
  输入: 阶段1的信号清单
  输出: 方向 + 幅度 + 置信度 + reasoning
  特点: 基于信号清单做判断，引用具体信号编号
```

### 2.3 阶段1：信号清单格式

```json
{
  "signal_summary": {
    "bullish_count": 3,
    "bearish_count": 5,
    "neutral_count": 2,
    "dominant_theme": "bearish"
  },
  "signals": [
    {
      "id": "S1",
      "category": "trend",
      "name": "均线空头排列",
      "direction": "bearish",
      "strength": "strong",
      "description": "MA5(10.19) < MA10(10.38) < MA20(10.61) < MA60(10.68)",
      "is_primary": true
    },
    {
      "id": "S2",
      "category": "momentum",
      "name": "KDJ金叉",
      "direction": "bullish",
      "strength": "moderate",
      "description": "K线(19.84)上穿D线(18.56)，处于超卖区金叉",
      "is_primary": false
    },
    {
      "id": "S3",
      "category": "volume",
      "name": "缩量运行",
      "direction": "neutral",
      "strength": "moderate",
      "description": "VOL_ratio=0.93，成交量低于5日均量",
      "is_primary": false
    }
    // ... 更多信号
  ],
  "contradictions": [
    {
      "signal_a": "S1",
      "signal_b": "S2", 
      "description": "趋势空头 vs KDJ超卖金叉 — 趋势类信号权重更高"
    }
  ]
}
```

### 2.4 阶段2：综合研判 Prompt

```
"基于以下信号清单，进行综合研判：

### 信号清单
{阶段1的JSON}

### 你的任务
1. 看涨信号和看跌信号各有多少？哪个更强？
2. 矛盾信号如何解读？(趋势 vs 动能，日线 vs 周线)
3. 如果必须选一个方向，你会选哪个？为什么？
4. 什么情况下这个判断会错？

输出: 标准 AnalysisResult JSON"
```

### 2.5 工作量与收益

| 维度 | 评估 |
|------|------|
| 代码改动 | `technical_analyst.py` 新增 `_stage1_detect_signals()` |
| LLM 调用 | 1 次 → 2 次（成本翻倍，但质量更高） |
| 预期收益 | 信号识别更全面，减少遗漏；综合判断有据可依 |
| 风险 | 阶段1 的 JSON 格式可能不稳定，需要严格的输出约束 |

---

## 3. 优化二：置信度与幅度校准

### 3.1 当前问题

LLM 输出的 confidence 完全是主观的：
- 同样形态，今天给 65%，明天可能给 55%
- confidence 与实际准确率之间没有校准关系
- 例如历史上 confidence=0.65 的预测，实际正确率可能只有 50%

### 3.2 解决方案：双层校准

```python
class ConfidenceCalibrator:
    """置信度校准器"""
    
    def __init__(self, store: PredictionStore):
        self.store = store
    
    def calibrate(self, raw_confidence: float, agent_name: str, 
                  timeframe: str, signal_strength: dict) -> float:
        """
        输入: LLM 给的原始置信度
        输出: 校准后的置信度
        
        校准步骤:
        1. 历史偏差修正: 
           该 Agent 短期预测中，实际准确率 / 平均置信度 = 校准系数
           例如: 实际准确率 62% / 平均宣称置信度 68% = 0.91
           → raw_confidence *= 0.91
        
        2. 信号一致性调整:
           多空信号都很多、矛盾严重 → 降低 confidence
           信号高度一致、无矛盾 → confidence 不变或微调
           
        3. 数据完整性调整:
           某些关键指标缺失 → 降低 confidence
        
        4. Clamp 到 [0.05, 0.95] 避免极端值
        """
```

### 3.3 幅度校准

```python
class MagnitudeCalibrator:
    """幅度校准器"""
    
    def calibrate(self, predicted: Magnitude, atr_pct: float, 
                  adx: float, timeframe: str) -> Magnitude:
        """
        基于 ATR 和 ADX 检查 LLM 预测的幅度是否合理
        
        规则:
        - 震荡市(ADX<20): 幅度应收窄，ATR引导
        - 趋势市(ADX>25): 幅度可以放宽
        - 如果 LLM 预测幅度远超 ATR 推导范围 → 标注为"可能过度自信"
        """
        weekly_vol = atr_pct * 2.24
        
        # 检查: 预测幅度是否在 ATR 的 0.5~3 倍范围内
        predicted_range = predicted.max_pct - predicted.min_pct
        
        if predicted_range < weekly_vol * 0.3:
            # LLM 可能太保守
            pass
        elif predicted_range > weekly_vol * 3:
            # LLM 可能太激进，收窄
            center = (predicted.min_pct + predicted.max_pct) / 2
            half = weekly_vol * 1.5
            return Magnitude(center - half, center + half)
        
        return predicted
```

### 3.4 实现注意

- 校准器**修正但不替代** LLM 判断
- 校准后的 confidence 在 reasoning 末尾标注: `[系统校准: 原始置信度68%→校准后62%，基于历史偏差0.91]`
- 校准参数随验证数据积累持续更新

---

## 4. 优化三：历史相似形态匹配

### 4.1 价值

Round 1 分析完全基于当前数据，不考虑历史。但实际上：
- "这个形态上次出现时，后来涨了还是跌了？"
- "这个标的的技术信号历史上准确率如何？"

利用 Phase 3 已有的 `PredictionStore` + `CaseRetriever` 基础设施。

### 4.2 实现方案

```python
class PatternMatcher:
    """历史形态匹配器"""
    
    def __init__(self, store: PredictionStore, retriever: CaseRetriever):
        self.store = store
        self.retriever = retriever
    
    async def find_similar(self, current_features: dict, 
                           symbol: str, top_k: int = 3) -> list[dict]:
        """
        找到历史上最相似的 N 个技术面分析案例
        
        特征向量:
        {
            "ma_arrangement": "bearish",      # 空头排列
            "rsi_zone": "30-50",              # 偏弱
            "macd_signal": "bearish_holding", # 空头持仓
            "adx_level": "strong",            # 强趋势
            "obv_divergence": "bullish",       # 底背离
            "weekly_trend": "down",           # 周线向下
            "vol_ratio": 0.93,                # 缩量
        }
        
        匹配逻辑:
        1. 从 PredictionStore 取该标的+其他同类标的的已验证预测
        2. 提取每次预测时的技术特征
        3. 计算余弦相似度
        4. 返回 Top-K，含事后验证结果
        """
```

### 4.3 注入 Analyst Context

检索结果注入到技术面分析师（或汇总分析师）的 prompt 中：

```markdown
## 📚 历史相似形态参考

当前形态特征: 空头排列 + ADX强趋势 + KDJ超卖金叉 + OBV底背离

历史上最相似的 3 次情况:

### 案例 1: 2026-03-15 (相似度 82%)
- 当时形态: MA空头、ADX=28、KDJ超卖金叉、量缩
- 预测: 📉 看跌 -3%~+1%，置信度 62%
- 实际: 📉 下跌 1.8% ✅ 方向正确
- **启示**: 趋势市中超卖反弹力度有限，顺势看跌成功率高

### 案例 2: 2026-05-20 (相似度 71%)
- 当时形态: MA空头、ADX=30、KDJ金叉、OBV背离
- 预测: 📉 看跌 -2%~+2%，置信度 55%
- 实际: 📈 反弹 2.3% ❌ 方向错误
- **启示**: OBV底背离+KDJ金叉的组合有时预示阶段性底部

> ⚠️ 历史不重复，但韵律相似。以上仅供参考。
```

### 4.4 工作和收益

| 维度 | 评估 |
|------|------|
| 代码改动 | `src/core/pattern_matcher.py` + 修改 `technical_analyst._build_user_prompt` |
| 依赖 | PredictionStore 中需有足够多的已验证预测（目前较少，需积累） |
| 预期收益 | 高，尤其是对常见形态的预测准确性 |
| 风险 | 历史匹配可能找到"表面相似但本质不同"的案例，LLM 需辨别 |

---

## 5. 优化四：预测事后分析（自进化）

### 5.1 核心思路

每次预测被验证后（通过 `track_predictions.py`），自动触发一次"事后分析"：
- 用 LLM 回顾这次预测
- 分析对错原因
- 提炼一条经验教训
- 存入数据库，供未来 RAG 检索

### 5.2 实现方案

```python
class PostMortemAnalyzer:
    """预测事后分析器"""
    
    async def analyze(self, prediction: PredictionRecord, 
                      llm: LLMClient) -> str:
        """
        复盘一次已完成验证的预测
        
        Returns:
            一条结构化的经验教训
        """
        
        # 获取预测时的原始数据（从 report_json 中提取）
        # 获取实际的结果
        # 让 LLM 分析
        
        prompt = f"""
## 预测复盘

### 预测时的情况
{预测时的技术数据摘要}

### 预测内容
- 方向: {prediction.direction}
- 幅度: {prediction.predicted_magnitude_str}
- 置信度: {prediction.confidence:.0%}

### 实际结果
- 方向: {prediction.actual_direction}
- 涨跌: {prediction.actual_change_pct:+.1f}%
- 方向正确: {'✅' if prediction.direction_correct else '❌'}

### 请回答
1. 预测正确/错误的主要原因是什么？（技术层面）
2. 有什么信号被忽略或高估了？
3. 一句话经验教训
4. 这一类形态在未来应该如何处理？

输出 JSON:
{{
  "root_cause": "…",
  "missed_signal": "…",
  "lesson": "一句话",
  "confidence_adjustment": "下次类似形态建议confidence从X调整为Y"
}}
"""
```

### 5.3 经验库

事后分析的结果存入 `PredictionStore` 的扩展表 `post_mortems`：

```sql
CREATE TABLE IF NOT EXISTS post_mortems (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id TEXT NOT NULL,
    root_cause TEXT,
    missed_signal TEXT,
    lesson TEXT,
    confidence_advice TEXT,
    created_at TEXT,
    FOREIGN KEY (prediction_id) REFERENCES predictions(id)
);
```

这些经验可以在下次类似形态出现时，通过 RAG 检索并注入 prompt。

### 5.4 工作和收益

| 维度 | 评估 |
|------|------|
| 代码改动 | `src/core/post_mortem.py` + schema 扩展 |
| 依赖 | 需要积累一定量的已验证预测 |
| 预期收益 | 长期最高——系统真正从错误中学习 |
| 风险 | 低，纯增量功能，不影响现有分析流程 |

---

## 6. 优化五：标的个性化参数

### 6.1 问题

当前对所有股票用同一套分析逻辑：
- 银行股（低波动、PE驱动）和创业板科技股（高波动、情绪驱动）
- 技术指标的有效性完全不同
- 例如：银行股的金叉信号更可靠（基本面支撑），科技股的金叉经常是假突破

### 6.2 解决方案

```python
class StockProfileManager:
    """标的个性化配置管理"""
    
    # 硬编码的已知标的特征（基于市场常识）
    KNOWN_PROFILES = {
        "000001": {  # 平安银行
            "type": "large_cap_value",
            "avg_atr_pct": 1.5,          # 低波动
            "trend_following": 0.7,       # 趋势性强
            "ma_effectiveness": 0.65,     # 均线信号较可靠
            "rsi_range": (25, 75),        # 很少极端超买超卖
            "typical_weekly_move": 3.0,   # 周波动通常 < 3%
            "notes": "大型银行股，低波动，技术信号可靠性中等，关注估值和宏观"
        },
        "600519": {  # 贵州茅台
            "type": "large_cap_growth",
            "avg_atr_pct": 2.0,
            "trend_following": 0.8,       # 趋势性很强
            "ma_effectiveness": 0.75,      # 均线信号很可靠
            "rsi_range": (30, 80),
            "typical_weekly_move": 4.0,
            "notes": "白酒龙头，趋势性强，回调通常是买入机会"
        },
    }
    
    def get_profile(self, symbol: str) -> dict:
        """获取标的个性化参数，未知标的返回默认值"""
        return self.KNOWN_PROFILES.get(
            symbol.zfill(6),
            {  # 默认参数
                "type": "unknown",
                "avg_atr_pct": 2.5,
                "trend_following": 0.6,
                "ma_effectiveness": 0.55,
                "rsi_range": (30, 70),
                "typical_weekly_move": 5.0,
                "notes": "未知标的，使用默认参数"
            }
        )
```

### 6.3 注入 Analyst Prompt

在 `_build_user_prompt` 中添加：

```markdown
## 🏷️ 标的信息
- 类型: 大型银行股（低波动）
- 历史周波动: 通常 < 3%
- 均线信号可靠性: 中等（65%）
- **注意**: 该标的波动较小，如果判断震荡，幅度区间应收窄（±1~2%）
```

### 6.4 长期：从验证数据中学习

```
V1(Round2): 硬编码已知标的特点
V2(Future): 从 PredictionStore 中的验证数据自动学习:
  - 该标的金叉信号准确率 = 历史验证数据统计
  - 该标的平均周波动 = 历史实际波动计算
  - 自动更新 profile
```

---

## 7. 优化六：板块联动分析

### 7.1 价值

个股走势受板块影响很大：
- 板块龙头涨，小弟跟涨
- 板块整体走弱，个股难以独善其身
- 个股相对板块的强弱（Alpha）是重要信号

### 7.2 实现方案

```python
class SectorAnalyzer:
    """板块联动分析"""
    
    async def analyze(self, symbol: str, market: str) -> dict:
        """
        获取:
        1. 标的所属板块指数近期走势
        2. 标的相对板块的强弱（Alpha）
        3. 板块内龙头股走势
        """
        
        sector_index_data = await self._fetch_sector_index(symbol, market)
        
        return {
            "sector_name": "银行",
            "sector_change_5d": -0.5,     # 板块5日涨跌
            "symbol_change_5d": -2.1,      # 标的5日涨跌
            "relative_strength": "weaker", # 弱于板块
            "alpha_5d": -1.6,             # 相对板块跑输 1.6%
            "sector_trend": "down",        # 板块趋势
            "sector_leader_performance": "neutral"
        }
```

### 7.3 注入 Prompt

```markdown
## 📊 板块背景
- 所属板块: 银行
- 板块5日: -0.5% | 标的5日: -2.1% → **弱于板块 1.6%**
- ⚠️ 标的在板块内表现偏弱，技术面走弱可能有基本面原因
```

---

## 8. 实施优先级与工作量估算

```
优先级  优化项                         预期收益  工作量  依赖
────── ──────────────────────────────── ──────  ─────  ────────────
 ⭐⭐⭐  优化二: 置信度与幅度校准          高      中     PredictionStore
 ⭐⭐⭐  优化一: 两阶段分析流程            高      中     无
 ⭐⭐    优化五: 标的个性化参数            中      低     无
 ⭐⭐    优化三: 历史相似形态匹配           高      中     PredictionStore+数据积累
 ⭐⭐    优化六: 板块联动分析              中      中     sector fetcher
 ⭐      优化四: 预测事后分析(自进化)      高(长期) 中     PredictionStore+数据积累

建议路线:
  Day 1-2: 优化一 + 优化二 (两阶段分析 + 校准) → 核心质量提升
  Day 3:   优化五 (标的个性化) → 快速见效
  Day 4-5: 优化三 (历史形态匹配) → 需数据积累，先搭框架
  Day 6:   优化六 (板块联动) → 补充维度
  Day 7+:  优化四 (自进化) → 需要足够多的验证数据后实施
```

---

## 附录：Round 1+2 完整架构变化

```
Round 0 (Phase 1):
  3个月日K线 + 4个基础指标 → LLM 一次性分析 → 输出

Round 1 (已完成):
  日线+周线 + 12个指标 + 6步框架 → 结构化信号表 → LLM 分析 → 输出

Round 2 (本阶段):
  日线+周线 + 12个指标 + 板块数据
    ↓
  [阶段1] 信号清单（LLM 清点信号）→ 结构化 JSON
    ↓
  [阶段2] 综合研判（LLM 基于信号清单判断）
    ↓
  [校准] 置信度校准 + 幅度校准 + 标的特点适配
    ↓
  [注入] 历史相似形态参考 + 板块联动背景
    ↓
  输出（校准后）
    ↓
  [验证后] 预测事后分析 → 经验入库 → 下次 RAG 检索
```

---

> 📌 **Round 2 的核心思想**：从"更好的数据 → 更好的预测"升级到"更好的数据 + 历史反馈 + 个性化适配 → 更好的预测"。系统不再是一个静态的分析工具，而是一个会从错误中学习、会针对不同标的调整策略的"成长型分析师"。
