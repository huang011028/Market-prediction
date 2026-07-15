"""
最终汇总分析师（Phase 2 升级版）

新功能：
- 接收权重配置，作为 LLM 综合判断的参考
- 处理更多 Agent（2→4 个）
- 四维交叉验证
- 失败 Agent 标注
"""

import json
import re
import logging
from typing import Optional

from src.core.llm_client import LLMClient
from src.core.llm_json import extract_json_text, parse_llm_json, repair_json_text
from src.core.result import (
    AnalysisResult, FinalReport, Direction, Magnitude,
)
from src.core.prediction_target import default_target_spec, market_for_target, resolve_prediction_target
from src.prompts.dynamic_overrides import build_prompt_with_overrides
from src.prompts.aggregator_prompts import AGGREGATOR_SYSTEM_PROMPT
from config.weight_manager import WeightConfig

logger = logging.getLogger(__name__)

FUNDAMENTAL_AGENT_NAME = "公司前景分析师"
STRUCTURED_EVIDENCE_DOMAINS = {
    "近期股价分析师": "技术",
    "公司前景分析师": "基本面",
    "行业对比分析师": "行业",
    "国际形势分析师": "宏观",
    "最新新闻分析师": "新闻",
}


class Aggregator:
    """最终汇总分析师（Phase 2）"""

    def __init__(self, llm: LLMClient):
        self.name = "汇总分析师"
        self.description = "综合各方分析结果，四维交叉验证，给出最终预测"
        self.llm = llm

    async def aggregate(
        self,
        target: str,
        timeframe: str,
        agent_results: list[AnalysisResult],
        weight_config: Optional[WeightConfig] = None,
        failed_agents: Optional[list[str]] = None,
    ) -> FinalReport:
        """综合所有 Agent 结果，生成最终报告

        Args:
            target: 分析标的
            timeframe: 预测周期
            agent_results: 各 Agent 的分析结果
            weight_config: 权重配置（Phase 2 新增）
            failed_agents: 执行失败的 Agent 名称列表

        Returns:
            FinalReport
        """
        failed = failed_agents or []

        logger.info(
            f"[汇总] 综合分析 | target={target} | "
            f"成功={len(agent_results)} | 失败={len(failed)}"
        )

        # 🆕 Round1: 评估各 Agent 质量
        quality_scores = {r.agent_name: self._score_quality(r) for r in agent_results}

        # 🆕 Round1: 动态调整权重
        if weight_config:
            original_weights = dict(weight_config.agent_weights)
            weight_config = self._adjust_weights(weight_config, quality_scores)
            adjusted_weights = dict(weight_config.agent_weights)
        else:
            original_weights = {}
            adjusted_weights = {}

        # 🆕 Round2: 分歧量化 + 贡献度
        disagreement = self._calculate_disagreement(agent_results)
        contributions = self._calculate_contributions(
            agent_results, original_weights, adjusted_weights, quality_scores)

        # 构建上下文
        context = self._build_context(
            target, timeframe, agent_results,
            quality_scores, weight_config, failed,
            disagreement, contributions,
        )

        # LLM 分析
        try:
            response = await self.llm.achat(
                system_prompt=build_prompt_with_overrides(
                    AGGREGATOR_SYSTEM_PROMPT,
                    self.name,
                ),
                user_prompt=context,
            )
        except Exception as e:
            logger.error(f"[汇总分析师] LLM 失败: {e}")
            report = self._fallback_report(target, timeframe, agent_results, failed, str(e))
            report = self._apply_final_decision_guardrails(
                report, quality_scores, weight_config,
            )
            return self._apply_learned_aggregator(report, agent_results)

        report = self._parse_response(
            response.content, target, timeframe, agent_results, failed,
        )
        report = self._apply_final_decision_guardrails(
            report, quality_scores, weight_config,
        )
        return self._apply_learned_aggregator(report, agent_results)

    def _apply_learned_aggregator(
        self,
        report: FinalReport,
        agent_results: list[AnalysisResult],
    ) -> FinalReport:
        """Apply a validated learned policy; shadow/disabled artifacts are ignored."""
        try:
            from src.core.learned_aggregator import LearnedAggregatorPolicy

            market = market_for_target(report.target)
            horizon = getattr(report.prediction_target, "horizon", "5d")
            pooled = LearnedAggregatorPolicy().aggregate(
                agent_results,
                market or "",
                horizon,
            )
        except Exception as exc:
            logger.debug("学习型 Aggregator 未应用: %s", exc)
            return report
        if not pooled:
            return report

        report.prob_down = pooled["prob_down"]
        report.prob_no_edge = pooled["prob_no_edge"]
        report.prob_up = pooled["prob_up"]
        report.expected_excess_return_pct = pooled["expected_excess_return_pct"]
        probabilities = {
            Direction.BEARISH: report.prob_down,
            Direction.NEUTRAL: report.prob_no_edge,
            Direction.BULLISH: report.prob_up,
        }
        report.direction = max(probabilities, key=probabilities.get)
        report.confidence = max(probabilities.values())
        report.edge_score = None
        report.decision = "observe"
        report.no_trade_reason = ""
        report.neutral_reason = ""
        report.disagreements.append(
            f"最终概率由已验证学习型 Aggregator {pooled.get('model_version', '')} 生成"
        )
        self._normalize_final_distribution(report)
        return report

    # ================================================================
    # 上下文构建（含权重）
    # ================================================================

    def _build_context(
        self,
        target: str,
        timeframe: str,
        results: list[AnalysisResult],
        quality_scores: dict,
        weight_config: Optional[WeightConfig],
        failed_agents: list[str],
        disagreement: dict,
        contributions: list[dict],
    ) -> str:
        parts = [
            "## 分析任务",
            f"- 标的: {target}",
            f"- 周期: {timeframe}",
            f"- 参与分析的 Agent: {len(results)} 个 | 失败: {len(failed_agents)} 个",
            "",
            "## 预测目标规格",
            "```json",
            json.dumps(
                default_target_spec(timeframe, target=target).to_dict(),
                ensure_ascii=False,
                indent=2,
            ),
            "```",
            "",
        ]

        # 质量评估
        parts.append("## 🔍 Agent 分析质量评估")
        parts.append("")
        parts.append("| Agent | 数据质量 | 数据源 | 可信度 | 备注 |")
        parts.append("|-------|---------|--------|--------|------|")
        for r in results:
            q = quality_scores.get(r.agent_name, {})
            qual_emoji = {"good": "✅", "normal": "✅", "partial": "⚠️", "poor": "❌"}.get(q.get("data_quality",""), "❓")
            rel_emoji = {"high": "高", "medium": "中", "low": "低"}.get(q.get("reliability",""), "?")
            issues = "; ".join(q.get("issues", [])) or "无"
            parts.append(
                f"| {r.agent_name} | {qual_emoji} {q.get('data_quality','?')} "
                f"| {q.get('source_type','?')} | {rel_emoji} | {issues} |"
            )
        parts.append("")
        parts.append("> 💡 质量低的 Agent 应降低其在综合判断中的实际权重。")
        parts.append("")

        structured_evidence = [
            evidence for evidence in (
                self._extract_structured_evidence(r) for r in results
            ) if evidence
        ]
        if structured_evidence:
            parts.append("## 结构化 Agent 证据（含结构化基本面证据）")
            parts.append("")
            parts.append("| Agent | 领域 | 矩阵 | 建议方向 | 质量评分 | 置信上限 | 约束 | 扩展 | 核心证据 |")
            parts.append("|-------|------|------|----------|----------|----------|------|------|----------|")
            for e in structured_evidence:
                constraints = "; ".join(e.get("hard_caps", [])[:2]) or "无"
                evidence_bits = (
                    (e.get("bullish_evidence") or [])[:1]
                    + (e.get("bearish_evidence") or [])[:1]
                    + (e.get("neutral_evidence") or [])[:1]
                )
                evidence_text = "; ".join(evidence_bits[:2]) or "无"
                extras = []
                if e.get("pe_percentile") is not None:
                    extras.append(f"PE分位 {self._format_ratio(e.get('pe_percentile'))}")
                if e.get("is_value_trap") is not None:
                    extras.append("价值陷阱 " + ("是" if e.get("is_value_trap") else "否"))
                event_matrix = e.get("event_impact_matrix") or {}
                if event_matrix.get("dominant_event"):
                    extras.append(f"主导事件 {event_matrix.get('dominant_event')}")
                transmission = e.get("transmission_matrix") or {}
                if transmission.get("macro_regime"):
                    extras.append(f"宏观 {transmission.get('macro_regime')}")
                extra_text = "; ".join(extras) or "无"
                parts.append(
                    f"| {e['agent']} | {e.get('domain', '未知')} "
                    f"| {e.get('matrix_position', 'N/A')} "
                    f"| {e.get('suggested_direction', 'neutral')} "
                    f"| {self._format_ratio(e.get('quality_score'))} "
                    f"| {self._format_ratio(e.get('max_confidence'))} "
                    f"| {constraints} | {extra_text} | {evidence_text} |"
                )
            parts.append("")
            parts.append(
                "> 结构化证据优先于各 Agent 的自由文本 reasoning；"
                "若矩阵、价值陷阱或置信上限与方向冲突，应降低其投票权重；"
                "若事件冲击与方向冲突，也应降低其投票权重。"
            )
            parts.append("")

        # 权重参考
        if weight_config and weight_config.agent_weights:
            parts.append("## ⚖️ 权重参考")
            parts.append(f"以下为 {timeframe} 预测各维度的参考权重：")
            parts.append("")
            for name, w in sorted(
                weight_config.agent_weights.items(), key=lambda x: -x[1]
            ):
                parts.append(f"- {name}: **{w:.0%}**")
            if weight_config.synthesis_weight > 0:
                parts.append(f"- 综合研判弹性: **{weight_config.synthesis_weight:.0%}**")
            parts.append("")
            parts.append("> 权重仅供参考，你作为研究主管可根据各 Agent 的分析质量适当调整。")
            parts.append("")

        # 失败 Agent 说明
        if failed_agents:
            parts.append("## ⚠️ 未参与分析的 Agent")
            for name in failed_agents:
                parts.append(f"- **{name}** 执行失败，其观点不在以下报告中")
            parts.append("请在你的分析中说明这些缺失对综合判断的影响。")
            parts.append("")

        # 各 Agent 报告
        parts.append("---")
        parts.append("")
        parts.append("## 各维度分析报告")
        parts.append("")

        for i, r in enumerate(results, 1):
            parts.append(f"### {i}. {r.agent_name}")
            parts.append("")
            parts.append(f"**方向**: {self._dir_label(r.direction)}")
            if r.magnitude:
                parts.append(f"**幅度**: {r.magnitude.range_str}")
            parts.append(f"**置信度**: {r.confidence:.0%}")
            if r.prediction_target:
                parts.append(
                    "**目标**: "
                    f"{r.prediction_target.horizon}, "
                    f"{r.prediction_target.target_type}, "
                    f"预期收益 {r.prediction_target.expected_return_pct:+.1f}%, "
                    f"P涨/跌/中性="
                    f"{r.prediction_target.prob_up:.0%}/"
                    f"{r.prediction_target.prob_down:.0%}/"
                    f"{r.prediction_target.prob_neutral:.0%}"
                )

            parts.append("")
            parts.append(f"**推理过程**:")
            parts.append(r.reasoning)

            if r.key_factors:
                parts.append(f"\n**关键因素**:")
                for f in r.key_factors[:5]:
                    parts.append(f"- {f}")

            if r.risks:
                parts.append(f"\n**风险提示**:")
                for risk in r.risks[:5]:
                    parts.append(f"- {risk}")

            parts.append("")
            parts.append("---")
            parts.append("")

        # 🆕 Round2: 分歧量化
        if disagreement.get("level") != "none":
            parts.append("## 📐 分歧量化")
            parts.append("")
            parts.append(f"- 分歧等级: **{disagreement['level']}**")
            parts.append(f"- 方向分布: 看涨{disagreement['direction_counts']['bullish']} / "
                         f"看跌{disagreement['direction_counts']['bearish']} / "
                         f"中性{disagreement['direction_counts']['neutral']}")
            parts.append(f"- 幅度离散度: {disagreement['magnitude_spread']}%")
            if disagreement["outliers"]:
                parts.append(f"- ⚠️ 异常值: {', '.join(disagreement['outliers'])} 与多数人方向不同")
            parts.append("")

        # 🆕 Round2: Agent 贡献度
        if contributions:
            parts.append("## 🏷️ Agent 贡献度")
            parts.append("")
            parts.append("| Agent | 原始权重 | 调整后 | 影响 | 方向 | 置信度 |")
            parts.append("|-------|---------|--------|------|------|--------|")
            for c in contributions:
                parts.append(
                    f"| {c['agent']} | {c['static_weight']:.0%} | {c['adjusted_weight']:.0%} "
                    f"| {c['impact']} | {c['direction']} | {c['confidence']:.0%} |"
                )
            parts.append("")

        # 任务
        parts.append("## 你的任务")
        parts.append("")
        parts.append("请综合以上所有分析报告，进行四维交叉验证和综合研判，输出最终分析结论。")
        parts.append("特别注意：")
        parts.append("1. 技术面×新闻面：短期情绪是否与技术信号一致？")
        parts.append("2. 基本面×宏观面：中长期价值是否被宏观环境支撑？")
        parts.append("3. 技术面×基本面：价格是否偏离内在价值？")
        parts.append("4. 新闻面×宏观面：事件冲击是短期还是结构性的？")
        parts.append("5. 任一 Agent 若出现 degraded、矩阵冲突或低置信上限，不得只按 reasoning 的方向直接采信。")
        parts.append("")
        parts.append("请严格按 JSON 格式输出。")
        parts.append("JSON 中必须包含收益分布和可操作边际字段：")
        parts.append("- expected_excess_return_pct: 目标周期风险调整后预期超额收益百分比")
        parts.append("- prob_up / prob_down / prob_no_edge: 看涨、看跌、无边际概率，三者应接近 1")
        parts.append("- decision: long_bias / short_bias / watchlist / observe / avoid")
        parts.append("- no_trade_reason / neutral_reason: no_edge / conflict / data_insufficient / priced_in")
        parts.append("- prediction_target.expected_return_pct/prob_up/prob_down/prob_neutral 应与上述字段一致")

        return "\n".join(parts)

    # ================================================================
    # 最终裁决闭环
    # ================================================================

    def _apply_final_decision_guardrails(
        self,
        report: FinalReport,
        quality_scores: dict,
        weight_config: Optional[WeightConfig],
    ) -> FinalReport:
        """用结构化证据复核 LLM 最终裁决，防止强方向脱离证据。"""
        audit = self._build_decision_audit(report, quality_scores, weight_config)
        if not audit.get("has_evidence"):
            self._normalize_final_distribution(report, audit)
            return report

        notes = []
        top_direction = audit.get("top_direction")
        final_direction = report.direction.value
        final_score = audit.get("direction_scores", {}).get(final_direction, 0.0)
        top_score = audit.get("top_score", 0.0)
        margin = top_score - final_score

        if (
            top_direction
            and top_direction != final_direction
            and top_score >= 0.12
            and margin >= 0.08
        ):
            message = (
                f"结构化证据多数指向{top_direction}，但最终输出为{final_direction}"
                f"（证据分差{margin:.2f}）"
            )
            notes.append(message)
            if final_direction != "neutral":
                report.direction = Direction.NEUTRAL
                report.magnitude = None
                report.confidence = min(report.confidence, 0.45)
                report.no_trade_reason = report.no_trade_reason or "conflict"
                report.neutral_reason = report.neutral_reason or "conflict"
                notes.append("最终方向已降为neutral以等待证据重新确认")
            else:
                report.confidence = min(report.confidence, 0.55)
                report.neutral_reason = report.neutral_reason or "conflict"

        aggregate_cap = audit.get("aggregate_confidence_cap")
        if aggregate_cap is not None and report.confidence > aggregate_cap:
            notes.append(
                f"综合置信度({report.confidence:.0%})超过结构化证据上限({aggregate_cap:.0%})"
            )
            report.confidence = round(max(0.05, aggregate_cap), 2)

        low_reliability_agents = audit.get("low_reliability_agents", [])
        if low_reliability_agents and report.confidence > 0.70:
            report.confidence = min(report.confidence, 0.70)
            notes.append(
                "存在低可信结构化证据 Agent: " + ", ".join(low_reliability_agents[:3])
            )

        if not notes:
            self._normalize_final_distribution(report, audit)
            return report

        report.disagreements = report.disagreements or []
        report.key_risks = report.key_risks or []
        for note in notes:
            disagreement = f"最终裁决复核: {note}"
            if disagreement not in report.disagreements:
                report.disagreements.append(disagreement)
            risk = f"最终裁决护栏: {note}"
            if risk not in report.key_risks:
                report.key_risks.append(risk)

        audit_summary = (
            "最终裁决复核: "
            f"结构化证据方向分布={audit.get('direction_scores')}, "
            f"置信上限={audit.get('aggregate_confidence_cap'):.0%}"
            if audit.get("aggregate_confidence_cap") is not None
            else "最终裁决复核: 结构化证据已检查。"
        )
        if audit_summary not in report.summary:
            report.summary = f"{report.summary}\n\n{audit_summary}"

        self._normalize_final_distribution(report, audit)
        return report

    def _build_decision_audit(
        self,
        report: FinalReport,
        quality_scores: dict,
        weight_config: Optional[WeightConfig],
    ) -> dict:
        """汇总结构化证据的方向分布和整体置信上限。"""
        direction_scores = {"bullish": 0.0, "bearish": 0.0, "neutral": 0.0}
        cap_numerator = 0.0
        cap_denominator = 0.0
        evidence_count = 0
        low_reliability_agents = []

        default_weight = 1.0 / max(1, len(report.agent_results))
        weights = weight_config.agent_weights if weight_config else {}
        for result in report.agent_results:
            evidence = self._extract_structured_evidence(result)
            if not evidence:
                continue

            evidence_count += 1
            suggested = evidence.get("suggested_direction") or result.direction.value
            if suggested not in direction_scores:
                suggested = "neutral"

            q = quality_scores.get(result.agent_name, {})
            base_weight = self._safe_float(weights.get(result.agent_name), default_weight)
            weight_factor = self._safe_float(q.get("weight_factor"), 1.0)
            max_confidence = self._safe_float(evidence.get("max_confidence"), result.confidence)
            confidence_component = min(result.confidence, max_confidence)

            reliability = q.get("reliability", "medium")
            reliability_factor = {"high": 1.0, "medium": 0.75, "low": 0.45}.get(
                reliability, 0.75
            )
            if reliability == "low":
                low_reliability_agents.append(result.agent_name)

            contribution = (
                base_weight
                * weight_factor
                * reliability_factor
                * max(0.25, confidence_component)
            )
            direction_scores[suggested] += contribution

            if max_confidence is not None:
                cap_numerator += max_confidence * max(0.01, base_weight)
                cap_denominator += max(0.01, base_weight)

        if evidence_count == 0:
            return {"has_evidence": False}

        direction_scores = {
            key: round(value, 4) for key, value in direction_scores.items()
        }
        top_direction = max(direction_scores, key=direction_scores.get)
        top_score = direction_scores[top_direction]

        aggregate_cap = None
        if cap_denominator > 0:
            aggregate_cap = min(0.85, cap_numerator / cap_denominator + 0.10)
            aggregate_cap = round(max(0.25, aggregate_cap), 2)

        return {
            "has_evidence": True,
            "evidence_count": evidence_count,
            "direction_scores": direction_scores,
            "top_direction": top_direction,
            "top_score": top_score,
            "aggregate_confidence_cap": aggregate_cap,
            "low_reliability_agents": low_reliability_agents,
        }

    @staticmethod
    def _dir_label(d: Direction) -> str:
        m = {
            Direction.BULLISH: "📈 看涨",
            Direction.BEARISH: "📉 看跌",
            Direction.NEUTRAL: "➡️ 震荡/中性",
        }
        return m.get(d, d.value)

    # ================================================================
    # 解析
    # ================================================================

    def _parse_response(
        self,
        content: str,
        target: str,
        timeframe: str,
        agent_results: list[AnalysisResult],
        failed_agents: list[str],
    ) -> FinalReport:
        parsed = parse_llm_json(content)
        if not parsed.ok or not isinstance(parsed.data, dict):
            return FinalReport(
                target=target, timeframe=timeframe,
                direction=Direction.NEUTRAL, confidence=0.0,
                prediction_target=default_target_spec(timeframe, target=target),
                agent_results=agent_results,
                summary=content,
                key_risks=[
                    "LLM 返回格式异常",
                    f"解析错误: {parsed.error}" if parsed.error else "解析错误: unknown",
                ],
            )
        data = parsed.data

        direction_raw = str(data.get("direction", "neutral")).lower().strip()
        try:
            direction = Direction(direction_raw)
        except ValueError:
            direction = Direction.NEUTRAL

        magnitude = None
        mag = data.get("magnitude")
        if mag and isinstance(mag, dict):
            try:
                magnitude = Magnitude(
                    min_pct=float(mag.get("min_pct", 0)),
                    max_pct=float(mag.get("max_pct", 0)),
                )
            except (ValueError, TypeError, KeyError):
                pass

        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
        expected_excess = self._safe_float(data.get("expected_excess_return_pct"), None)
        expected_p10 = self._safe_float(data.get("expected_return_p10"), None)
        expected_p50 = self._safe_float(data.get("expected_return_p50"), None)
        expected_p90 = self._safe_float(data.get("expected_return_p90"), None)
        prob_up = self._probability(data.get("prob_up"))
        prob_down = self._probability(data.get("prob_down"))
        prob_no_edge = self._probability(
            data.get("prob_no_edge", data.get("prob_neutral"))
        )
        prediction_target = data.get("prediction_target")
        if isinstance(prediction_target, dict):
            if expected_excess is not None and prediction_target.get("expected_return_pct") is None:
                prediction_target["expected_return_pct"] = expected_excess
            if prob_up is not None and prediction_target.get("prob_up") is None:
                prediction_target["prob_up"] = prob_up
            if prob_down is not None and prediction_target.get("prob_down") is None:
                prediction_target["prob_down"] = prob_down
            if prob_no_edge is not None and prediction_target.get("prob_neutral") is None:
                prediction_target["prob_neutral"] = prob_no_edge
            prediction_target.setdefault("direction", direction.value)
        decision = self._normalize_decision(data.get("decision"))
        no_trade_reason = self._normalize_reason(data.get("no_trade_reason"))
        neutral_reason = self._normalize_reason(data.get("neutral_reason"))

        report = FinalReport(
            target=target,
            timeframe=timeframe,
            direction=direction,
            magnitude=magnitude,
            confidence=confidence,
            prediction_target=prediction_target,
            expected_excess_return_pct=expected_excess,
            expected_return_p10=expected_p10,
            expected_return_p50=expected_p50,
            expected_return_p90=expected_p90,
            prob_up=prob_up,
            prob_down=prob_down,
            prob_no_edge=prob_no_edge,
            decision=decision,
            no_trade_reason=no_trade_reason,
            neutral_reason=neutral_reason,
            edge_score=self._safe_float(data.get("edge_score"), None),
            agent_results=agent_results,
            summary=str(data.get("summary", content[:500])),
            key_risks=data.get("key_risks", []),
            disagreements=data.get("disagreements", []),
        )
        self._normalize_final_distribution(report)
        return report

    # ================================================================
    # 降级
    # ================================================================

    def _fallback_report(
        self, target: str, timeframe: str,
        agent_results: list[AnalysisResult],
        failed_agents: list[str],
        error: str,
    ) -> FinalReport:
        bullish = sum(1 for r in agent_results if r.direction == Direction.BULLISH)
        bearish = sum(1 for r in agent_results if r.direction == Direction.BEARISH)

        if bullish > bearish:
            direction = Direction.BULLISH
        elif bearish > bullish:
            direction = Direction.BEARISH
        else:
            direction = Direction.NEUTRAL

        all_risks = []
        for r in agent_results:
            all_risks.extend(r.risks)

        report = FinalReport(
            target=target, timeframe=timeframe,
            direction=direction, confidence=0.3,
            prediction_target=default_target_spec(timeframe, target=target),
            agent_results=agent_results,
            summary=f"⚠️ 综合分析 LLM 调用失败: {error}\n\n以下为基于规则简单汇总的结果。",
            key_risks=list(set(all_risks))[:5],
            disagreements=["综合分析未能执行"],
            decision="observe",
            no_trade_reason="data_insufficient",
            neutral_reason="data_insufficient" if direction == Direction.NEUTRAL else "",
        )
        self._normalize_final_distribution(report)
        return report

    @staticmethod
    def _probability(value) -> Optional[float]:
        parsed = Aggregator._safe_float(value, None)
        if parsed is None:
            return None
        if parsed > 1.0 and parsed <= 100.0:
            parsed = parsed / 100.0
        return max(0.0, min(1.0, parsed))

    @staticmethod
    def _normalize_decision(value) -> str:
        text = str(value or "").lower().strip()
        aliases = {
            "buy": "long_bias",
            "long": "long_bias",
            "bullish": "long_bias",
            "sell": "short_bias",
            "short": "short_bias",
            "bearish": "short_bias",
            "watch": "watchlist",
            "hold": "observe",
            "neutral": "observe",
            "no_trade": "observe",
            "wait": "observe",
        }
        text = aliases.get(text, text)
        return text if text in {"long_bias", "short_bias", "watchlist", "observe", "avoid"} else "observe"

    @staticmethod
    def _normalize_reason(value) -> str:
        text = str(value or "").lower().strip()
        aliases = {
            "data_missing": "data_insufficient",
            "missing_data": "data_insufficient",
            "insufficient_data": "data_insufficient",
            "priced-in": "priced_in",
            "pricedin": "priced_in",
            "already_priced": "priced_in",
            "no edge": "no_edge",
            "no-edge": "no_edge",
        }
        text = aliases.get(text, text)
        return text if text in {"no_edge", "conflict", "data_insufficient", "priced_in"} else ""

    def _normalize_final_distribution(
        self,
        report: FinalReport,
        audit: Optional[dict] = None,
    ) -> None:
        """规范最终收益分布、决策标签和目标规格。"""
        pt = report.prediction_target or default_target_spec(report.timeframe, target=report.target)
        expected = self._safe_float(report.expected_excess_return_pct, None)
        if expected is None:
            expected = self._safe_float(pt.expected_return_pct, 0.0)
        report.expected_excess_return_pct = round(float(expected or 0.0), 2)
        report.expected_return_p50 = round(float(
            self._safe_float(report.expected_return_p50, report.expected_excess_return_pct)
        ), 2)
        if report.expected_return_p10 is None:
            report.expected_return_p10 = (
                float(report.magnitude.min_pct)
                if report.magnitude else report.expected_return_p50 - abs(float(pt.up_threshold_pct))
            )
        if report.expected_return_p90 is None:
            report.expected_return_p90 = (
                float(report.magnitude.max_pct)
                if report.magnitude else report.expected_return_p50 + abs(float(pt.up_threshold_pct))
            )
        report.expected_return_p10 = round(min(
            float(report.expected_return_p10), report.expected_return_p50
        ), 2)
        report.expected_return_p90 = round(max(
            float(report.expected_return_p90), report.expected_return_p50
        ), 2)

        prob_up = self._probability(report.prob_up)
        prob_down = self._probability(report.prob_down)
        prob_no_edge = self._probability(report.prob_no_edge)
        if prob_up is None:
            prob_up = self._probability(pt.prob_up)
        if prob_down is None:
            prob_down = self._probability(pt.prob_down)
        if prob_no_edge is None:
            prob_no_edge = self._probability(pt.prob_neutral)
        if prob_up is None or prob_down is None or prob_no_edge is None:
            probs = self._probabilities_from_direction(report.direction.value, report.confidence)
            prob_up = prob_up if prob_up is not None else probs["bullish"]
            prob_down = prob_down if prob_down is not None else probs["bearish"]
            prob_no_edge = prob_no_edge if prob_no_edge is not None else probs["neutral"]

        total = max(prob_up + prob_down + prob_no_edge, 1e-9)
        report.prob_up = round(prob_up / total, 4)
        report.prob_down = round(prob_down / total, 4)
        report.prob_no_edge = round(prob_no_edge / total, 4)

        threshold = max(abs(pt.up_threshold_pct), abs(pt.down_threshold_pct), 1.0)
        edge_score = self._safe_float(report.edge_score, None)
        if edge_score is None:
            directional_prob = max(report.prob_up, report.prob_down)
            edge_score = min(1.0, abs(report.expected_excess_return_pct) / threshold * directional_prob)
        report.edge_score = round(max(0.0, min(1.0, edge_score)), 4)

        report.decision = self._normalize_decision(report.decision)
        if report.decision == "observe":
            report.decision = self._decision_from_distribution(report, threshold)

        report.no_trade_reason = self._normalize_reason(report.no_trade_reason)
        report.neutral_reason = self._normalize_reason(report.neutral_reason)
        if report.direction == Direction.NEUTRAL and not report.neutral_reason:
            report.neutral_reason = self._infer_neutral_reason(report, audit)
        if report.decision in {"observe", "avoid"} and not report.no_trade_reason:
            report.no_trade_reason = report.neutral_reason or self._infer_neutral_reason(report, audit)

        report.prediction_target = resolve_prediction_target(
            report.timeframe,
            report.direction,
            report.magnitude,
            report.confidence,
            {
                **pt.to_dict(),
                "expected_return_pct": report.expected_excess_return_pct,
                "expected_return_p10": report.expected_return_p10,
                "expected_return_p50": report.expected_return_p50,
                "expected_return_p90": report.expected_return_p90,
                "prob_up": report.prob_up,
                "prob_down": report.prob_down,
                "prob_neutral": report.prob_no_edge,
                "direction": report.direction.value,
            },
            target=report.target,
        )

    @staticmethod
    def _probabilities_from_direction(direction: str, confidence: float) -> dict[str, float]:
        conf = max(0.0, min(1.0, float(confidence or 0.0)))
        residual = 1.0 - conf
        if direction == "bullish":
            return {"bullish": conf, "bearish": residual * 0.35, "neutral": residual * 0.65}
        if direction == "bearish":
            return {"bearish": conf, "bullish": residual * 0.35, "neutral": residual * 0.65}
        return {"neutral": conf, "bullish": residual * 0.5, "bearish": residual * 0.5}

    @staticmethod
    def _decision_from_distribution(report: FinalReport, threshold: float) -> str:
        expected = float(report.expected_excess_return_pct or 0.0)
        edge = float(report.edge_score or 0.0)
        if report.direction == Direction.BULLISH and expected >= threshold and report.prob_up >= 0.52 and edge >= 0.35:
            return "long_bias"
        if report.direction == Direction.BEARISH and expected <= -threshold and report.prob_down >= 0.52 and edge >= 0.35:
            return "short_bias"
        if report.direction in {Direction.BULLISH, Direction.BEARISH} and edge >= 0.22:
            return "watchlist"
        if report.prob_no_edge >= 0.55 or edge < 0.18:
            return "observe"
        return "watchlist"

    def _infer_neutral_reason(self, report: FinalReport, audit: Optional[dict] = None) -> str:
        if any(r.status in {"failed", "degraded"} or r.data_quality_score < 0.45 for r in report.agent_results):
            return "data_insufficient"
        if audit and audit.get("direction_scores"):
            scores = audit.get("direction_scores") or {}
            bullish = float(scores.get("bullish", 0.0))
            bearish = float(scores.get("bearish", 0.0))
            if bullish > 0 and bearish > 0 and abs(bullish - bearish) <= 0.08:
                return "conflict"
        if report.disagreements:
            return "conflict"
        text = " ".join(
            [report.summary or ""]
            + list(report.key_risks or [])
            + [r.reasoning or "" for r in report.agent_results]
            + [item for r in report.agent_results for item in (r.key_factors or [])]
        )
        if re.search(r"定价|消化|已反映|price[sd]?[-_ ]?in|高估|估值.*高", text, re.IGNORECASE):
            return "priced_in"
        return "no_edge"

    @staticmethod
    def _extract_json(text: str) -> str:
        """从 LLM 返回中提取 JSON，并修复常见格式问题"""
        json_str = extract_json_text(text)
        repaired, _ = repair_json_text(json_str)
        return repaired

    # ================================================================
    # 🆕 Round1: Agent 质量评分
    # ================================================================

    @staticmethod
    def _safe_float(value, default=None):
        try:
            if value in (None, "", "N/A"):
                return default
            if isinstance(value, str):
                stripped = value.strip()
                if stripped.endswith("%"):
                    return float(stripped[:-1]) / 100
                value = stripped
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _format_ratio(value) -> str:
        value = Aggregator._safe_float(value, None)
        if value is None:
            return "N/A"
        return f"{value:.0%}" if abs(value) <= 1 else f"{value:.0f}"

    @staticmethod
    def _extract_structured_evidence(result: AnalysisResult) -> dict:
        """提取任一 Agent 的结构化证据包，供汇总层统一消费。"""
        summary = result.data_summary or {}
        if isinstance(summary, str):
            try:
                summary = json.loads(summary)
            except json.JSONDecodeError:
                summary = {}
        if not isinstance(summary, dict):
            summary = {}

        packet = summary.get("evidence", {})
        if not isinstance(packet, dict):
            packet = {}

        matrix = packet.get("decision_matrix") or summary.get("decision_matrix") or {}
        confidence = (
            packet.get("confidence_constraints")
            or packet.get("confidence_model")
            or summary.get("confidence_constraints")
            or {}
        )
        evidence_lists = packet.get("evidence") or {}

        if not (matrix or confidence or evidence_lists):
            return {}

        data_quality = (
            packet.get("data_quality")
            or packet.get("source_quality")
            or summary.get("data_quality")
            or {}
        )
        valuation = packet.get("valuation_analysis") or summary.get("valuation_analysis") or {}
        trap = packet.get("value_trap_analysis") or summary.get("value_trap_analysis") or {}
        scorecard = packet.get("quality_scorecard") or summary.get("quality_scorecard") or {}

        quality_score = Aggregator._safe_float(
            data_quality.get("overall_quality"),
            Aggregator._safe_float(
                data_quality.get("overall"),
                Aggregator._safe_float(
                    data_quality.get("overall_freshness"),
                    Aggregator._safe_float(
                        data_quality.get("quality_score"),
                        Aggregator._safe_float(
                            data_quality.get("score"),
                            Aggregator._safe_float(summary.get("quality"), None),
                        ),
                    ),
                ),
            ),
        )

        return {
            "agent": result.agent_name,
            "domain": STRUCTURED_EVIDENCE_DOMAINS.get(result.agent_name, "通用"),
            "status": result.status,
            "actual_direction": result.direction.value,
            "matrix_position": matrix.get("matrix_position"),
            "suggested_direction": matrix.get("suggested_direction"),
            "matrix_reason": matrix.get("reason"),
            "quality_score": quality_score,
            "scorecard_total": Aggregator._safe_float(scorecard.get("total"), None),
            "scorecard_rating": scorecard.get("rating"),
            "pe_percentile": Aggregator._safe_float(
                valuation.get("pe_percentile_3yr"), None,
            ),
            "is_value_trap": (
                bool(trap.get("is_trap")) if trap or result.agent_name == FUNDAMENTAL_AGENT_NAME else None
            ),
            "value_trap_signals": trap.get("signals", []) or [],
            "max_confidence": Aggregator._safe_float(
                confidence.get("max_confidence"),
                Aggregator._safe_float(confidence.get("ceiling"), None),
            ),
            "hard_caps": confidence.get("hard_caps", []) or [],
            "bullish_evidence": evidence_lists.get("bullish", []) or [],
            "bearish_evidence": evidence_lists.get("bearish", []) or [],
            "neutral_evidence": evidence_lists.get("neutral", []) or [],
            "consistency_issues": summary.get("consistency_issues", []) or [],
            "event_impact_matrix": packet.get("event_impact_matrix", {}) or {},
            "transmission_matrix": packet.get("transmission_matrix", {}) or {},
        }

    @staticmethod
    def _extract_fundamental_evidence(result: AnalysisResult) -> dict:
        """从公司前景分析师结果中提取给汇总层使用的结构化证据。"""
        if result.agent_name != FUNDAMENTAL_AGENT_NAME:
            return {}
        return Aggregator._extract_structured_evidence(result)

    def _score_fundamental_quality(
        self, result: AnalysisResult, evidence: dict
    ) -> dict:
        """基于结构化基本面证据评估公司前景分析师质量。"""
        quality_score = evidence.get("quality_score")
        if quality_score is None:
            data_quality = "partial"
        elif quality_score >= 0.75:
            data_quality = "good"
        elif quality_score >= 0.50:
            data_quality = "normal"
        elif quality_score >= 0.30:
            data_quality = "partial"
        else:
            data_quality = "poor"

        source_type = "structured_fundamental"
        if result.status == "degraded":
            source_type = "degraded"

        issues = []
        if result.status == "degraded":
            issues.append("Agent 降级")

        suggested = evidence.get("suggested_direction")
        if suggested and suggested != result.direction.value:
            issues.append(f"矩阵建议{suggested}但输出{result.direction.value}")

        if evidence.get("is_value_trap"):
            issues.append("价值陷阱信号")

        issues.extend(evidence.get("consistency_issues", [])[:2])
        issues.extend(evidence.get("hard_caps", [])[:2])

        max_confidence = evidence.get("max_confidence")
        if max_confidence is not None and max_confidence <= 0.40:
            reliability = "low"
        elif (
            result.status == "degraded"
            or data_quality == "partial"
            or max_confidence is not None and max_confidence <= 0.60
        ):
            reliability = "medium"
        elif data_quality == "poor":
            reliability = "low"
        else:
            reliability = "high"

        weight_factor = 1.0
        if suggested and suggested != result.direction.value:
            weight_factor = min(weight_factor, 0.55)
        if max_confidence is not None and max_confidence <= 0.40:
            weight_factor = min(weight_factor, 0.60)
        if evidence.get("is_value_trap") and result.direction == Direction.BULLISH:
            weight_factor = min(weight_factor, 0.50)

        return {
            "data_quality": data_quality,
            "source_type": source_type,
            "reliability": reliability,
            "issues": issues,
            "na_count": 0,
            "fundamental_evidence": evidence,
            "structured_evidence": evidence,
            "weight_factor": weight_factor,
        }

    def _score_structured_evidence_quality(
        self, result: AnalysisResult, evidence: dict
    ) -> dict:
        """基于通用结构化证据评估非基本面 Agent 质量。"""
        quality_score = evidence.get("quality_score")
        if quality_score is None:
            data_quality = "normal"
        elif quality_score >= 0.75:
            data_quality = "good"
        elif quality_score >= 0.50:
            data_quality = "normal"
        elif quality_score >= 0.30:
            data_quality = "partial"
        else:
            data_quality = "poor"

        source_type = f"structured_{evidence.get('domain', 'generic')}"
        if result.status == "degraded":
            source_type = "degraded"

        issues = []
        if result.status == "degraded":
            issues.append("Agent 降级")

        suggested = evidence.get("suggested_direction")
        if suggested and suggested != result.direction.value:
            issues.append(f"矩阵建议{suggested}但输出{result.direction.value}")

        issues.extend(evidence.get("consistency_issues", [])[:2])
        issues.extend(evidence.get("hard_caps", [])[:2])

        max_confidence = evidence.get("max_confidence")
        if max_confidence is not None and max_confidence <= 0.40:
            reliability = "low"
        elif (
            result.status == "degraded"
            or data_quality == "partial"
            or max_confidence is not None and max_confidence <= 0.60
        ):
            reliability = "medium"
        elif data_quality == "poor":
            reliability = "low"
        else:
            reliability = "high"

        weight_factor = 1.0
        if suggested and suggested != result.direction.value:
            weight_factor = min(weight_factor, 0.55)
        if max_confidence is not None and max_confidence <= 0.40:
            weight_factor = min(weight_factor, 0.60)
        if evidence.get("hard_caps"):
            weight_factor = min(weight_factor, 0.85)

        return {
            "data_quality": data_quality,
            "source_type": source_type,
            "reliability": reliability,
            "issues": issues,
            "na_count": 0,
            "structured_evidence": evidence,
            "weight_factor": weight_factor,
        }

    def _score_quality(self, result: AnalysisResult) -> dict:
        """评估单个 Agent 的分析质量"""
        reasoning = result.reasoning or ""
        status = getattr(result, "status", "ok")
        if status == "failed":
            return {
                "data_quality": "poor",
                "source_type": "failed",
                "reliability": "low",
                "issues": [getattr(result, "error_message", None) or "Agent 执行失败"],
                "na_count": 99,
            }

        fundamental_evidence = self._extract_fundamental_evidence(result)
        if fundamental_evidence:
            return self._score_fundamental_quality(result, fundamental_evidence)

        structured_evidence = self._extract_structured_evidence(result)
        if structured_evidence:
            return self._score_structured_evidence_quality(result, structured_evidence)

        # 数据质量: 统计"N/A"、"缺失"、"不可用"
        na_count = reasoning.count("N/A") + reasoning.count("缺失") + reasoning.count("不可用")
        if na_count > 5:       data_quality = "poor"
        elif na_count > 2:     data_quality = "partial"
        elif na_count > 0:     data_quality = "normal"
        else:                  data_quality = "good"

        # 数据源类型
        source_type = "realtime"
        if status == "degraded":
            source_type = "degraded"
        if "知识库" in reasoning:
            source_type = "knowledge_base"
        elif "参考值" in reasoning:
            source_type = "reference"

        # 可信度
        if data_quality == "poor" or result.confidence < 0.15:
            reliability = "low"
        elif data_quality == "partial" or source_type == "knowledge_base":
            reliability = "medium"
        else:
            reliability = "high"

        issues = []
        if status == "degraded": issues.append("Agent 降级")
        if na_count > 0:    issues.append(f"数据缺口({na_count}处)")
        if source_type == "knowledge_base": issues.append("知识库非实时")
        if result.confidence < 0.25: issues.append("置信度极低")

        return {
            "data_quality": data_quality, "source_type": source_type,
            "reliability": reliability, "issues": issues, "na_count": na_count,
        }

    # ================================================================
    # 🆕 Round1: 动态权重调整
    # ================================================================

    def _adjust_weights(self, original: WeightConfig, quality_scores: dict) -> WeightConfig:
        """根据 Agent 质量动态调整权重"""
        new_weights = dict(original.agent_weights)

        for name, w in list(new_weights.items()):
            q = quality_scores.get(name, {})
            quality = q.get("data_quality", "normal")
            source = q.get("source_type", "realtime")
            factor = 1.0

            if quality == "poor":       factor *= 0.3
            elif quality == "partial":  factor *= 0.6
            if source == "failed": factor *= 0.0
            if source == "degraded": factor *= 0.4
            if source == "knowledge_base": factor *= 0.5
            factor *= float(q.get("weight_factor", 1.0) or 1.0)

            if factor < 0.95:
                logger.info(f"[权重] {name}: {w:.0%}→{w*factor:.0%} (quality={quality})")
                new_weights[name] = round(w * factor, 3)

        # 归一化
        total = sum(new_weights.values())
        if total > 0:
            target = 1.0 - original.synthesis_weight
            for name in new_weights:
                new_weights[name] = round(new_weights[name] / total * target, 3)

        return WeightConfig(agent_weights=new_weights, synthesis_weight=original.synthesis_weight)

    # ================================================================
    # 🆕 Round2: 分歧量化
    # ================================================================

    def _calculate_disagreement(self, results: list[AnalysisResult]) -> dict:
        """量化 Agent 之间的分歧程度"""
        if len(results) <= 1:
            return {"level": "none", "note": "单Agent无分歧"}

        # 方向分布
        dirs = [r.direction.value for r in results]
        dir_counts = {"bullish": dirs.count("bullish"), "bearish": dirs.count("bearish"), "neutral": dirs.count("neutral")}

        # 方向熵（0=完全一致, 1=完全均匀分布）
        import math
        total = len(results)
        entropy = 0
        for count in dir_counts.values():
            if count > 0:
                p = count / total
                entropy -= p * math.log(p, 3)  # log base 3, max = 1
        entropy = round(entropy, 2)

        # 分歧等级
        unique_dirs = sum(1 for v in dir_counts.values() if v > 0)
        if unique_dirs == 1:
            level = "none"
        elif unique_dirs == 2 and entropy < 0.5:
            level = "low"
        elif unique_dirs == 2:
            level = "medium"
        else:
            level = "high" if entropy < 0.7 else "severe"

        # 找异常值（与多数人方向不同的 Agent）
        majority_dir = max(dir_counts, key=dir_counts.get)
        outliers = [r.agent_name for r in results if r.direction.value != majority_dir]

        # 幅度离散度
        mags = []
        for r in results:
            if r.magnitude:
                mags.append((r.magnitude.min_pct, r.magnitude.max_pct))
        mag_spread = 0
        if len(mags) >= 2:
            all_mins = [m[0] for m in mags]
            all_maxs = [m[1] for m in mags]
            mag_spread = round(max(all_maxs) - min(all_mins), 1)

        return {
            "level": level,
            "entropy": entropy,
            "direction_counts": dir_counts,
            "outliers": outliers,
            "majority": majority_dir,
            "magnitude_spread": mag_spread,
            "unique_directions": unique_dirs,
        }

    # ================================================================
    # 🆕 Round2: Agent 贡献度
    # ================================================================

    def _calculate_contributions(
        self, results: list[AnalysisResult],
        original_weights: dict, adjusted_weights: dict, quality_scores: dict,
    ) -> list[dict]:
        """计算每个 Agent 对最终判断的贡献"""
        contribs = []
        for r in results:
            name = r.agent_name
            static_w = original_weights.get(name, 0)
            adjusted_w = adjusted_weights.get(name, static_w)
            q = quality_scores.get(name, {})

            # 贡献描述
            if adjusted_w > 0.25:
                impact = "主导"
            elif adjusted_w > 0.12:
                impact = "重要"
            elif adjusted_w > 0.05:
                impact = "参考"
            else:
                impact = "微弱"

            contribs.append({
                "agent": name,
                "static_weight": static_w,
                "adjusted_weight": adjusted_w,
                "quality": q.get("data_quality", "?"),
                "impact": impact,
                "direction": r.direction.value,
                "confidence": r.confidence,
            })

        return sorted(contribs, key=lambda c: -c["adjusted_weight"])
