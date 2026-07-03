"""
行业对比分析师 v3

获取行业对比数据 → 预处理管线（排名/趋势/性价比）
→ 行业轮动检测 + 产业链分析 + 催化剂日历
→ 多步 LLM 推理 → 置信度校准（含历史数据回归）→ 输出 AnalysisResult

v3 改进 (基于 v2):
- 行业轮动检测（估值极端/动量反转/风格切换）
- 产业链上下游分析
- 催化剂日历
- 置信度校准器（IndustryConfidenceCalibrator）
- 按行业准确率追踪
"""

import logging
import json
import re
from typing import Optional

from src.core.base_agent import BaseAgent
from src.core.llm_client import LLMClient
from src.core.result import AnalysisResult, Direction, Magnitude
from src.data.industry_fetcher import IndustryFetcher
from src.prompts.industry_prompts import (
    INDUSTRY_SYSTEM_PROMPT,
    CYCLICAL_INDUSTRY_APPENDIX,
    GROWTH_INDUSTRY_APPENDIX,
    DEFENSIVE_INDUSTRY_APPENDIX,
)
from src.utils.industry_calibrator import IndustryConfidenceCalibrator
from src.utils.sector_rotation_detector import (
    SectorRotationDetector,
    build_rotation_prompt_appendix,
)
from src.utils.industry_chain import (
    analyze_industry_chain,
    get_upcoming_catalysts,
    build_catalyst_prompt_appendix,
)

logger = logging.getLogger(__name__)


class IndustryAnalyst(BaseAgent):
    """行业对比分析师 v2

    判断标的在其行业中的估值分位、盈利能力排名、
    行业景气度方向，给出行业维度的方向判断。

    改进:
    - 使用预处理管线（排名、趋势、性价比）
    - 两步 CoT 推理
    - 置信度校准
    """

    def __init__(self, llm: LLMClient):
        super().__init__(
            name="行业对比分析师",
            description="对比标的与同行业公司的估值、盈利能力，判断行业景气度与相对位置",
            llm=llm,
        )
        self.fetcher = IndustryFetcher()
        self.calibrator = IndustryConfidenceCalibrator()
        self.rotation_detector = SectorRotationDetector()

    async def gather_data(self, target: str, timeframe: str) -> dict:
        """获取增强版行业对比数据"""
        market = self._identify_market(target)
        data = await self.fetcher.fetch_enhanced(target, market)
        return data

    def _identify_market(self, symbol: str) -> str:
        """识别市场，支持代码和中文名"""
        s = symbol.strip().upper().replace(".HK", "").replace(".SZ", "").replace(".SS", "")
        if s.isdigit():
            if len(s) <= 5:
                return "HK"
            return "A"
        # 中文港股名
        HK_NAMES = {"美团", "美团-W", "腾讯", "腾讯控股", "阿里巴巴", "阿里",
                    "百度", "京东", "小米", "小米集团", "快手", "网易",
                    "哔哩哔哩", "B站", "拼多多", "商汤", "海底捞", "安踏",
                    "李宁", "华润啤酒", "青岛啤酒", "中芯国际", "药明生物",
                    "信达生物", "百济神州", "君实生物"}
        if s in HK_NAMES:
            return "HK"
        if s.isalpha():
            return "US"
        return "US"

    def _get_system_prompt(self) -> str:
        return INDUSTRY_SYSTEM_PROMPT

    async def analyze(self, data: dict, context: dict) -> AnalysisResult:
        """覆盖基类的 analyze()，实现两步 CoT 推理（含轮动/产业链/催化剂）"""

        # 注入行业类型区分附录
        industry_appendix = self._get_industry_type_appendix(data)

        # 注入行业轮动信号
        rotation_signals = self.rotation_detector.detect_rotation_signals()
        rotation_appendix = build_rotation_prompt_appendix(rotation_signals)

        # 注入产业链分析
        industry_name = data.get("industry_name", "")
        chain_analysis = analyze_industry_chain(industry_name)
        chain_appendix = ""
        if chain_analysis.get("implication"):
            chain_appendix = (
                f"\n\n## 🔗 产业链分析\n"
                f"上下游: {chain_analysis.get('description', 'N/A')}\n"
                f"传导分析: {chain_analysis.get('implication', 'N/A')}"
            )

        # 注入催化剂日历
        catalysts = get_upcoming_catalysts(industry_name)
        catalyst_appendix = build_catalyst_prompt_appendix(catalysts)

        system_prompt = (
            INDUSTRY_SYSTEM_PROMPT +
            industry_appendix +
            rotation_appendix +
            chain_appendix +
            catalyst_appendix
        )

        # === Step A: 定位 + 判断 ===
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

    def _get_industry_type_appendix(self, data: dict) -> str:
        """根据行业类型获取对应的 prompt 附录"""
        industry_name = data.get("industry_name", "")

        # 周期性行业
        cyclical = ["钢铁", "有色金属", "煤炭", "化工", "房地产", "水泥", "石油"]
        # 成长性行业
        growth = ["科技", "半导体", "新能源", "生物医药", "计算机", "电子", "通信"]
        # 防御性行业
        defensive = ["食品饮料", "医药", "电力", "银行", "保险", "家电"]

        for keyword in cyclical:
            if keyword in industry_name:
                return "\n\n" + CYCLICAL_INDUSTRY_APPENDIX
        for keyword in growth:
            if keyword in industry_name:
                return "\n\n" + GROWTH_INDUSTRY_APPENDIX
        for keyword in defensive:
            if keyword in industry_name:
                return "\n\n" + DEFENSIVE_INDUSTRY_APPENDIX

        return ""

    def _build_step_a_prompt(self, data: dict, context: dict) -> str:
        """构建 Step A 的 prompt：定位 + 判断"""
        target = context.get("target", "N/A")
        timeframe = context.get("timeframe", "短期(1周)")

        # 数据质量提示
        dq = data.get("data_quality", {})
        quality_note = ""
        if dq.get("overall", 1.0) < 0.4:
            quality_note = (
                "\n\n⚠️ 注意：行业数据质量较低。"
                "请基于有限数据给出判断，在 reasoning 中标注数据局限性，并降低 confidence。"
            )

        # 构建已处理数据的摘要
        rank = data.get("rank_in_industry", {})
        value = data.get("value_score", {})
        trend = data.get("industry_trend", {})

        return f"""请基于以下行业对比数据进行分析：

## 分析标的
{target}

## 预测周期
{timeframe}

## 数据已预处理，请直接使用以下结果：

### 标的在行业中的排名
- PE排名: {rank.get('pe_rank', 'N/A')} (百分位: {rank.get('pe_percentile', 'N/A')})
- ROE排名: {rank.get('roe_rank', 'N/A')} (百分位: {rank.get('roe_percentile', 'N/A')})
- 估值标签: {rank.get('valuation_label', 'N/A')}
- vs行业中位数PE: {rank.get('vs_median_pe', 'N/A')}

### 性价比评分
- value_ratio: {value.get('value_ratio', 'N/A')}
- 评分: {value.get('score', 'N/A')}
- 解读: {value.get('interpretation', 'N/A')}

### 行业趋势
- 近5日: {trend.get('change_5d_pct', 'N/A')}%
- 近20日: {trend.get('change_20d_pct', 'N/A')}%
- 近60日: {trend.get('change_60d_pct', 'N/A')}%
- 周期阶段: {trend.get('phase', 'N/A')}
- 信号: {trend.get('signal', 'N/A')}

### 数据质量
- 完整度: {dq.get('overall', 'N/A')}
- 置信度上限: {dq.get('confidence_ceiling', 'N/A')}
- 说明: {dq.get('notes', 'N/A')}
{quality_note}

请回答：
1. 公司在行业中处于什么位置？（定位）
2. 这个位置意味着什么？（判断）
3. 行业当前处于什么周期阶段？对标的有什么影响？

输出 JSON:
{{
  "position_analysis": "公司在行业中的位置描述",
  "industry_outlook": "行业前景判断",
  "preliminary_direction": "bullish|bearish|neutral",
  "preliminary_confidence": 0.60,
  "key_factors": ["因素1", "因素2"],
  "key_risks": ["风险1", "风险2"]
}}"""

    def _build_step_b_prompt(self, data: dict, context: dict,
                              step_a_result: dict) -> str:
        """构建 Step B 的 prompt：综合判断 + 反思"""
        target = context.get("target", "N/A")
        timeframe = context.get("timeframe", "短期(1周)")
        dq = data.get("data_quality", {})
        ceiling = dq.get("confidence_ceiling", 0.45)

        return f"""## 综合判断

综合分析 {target}（{timeframe}）的行业对比：

### Step A 评估结果
- 公司定位: {step_a_result.get('position_analysis', 'N/A')}
- 行业前景: {step_a_result.get('industry_outlook', 'N/A')}
- 方向预判: {step_a_result.get('preliminary_direction', 'N/A')} (预设置信度: {step_a_result.get('preliminary_confidence', 'N/A')})

### 数据质量约束
- 数据完整度: {dq.get('overall', 'N/A')}
- 置信度上限: {ceiling}

请给出最终判断。

### 反思环节
在给出判断前，请检查：
1. 有没有周期性因素被忽略？（当前是行业景气高点还是低点？）
2. 板块轮动是否可能导致行业偏好变化？
3. 数据缺失最大的盲区是什么？

输出 JSON:
{{
  "direction": "bullish|bearish|neutral",
  "magnitude": {{"min_pct": -10.0, "max_pct": 10.0}},
  "confidence": 0.60,
  "reasoning": "完整分析推理过程（200-500字）",
  "key_factors": ["行业层面的利好因素", "行业层面的利空因素"],
  "risks": ["行业特有风险"],
  "position_analysis": "公司在行业中的位置最终描述",
  "industry_outlook": "行业前景最终判断"
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
        ceiling = dq.get("confidence_ceiling", 0.45)
        industry_name = data.get("industry_name")

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

        # 3. 性价比评分与方向一致性
        value = data.get("value_score", {})
        value_score = value.get("score", "")
        if value_score == "overpriced" and result.direction == Direction.BULLISH:
            calibrated *= 0.7
        elif value_score == "excellent" and result.direction == Direction.BEARISH:
            calibrated *= 0.7

        # 4. 基于历史数据的校准（如果有足够样本）
        data_quality_level = self._get_data_quality_level(dq)
        calibrated = self.calibrator.calibrate(
            raw_confidence=calibrated,
            industry=industry_name,
            data_quality_level=data_quality_level,
        )

        result.confidence = round(min(max(calibrated, 0.05), 0.95), 2)
        return result

    @staticmethod
    def _get_data_quality_level(dq: dict) -> str:
        """将数据质量映射到级别"""
        has_constituents = dq.get("has_constituents", False)
        has_trend = dq.get("has_trend", False)

        if has_constituents and has_trend:
            return "constituents+trend"
        elif has_constituents:
            return "constituents_only"
        elif dq.get("overall", 0) > 0.1:
            return "reference_only"
        else:
            return "none"

    def _validate_consistency(self, result: AnalysisResult, data: dict):
        """校验结果与数据的一致性（仅警告，不修改）"""
        issues = []

        # 1. 排名-方向一致性
        rank = data.get("rank_in_industry", {})
        pe_pct = rank.get("pe_percentile")

        if pe_pct is not None:
            if result.direction == Direction.BULLISH and pe_pct > 0.85:
                issues.append(f"PE排名在行业后15%(很贵)但方向为看涨")
            if result.direction == Direction.BEARISH and pe_pct < 0.15:
                issues.append(f"PE排名在行业前15%(很便宜)但方向为看跌")

        # 2. 性价比-方向一致性
        value = data.get("value_score", {})
        if value.get("score") == "overpriced" and result.direction == Direction.BULLISH:
            issues.append("性价比评分为'明显高估'但方向为看涨——逻辑矛盾")
        if value.get("score") == "excellent" and result.direction == Direction.BEARISH:
            issues.append("性价比评分为'明显低估'但方向为看跌——逻辑矛盾")

        # 3. 数据质量 ceiling
        dq = data.get("data_quality", {})
        ceiling = dq.get("confidence_ceiling", 0.45)
        if result.confidence > ceiling + 0.05:
            issues.append(
                f"confidence({result.confidence})超过数据质量上限({ceiling})"
            )

        # 4. 行业趋势-方向一致性（轻微检查）
        trend = data.get("industry_trend", {})
        cycle = trend.get("cycle")
        if cycle == "slowdown" and result.direction == Direction.BULLISH:
            issues.append("行业处于衰退期但方向为看涨——可以但需标注行业风险")

        if issues:
            for issue in issues:
                logger.warning(f"[{self.name}] 一致性警告: {issue}")
