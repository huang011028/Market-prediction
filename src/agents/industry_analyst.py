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
from src.core.llm_json import parse_llm_json
from src.core.llm_client import LLMClient
from src.core.result import AnalysisResult, Direction, Magnitude
from src.data.industry_fetcher import IndustryFetcher
from src.data.symbol_resolver import resolve_symbol, identify_market
from src.prompts.dynamic_overrides import build_prompt_with_overrides
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
        return build_prompt_with_overrides(INDUSTRY_SYSTEM_PROMPT, self.name)

    def _use_compact_llm_path(self) -> bool:
        return bool(getattr(self.llm, "max_prompt_chars", 0) > 0)

    async def analyze(self, data: dict, context: dict) -> AnalysisResult:
        """覆盖基类的 analyze()，实现两步 CoT 推理（含轮动/产业链/催化剂）"""

        # 注入行业类型区分附录
        industry_appendix = self._get_industry_type_appendix(data)

        # 注入行业轮动信号
        rotation_signals = self.rotation_detector.detect_rotation_signals()
        data["_rotation_signals"] = rotation_signals
        rotation_appendix = build_rotation_prompt_appendix(rotation_signals)

        # 注入产业链分析
        industry_name = data.get("industry_name", "")
        chain_analysis = analyze_industry_chain(industry_name)
        data["_industry_chain"] = chain_analysis
        chain_appendix = ""
        if chain_analysis.get("implication"):
            chain_appendix = (
                f"\n\n## 🔗 产业链分析\n"
                f"上下游: {chain_analysis.get('description', 'N/A')}\n"
                f"传导分析: {chain_analysis.get('implication', 'N/A')}"
            )

        # 注入催化剂日历
        catalysts = get_upcoming_catalysts(industry_name)
        data["_catalysts"] = catalysts
        catalyst_appendix = build_catalyst_prompt_appendix(catalysts)

        system_prompt = build_prompt_with_overrides(
            INDUSTRY_SYSTEM_PROMPT +
            industry_appendix +
            rotation_appendix +
            chain_appendix +
            catalyst_appendix,
            self.name,
        )

        # === Step A: 定位 + 判断 ===
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
        sanitize_issues = self._sanitize_reference_peer_claims(result, data)
        all_issues = consistency_issues + evidence_issues
        all_issues.extend(sanitize_issues)

        # === 结构化摘要 ===
        result.data_summary = self._build_data_summary(
            data, step_a_result, all_issues,
        )
        result.data_quality_score = self._safe_float(
            data.get("data_quality", {}).get("overall"), 1.0,
        )
        if result.data_quality_score < 0.4 and result.status == "ok":
            result.status = "degraded"

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
        evidence = self._build_evidence_packet(data, timeframe)
        evidence_str = json.dumps(evidence, ensure_ascii=False, indent=2)

        return f"""## 综合判断

综合分析 {target}（{timeframe}）的行业对比：

### Step A 评估结果
- 公司定位: {step_a_result.get('position_analysis', 'N/A')}
- 行业前景: {step_a_result.get('industry_outlook', 'N/A')}
- 方向预判: {step_a_result.get('preliminary_direction', 'N/A')} (预设置信度: {step_a_result.get('preliminary_confidence', 'N/A')})

### 数据质量约束
- 数据完整度: {dq.get('overall', 'N/A')}
- 置信度上限: {ceiling}

### 行业证据包（代码计算，不依赖 LLM）
```json
{evidence_str}
```

请给出最终判断。

### 反思环节
在给出判断前，请检查：
1. 有没有周期性因素被忽略？（当前是行业景气高点还是低点？）
2. 板块轮动是否可能导致行业偏好变化？
3. 数据缺失最大的盲区是什么？

硬性约束:
- 不得编造未提供的行业排名、估值、成分股、轮动或催化剂。
- 若行业证据矩阵、性价比评分、行业趋势与方向矛盾，必须降低 confidence，并在 risks 中说明。
        - confidence 不得超过行业证据包中的置信度上限。
        - 短期预测中，若缺少明确行业催化剂，行业维度不得给高置信强判断。
        - 若 data_source 为 hk_peer_reference 或 data_quality.ranking_reliability 为 reference_snapshot，
          不得使用“垫底/最差/绝对劣势”等绝对化措辞，只能说“参考 peer 样本中靠后”，并说明非实时排名。

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
        calibrated = min(calibrated, ceiling)

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

    @staticmethod
    def _safe_float(value, default: float = 0.0) -> float:
        try:
            if value in (None, "", "N/A"):
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    def _build_evidence_packet(self, data: dict, timeframe: str = "") -> dict:
        """提取代码计算的行业证据，减少 Step B 对自由文本的依赖。"""
        signals = self._derive_industry_signals(data, timeframe)
        return {
            "identity": {
                "symbol": data.get("symbol") or data.get("_resolved_symbol"),
                "name": data.get("company_name") or data.get("_resolved_name"),
                "market": data.get("market"),
                "industry": data.get("industry_name"),
                "data_source": data.get("data_source"),
            },
            "rank_in_industry": data.get("rank_in_industry", {}),
            "value_score": data.get("value_score", {}),
            "industry_trend": data.get("industry_trend", {}),
            "rotation_signals": data.get("_rotation_signals", [])[:5],
            "industry_chain": data.get("_industry_chain", {}),
            "catalysts": data.get("_catalysts", [])[:5],
            "data_quality": data.get("data_quality", {}),
            "anomaly_flags": data.get("anomaly_flags", {}),
            "decision_matrix": signals["decision_matrix"],
            "evidence": signals["evidence"],
            "confidence_constraints": signals["confidence_model"],
        }

    def _derive_industry_signals(self, data: dict, timeframe: str = "") -> dict:
        """用行业排名、性价比和趋势生成矩阵判断、证据列表和置信度硬上限。"""
        rank = data.get("rank_in_industry", {}) or {}
        value = data.get("value_score", {}) or {}
        trend = data.get("industry_trend", {}) or {}
        dq = data.get("data_quality", {}) or {}
        rotation = data.get("_rotation_signals", []) or []
        catalysts = data.get("_catalysts", []) or []

        pe_pct = self._safe_float(rank.get("pe_percentile"), None)
        roe_pct = self._safe_float(rank.get("roe_percentile"), None)
        value_score = value.get("score", "unknown")
        cycle = trend.get("cycle", "unknown")
        data_quality = self._safe_float(dq.get("overall"), 1.0)
        base_ceiling = self._safe_float(dq.get("confidence_ceiling"), 0.45)
        reference_rank_only = (
            bool(dq.get("has_reference_peers"))
            or dq.get("ranking_reliability") == "reference_snapshot"
            or data.get("data_source") == "hk_peer_reference"
        )

        relative_bucket = "unknown"
        relative_label = "相对位置未知"
        if value_score in ("excellent", "good"):
            relative_bucket = "attractive"
            relative_label = "性价比突出"
        elif value_score in ("expensive", "overpriced"):
            relative_bucket = "expensive"
            relative_label = "参考样本相对偏贵" if reference_rank_only else "相对偏贵"
        elif value_score == "loss_making":
            relative_bucket = "loss_making"
            relative_label = "亏损公司"
        elif pe_pct is not None and roe_pct is not None:
            if pe_pct < 0.30 and roe_pct < 0.50:
                relative_bucket = "attractive"
                relative_label = "参考样本低估高质" if reference_rank_only else "低估高质"
            elif pe_pct > 0.70 and roe_pct > 0.70:
                relative_bucket = "expensive"
                relative_label = "参考样本高估低质" if reference_rank_only else "高估低质"
            elif pe_pct < 0.30 and roe_pct > 0.70:
                relative_bucket = "value_trap"
                relative_label = "低估低质"
            elif pe_pct > 0.70 and roe_pct < 0.30:
                relative_bucket = "growth_premium"
                relative_label = "高估高质"
            else:
                relative_bucket = "fair"
                relative_label = "相对合理"
        elif value_score == "fair":
            relative_bucket = "fair"
            relative_label = "相对合理"
        elif value_score == "insufficient_data":
            relative_bucket = "unknown"
            relative_label = "相对位置未知"

        trend_bucket = "neutral"
        trend_label = "趋势中性"
        if cycle == "recovery":
            trend_bucket = "positive"
            trend_label = "行业复苏"
        elif cycle == "boom":
            trend_bucket = "overheated"
            trend_label = "行业过热"
        elif cycle == "slowdown":
            trend_bucket = "negative"
            trend_label = "行业下行"
        elif cycle == "depression":
            trend_bucket = "weak"
            trend_label = "行业低迷"

        suggested_direction = "neutral"
        matrix_reason = "行业相对位置或趋势证据不足，默认中性。"
        if relative_bucket == "attractive" and trend_bucket in ("positive", "neutral"):
            suggested_direction = "bullish"
            matrix_reason = "标的相对行业性价比突出，且行业趋势不差。"
        elif relative_bucket == "attractive" and trend_bucket == "overheated":
            suggested_direction = "neutral"
            matrix_reason = "标的性价比好但行业可能过热，追涨需谨慎。"
        elif relative_bucket == "expensive" and trend_bucket in ("negative", "neutral", "weak"):
            suggested_direction = "bearish"
            matrix_reason = "标的相对行业偏贵，且行业趋势缺少支撑。"
        elif relative_bucket == "expensive" and trend_bucket == "overheated":
            suggested_direction = "neutral"
            matrix_reason = "高估叠加行业高位，风险上升但需观察动量延续。"
        elif relative_bucket == "value_trap":
            suggested_direction = "neutral"
            matrix_reason = "低估但盈利能力靠后，疑似价值陷阱。"
        elif relative_bucket == "loss_making":
            suggested_direction = "bearish"
            matrix_reason = "亏损公司在行业对比中风险优先。"
        elif trend_bucket == "negative":
            suggested_direction = "bearish"
            matrix_reason = "行业趋势下行，行业维度偏看空。"
        elif trend_bucket == "positive" and relative_bucket in ("fair", "growth_premium"):
            suggested_direction = "bullish"
            matrix_reason = "行业复苏对相对位置尚可的标的形成支撑。"

        if reference_rank_only:
            matrix_reason += " 但当前排名来自参考 peer 快照，不能作为实时行业绝对排名。"
            if trend_bucket == "neutral" and not catalysts:
                suggested_direction = "neutral"

        bullish = []
        bearish = []
        neutral = []

        if pe_pct is None:
            neutral.append("缺少行业 PE 分位")
        elif reference_rank_only:
            if pe_pct < 0.30:
                neutral.append(f"PE 在参考 peer 样本中处于{pe_pct*100:.0f}%分位，非实时成分股排名")
            elif pe_pct > 0.70:
                neutral.append(f"PE 在参考 peer 样本中处于{pe_pct*100:.0f}%分位，非实时成分股排名")
            else:
                neutral.append(f"PE 在参考 peer 样本中处于{pe_pct*100:.0f}%分位")
        elif pe_pct < 0.30:
            bullish.append(f"PE 处于行业{pe_pct*100:.0f}%分位")
        elif pe_pct > 0.70:
            bearish.append(f"PE 处于行业{pe_pct*100:.0f}%分位")
        else:
            neutral.append(f"PE 处于行业{pe_pct*100:.0f}%分位")

        if roe_pct is None:
            neutral.append("缺少行业 ROE 分位")
        elif reference_rank_only:
            if roe_pct < 0.30:
                neutral.append(f"ROE 在参考 peer 样本中靠前({roe_pct*100:.0f}%分位)，非实时成分股排名")
            elif roe_pct > 0.70:
                neutral.append(f"ROE 在参考 peer 样本中靠后({roe_pct*100:.0f}%分位)，非实时成分股排名")
            else:
                neutral.append(f"ROE 在参考 peer 样本中处于{roe_pct*100:.0f}%分位")
        elif roe_pct < 0.30:
            bullish.append(f"ROE 排名处于行业前{roe_pct*100:.0f}%")
        elif roe_pct > 0.70:
            bearish.append(f"ROE 排名处于行业后{(1-roe_pct)*100:.0f}%")
        else:
            neutral.append(f"ROE 排名处于行业{roe_pct*100:.0f}%分位")

        if value_score in ("excellent", "good"):
            bullish.append(f"性价比评分{value_score}: {value.get('interpretation', '')}".strip())
        elif value_score in ("expensive", "overpriced", "loss_making"):
            bearish.append(f"性价比评分{value_score}: {value.get('interpretation', '')}".strip())
        elif value_score:
            neutral.append(f"性价比评分{value_score}")

        if cycle in ("recovery",):
            bullish.append(f"行业周期{trend.get('phase', cycle)}: {trend.get('signal', '')}".strip())
        elif cycle in ("slowdown", "depression"):
            bearish.append(f"行业周期{trend.get('phase', cycle)}: {trend.get('signal', '')}".strip())
        elif cycle == "boom":
            neutral.append(f"行业周期{trend.get('phase', cycle)}: {trend.get('signal', '')}".strip())

        if rotation:
            neutral.append(f"检测到{len(rotation)}个行业轮动信号")

        positive_catalysts = [c for c in catalysts if c.get("impact") == "positive"]
        negative_catalysts = [c for c in catalysts if c.get("impact") == "negative"]
        if positive_catalysts:
            bullish.append("近期正向行业催化剂: " + "；".join(c["event"] for c in positive_catalysts[:2]))
        if negative_catalysts:
            bearish.append("近期负向行业催化剂: " + "；".join(c["event"] for c in negative_catalysts[:2]))

        hard_caps = []
        max_confidence = base_ceiling
        if data_quality < 0.30:
            max_confidence = min(max_confidence, 0.35)
            hard_caps.append("行业数据完整度低于30%，confidence 不超过0.35")
        elif data_quality < 0.50:
            max_confidence = min(max_confidence, 0.50)
            hard_caps.append("行业数据完整度低于50%，confidence 不超过0.50")

        if pe_pct is None or roe_pct is None:
            max_confidence = min(max_confidence, 0.60)
            hard_caps.append("缺少行业 PE/ROE 分位，confidence 不超过0.60")

        if ("短期" in timeframe or "周" in timeframe) and not catalysts:
            max_confidence = min(max_confidence, 0.60)
            hard_caps.append("短期行业判断缺少明确催化剂，confidence 不超过0.60")
        if reference_rank_only:
            max_confidence = min(max_confidence, 0.45)
            hard_caps.append("行业排名来自参考 peer 快照，confidence 不超过0.45，且不得作绝对排名断言")

        return {
            "decision_matrix": {
                "relative_value_bucket": relative_bucket,
                "relative_value_label": relative_label,
                "industry_trend_bucket": trend_bucket,
                "industry_trend_label": trend_label,
                "matrix_position": f"{relative_label}+{trend_label}",
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
                "quality_level": self._get_data_quality_level(dq),
                "hard_caps": hard_caps,
            },
        }

    def _sanitize_reference_peer_claims(
        self,
        result: AnalysisResult,
        data: dict,
    ) -> list[str]:
        """低质量参考 peer 场景下，移除“垫底/绝对劣势”等绝对化表述。"""
        dq = data.get("data_quality", {}) or {}
        reference_rank_only = (
            bool(dq.get("has_reference_peers"))
            or dq.get("ranking_reliability") == "reference_snapshot"
            or data.get("data_source") == "hk_peer_reference"
        )
        if not reference_rank_only:
            return []

        replacements = {
            "ROE垫底": "ROE在参考 peer 样本中靠后",
            "ROE 垫底": "ROE在参考 peer 样本中靠后",
            "行业垫底": "参考 peer 样本中靠后",
            "垫底": "参考样本靠后",
            "基本面绝对劣势": "参考样本显示相对劣势，但不能作为绝对行业结论",
            "绝对劣势": "参考样本相对劣势",
            "最差": "参考样本靠后",
        }
        changed = False

        def sanitize_text(text: str) -> str:
            nonlocal changed
            new_text = str(text or "")
            for old, new in replacements.items():
                if old in new_text:
                    new_text = new_text.replace(old, new)
                    changed = True
            return new_text

        result.reasoning = sanitize_text(result.reasoning)
        result.key_factors = [sanitize_text(item) for item in (result.key_factors or [])]
        result.risks = [sanitize_text(item) for item in (result.risks or [])]
        if not changed:
            return []

        issue = "行业排名来自参考 peer 快照，已移除绝对化排名措辞"
        if result.risks is None:
            result.risks = []
        result.risks.append(f"行业证据约束: {issue}")
        if result.status == "ok":
            result.status = "degraded"
        return [issue]

    def _fallback_step_a_result(self, data: dict) -> dict:
        """Step A JSON 解析失败时，用代码证据生成保守的行业预判。"""
        signals = self._derive_industry_signals(data)
        matrix = signals["decision_matrix"]
        rank = data.get("rank_in_industry", {})
        trend = data.get("industry_trend", {})
        return {
            "position_analysis": rank.get("valuation_label", matrix["matrix_position"]),
            "industry_outlook": trend.get("signal", "行业趋势证据不足"),
            "preliminary_direction": matrix.get("suggested_direction", "neutral"),
            "preliminary_confidence": min(
                signals["confidence_model"].get("max_confidence", 0.45), 0.45,
            ),
            "key_factors": signals["evidence"].get("bullish", [])[:2],
            "key_risks": signals["evidence"].get("bearish", [])[:2],
            "fallback_reason": "Step A JSON 解析失败，使用行业证据包生成保守预判",
        }

    def _apply_consistency_issues(
        self, result: AnalysisResult, issues: list[str]
    ) -> AnalysisResult:
        """把行业一致性校验结果写回 AnalysisResult。"""
        if not issues:
            return result

        if result.risks is None:
            result.risks = []
        existing = set(result.risks or [])
        for issue in issues:
            risk = f"行业一致性校验: {issue}"
            if risk not in existing:
                result.risks.append(risk)
                existing.add(risk)

        result.reasoning += (
            "\n\n---\n"
            "⚠️ **行业一致性校验提示**: " + "；".join(issues)
        )

        severe_markers = ("逻辑矛盾", "超过数据质量上限", "行业处于衰退期", "数据完整度较低", "但方向")
        if any(marker in issue for issue in issues for marker in severe_markers):
            old_confidence = result.confidence
            result.confidence = round(max(0.05, min(result.confidence, result.confidence * 0.85)), 2)
            if result.status == "ok":
                result.status = "degraded"
            logger.info(
                f"[{self.name}] 一致性降权: {old_confidence:.0%} → {result.confidence:.0%}"
            )

        return result

    def _apply_evidence_constraints(
        self, result: AnalysisResult, data: dict, context: dict
    ) -> list[str]:
        """用行业矩阵和硬上限约束 LLM 输出。"""
        signals = self._derive_industry_signals(data, context.get("timeframe", ""))
        matrix = signals["decision_matrix"]
        confidence_model = signals["confidence_model"]
        issues = []

        suggested = matrix.get("suggested_direction", "neutral")
        max_conf = self._safe_float(confidence_model.get("max_confidence"), 0.45)
        if suggested != "neutral" and result.direction.value != suggested and result.confidence > 0.50:
            issues.append(
                f"行业矩阵建议{suggested}，但 LLM 输出{result.direction.value}"
            )
            max_conf = min(max_conf, 0.50)

        if result.confidence > max_conf:
            issues.append(f"confidence({result.confidence:.2f})超过行业证据上限({max_conf:.2f})")
            result.confidence = round(max_conf, 2)

        if not issues:
            return []

        if result.risks is None:
            result.risks = []
        existing = set(result.risks)
        for issue in issues:
            risk = f"行业证据约束: {issue}"
            if risk not in existing:
                result.risks.append(risk)
                existing.add(risk)

        result.reasoning += (
            "\n\n---\n"
            "⚠️ **行业证据约束提示**: " + "；".join(issues)
        )
        if result.status == "ok":
            result.status = "degraded"
        return issues

    def _build_data_summary(
        self,
        data: dict,
        step_a_result: dict,
        consistency_issues: list[str],
    ) -> dict:
        """输出给 API/Aggregator 的结构化行业摘要。"""
        evidence = self._build_evidence_packet(data)
        return {
            "symbol": data.get("symbol") or data.get("_resolved_symbol"),
            "name": data.get("company_name") or data.get("_resolved_name"),
            "industry": data.get("industry_name"),
            "source": data.get("data_source", "unknown"),
            "quality": data.get("data_quality", {}).get("overall", "unknown"),
            "data_quality": data.get("data_quality", {}),
            "rank_in_industry": data.get("rank_in_industry", {}),
            "value_score": data.get("value_score", {}),
            "industry_trend": data.get("industry_trend", {}),
            "rotation_signals": data.get("_rotation_signals", [])[:5],
            "industry_chain": data.get("_industry_chain", {}),
            "catalysts": data.get("_catalysts", [])[:5],
            "anomaly_flags": data.get("anomaly_flags", {}),
            "step_a_result": step_a_result,
            "consistency_issues": consistency_issues,
            "evidence": evidence,
        }

    def _validate_consistency(self, result: AnalysisResult, data: dict) -> list[str]:
        """校验结果与数据的一致性。"""
        issues = []

        # 1. 排名-方向一致性
        rank = data.get("rank_in_industry", {})
        pe_pct = self._safe_float(rank.get("pe_percentile"), None)

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
        ceiling = self._safe_float(dq.get("confidence_ceiling"), 0.45)
        if result.confidence > ceiling + 0.05:
            issues.append(
                f"confidence({result.confidence})超过数据质量上限({ceiling})"
            )

        # 4. 行业趋势-方向一致性
        trend = data.get("industry_trend", {})
        cycle = trend.get("cycle")
        if cycle == "slowdown" and result.direction == Direction.BULLISH:
            issues.append("行业处于衰退期但方向为看涨——可以但需标注行业风险")

        # 5. 低数据质量不应强判断
        quality = self._safe_float(dq.get("overall"), 1.0)
        if quality < 0.4 and result.direction != Direction.NEUTRAL and result.confidence > 0.4:
            issues.append(
                f"行业数据完整度较低({quality:.0%})但给出非中性方向和较高置信度"
            )

        # 6. 看涨必须有风险提示
        if result.direction == Direction.BULLISH and not result.risks:
            issues.append("行业维度看涨但未列出任何行业风险")

        if issues:
            for issue in issues:
                logger.warning(f"[{self.name}] 一致性警告: {issue}")
        return issues
