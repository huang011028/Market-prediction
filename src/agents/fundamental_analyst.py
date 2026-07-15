"""
公司前景 / 基本面分析师 v3

获取财务数据 + 估值指标 → 预处理管线（评分卡/趋势/分位）
→ 多步 LLM 推理 → 置信度校准（含历史数据回归）→ 输出 AnalysisResult

v3 改进 (基于 v2):
- 港股财务数据补全（AASTOCKS/东方财富F10 降级链）
- 置信度校准器（FundamentalConfidenceCalibrator）
- 行业差异化评分（INDUSTRY_BENCHMARKS）
- 评分卡权重优化接口
"""

import logging
import json
import re
from typing import Optional

from src.core.base_agent import BaseAgent
from src.core.llm_json import parse_llm_json
from src.core.llm_client import LLMClient
from src.core.result import AnalysisResult, Direction, Magnitude
from src.data.fundamental_fetcher import FundamentalFetcher
from src.data.symbol_resolver import resolve_symbol, identify_market
from src.prompts.dynamic_overrides import build_prompt_with_overrides
from src.prompts.fundamental_prompts import (
    FUNDAMENTAL_SYSTEM_PROMPT,
    A_SHARE_FUNDAMENTAL_APPENDIX,
    HK_SHARE_FUNDAMENTAL_APPENDIX,
    US_SHARE_FUNDAMENTAL_APPENDIX,
)
from src.utils.fundamental_calibrator import FundamentalConfidenceCalibrator

logger = logging.getLogger(__name__)


class FundamentalAnalyst(BaseAgent):
    """公司前景 / 基本面分析师 v2

    基于财报数据、估值水平、行业地位、
    机构评级判断公司内在价值与成长前景。

    改进:
    - 使用预处理管线（评分卡、趋势、分位）
    - 两步 CoT 推理
    - 置信度校准
    """

    def __init__(self, llm: LLMClient):
        super().__init__(
            name="公司前景分析师",
            description="基于财报数据、估值水平、行业地位判断公司内在价值与成长前景",
            llm=llm,
        )
        self.fetcher = FundamentalFetcher()
        self.calibrator = FundamentalConfidenceCalibrator()

    async def gather_data(self, target: str, timeframe: str) -> dict:
        """获取增强版财务数据（含预处理管线结果）"""
        info = resolve_symbol(target)
        market = info.market
        data = await self.fetcher.fetch_enhanced(info.symbol, market)
        data["_resolved_symbol"] = info.symbol
        data["_resolved_name"] = info.name
        return data

    def _identify_market(self, symbol: str) -> str:
        """识别市场，支持代码和中文名"""
        return identify_market(symbol)

    def _get_system_prompt(self) -> str:
        return build_prompt_with_overrides(FUNDAMENTAL_SYSTEM_PROMPT, self.name)

    def _use_compact_llm_path(self) -> bool:
        return bool(getattr(self.llm, "max_prompt_chars", 0) > 0)

    async def analyze(self, data: dict, context: dict) -> AnalysisResult:
        """覆盖基类的 analyze()，实现两步 CoT 推理"""

        if self._is_fundamental_data_empty(data):
            reason = (
                "基本面财务和估值字段完全缺失，不能评价公司质量、利润趋势或估值方向。"
                "本轮基本面结论仅作为数据缺失提示，不参与强方向判断。"
            )
            return AnalysisResult(
                agent_name=self.name,
                target=context.get("target", ""),
                timeframe=context.get("timeframe", ""),
                direction=Direction.NEUTRAL,
                magnitude=Magnitude(min_pct=-2.0, max_pct=2.0),
                confidence=0.10,
                reasoning=reason,
                key_factors=["基本面数据完全缺失"],
                risks=["缺少财务和估值数据，无法判断公司质量或利润趋势"],
                data_summary=self._build_data_summary(data, {}, ["基本面数据完全缺失"]),
                status="failed",
                error_message=reason,
                data_quality_score=self._safe_float(
                    data.get("data_quality", {}).get("overall_quality"),
                    0.0,
                ),
            )

        market = data.get("market", "A")

        # 注入市场区分附录
        market_appendix = self._get_market_appendix(market)
        system_prompt = build_prompt_with_overrides(
            FUNDAMENTAL_SYSTEM_PROMPT + market_appendix,
            self.name,
        )

        # === Step A: 综合评估（质量+估值+催化+风险） ===
        if self._use_compact_llm_path():
            step_a_result = self._fallback_step_a_result(data)
        else:
            user_prompt_step_a = self._build_step_a_prompt(data, context)
            response_a = await self.llm.achat(
                system_prompt=system_prompt,
                user_prompt=user_prompt_step_a,
            )
            step_a_result = self._parse_json_from_response(response_a.content)
            if not step_a_result:
                step_a_result = self._fallback_step_a_result(data)

        # === Step B: 综合判断 + 反思 ===
        user_prompt_step_b = self._build_step_b_prompt(
            data, context, step_a_result
        )
        response_b = await self.llm.achat(
            system_prompt=system_prompt,
            user_prompt=user_prompt_step_b,
        )

        # 解析最终结果
        result = self._parse_llm_response(response_b.content, context)

        # === 置信度校准 ===
        result = self._calibrate_confidence(result, data, step_a_result)

        # === 一致性校验 ===
        consistency_issues = self._validate_consistency(result, data)
        result = self._apply_consistency_issues(result, consistency_issues)
        evidence_issues = self._apply_evidence_constraints(result, data, context)
        sanitize_issues = self._sanitize_unsupported_fundamental_claims(result, data)
        all_issues = consistency_issues + evidence_issues
        all_issues.extend(sanitize_issues)

        # === 结构化摘要 ===
        result.data_summary = self._build_data_summary(
            data, step_a_result, all_issues,
        )
        result.data_quality_score = self._safe_float(
            data.get("data_quality", {}).get("overall_quality"), 1.0,
        )
        if result.data_quality_score < 0.4 and result.status == "ok":
            result.status = "degraded"

        return result

    def _get_market_appendix(self, market: str) -> str:
        """获取市场区分 prompt 附录"""
        if market == "A":
            return "\n\n" + A_SHARE_FUNDAMENTAL_APPENDIX
        elif market == "HK":
            return "\n\n" + HK_SHARE_FUNDAMENTAL_APPENDIX
        else:
            return "\n\n" + US_SHARE_FUNDAMENTAL_APPENDIX

    def _build_step_a_prompt(self, data: dict, context: dict) -> str:
        """构建 Step A 的 prompt：综合评估"""
        data_str = json.dumps(data, ensure_ascii=False, indent=2)
        if len(data_str) > 8000:
            data_str = data_str[:8000] + "\n... (数据过长，已截断)"

        timeframe = context.get("timeframe", "短期(1周)")
        target = context.get("target", "N/A")

        # 数据质量提示
        quality_note = ""
        dq = data.get("data_quality", {})
        if dq.get("overall_quality", 1.0) < 0.4:
            quality_note = (
                "\n\n⚠️ 注意：数据完整度较低（{}/{}财务字段，{}/{}估值字段）。"
                "请基于有限数据给出判断，在 reasoning 中标注数据局限性，并降低 confidence。"
            ).format(
                dq.get("financial_fields_filled", "?"),
                dq.get("financial_fields_total", "?"),
                dq.get("valuation_fields_filled", "?"),
                dq.get("valuation_fields_total", "?"),
            )
        elif dq.get("overall_quality", 1.0) < 0.5:
            quality_note = (
                "\n\n⚠️ 注意：数据覆盖不足。质量评分卡只能作为字段覆盖提示，"
                "不得把低分解释为公司质量极低或利润趋势恶化。"
            )
        scorecard = data.get("quality_scorecard", {})
        scorecard_validity = scorecard.get(
            "coverage_warning",
            "评分卡可用" if not scorecard.get("not_scorable") else "评分卡不可用",
        )

        return f"""请基于以下基本面数据进行综合评估：

## 分析标的
{target}

## 预测周期
{timeframe}

## 数据已预处理，请直接使用以下结果：
### 质量评分卡
- 总分: {scorecard.get('total', 'N/A')} ({scorecard.get('rating', 'N/A')})
- 评分有效性: {scorecard_validity}
- 盈利能力: {scorecard.get('breakdown', {}).get('profitability', {})}
- 成长性: {scorecard.get('breakdown', {}).get('growth', {})}
- 估值: {scorecard.get('breakdown', {}).get('valuation', {})}
- 健康度: {scorecard.get('breakdown', {}).get('health', {})}

### 估值分析
{json.dumps(data.get('valuation_analysis', {}), ensure_ascii=False, indent=2)}

### 财务趋势
{json.dumps(data.get('financials', {}).get('_trend', {}), ensure_ascii=False, indent=2)}

### 价值陷阱分析
{json.dumps(data.get('value_trap_analysis', {}), ensure_ascii=False, indent=2)}

### 分析数据质量
完整度: {dq.get('overall_quality', 'N/A')} | 置信度上限: {dq.get('confidence_ceiling', 'N/A')}
{quality_note}

请回答：
1. 这家公司的质量如何？(评分卡已给出，请验证)
2. 当前估值是否合理？(分位已给出，请解读)
3. 有无价值陷阱风险？
4. 核心催化剂和风险是什么？

输出 JSON:
{{
  "quality_assessment": "公司质量简要判断",
  "valuation_judgment": "估值合理性判断",
  "value_trap_risk": true/false,
  "key_catalysts": ["催化因素1", "催化因素2"],
  "key_risks": ["风险1", "风险2"],
  "preliminary_direction": "bullish|bearish|neutral",
  "preliminary_confidence": 0.65
}}"""

    def _build_step_b_prompt(self, data: dict, context: dict,
                              step_a_result: dict) -> str:
        """构建 Step B 的 prompt：综合判断 + 反思"""
        target = context.get("target", "N/A")
        timeframe = context.get("timeframe", "短期(1周)")
        dq = data.get("data_quality", {})
        ceiling = dq.get("confidence_ceiling", 0.70)
        evidence = self._build_evidence_packet(data)
        evidence_str = json.dumps(evidence, ensure_ascii=False, indent=2)

        return f"""## 综合判断

综合分析 {target}（{timeframe}）的基本面：

### Step A 评估结果
- 公司质量: {step_a_result.get('quality_assessment', 'N/A')}
- 估值判断: {step_a_result.get('valuation_judgment', 'N/A')}
- 价值陷阱风险: {'是' if step_a_result.get('value_trap_risk') else '否'}
- 方向预判: {step_a_result.get('preliminary_direction', 'N/A')} (预设置信度: {step_a_result.get('preliminary_confidence', 'N/A')})

### 数据质量约束
- 数据完整度: {dq.get('overall_quality', 'N/A')}
- 置信度上限: {ceiling}

### 基本面证据包（代码计算，不依赖 LLM）
```json
{evidence_str}
```

请给出最终判断。

### 反思环节
在给出判断前，请检查：
1. 有没有周期性因素被忽略？（当前是行业景气高点还是低点？）
2. 是否过度依赖历史数据而忽略了结构性变化？
3. 数据缺失最大的盲区是什么？

硬性约束:
- 不得编造未提供的财务、估值、评级或行业数据。
- 若估值分位、评分卡、价值陷阱信号与方向矛盾，必须降低 confidence，并在 risks 中说明。
- confidence 不得超过数据质量约束中的置信度上限。
- 短期预测中，若缺少财报/业绩预告等催化剂，基本面方向不得给高置信强判断。
- 若 profit_yoy_pct、利润趋势或净利润字段缺失，不得使用“利润暴跌/利润下滑/盈利恶化”等趋势断言。
- 若质量评分卡 rating 为 unknown 或数据完整度低于50%，不得把低分解释为“公司质量极低”，只能说明“质量不可评分/证据不足”。

输出 JSON:
{{
  "direction": "bullish|bearish|neutral",
  "magnitude": {{"min_pct": -10.0, "max_pct": 10.0}},
  "confidence": 0.65,
  "reasoning": "完整分析推理过程（200-500字）",
  "key_factors": ["核心利多因素", "核心利空因素"],
  "risks": ["风险1", "风险2"],
  "quality_assessment": "公司质量最终判断",
  "valuation_judgment": "估值合理性最终判断"
}}"""

    def _parse_json_from_response(self, content: str) -> dict:
        """从 LLM 响应中提取 JSON"""
        parsed = parse_llm_json(content)
        if parsed.ok and isinstance(parsed.data, dict):
            if parsed.repaired:
                parsed.data["_llm_json_repaired"] = True
                parsed.data["_llm_json_repairs"] = parsed.repairs
            return parsed.data
        logger.warning(f"Step A JSON 解析失败: {parsed.error}")
        return {}

    def _calibrate_confidence(self, result: AnalysisResult, data: dict,
                               step_a_result: dict = None) -> AnalysisResult:
        """校准置信度（含历史数据回归）"""
        dq = data.get("data_quality", {})
        ceiling = dq.get("confidence_ceiling", 0.70)
        scorecard = data.get("quality_scorecard", {})

        # 1. 数据质量 ceiling 限制
        calibrated = min(result.confidence, ceiling)

        # 2. Step A 与最终方向一致性检查
        if step_a_result:
            step_a_dir = step_a_result.get("preliminary_direction", "")
            if step_a_dir and step_a_dir != result.direction.value:
                calibrated *= 0.85
                logger.debug(
                    f"方向从 Step A 的 {step_a_dir} 翻转为 {result.direction.value}，降低置信度"
                )

        # 3. 价值陷阱场景的特殊处理
        trap_analysis = data.get("value_trap_analysis", {})
        if trap_analysis.get("is_trap") and result.direction == Direction.BULLISH:
            calibrated *= 0.7
            logger.debug("价值陷阱风险 + 看涨方向 → 大幅降低置信度")

        # 4. 基于历史数据的校准（如果有足够样本）
        data_quality_bucket = self._get_data_quality_bucket(dq.get("overall_quality", 0.5))
        scorecard_rating = scorecard.get("rating")

        calibrated = self.calibrator.calibrate(
            raw_confidence=calibrated,
            data_quality_bucket=data_quality_bucket,
            scorecard_rating=scorecard_rating,
        )

        # 校准器可能基于历史样本上调置信度；最终仍必须服从本次数据质量上限。
        calibrated = min(calibrated, ceiling)

        result.confidence = round(min(max(calibrated, 0.05), 0.95), 2)
        return result

    @staticmethod
    def _get_data_quality_bucket(quality: float) -> str:
        """将数据完整度映射到分桶"""
        if quality >= 0.7:
            return "high"
        elif quality >= 0.4:
            return "medium"
        else:
            return "low"

    def _validate_consistency(self, result: AnalysisResult, data: dict) -> list[str]:
        """校验结果与数据的一致性（仅警告，不修改）"""
        issues = []

        # 1. 估值分位-方向一致性
        va = data.get("valuation_analysis", {})
        pe_pct = self._safe_float(va.get("pe_percentile_3yr"), None)

        if pe_pct is not None:
            pct = self._format_percentile(pe_pct)
            if result.direction == Direction.BULLISH and pe_pct > 0.85:
                issues.append(f"PE处于3年{pct:.0f}%分位(很贵)但方向为看涨")
            if result.direction == Direction.BEARISH and pe_pct < 0.15:
                issues.append(f"PE处于3年{pct:.0f}%分位(很便宜)但方向为看跌")

        # 2. 数据质量-ceiling一致性
        dq = data.get("data_quality", {})
        ceiling = dq.get("confidence_ceiling", 0.70)
        if result.confidence > ceiling + 0.05:
            issues.append(
                f"confidence({result.confidence})超过数据质量上限({ceiling})"
            )

        # 3. 风险列表检查
        if result.direction == Direction.BULLISH and not result.risks:
            issues.append("看涨但未列出任何风险")

        # 4. 价值陷阱-方向一致性
        trap = data.get("value_trap_analysis", {})
        if trap.get("is_trap") and result.direction == Direction.BULLISH:
            issues.append("价值陷阱风险已触发但方向为看涨")

        # 5. 低数据质量不应强判断
        quality = self._safe_float(dq.get("overall_quality"), 1.0)
        if quality < 0.4 and result.direction != Direction.NEUTRAL and result.confidence > 0.4:
            issues.append(
                f"数据完整度较低({quality:.0%})但给出非中性方向和较高置信度"
            )

        if issues:
            for issue in issues:
                logger.warning(f"[{self.name}] 一致性警告: {issue}")
        return issues

    def _apply_consistency_issues(
        self, result: AnalysisResult, issues: list[str]
    ) -> AnalysisResult:
        """把一致性校验结果写回 AnalysisResult，供 Aggregator 和前端消费。"""
        if not issues:
            return result

        if result.risks is None:
            result.risks = []

        existing_risks = set(result.risks or [])
        for issue in issues:
            risk = f"基本面一致性校验: {issue}"
            if risk not in existing_risks:
                result.risks.append(risk)
                existing_risks.add(risk)

        result.reasoning += (
            "\n\n---\n"
            "⚠️ **基本面一致性校验提示**: " + "；".join(issues)
        )

        severe_markers = (
            "但方向",
            "超过数据质量上限",
            "价值陷阱",
            "未列出任何风险",
            "数据完整度较低",
        )
        if any(marker in issue for issue in issues for marker in severe_markers):
            old_confidence = result.confidence
            result.confidence = round(max(0.05, min(result.confidence, result.confidence * 0.85)), 2)
            if result.status == "ok":
                result.status = "degraded"
            logger.info(
                f"[{self.name}] 一致性降权: {old_confidence:.0%} → {result.confidence:.0%}"
            )

        return result

    @staticmethod
    def _format_percentile(value) -> float:
        """把 0~1 或 0~100 的分位值统一转为百分数。"""
        try:
            pct = float(value)
        except (TypeError, ValueError):
            return 0.0
        return pct * 100 if pct <= 1 else pct

    @staticmethod
    def _safe_float(value, default: float = 0.0) -> float:
        try:
            if value in (None, "", "N/A"):
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    def _build_evidence_packet(self, data: dict) -> dict:
        """提取代码计算的基本面证据，减少 Step B 对自由文本的依赖。"""
        financials = data.get("financials", {})
        valuation = data.get("valuation", {})
        scorecard = data.get("quality_scorecard", {})
        dq = data.get("data_quality", {})
        signals = self._derive_fundamental_signals(data)
        return {
            "identity": {
                "symbol": data.get("symbol") or data.get("_resolved_symbol"),
                "name": data.get("company_name") or data.get("_resolved_name"),
                "market": data.get("market"),
                "industry": data.get("industry"),
                "data_source": data.get("data_source"),
            },
            "quality_scorecard": scorecard,
            "valuation_analysis": data.get("valuation_analysis", {}),
            "financial_trend": financials.get("_trend", {}),
            "value_trap_analysis": data.get("value_trap_analysis", {}),
            "data_quality": dq,
            "anomaly_flags": data.get("anomaly_flags", {}),
            "decision_matrix": signals["decision_matrix"],
            "evidence": signals["evidence"],
            "raw_snapshot": {
                "financials": {
                    "revenue_yoy_pct": financials.get("revenue_yoy_pct"),
                    "profit_yoy_pct": financials.get("profit_yoy_pct"),
                    "gross_margin_pct": financials.get("gross_margin_pct"),
                    "net_margin_pct": financials.get("net_margin_pct"),
                    "roe_pct": financials.get("roe_pct"),
                    "eps": financials.get("eps"),
                },
                "valuation": {
                    "pe": valuation.get("pe"),
                    "pb": valuation.get("pb"),
                    "ps": valuation.get("ps"),
                    "market_cap_100m": valuation.get("market_cap_100m"),
                    "dividend_yield_pct": valuation.get("dividend_yield_pct"),
                },
            },
            "confidence_constraints": signals["confidence_model"],
        }

    def _derive_fundamental_signals(self, data: dict, timeframe: str = "") -> dict:
        """用代码证据生成估值-质量矩阵判断、证据列表和置信度硬上限。"""
        scorecard = data.get("quality_scorecard", {}) or {}
        valuation = data.get("valuation_analysis", {}) or {}
        trend = data.get("financials", {}).get("_trend", {}) or {}
        trap = data.get("value_trap_analysis", {}) or {}
        dq = data.get("data_quality", {}) or {}

        total = self._safe_float(scorecard.get("total"), 0.0)
        rating = scorecard.get("rating", "unknown")
        pe_pct = self._safe_float(valuation.get("pe_percentile_3yr"), None)
        data_quality = self._safe_float(dq.get("overall_quality"), 1.0)
        base_ceiling = self._safe_float(dq.get("confidence_ceiling"), 0.70)
        scorecard_unreliable = (
            data_quality < 0.50
            or rating in ("unknown", "not_scorable")
            or bool(scorecard.get("not_scorable"))
        )

        if scorecard_unreliable:
            quality_bucket = "unknown_company"
            quality_label = "质量不可评分"
        elif total >= 70 or rating == "excellent":
            quality_bucket = "good_company"
            quality_label = "好公司"
        elif total >= 40 or rating in ("good", "average"):
            quality_bucket = "average_company"
            quality_label = "一般公司"
        else:
            quality_bucket = "weak_company"
            quality_label = "弱公司"

        if pe_pct is None:
            valuation_bucket = "unknown"
            valuation_label = "估值分位未知"
        elif pe_pct < 0.30:
            valuation_bucket = "undervalued"
            valuation_label = "低估"
        elif pe_pct <= 0.70:
            valuation_bucket = "fair"
            valuation_label = "合理"
        else:
            valuation_bucket = "overvalued"
            valuation_label = "高估"

        suggested_direction = "neutral"
        matrix_reason = "估值或质量证据不足，默认中性。"
        if trap.get("is_trap"):
            suggested_direction = "bearish"
            matrix_reason = "价值陷阱信号优先级最高。"
        elif quality_bucket == "good_company" and valuation_bucket == "undervalued":
            suggested_direction = "bullish"
            matrix_reason = "好公司且低估，基本面矩阵偏看涨。"
        elif quality_bucket == "good_company" and valuation_bucket == "fair":
            suggested_direction = "bullish"
            matrix_reason = "好公司且估值合理，基本面温和偏多。"
        elif quality_bucket == "good_company" and valuation_bucket == "overvalued":
            suggested_direction = "neutral"
            matrix_reason = "好公司但估值偏高，等待盈利消化估值。"
        elif quality_bucket == "average_company" and valuation_bucket == "undervalued":
            suggested_direction = "bullish"
            matrix_reason = "一般公司低估，偏估值修复逻辑。"
        elif quality_bucket == "average_company" and valuation_bucket == "overvalued":
            suggested_direction = "bearish"
            matrix_reason = "一般公司高估，估值压缩风险较大。"
        elif quality_bucket == "weak_company" and valuation_bucket == "overvalued":
            suggested_direction = "bearish"
            matrix_reason = "弱公司高估，基本面矩阵偏看空。"
        elif quality_bucket == "weak_company" and valuation_bucket == "undervalued":
            suggested_direction = "neutral"
            matrix_reason = "弱公司低估不自动构成机会，需警惕价值陷阱。"
        elif quality_bucket == "unknown_company":
            suggested_direction = "neutral"
            matrix_reason = "基本面数据覆盖不足，不能把低评分解释为公司质量差。"

        bullish = []
        bearish = []
        neutral = []

        if quality_bucket == "unknown_company":
            neutral.append(f"基本面数据覆盖不足({data_quality:.0%})，质量评分不可强解释")
        elif quality_bucket == "good_company":
            bullish.append(f"质量评分{total:.0f}分({rating})")
        elif quality_bucket == "weak_company":
            bearish.append(f"质量评分{total:.0f}分({rating})")
        else:
            neutral.append(f"质量评分{total:.0f}分({rating})")

        if pe_pct is None:
            neutral.append("缺少可用 PE 历史分位")
        elif pe_pct < 0.30:
            bullish.append(f"PE 处于历史{pe_pct*100:.0f}%分位")
        elif pe_pct > 0.70:
            bearish.append(f"PE 处于历史{pe_pct*100:.0f}%分位")
        else:
            neutral.append(f"PE 处于历史{pe_pct*100:.0f}%分位")

        if trend.get("profit_trend") in ("accelerating", "growing"):
            bullish.append(f"利润趋势{trend.get('profit_trend')}")
        elif trend.get("profit_trend") == "declining":
            bearish.append("利润趋势下滑")

        if trend.get("earnings_quality") == "improving":
            bullish.append("盈利质量改善")
        elif trend.get("earnings_quality") == "deteriorating":
            bearish.append("盈利质量恶化")

        if trap.get("is_trap"):
            bearish.append("触发价值陷阱信号: " + "；".join(trap.get("signals", [])[:3]))

        if data_quality < 0.5:
            neutral.append(f"数据完整度较低({data_quality:.0%})")

        hard_caps = []
        max_confidence = base_ceiling
        if data_quality < 0.30:
            max_confidence = min(max_confidence, 0.35)
            hard_caps.append("数据完整度低于30%，confidence 不超过0.35")
        elif data_quality < 0.50:
            max_confidence = min(max_confidence, 0.50)
            hard_caps.append("数据完整度低于50%，confidence 不超过0.50")

        if pe_pct is None:
            max_confidence = min(max_confidence, 0.65)
            hard_caps.append("缺少历史估值分位，confidence 不超过0.65")

        if "短期" in timeframe or "周" in timeframe:
            if suggested_direction != "neutral":
                max_confidence = min(max_confidence, 0.60)
                hard_caps.append("短期预测缺少明确催化剂时，基本面强方向 confidence 不超过0.60")

        return {
            "decision_matrix": {
                "quality_bucket": quality_bucket,
                "quality_label": quality_label,
                "valuation_bucket": valuation_bucket,
                "valuation_label": valuation_label,
                "matrix_position": f"{quality_label}+{valuation_label}",
                "suggested_direction": suggested_direction,
                "reason": matrix_reason,
            },
            "evidence": {
                "bullish": bullish,
                "bearish": bearish,
                "neutral": neutral,
            },
            "confidence_model": {
                "ceiling": base_ceiling,
                "max_confidence": round(max_confidence, 2),
                "quality_bucket": self._get_data_quality_bucket(data_quality),
                "hard_caps": hard_caps,
            },
        }

    def _sanitize_unsupported_fundamental_claims(
        self,
        result: AnalysisResult,
        data: dict,
    ) -> list[str]:
        """移除数据不支持的强财务趋势断言，避免把缺失说成暴跌。"""
        financials = data.get("financials", {}) or {}
        trend = financials.get("_trend", {}) or {}
        profit_yoy = self._safe_float(financials.get("profit_yoy_pct"), None)
        has_profit_evidence = (
            profit_yoy is not None
            or trend.get("profit_trend") not in (None, "", "insufficient_data", "unknown")
        )
        if has_profit_evidence:
            return []

        unsupported_terms = ("利润暴跌", "利润大跌", "利润下滑", "盈利恶化", "利润恶化")
        changed = False

        def sanitize_text(text: str) -> str:
            nonlocal changed
            new_text = str(text or "")
            for term in unsupported_terms:
                if term in new_text:
                    new_text = new_text.replace(term, "利润趋势数据缺失")
                    changed = True
            return new_text

        result.reasoning = sanitize_text(result.reasoning)
        result.key_factors = [sanitize_text(item) for item in (result.key_factors or [])]
        result.risks = [sanitize_text(item) for item in (result.risks or [])]
        if not changed:
            return []

        issue = "利润趋势字段缺失，已移除/替换 LLM 的利润下滑类强断言"
        if result.risks is None:
            result.risks = []
        result.risks.append(f"基本面证据约束: {issue}")
        if result.status == "ok":
            result.status = "degraded"
        return [issue]

    @staticmethod
    def _is_fundamental_data_empty(data: dict) -> bool:
        dq = data.get("data_quality", {}) or {}
        return (
            dq.get("financial_fields_filled") == "0/8"
            and dq.get("valuation_fields_filled") == "0/4"
            and data.get("data_source") in (None, "", "none")
        )

    def _apply_evidence_constraints(
        self, result: AnalysisResult, data: dict, context: dict
    ) -> list[str]:
        """用代码矩阵和硬上限约束 LLM 输出。"""
        signals = self._derive_fundamental_signals(
            data, context.get("timeframe", ""),
        )
        matrix = signals["decision_matrix"]
        confidence_model = signals["confidence_model"]
        issues = []

        suggested = matrix.get("suggested_direction", "neutral")
        max_conf = self._safe_float(confidence_model.get("max_confidence"), 0.70)
        if suggested != "neutral" and result.direction.value != suggested and result.confidence > 0.50:
            issues.append(
                f"基本面矩阵建议{suggested}，但 LLM 输出{result.direction.value}"
            )
            max_conf = min(max_conf, 0.50)

        if result.confidence > max_conf:
            issues.append(f"confidence({result.confidence:.2f})超过基本面证据上限({max_conf:.2f})")
            result.confidence = round(max_conf, 2)

        if not issues:
            return []

        if result.risks is None:
            result.risks = []
        existing = set(result.risks)
        for issue in issues:
            risk = f"基本面证据约束: {issue}"
            if risk not in existing:
                result.risks.append(risk)
                existing.add(risk)

        result.reasoning += (
            "\n\n---\n"
            "⚠️ **基本面证据约束提示**: " + "；".join(issues)
        )
        if result.status == "ok":
            result.status = "degraded"
        return issues

    def _fallback_step_a_result(self, data: dict) -> dict:
        """Step A JSON 解析失败时，用代码证据生成保守的预判。"""
        scorecard = data.get("quality_scorecard", {})
        valuation = data.get("valuation_analysis", {})
        trap = data.get("value_trap_analysis", {})
        dq = data.get("data_quality", {})
        signals = self._derive_fundamental_signals(data)
        matrix = signals["decision_matrix"]

        rating = scorecard.get("rating", "unknown")
        total = self._safe_float(scorecard.get("total"), 0.0)

        confidence = min(self._safe_float(dq.get("confidence_ceiling"), 0.45), 0.45)
        return {
            "quality_assessment": f"评分卡{total:.0f}分，评级{rating}",
            "valuation_judgment": valuation.get("interpretation", "估值分位数据不足"),
            "value_trap_risk": bool(trap.get("is_trap")),
            "key_catalysts": [],
            "key_risks": trap.get("signals", []) or dq.get("data_gaps", [])[:3],
            "preliminary_direction": matrix.get("suggested_direction", "neutral"),
            "preliminary_confidence": confidence,
            "fallback_reason": "Step A JSON 解析失败，使用代码证据包生成保守预判",
        }

    def _build_data_summary(
        self,
        data: dict,
        step_a_result: dict,
        consistency_issues: list[str],
    ) -> dict:
        """输出给 API/Aggregator 的结构化基本面摘要。"""
        evidence = self._build_evidence_packet(data)
        dq = data.get("data_quality", {})
        return {
            "symbol": data.get("symbol") or data.get("_resolved_symbol"),
            "name": data.get("company_name") or data.get("_resolved_name"),
            "market": data.get("market"),
            "industry": data.get("industry"),
            "source": data.get("data_source", "unknown"),
            "freshness": dq.get("freshness", "未提供"),
            "quality": dq.get("overall_quality", "unknown"),
            "data_quality": dq,
            "quality_scorecard": data.get("quality_scorecard", {}),
            "valuation_analysis": data.get("valuation_analysis", {}),
            "financial_trend": data.get("financials", {}).get("_trend", {}),
            "value_trap_analysis": data.get("value_trap_analysis", {}),
            "anomaly_flags": data.get("anomaly_flags", {}),
            "step_a_result": step_a_result,
            "consistency_issues": consistency_issues,
            "evidence": evidence,
        }
