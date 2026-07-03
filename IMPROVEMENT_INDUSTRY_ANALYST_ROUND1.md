# 🏭 行业对比分析师改进方案 — Round 1

> **版本**: v1.0 | **日期**: 2026-07-03 | **对标**: 新闻分析师 Round 1-2、宏观/公司前景分析师 Round 1 已完成

---

## 目录

1. [现状评估](#1-现状评估)
2. [改进总览](#2-改进总览)
3. [数据源增强](#3-数据源增强)
4. [行业数据处理管线](#4-行业数据处理管线)
5. [Agent 架构升级](#5-agent-架构升级)
6. [Prompt 工程优化](#6-prompt-工程优化)
7. [自进化机制](#7-自进化机制)
8. [质量保障体系](#8-质量保障体系)
9. [实施路线图](#9-实施路线图)
10. [附录：效果度量](#10-附录效果度量)

---

## 1. 现状评估

### 1.1 当前流程

```
┌──────────────────┐     ┌─────────────────┐     ┌──────────────┐
│ IndustryFetcher  │ ──▶ │  单次 LLM 推理   │ ──▶ │ AnalysisResult│
│                  │     │  (一次 prompt)   │     │              │
│ A股: PE(腾讯) +  │     │                 │     │              │
│   akshare财务 +   │     │                 │     │              │
│   硬编码行业参考   │     │                 │     │              │
│ HK: PE(腾讯) +   │     │                 │     │              │
│   财务几乎无数据   │     │                 │     │              │
└──────────────────┘     └─────────────────┘     └──────────────┘
```

**一句话描述当前流程**：获取标的PE + 硬编码行业参考值 → 让LLM做对比分析 → 输出结果。

### 1.2 优点（保留）

| 项目 | 说明 |
|------|------|
| ✅ 结构清晰 | 继承 BaseAgent，与其他 Agent 风格一致 |
| ✅ 数据组织合理 | `to_agent_dict()` 输出 stock_metrics + industry_average + comparison 三块 |
| ✅ 内置基础对比逻辑 | PE/PB/ROE 的 vs_industry 文字描述是自动生成的，不需要 LLM 算 |
| ✅ 行业分类有已知映射 | `KNOWN_INDUSTRIES` 覆盖 28 个常用标的，API 失败时可用 |
| ✅ 异常处理 | 数据获取失败有 fallback |
| ✅ 分析框架系统 | Prompt 覆盖估值对比→盈利对比→行业景气→综合性价比 |

### 1.3 核心问题（需要解决）

#### 🔴 严重问题

| # | 问题 | 现象 | 影响 |
|---|------|------|------|
| 1 | **行业参考值全是硬编码常量** | `INDUSTRY_REF` 字典仅 15 个行业，且数值可能严重过时 | 行业平均PE/PB是2023-2024年的经验值，市场环境已变，LLM基于过时数据做判断 |
| 2 | **港股/美股行业数据几乎为零** | `_fetch_industry_data` 只处理 A 股，港股/美股直接跳过 | 这两类标的的行业对比分析师基本"空转"，置信度长期 40-50% |
| 3 | **行业覆盖极窄** | 仅 15 个行业 + 28 个已知标的映射 | 大量标的无行业分类（`_find_industry` 返回 None）→ 行业数据全部缺失 |
| 4 | **无行业景气度数据** | 只有参考的 PE/PB/ROE，无行业近期涨跌幅、资金流向 | 无法判断"行业当前处于什么周期阶段"——这是行业分析的核心 |
| 5 | **无实际行业内公司数据** | 行业数据只是 3 个数字（PE/PB/ROE），没有具体公司列表和对应指标 | LLM 无法判断"这个公司在行业中排第几" |

#### 🟡 中等问题

| # | 问题 | 现象 | 影响 |
|---|------|------|------|
| 6 | **单 pass 推理** | 一次 prompt 完成估值对比+盈利分析+行业景气 | 三个不同维度的任务挤在一起，容易遗漏 |
| 7 | **无 few-shot 示例** | Prompt 纯文字描述 | LLM 对"好的行业分析输出"缺乏具体参照 |
| 8 | **行业分类方法脆弱** | akshare API 逐个尝试银行/白酒 → 超时风险高 | 大量 API 调用导致 16s+ 的采集耗时 |
| 9 | **置信度未校准** | confidence 的含义不明确 | 不同市场的置信度应该不同（A股数据多、港股数据少） |
| 10 | **无自评/反思** | 输出完就结束 | 没有"行业分析有什么盲点"的考量 |
| 11 | **与基本面分析师数据不打通** | 两个 Agent 独立获取 PE/财务数据 | 重复采集、数据不一致风险 |

#### 🟢 轻微问题

| # | 问题 | 现象 | 影响 |
|---|------|------|------|
| 12 | 无行业轮动判断 | 不知道当前资金在流入还是流出这个行业 | 错过中期重要的风格切换信号 |
| 13 | 无行业催化剂日历 | 不知道近期是否有行业性事件（政策、展会等） | 信息盲区 |
| 14 | 行业名称不统一 | 不同数据源的行业命名可能不一致 | 映射失败 |

### 1.4 当前在系统中的权重与现状

```
Agent 权重（短期）：10%（排最低）
Agent 权重（中期）：15%（排第四）
Agent 权重（长期）：18%（排第三）
```

从样本报告看：

> "行业对比分析师：中性（-3%~+3%）。行业对比数据严重缺失，仅依赖自身历史估值判断下行风险有限但缺乏上行催化，给出低置信度中性判断。"

置信度 45% 的中性判断，本质上在说"我不知道"。**行业对比分析师的核心问题不是能力不够，而是数据严重不足**——巧妇难为无米之炊。

---

## 2. 改进总览

### 2.1 目标架构

```
                    ┌──────────────────────────────────────────┐
                    │        🌐 行业数据采集层                  │
                    │                                          │
                    │  东方财富行业  │  同花顺行业  │  申万行业  │
                    │  (实时行业PE) │  (成分股)   │ (标准分类) │
                    │  东方财富板块  │  腾讯行情   │            │
                    │  (涨跌幅/资金) │ (港股补充)  │            │
                    │      │            │            │          │
                    │      └────────────┼────────────┘          │
                    │           ▼       ▼       ▼              │
                    │      ┌──────────────────────┐            │
                    │      │   行业分类确定        │            │
                    │      │   (多源 + 模糊匹配)  │            │
                    │      └──────────┬───────────┘            │
                    └─────────────────┼────────────────────────┘
                                      │
                    ┌─────────────────▼────────────────────────┐
                    │      🔧 行业数据处理管线                 │
                    │                                          │
                    │  ① 行业平均估值计算（成分股加权）          │
                    │  ② 行业排名分位（标的在行业中排第几）      │
                    │  ③ 行业趋势判断（涨跌幅/资金流向）         │
                    │  ④ 行业周期定位（复苏/繁荣/衰退/萧条）     │
                    │  ⑤ 行业催化剂识别（政策/事件）            │
                    │  ⑥ 数据质量评估                          │
                    └─────────────────┬────────────────────────┘
                                      │
                    ┌─────────────────▼────────────────────────┐
                    │      🧠 多步推理引擎                     │
                    │                                          │
                    │  Step 1: 定位 ("公司在行业中处于什么位置？")│
                    │  Step 2: 判断 ("这个位置意味着什么？")     │
                    │  Step 3: 催化+风险 ("行业催化剂/风险是什么？")│
                    │  Step 4: 综合判断 + 反思                 │
                    │  Step 5: 置信度校准                     │
                    └─────────────────┬────────────────────────┘
                                      │
                    ┌─────────────────▼────────────────────────┐
                    │      📊 输出 + 自进化反馈                │
                    └──────────────────────────────────────────┘
```

### 2.2 改进维度一览

| 维度 | 改进项 | 优先级 | 预期收益 |
|------|--------|--------|---------|
| 📡 数据源 | 实时行业平均PE（东方财富行业板块API） | 🔴 P0 | 替代硬编码常量，动态反映市场 |
| 📡 数据源 | 行业成分股数据（获取行业内公司列表+估值） | 🔴 P0 | 从"3个数字"升级为"完整的行业画像" |
| 📡 数据源 | 行业涨跌幅/资金流向（东方财富板块行情） | 🔴 P0 | 新增行业趋势维度 |
| 📡 数据源 | 港股行业分类 | 🟡 P1 | 港股行业对比从"互联网"一句话判断升级为有数据 |
| 📡 数据源 | 申万/证监会行业标准分类 | 🟡 P1 | 统一的行业命名，覆盖更广 |
| 🔧 预处理 | 行业排名分位计算 | 🔴 P0 | 量化"公司在行业中排第几" |
| 🔧 预处理 | 行业周期判断（量化规则） | 🟡 P1 | 繁荣/衰退阶段的自动判断 |
| 🧠 架构 | 三步 CoT 推理 | 🔴 P0 | 推理深度 ↑，可解释性 ↑ |
| 🧠 架构 | 反思环节 | 🟡 P1 | 识别盲点 |
| 📝 Prompt | Few-shot 示例 + 置信度锚定 | 🔴 P0 | 输出格式稳定性 ↑，置信度更诚实 |
| 📝 Prompt | 行业类型区分（周期/成长/防御） | 🟡 P1 | 不同行业的分析框架不同 |
| 🔬 质量 | 输出校验增强 | 🟡 P1 | 检测逻辑矛盾 |
| 🔬 质量 | 行业数据质量标注 | 🟡 P1 | confidence 与数据质量挂钩 |
| 🧬 自进化 | 准确率追踪 + 行业分桶 | 🟡 P1 | 哪个行业判断更准？ |
| 🧬 自进化 | 行业参考值动态更新 | 🟢 P2 | 自动更新行业均值 |

---

## 3. 数据源增强

### 3.1 当前 vs 目标

| 维度 | 当前 | 目标 |
|------|------|------|
| 行业PE/PB | 硬编码常量（15个行业） | 实时获取（东方财富/同花顺行业板块） |
| 行业成分股 | 无 | 获取行业内所有公司 + PE/ROE |
| 行业涨跌幅 | 无 | 5日/20日/60日行业指数涨跌幅 |
| 行业资金流向 | 无 | 主力净流入方向（可选） |
| 行业分类 | 15个大类+28个已知标的 | 申万二级行业（约100+） |
| 港股行业 | 硬编码"互联网" | 东方财富港股行业分类 |
| 行业数量 | 15个（已知常量） | 根据标的动态获取 |

### 3.2 新增数据源

#### 3.2.1 东方财富行业板块 API（A股 主力数据源）

```python
# 方案：东方财富行业板块行情
# ak.stock_board_industry_name_em()  # 获取所有行业板块名称列表
# ak.stock_board_industry_cons_em(symbol="银行")  # 获取行业内成分股
# ak.stock_board_industry_hist_em(symbol="银行", start_date="20240101")  # 行业历史行情

import akshare as ak

async def fetch_industry_data_em(industry_name: str) -> dict:
    """
    获取行业板块数据
    
    Returns:
    {
        "industry_name": "银行",
        "stock_count": 42,
        "avg_pe": 5.8,        # 从成分股计算
        "avg_pb": 0.65,
        "avg_roe": 10.5,
        "pe_median": 5.5,
        "change_5d_pct": 1.2,
        "change_20d_pct": -0.5,
        "change_60d_pct": 3.8,
        "peers": [  # 前10大成分股
            {"code": "601398", "name": "工商银行", "pe": 5.2, "pb": 0.6, "roe": 11.0, "weight_pct": 15.2},
            ...
        ]
    }
    """
    # 获取行业板块成分股列表
    df = ak.stock_board_industry_cons_em(symbol=industry_name)
    
    if df is None or df.empty:
        return None
    
    # 从成分股数据中提取估值信息
    # DataFrame 列: 代码, 名称, 最新价, 涨跌幅, 市盈率-动态, 市净率, ...
    pe_values = []
    pb_values = []
    peer_list = []
    
    for _, row in df.head(20).iterrows():  # 取前20
        pe = safe_float(row.get("市盈率-动态"))
        pb = safe_float(row.get("市净率"))
        if pe and pe > 0: pe_values.append(pe)
        if pb and pb > 0: pb_values.append(pb)
        peer_list.append({
            "code": row.get("代码"),
            "name": row.get("名称"),
            "pe": pe,
            "pb": pb,
            "change_pct": safe_float(row.get("涨跌幅")),
        })
    
    return {
        "stock_count": len(df),
        "avg_pe": sum(pe_values) / len(pe_values) if pe_values else None,
        "pe_median": sorted(pe_values)[len(pe_values)//2] if pe_values else None,
        "avg_pb": sum(pb_values) / len(pb_values) if pb_values else None,
        "pb_median": sorted(pb_values)[len(pb_values)//2] if pb_values else None,
        "peers": peer_list,
    }
```

- **优势**：免费、实时、数据丰富（自带成分股列表+估值）
- **劣势**：akshare 部分接口有超时风险，需加短超时
- **价值**：这是**最关键的改进**——从硬编码常量升级为实时数据

#### 3.2.2 行业历史行情 — 趋势判断

```python
async def fetch_industry_trend(industry_name: str) -> dict:
    """
    获取行业历史行情，计算趋势
    """
    # 获取近60日行业指数行情
    start = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")
    df = ak.stock_board_industry_hist_em(
        symbol=industry_name, 
        start_date=start,
        period="日k",
    )
    
    if df is None or df.empty:
        return {"trend": "unknown"}
    
    closes = df["收盘"].tolist()
    
    return {
        "change_5d": (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 else None,
        "change_20d": (closes[-1] / closes[-21] - 1) * 100 if len(closes) >= 21 else None,
        "change_60d": (closes[-1] / closes[-61] - 1) * 100 if len(closes) >= 61 else None,
        "ma5_above_ma20": None,  # 均线趋势
        "trend": classify_trend(closes),  # "uptrend" / "downtrend" / "range"
    }
```

#### 3.2.3 港股行业分类

```python
async def fetch_industry_hk(symbol: str) -> dict:
    """
    港股行业分类方案
    
    方案1: 从 yfinance info 中获取 sector/industry
    方案2: 东方财富港股板块
    方案3: 已知映射表（大型港股公司）
    """
    KNOWN_HK_INDUSTRIES = {
        "0700": {"name": "互联网/科技", "sector": "科技", "peers": ["9988", "9618", "9888"]},
        "0941": {"name": "电信", "sector": "电信", "peers": ["0981", "1883"]},
        "0005": {"name": "金融", "sector": "金融", "peers": ["0011", "0388", "1299"]},
        "0388": {"name": "金融", "sector": "金融", "peers": ["0005", "0011"]},
        "1299": {"name": "保险", "sector": "保险", "peers": ["2318", "2628"]},
        "0012": {"name": "房地产", "sector": "房地产", "peers": ["1109", "0016", "0001"]},
        # ... 可扩展
    }
    return KNOWN_HK_INDUSTRIES.get(symbol.zfill(5), None)
```

#### 3.2.4 获取所有行业板块列表

```python
async def get_all_industries_em() -> list[str]:
    """
    获取东方财富所有行业板块名称
    
    Returns: ["银行", "白酒", "证券", ...]
    """
    try:
        df = ak.stock_board_industry_name_em()
        if df is not None and not df.empty and "板块名称" in df.columns:
            return df["板块名称"].tolist()
    except Exception:
        pass
    
    # 降级：返回已知行业列表
    return ["银行", "白酒", "证券", "保险", "医药", "新能源", "家电", 
            "电子", "房地产", "电力", "有色金属", "水泥", "互联网", "科技",
            "汽车", "半导体", "食品饮料", "军工", "计算机", "通信",
            "传媒", "化工", "钢铁", "煤炭", "有色金属", "机械设备",
            "建筑装饰", "交通运输", "农林牧渔", "纺织服装"]
```

### 3.3 行业分类改进

当前 `_find_industry` 依赖逐个尝试 akshare API（耗时）+ 少量已知映射。改进方案：

```python
async def find_industry_improved(symbol: str, company_name: str = "") -> Optional[str]:
    """
    改进的行业分类方法
    
    优先级：
    1. 已知映射（扩展版，覆盖 100+ 常用标的）
    2. 东方财富行业板块列表 + 模糊匹配
    3. 公司名称关键词推断
    4. akshare API 逐个尝试（加短超时，降级）
    """
    code = symbol.zfill(6)
    
    # === 1. 扩展的已知映射（从同花顺/东方财富缓存）===
    KNOWN_INDUSTRIES_EXTENDED = {
        # 银行
        "000001": "银行", "002142": "银行", "600000": "银行", "600036": "银行",
        "601398": "银行", "601939": "银行", "601328": "银行", "601166": "银行",
        # 白酒
        "600519": "白酒", "000858": "白酒", "002304": "白酒", "000568": "白酒",
        # 证券
        "600030": "证券", "300059": "证券", "601066": "证券",
        # 新能源
        "300750": "新能源", "601012": "新能源", "002594": "新能源", "300014": "新能源",
        # 医药
        "600276": "医药", "000538": "医药", "300760": "医药", "300347": "医药",
        # 半导体/电子
        "002475": "电子", "002415": "电子", "603986": "半导体", "600745": "半导体",
        # 科技/互联网
        "000725": "科技", "002236": "科技", "600588": "计算机", "002230": "计算机",
        # ... 实际可扩展至 100+ 常用标的
    }
    
    if code in KNOWN_INDUSTRIES_EXTENDED:
        return KNOWN_INDUSTRIES_EXTENDED[code]
    
    # === 2. 从东方财富全量行业板块中搜索 ===
    all_industries = await get_all_industries_em()
    for ind in all_industries:
        try:
            # 只搜索第一个成分股，快速判断
            df = ak.stock_board_industry_cons_em(symbol=ind)
            if df is not None and not df.empty and "代码" in df.columns:
                if code in df["代码"].astype(str).tolist():
                    logger.info(f"行业分类命中: {code} → {ind}")
                    return ind
        except Exception:
            continue
    
    # === 3. 名称关键词推断 ===
    if company_name:
        NAME_KEYWORDS = {
            "银行": ["银行"], "证券": ["证券", "券商"], "保险": ["保险"],
            "白酒": ["酒", "窖", "醇"], "医药": ["医药", "制药", "生物", "药"],
            "新能源": ["新能", "锂", "电"], "半导体": ["半导体", "芯", "集成电路"],
            "房地产": ["地产", "置业", "城建"], "汽车": ["汽车", "车"],
            "科技": ["科技", "软件", "信息", "网络"], "军工": ["军工", "航天", "航空"],
        }
        for industry, keywords in NAME_KEYWORDS.items():
            if any(kw in company_name for kw in keywords):
                return industry
    
    return None
```

### 3.4 数据源降级策略

```
A股行业数据:
  东方财富行业板块(主力) → 同花顺行业(备选) → 硬编码参考值(兜底) → 知识库(最后)

港股行业数据:
  yfinance sector → 已知映射表 → 行业大类推断 → 仅标的数据

美股行业数据:
  yfinance sector/industry → 仅标的数据
```

---

## 4. 行业数据处理管线

### 4.1 管线流程

```
原始行业数据（成分股列表 + 行情 + 标的数据）
        │
        ▼
┌──────────────────┐
│ ① 行业平均估值    │  → 加权/算术 PE/PB 均值、中位数
│   计算            │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ ② 标的行业排名    │  → PE 排名、ROE 排名、估值分位
│   计算            │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ ③ 行业周期判断    │  → 趋势方向 + 周期阶段
│   (量化规则)      │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ ④ 性价比综合评分  │  → 公司质量/估值的综合打分
│                  │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ ⑤ 数据质量评估    │  → 行业数据完整度评估
│   + 结构化摘要    │
└──────────────────┘
```

### 4.2 ① 行业平均估值计算

```python
def calculate_industry_metrics(peers: list[dict]) -> dict:
    """
    从成分股列表计算行业统计指标
    
    Args:
        peers: [{"code": "601398", "name": "工商银行", "pe": 5.2, "pb": 0.6, "roe": 11.0}, ...]
    
    Returns:
        {
            "avg_pe": 5.8,         # 算术平均
            "median_pe": 5.5,      # 中位数 (更抗极端值)
            "weighted_avg_pe": 6.2, # 市值加权
            "avg_pb": 0.65,
            "median_pb": 0.62,
            "avg_roe": 10.5,
            "median_roe": 11.0,
            "sample_size": 20,      # 参与计算的成分股数
            "pe_std": 1.2,          # 标准差（行业内估值离散度）
        }
    """
    pe_values = [p["pe"] for p in peers if p.get("pe") and p["pe"] > 0]
    pb_values = [p["pb"] for p in peers if p.get("pb") and p["pb"] > 0]
    roe_values = [p["roe"] for p in peers if p.get("roe") and p["roe"] > 0]
    
    if not pe_values:
        return {"sample_size": 0}
    
    return {
        "avg_pe": round(sum(pe_values) / len(pe_values), 2),
        "median_pe": round(sorted(pe_values)[len(pe_values) // 2], 2),
        "avg_pb": round(sum(pb_values) / len(pb_values), 2) if pb_values else None,
        "median_pb": round(sorted(pb_values)[len(pb_values) // 2], 2) if pb_values else None,
        "avg_roe": round(sum(roe_values) / len(roe_values), 2) if roe_values else None,
        "median_roe": round(sorted(roe_values)[len(roe_values) // 2], 2) if roe_values else None,
        "sample_size": len(peers),
        "pe_std": round((sum((x - sum(pe_values)/len(pe_values))**2 for x in pe_values) / len(pe_values))**0.5, 2),
    }

# 关键优化：中位数比均值更适合行业对比（避免极端值干扰）
# PE标准差大 → 行业内估值分歧大 → 需要更仔细地判断
```

### 4.3 ② 标的行业排名

```python
def calculate_industry_rank(stock_pe: float, stock_roe: float,
                           peers: list[dict], industry_median: dict) -> dict:
    """
    计算标的在行业中的排名
    
    Returns:
        {
            "pe_rank": "5/42",
            "pe_percentile": 0.88,      # PE比88%的公司贵
            "roe_rank": "3/42",
            "roe_percentile": 0.93,     # ROE比93%的公司高
            "valuation_label": "高PE+高ROE = 溢价合理的成长股",
        }
    """
    if not peers:
        return {"note": "无行业成分股数据"}
    
    # PE 排名 (从低到高 = 从便宜到贵)
    pe_values = sorted([p.get("pe") for p in peers if p.get("pe")])
    if stock_pe and pe_values:
        pe_rank = sum(1 for pe in pe_values if pe <= stock_pe)
        pe_pct = pe_rank / len(pe_values)
    else:
        pe_rank, pe_pct = None, None
    
    # ROE 排名 (从高到低 = 从好到差)
    roe_values = sorted([p.get("roe") for p in peers if p.get("roe")], reverse=True)
    if stock_roe and roe_values:
        roe_rank = sum(1 for roe in roe_values if roe >= stock_roe) + 1
        roe_pct = roe_rank / len(roe_values)
    else:
        roe_rank, roe_pct = None, None
    
    # 自动标签
    label = None
    if pe_pct is not None and roe_pct is not None:
        if pe_pct < 0.3 and roe_pct < 0.5:
            label = "低PE+高ROE = 性价比突出"
        elif pe_pct < 0.5 and roe_pct < 0.5:
            label = "合理估值+良好盈利"
        elif pe_pct > 0.7 and roe_pct > 0.7:
            label = "高PE+低ROE = 估值偏高"
        elif pe_pct < 0.3 and roe_pct > 0.7:
            label = "低PE+低ROE = 价值陷阱?"
        elif pe_pct > 0.7 and roe_pct < 0.3:
            label = "高PE+高ROE = 溢价成长"
    
    return {
        "pe_rank": f"{pe_rank}/{len(pe_values)}" if pe_rank else "N/A",
        "pe_percentile": round(pe_pct, 2) if pe_pct else None,
        "roe_rank": f"{roe_rank}/{len(roe_values)}" if roe_rank else "N/A",
        "roe_percentile": round(roe_pct, 2) if roe_pct else None,
        "valuation_label": label,
        "industry_median_pe": industry_median.get("median_pe"),
        "stock_vs_median_pe": f"{(stock_pe / industry_median['median_pe'] - 1) * 100:.0f}%" 
                              if stock_pe and industry_median.get("median_pe") else "N/A",
    }
```

### 4.4 ③ 行业周期判断

```python
class IndustryCycleClassifier:
    """
    基于涨跌幅和估值变化的行业周期分类
    
    四阶段模型:
    - 复苏(Recovery): 估值低 + 价格开始上涨 → 最佳买点
    - 繁荣(Boom): 估值走高 + 价格持续上涨 → 注意过热信号
    - 衰退(Slowdown): 估值高 + 价格开始下跌 → 回避
    - 萧条(Depression): 估值低 + 价格持续下跌 → 可能孕育机会
    """
    
    def classify(self, trend: dict, valuation: dict) -> dict:
        """
        Args:
            trend: {"change_5d": 1.2, "change_20d": 5.0, "change_60d": 12.0}
            valuation: {"pe_percentile": 0.3}  # 在基本面分析师中已算出
        """
        change_20d = trend.get("change_20d") or 0
        change_60d = trend.get("change_60d") or 0
        pe_pct = valuation.get("pe_percentile") or 0.5
        
        # 简化规则
        if change_60d > 10 and pe_pct > 0.6:
            cycle = "boom"
            phase = "繁荣期"
            signal = "行业处于景气高位，估值偏贵，追高风险大"
        elif change_60d < -10 and pe_pct > 0.6:
            cycle = "slowdown"
            phase = "衰退期"
            signal = "行业下行+估值仍贵，可能进一步下跌"
        elif change_60d < 0 and pe_pct < 0.4:
            cycle = "depression"
            phase = "萧条期"
            signal = "行业低迷+估值便宜，关注拐点信号"
        elif change_60d > 0 and pe_pct < 0.4:
            cycle = "recovery"
            phase = "复苏期"
            signal = "行业从低估中恢复，基本面改善"
        else:
            cycle = "normal"
            phase = "常态期"
            signal = "行业无极端信号"
        
        return {
            "cycle": cycle,
            "phase_name": phase,
            "signal": signal,
            "momentum_score": self._calc_momentum(change_5d=trend.get("change_5d", 0),
                                                    change_20d=change_20d,
                                                    change_60d=change_60d),
        }
```

### 4.5 ④ 性价比评分

```python
def calculate_value_score(stock_data: dict, industry_data: dict) -> dict:
    """
    综合性价比评分: 质量 vs 价格的匹配度
    
    逻辑:
    - 好公司 + 便宜价格 = 高分 (未来大概率上涨)
    - 差公司 + 贵价格 = 低分 (未来大概率下跌)
    - 关键是判断"当前的折溢价是否合理"
    """
    roe = stock_data.get("stock_roe") or 0
    pe = stock_data.get("stock_pe") or 999
    industry_roe = industry_data.get("avg_roe") or roe
    industry_pe = industry_data.get("avg_pe") or pe
    
    # 相对 ROE (公司ROE / 行业ROE)
    roe_ratio = roe / industry_risk if industry_roe > 0 else 1.0
    
    # 相对 PE (公司PE / 行业PE)
    pe_ratio = pe / industry_pe if industry_pe > 0 else 1.0
    
    # PEG-like: 相对PE / 相对ROE
    # < 1 = 好公司相对便宜 → 好
    # > 1 = 好公司相对偏贵 → 需看增长
    value_ratio = pe_ratio / roe_ratio if roe_ratio > 0 else pe_ratio
    
    if value_ratio < 0.7:
        score = "excellent"  # 明显低估
        interpretation = "公司盈利能力优于行业，但估值偏低——大概率被低估"
    elif value_ratio < 1.0:
        score = "good"
        interpretation = "性价比良好，公司盈利与估值基本匹配"
    elif value_ratio < 1.3:
        score = "fair"
        interpretation = "性价比一般，估值与盈利基本匹配"
    elif value_ratio < 1.8:
        score = "expensive"
        interpretation = "性价比偏弱，公司为获得单位盈利付出的价格偏高"
    else:
        score = "overpriced"  # 明显高估
        interpretation = "性价比差，估值显著高于盈利能力对应的合理水平"
    
    return {
        "value_ratio": round(value_ratio, 2),
        "score": score,
        "interpretation": interpretation,
        "roe_ratio": round(roe_ratio, 2),
        "pe_ratio": round(pe_ratio, 2),
    }
```

### 4.6 ⑤ 结构化摘要输出

```json
{
  "symbol": "000001",
  "company_name": "平安银行",
  "industry_name": "银行",
  "market": "A",
  "data_quality": {
    "overall": 0.85,
    "sources": ["eastmoney_industry", "akshare_financial"],
    "notes": "行业数据来自实时板块，财务来自akshare",
    "confidence_ceiling": 0.85
  },
  
  "industry_metrics": {
    "avg_pe": 5.8,
    "median_pe": 5.5,
    "avg_pb": 0.65,
    "avg_roe": 10.5,
    "median_roe": 11.0,
    "stock_count": 42,
    "pe_std": 1.5
  },
  
  "stock_metrics": {
    "pe": 6.2,
    "pb": 0.72,
    "roe": 12.0
  },
  
  "rank_in_industry": {
    "pe_rank": "18/42",
    "pe_percentile": 0.43,
    "roe_rank": "10/42",
    "roe_percentile": 0.76,
    "valuation_label": "合理估值+良好盈利",
    "vs_median_pe": "+13%"
  },
  
  "industry_trend": {
    "change_5d_pct": 1.2,
    "change_20d_pct": 3.5,
    "change_60d_pct": -4.0,
    "trend": "range",
    "cycle": "normal",
    "phase": "常态期"
  },
  
  "value_score": {
    "value_ratio": 0.85,
    "score": "good",
    "interpretation": "性价比良好，公司ROE高于行业但估值仅略高"
  },
  
  "peer_comparison": {
    "top_peers": [
      {"name": "招商银行", "pe": 8.5, "roe": 16.0},
      {"name": "兴业银行", "pe": 5.8, "roe": 12.5},
      {"name": "工商银行", "pe": 5.2, "roe": 11.0}
    ]
  },
  
  "anomaly_flags": {
    "divergence_warning": false,     // 行业在涨但标的不涨
    "extreme_valuation": false,       // 估值处于极端位置
    "industry_rotation_signal": false // 行业轮动信号
  }
}
```

---

## 5. Agent 架构升级

### 5.1 当前：单 Pass 推理

```
行业数据(json) → [一个大 Prompt] → 结果(json)
```

### 5.2 目标：多步链式推理

```python
class IndustryAnalyst(BaseAgent):
    """升级版：三步链式推理"""
    
    async def analyze(self, data: dict, context: dict) -> AnalysisResult:
        
        # Step 1: 定位分析 (Positioning)
        position = await self._step_position_analysis(data, context)
        
        # Step 2: 判断分析 (Judgment)
        judgment = await self._step_judgment(data, position, context)
        
        # Step 3: 综合判断 + 反思
        final = await self._step_synthesize(data, position, judgment, context)
        
        # Step 4: 置信度校准
        calibrated = self._calibrate_confidence(final, data)
        
        return calibrated
```

### 5.3 各步骤详解

#### Step 1: 定位分析 (Positioning)

```
输入: 行业数据 + 排名数据
输出: "这个公司在行业中处于什么位置？"
```

```
你是行业定位专家。数据已经过预处理，你只需要做判断：

## 已为你计算好的数据
- 标的 PE 排名: 18/42 (比 43% 的公司贵)
- 标的 ROE 排名: 10/42 (比 76% 的公司高)
- 行业趋势: 近60日下跌 4%，震荡期
- 性价比评分: 0.85 (good)

请回答:
1. "溢价还是折价？是否合理？" — 如果高 ROE 支撑了高 PE，溢价合理
2. "相对位置是改善还是恶化？" — 排名趋势（需要多期数据）
3. "对标公司中最像谁？" — 历史规律

输出 JSON:
{
  "position": "行业中的位置描述",
  "premium_discount_justified": true/false,
  "relative_improving": true/false,
  "peers_most_similar": "招商银行",
  "position_confidence": 0.8
}
```

#### Step 2: 判断分析 (Industry Judgment)

```
输入: 行业趋势 + 周期定位 + Step 1 输出
输出: "这个行业当前值得投资吗？"
```

```
你是行业景气度判断专家。

## 行业周期现状
- 当前阶段: 常态期
- 近20日涨跌幅: +3.5%
- 近60日涨跌幅: -4.0%
- 估值分位: 35%（3年）

## 标的位置
- 公司定位: {step1_position}
- 性价比评分: 0.85 (good)

请回答:
1. 行业周期方向: 向上/向下/横盘？
2. 当前阶段的投资信号: 超配/标配/低配？
3. 行业核心驱动因素是什么？
4. 未来1-3个月行业可能的变化方向？

输出 JSON:
{
  "industry_direction": "slightly_bullish",
  "allocation_signal": "standard",  // overweight/standard/underweight
  "key_drivers": ["净息差企稳", "政策托底"],
  "outlook_1_3m": "银行业基本面底部震荡，估值修复需要催化"
}
```

#### Step 3: 综合判断 + 反思 (Synthesis + Reflection)

```
Step 3a: 综合判断
- 结合 Step 1 的"位置"和 Step 2 的"趋势"
- 给出 direction + magnitude + confidence
- 生成 reasoning

Step 3b: 反思
- "行业分析中有什么盲点？"
- "周期性因素有没有被忽略？"
- "板块轮动是否可能导致行业偏好变化？"
```

### 5.4 Fallback：行业数据不足时的处理

当行业数据严重不足时（只有标的数据），自动降级为"有限行业分析"：

```python
async def _fallback_limited_analysis(self, data: dict, context: dict) -> AnalysisResult:
    """
    行业数据不足时的降级分析
    
    仅基于:
    1. 标的自身的 PE/PB 与历史对比
    2. 已知行业参考值（硬编码作为最后手段）
    3. 公司的财务趋势
    """
    # 不再说"我不知道"
    # 而是说"基于有限数据的有限判断"
    
    return AnalysisResult(
        agent_name=self.name,
        direction=Direction.NEUTRAL,
        confidence=0.35,
        reasoning="行业数据有限。基于标的估值水平(PE处于历史30%分位)和公司基本面(ROE 15%良好)，"
                  "标的中性偏正面。但缺乏行业对比无法确认相对位置。",
        # ...
    )
```

---

## 6. Prompt 工程优化

### 6.1 Few-shot 示例

```python
INDUSTRY_SYSTEM_PROMPT = """你是一个专业的行业研究员...

## 输出示例

### 示例 1: 行业复苏 + 标的好位置
输入: 标的PE排名15/42(便宜), ROE排名5/42(优秀), 行业5日涨幅2%, 性价比评分0.75
输出:
{
  "direction": "bullish",
  "magnitude": {"min_pct": 3.0, "max_pct": 8.0},
  "confidence": 0.72,
  "reasoning": "1) 行业定位: 标的公司ROE排名行业前12%，但PE仅在中位数附近，性价比突出...",
  "key_factors": ["公司ROE显著高于行业均值", "行业处于复苏早期", "性价比高"],
  "risks": ["行业复苏需要时间", "短期涨幅可能回调"]
}

### 示例 2: 行业泡沫 + 标的估值极端
输入: 标的PE排名40/42(很贵), ROE排名35/42(中下), 行业60日涨幅25%
输出:
{
  "direction": "bearish",
  "magnitude": {"min_pct": -10.0, "max_pct": -3.0},
  "confidence": 0.65,
  "reasoning": "1) 行业定位: 公司ROE仅排在中下游但PE接近行业最高，明显高估...",
  "key_factors": ["估值泡沫化", "景气度可能见顶"],
  "risks": ["市场情绪可能继续推高", "行业景气拐点难以精确判断"]
}

### 示例 3: 行业数据缺失
输入: 无行业数据，仅标的PE=15, ROE=18%
输出:
{
  "direction": "neutral",
  "magnitude": {"min_pct": -3.0, "max_pct": 3.0},
  "confidence": 0.35,
  "reasoning": "行业对比数据不可用。仅凭公司基本面: ROE 18%优秀，PE 15合理...",
  "key_factors": ["公司基本面稳健"],
  "risks": ["无法判断行业位置", "无法判断是否存在系统性风险"]
}
"""
```

### 6.2 置信度锚定

```python
CONFIDENCE_ANCHORS = """
## 置信度(calibration)指引

行业对比分析的置信度取决于"行业数据质量 × 判断清晰度"：

| confidence | 含义 | 何时使用 |
|------------|------|---------|
| 0.75-0.85 | 较有把握 | 行业数据完整(有成分股+行情), 标的排名清晰, 行业周期明确 |
| 0.60-0.74 | 中等把握 | 行业数据基本完整, 趋势方向较明确但位置判断有模糊地带 |
| 0.45-0.59 | 有限把握 | 行业数据部分缺失, 仅靠有限信息判断 |
| 0.30-0.44 | 较弱判断 | 行业数据严重缺失, 仅靠硬编码参考值 |
| 0.15-0.29 | 几乎无信号 | 无行业数据, 纯知识库判断 |

你的 confidence 上限受数据质量 ceiling 约束:
- 数据完整性 >80%: ceiling = 0.85
- 数据完整性 50-80%: ceiling = 0.65
- 数据完整性 <50%: ceiling = 0.45
"""
```

### 6.3 行业类型区分

```python
# 不同行业的分析框架差异

CYCLICAL_INDUSTRY_APPENDIX = """
## 周期性行业分析（钢铁/有色/煤炭/化工/房地产）
- **看 PB 不看 PE**: 周期性行业盈利波动大，PE 在高盈利时失真（PE 最低时可能是卖出点）
- **看库存周期**: 当前处于被动去库存(复苏)还是主动补库存(繁荣)？
- **看价格**: 行业产品价格趋势是领先指标
- **估值要在盈利中枢上算**: 用 normalized PE 而非当前 PE
"""

GROWTH_INDUSTRY_APPENDIX = """
## 成长性行业分析（科技/半导体/新能源/生物医药）
- **看增速**: 增速 > 估值，PEG 是关键
- **看催化剂**: 技术突破、政策扶持、渗透率拐点
- **高 PE 可以是合理的**: 如果增速能支撑（增速% > PE → 合理）
- **注意增速拐点**: 增速从 50% 降到 30%，股价可能跌 40%
"""

DEFENSIVE_INDUSTRY_APPENDIX = """
## 防御性行业分析（食品/饮料/公用事业/医药）
- **看稳定**: ROE 稳定性比绝对值更重要
- **看股息**: 分派率和股息增长记录
- **在熊市中跑赢**: 防御性板块的相对收益比绝对收益更重要
- **估值中枢稳定**: 这类行业的 PE 波动区间较窄，偏离中枢就是信号
"""
```

### 6.4 行业轮动判断触发

在 prompt 中加入：

```
## 行业轮动信号判断

请判断当前是否出现行业轮动的早期信号（以下任一为信号）:
1. 风格切换: 成长→价值 或 价值→成长 的切换
2. 资金轮动: 前期强势板块开始走弱，资金寻找新方向
3. 政策驱动: 产业政策变化可能导致资金重新配置
4. 周期切换: 经济周期阶段变化（复苏→过热→滞涨→衰退）

如果检测到行业轮动信号，请标注:
- 轮动方向: ____
- 对标的行业的影响: ____
```

---

## 7. 自进化机制

### 7.1 按行业的准确率追踪

```python
# 在 PredictionStore 中增加行业维度

class IndustryAccuracyTracker:
    """
    按行业追踪行业对比分析师的准确率
    
    假设: 分析师在某些行业(如银行/白酒)的判断可能一直比其他行业(如半导体)准
    → 如果某行业判断准确率高，下次对该行业的预测可以给更高权重
    """
    
    def track_by_industry(self):
        """
        统计:
        - 银行业: 方向准确率 68%
        - 白酒业: 方向准确率 72%
        - 半导体: 方向准确率 45% (周期判断困难)
        
        下次分析半导体时，自动降低 confidence ceiling
        """
```

### 7.2 行业参考值动态更新

```python
class IndustryRefresher:
    """
    定期刷新行业参考值
    
    当前问题: INDUSTRY_REF 字典的数值可能过时
    解决方案: 定期重新计算 + 存到缓存文件
    """
    
    async def refresh_industry_reference(self):
        """
        每月或每周执行:
        1. 拉取当前所有行业的实时 PE/PB
        2. 更新 INDUSTRY_REF 字典
        3. 保存到 config/industry_reference_cache.json
        4. 记录更新日志
        """
    
    def load_cached_reference(self) -> dict:
        """加载缓存的行业参考值"""
        cache_path = "config/industry_reference_cache.json"
        if os.path.exists(cache_path):
            with open(cache_path) as f:
                cache = json.load(f)
            # 检查缓存时间
            cached_date = datetime.fromisoformat(cache["updated_at"])
            if (datetime.now() - cached_date).days < 7:
                return cache["data"]
        return INDUSTRY_REF  # 降级到硬编码
```

### 7.3 失败案例分析

```python
class IndustryAnalysisReviewer:
    """分析行业对比预测失败的原因"""
    
    def analyze_failure(self, prediction_id: str):
        """
        对于失败的预测:
        1. 行业判断对了但个股判断错了？（选错公司 vs 选错行业）
        2. 行业周期判断错了？（误判繁荣/衰退）
        3. 数据质量问题？（行业均值过时导致判断偏差）
        4. 行业轮动没捕捉到？（风格突然切换）
        """
```

---

## 8. 质量保障体系

### 8.1 专项校验

```python
class IndustryResultValidator:
    """行业对比分析结果专项校验"""
    
    def validate(self, result: AnalysisResult, data: dict) -> list[str]:
        issues = []
        
        # 1. 排名-方向一致性
        rank = data.get("rank_in_industry", {})
        pe_pct = rank.get("pe_percentile")
        
        if pe_pct is not None:
            if result.direction == "bullish" and pe_pct > 0.85:
                issues.append(f"PE排名在行业后15%(很贵)但方向为看涨，需要特别强的理由")
            if result.direction == "bearish" and pe_pct < 0.15:
                issues.append(f"PE排名在行业前15%(很便宜)但方向为看跌，需充分解释")
        
        # 2. 性价比评分-方向一致性
        value = data.get("value_score", {})
        if value.get("score") == "overpriced" and result.direction == "bullish":
            issues.append("性价比评分为'明显高估'但方向为看涨——逻辑矛盾")
        if value.get("score") == "excellent" and result.direction == "bearish":
            issues.append("性价比评分为'明显低估'但方向为看跌——逻辑矛盾")
        
        # 3. 数据质量 ceiling
        ceiling = data.get("data_quality", {}).get("confidence_ceiling", 0.70)
        if result.confidence > ceiling + 0.05:
            issues.append(f"confidence({result.confidence})超过数据质量上限({ceiling})")
        
        # 4. 行业趋势-方向一致性（轻微检查）
        trend = data.get("industry_trend", {})
        cycle = trend.get("cycle")
        if cycle == "slowdown" and result.direction == "bullish":
            issues.append("行业处于衰退期但方向为看涨——可以但需标注行业风险")
        
        # 5. 缺失数据检查
        if data.get("data_quality", {}).get("overall", 1.0) < 0.3:
            if result.confidence > 0.45:
                issues.append("极低数据质量下 confidence 不应超过 0.45")
        
        return issues
```

### 8.2 特殊情况处理矩阵

| 情况 | 处理 | confidence 上限 |
|------|------|----------------|
| 行业数据完整（成分股+行情+排名） | 正常分析 | 0.85 |
| 行业数据部分完整（有均值无成分股） | 降级分析 | 0.65 |
| 估值+标的数据但无行业成分股 | 有限分析 | 0.50 |
| 仅有硬编码参考值 | 非常有限分析 | 0.40 |
| 仅标的数据，无行业数据 | 知识库降级 | 0.25 |
| 数据源全部不可用 | 纯知识库 | 0.15 |

### 8.3 测试策略

```python
# tests/test_industry_analyst_v2.py

class TestIndustryDataPipeline:
    def test_industry_metrics_calculation(self): ...
    def test_stock_ranking_in_industry(self): ...
    def test_industry_cycle_classification(self): ...
    def test_value_score_calculation(self): ...

class TestMultiStepReasoning:
    def test_position_analysis_output(self): ...
    def test_judgment_consider_industry_trend(self): ...
    def test_synthesis_direction(self): ...

class TestSelfEvolution:
    def test_industry_accuracy_tracking(self): ...
    def test_confidence_ceiling_enforced(self): ...

class TestQualityAssurance:
    def test_validator_catches_inconsistency(self): ...
    def test_validator_enforces_data_ceiling(self): ...
    def test_fallback_handles_missing_data(self): ...
```

---

## 9. 实施路线图

### Phase A: 数据管道升级 + 核心功能（1-2 周）🔴 P0

| 任务 | 文件 | 工作量 |
|------|------|--------|
| ① 东方财富行业板块数据获取（替代硬编码） | `industry_fetcher.py` | 1.5 天 |
| ② 行业平均估值计算 + 成分股列表 | `industry_fetcher.py` | 1 天 |
| ③ 标的行业排名分位 | 新增 `src/data/industry_preprocessor.py` | 1 天 |
| ④ 行业趋势/周期判断 | `industry_preprocessor.py` | 1 天 |
| ⑤ 扩展已知行业映射（100+ 标的） | `industry_fetcher.py`KNOWN_INDUSTRIES | 0.5 天 |
| ⑥ Few-shot + 置信度锚定 prompt | `industry_prompts.py` | 0.5 天 |
| ⑦ 输出校验增强 | `industry_analyst.py` | 0.5 天 |
| ⑧ 测试 | `tests/test_industry_v2.py` | 1 天 |

**预期效果**：
- 从硬编码常量升级为实时行业数据
- 行业覆盖从 28 标的大幅扩展
- 排名分位量化（"在行业中排第几"）
- 行业趋势方向引入

### Phase B: 多步推理 + 港股支持（1-2 周）🟡 P1

| 任务 | 文件 | 工作量 |
|------|------|--------|
| ① 三步 CoT 推理 | `industry_analyst.py` | 1.5 天 |
| ② 港股行业分类（映射表 + yfinance） | `industry_fetcher.py` | 1.5 天 |
| ③ 性价比综合评分 | `industry_preprocessor.py` | 0.5 天 |
| ④ 行业类型区分 prompt | `industry_prompts.py` | 0.5 天 |
| ⑤ 置信度校准 + 数据质量 ceiling | `industry_analyst.py` | 1 天 |
| ⑥ 端到端测试 | `tests/` | 1 天 |

**预期效果**：
- 推理质量：单 pass → 多步链式
- 港股行业对比从"无"到有
- 性价比评分量化（PE/ROE 比值法）

### Phase C: 自进化 + 深色功能（2-3 周）🟢 P2

| 任务 | 文件 | 工作量 |
|------|------|--------|
| ① 按行业的准确率追踪 | `prediction_store.py` 扩展 | 1 天 |
| ② 行业参考值动态刷新缓存 | 新增 `src/utils/industry_refresher.py` | 1 天 |
| ③ 行业轮动检测 | `industry_preprocessor.py` | 1.5 天 |
| ④ 失败案例分析 | 新增 `src/utils/failure_analyzer.py` | 1 天 |
| ⑤ 与基本面分析师数据打通 | 共享 `stock_metrics` 数据 | 1 天 |
| ⑥ 催化剂日历（可选） | 从新闻/政策推断 | 1 天 |

### Phase D: 深度优化（1 月+）🟢 P3

| 任务 | 说明 |
|------|------|
| 行业风险溢价估计 | 估计行业的 equity risk premium |
| 行业轮动模型 | 构建轮动因子模型（估值差异 + 动量 + 宏观） |
| 国际对标 | 同一行业在全球视角下的估值对比 |
| 产业链上下游分析 | 从产业链角度判断各行业景气度传导 |

---

## 10. 附录：效果度量

### 10.1 关键指标

| 指标 | 当前（估算） | Phase A 目标 | Phase B 目标 | 度量方式 |
|------|------------|-------------|-------------|---------|
| 方向准确率（A股） | ~50% | ≥58% | ≥63% | PredictionStore |
| 方向准确率（港股） | ~35-40% | ≥45% | ≥52% | PredictionStore |
| 置信度校准误差 | 未知 | ≤0.18 | ≤0.12 | \|confidence - actual_acc\| |
| 行业数据可用率（A股） | ~40%（28/70常用标的） | ≥80% | ≥90% | 行业分类成功率 |
| 行业数据可用率（港股） | ~5%（仅互联网） | ≥30% | ≥50% | 港股映射覆盖率 |
| 排名分位准确率 | 无该能力 | 统计建立 | ≥60% | 排名 vs 实际表现 |
| 价值陷阱预警率 | 0% | ≥30% | ≥50% | 低PE+下跌案例 |
| JSON 格式成功率 | ~90% | ≥97% | ≥98% | 日志统计 |

### 10.2 实验设计

```bash
# 改进前后对比
python scripts/run_backtest.py -t 000001 --start 2026-01-01 --end 2026-06-30 --agent industry
python scripts/run_backtest.py -t 0700 --start 2026-01-01 --end 2026-06-30 --agent industry_v2

# 重点观察:
# 1. 行业分类成功率: 从 40% → ?
# 2. 有完整行业数据时 → 准确率提升多少
# 3. "低PE+高ROE" 类型判断的准确率
# 4. 行业衰退期判断的领先性
```

---

## 附录 A：文件变更清单

### 需要修改的文件

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `src/data/industry_fetcher.py` | 重构 | 多源采集 + 扩展行业分类 + 行业数据获取 |
| `src/agents/industry_analyst.py` | 重构 | 多步推理 + 校验 + 校准 |
| `src/prompts/industry_prompts.py` | 重写 | Few-shot + 置信度锚定 + 行业类型区分 |

### 需要新增的文件

| 文件 | 说明 |
|------|------|
| `src/data/industry_preprocessor.py` | 排名分位 + 周期判断 + 性价比评分 + 质量评估 |
| `src/utils/industry_validator.py` | 专项校验器 |
| `src/utils/industry_refresher.py` | 行业参考值缓存刷新 |
| `config/industry_reference_cache.json` | 行业参考值缓存（运行时生成） |
| `tests/test_industry_v2.py` | Agent v2 测试 |
| `tests/test_industry_preprocessing.py` | 预处理测试 |

### 不需要修改的文件

- `src/core/base_agent.py` — 接口不变
- `src/core/orchestrator.py` — Agent 接口不变
- `src/core/result.py` — `AnalysisResult` 无需新增字段
- `src/data/prediction_store.py` — Phase C 需 schema 微调

---

## 附录 B：行业分类性能优化

当前行业分类 `_find_industry` 通过逐个尝试 akshare API（每个行业一次网络请求），严重耗时。

**优化方案**：

```python
class IndustryClassifierCache:
    """
    行业分类缓存器
    
    大部分标的不经常变化行业分类，缓存可大幅减少 API 调用
    """
    
    CACHE_FILE = "config/industry_classifier_cache.json"
    
    def __init__(self):
        self.cache = self._load_cache()
        self._dirty = False
    
    def get(self, symbol: str) -> Optional[str]:
        """从缓存查找"""
        return self.cache.get(symbol)
    
    def put(self, symbol: str, industry: str):
        """缓存行业分类"""
        self.cache[symbol] = industry
        self._dirty = True
    
    async def save(self):
        """持久化到 JSON 文件"""
        if self._dirty:
            with open(self.CACHE_FILE, "w") as f:
                json.dump(self.cache, f, ensure_ascii=False, indent=2)
            self._dirty = False

# 使用方式
classifier_cache = IndustryClassifierCache()

async def find_industry_with_cache(symbol: str) -> Optional[str]:
    # 先查缓存
    cached = classifier_cache.get(symbol)
    if cached is not None:
        return cached
    
    # 再查扩展映射
    industry = EXTENDED_KNOWN_INDUSTRIES.get(symbol)
    
    # 最后才尝试 API
    if industry is None:
        industry = await find_industry_improved(symbol)
    
    # 缓存结果
    if industry:
        classifier_cache.put(symbol, industry)
    
    return industry
```

---

## 附录 C：行业参考值动态更新实现

```python
# src/utils/industry_refresher.py

class IndustryReferenceRefresher:
    """
    定期刷新行业参考值
    
    执行频率: 每周一次 (cron 或手动触发)
    输出: config/industry_reference_cache.json
    """
    
    async def refresh(self) -> dict:
        """
        从东方财富行业板块获取所有行业实时估值
        """
        all_industries = await get_all_industries_em()
        reference = {}
        
        for ind in all_industries:
            try:
                df = ak.stock_board_industry_cons_em(symbol=ind)
                if df is not None and not df.empty:
                    pe_values = [safe_float(x) for x in df.get("市盈率-动态", []) if safe_float(x) and safe_float(x) > 0]
                    pb_values = [safe_float(x) for x in df.get("市净率", []) if safe_float(x) and safe_float(x) > 0]
                    
                    if pe_values:
                        reference[ind] = {
                            "pe": round(sum(pe_values) / len(pe_values), 2),
                            "pb": round(sum(pb_values) / len(pb_values), 2) if pb_values else None,
                            "stock_count": len(pe_values),
                        }
            except Exception:
                continue
        
        # 保存缓存
        cache_data = {
            "updated_at": datetime.now().isoformat(),
            "data": reference,
        }
        with open("config/industry_reference_cache.json", "w") as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
        
        return reference
```

---

> 📌 **核心原则**：行业对比分析师的竞争力不在于"能记住多少行业的 PE/PB"，而在于"能否准确判断一个公司在其行业中的相对位置，以及这个位置在行业周期的什么阶段"。好的行业分析 = 准确定位（排名）+ 正确判断（周期方向） + 诚实评估（置信度与数据质量匹配）。没有行业对比，"基本面好不好"就没有参照系。
