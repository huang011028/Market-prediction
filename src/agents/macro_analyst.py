"""
国际形势 / 宏观经济分析师 v2

Phase A 升级：
- 标的上下文注入（行业+宏观敏感因子）
- 两步链式推理（宏观评估 → 标的传导）
- 输出一致性校验
- 数据新鲜度感知

Round 2 升级：
- 地缘政治事件实时采集
- 置信度校准器集成（历史数据驱动）
"""

import json
import logging
from src.core.base_agent import BaseAgent
from src.core.llm_client import LLMClient
from src.data.macro_fetcher import MacroFetcherV2
from src.data.stock_context import get_stock_macro_context
from src.data.symbol_resolver import resolve_symbol, identify_market
from src.prompts.macro_prompts import (
    MACRO_SYSTEM_PROMPT,
    MACRO_ASSESSMENT_PROMPT,
    MACRO_TRANSMISSION_PROMPT,
    A_SHARE_MACRO_APPENDIX,
    HK_SHARE_MACRO_APPENDIX,
    US_SHARE_MACRO_APPENDIX,
)

logger = logging.getLogger(__name__)


class MacroAnalyst(BaseAgent):
    """国际形势 / 宏观经济分析师 v2

    升级内容：
    - 标的上下文（行业敏感度 → 精确传导链）
    - 两步 CoT（宏观评估 → 标的传导）
    - 数据新鲜度感知
    - 一致性校验
    """

    def __init__(self, llm: LLMClient):
        super().__init__(
            name="国际形势分析师",
            description="分析货币政策、经济指标、地缘政治对标的的宏观环境影响",
            llm=llm,
        )
        self.fetcher = MacroFetcherV2()

    # ================================================================
    # 数据采集
    # ================================================================

    async def gather_data(self, target: str, timeframe: str) -> dict:
        """获取宏观数据 + 标的上下文 + 地缘事件"""
        info = resolve_symbol(target)
        market = info.market
        data = await self.fetcher.fetch(info.symbol, market)
        result = data.to_agent_dict()

        # 注入标的上下文
        company_name = info.name or self._try_get_company_name(info.symbol, market)
        stock_ctx = get_stock_macro_context(info.symbol, market, company_name)
        result["_stock_context"] = stock_ctx
        result["_market"] = market
        result["_resolved_symbol"] = info.symbol
        result["_resolved_name"] = info.name

        # Round 2: 地缘政治事件采集（可选，失败不影响主流程）
        try:
            from src.data.geopolitical_fetcher import fetch_geopolitical_signals
            geo_signals = await fetch_geopolitical_signals(days=30)
            if geo_signals.get("recent_events"):
                result["_geopolitical"] = geo_signals
                logger.info(f"地缘事件: {geo_signals.get('total_events', 0)}条, 风险={geo_signals.get('risk_level', '?')}")
        except Exception as e:
            logger.debug(f"地缘事件采集跳过: {e}")

        return result

    def _identify_market(self, symbol: str) -> str:
        return identify_market(symbol)

    @staticmethod
    def _try_get_company_name(symbol: str, market: str) -> str:
        """尝试解析公司名（复用 news_preprocessor 的解析器）"""
        try:
            from src.data.news_preprocessor import resolve_company_name
            name = resolve_company_name(symbol, market)
            if name:
                return name
        except Exception:
            pass
        return ""

    # ================================================================
    # 两步推理
    # ================================================================

    async def analyze(self, data: dict, context: dict) -> "AnalysisResult":
        """两步链式推理：宏观评估 → 标的传导"""
        from src.core.result import AnalysisResult

        stock_ctx = data.get("_stock_context", {})
        market = data.get("_market", "A")

        # 判断是否需要两步推理（数据充足时用两步，不足时单 pass）
        data_quality = data.get("data_quality", {})
        freshness = float(str(data_quality.get("overall_freshness", "50%")).rstrip("%")) / 100

        if freshness < 0.3:
            # 数据太差，单 pass 让 LLM 用知识库补充
            logger.info(f"数据新鲜度低({freshness:.0%})，使用单 pass 模式")
            return await self._analyze_single_pass(data, context, stock_ctx, market)

        # 两步 CoT
        try:
            logger.info("两步 CoT: 宏观评估 → 标的传导")
            # Step 1
            macro_assessment = await self._step_assess_macro(data, market)
            # Step 2
            result = await self._step_transmit_to_stock(
                macro_assessment, data, context, stock_ctx, market
            )
        except Exception as e:
            logger.warning(f"两步推理异常({e})，回退单 pass")
            return await self._analyze_single_pass(data, context, stock_ctx, market)

        # 校验
        issues = self._validate_macro_result(result, data, stock_ctx)
        if issues:
            logger.warning(f"宏观校验: {issues}")
            result.reasoning += f"\n\n---\n⚠️ **校验提示**: {'; '.join(issues)}"

        return result

    async def _step_assess_macro(self, data: dict, market: str) -> dict:
        """Step 1: 宏观环境评估（含地缘事件数据）"""
        market_app = self._get_market_appendix(market)
        macro_data_str = json.dumps(data, ensure_ascii=False, indent=2)[:5000]

        # Round 2: 注入地缘事件数据
        geo_section = ""
        geo_data = data.get("_geopolitical")
        if geo_data:
            geo_section = f"""
## 近期地缘政治事件（实时采集）
风险等级: {geo_data.get('risk_level', '?')}
主要主题: {', '.join(geo_data.get('key_themes', []))}
事件数: {geo_data.get('total_events', 0)}
趋势: {geo_data.get('trend', '?')}
摘要: {geo_data.get('summary', '')}

最近事件:
```json
{json.dumps(geo_data.get('recent_events', [])[:5], ensure_ascii=False, indent=2)}
```
"""

        user_prompt = f"""{MACRO_ASSESSMENT_PROMPT}

## 宏观数据
```json
{macro_data_str}
```
{geo_section}
{market_app}

请输出宏观环境评估 JSON。"""

        response = await self.llm.achat(
            system_prompt=MACRO_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )
        return self._extract_json(response.content)

    async def _step_transmit_to_stock(
        self, macro_assessment: dict, data: dict, context: dict,
        stock_ctx: dict, market: str
    ) -> "AnalysisResult":
        """Step 2: 宏观→标的传导"""
        from src.core.result import AnalysisResult

        market_app = self._get_market_appendix(market)
        macro_data_str = json.dumps(data, ensure_ascii=False, indent=2)[:4000]
        assessment_str = json.dumps(macro_assessment, ensure_ascii=False, indent=2)
        ctx_str = json.dumps(stock_ctx, ensure_ascii=False, indent=2)

        user_prompt = MACRO_TRANSMISSION_PROMPT.format(
            stock_context=ctx_str,
            macro_assessment=assessment_str,
            macro_data=macro_data_str,
            market_appendix=market_app,
        )

        response = await self.llm.achat(
            system_prompt=MACRO_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )
        return self._parse_llm_response(response.content, context)

    async def _analyze_single_pass(
        self, data: dict, context: dict, stock_ctx: dict, market: str
    ) -> "AnalysisResult":
        """单 pass 回退模式"""
        from src.core.result import AnalysisResult

        market_app = self._get_market_appendix(market)
        ctx_str = json.dumps(stock_ctx, ensure_ascii=False, indent=2)
        data_str = json.dumps(data, ensure_ascii=False, indent=2)
        if len(data_str) > 6000:
            data_str = data_str[:6000] + "\n... (截断)"

        note = (
            "\n\n📌 **重要提示**：以上宏观数据可能不完整（部分标注为'参考值'）。"
            "参考值不是当前实时数据！对于标注为'参考值'的指标，"
            "请结合你的知识库补充判断，并对这些指标的判断降低权重。"
            "请在 reasoning 中明确区分：哪些判断基于实时数据，哪些基于参考值/知识库。\n"
        )

        user_prompt = f"""请基于以下宏观数据和对当前宏观经济环境的了解，分析对标的的影响：

## 标的宏观上下文
```json
{ctx_str}
```

## 分析标的
{context.get('target', 'N/A')}

## 预测周期
{context.get('timeframe', 'N/A')}
{note}
## 宏观数据
```json
{data_str}
```

{market_app}

请严格按照要求的 JSON 格式输出分析结果。"""

        response = await self.llm.achat(
            system_prompt=MACRO_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )
        result = self._parse_llm_response(response.content, context)

        # 数据不足时限制置信度
        freshness = float(
            str(data.get("data_quality", {}).get("overall_freshness", "50%")).rstrip("%")
        ) / 100
        if freshness < 0.3 and result.confidence > 0.4:
            result.confidence = 0.4
            result.reasoning += "\n\n[自动校准：数据新鲜度低，置信度上限设为 0.4]"

        return result

    # ================================================================
    # Prompt
    # ================================================================

    def _get_system_prompt(self) -> str:
        return MACRO_SYSTEM_PROMPT

    @staticmethod
    def _get_market_appendix(market: str) -> str:
        if market == "A":
            return A_SHARE_MACRO_APPENDIX
        elif market == "HK":
            return HK_SHARE_MACRO_APPENDIX
        elif market == "US":
            return US_SHARE_MACRO_APPENDIX
        return ""

    # ================================================================
    # 校验
    # ================================================================

    def _validate_macro_result(
        self, result: "AnalysisResult", data: dict, stock_ctx: dict
    ) -> list[str]:
        """宏观分析输出校验"""
        issues = []

        # 1. 行业是否被提及
        sector = stock_ctx.get("inferred_sector", "")
        company = stock_ctx.get("company_name", "")
        if company and company not in result.reasoning and sector not in result.reasoning:
            issues.append(f"reasoning 未提及标的({company}/{sector})，宏观分析可能泛化")

        # 2. 传导链是否包含行业敏感度
        sensitivity = stock_ctx.get("macro_sensitivity", {})
        rate_sens = sensitivity.get("rate_sensitive", 0)
        if rate_sens >= 0.7 and "利率" not in result.reasoning:
            issues.append(f"标的是利率高度敏感行业({rate_sens})，但 reasoning 未提及利率影响")

        # 3. 置信度 vs 数据质量
        freshness = float(
            str(data.get("data_quality", {}).get("overall_freshness", "50%")).rstrip("%")
        ) / 100
        if result.confidence > 0.65 and freshness < 0.4:
            issues.append(f"高置信度({result.confidence:.0%})但数据新鲜度低({freshness:.0%})")

        # 4. 参考值占比过高
        ref_count = len(data.get("reference_fields", []))
        if ref_count >= 3 and result.confidence > 0.55:
            issues.append(f"{ref_count}个指标为参考值但置信度 > 0.55，可能过度自信")

        return issues

    # ================================================================
    # 工具
    # ================================================================

    @staticmethod
    def _extract_json(content: str) -> dict:
        """从 LLM 响应中提取 JSON"""
        import re
        json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(1).strip())
        brace_match = re.search(r"\{.*\}", content, re.DOTALL)
        if brace_match:
            return json.loads(brace_match.group(0))
        return {}
