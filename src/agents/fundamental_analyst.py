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
from src.core.llm_client import LLMClient
from src.core.result import AnalysisResult, Direction, Magnitude
from src.data.fundamental_fetcher import FundamentalFetcher
from src.data.symbol_resolver import resolve_symbol, identify_market
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
        return FUNDAMENTAL_SYSTEM_PROMPT

    async def analyze(self, data: dict, context: dict) -> AnalysisResult:
        """覆盖基类的 analyze()，实现两步 CoT 推理"""

        market = data.get("market", "A")

        # 注入市场区分附录
        market_appendix = self._get_market_appendix(market)
        system_prompt = FUNDAMENTAL_SYSTEM_PROMPT + market_appendix

        # === Step A: 综合评估（质量+估值+催化+风险） ===
        user_prompt_step_a = self._build_step_a_prompt(data, context)
        response_a = await self.llm.achat(
            system_prompt=system_prompt,
            user_prompt=user_prompt_step_a,
        )
        step_a_result = self._parse_json_from_response(response_a.content)

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
        self._validate_consistency(result, data)

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

        return f"""请基于以下基本面数据进行综合评估：

## 分析标的
{target}

## 预测周期
{timeframe}

## 数据已预处理，请直接使用以下结果：
### 质量评分卡
- 总分: {data.get('quality_scorecard', {}).get('total', 'N/A')} ({data.get('quality_scorecard', {}).get('rating', 'N/A')})
- 盈利能力: {data.get('quality_scorecard', {}).get('breakdown', {}).get('profitability', {})}
- 成长性: {data.get('quality_scorecard', {}).get('breakdown', {}).get('growth', {})}
- 估值: {data.get('quality_scorecard', {}).get('breakdown', {}).get('valuation', {})}
- 健康度: {data.get('quality_scorecard', {}).get('breakdown', {}).get('health', {})}

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

请给出最终判断。

### 反思环节
在给出判断前，请检查：
1. 有没有周期性因素被忽略？（当前是行业景气高点还是低点？）
2. 是否过度依赖历史数据而忽略了结构性变化？
3. 数据缺失最大的盲区是什么？

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
        try:
            json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
            if json_match:
                json_str = json_match.group(1).strip()
            else:
                brace_match = re.search(r'\{.*\}', content, re.DOTALL)
                if brace_match:
                    json_str = brace_match.group(0)
                else:
                    return {}

            json_str = re.sub(r':\s*\+(\d+\.?\d*)', r': \1', json_str)
            json_str = re.sub(r',\s*}', '}', json_str)

            return json.loads(json_str)
        except (json.JSONDecodeError, KeyError, ValueError):
            logger.warning(f"Step A JSON 解析失败")
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

    def _validate_consistency(self, result: AnalysisResult, data: dict):
        """校验结果与数据的一致性（仅警告，不修改）"""
        issues = []

        # 1. 估值分位-方向一致性
        va = data.get("valuation_analysis", {})
        pe_pct = va.get("pe_percentile_3yr")

        if pe_pct is not None:
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

        if issues:
            for issue in issues:
                logger.warning(f"[{self.name}] 一致性警告: {issue}")
