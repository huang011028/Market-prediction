"""
最新新闻分析师 v2

升级内容（Phase A + B）：
- 结构化用户提示词（利用预处理摘要）
- 两步链式推理（信号提取 → 综合判断+反思）
- 一致性校验增强
- 置信度校准（可选，依赖 PredictionStore）
- 市场区分 prompt

架构：
  gather_data → 采集 + 预处理（NewsFetcher v2）
       │
       ▼
  analyze (覆盖) → Step 1: 信号提取
       │        → Step 2: 综合判断 + 反思
       ▼
  _validate_consistency → 方向 vs 情绪一致性检查
"""

import json
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional

from src.core.base_agent import BaseAgent
from src.core.llm_json import parse_llm_json
from src.core.llm_client import LLMClient
from src.core.prediction_target import PredictionTargetSpec
from src.core.result import Direction, Magnitude
from src.data.news_fetcher import NewsFetcher
from src.data.symbol_resolver import resolve_symbol, identify_market
from src.prompts.dynamic_overrides import build_prompt_with_overrides
from src.prompts.news_prompts import (
    NEWS_SYSTEM_PROMPT,
    SIGNAL_EXTRACTION_PROMPT,
    SYNTHESIS_PROMPT,
    A_SHARE_NEWS_APPENDIX,
    HK_SHARE_NEWS_APPENDIX,
)

logger = logging.getLogger(__name__)


class NewsAnalyst(BaseAgent):
    """最新新闻分析师 v2

    采集并分析标的相关的近期新闻/公告/研报，
    从情绪面和事件驱动角度判断短期价格走向。

    v2 新增：
    - 预处理数据利用（情感统计、分类、异常标记）
    - 两步 CoT 推理
    - 一致性校验
    """

    # A/HK 个股新闻源偶尔很慢；并行运行时其他同步数据源也可能拖慢事件循环。
    # 新闻面宁可晚一些返回 degraded 结论，也不要把已返回的新闻误判为 failed。
    data_timeout_seconds = 120

    def __init__(
        self,
        llm: LLMClient,
        prediction_store=None,
        snapshot_archive=None,
        archive_snapshots: bool = True,
    ):
        super().__init__(
            name="最新新闻分析师",
            description="分析近期相关新闻的情绪方向、重大事件的影响程度",
            llm=llm,
        )
        self.news_fetcher = NewsFetcher(max_items=20)
        self.two_step_max_news_count = 8
        self.two_step_timeout_seconds = 75
        self._prediction_store = prediction_store  # 可选：用于置信度校准
        self._snapshot_archive = snapshot_archive
        if archive_snapshots and self._snapshot_archive is None:
            try:
                from src.data.news_snapshot_archive import NewsSnapshotArchive

                self._snapshot_archive = NewsSnapshotArchive()
            except Exception as e:
                logger.debug(f"新闻快照归档初始化跳过: {e}")
                self._snapshot_archive = None

    def _use_compact_llm_path(self) -> bool:
        return bool(getattr(self.llm, "max_prompt_chars", 0) > 0)

    # ================================================================
    # 数据采集
    # ================================================================

    async def gather_data(self, target: str, timeframe: str) -> dict:
        """获取标的相关的近期新闻（含预处理结果）

        Args:
            target: 股票代码
            timeframe: 预测周期

        Returns:
            包含预处理摘要和新闻列表的字典
        """
        days = self._timeframe_to_days(timeframe)
        info = resolve_symbol(target)
        market = info.market

        try:
            news_data = await self.news_fetcher.fetch(info.symbol, market, days)
            result = news_data.to_agent_dict()

            # 添加市场信息（用于 prompt 选择）
            result["_market"] = market
            result["_resolved_symbol"] = info.symbol
            result["_resolved_name"] = info.name

            # 如果新闻数据不可用
            if news_data.news_source == "unavailable":
                logger.warning(f"未能获取 {info.display_name} 的新闻数据，Agent 将基于知识库分析")
                result["_warning"] = (
                    "新闻数据暂时不可用，以下分析基于 AI 知识库（非实时新闻），仅供参考"
                )

            # 数据质量标记
            result["_data_quality"] = self._assess_data_quality(news_data)

            return result

        except Exception as e:
            logger.error(f"新闻数据获取失败: {e}")
            return self._build_unavailable_data(info, market, days, str(e))

    def _timeframe_to_days(self, timeframe: str) -> int:
        """将中文周期映射为新闻回溯天数"""
        t = timeframe.lower()
        if "长期" in t:
            return 90
        elif "中期" in t:
            return 30
        else:
            return 7

    def _identify_market(self, symbol: str) -> str:
        """识别标的市场"""
        return identify_market(symbol)

    def _assess_data_quality(self, news_data) -> dict:
        """评估数据质量"""
        preproc = news_data.preprocessing_summary or {}
        n = news_data.news_count
        sources = news_data.sources_used

        quality = 1.0

        # 数量惩罚
        if n == 0:
            quality = 0.1
        elif n <= 2:
            quality = 0.3
        elif n <= 5:
            quality = 0.6
        elif n <= 10:
            quality = 0.85

        # 来源加分
        if len(sources) >= 2:
            quality = min(1.0, quality + 0.1)

        # 无来源惩罚
        if news_data.news_source == "unavailable":
            quality = 0.1

        # 预处理异常
        anomaly = preproc.get("anomaly_flags", {})
        if anomaly.get("sentiment_divergence"):
            quality = min(quality, 0.7)
        if anomaly.get("relevance_filter_empty_fallback"):
            quality = min(quality, 0.5)

        return {
            "score": round(quality, 2),
            "news_count": n,
            "sources": sources,
            "is_available": news_data.news_source != "unavailable",
        }

    # ================================================================
    # 两步链式推理（覆盖基类的 analyze）
    # ================================================================

    async def analyze(self, data: dict, context: dict) -> "AnalysisResult":
        """两步链式推理：信号提取 → 综合判断+反思

        如果数据不可用，回退到单 pass 模式（兼容原有逻辑）。
        """
        data_quality = data.get("_data_quality", {})
        is_available = data_quality.get("is_available", True)
        news_count = data_quality.get("news_count", 0)

        if not is_available:
            logger.info("实时新闻不可用，使用确定性低置信兜底结果")
            result = self._build_no_realtime_news_result(data, context)
            return self._finalize_result(result, data, context, {})

        # 新闻极少、新闻较多或慢模型 → 单 pass 模式（节省 token/避免超时）
        if self._use_compact_llm_path() or not self._should_use_two_step(data_quality):
            if news_count <= 2:
                logger.info(f"新闻数据不足（{news_count}条），使用单 pass 模式")
            elif self._use_compact_llm_path():
                logger.info("当前模型启用低延迟路径，新闻分析使用单 pass 模式")
            else:
                logger.info(f"新闻数量较多（{news_count}条），使用单 pass 模式避免超时")
            result = await self._analyze_single_pass(data, context)
            return self._finalize_result(result, data, context, {})

        # 正常模式：两步 CoT
        try:
            return await asyncio.wait_for(
                self._analyze_two_step(data, context),
                timeout=self.two_step_timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning("两步推理超时，回退到单 pass 模式")
            result = await self._analyze_single_pass(data, context)
            return self._finalize_result(result, data, context, {})
        except Exception as e:
            logger.warning(f"两步推理失败 ({e})，回退到单 pass 模式")
            result = await self._analyze_single_pass(data, context)
            return self._finalize_result(result, data, context, {})

    def _should_use_two_step(self, data_quality: dict) -> bool:
        is_available = data_quality.get("is_available", True)
        news_count = data_quality.get("news_count", 0)
        return bool(is_available and 2 < news_count <= self.two_step_max_news_count)

    def _build_unavailable_data(self, info, market: str, days: int, reason: str) -> dict:
        today = datetime.now()
        start_date = today - timedelta(days=max(days, 1))
        company_name = info.name or info.symbol
        return {
            "symbol": info.symbol,
            "company_name": company_name,
            "news_count": 0,
            "date_range": f"{start_date.strftime('%Y-%m-%d')} ~ {today.strftime('%Y-%m-%d')}",
            "news_source": "unavailable",
            "sources_used": [],
            "news_items": [],
            "_market": market,
            "_resolved_symbol": info.symbol,
            "_resolved_name": info.name,
            "_warning": f"新闻数据获取异常，已降级为低置信中性分析: {reason}",
            "_data_quality": {
                "score": 0.1,
                "news_count": 0,
                "sources": [],
                "is_available": False,
                "reason": reason,
            },
        }

    def _build_no_realtime_news_result(self, data: dict, context: dict) -> "AnalysisResult":
        from src.core.result import AnalysisResult

        spec = PredictionTargetSpec.from_dict(context.get("prediction_target"))
        band = max(float(spec.neutral_band_pct or 1.0), 1.0)
        name = data.get("company_name") or data.get("_resolved_name") or context.get("target", "")
        source = data.get("news_source") or "unavailable"
        reason = (
            f"未获取到 {name} 的可用实时新闻，新闻面不提供方向性贡献。"
            "本结果仅表示新闻数据源暂时不可用，不代表公司没有事件风险。"
        )
        return AnalysisResult(
            agent_name=self.name,
            target=context.get("target", ""),
            timeframe=context.get("timeframe", ""),
            direction=Direction.NEUTRAL,
            magnitude=Magnitude(min_pct=-band, max_pct=band),
            confidence=0.08,
            prediction_target=spec,
            reasoning=reason,
            key_factors=[f"实时新闻源不可用: {source}"],
            risks=["新闻源超时或暂时不可访问，短期事件和情绪信号缺失"],
            status="degraded",
            error_message=None,
            data_quality_score=0.1,
        )

    async def _analyze_two_step(self, data: dict, context: dict) -> "AnalysisResult":
        """两步链式推理"""
        from src.core.result import AnalysisResult

        market = data.get("_market", "A")

        # === Step 1: 信号提取 ===
        logger.debug("Step 1/2: 信号提取...")
        signals = await self._step_extract_signals(data, context, market)

        # === Step 2: 综合判断 + 反思 ===
        logger.debug("Step 2/2: 综合判断 + 反思...")
        result = await self._step_synthesize(signals, data, context, market)

        return self._finalize_result(result, data, context, signals)

    async def _step_extract_signals(
        self, data: dict, context: dict, market: str
    ) -> dict:
        """Step 1: 从新闻中提取关键信号"""
        system_prompt = self._get_system_prompt()
        signal_prompt = self._build_signal_extraction_prompt(data, context, market)

        response = await self.llm.achat(
            system_prompt=system_prompt,
            user_prompt=signal_prompt,
        )

        content = response.content
        parsed = parse_llm_json(content)
        if parsed.ok and isinstance(parsed.data, dict):
            if parsed.repaired:
                parsed.data["_llm_json_repaired"] = True
                parsed.data["_llm_json_repairs"] = parsed.repairs
            return parsed.data
        logger.warning(f"信号提取 JSON 解析失败，使用原始文本: {parsed.error}")
        return {
            "signals": [],
            "raw_output": content,
            "noise_discarded": [],
            "llm_json_error": parsed.error,
            "llm_json_repairs_attempted": parsed.repairs,
        }

    async def _step_synthesize(
        self, signals: dict, data: dict, context: dict, market: str
    ) -> "AnalysisResult":
        """Step 2: 基于信号综合判断 + 魔鬼代言人反思"""
        from src.core.result import AnalysisResult

        system_prompt = self._get_system_prompt()
        synthesis_prompt = self._build_synthesis_prompt(
            signals, data, context, market
        )

        response = await self.llm.achat(
            system_prompt=system_prompt,
            user_prompt=synthesis_prompt,
        )

        result = self._parse_llm_response(response.content, context)

        # 应用置信度校准
        calibrated_conf = self._calibrate_confidence(result.confidence, data)
        if calibrated_conf != result.confidence:
            logger.debug(
                f"置信度校准: {result.confidence:.2f} → {calibrated_conf:.2f}"
            )
            result.confidence = calibrated_conf

        return result

    async def _analyze_single_pass(self, data: dict, context: dict) -> "AnalysisResult":
        """单 pass 模式（数据不足时的回退方案）"""
        from src.core.result import AnalysisResult

        system_prompt = self._get_system_prompt()
        user_prompt = self._build_user_prompt(data, context)

        response = await self.llm.achat(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

        result = self._parse_llm_response(response.content, context)

        # 数据不足时确保 confidence 不虚高
        news_count = data.get("news_count", 0)
        if news_count <= 2 and result.confidence > 0.25:
            result.confidence = 0.25
            result.reasoning += "\n\n[自动校准：新闻不足，置信度上限设为 0.25]"

        return result

    # ================================================================
    # Prompt 构建
    # ================================================================

    def _get_system_prompt(self) -> str:
        return build_prompt_with_overrides(NEWS_SYSTEM_PROMPT, self.name)

    def _build_user_prompt(self, data: dict, context: dict) -> str:
        """构建用户提示词（v2：利用预处理数据）"""
        market = data.get("_market", "A")
        preproc = data.get("preprocessing", {})

        # 不可用提示
        unavailable_note = ""
        if data.get("news_source") == "unavailable":
            unavailable_note = (
                "\n\n⚠️ **重要提示**：当前无法获取实时新闻数据。"
                "请基于你对这个标的的知识进行合理分析，"
                "但必须在 reasoning 开头标注'[注：基于知识库信息，非实时新闻数据]'，"
                "并适当降低 confidence（建议不超过 0.4）。\n"
            )

        # 预处理摘要（如果有）
        preproc_section = ""
        if preproc:
            preproc_section = self._format_preprocessing_summary(preproc, data)

        # 市场附录
        market_appendix = self._get_market_appendix(market)
        evidence = self._build_evidence_packet(data, context.get("timeframe", ""))
        evidence_str = json.dumps(evidence, ensure_ascii=False, indent=2)

        return f"""请基于以下新闻数据进行分析：

## 分析标的
{context.get('target', 'N/A')}

## 预测周期
{context.get('timeframe', 'N/A')}

## 预测目标规格
```json
{json.dumps(context.get('prediction_target', {}), ensure_ascii=False, indent=2)}
```
{unavailable_note}
{preproc_section}

## 新闻证据包（代码计算，不依赖 LLM）
```json
{evidence_str[:5000]}
```

## 新闻数据
```json
{json.dumps(data.get('news_items', []), ensure_ascii=False, indent=2)[:6000]}
```

{market_appendix}

硬性约束:
- 不得编造未提供的新闻、公告、来源或发布时间。
- 若新闻证据包的方向、事件冲击或置信上限与输出矛盾，必须降低 confidence，并在 risks 中说明。
- confidence 不得超过新闻证据包中的 max_confidence。
- 新闻稀少、来源单一、情绪分化或以传闻为主时，不得给高置信强方向判断。
- 建议输出 prediction_target.expected_return_pct 与 P(涨/跌/中性)，方向需由收益目标派生。

请严格按照要求的 JSON 格式输出分析结果。"""

    def _build_signal_extraction_prompt(
        self, data: dict, context: dict, market: str
    ) -> str:
        """构建信号提取 prompt"""
        preproc = data.get("preprocessing", {})
        preproc_section = self._format_preprocessing_summary(preproc, data)
        market_appendix = self._get_market_appendix(market)
        evidence = self._build_evidence_packet(data, context.get("timeframe", ""))
        evidence_str = json.dumps(evidence, ensure_ascii=False, indent=2)

        return f"""{SIGNAL_EXTRACTION_PROMPT}

## 分析标的
{context.get('target', 'N/A')}

## 预测周期
{context.get('timeframe', 'N/A')}

{preproc_section}

## 新闻证据包（代码计算，不依赖 LLM）
```json
{evidence_str[:5000]}
```

## 新闻数据（前10条）
```json
{json.dumps(data.get('news_items', [])[:10], ensure_ascii=False, indent=2)}
```

{market_appendix}

请提取关键信号。"""

    def _build_synthesis_prompt(
        self, signals: dict, data: dict, context: dict, market: str
    ) -> str:
        """构建综合判断 prompt"""
        preproc = data.get("preprocessing", {})
        preproc_section = self._format_preprocessing_summary(preproc, data)
        market_appendix = self._get_market_appendix(market)
        evidence = self._build_evidence_packet(data, context.get("timeframe", ""))
        evidence_str = json.dumps(evidence, ensure_ascii=False, indent=2)

        signals_str = json.dumps(signals, ensure_ascii=False, indent=2)

        return f"""{SYNTHESIS_PROMPT}

## 分析标的
{context.get('target', 'N/A')}

## 预测周期
{context.get('timeframe', 'N/A')}

{preproc_section}

## 新闻证据包（代码计算，不依赖 LLM）
```json
{evidence_str[:5000]}
```

## Step 1 提取的信号
```json
{signals_str[:5000]}
```

{market_appendix}

硬性约束:
- 不得编造未提供的新闻、公告、来源或发布时间。
- 若证据包 suggested_direction 与最终方向不同，必须解释原因并降低 confidence。
- confidence 不得超过证据包 max_confidence。
- 情绪分化、来源单一、新闻稀少、传闻主导时，优先中性或低置信。

请综合信号做出最终判断，并进行魔鬼代言人反思。"""

    @staticmethod
    def _format_preprocessing_summary(preproc: dict, data: dict) -> str:
        """格式化预处理摘要为 markdown"""
        sentiment = preproc.get("sentiment_stats", {})
        categories = preproc.get("category_breakdown", {})
        anomaly = preproc.get("anomaly_flags", {})
        data_quality = data.get("_data_quality", {})

        lines = ["## 📊 预处理摘要（自动标注，仅供参考）", ""]

        # 数据来源
        sources = data.get("sources_used", [])
        lines.append(f"- **数据源**: {', '.join(sources) if sources else '无'}")
        lines.append(f"- **数据质量**: {data_quality.get('score', 0):.0%}")

        # 数量统计
        lines.append(
            f"- **新闻数量**: 原始{preproc.get('total_fetched', 0)}条 → "
            f"去重{preproc.get('after_dedup', 0)}条 → "
            f"过滤{preproc.get('after_relevance_filter', 0)}条"
        )

        # 情感统计
        lines.append("")
        lines.append("### 情感统计")
        lines.append(
            f"- 正面: {sentiment.get('positive', 0)}条 | "
            f"负面: {sentiment.get('negative', 0)}条 | "
            f"中性: {sentiment.get('neutral', 0)}条 | "
            f"未知: {sentiment.get('unknown', 0)}条"
        )
        lines.append(
            f"- 加权得分: 正面 {sentiment.get('weighted_positive_score', 0)}, "
            f"负面 {sentiment.get('weighted_negative_score', 0)}"
        )

        # 分类统计
        if categories:
            lines.append("")
            lines.append("### 事件分类")
            cat_str = ", ".join(f"{k}:{v}条" for k, v in sorted(categories.items()))
            lines.append(f"- {cat_str}")

        # 异常标记
        if anomaly:
            lines.append("")
            lines.append("### ⚠️ 异常标记")
            for key, value in anomaly.items():
                if isinstance(value, bool) and value:
                    detail_key = f"{key}_detail"
                    detail = anomaly.get(detail_key, key)
                    lines.append(f"- {detail}")

        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _get_market_appendix(market: str) -> str:
        """获取市场区分附录"""
        if market == "A":
            return A_SHARE_NEWS_APPENDIX
        elif market == "HK":
            return HK_SHARE_NEWS_APPENDIX
        return ""

    # ================================================================
    # 结构化新闻证据（供 Prompt/API/Aggregator 消费）
    # ================================================================

    def _finalize_result(
        self,
        result: "AnalysisResult",
        data: dict,
        context: dict,
        step_signals: dict,
    ) -> "AnalysisResult":
        """统一应用新闻校验、证据约束和结构化摘要。"""
        consistency_issues = self._validate_consistency(result, data)
        result = self._apply_consistency_issues(result, consistency_issues)
        evidence_issues = self._apply_evidence_constraints(result, data, context)
        all_issues = consistency_issues + evidence_issues

        result.data_summary = self._build_data_summary(
            data,
            context,
            step_signals,
            all_issues,
        )
        result.data_quality_score = self._safe_float(
            data.get("_data_quality", {}).get("score"), 0.0
        )
        if result.data_quality_score < 0.4 and result.status == "ok":
            result.status = "degraded"

        self._archive_news_snapshot(result, data, context, step_signals)
        return result

    def _archive_news_snapshot(
        self,
        result: "AnalysisResult",
        data: dict,
        context: dict,
        step_signals: dict,
    ) -> None:
        """把本次新闻分析保存为可回放快照。"""
        archive = getattr(self, "_snapshot_archive", None)
        if archive is None:
            return
        try:
            meta = archive.save_analysis_snapshot(
                target=context.get("target") or data.get("symbol"),
                timeframe=context.get("timeframe", ""),
                news_data=data,
                result=result,
                step_signals=step_signals,
            )
            if meta:
                result.data_summary["news_snapshot"] = meta
        except Exception as e:
            logger.debug(f"新闻快照归档失败: {e}")

    @staticmethod
    def _safe_float(value, default: float = 0.0) -> float:
        try:
            if value in (None, "", "N/A"):
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _get_news_items(data: dict) -> list[dict]:
        preproc = data.get("preprocessing", {}) or {}
        items = preproc.get("top_news") or data.get("news_items") or []
        return items if isinstance(items, list) else []

    def _build_evidence_packet(self, data: dict, timeframe: str = "") -> dict:
        """提取代码计算的新闻证据，减少最终判断对自由文本的依赖。"""
        preproc = data.get("preprocessing", {}) or {}
        data_quality = data.get("_data_quality", {}) or {}
        signals = self._derive_news_signals(data, timeframe)

        return {
            "identity": {
                "symbol": data.get("symbol") or data.get("_resolved_symbol"),
                "name": data.get("company_name") or data.get("_resolved_name"),
                "market": data.get("_market"),
                "news_source": data.get("news_source"),
            },
            "news_window": {
                "date_range": data.get("date_range"),
                "news_count": data.get("news_count", data_quality.get("news_count", 0)),
                "sources_used": data.get("sources_used", data_quality.get("sources", [])),
            },
            "source_quality": {
                "quality_score": data_quality.get("score", 0.0),
                "is_available": data_quality.get(
                    "is_available", data.get("news_source") != "unavailable"
                ),
                "source_count": len(data.get("sources_used") or data_quality.get("sources") or []),
            },
            "sentiment_stats": preproc.get("sentiment_stats", {}),
            "category_breakdown": preproc.get("category_breakdown", {}),
            "anomaly_flags": preproc.get("anomaly_flags", {}),
            "decision_matrix": signals["decision_matrix"],
            "event_impact_matrix": signals["event_impact_matrix"],
            "evidence": signals["evidence"],
            "top_news_snapshot": signals["top_news_snapshot"],
            "confidence_constraints": signals["confidence_model"],
        }

    def _derive_news_signals(self, data: dict, timeframe: str = "") -> dict:
        """用情绪、事件、来源和时效生成新闻矩阵、证据列表和置信度硬上限。"""
        preproc = data.get("preprocessing", {}) or {}
        sentiment = preproc.get("sentiment_stats", {}) or {}
        categories = preproc.get("category_breakdown", {}) or {}
        anomaly = preproc.get("anomaly_flags", {}) or {}
        data_quality = data.get("_data_quality", {}) or {}
        news_items = self._get_news_items(data)

        news_count = int(
            self._safe_float(
                data.get("news_count", data_quality.get("news_count", len(news_items))),
                len(news_items),
            )
        )
        sources = data.get("sources_used") or data_quality.get("sources") or []
        source_count = len(sources)
        is_available = data_quality.get(
            "is_available", data.get("news_source") != "unavailable"
        )
        quality_score = self._safe_float(data_quality.get("score"), 0.0)

        wp = self._safe_float(sentiment.get("weighted_positive_score"), 0.0)
        wn = self._safe_float(sentiment.get("weighted_negative_score"), 0.0)
        pos_count = int(self._safe_float(sentiment.get("positive"), 0.0))
        neg_count = int(self._safe_float(sentiment.get("negative"), 0.0))
        neutral_count = int(self._safe_float(sentiment.get("neutral"), 0.0))
        unknown_count = int(self._safe_float(sentiment.get("unknown"), 0.0))
        weighted_total = max(wp + wn, 0.01)
        spread = round(wp - wn, 2)
        spread_ratio = spread / weighted_total

        if not is_available or news_count <= 0:
            volume_bucket = "no_data"
            volume_label = "无实时新闻"
        elif news_count <= 2:
            volume_bucket = "sparse"
            volume_label = "新闻稀少"
        elif news_count <= 5:
            volume_bucket = "limited"
            volume_label = "新闻有限"
        elif news_count <= 12:
            volume_bucket = "adequate"
            volume_label = "新闻充足"
        else:
            volume_bucket = "rich"
            volume_label = "新闻密集"

        if anomaly.get("sentiment_divergence"):
            sentiment_bucket = "divergent"
            sentiment_label = "情绪分化"
        elif pos_count == 0 and neg_count == 0:
            sentiment_bucket = "unknown"
            sentiment_label = "情绪不明"
        elif spread_ratio >= 0.35 or spread >= 1.0:
            sentiment_bucket = "positive"
            sentiment_label = "情绪偏正"
        elif spread_ratio <= -0.35 or spread <= -1.0:
            sentiment_bucket = "negative"
            sentiment_label = "情绪偏负"
        else:
            sentiment_bucket = "neutral"
            sentiment_label = "情绪中性"

        category_weight = {
            "earnings": 1.20,
            "policy": 1.15,
            "corp_action": 1.10,
            "rating": 1.00,
            "product": 0.90,
            "industry": 0.80,
            "rumor": 0.35,
            "other": 0.50,
        }
        positive_event_weight = 0.0
        negative_event_weight = 0.0
        time_weights = []
        for item in news_items:
            time_weight = self._safe_float(item.get("_time_weight"), 0.5)
            time_weights.append(time_weight)
            weight = time_weight * category_weight.get(item.get("_category", "other"), 0.5)
            if item.get("_sentiment") == "positive":
                positive_event_weight += weight
            elif item.get("_sentiment") == "negative":
                negative_event_weight += weight

        dominant_event = None
        dominant_event_count = 0
        if categories:
            dominant_event = max(categories, key=categories.get)
            dominant_event_count = int(categories.get(dominant_event, 0))

        if dominant_event == "rumor" and dominant_event_count >= max(2, news_count // 2):
            event_bucket = "rumor_driven"
            event_label = "传闻主导"
        elif positive_event_weight > 0 and negative_event_weight > 0:
            ratio = min(positive_event_weight, negative_event_weight) / max(
                positive_event_weight, negative_event_weight
            )
            if ratio >= 0.40:
                event_bucket = "mixed"
                event_label = "事件多空并存"
            elif positive_event_weight > negative_event_weight:
                event_bucket = "positive_catalyst"
                event_label = "正面催化"
            else:
                event_bucket = "negative_catalyst"
                event_label = "负面冲击"
        elif positive_event_weight >= 0.80:
            event_bucket = "positive_catalyst"
            event_label = "正面催化"
        elif negative_event_weight >= 0.80:
            event_bucket = "negative_catalyst"
            event_label = "负面冲击"
        else:
            event_bucket = "no_clear_event"
            event_label = "无明确事件"

        suggested_direction = "neutral"
        matrix_reason = "新闻数量、情绪或事件证据不足，默认中性。"
        if volume_bucket in ("no_data", "sparse"):
            suggested_direction = "neutral"
            matrix_reason = "新闻不足，不能形成强方向判断。"
        elif sentiment_bucket == "divergent" or event_bucket in ("mixed", "rumor_driven"):
            suggested_direction = "neutral"
            matrix_reason = "多空分歧或传闻主导，优先降低方向性。"
        elif sentiment_bucket == "positive" and event_bucket != "negative_catalyst":
            suggested_direction = "bullish"
            matrix_reason = "加权情绪偏正且未出现主导性负面事件。"
        elif sentiment_bucket == "negative" and event_bucket != "positive_catalyst":
            suggested_direction = "bearish"
            matrix_reason = "加权情绪偏负且未出现主导性正面催化。"
        elif event_bucket == "positive_catalyst" and spread >= 0:
            suggested_direction = "bullish"
            matrix_reason = "正面事件权重占优，情绪未明显抵触。"
        elif event_bucket == "negative_catalyst" and spread <= 0:
            suggested_direction = "bearish"
            matrix_reason = "负面事件权重占优，情绪未明显抵触。"

        bullish = []
        bearish = []
        neutral = []
        if pos_count or wp:
            bullish.append(f"正面新闻{pos_count}条，加权正面得分{wp:.2f}")
        if neg_count or wn:
            bearish.append(f"负面新闻{neg_count}条，加权负面得分{wn:.2f}")
        if neutral_count or unknown_count:
            neutral.append(f"中性/未知新闻{neutral_count + unknown_count}条")
        if dominant_event:
            target_list = neutral
            if event_bucket == "positive_catalyst":
                target_list = bullish
            elif event_bucket == "negative_catalyst":
                target_list = bearish
            target_list.append(f"主导事件类别 {dominant_event}:{dominant_event_count}条")
        if anomaly.get("sentiment_divergence"):
            neutral.append(anomaly.get("sentiment_divergence_detail", "正负面情绪分化"))
        if anomaly.get("sudden_volume_spike"):
            neutral.append(anomaly.get("volume_spike_detail", "新闻量突然放大"))
        if source_count <= 1:
            neutral.append("新闻来源单一")
        if not is_available:
            neutral.append("实时新闻不可用")

        avg_time_weight = (
            round(sum(time_weights) / len(time_weights), 2) if time_weights else 0.0
        )
        unknown_ratio = (
            (neutral_count + unknown_count) / max(1, pos_count + neg_count + neutral_count + unknown_count)
        )

        max_confidence = 0.75 if suggested_direction != "neutral" else 0.60
        hard_caps = []
        if not is_available or news_count <= 0:
            max_confidence = min(max_confidence, 0.25)
            hard_caps.append("无实时新闻，confidence 不超过0.25")
        elif news_count <= 2:
            max_confidence = min(max_confidence, 0.35)
            hard_caps.append("新闻数量不超过2条，confidence 不超过0.35")
        elif news_count <= 5:
            max_confidence = min(max_confidence, 0.55)
            hard_caps.append("新闻数量不超过5条，confidence 不超过0.55")

        if quality_score and quality_score < 0.30:
            max_confidence = min(max_confidence, 0.35)
            hard_caps.append("数据质量低于30%，confidence 不超过0.35")
        elif quality_score and quality_score < 0.50:
            max_confidence = min(max_confidence, 0.50)
            hard_caps.append("数据质量低于50%，confidence 不超过0.50")

        if source_count <= 1 and news_count > 0:
            max_confidence = min(max_confidence, 0.60)
            hard_caps.append("新闻来源单一，confidence 不超过0.60")
        if sentiment_bucket == "divergent":
            max_confidence = min(max_confidence, 0.55)
            hard_caps.append("情绪分化，confidence 不超过0.55")
        if event_bucket == "mixed":
            max_confidence = min(max_confidence, 0.55)
            hard_caps.append("正负事件并存，confidence 不超过0.55")
        if event_bucket == "rumor_driven":
            max_confidence = min(max_confidence, 0.45)
            hard_caps.append("传闻主导，confidence 不超过0.45")
        if news_count > 0 and avg_time_weight < 0.40:
            max_confidence = min(max_confidence, 0.55)
            hard_caps.append("新闻时效偏弱，confidence 不超过0.55")
        if unknown_ratio >= 0.70 and news_count > 0:
            max_confidence = min(max_confidence, 0.55)
            hard_caps.append("多数新闻情绪未知或中性，confidence 不超过0.55")

        top_news_snapshot = [
            {
                "title": item.get("title"),
                "source": item.get("source"),
                "time": item.get("time") or item.get("publish_time"),
                "sentiment": item.get("_sentiment"),
                "category": item.get("_category"),
                "time_weight": item.get("_time_weight"),
            }
            for item in news_items[:5]
        ]

        return {
            "decision_matrix": {
                "volume_bucket": volume_bucket,
                "volume_label": volume_label,
                "sentiment_bucket": sentiment_bucket,
                "sentiment_label": sentiment_label,
                "event_bucket": event_bucket,
                "event_label": event_label,
                "matrix_position": f"{volume_label}+{sentiment_label}+{event_label}",
                "suggested_direction": suggested_direction,
                "reason": matrix_reason,
            },
            "event_impact_matrix": {
                "dominant_event": dominant_event,
                "dominant_event_count": dominant_event_count,
                "positive_event_weight": round(positive_event_weight, 2),
                "negative_event_weight": round(negative_event_weight, 2),
                "sentiment_spread": spread,
                "sentiment_spread_ratio": round(spread_ratio, 2),
                "average_time_weight": avg_time_weight,
                "source_count": source_count,
            },
            "evidence": {
                "bullish": bullish,
                "bearish": bearish,
                "neutral": neutral,
            },
            "top_news_snapshot": top_news_snapshot,
            "confidence_model": {
                "max_confidence": round(max_confidence, 2),
                "quality_score": quality_score,
                "hard_caps": hard_caps,
            },
        }

    # ================================================================
    # 一致性校验（v2 新增）
    # ================================================================

    def _validate_consistency(
        self, result: "AnalysisResult", data: dict
    ) -> list[str]:
        """校验分析结果与数据的一致性"""
        issues = []
        preproc = data.get("preprocessing", {})
        sentiment = preproc.get("sentiment_stats", {})
        data_quality = data.get("_data_quality", {})

        # 1. 方向 vs 加权情绪一致性
        wp = sentiment.get("weighted_positive_score", 0)
        wn = sentiment.get("weighted_negative_score", 0)
        if result.direction.value == "bullish" and wn > wp * 1.5:
            issues.append(
                f"方向看涨但加权负面得分({wn})远高于正面({wp})——请检查是否有遗漏的利空因素"
            )
        if result.direction.value == "bearish" and wp > wn * 1.5:
            issues.append(
                f"方向看跌但加权正面得分({wp})远高于负面({wn})——请检查是否有遗漏的利好因素"
            )

        # 2. 高置信度 vs 低数据量
        news_count = data_quality.get("news_count", 0)
        if result.confidence > 0.6 and news_count < 5:
            issues.append(
                f"高置信度({result.confidence:.0%})但新闻数量仅{news_count}条，可能过度自信"
            )
        if result.confidence > 0.4 and news_count == 0:
            issues.append("无数据状态下不应有有意义的置信度")

        # 3. 方向与 magnitude 一致性
        if result.magnitude:
            if result.direction.value == "bullish" and result.magnitude.max_pct <= 0:
                issues.append("看涨但幅度上限 ≤ 0%，存在矛盾")
            if result.direction.value == "bearish" and result.magnitude.min_pct >= 0:
                issues.append("看跌但幅度下限 ≥ 0%，存在矛盾")

        # 4. 异常标记检查
        anomaly = preproc.get("anomaly_flags", {})
        if anomaly.get("sentiment_divergence") and result.direction.value != "neutral":
            issues.append(
                "预处理检测到情绪分化（正负面新闻势均力敌），但方向非 neutral——"
                "情绪分化时建议方向设为 neutral 或 confidence ≤ 0.55"
            )

        return issues

    def _apply_consistency_issues(
        self, result: "AnalysisResult", issues: list[str]
    ) -> "AnalysisResult":
        """把一致性校验结果写回 AnalysisResult，供 Aggregator 和前端消费。"""
        if not issues:
            return result

        if result.risks is None:
            result.risks = []

        existing_risks = set(result.risks or [])
        for issue in issues:
            risk = f"新闻一致性校验: {issue}"
            if risk not in existing_risks:
                result.risks.append(risk)
                existing_risks.add(risk)

        result.reasoning += (
            "\n\n---\n"
            "⚠️ **新闻一致性校验提示**: " + "；".join(issues)
        )

        severe_markers = (
            "过度自信",
            "无数据",
            "情绪分化",
            "存在矛盾",
            "远高于",
        )
        if any(marker in issue for issue in issues for marker in severe_markers):
            old_confidence = result.confidence
            result.confidence = round(max(0.05, min(result.confidence, result.confidence * 0.85)), 2)
            if result.status == "ok":
                result.status = "degraded"
            logger.info(
                f"[{self.name}] 新闻一致性降权: {old_confidence:.0%} → {result.confidence:.0%}"
            )

        return result

    def _apply_evidence_constraints(
        self, result: "AnalysisResult", data: dict, context: dict
    ) -> list[str]:
        """用新闻矩阵和硬上限约束 LLM 输出。"""
        signals = self._derive_news_signals(data, context.get("timeframe", ""))
        matrix = signals["decision_matrix"]
        confidence_model = signals["confidence_model"]
        suggested = matrix.get("suggested_direction", "neutral")
        max_conf = self._safe_float(confidence_model.get("max_confidence"), 0.60)
        issues = []

        if result.direction.value != suggested and result.confidence > 0.50:
            if suggested == "neutral":
                issues.append(
                    f"新闻矩阵建议neutral，但 LLM 输出{result.direction.value}"
                )
            else:
                issues.append(
                    f"新闻矩阵建议{suggested}，但 LLM 输出{result.direction.value}"
                )
            max_conf = min(max_conf, 0.50)

        if result.confidence > max_conf:
            issues.append(
                f"confidence({result.confidence:.2f})超过新闻证据上限({max_conf:.2f})"
            )
            result.confidence = round(max_conf, 2)

        if not issues:
            return []

        if result.risks is None:
            result.risks = []
        existing = set(result.risks)
        for issue in issues:
            risk = f"新闻证据约束: {issue}"
            if risk not in existing:
                result.risks.append(risk)
                existing.add(risk)

        result.reasoning += (
            "\n\n---\n"
            "⚠️ **新闻证据约束提示**: " + "；".join(issues)
        )
        if result.status == "ok":
            result.status = "degraded"
        return issues

    def _build_data_summary(
        self,
        data: dict,
        context: dict,
        step_signals: dict,
        consistency_issues: list[str],
    ) -> dict:
        """输出给 API/Aggregator 的结构化新闻摘要。"""
        preproc = data.get("preprocessing", {}) or {}
        data_quality = data.get("_data_quality", {}) or {}
        step_signal_summary = {}
        if isinstance(step_signals, dict):
            step_signal_summary = {
                "signals": (step_signals.get("signals") or [])[:8],
                "noise_discarded": (step_signals.get("noise_discarded") or [])[:8],
            }
            if step_signals.get("raw_output") and not step_signal_summary["signals"]:
                step_signal_summary["raw_output"] = str(step_signals.get("raw_output"))[:1000]

        return {
            "symbol": data.get("symbol") or data.get("_resolved_symbol"),
            "name": data.get("company_name") or data.get("_resolved_name"),
            "market": data.get("_market"),
            "timeframe": context.get("timeframe"),
            "source": data.get("news_source", "unknown"),
            "sources_used": data.get("sources_used", []),
            "date_range": data.get("date_range"),
            "news_count": data.get("news_count", data_quality.get("news_count", 0)),
            "quality": data_quality.get("score", 0.0),
            "data_quality": data_quality,
            "sentiment_stats": preproc.get("sentiment_stats", {}),
            "category_breakdown": preproc.get("category_breakdown", {}),
            "anomaly_flags": preproc.get("anomaly_flags", {}),
            "step_signals": step_signal_summary,
            "consistency_issues": consistency_issues,
            "evidence": self._build_evidence_packet(
                data, context.get("timeframe", "")
            ),
        }

    # ================================================================
    # 置信度校准（v2 新增）
    # ================================================================

    def _calibrate_confidence(self, raw_confidence: float, data: dict) -> float:
        """基于数据质量和历史准确率校准置信度"""
        calibrated = raw_confidence
        data_quality = data.get("_data_quality", {})
        preproc = data.get("preprocessing", {})

        # 1. 数据质量惩罚
        quality_score = data_quality.get("score", 1.0)
        if quality_score < 0.5:
            calibrated *= 0.6
        elif quality_score < 0.8:
            calibrated *= 0.85

        # 2. 情绪分化惩罚
        anomaly = preproc.get("anomaly_flags", {})
        if anomaly.get("sentiment_divergence"):
            calibrated *= 0.8

        # 3. 新闻面专用历史桶校准：让历史快照/在线验证样本直接影响新闻置信度。
        try:
            from src.utils.news_calibrator import NewsConfidenceCalibrator

            evidence_packet = self._build_evidence_packet(data)
            buckets = NewsConfidenceCalibrator.extract_buckets_from_evidence(evidence_packet)
            dedicated_calibrator = NewsConfidenceCalibrator()
            calibrated = dedicated_calibrator.calibrate(calibrated, **buckets)
        except Exception as e:
            logger.debug(f"新闻专用校准跳过: {e}")

        # 4. 历史准确率校准（如果 PredictionStore 可用）
        if self._prediction_store:
            try:
                from src.core.confidence_calibrator import ConfidenceCalibrator

                calibrator = ConfidenceCalibrator(self._prediction_store)
                calibrated = calibrator.calibrate(
                    self.name, calibrated, quality_score
                )
            except Exception as e:
                logger.debug(f"置信度校准跳过: {e}")

        return round(min(calibrated, 0.95), 2)
