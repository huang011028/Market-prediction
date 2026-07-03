# 🌍 国际形势分析师改进方案 — Round 2

> **版本**: v1.0 | **日期**: 2026-07-03 | **前置**: IMPROVEMENT_MACRO_ANALYST_ROUND1.md（已完成 Phase A）

---

## 目录

1. [Round 1 (Phase A) 回顾](#1-round-1-phase-a-回顾)
2. [Round 2 改进总览](#2-round-2-改进总览)
3. [美国数据源突破](#3-美国数据源突破)
4. [地缘政治事件采集](#4-地缘政治事件采集)
5. [宏观因子重要性追踪](#5-宏观因子重要性追踪)
6. [置信度校准升级](#6-置信度校准升级)
7. [实施路线图](#7-实施路线图)

---

## 1. Round 1 (Phase A) 回顾

### 1.1 已完成内容

| 模块 | 文件 | 核心成果 |
|------|------|---------|
| 📡 数据源实时化 | `macro_fetcher.py` (重写) | LPR/M2 通过 akshare 实时获取；DXY/VIX/US10Y/Fed 多源尝试（YF→FRED→参考值）；数据新鲜度评分 |
| 🏷️ 标的上下文 | `stock_context.py` (新建) | 12 个行业分类 + 宏观敏感因子映射 + 行业级传导链提示 |
| 🧠 两步 CoT | `macro_analyst.py` (重写) | Step 1 宏观环境评估 + Step 2 宏观→标的传导（利用行业敏感度） |
| 📝 Prompt | `macro_prompts.py` (重写) | 2 个 few-shot 示例 + 宏观专用置信度锚定 + A/HK/US 市场附录 + 数据新鲜度指引 |
| 🔬 校验 | `macro_analyst.py` (_validate_macro_result) | 行业是否被提及、敏感度与传导链一致性、置信度 vs 数据质量 |
| 🧪 测试 | `test_macro_analyst_v2.py` | 20 个测试（15 单元 + 5 集成） |

### 1.2 关键数据对比

| 指标 | Phase A 前 | Phase A 后 |
|------|-----------|-----------|
| **LPR 1Y** | 硬编码 `3.0%` (无日期) | **实时 3.0% (2026-06-22)** ✅ |
| **M2 增速** | 硬编码 `~7.5%` (无日期) | **实时从 akshare 获取** ✅ |
| **DXY** | 硬编码 `~98` | 多源尝试 → 参考值（标注日期）⚠️ |
| **VIX** | 硬编码 `16` | 多源尝试 → 参考值（标注日期）⚠️ |
| **美10Y** | 硬编码 `4.4%` | 多源尝试 → 参考值（标注日期）⚠️ |
| **Fed利率** | ❌ 完全缺失 | 多源尝试 → 参考值（标注日期）⚠️ |
| **数据新鲜度** | ❌ 无此概念 | **每个指标标注新鲜度 + 综合评分** ✅ |
| **行业敏感度** | ❌ 无 | **12 行业 + 传导链提示** ✅ |

### 1.3 Round 1 遗留问题

| 问题 | 现状 | 优先级 |
|------|------|--------|
| **美国数据仍然是参考值** | yfinance 频繁限流（环境网络限制），FRED 需 API key | 🔴 P0 |
| **地缘政治事件无数据** | 完全依赖 LLM 训练数据中的过时地缘知识 | 🟡 P1 |
| **置信度校准未接入历史** | 校验规则有了，但未读 PredictionStore | 🟡 P1 |
| **宏观因子预测力未知** | 不知道哪些宏观指标在实际预测中最有用 | 🟢 P2 |

---

## 2. Round 2 改进总览

### 2.1 核心主题：**突破美国数据 + 自进化**

| 维度 | Round 1 状态 | Round 2 目标 |
|------|------------|-------------|
| 📡 美国数据 | 4 项全为参考值 | 至少 2/4 项实时化 |
| 🌍 地缘事件 | 完全依赖 LLM 记忆 | 事件采集+情绪标注 |
| 🧬 自进化 | 无 | 宏观因子重要性追踪 |
| 📊 校准 | 规则校验 | 历史数据驱动校准 |

---

## 3. 美国数据源突破

### 3.1 问题

Round 1 中 DXY/VIX/US10Y/Fed利率 四项全部降级到参考值，因为：
- yfinance 被环境限流（`Too Many Requests`）
- FRED API 未配置 key
- 新浪不支持这些国际品种
- CBOE/MarketWatch/CNBC 爬取不稳定

### 3.2 Round 2 方案

#### 方案 A: FRED API 注册（推荐）

```bash
# 免费注册: https://fred.stlouisfed.org/docs/api/api_key.html
# 限额: 120 requests/minute, 不需要信用卡

# .env 新增:
FRED_API_KEY=your-free-fred-api-key
```

注册后即可获取：
- `DGS10`: 10-Year Treasury
- `DTWEXBGS`: Trade Weighted Dollar Index (Broad) — 替代 DXY
- `FEDFUNDS`: Federal Funds Rate
- `VIXCLS`: VIX (CBOE 日数据)

**优势**: 免费、稳定、官方数据  
**工作量**: 1 天（已预留接口，只需填 key）

#### 方案 B: Alpha Vantage 免费层

```bash
# 免费注册: https://www.alphavantage.co/support/#api-key
# 限额: 25 requests/day

# .env 新增:
ALPHA_VANTAGE_API_KEY=your-free-key
```

Alpha Vantage 提供：
- `TREASURY_YIELD`: US Treasury yields (10Y/2Y/30Y)
- `FX_DAILY`: DXY 近似（EUR/USD + 计算）
- `VIX`: 通过 `TIME_SERIES_INTRADAY`

**优势**: 也是免费、JSON 格式  
**劣势**: 25次/天限额较紧

#### 方案 C: 新浪港股通数据间接推算

```python
# 通过港股通资金流、港汇(HKD)等可获取的指标间接推算 DXY 走势
# 例如: HKD 弱方保证 → DXY 走强概率大
# 虽然不精确，但比纯参考值好
```

### 3.3 推荐实施路径

```
Phase C 优先级:
1. 注册 FRED API key → 填入 .env → DXY/US10Y/Fed利率 实时化 (1天)
2. VIX 尝试 Yahoo Finance 重试策略优化（增加重试间隔 + 随机延迟）
3. 降级链: FRED → Alpha Vantage → 参考值
```

---

## 4. 地缘政治事件采集

### 4.1 为什么需要

当前宏观分析的地缘政治部分完全依赖 LLM 训练数据。如果今天发生了新的贸易摩擦、制裁升级，LLM 不知道。

### 4.2 方案

新增轻量级地缘事件采集器，从新闻标题中提取地缘信号：

```python
# 新增: src/data/geopolitical_fetcher.py

GEOPOLITICAL_KEYWORDS = {
    "trade_war": ["关税", "贸易战", "制裁", "实体清单", "tariff", "sanction"],
    "military": ["冲突", "军事", "战争", "演习", "military", "conflict"],
    "tech_decoupling": ["科技脱钩", "芯片禁令", "技术限制", "出口管制"],
    "supply_chain": ["供应链", "脱钩", "回流", "reshoring"],
    "brexit_style": ["退欧", "公投", "脱欧", "referendum"],
}

async def fetch_geopolitical_signals(days: int = 30) -> list[dict]:
    """
    从新闻源中提取地缘政治信号
    
    复用已有的 eastmoney/sina 新闻接口，
    用地缘关键词过滤 + 简单情绪标注
    """
```

### 4.3 输出示例

```json
{
  "recent_geopolitical_events": [
    {
      "type": "tech_decoupling",
      "title": "美国商务部新增12家中国实体至出口管制清单",
      "date": "2026-07-01",
      "sentiment": "negative",
      "relevance": "high"
    }
  ],
  "geopolitical_risk_level": "中等偏高",
  "key_themes": ["中美科技脱扣", "芯片出口管制"],
  "trend": "escalating"
}
```

---

## 5. 宏观因子重要性追踪

### 5.1 核心思路

不同标的对不同宏观因子的**实际**敏感度可能和我们的预设不同。通过回测数据学习：

```
历史数据:
- 预测时 DXY=102, PMI=50.3, 给 bullish
- 事后验证: 方向错误（bearish）
- 分析: DXY 走强对这只股的负面影响超过 PMI 扩张的正面影响
→ 结论: 这只股对 DXY 的实际敏感度 > 预设值
```

### 5.2 实现方案

```python
# 扩展 PredictionStore schema
# 在 agent_results 中记录宏观环境快照

# 事后分析脚本
def analyze_macro_factor_importance(target: str, lookback: int = 20):
    """
    分析过去 N 次预测中，哪些宏观因子与预测准确率最相关
    
    返回:
    {
        "DXY": {"correlation_with_accuracy": 0.65, "importance": "high"},
        "PMI": {"correlation_with_accuracy": 0.30, "importance": "medium"},
        "VIX": {"correlation_with_accuracy": 0.10, "importance": "low"},
    }
    """
```

### 5.3 预期产出

- 自动发现"对这只股票，DXY 比 PMI 重要 3 倍"
- 反馈到 stock_context 的敏感度映射 → 自适应优化
- 在 aggregator 中据此调整宏观 Agent 的权重

---

## 6. 置信度校准升级

### 6.1 Round 1 的校准机制

仅规则校验（如"高置信度+低数据新鲜度→警告"），未读取历史验证数据。

### 6.2 Round 2 升级

直接复用新闻分析师的 `ConfidenceCalibrator`：

```python
from src.core.confidence_calibrator import ConfidenceCalibrator

class MacroAnalyst(BaseAgent):
    def __init__(self, llm, prediction_store=None):
        ...
        self._calibrator = ConfidenceCalibrator(prediction_store) if prediction_store else None
    
    def _calibrate_confidence(self, raw_conf, data_freshness):
        if self._calibrator:
            calibrated = self._calibrator.calibrate(
                self.name, raw_conf, data_quality=data_freshness
            )
            return calibrated
        return raw_conf
```

---

## 7. 实施路线图

### Phase C: 美国数据突破 + 地缘事件（1-2 周）🟡 P1

| 任务 | 工作量 | 依赖 |
|------|--------|------|
| ① FRED API key 注册 + 接入 | 0.5 天 | 免费注册 |
| ② yfinance 重试策略优化（指数退避+随机延迟） | 0.5 天 | — |
| ③ 降级链完善（FRED→YF→AlphaVantage→参考值） | 0.5 天 | ①② |
| ④ 地缘政治事件采集器 | 1.5 天 | 复用新闻源 |
| ⑤ 测试 + 验证 | 0.5 天 | — |

**预期效果**: 美国数据至少 2/4 实时化；地缘事件不再纯靠 LLM 记忆

### Phase D: 自进化（2-3 周）🟢 P2

| 任务 | 工作量 |
|------|--------|
| ① PredictionStore schema 扩展（宏观快照） | 0.5 天 |
| ② 宏观因子重要性分析脚本 | 1.5 天 |
| ③ 历史校准器集成 | 0.5 天 |
| ④ 自适应敏感度反馈到 stock_context | 1 天 |

---

## 附录 A: Round 2 文件变更清单

### 需要修改

| 文件 | 变更 |
|------|------|
| `src/data/macro_fetcher.py` | +FRED API 集成（已预留接口）、+yfinance 重试优化 |
| `src/agents/macro_analyst.py` | +置信度校准器集成 |
| `.env.example` | +FRED_API_KEY、+ALPHA_VANTAGE_API_KEY |

### 需要新增

| 文件 | 说明 |
|------|------|
| `src/data/geopolitical_fetcher.py` | 地缘事件采集 |

---

## 附录 B: Phase A 实施总结

### 变更统计

| 操作 | 文件 | 说明 |
|------|------|------|
| 🔄 重写 | `src/data/macro_fetcher.py` | v2: LPR/M2实时+DXY/VIX/US10Y多源+新鲜度评分 |
| ✨ 新建 | `src/data/stock_context.py` | 12行业→宏观敏感因子+传导链 |
| 🔄 重写 | `src/prompts/macro_prompts.py` | Few-shot+置信度锚定+市场附录 |
| 🔄 重写 | `src/agents/macro_analyst.py` | 两步CoT+上下文注入+校验 |
| ✨ 新建 | `tests/test_macro_analyst_v2.py` | 20个测试 |

### 测试覆盖

```
test_macro_analyst_v2.py: 20 tests
  5x TestMacroFetcherV2 (集成，标记 slow)
  8x TestStockContext (单元)
  7x TestMacroPrompts (单元)

所有非 slow 测试: 15 passed ✅
全量回归 (含 news v2): 62 passed ✅
```

---

> 📌 **Round 2 核心目标**: 把"美国数据全参考值"的临时状态变为"至少 Fed利率+美10Y 实时化"，同时引入地缘事件采集和宏观因子自进化。FRED API 免费注册是最高 ROI 的下一步——1 天工作量换取 3 项关键指标的实时化。
