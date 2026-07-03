# 🌍 国际形势分析师改进方案 — Round 1

> **版本**: v1.0 | **日期**: 2026-07-03 | **对标**: 新闻分析师 Round 1 已完成

---

## 目录

1. [现状评估](#1-现状评估)
2. [改进总览](#2-改进总览)
3. [数据源增强](#3-数据源增强)
4. [标的上下文注入](#4-标的上下文注入)
5. [Agent 架构升级](#5-agent-架构升级)
6. [Prompt 工程优化](#6-prompt-工程优化)
7. [质量保障体系](#7-质量保障体系)
8. [自进化机制](#8-自进化机制)
9. [实施路线图](#9-实施路线图)
10. [附录：效果度量](#10-附录效果度量)

---

## 1. 现状评估

### 1.1 当前流程

```
MacroFetcher（东方财富DataCenter CPI/PMI/GDP + 新浪汇率）
       │
       ├── 实时数据：CPI, PMI, GDP, USDCNY, USDCNH  ← API 获取 ✅
       │
       ├── 参考值（硬编码）：LPR=3.0%, M2=~7.5%, DXY=~98, VIX=16, US10Y=4.4%  ← 可能严重过时 ❌
       │
       ▼
MacroAnalyst（单 pass LLM 推理，告知 LLM 用知识库补充）
       │
       ▼
AnalysisResult（方向 + 幅度 + 置信度 + reasoning）
```

### 1.2 优点（保留）

| 项目 | 说明 |
|------|------|
| ✅ 分析框架优秀 | Prompt 中 流动性→经济周期→汇率→地缘→标的传导 的链路清晰 |
| ✅ 实时/参考区分 | 明确标注 `realtime_fields` vs `reference_fields`，LLM 知道哪些该信 |
| ✅ CPI/PMI/GDP 实时 | 东方财富 DataCenter 免费、稳定 |
| ✅ 汇率实时 | 新浪 `hq.sinajs.cn` 提供 USDCNY/USDCNH |
| ✅ 宏观→标的要求 | Prompt 要求 LLM 必须说明"如何具体影响这个标的"，不泛泛而谈 |
| ✅ 异常处理 | 数据获取失败不阻塞，参考值兜底 |

### 1.3 核心问题（需要解决）

#### 🔴 严重问题

| # | 问题 | 现象 | 影响 |
|---|------|------|------|
| 1 | **关键参考值硬编码且过时** | DXY、VIX、美10Y、LPR、M2 全部硬编码 | 这些是美国货币政策的核心指标，用几个月前的值做宏观分析无异于盲人摸象 |
| 2 | **完全缺失美国宏观数据** | 无 Fed 利率、无 US CPI、无就业数据 | 全球最重要的宏观变量缺失，LLM 只能"猜" |
| 3 | **无标的信息** | LLM 不知道标的属于什么行业、市值大小 | 宏观→标的的传导链无法精确——银行和芯片对利率的敏感度完全不同 |
| 4 | **单 pass 推理** | 一次 prompt 完成宏观评估+标的传导 | 两个截然不同的任务（宏观判断 vs 标的映射）挤在一起，容易跳跃推理 |

#### 🟡 中等问题

| # | 问题 | 现象 | 影响 |
|---|------|------|------|
| 5 | **无市场区分** | A 股/港股/美股用同一套 prompt | A 股看政策、港股看流动性、美股看 Fed——宏观驱动因子完全不同 |
| 6 | **无 few-shot** | 纯文字描述，无正例/反例 | JSON 格式不稳定，reasoning 质量波动大 |
| 7 | **无地缘事件数据** | 完全依赖 LLM 训练数据中的地缘知识 | 今天刚发生的贸易摩擦、制裁升级等"新鲜"事件完全依赖 LLM 知识截止日期 |
| 8 | **数据新鲜度不可见** | LLM 不知道 CPI 是上个月的还是去年的 | 置信度应该随数据新鲜度变化 |
| 9 | **置信度未校准** | Prompt 说"不超过 0.7"，但没有具体锚定 | confidence=0.6 的含义不明确 |
| 10 | **无时间维度差异** | 预测 1 周和预测 1 季用同样的宏观数据 | 短期看事件冲击、长期看趋势——数据呈现应有区别 |

#### 🟢 轻微问题

| # | 问题 |
|---|------|
| 11 | 无数据源健康监控——某源挂了静默降级到参考值，用户不知道 |
| 12 | 无历史宏观环境对比——"当前的利率环境在历史上是什么水平"对判断很重要 |
| 13 | M2 和 LPR 其实可以通过东方财富 DataCenter 的其他接口获取（未探索） |

### 1.4 当前在系统中的权重

```
短期(1周): 12%（排第四，低于技术30%、新闻20%、综合研判16%）
中期(1月): 18%（排第三，仅低于基本面22%、技术20%）
长期(1季): 28%（排第二，仅低于基本面32%）
```

宏观分析师在**中长期预测中权重极高**（28%），但当前数据质量与其权重完全不匹配——最关键的 DXY/美债/VIX 都是硬编码参考值。**改进宏观数据的回报率可能是所有 Agent 中最高的**。

---

## 2. 改进总览

### 2.1 目标架构

```
                        ┌─────────────────────────────────────────────┐
                        │         🌐 宏观数据采集层（多源实时）         │
                        │                                             │
                        │  东方财富DC    FRED/新浪    Yahoo Finance    │
                        │  CPI/PMI/GDP  DXY/美10Y    VIX              │
                        │  LPR/M2(新)   汇率                          │
                        │       │           │            │            │
                        │       └───────────┼────────────┘            │
                        │                   ▼                         │
                        │         ┌──────────────────┐                │
                        │         │  数据新鲜度评分   │                │
                        │         │  + 源健康监控     │                │
                        │         └────────┬─────────┘                │
                        └──────────────────┼──────────────────────────┘
                                           │
                        ┌──────────────────▼──────────────────────────┐
                        │         🏷️ 标的上下文解析                    │
                        │                                             │
                        │  行业分类 → 宏观敏感因子映射                 │
                        │  市值规模 → 利率敏感度                       │
                        │  跨境业务 → 汇率影响评估                     │
                        └──────────────────┼──────────────────────────┘
                                           │
                        ┌──────────────────▼──────────────────────────┐
                        │       🧠 两步链式推理引擎                    │
                        │                                             │
                        │  Step 1: 宏观环境评估                       │
                        │    "当前全球宏观环境是什么样的？"             │
                        │    流动性 + 经济周期 + 汇率 + 地缘政治        │
                        │              │                               │
                        │  Step 2: 宏观→标的传导                      │
                        │    "这个宏观环境对这个具体标的意味着什么？"    │
                        │    行业敏感度 + 估值影响 + 盈利传导           │
                        │              │                               │
                        │  Step 3: 反思 + 置信度校准                   │
                        │    "我的分析有什么盲点？数据够不够？"          │
                        └──────────────────┬──────────────────────────┘
                                           │
                        ┌──────────────────▼──────────────────────────┐
                        │          📊 输出 + 自进化                    │
                        │                                             │
                        │  AnalysisResult → PredictionStore            │
                        │       │                                     │
                        │       └──→ 事后验证 → 宏观因子重要性分析     │
                        │                  → 置信度校准               │
                        └─────────────────────────────────────────────┘
```

### 2.2 改进维度一览

| 维度 | 改进项 | 优先级 | 预期收益 |
|------|--------|--------|---------|
| 📡 数据源 | DXY/VIX/US10Y 实时化 | 🔴 P0 | 从"盲猜"到"有据"——最重要 |
| 📡 数据源 | LPR/M2 实时化 | 🟡 P1 | 消除参考值硬编码 |
| 📡 数据源 | US Fed 利率/CPI 补充 | 🟡 P1 | 填补美国数据空白 |
| 🏷️ 上下文 | 行业+市值+跨境业务注入 | 🔴 P0 | 宏观→标的传导精准化 |
| 🧠 架构 | 两步 CoT（宏观评估→标的传导） | 🔴 P0 | 减少跳跃推理 |
| 📝 Prompt | Few-shot + 市场区分 + 置信度锚定 | 🟡 P1 | 输出质量稳定性 ↑ |
| 🔬 质量 | 数据新鲜度评分 | 🟡 P1 | LLM 知道该信多少 |
| 🔬 质量 | 输出一致性校验 | 🟡 P1 | 检测矛盾判断 |
| 🧬 自进化 | 宏观因子重要性追踪 | 🟢 P2 | 了解哪些指标最有预测力 |

---

## 3. 数据源增强

### 3.1 当前 vs 目标

| 指标 | 当前状态 | 目标状态 | 数据源 |
|------|---------|---------|--------|
| **中国 CPI** | ✅ 实时 | ✅ 保持不变 | 东方财富 DataCenter |
| **中国 PMI** | ✅ 实时 | ✅ 保持不变 | 东方财富 DataCenter |
| **中国 GDP** | ✅ 实时 | ✅ 保持不变 | 东方财富 DataCenter |
| **USDCNY/CNH** | ✅ 实时 | ✅ 保持不变 | 新浪 hq.sinajs.cn |
| **LPR 1Y** | ❌ 硬编码 3.0% | ✅ 实时 | 东方财富 DataCenter 新接口 |
| **M2 增速** | ❌ 硬编码 ~7.5% | ✅ 实时 | 东方财富 DataCenter 新接口 |
| **DXY** | ❌ 硬编码 ~98 | ✅ 实时 | FRED / Yahoo Finance |
| **VIX** | ❌ 硬编码 16 | ✅ 实时 | Yahoo Finance ^VIX |
| **美 10Y 收益率** | ❌ 硬编码 4.4% | ✅ 实时 | FRED DGS10 / Sina US bond |
| **Fed 利率** | ❌ 缺失 | ✅ 实时 | FRED FEDFUNDS |
| **US CPI** | ❌ 缺失 | 🟡 参考值 | 可爬取，优先度低于上面 |

### 3.2 新增数据源详解

#### 3.2.1 FRED API（美联储经济数据库）— DXY、美10Y、Fed 利率

FRED (Federal Reserve Economic Data) 提供**免费 API**，无需注册即可使用（注册后限额更高）。

```python
# 方案：FRED API
# 文档：https://fred.stlouisfed.org/docs/api/fred/

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
# 无需 API key 也可用（有限额），建议注册免费 key

SERIES = {
    "us_10y_yield": "DGS10",       # 10-Year Treasury Constant Maturity Rate
    "fed_funds_rate": "FEDFUNDS",  # Effective Federal Funds Rate
    "dxy": "DTWEXBGS",            # Trade Weighted U.S. Dollar Index (Broad)
    "us_cpi_yoy": "CPIAUCSL",     # Consumer Price Index (需计算 YoY)
}

async def fetch_fred(series_id: str) -> Optional[float]:
    """获取 FRED 最新数据点"""
    url = (
        f"{FRED_BASE}?series_id={series_id}&api_key={FRED_API_KEY}"
        f"&sort_order=desc&limit=1&file_type=json"
    )
    resp = requests.get(url, timeout=10)
    data = resp.json()
    observations = data.get("observations", [])
    if observations:
        return float(observations[0]["value"])
    return None
```

**备选方案（无需 API key）**：
- **DXY**：Yahoo Finance `DX-Y.NYB` 或 Sina `fx_susdidx`
- **美10Y**：Sina `gb_usr10yr` 或 CNBC 爬取
- **VIX**：Yahoo Finance `^VIX`（项目已有 yfinance）

#### 3.2.2 东方财富 DataCenter — LPR、M2

```python
# LPR 1年期（东方财富 DataCenter 有对应接口）
# reportName=RPT_ECONOMY_LPR, field=IR

# M2 增速（东方财富 DataCenter）
# reportName=RPT_ECONOMY_MONEYSUPPLY, field=M2_SAME
```

#### 3.2.3 VIX — Yahoo Finance

```python
import yfinance as yf

def fetch_vix() -> Optional[float]:
    """通过 yfinance 获取 VIX"""
    ticker = yf.Ticker("^VIX")
    # 获取最新价格
    hist = ticker.history(period="5d")
    if not hist.empty:
        return float(hist["Close"].iloc[-1])
    return None
```

#### 3.2.4 新浪美元指数

```python
# 新浪美元指数实时行情
# URL: https://hq.sinajs.cn/list=fx_susdidx
# 备选方案，如果 FRED 不可用
```

### 3.3 数据新鲜度评分

```python
@dataclass
class MacroDataV2:
    # ... 指标值 ...
    
    # 每个指标附带时间戳
    data_timestamps: dict = field(default_factory=dict)
    # e.g. {"cpi": "2026-06-15", "pmi": "2026-06-30", "us_10y": "2026-07-03"}
    
    def get_freshness_score(self) -> float:
        """
        综合数据新鲜度评分 (0.0 ~ 1.0)
        
        - 实时/当天: 1.0
        - 1 周内: 0.9
        - 1 月内: 0.7
        - 3 月内: 0.5
        - 更旧: 0.3
        - 硬编码参考值: 0.2
        """
```

### 3.4 数据源降级链

```
每个指标独立降级，不影响其他指标：

DXY:  FRED API → Sina fx_susdidx → Yahoo Finance DX-Y.NYB → 参考值
美10Y: FRED DGS10 → Sina gb_usr10yr → 参考值
VIX:  Yahoo Finance ^VIX → 参考值
LPR:  东方财富 DataCenter → 参考值
M2:   东方财富 DataCenter → 参考值
Fed利率: FRED FEDFUNDS → 参考值

CPI/PMI/GDP/汇率：保持现有链路不变
```

---

## 4. 标的上下文注入

### 4.1 为什么需要

当前 LLM 只知道标的代码（如 "0700"），不知道它是腾讯（互联网平台）还是汇丰（银行）。宏观因子对不同行业的传导链完全不同：

| 宏观因子 | 银行（如 0005 汇丰） | 互联网（如 0700 腾讯） | 出口制造（如 2331 李宁） |
|---------|---------------------|----------------------|------------------------|
| 利率上行 | **利好**（息差扩大） | **利空**（DCF 估值压缩） | 中性偏空 |
| 美元走强 | **利好**（美元资产增值） | 中性偏空（资金外流） | **利空**（汇兑损失） |
| PMI 下行 | 中性偏空（信贷需求降） | 中性 | **利空**（订单减少） |
| VIX 飙升 | 中性 | **利空**（风险偏好降） | **利空** |

### 4.2 实现方案

```python
# 新增: src/data/stock_context.py 或在 macro_fetcher.py 中扩展

# 行业→宏观敏感因子映射表
SECTOR_MACRO_SENSITIVITY = {
    "银行": {
        "rate_sensitive": 0.9,       # 对利率高度敏感
        "fx_sensitive": 0.7,         # 对汇率较敏感
        "cycle_sensitive": 0.6,      # 周期性中等
        "geopolitical_sensitive": 0.3,
        "notes": "利率上行利好息差，美元走强利好港股银行（挂钩美元）",
    },
    "互联网平台": {
        "rate_sensitive": 0.8,       # DCF 估值对利率敏感
        "fx_sensitive": 0.4,
        "cycle_sensitive": 0.5,
        "geopolitical_sensitive": 0.7,  # 中美关系影响大
        "notes": "高估值成长股，利率上行压缩估值；中美科技脱钩风险",
    },
    "消费": {
        "rate_sensitive": 0.3,
        "fx_sensitive": 0.3,
        "cycle_sensitive": 0.8,      # 消费随经济周期波动
        "geopolitical_sensitive": 0.4,
        "notes": "经济复苏利好可选消费，PMI/GDP 是关键指标",
    },
    # ... 更多行业
}

async def get_stock_macro_context(symbol: str, market: str) -> dict:
    """
    获取标的的宏观分析上下文
    
    1. 解析行业分类（腾讯API公司名 → 关键词匹配 → 行业）
    2. 返回该行业的宏观敏感因子
    3. 提供具体的传导链提示
    """
```

### 4.3 上下文输出示例

```json
{
  "stock": "0700",
  "company_name": "腾讯控股",
  "inferred_sector": "互联网平台",
  "market": "HK",
  "macro_sensitivity": {
    "rate_sensitive": 0.8,
    "fx_sensitive": 0.4,
    "cycle_sensitive": 0.5,
    "geopolitical_sensitive": 0.7
  },
  "transmission_hints": [
    "利率上行 → DCF估值压缩 → 高估值互联网首当其冲",
    "人民币升值 → 外资流入港股 → 腾讯作为权重股率先受益",
    "中美科技摩擦 → 可能影响海外游戏/云业务 → 关注政策信号",
    "PMI上行 → 广告主投放意愿增强 → 腾讯广告收入有望提升"
  ]
}
```

---

## 5. Agent 架构升级

### 5.1 当前：单 Pass

```
宏观数据 → [一个大 Prompt] → 结果
```

### 5.2 目标：两步 CoT + 标的上下文

```python
class MacroAnalystV2(BaseAgent):
    """升级版：两步链式推理 + 标的上下文"""
    
    async def analyze(self, data, context):
        # Step 1: 宏观环境评估（与标的无关）
        macro_assessment = await self._step_assess_macro(data, context)
        
        # Step 2: 宏观→标的传导（结合行业敏感度）
        stock_impact = await self._step_translate_to_stock(
            macro_assessment, data, context
        )
        
        # Step 3: 反思 + 置信度校准
        return await self._step_reflect_and_calibrate(
            stock_impact, data, context
        )
```

### 5.3 各步骤详解

#### Step 1: 宏观环境评估（Macro Environment Assessment）

**输入**: 实时+参考宏观数据  
**输出**: 结构化的宏观环境画像

```json
{
  "liquidity_environment": {
    "assessment": "中性偏宽松",
    "key_signal": "Fed 暂停加息，中国 LPR 维持低位",
    "direction_for_stocks": "温和利好",
    "confidence": 0.7
  },
  "economic_cycle": {
    "phase": "弱复苏",
    "key_signal": "PMI 连续3月在50以上但力度不强",
    "direction_for_stocks": "中性偏正面",
    "confidence": 0.6
  },
  "fx_outlook": {
    "rmb_trend": "震荡偏强",
    "dxy_trend": "偏弱",
    "direction_for_hk_stocks": "利好（外资流入）",
    "confidence": 0.55
  },
  "geopolitical_risk": {
    "level": "中等",
    "key_concern": "中美科技摩擦",
    "direction_for_stocks": "偏负面",
    "confidence": 0.5
  },
  "overall_macro_stance": "中性偏正面",
  "overall_confidence": 0.6
}
```

#### Step 2: 宏观→标的传导（Macro-to-Stock Translation）

**输入**: Step 1 的宏观画像 + 标的上下文（行业/市值/跨境业务）  
**输出**: 对该标的的最终判断

```json
{
  "transmission_chains": [
    {
      "macro_factor": "Fed 暂停加息 + 市场预期降息",
      "sector_sensitivity": "互联网平台对利率高度敏感(0.8)",
      "impact": "DCF估值模型分母下降 → 腾讯远期估值提升 → 利好",
      "magnitude_hint": "若10Y收益率从4.4%降至4.0%，腾讯公允价值可提升5-8%"
    }
  ],
  "direction": "bullish",
  "magnitude": {"min_pct": -3.0, "max_pct": 6.0},
  "confidence": 0.62,
  "reasoning": "...",
  "key_factors": [...],
  "risks": [...]
}
```

#### Step 3: 反思 + 校准

```
你现在是"宏观风险官"，请检查 Step 2 的分析：

1. 宏观判断依赖于哪些假设？（如"Fed 暂停加息"如果错了会怎样？）
2. 有没有被单一因子主导，忽略了其他因子？
3. 数据的时效性够吗？（CPI 是上月的，VIX 是即时的——权重应该不同）
4. 这个标的有没有特殊因素（如重大重组、停牌）使宏观分析不适用？
5. 历史上类似的宏观环境，该标的表现如何？
```

---

## 6. Prompt 工程优化

### 6.1 Few-shot 示例

```python
FEW_SHOT_EXAMPLES = """
## 输出示例

### 示例 1: 宏观环境利好互联网
**背景**: Fed 暂停加息，10Y 从 4.8% 降至 4.4%，人民币升值至 7.10，PMI 50.3
**标的**: 腾讯控股(0700.HK)，互联网平台，高估值成长股

**正确输出**:
```json
{
  "direction": "bullish",
  "magnitude": {"min_pct": -2.0, "max_pct": 6.0},
  "confidence": 0.62,
  "reasoning": "## 1) 流动性环境\\nFed暂停加息+市场定价9月降息，全球流动性边际宽松。美10Y从4.8%降至4.4%，对DCF估值的互联网平台是直接利好。\\n\\n## 2) 经济周期\\nPMI 50.3连续3月扩张，弱复苏格局。广告主投放信心回升→腾讯广告收入预期改善。\\n\\n## 3) 汇率与资本流动\\n人民币升值至7.10（从7.30回落），外资回流港股趋势明显。腾讯作为恒指权重股率先受益于被动资金流入。\\n\\n## 4) 地缘政治\\n中美科技摩擦仍存但无新升级。短期风险可控，但不排除突发性制裁→这是最大尾部风险。\\n\\n## 5) 宏观→腾讯传导\\n三个正向传导链：(a)利率下行→DCF估值提升；(b)PMI改善→广告收入恢复；(c)人民币升值→外资流入。综合判断短期看涨，幅度-2%~+6%。",
  "key_factors": ["Fed暂停加息，全球流动性边际宽松", "美10Y下行利好DCF估值", "人民币升值吸引外资流入港股"],
  "risks": ["中美科技摩擦突然升级", "Fed政策预期反转", "人民币汇率大幅波动"]
}
```

### 示例 2: 宏观逆风
**背景**: DXY 走强至 102，VIX 飙升到 25，美10Y 升至 4.8%
**标的**: 李宁(2331.HK)，消费/出口

**正确输出**:
```json
{
  "direction": "bearish",
  "magnitude": {"min_pct": -6.0, "max_pct": -1.0},
  "confidence": 0.58,
  "reasoning": "## 1) 流动性环境\\nDXY走强+美债收益率上行=全球流动性收紧信号。港股（尤其消费类）对流动性高度敏感，面临估值压缩。\\n\\n## 2) 风险偏好\\nVIX=25处于恐慌区间，资金从风险资产撤出。消费股（尤其高估值消费品）在避险环境中承压最重。\\n\\n## 3) 汇率影响\\nDXY走强意味着人民币贬值压力→对李宁的双重打击：(a)港元挂钩美元但港股资金外流；(b)人民币贬值利好出口但李宁以内销为主，无对冲。\\n\\n## 4) 综合判断\\n三个宏观因子同向利空，短期(-1周)看跌。但若VIX快速回落，跌幅会收窄。",
  "key_factors": ["DXY走强导致全球流动性收紧", "VIX飙升抑制风险偏好", "美10Y上行压缩消费股估值"],
  "risks": ["DXY可能快速回落（若美国经济数据转弱）", "公司层面利好可能对冲宏观利空"]
}
```
"""
```

### 6.2 市场区分附录

```python
A_SHARE_MACRO_APPENDIX = """
## ⚠️ A股宏观特色
- **政策信号权重最高**：中央经济工作会议、国常会、政治局会议的政策定调比经济数据本身更重要
- **"宽货币+紧信用"格局**：降息降准对A股的实际推动力经常被信用传导不畅抵消——关注社融数据
- **北向资金情绪指标**：外资流入/流出对A股短期方向影响极大，关注每日北向资金净额
- **IPO节奏**：证监会IPO审核速度是重要的流动性信号——加速=抽水，减速=呵护
"""

HK_SHARE_MACRO_APPENDIX = """
## ⚠️ 港股宏观特色
- **全球流动性决定方向**：港股=中国资产+美元定价，Fed政策对港股的影响甚至大于中国央行
- **南向资金是稳定器**：南向资金持续买入可对冲外资流出，关注港股通每日净流入
- **港汇(HKD)是先行指标**：港汇触及弱方兑换保证(7.85)时金管局干预→短期流动性收紧→港股承压
- **恒指权重集中**：腾讯+阿里+美团占恒指~25%，三者同步受宏观影响时指数效应放大
"""

US_SHARE_MACRO_APPENDIX = """
## ⚠️ 美股宏观特色
- **Fed是第一驱动力**：Fed政策（利率+缩表）是美股唯一最重要的宏观变量
- **就业数据>CPI**：市场对非农数据的即时反应往往比对CPI更剧烈（就业=经济健康度=盈利预期）
- **板块轮动**：利率下行→科技/成长跑赢；利率上行→价值/能源跑赢。宏观判断必须结合板块
- **企业回购**：美股最大的买方是企业自己，回购规模受利率和税率影响（低利率=更多回购）
"""
```

### 6.3 置信度锚定 + 数据新鲜度惩罚

```python
CONFIDENCE_ANCHORS_MACRO = """
## 置信度校准指引（宏观分析专用）

宏观分析的固有不确定性高于个股分析，confidence 上限应更低：

| confidence | 含义 | 适用场景 |
|------------|------|---------|
| 0.70-0.80 | 很强信号 | 多重宏观因子同向共振（如降息+PMI回升+人民币升值三箭齐发），且关键数据均为实时（非参考值） |
| 0.60-0.69 | 较强信号 | 主因子明确利好/利空，但有一两个次要因子反向，实时数据覆盖率 > 60% |
| 0.50-0.59 | 中等信号 | 宏观方向可判断，但数据有缺失或信号不够强。这是最常见的宏观分析置信度 |
| 0.40-0.49 | 较弱信号 | 宏观因子方向分散（多空交织），或多数关键数据为参考值（非实时） |
| 0.30-0.39 | 很弱信号 | 数据严重不足，依赖 LLM 知识库补充。**此时 magnitude 区间应加宽** |
| <0.30 | 无有效信号 | 无法做出有意义的宏观判断，direction 应设为 neutral |

### 额外校准因子
- 关键指标（DXY/美10Y/VIX）为实时数据：confidence 不调整
- 关键指标为参考值（硬编码/非实时）：confidence × 0.85
- 标的行业宏观敏感度低（如公用事业）：confidence 可适当提高（宏观噪音本身不重要）
- 标的行业宏观敏感度高（如银行、互联网）：confidence 适当降低（容错空间小）
"""
```

---

## 7. 质量保障体系

### 7.1 输出校验

```python
class MacroResultValidator:
    """宏观分析输出专项校验"""
    
    def validate(self, result, data, stock_context) -> list[str]:
        issues = []
        
        # 1. 宏观方向 vs 数据一致性
        # 如果利率下行+PMI上行但给出了 bearish → 需要特别说明理由
        if data.us_10y_yield < 4.3 and data.cn_pmi > 50:
            if result.direction == "bearish":
                issues.append("利率下行+PMI扩张但看跌——需要明确的逆向逻辑")
        
        # 2. 传导链是否具体到该标的
        if stock_context.get("company_name") not in result.reasoning:
            issues.append("reasoning 未提及标的公司名，宏观分析可能过于泛化")
        
        # 3. 置信度与数据质量一致性
        freshness = data.get("freshness_score", 0.5)
        if result.confidence > 0.65 and freshness < 0.5:
            issues.append(f"高置信度({result.confidence})但数据新鲜度低({freshness})")
        
        return issues
```

### 7.2 特殊情况处理

| 情况 | 处理 | confidence 上限 |
|------|------|----------------|
| 关键指标全实时 | 正常分析 | 0.80 |
| 关键指标 50%+ 参考值 | 正常分析，标注 | 0.60 |
| 关键指标全参考值 | 降低 confidence，加宽 magnitude | 0.45 |
| 宏观信号矛盾（利好利空各半） | 标注分歧，neutral | 0.50 |
| 地缘政治突发（VIX 飙升>25） | 标注"极端环境"，bearish 倾向 | 0.65 |
| 数据源全部不可用 | 降级 neutral，注明知库分析 | 0.30 |

---

## 8. 自进化机制

### 8.1 宏观因子重要性追踪

不同标的对不同宏观因子的敏感度不同。通过回测验证数据积累，可以量化这种敏感度：

```python
# 在 PredictionStore 中记录每次预测时的宏观环境
# 事后分析：当 DXY 上升 1% 时，哪些标的的预测准确率最高/最低？
# → 自动发现该标的对汇率的实际敏感度，优化 prompt 中的传导链
```

### 8.2 置信度校准

与新闻分析师相同的机制，基于 `PredictionStore` 中的历史验证数据：

```python
from src.core.confidence_calibrator import ConfidenceCalibrator
# 直接复用新闻分析师的校准器
```

---

## 9. 实施路线图

### Phase A: 快速见效（1 周）🔴 P0

| 任务 | 文件 | 工作量 |
|------|------|--------|
| ① DXY/VIX/US10Y 实时化（FRED + Yahoo Finance） | `src/data/macro_fetcher.py` | 1.5 天 |
| ② LPR/M2 实时化（东方财富 DataCenter 新接口） | `src/data/macro_fetcher.py` | 0.5 天 |
| ③ 数据新鲜度评分 | `src/data/macro_fetcher.py` | 0.5 天 |
| ④ 标的上下文解析（行业+宏观敏感因子） | 新增 `src/data/stock_context.py` | 1 天 |
| ⑤ Few-shot + 市场区分 + 置信度锚定 prompt | `src/prompts/macro_prompts.py` | 0.5 天 |
| ⑥ 输出校验增强 | `src/agents/macro_analyst.py` | 0.5 天 |
| ⑦ 测试 | `tests/test_macro_analyst_v2.py` | 1 天 |

**预期效果**：
- DXY/VIX/US10Y 从"硬编码猜测"变为"实时数据"
- 标的上下文使宏观→标的传导具体化
- confidence 有据可依

### Phase B: 架构升级（1-2 周）🟡 P1

| 任务 | 文件 | 工作量 |
|------|------|--------|
| ① 两步 CoT 推理（宏观评估→标的传导+反思） | `src/agents/macro_analyst.py` | 1.5 天 |
| ② 地缘政治事件采集（贸易摩擦/制裁新闻） | 新增 `src/data/geopolitical_fetcher.py` | 1 天 |
| ③ 美联储政策追踪（FedWatch 概率） | `macro_fetcher.py` 扩展 | 0.5 天 |
| ④ 置信度校准集成 | 复用 `confidence_calibrator.py` | 0.5 天 |

### Phase C: 自进化（Phase B 后）🟢 P2

| 任务 | 说明 |
|------|------|
| 宏观因子重要性追踪 | 回测分析：哪些指标预测力最强 |
| 自适应宏观权重 | 在 aggregator 中根据近期准确率调整宏观权重 |
| 多市场宏观对比 | 同时展示中美宏观分歧 |
| 历史相似宏观环境检索 | RAG：历史上类似的宏观组合后市表现 |

---

## 10. 附录：效果度量

### 10.1 关键指标

| 指标 | 当前（估算） | Phase A 目标 | 度量方式 |
|------|------------|-------------|---------|
| 方向准确率（中期） | ~50-55% | ≥58% | PredictionStore |
| 方向准确率（长期） | ~50-55% | ≥60% | PredictionStore |
| 幅度命中率 | ~35% | ≥42% | PredictionStore |
| 实时数据覆盖率 | ~35%（5/14指标） | ≥70%（10/14指标） | 日志统计 |
| 置信度校准误差 | 未知 | ≤0.15 | | 预测置信度 - 实际准确率 | |
| JSON 解析成功率 | ~90% | ≥98% | 日志统计 |

### 10.2 关键成功标准

> **Phase A 是否成功的核心判断标准**：
> 1. DXY、VIX、美10Y 不再使用硬编码值——每次分析获取**当天实时数据**
> 2. LLM 能根据标的的**行业和规模**给出**具体**的宏观传导链，而非泛泛而谈
> 3. 数据新鲜度可见——LLM 知道 CPI 是上个月的、VIX 是今天的，权重不同

---

## 附录 A：文件变更清单

### 需要修改的文件

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `src/data/macro_fetcher.py` | 重构 | 多源采集 + 数据新鲜度 + 消除硬编码 |
| `src/agents/macro_analyst.py` | 重构 | 两步 CoT + 上下文注入 + 校验 |
| `src/prompts/macro_prompts.py` | 重写 | Few-shot + 市场区分 + 置信度锚定 |

### 需要新增的文件

| 文件 | 说明 |
|------|------|
| `src/data/stock_context.py` | 行业识别 → 宏观敏感因子映射 |
| `src/data/geopolitical_fetcher.py` | 地缘事件采集（Phase B） |
| `tests/test_macro_analyst_v2.py` | 宏观分析师 v2 测试 |

### 不需要修改的文件

- `src/core/base_agent.py` — 接口不变
- `src/core/orchestrator.py` — 不变
- `src/core/result.py` — `AnalysisResult` 不变
- `src/core/confidence_calibrator.py` — 直接复用
- `config/agent_config.yaml` — 权重暂不变

---

## 附录 B：风险与应对

| 风险 | 概率 | 应对 |
|------|------|------|
| FRED API 国内访问不稳定 | 中 | Sina/Yahoo Finance 作为备选降级链 |
| Yahoo Finance 频繁限流 | 中 | VIX 每日只查一次，缓存结果 |
| 行业识别不准确（如"美团"是科技还是消费？） | 中 | 模糊匹配比硬分类更好——给出多个可能的行业+各自的敏感度 |
| CoT 增加 LLM 调用成本 | 中 | Step 1（宏观评估）可用更短 prompt，Step 2 才用完整分析 |
| 宏观预测本身不确定性高 | 高 | 接受现实——置信度上限 0.8，magnitude 区间更宽 |

---

> 📌 **核心原则**：宏观分析师的竞争力不在于"收集了多少指标"，而在于 **(1) 关键指标是否实时（DXY/VIX/美10Y 绝不能是几个月前的硬编码），(2) 能否将宏观环境精确地翻译为这个具体标的的影响（而非泛泛的"利好股市"）**。
