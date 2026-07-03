# 📰 新闻分析师改进方案 — Round 3

> **版本**: v1.0 | **日期**: 2026-07-03 | **前置**: ROUND1 ✅ | ROUND2 ✅

---

## 1. Round 1-2 累计成果

| Round | 核心交付 |
|-------|---------|
| **Round 1** | 预处理管线（去重/情感/分类/衰减）、多源采集(东方财富+新浪)、两步CoT、Few-shot+置信度锚定、一致性校验、置信度校准器、动态公司名解析 |
| **Round 2** | 失败案例自动诊断(`failure_analyzer.py`)、新闻源权重自适应(`source_weight_manager.py`)、LLM响应缓存(`llm_cache.py`) |

### 当前架构全景

```
多源采集（eastmoney + sina）→ 预处理管线 → 两步CoT推理 → 校验+校准
                                                      ↓
                                              PredictionStore
                                                      ↓
                                         failure_analyzer（失败诊断）
                                         source_weight_manager（权重自适应）
                                         confidence_calibrator（置信度校准）
```

---

## 2. Round 2 遗留项 & Round 3 方向

### 2.1 未实现的 Round 2 项

| 项目 | 原因 | Round 3 优先级 |
|------|------|---------------|
| 雪球热帖源 (`xueqiu.py`) | 需要 cookie 模拟和稳定爬虫 | 🟡 中 |
| RAG 历史案例检索集成 | 需要 ChromaDB 正常运作 + 足够样本 | 🟡 中 |
| 辟谣/反转检测 | 预处理增强，需要可靠的关键词库 | 🟢 低 |
| 标题党检测 | 标题 vs 正文情感差异分析 | 🟢 低 |
| 美股 Alpha Vantage 源 | 需要注册 API key | 🟢 低 |

### 2.2 Round 3 核心主题：**推理质量深度优化**

当前新闻分析师的瓶颈已从"数据不足"转移到"推理质量"——数据够了，但 LLM 有时会在边界情况（信号矛盾、情绪分化、突发反转）下给出不够稳定的判断。

---

## 3. Round 3 改进方向

### 3.1 边缘案例 Prompt 增强 🔴 高

**问题**: 当前 few-shot 只有 3 个示例，无法覆盖所有边界情况。LLM 在遇到陌生情境时输出不稳定。

**方案**: 增加边缘案例 few-shot + 输出自检清单

```python
# 新增边缘案例：
# - 所有信号同向但强度都很弱（1-2条低权重新闻）→ confidence < 0.3
# - 重大利好+重大利空同时出现（如回购+减持）→ neutral + 加宽 magnitude
# - 新闻量突然暴增但方向不明（突发但未定性的消息）→ neutral + 高波动区间
# - 来源单一（所有 top news 来自同一源）→ confidence × 0.8

# 自检清单（追加到系统 prompt 末尾）:
SELF_CHECK_CHECKLIST = """
输出前请逐项自检:
- [ ] direction 与加权情感得分方向一致？（不一致需在 reasoning 中解释）
- [ ] confidence 属于正确的锚定范围？（参考置信度表）
- [ ] magnitude 区间是否合理？（单方向>8%需极强证据）
- [ ] reasoning 是否包含四要素？（情绪统计/事件分析/预期差/综合结论）
- [ ] key_factors 和 risks 是否与 reasoning 一致？（不要出现 reasoning 没提到的因素）
"""
```

### 3.2 历史案例注入（轻量版）🟡 中

**方案**: 不依赖 ChromaDB 向量检索，直接从 PredictionStore 查询"同一标的+类似情绪分布"的历史案例。

```python
# 在 news_analyst.py 的 CoT Step 2 前注入
def _get_similar_historical_cases(self, target: str, sentiment_stats: dict) -> list[dict]:
    """从 PredictionStore 查询历史上该标的的表现"""
    # 查询同标的已验证预测
    # 筛选情绪分布相似（正面/负面比例接近）的案例
    # 返回"当时预测方向 + 实际结果"
    pass
```

### 3.3 多源分歧检测与升级 🟡 中

**问题**: 东方财富和新浪可能对同一事件有不同报道角度。当前预处理管线只是合并去重，没有利用这种"多源分歧"作为信号。

**方案**: 检测多源分歧并标注

```python
# 在预处理管线中新增
class SourceDivergenceDetector:
    """
    检测: 东方财富说"利好"但新浪说"利空"？
    → 标记 source_divergence → LLM 应该降低 confidence
    → 也可能是"多空分歧加大"的信号 → 后市波动可能加剧
    """
```

### 3.4 新闻分析师"记忆" 🟢 低

连续分析同一标的时，利用上次的 reasoning 和本次的新增新闻做差异分析：

```
上次预测(3天前): "看涨，基于业绩超预期"
本次新闻: "大股东减持公告"
→ LLM 应该能识别"新信息改变了之前的判断基础"
→ 当前是每次独立分析，无上下文
```

---

## 4. 实施路线图（Round 3）

| 优先级 | 任务 | 工作量 | 预期收益 |
|--------|------|--------|---------|
| 🔴 P0 | 边缘案例 few-shot + 自检清单 | 0.5 天 | 输出稳定性 ↑，边界情况准确率 ↑ |
| 🟡 P1 | 历史案例注入（轻量，SQL查询） | 1 天 | 利用已有验证数据辅助判断 |
| 🟡 P1 | 雪球热帖源 | 1.5 天 | 散户情绪维度 |
| 🟡 P1 | 多源分歧检测 | 0.5 天 | 识别信息不确定性 |
| 🟢 P2 | 辟谣/反转检测 | 0.5 天 | 减少"被辟谣后仍按原方向判断"的错误 |
| 🟢 P3 | Agent 记忆（连续分析上下文） | 1.5 天 | 识别"新信息改变旧判断" |

---

## 附录：文件变更清单

### Round 3 需要修改

| 文件 | 变更 |
|------|------|
| `src/prompts/news_prompts.py` | +边缘案例 few-shot、+自检清单 |
| `src/data/news_preprocessor.py` | +多源分歧检测、+辟谣检测 |
| `src/agents/news_analyst.py` | +历史案例注入、+Agent记忆 |

### Round 3 需要新增

| 文件 | 说明 |
|------|------|
| `src/data/news_sources/xueqiu.py` | 雪球热帖采集 |

---

> 📌 **Round 3 核心**: 数据够了（Round 1-2），现在让 LLM 在边界情况下更稳定、从历史中学习、利用多源信息的不确定性作为信号。
