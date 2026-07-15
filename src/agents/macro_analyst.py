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
from src.core.llm_json import parse_llm_json
from src.core.llm_client import LLMClient
from src.data.macro_fetcher import MacroFetcherV2
from src.data.stock_context import get_stock_macro_context
from src.data.symbol_resolver import resolve_symbol, identify_market
from src.utils.macro_calibrator import MacroConfidenceCalibrator
from src.prompts.dynamic_overrides import build_prompt_with_overrides
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
        self.calibrator = MacroConfidenceCalibrator()

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

    def _use_compact_llm_path(self) -> bool:
        return bool(getattr(self.llm, "max_prompt_chars", 0) > 0)

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
        freshness = self._parse_percent(data_quality.get("overall_freshness", "50%"), 0.5)

        if self._use_compact_llm_path():
            logger.info("当前模型启用低延迟路径，宏观分析使用单 pass 模式")
            result = await self._analyze_single_pass(data, context, stock_ctx, market)
            return self._finalize_result(result, data, context, stock_ctx, market, {})

        if freshness < 0.3:
            # 数据太差，单 pass 让 LLM 用知识库补充
            logger.info(f"数据新鲜度低({freshness:.0%})，使用单 pass 模式")
            result = await self._analyze_single_pass(data, context, stock_ctx, market)
            return self._finalize_result(result, data, context, stock_ctx, market, {})

        # 两步 CoT
        try:
            logger.info("两步 CoT: 宏观评估 → 标的传导")
            # Step 1
            macro_assessment = await self._step_assess_macro(data, market)
            if not macro_assessment:
                macro_assessment = self._fallback_macro_assessment(data, stock_ctx, market)
            # Step 2
            result = await self._step_transmit_to_stock(
                macro_assessment, data, context, stock_ctx, market
            )
        except Exception as e:
            logger.warning(f"两步推理异常({e})，回退单 pass")
            result = await self._analyze_single_pass(data, context, stock_ctx, market)
            return self._finalize_result(result, data, context, stock_ctx, market, {})

        return self._finalize_result(
            result, data, context, stock_ctx, market, macro_assessment,
        )

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
            system_prompt=self._get_system_prompt(),
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
        evidence = self._build_evidence_packet(data, stock_ctx, market, context.get("timeframe", ""))
        evidence_str = json.dumps(evidence, ensure_ascii=False, indent=2)

        user_prompt = MACRO_TRANSMISSION_PROMPT.format(
            stock_context=ctx_str,
            macro_assessment=assessment_str,
            macro_data=macro_data_str,
            market_appendix=market_app,
        )
        user_prompt += f"""

## 宏观证据包（代码计算，不依赖 LLM）
```json
{evidence_str}
```

硬性约束:
- 不得编造未提供的宏观、汇率、利率、地缘或标的敏感度数据。
- 若宏观证据矩阵、行业敏感度与方向矛盾，必须降低 confidence，并在 risks 中说明。
- confidence 不得超过宏观证据包中的置信度上限。
- 若数据新鲜度低或参考值占比高，不得给出高置信强判断。
"""

        response = await self.llm.achat(
            system_prompt=self._get_system_prompt(),
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
        evidence = self._build_evidence_packet(data, stock_ctx, market, context.get("timeframe", ""))
        evidence_str = json.dumps(evidence, ensure_ascii=False, indent=2)

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

## 宏观证据包（代码计算，不依赖 LLM）
```json
{evidence_str}
```

{market_app}

硬性约束:
- 不得编造未提供的宏观、汇率、利率、地缘或标的敏感度数据。
- 若数据新鲜度低或参考值占比高，必须降低 confidence，并在 risks 中说明。
- confidence 不得超过宏观证据包中的置信度上限。

请严格按照要求的 JSON 格式输出分析结果。"""

        response = await self.llm.achat(
            system_prompt=self._get_system_prompt(),
            user_prompt=user_prompt,
        )
        result = self._parse_llm_response(response.content, context)

        # 数据不足时限制置信度
        freshness = self._parse_percent(
            data.get("data_quality", {}).get("overall_freshness", "50%"), 0.5,
        )
        if freshness < 0.3 and result.confidence > 0.4:
            result.confidence = 0.4
            result.reasoning += "\n\n[自动校准：数据新鲜度低，置信度上限设为 0.4]"

        return result

    # ================================================================
    # Prompt
    # ================================================================

    def _get_system_prompt(self) -> str:
        return build_prompt_with_overrides(MACRO_SYSTEM_PROMPT, self.name)

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
    # 结构化宏观证据
    # ================================================================

    def _finalize_result(
        self,
        result: "AnalysisResult",
        data: dict,
        context: dict,
        stock_ctx: dict,
        market: str,
        macro_assessment: dict,
    ) -> "AnalysisResult":
        """统一应用宏观校验、证据约束、校准和结构化摘要。"""
        consistency_issues = self._validate_macro_result(result, data, stock_ctx)
        result = self._apply_consistency_issues(result, consistency_issues)
        result = self._calibrate_confidence(result, data, stock_ctx, market)
        evidence_issues = self._apply_evidence_constraints(
            result, data, context, stock_ctx, market,
        )
        all_issues = consistency_issues + evidence_issues
        result.data_summary = self._build_data_summary(
            data, stock_ctx, market, macro_assessment, all_issues,
            context.get("timeframe", ""),
        )
        result.data_quality_score = self._parse_percent(
            data.get("data_quality", {}).get("overall_freshness", "50%"), 0.5,
        )
        if result.data_quality_score < 0.4 and result.status == "ok":
            result.status = "degraded"
        return result

    @staticmethod
    def _safe_float(value, default=None):
        try:
            if value in (None, "", "N/A"):
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _parse_percent(value, default: float = 0.5) -> float:
        if value in (None, "", "N/A"):
            return default
        try:
            if isinstance(value, str):
                value = value.strip()
                if value.endswith("%"):
                    return float(value[:-1]) / 100
            number = float(value)
            return number / 100 if number > 1 else number
        except (TypeError, ValueError):
            return default

    def _build_evidence_packet(
        self,
        data: dict,
        stock_ctx: dict,
        market: str,
        timeframe: str = "",
    ) -> dict:
        """提取代码计算的宏观证据，减少传导判断对自由文本的依赖。"""
        signals = self._derive_macro_signals(data, stock_ctx, market, timeframe)
        return {
            "identity": {
                "symbol": data.get("_resolved_symbol") or stock_ctx.get("symbol"),
                "name": data.get("_resolved_name") or stock_ctx.get("company_name"),
                "market": market,
                "sector": stock_ctx.get("inferred_sector"),
                "data_source": data.get("data_source"),
            },
            "macro_snapshot": {
                "china": data.get("china", {}),
                "us": data.get("us", {}),
                "forex": data.get("forex", {}),
            },
            "geopolitical": data.get("_geopolitical", {}),
            "stock_context": stock_ctx,
            "data_quality": data.get("data_quality", {}),
            "decision_matrix": signals["decision_matrix"],
            "evidence": signals["evidence"],
            "confidence_constraints": signals["confidence_model"],
        }

    def _derive_macro_signals(
        self,
        data: dict,
        stock_ctx: dict,
        market: str = "",
        timeframe: str = "",
    ) -> dict:
        """用宏观指标和标的敏感度生成传导矩阵、证据列表和置信度硬上限。"""
        china = data.get("china", {}) or {}
        us = data.get("us", {}) or {}
        forex = data.get("forex", {}) or {}
        geo = data.get("_geopolitical", {}) or {}
        dq = data.get("data_quality", {}) or {}
        sensitivity = stock_ctx.get("macro_sensitivity", {}) or {}

        sector = stock_ctx.get("inferred_sector", "综合")
        rate_sens = self._safe_float(sensitivity.get("rate_sensitive"), 0.5)
        rate_direction = sensitivity.get("rate_direction", "neutral")
        fx_sens = self._safe_float(sensitivity.get("fx_sensitive"), 0.4)
        cycle_sens = self._safe_float(sensitivity.get("cycle_sensitive"), 0.5)
        geo_sens = self._safe_float(sensitivity.get("geopolitical_sensitive"), 0.5)
        liquidity_sens = self._safe_float(sensitivity.get("liquidity_sensitive"), 0.5)

        pmi = self._safe_float(china.get("pmi_manufacturing"), None)
        lpr = self._safe_float(china.get("lpr_1y_pct"), None)
        m2 = self._safe_float(china.get("m2_yoy_pct"), None)
        gdp = self._safe_float(china.get("gdp_yoy_pct"), None)
        us10y = self._safe_float(us.get("10y_yield_pct"), None)
        fed = self._safe_float(us.get("fed_funds_rate_pct"), None)
        vix = self._safe_float(us.get("vix"), None)
        dxy = self._safe_float(forex.get("dxy"), None)
        usd_cny = self._safe_float(forex.get("usd_cny"), None)
        geo_score = self._safe_float(geo.get("risk_score"), None)

        score = 0.0
        bullish = []
        bearish = []
        neutral = []

        if pmi is None:
            neutral.append("缺少 PMI 数据")
        elif pmi >= 50.5:
            score += cycle_sens * 0.8
            bullish.append(f"PMI {pmi:.1f}，经济景气扩张")
        elif pmi < 49.5:
            score -= cycle_sens * 0.8
            bearish.append(f"PMI {pmi:.1f}，经济景气收缩")
        else:
            neutral.append(f"PMI {pmi:.1f}，景气接近荣枯线")

        if gdp is not None:
            if gdp >= 5.0:
                score += cycle_sens * 0.3
                bullish.append(f"GDP 同比 {gdp:.1f}%，增长韧性较好")
            elif gdp < 4.0:
                score -= cycle_sens * 0.3
                bearish.append(f"GDP 同比 {gdp:.1f}%，增长偏弱")

        if m2 is None:
            neutral.append("缺少 M2 数据")
        elif m2 >= 8.0:
            score += liquidity_sens * 0.45
            bullish.append(f"M2 同比 {m2:.1f}%，流动性偏宽")
        elif m2 < 6.0:
            score -= liquidity_sens * 0.35
            bearish.append(f"M2 同比 {m2:.1f}%，流动性偏弱")

        if lpr is not None:
            if lpr <= 3.2:
                delta = rate_sens * (0.35 if rate_direction != "positive" else -0.25)
                score += delta
                item = f"LPR {lpr:.2f}%，融资成本偏低"
                (bullish if delta >= 0 else bearish).append(item)
            elif lpr >= 3.8:
                delta = rate_sens * (-0.35 if rate_direction != "positive" else 0.25)
                score += delta
                item = f"LPR {lpr:.2f}%，融资成本偏高"
                (bullish if delta >= 0 else bearish).append(item)

        high_rate_signal = False
        if us10y is not None and us10y >= 4.5:
            high_rate_signal = True
        if fed is not None and fed >= 5.0:
            high_rate_signal = True
        if high_rate_signal:
            delta = rate_sens * (-0.85 if rate_direction == "negative" else 0.45 if rate_direction == "positive" else -0.25)
            score += delta
            item = f"美元利率偏高(US10Y={us10y}, Fed={fed})"
            (bullish if delta >= 0 else bearish).append(item)
        elif us10y is not None and us10y <= 3.8:
            delta = rate_sens * (0.55 if rate_direction == "negative" else -0.25 if rate_direction == "positive" else 0.20)
            score += delta
            item = f"美国10年期利率 {us10y:.2f}%，利率压力缓和"
            (bullish if delta >= 0 else bearish).append(item)

        if dxy is None:
            neutral.append("缺少 DXY 数据")
        elif dxy >= 105:
            score -= fx_sens * (0.65 if market in ("HK", "A") else 0.25)
            bearish.append(f"DXY {dxy:.1f}，美元偏强压制风险资产")
        elif dxy <= 101:
            score += fx_sens * (0.35 if market in ("HK", "A") else 0.15)
            bullish.append(f"DXY {dxy:.1f}，美元压力缓和")

        if usd_cny is not None and market in ("A", "HK"):
            if usd_cny >= 7.25:
                score -= fx_sens * 0.35
                bearish.append(f"USD/CNY {usd_cny:.2f}，人民币偏弱")
            elif usd_cny <= 7.05:
                score += fx_sens * 0.25
                bullish.append(f"USD/CNY {usd_cny:.2f}，人民币偏强")

        if vix is None:
            neutral.append("缺少 VIX 数据")
        elif vix >= 25:
            score -= 0.80
            bearish.append(f"VIX {vix:.1f}，全球风险偏好显著下降")
        elif vix >= 20:
            score -= 0.40
            bearish.append(f"VIX {vix:.1f}，风险偏好偏弱")
        elif vix < 16:
            score += 0.30
            bullish.append(f"VIX {vix:.1f}，风险偏好稳定")

        if geo_score is None:
            neutral.append("缺少近期地缘风险量化数据")
        elif geo_score >= 0.65:
            score -= geo_sens * 0.8
            bearish.append(f"地缘风险{geo.get('risk_level', '')}，风险分{geo_score:.2f}")
        elif geo_score <= 0.45:
            score += geo_sens * 0.25
            bullish.append(f"地缘风险较低，风险分{geo_score:.2f}")

        suggested_direction = "neutral"
        if score >= 0.65:
            suggested_direction = "bullish"
        elif score <= -0.65:
            suggested_direction = "bearish"

        if suggested_direction == "bullish":
            macro_regime = "supportive"
            macro_regime_label = "宏观顺风"
            reason = "宏观指标通过标的敏感因子传导后偏正面。"
        elif suggested_direction == "bearish":
            macro_regime = "hostile"
            macro_regime_label = "宏观逆风"
            reason = "宏观指标通过标的敏感因子传导后偏负面。"
        else:
            macro_regime = "mixed"
            macro_regime_label = "宏观混合"
            reason = "宏观指标多空交织或证据不足。"

        freshness = self._parse_percent(dq.get("overall_freshness", "50%"), 0.5)
        ref_count = int(self._safe_float(dq.get("reference_count"), 0) or 0)
        realtime_count = int(self._safe_float(dq.get("realtime_count"), 0) or 0)
        max_confidence = 0.75
        hard_caps = []

        if freshness < 0.30:
            max_confidence = min(max_confidence, 0.35)
            hard_caps.append("宏观数据新鲜度低于30%，confidence 不超过0.35")
        elif freshness < 0.50:
            max_confidence = min(max_confidence, 0.50)
            hard_caps.append("宏观数据新鲜度低于50%，confidence 不超过0.50")

        if ref_count >= 3:
            max_confidence = min(max_confidence, 0.55)
            hard_caps.append("参考值指标不少于3个，confidence 不超过0.55")

        missing_key = sum(1 for value in (pmi, us10y, dxy, vix) if value is None)
        if missing_key >= 2:
            max_confidence = min(max_confidence, 0.60)
            hard_caps.append("关键宏观指标缺失较多，confidence 不超过0.60")

        if ("短期" in timeframe or "周" in timeframe) and not geo.get("recent_events"):
            if suggested_direction != "neutral":
                max_confidence = min(max_confidence, 0.60)
                hard_caps.append("短期宏观判断缺少近期事件催化，confidence 不超过0.60")

        return {
            "decision_matrix": {
                "macro_regime": macro_regime,
                "macro_regime_label": macro_regime_label,
                "sector": sector,
                "sensitivity": sensitivity,
                "transmission_score": round(score, 2),
                "matrix_position": f"{sector}+{macro_regime_label}",
                "suggested_direction": suggested_direction,
                "reason": reason,
            },
            "evidence": {
                "bullish": bullish,
                "bearish": bearish,
                "neutral": neutral,
            },
            "confidence_model": {
                "ceiling": 0.75,
                "max_confidence": round(max_confidence, 2),
                "data_quality_level": self._get_data_quality_level(freshness, ref_count, realtime_count),
                "hard_caps": hard_caps,
            },
        }

    @staticmethod
    def _get_data_quality_level(
        freshness: float,
        ref_count: int,
        realtime_count: int,
    ) -> str:
        if freshness >= 0.70 and ref_count <= 1:
            return "fresh"
        if freshness >= 0.45 and ref_count <= 2:
            return "mixed"
        if ref_count >= 3:
            return "reference_heavy"
        if realtime_count <= 2:
            return "sparse"
        return "stale"

    def _fallback_macro_assessment(self, data: dict, stock_ctx: dict, market: str) -> dict:
        """Step 1 JSON 解析失败时，用代码证据生成保守宏观评估。"""
        signals = self._derive_macro_signals(data, stock_ctx, market)
        matrix = signals["decision_matrix"]
        return {
            "liquidity": matrix["macro_regime"],
            "economic_cycle": matrix["macro_regime_label"],
            "geopolitical": data.get("_geopolitical", {}).get("risk_level", "unknown"),
            "market": market,
            "preliminary_direction": matrix.get("suggested_direction", "neutral"),
            "preliminary_confidence": min(
                signals["confidence_model"].get("max_confidence", 0.45), 0.45,
            ),
            "key_factors": signals["evidence"].get("bullish", [])[:2],
            "key_risks": signals["evidence"].get("bearish", [])[:2],
            "fallback_reason": "宏观评估 JSON 解析失败，使用代码证据包生成保守预判",
        }

    def _calibrate_confidence(
        self,
        result: "AnalysisResult",
        data: dict,
        stock_ctx: dict,
        market: str,
    ) -> "AnalysisResult":
        """使用宏观历史校准器调整置信度；没有校准器时保持原值。"""
        calibrator = getattr(self, "calibrator", None)
        if calibrator is None:
            try:
                from src.utils.macro_calibrator import MacroConfidenceCalibrator
                calibrator = MacroConfidenceCalibrator()
                self.calibrator = calibrator
            except Exception:
                return result

        signals = self._derive_macro_signals(
            data, stock_ctx, market, result.timeframe,
        )
        quality_level = signals["confidence_model"].get("data_quality_level", "mixed")
        max_conf = self._safe_float(
            signals["confidence_model"].get("max_confidence"), 0.75,
        )
        try:
            calibrated = calibrator.calibrate(
                raw_confidence=result.confidence,
                market=market,
                sector=stock_ctx.get("inferred_sector"),
                data_quality_level=quality_level,
            )
        except Exception:
            return result

        calibrated = min(calibrated, max_conf)
        if round(calibrated, 2) != result.confidence:
            old_confidence = result.confidence
            result.confidence = round(calibrated, 2)
            result.reasoning += (
                f"\n\n[宏观校准: 原始置信度{old_confidence:.0%}→"
                f"校准后{result.confidence:.0%}]"
            )
        return result

    def _apply_consistency_issues(
        self,
        result: "AnalysisResult",
        issues: list[str],
    ) -> "AnalysisResult":
        """把宏观一致性校验结果写回 AnalysisResult。"""
        if not issues:
            return result

        if result.risks is None:
            result.risks = []
        existing = set(result.risks or [])
        for issue in issues:
            risk = f"宏观一致性校验: {issue}"
            if risk not in existing:
                result.risks.append(risk)
                existing.add(risk)

        result.reasoning += (
            "\n\n---\n"
            "⚠️ **宏观一致性校验提示**: " + "；".join(issues)
        )

        if any(self._is_severe_consistency_issue(issue) for issue in issues):
            old_confidence = result.confidence
            result.confidence = round(max(0.05, min(result.confidence, result.confidence * 0.85)), 2)
            if result.status == "ok":
                result.status = "degraded"
            logger.info(
                f"[{self.name}] 宏观一致性降权: {old_confidence:.0%} → {result.confidence:.0%}"
            )
        return result

    @staticmethod
    def _is_severe_consistency_issue(issue: str) -> bool:
        severe_markers = (
            "高置信度",
            "参考值",
            "VIX",
            "DXY",
            "地缘风险",
            "过度自信",
        )
        return any(marker in issue for marker in severe_markers)

    def _apply_evidence_constraints(
        self,
        result: "AnalysisResult",
        data: dict,
        context: dict,
        stock_ctx: dict,
        market: str,
    ) -> list[str]:
        """用宏观传导矩阵和硬上限约束 LLM 输出。"""
        signals = self._derive_macro_signals(
            data, stock_ctx, market, context.get("timeframe", ""),
        )
        matrix = signals["decision_matrix"]
        confidence_model = signals["confidence_model"]
        issues = []

        suggested = matrix.get("suggested_direction", "neutral")
        max_conf = self._safe_float(confidence_model.get("max_confidence"), 0.75)
        if suggested != "neutral" and result.direction.value != suggested and result.confidence > 0.50:
            issues.append(
                f"宏观传导矩阵建议{suggested}，但 LLM 输出{result.direction.value}"
            )
            max_conf = min(max_conf, 0.50)

        if result.confidence > max_conf:
            issues.append(f"confidence({result.confidence:.2f})超过宏观证据上限({max_conf:.2f})")
            result.confidence = round(max_conf, 2)

        if not issues:
            return []

        if result.risks is None:
            result.risks = []
        existing = set(result.risks)
        for issue in issues:
            risk = f"宏观证据约束: {issue}"
            if risk not in existing:
                result.risks.append(risk)
                existing.add(risk)

        result.reasoning += (
            "\n\n---\n"
            "⚠️ **宏观证据约束提示**: " + "；".join(issues)
        )
        if result.status == "ok":
            result.status = "degraded"
        return issues

    def _build_data_summary(
        self,
        data: dict,
        stock_ctx: dict,
        market: str,
        macro_assessment: dict,
        consistency_issues: list[str],
        timeframe: str = "",
    ) -> dict:
        """输出给 API/Aggregator 的结构化宏观摘要。"""
        evidence = self._build_evidence_packet(data, stock_ctx, market, timeframe)
        return {
            "symbol": data.get("_resolved_symbol") or stock_ctx.get("symbol"),
            "name": data.get("_resolved_name") or stock_ctx.get("company_name"),
            "market": market,
            "sector": stock_ctx.get("inferred_sector"),
            "source": data.get("data_source", "unknown"),
            "data_quality": data.get("data_quality", {}),
            "china": data.get("china", {}),
            "us": data.get("us", {}),
            "forex": data.get("forex", {}),
            "geopolitical": data.get("_geopolitical", {}),
            "stock_context": stock_ctx,
            "macro_assessment": macro_assessment,
            "consistency_issues": consistency_issues,
            "evidence": evidence,
        }

    # ================================================================
    # 校验
    # ================================================================

    def _validate_macro_result(
        self, result: "AnalysisResult", data: dict, stock_ctx: dict
    ) -> list[str]:
        """宏观分析输出校验"""
        issues = []

        # 1. 标的/行业是否被提及。支持公司简称，避免“腾讯控股”未出现但“腾讯”
        # 已出现时被误判为泛化。
        sector = stock_ctx.get("inferred_sector", "")
        company = stock_ctx.get("company_name", "")
        identity_terms = self._macro_identity_terms(stock_ctx, data)
        if identity_terms and not any(term in result.reasoning for term in identity_terms):
            issues.append(f"reasoning 未提及标的({company}/{sector})，宏观分析可能泛化")

        # 2. 传导链是否包含行业敏感度
        sensitivity = stock_ctx.get("macro_sensitivity", {})
        rate_sens = sensitivity.get("rate_sensitive", 0)
        if rate_sens >= 0.7 and "利率" not in result.reasoning:
            issues.append(f"标的是利率高度敏感行业({rate_sens})，但 reasoning 未提及利率影响")

        # 3. 置信度 vs 数据质量
        freshness = self._parse_percent(
            data.get("data_quality", {}).get("overall_freshness", "50%"), 0.5,
        )
        if result.confidence > 0.65 and freshness < 0.4:
            issues.append(f"高置信度({result.confidence:.0%})但数据新鲜度低({freshness:.0%})")

        # 4. 参考值占比过高
        ref_count = len(data.get("reference_fields", []))
        if ref_count >= 3 and result.confidence > 0.55:
            issues.append(f"{ref_count}个指标为参考值但置信度 > 0.55，可能过度自信")

        # 5. 风险偏好与方向一致性
        vix = self._safe_float((data.get("us") or {}).get("vix"), None)
        if vix is not None and vix >= 25 and result.direction.value == "bullish":
            issues.append(f"VIX({vix:.1f})显示风险偏好显著下降但方向为看涨")

        dxy = self._safe_float((data.get("forex") or {}).get("dxy"), None)
        market = data.get("_market", "")
        if market in ("A", "HK") and dxy is not None and dxy >= 105 and result.direction.value == "bullish":
            issues.append(f"DXY({dxy:.1f})偏强压制中国/港股资产但方向为看涨")

        geo = data.get("_geopolitical", {}) or {}
        geo_score = self._safe_float(geo.get("risk_score"), None)
        geo_sens = self._safe_float(
            (stock_ctx.get("macro_sensitivity", {}) or {}).get("geopolitical_sensitive"),
            0.5,
        )
        if geo_score is not None and geo_score >= 0.65 and geo_sens >= 0.6:
            if result.direction.value == "bullish" and result.confidence > 0.5:
                issues.append(
                    f"标的地缘敏感度较高({geo_sens})且地缘风险偏高({geo_score})，但高置信看涨"
                )

        return issues

    @staticmethod
    def _macro_identity_terms(stock_ctx: dict, data: dict) -> set[str]:
        terms = set()
        for value in (
            stock_ctx.get("company_name"),
            stock_ctx.get("inferred_sector"),
            data.get("_resolved_name"),
            data.get("_resolved_symbol"),
        ):
            if value:
                terms.add(str(value))

        company = str(stock_ctx.get("company_name") or "")
        for suffix in (
            "控股有限公司",
            "股份有限公司",
            "有限公司",
            "集团",
            "控股",
            "公司",
            "Inc.",
            "Inc",
            "Ltd.",
            "Ltd",
        ):
            if company.endswith(suffix) and len(company) > len(suffix) + 1:
                terms.add(company[: -len(suffix)])
        return {term for term in terms if len(term) >= 2}

    # ================================================================
    # 工具
    # ================================================================

    @staticmethod
    def _extract_json(content: str) -> dict:
        """从 LLM 响应中提取 JSON"""
        parsed = parse_llm_json(content)
        if parsed.ok and isinstance(parsed.data, dict):
            if parsed.repaired:
                parsed.data["_llm_json_repaired"] = True
                parsed.data["_llm_json_repairs"] = parsed.repairs
            return parsed.data
        logger.warning(f"宏观 JSON 解析失败: {parsed.error}")
        return {}
