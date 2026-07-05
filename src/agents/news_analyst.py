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
from datetime import datetime
from typing import Optional

from src.core.base_agent import BaseAgent
from src.core.llm_client import LLMClient
from src.data.news_fetcher import NewsFetcher
from src.data.symbol_resolver import resolve_symbol, identify_market
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

    def __init__(self, llm: LLMClient, prediction_store=None):
        super().__init__(
            name="最新新闻分析师",
            description="分析近期相关新闻的情绪方向、重大事件的影响程度",
            llm=llm,
        )
        self.news_fetcher = NewsFetcher(max_items=20)
        self._prediction_store = prediction_store  # 可选：用于置信度校准

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
            raise

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

        # 数据不可用或新闻极少 → 单 pass 模式（节省 token）
        if not is_available or news_count <= 2:
            logger.info(f"新闻数据不足（{news_count}条），使用单 pass 模式")
            return await self._analyze_single_pass(data, context)

        # 正常模式：两步 CoT
        try:
            return await self._analyze_two_step(data, context)
        except Exception as e:
            logger.warning(f"两步推理失败 ({e})，回退到单 pass 模式")
            return await self._analyze_single_pass(data, context)

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

        # === 校验 ===
        issues = self._validate_consistency(result, data)
        if issues:
            logger.warning(f"一致性校验发现问题: {issues}")
            # 将问题追加到 reasoning 中
            result.reasoning += f"\n\n---\n⚠️ **一致性校验提示**: {'; '.join(issues)}"

        return result

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

        # 解析信号提取结果
        content = response.content
        try:
            # 尝试提取 JSON
            import re
            json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL)
            if json_match:
                return json.loads(json_match.group(1).strip())
            brace_match = re.search(r"\{.*\}", content, re.DOTALL)
            if brace_match:
                return json.loads(brace_match.group(0))
        except (json.JSONDecodeError, AttributeError):
            logger.warning("信号提取 JSON 解析失败，使用原始文本")
            return {"signals": [], "raw_output": content, "noise_discarded": []}

        return {"signals": [], "raw_output": content, "noise_discarded": []}

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
        return NEWS_SYSTEM_PROMPT

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

        return f"""请基于以下新闻数据进行分析：

## 分析标的
{context.get('target', 'N/A')}

## 预测周期
{context.get('timeframe', 'N/A')}
{unavailable_note}
{preproc_section}

## 新闻数据
```json
{json.dumps(data.get('news_items', []), ensure_ascii=False, indent=2)[:6000]}
```

{market_appendix}

请严格按照要求的 JSON 格式输出分析结果。"""

    def _build_signal_extraction_prompt(
        self, data: dict, context: dict, market: str
    ) -> str:
        """构建信号提取 prompt"""
        preproc = data.get("preprocessing", {})
        preproc_section = self._format_preprocessing_summary(preproc, data)
        market_appendix = self._get_market_appendix(market)

        return f"""{SIGNAL_EXTRACTION_PROMPT}

## 分析标的
{context.get('target', 'N/A')}

## 预测周期
{context.get('timeframe', 'N/A')}

{preproc_section}

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

        signals_str = json.dumps(signals, ensure_ascii=False, indent=2)

        return f"""{SYNTHESIS_PROMPT}

## 分析标的
{context.get('target', 'N/A')}

## 预测周期
{context.get('timeframe', 'N/A')}

{preproc_section}

## Step 1 提取的信号
```json
{signals_str[:5000]}
```

{market_appendix}

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

        # 3. 历史准确率校准（如果 PredictionStore 可用）
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
