# 📰 新闻分析师改进方案 — Round 2

> **版本**: v1.0 | **日期**: 2026-07-03 | **前置**: IMPROVEMENT_NEWS_ANALYST_ROUND1.md（已完成）

---

## 目录

1. [Round 1 回顾](#1-round-1-回顾)
2. [Round 2 改进总览](#2-round-2-改进总览)
3. [自进化闭环](#3-自进化闭环)
4. [数据源深化](#4-数据源深化)
5. [推理质量升级](#5-推理质量升级)
6. [性能与可靠性](#6-性能与可靠性)
7. [实施路线图](#7-实施路线图)

---

## 1. Round 1 回顾

### 1.1 已完成内容

| 模块 | 文件 | 核心成果 |
|------|------|---------|
| 📡 多源采集 | `news_sources/eastmoney.py`, `sina.py` | 东方财富 + 新浪双源并发，容错降级 |
| 🔧 预处理管线 | `news_preprocessor.py` | 去重 → 相关度过滤 → 情感预标注 → 事件分类 → 时间衰减 → 异常检测 |
| 🧠 两步 CoT | `news_analyst.py` (analyze 覆盖) | Step 1 信号提取 + Step 2 综合判断+魔鬼代言人反思 |
| 📝 Prompt 增强 | `news_prompts.py` | 3 个 few-shot 示例、置信度锚定表、A股/港股市场区分附录 |
| 🔬 校验增强 | `news_analyst.py` (_validate_consistency) | 方向 vs 情绪一致性、高置信度+低数据量检测、情绪分化检测 |
| 📊 置信度校准 | `confidence_calibrator.py` | 贝叶斯收缩校准 + 数据质量惩罚 |
| 🧪 测试 | `test_news_analyst_v2.py` | 22 个单元测试（预处理、校验、prompt、校准器） |

### 1.2 架构现状

```
┌─────────────────────────────────────────────────────────┐
│                   Round 1 实现后的架构                    │
│                                                         │
│  ┌──────────────────┐    ┌──────────────────┐          │
│  │ eastmoney (主力)  │    │   sina (补充)     │          │
│  └────────┬─────────┘    └────────┬─────────┘          │
│           │        并发采集       │                     │
│           └──────────┬───────────┘                     │
│                      ▼                                 │
│  ┌──────────────────────────────────────┐              │
│  │      NewsFetcher v2 (多源合并)        │              │
│  └────────────────┬─────────────────────┘              │
│                   ▼                                    │
│  ┌──────────────────────────────────────┐              │
│  │   process_news_pipeline()            │              │
│  │   去重 → 相关度 → 情感 → 分类 → 衰减  │              │
│  └────────────────┬─────────────────────┘              │
│                   ▼                                    │
│  ┌──────────────────────────────────────┐              │
│  │   NewsAnalyst.analyze()              │              │
│  │   Step 1: 信号提取 (LLM call 1)      │              │
│  │   Step 2: 综合判断+反思 (LLM call 2)  │              │
│  │   → _validate_consistency()          │              │
│  │   → _calibrate_confidence()          │              │
│  └────────────────┬─────────────────────┘              │
│                   ▼                                    │
│              AnalysisResult                            │
└─────────────────────────────────────────────────────────┘
```

### 1.3 Round 1 遗留的未实现项

以下来自 Round 1 设计文档但未在 Phase A/B 中实现：

| 项目 | 原因 | 优先级 |
|------|------|--------|
| 雪球热帖源 (`xueqiu.py`) | 需要 cookie 模拟登录，实现复杂度高 | 🟡 P1 |
| 美股 Alpha Vantage 源 | 需要 API key 注册，暂未集成 | 🟢 P2 |
| 历史案例 RAG 检索 | 需扩展现有 `case_retriever.py` | 🟡 P1 |
| 按新闻源的准确率追踪 | 需扩展 `PredictionStore` schema | 🟡 P1 |
| 新闻源权重自适应 | 依赖准确率追踪 | 🟢 P2 |
| 失败案例自动分析 | 需要 LLM 参与分析失败原因 | 🟢 P2 |
| 置信度桶校准 | 需要大量累积样本（>50） | 🟢 P3 |

---

## 2. Round 2 改进总览

### 2.1 目标

从"能用"到"好用"——让新闻分析师成为一个**可测量、可进化、可信任**的子系统。

### 2.2 改进维度

| 维度 | 核心问题 | Round 2 方案 |
|------|---------|-------------|
| 🧬 **自进化** | 预测做完就完了，没有反馈闭环 | 失败案例自动诊断 + 新闻源权重自适应 + 置信度重新校准 |
| 📡 **数据源** | 缺少散户情绪维度；美股数据薄弱 | 雪球热帖集成 + 美股 Alpha Vantage 备选 |
| 🧠 **推理质量** | 两步 CoT 仍是"无记忆"推理 | RAG 历史案例检索 + 多模型交叉验证 |
| ⚡ **性能** | 两步 CoT 增加了延迟 | LLM 响应缓存 + 数据缓存 |
| 🔬 **可靠性** | 异常新闻可能漏判 | 新闻量异常爆发检测增强 + 辟谣/反转检测 |

---

## 3. 自进化闭环

这是 Round 2 的**核心主题**。当前系统已经能预测并存储结果，但缺乏自动分析和自我改进的能力。

### 3.1 失败案例自动诊断

#### 3.1.1 设计方案

```
预测验证后 → 方向判断错误？ → 触发自动诊断
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
              信号提取有误？    市场非理性？    数据源遗漏？
              (LLM 重新审视    (黑天鹅事件，   (对比其他源
               当时新闻)        无法预测)       是否有遗漏)
```

#### 3.1.2 实现方案

```python
# 新增: src/core/failure_analyzer.py

class FailureAnalyzer:
    """自动分析预测失败的原因"""
    
    def __init__(self, llm: LLMClient, prediction_store: PredictionStore):
        self.llm = llm
        self.store = prediction_store
    
    async def analyze(self, prediction_id: str) -> FailureReport:
        """
        1. 获取预测时的新闻数据和 LLM 输出
        2. 获取实际的股价变化
        3. 让 LLM 对比分析：
           - 新闻信号是否确实指向了错误方向？（信号没问题，市场不理性）
           - 新闻信号是否被误读？（解读出错，需改进 prompt）
           - 是否有遗漏的重大新闻？（数据源问题）
           - 是否有不可预见的黑天鹅？（无法改进）
        4. 生成 FailureReport 存储到 DB
        """
```

#### 3.1.3 输出结构

```python
@dataclass
class FailureReport:
    prediction_id: str
    failure_type: str  # "signal_misread" | "data_missed" | "market_irrational" | "black_swan"
    root_cause: str
    affected_news_source: Optional[str]  # 如果是数据遗漏，是哪个源
    prompt_improvement_suggestion: str
    confidence_should_be: float  # 基于结果反推的合理置信度
    analyzed_at: str
```

#### 3.1.4 数据库扩展

```sql
-- 新增表: failure_analysis
CREATE TABLE IF NOT EXISTS failure_analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id TEXT NOT NULL,
    failure_type TEXT NOT NULL,
    root_cause TEXT,
    affected_source TEXT,
    prompt_suggestion TEXT,
    should_confidence REAL,
    analyzed_at TEXT NOT NULL,
    FOREIGN KEY (prediction_id) REFERENCES predictions(id)
);
```

### 3.2 新闻源权重自适应

#### 3.2.1 核心思路

```
问题: 东方财富和新浪的新闻质量不同，谁更有预测力？
方案: 追踪每个源的历史准确率，动态调整预处理时该源的权重
```

#### 3.2.2 实现方案

```python
# 新增: src/core/source_weight_manager.py

class SourceWeightManager:
    """基于历史准确率的新闻源权重管理"""
    
    def __init__(self, prediction_store: PredictionStore):
        self.store = prediction_store
        # 初始先验权重
        self._weights = {
            "eastmoney": 0.80,  # 东方财富 — 覆盖面广，但散户情绪较重
            "sina": 0.75,       # 新浪财经 — 编辑质量较高
            "xueqiu": 0.50,     # 雪球 — 散户情绪、噪音大
            "yfinance": 0.70,   # yfinance — 英文源
        }
    
    def get_weight(self, source: str) -> float:
        """获取某新闻源的可信度权重"""
        return self._weights.get(source, 0.5)
    
    def update_weights(self):
        """
        从 PredictionStore 读取：
        - 当新闻主要来自源 X 时，预测准确率是多少？
        - 用贝叶斯更新调整权重
        
        伪代码:
        for each source:
            predictions_using_source = query(source=source)
            if len(predictions) > 10:
                accuracy = dir_correct / total
                self._weights[source] = prior * 0.3 + accuracy * 0.7
        """
```

#### 3.3.3 数据库扩展

```sql
-- 在 predictions 表新增字段
ALTER TABLE predictions ADD COLUMN primary_news_source TEXT;
ALTER TABLE predictions ADD COLUMN news_sources_used TEXT;  -- JSON array
```

或者在 `prediction_store.save_prediction()` 中记录 `news_sources_used`（已有字段，但当前未充分使用）。

### 3.3 置信度重新校准 v2

#### 3.3.1 Round 1 的限制

当前校准器 (`confidence_calibrator.py`)：
- ✅ 样本不足时不校准
- ✅ 贝叶斯收缩向历史准确率回归
- ❌ 没有按置信度桶分别校准
- ❌ 没有考虑不同市场（A/HK/US）的差异

#### 3.3.2 v2 升级

```python
class ConfidenceCalibratorV2(ConfidenceCalibrator):
    """升级版：分桶校准 + 市场区分"""
    
    BUCKETS = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0)]
    
    def calibrate(self, agent_name, raw_confidence, data_quality=1.0, market=None):
        total = self._get_total(agent_name)
        
        if total < 10:
            return raw_confidence  # 样本不足
        
        if total < 50:
            # 贝叶斯收缩（同 v1）
            pass
        else:
            # 按置信度桶分别校准
            bucket = self._find_bucket(raw_confidence)
            bucket_acc = self._get_bucket_accuracy(agent_name, bucket, market)
            calibrated = bucket_acc if bucket_acc > 0 else raw_confidence
        
        return calibrated
```

#### 3.3.3 需要的数据

```sql
-- 新增: confidence_calibration_data
-- 需要按置信度范围分组统计实际准确率
SELECT 
    CASE 
        WHEN confidence < 0.2 THEN '0.0-0.2'
        WHEN confidence < 0.4 THEN '0.2-0.4'
        WHEN confidence < 0.6 THEN '0.4-0.6'
        WHEN confidence < 0.8 THEN '0.6-0.8'
        ELSE '0.8-1.0'
    END as bucket,
    COUNT(*) as total,
    AVG(CASE WHEN direction_correct=1 THEN 1.0 ELSE 0.0 END) as actual_accuracy
FROM predictions
WHERE verified_at IS NOT NULL
GROUP BY bucket;
```

---

## 4. 数据源深化

### 4.1 雪球热帖集成

#### 4.1.1 价值

| 维度 | 说明 |
|------|------|
| 散户情绪 | 雪球是中国最大的投资者社区，讨论热度反映散户关注度 |
| 异常预警 | 讨论量突然暴增常预示重大事件 |
| 情绪补充 | 与东方财富（偏编辑）形成互补——编辑说"利好"但散户说"骗炮"时，是重要信号 |

#### 4.1.2 技术方案

```python
# 新增: src/data/news_sources/xueqiu.py

async def fetch_from_xueqiu(symbol: str, market: str = "A", max_items: int = 10):
    """
    获取雪球个股热门讨论
    
    方案 A: 雪球搜索 API（需要 cookie）
    URL: https://xueqiu.com/statuses/search.json?count=10&comment=0&symbol={code}&type=11
    
    方案 B: 爬取个股页面热帖（更稳定但更慢）
    URL: https://xueqiu.com/S/{code}
    
    推荐方案 B，原因：
    - 不需要 cookie 登录
    - 数据更稳定
    - 失败率低
    """
    import requests
    from bs4 import BeautifulSoup
    
    xq_code = _to_xueqiu_code(symbol, market)
    url = f"https://xueqiu.com/S/{xq_code}"
    
    # 先访问首页获取 cookie
    session = requests.Session()
    session.get("https://xueqiu.com", headers={...})
    
    resp = session.get(url, headers={...})
    soup = BeautifulSoup(resp.text, "html.parser")
    
    # 提取热门讨论
    items = []
    for article in soup.select(".article-item"):
        title = article.select_one(".title").get_text(strip=True)
        reply_count = article.select_one(".reply-count").get_text(strip=True)
        items.append({
            "title": title,
            "summary": title,
            "source": f"雪球(回复{reply_count})",
            "time": _extract_time(article),
            "url": article.select_one("a")["href"],
        })
        if len(items) >= max_items:
            break
    
    return items
```

#### 4.1.3 雪球数据的特殊处理

雪球的数据与新闻不同，需要在预处理时特殊处理：
- 来源可信度设为 0.4（自媒体为主）
- 不参与情感预标注（规则标注效果差），标记为 `_sentiment: "unknown"`
- 提供 `hot_topic_count` 和 `avg_reply_count` 作为热度指标
- 在 anomaly_flags 中增加 `retail_divergence`：雪球情绪 vs 机构新闻情绪是否分化

### 4.2 美股数据源补充

#### 4.2.1 Alpha Vantage News API

```python
# 新增: src/data/news_sources/alpha_vantage.py

async def fetch_from_alpha_vantage(symbol: str, max_items: int = 10):
    """
    Alpha Vantage News Sentiment API
    
    免费额度: 25 requests/day
    优势: 自带情感分 (relevance_score, sentiment_score)
    """
    api_key = os.getenv("ALPHA_VANTAGE_API_KEY", "")
    if not api_key:
        return None
    
    url = f"https://www.alphavantage.co/query"
    params = {
        "function": "NEWS_SENTIMENT",
        "tickers": symbol,
        "apikey": api_key,
        "limit": max_items,
    }
    
    resp = requests.get(url, params=params, timeout=15)
    data = resp.json()
    
    items = []
    for feed in data.get("feed", []):
        items.append({
            "title": feed.get("title", ""),
            "summary": feed.get("summary", "")[:300],
            "source": feed.get("source", "Alpha Vantage"),
            "time": feed.get("time_published", ""),
            "url": feed.get("url", ""),
            "_av_sentiment": feed.get("overall_sentiment_score", 0),
        })
    
    return items
```

#### 4.2.2 环境变量

```bash
# .env 新增
ALPHA_VANTAGE_API_KEY=your-free-key  # 免费注册: https://www.alphavantage.co/support/#api-key
```

### 4.3 数据源健康监控

```python
# 新增: src/core/source_health_monitor.py

class SourceHealthMonitor:
    """监控各新闻源的可用性和时效性"""
    
    def check(self) -> dict:
        """
        返回各源的健康状态:
        {
            "eastmoney": {"status": "healthy", "last_success": "2026-07-03 10:00", "avg_latency_ms": 1200},
            "sina": {"status": "degraded", "last_success": "2026-07-02 15:00", "avg_latency_ms": 3500},
            ...
        }
        """
    
    def is_source_reliable(self, source: str) -> bool:
        """某源最近是否可靠（可用率 > 80%）"""
```

---

## 5. 推理质量升级

### 5.1 RAG 历史案例检索

#### 5.1.1 为什么需要

两步 CoT 虽然提升了推理质量，但仍是"无记忆"推理。LLM 不知道：
- "类似新闻组合在历史上对这只股票的影响"
- "这类事件（如回购公告）在 A 股的统计影响"

#### 5.1.2 方案

复用项目中已有的 `case_retriever.py`（ChromaDB 向量检索），在 CoT Step 2 之前注入相似历史案例。

```python
# news_analyst.py 中新增

async def _retrieve_similar_cases(self, signals: dict, target: str) -> list[dict]:
    """检索历史上类似的新闻情境"""
    try:
        from src.core.case_retriever import CaseRetriever
        
        retriever = CaseRetriever()
        query_text = json.dumps(signals.get("signals", []), ensure_ascii=False)
        cases = await retriever.search(query_text, top_k=3)
        
        return [
            {
                "date": c["predicted_at"],
                "signals": c["key_factors"],
                "predicted_direction": c["direction"],
                "actual_direction": c.get("actual_direction", "待验证"),
                "outcome": "正确" if c.get("direction_correct") else "错误" if c.get("verified") else "待验证",
            }
            for c in cases
        ]
    except Exception as e:
        logger.debug(f"RAG 检索跳过: {e}")
        return []

# 在 _build_synthesis_prompt 中拼接历史案例
```

#### 5.1.3 注意事项

- RAG 是可选的增强，不可用时不影响主流程
- 仅在样本充足（>20 条已验证预测）时启用
- 避免引入"近因偏差"——历史案例仅供参考，不替代当前分析

### 5.2 辟谣/反转事件检测

#### 5.2.1 场景

```
T日 10:00: "传XX公司将被收购" → 新闻分析师标记为利好
T日 14:00: "XX公司否认被收购传闻" → 反转
```

如果没有检测辟谣新闻，Agent 会持续给出基于"收购传闻"的利好判断。

#### 5.2.2 方案

在预处理管线中新增"辟谣检测"步骤：

```python
# news_preprocessor.py 中新增

class RumorRefutationDetector:
    """检测辟谣/反转新闻"""
    
    REFUTATION_KEYWORDS = [
        "否认", "辟谣", "澄清", "不属实", "传闻不实",
        "尚未", "未收到", "未涉及", "暂无计划",
        "denied", "clarify", "not true", "no plan",
    ]
    
    def detect(self, items: list[dict]) -> list[dict]:
        """标记可能的辟谣新闻，并找到对应的原始传闻"""
        for item in items:
            title = item.get("title", "")
            if any(kw in title for kw in self.REFUTATION_KEYWORDS):
                item["_is_refutation"] = True
                # 尝试找到被辟谣的原始新闻
                # 模糊匹配标题中的关键词
        return items
```

### 5.3 多模型交叉验证（低成本版）

#### 5.3.1 为什么

不同 LLM 对同一新闻的解读可能有差异。DeepSeek 说"利好"，Qwen 可能说"中性"。交叉验证可以降低单一模型的偏差。

#### 5.3.2 低成本方案

```python
async def _cross_validate(self, result: AnalysisResult, data: dict) -> dict:
    """
    用更短、更便宜的 prompt 做二次判断（仅信号提取阶段的简化版）
    
    如果主模型和验证模型的方向一致 → confidence 不调整
    如果不一致 → confidence 降低 10-15%
    """
    # 仅在有分歧时做二次验证，节省 API 费用
    pass
```

> **注意**：此功能仅在 API 预算充足时开启。初始阶段可暂时跳过。

---

## 6. 性能与可靠性

### 6.1 LLM 响应缓存

```python
# 新增: src/core/llm_cache.py

class SimpleLLMCache:
    """
    简单的 prompt 相似度缓存
    
    策略:
    - 相同标的 + 相同周期 + 相近时间的请求，复用之前的 LLM 结果
    - 新闻数据不同时不复用（通过预处理摘要 hash 判断）
    """
    
    def __init__(self, ttl_minutes: int = 60):
        self.cache = {}
        self.ttl = ttl_minutes
    
    def get(self, key: str) -> Optional[str]:
        ...
    
    def set(self, key: str, value: str):
        ...
```

但需要注意：**不要缓存新闻分析本身**（因为新闻在变化），而是缓存信号提取中的"通用常识"部分（如"回购一般来说是利好"这类知识性推理）。

### 6.2 数据源健康监控 + 自动切换

当主源（东方财富）不可用时，自动提升备用源的权重，而不是简单降级。

```python
# 在 news_fetcher.py 的 fetch() 中
health = source_health_monitor.check()

if health["eastmoney"]["status"] == "down":
    # 东方财富挂了，临时提升新浪的 max_items
    sina_max = self.max_items * 2
    logger.warning("东方财富不可用，切换到新浪为主源")
```

### 6.3 异常检测增强

Round 1 已有基础的异常检测（情绪分化、新闻量暴增）。Round 2 增强：

| 异常类型 | 检测方法 | 处理 |
|---------|---------|------|
| 辟谣反转 | 关键词检测 "否认/辟谣" | 标记 `_is_refutation`，confidence 降低 |
| 标题党 | 标题情绪 vs 正文情绪不一致 | 标记 `_clickbait`，降低该条权重 |
| 旧闻重发 | 同标题 + 不同时间 | 去重时检查时间差异 |
| 来源单一 | 所有 top_news 来自同一源 | 标记 `single_source_risk` |

---

## 7. 实施路线图

### Phase C: 自进化闭环（2-3 周）🟡 P1

| 任务 | 文件 | 工作量 | 依赖 |
|------|------|--------|------|
| ① PredictionStore schema 扩展 | `schema.sql`, `prediction_store.py` | 0.5 天 | — |
| ② 失败案例自动诊断 | 新增 `src/core/failure_analyzer.py` | 2 天 | ① |
| ③ 新闻源权重管理器 | 新增 `src/core/source_weight_manager.py` | 1.5 天 | ① |
| ④ 置信度校准器 v2（分桶） | 更新 `confidence_calibrator.py` | 1.5 天 | ① |
| ⑤ 雪球热帖源 | 新增 `news_sources/xueqiu.py` | 1.5 天 | — |
| ⑥ RAG 历史案例检索集成 | 更新 `news_analyst.py` | 1.5 天 | 复用 `case_retriever.py` |
| ⑦ 测试 | 更新 `test_news_analyst_v2.py` | 1 天 | — |

**预期效果**：
- Agent 能从失败中学习（失败案例诊断）
- 新闻源权重自适应（随着数据积累自动优化）
- 置信度从"感觉"变成"统计"
- 散户情绪维度引入

### Phase D: 深度优化（1 月+）🟢 P2/P3

| 任务 | 说明 | 工作量 |
|------|------|--------|
| 美股 Alpha Vantage | 需注册 API key | 0.5 天 |
| 多模型交叉验证 | 需 API 预算 | 1.5 天 |
| LLM 响应缓存 | 减少重复 API 调用 | 1 天 |
| 数据源健康监控 | 自动切换 | 1 天 |
| 辟谣/反转检测 | 预处理增强 | 0.5 天 |
| 标题党检测 | 标题 vs 正文情感差异 | 0.5 天 |
| Prompt A/B 测试框架 | 对比不同 prompt 效果 | 2 天 |

---

## 附录 A: Round 2 文件变更清单

### 需要修改的文件

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `src/data/news_preprocessor.py` | 扩展 | +辟谣检测、+标题党检测 |
| `src/agents/news_analyst.py` | 扩展 | +RAG 集成、+失败分析回调 |
| `src/core/confidence_calibrator.py` | 升级 | v2：分桶校准 + 市场区分 |
| `src/data/prediction_store.py` | 扩展 | schema 扩展 + 失败分析存储 |
| `src/data/schema.sql` | 扩展 | +failure_analysis 表 |
| `config/agent_config.yaml` | 微调 | 可选：注册新 Agent 变体 |

### 需要新增的文件

| 文件 | 说明 |
|------|------|
| `src/data/news_sources/xueqiu.py` | 雪球热帖源 |
| `src/data/news_sources/alpha_vantage.py` | 美股 Alpha Vantage 新闻 |
| `src/core/failure_analyzer.py` | 失败案例自动诊断 |
| `src/core/source_weight_manager.py` | 新闻源权重自适应 |
| `src/core/source_health_monitor.py` | 数据源健康监控 |
| `src/core/llm_cache.py` | LLM 响应缓存 |

### 新增环境变量

```bash
# .env 新增
ALPHA_VANTAGE_API_KEY=        # Alpha Vantage 免费 API key
ENABLE_RAG_RETRIEVAL=true     # 是否启用 RAG 历史检索
ENABLE_AUTO_FAILURE_ANALYSIS=true  # 是否启用自动失败分析
```

---

## 附录 B: 成功度量

### Round 2 完成后的目标指标

| 指标 | Round 1 目标 | Round 2 目标 | 备注 |
|------|------------|-------------|------|
| 方向准确率（短期） | ≥55% | ≥58% | 自进化 + RAG 贡献 |
| 幅度命中率 | ≥40% | ≥45% | 反射思考减少极端预测 |
| 置信度校准误差 | ≤0.15 | ≤0.10 | 分桶校准贡献 |
| 数据可用率（A股） | ≥95% | ≥98% | 雪球源 + 健康监控 |
| 数据可用率（港股） | ≥90% | ≥95% | 同上 |
| 单次分析耗时（含 CoT） | ~35s | ~25s | LLM 缓存贡献 |
| 自动诊断覆盖率 | 0% | ≥80% | 失败案例自动诊断 |

---

> 📌 **Round 2 核心思想**：从"做一个更好的预测"升级为"建立一个能自我改进的预测系统"。自进化闭环（失败诊断 + 源权重自适应 + 置信度分桶校准）是 Round 2 的灵魂。
