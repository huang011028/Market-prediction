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
from src.core.result import (
    AnalysisResult, FinalReport, Direction, Magnitude,
)
from src.prompts.aggregator_prompts import AGGREGATOR_SYSTEM_PROMPT
from config.weight_manager import WeightConfig

logger = logging.getLogger(__name__)


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
                system_prompt=AGGREGATOR_SYSTEM_PROMPT,
                user_prompt=context,
            )
        except Exception as e:
            logger.error(f"[汇总分析师] LLM 失败: {e}")
            return self._fallback_report(target, timeframe, agent_results, failed, str(e))

        return self._parse_response(
            response.content, target, timeframe, agent_results, failed,
        )

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
        parts.append("")
        parts.append("请严格按 JSON 格式输出。")

        return "\n".join(parts)

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
        json_str = self._extract_json(content)

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            return FinalReport(
                target=target, timeframe=timeframe,
                direction=Direction.NEUTRAL, confidence=0.0,
                agent_results=agent_results,
                summary=content,
                key_risks=["LLM 返回格式异常"],
            )

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

        return FinalReport(
            target=target,
            timeframe=timeframe,
            direction=direction,
            magnitude=magnitude,
            confidence=confidence,
            agent_results=agent_results,
            summary=str(data.get("summary", content[:500])),
            key_risks=data.get("key_risks", []),
            disagreements=data.get("disagreements", []),
        )

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

        return FinalReport(
            target=target, timeframe=timeframe,
            direction=direction, confidence=0.3,
            agent_results=agent_results,
            summary=f"⚠️ 综合分析 LLM 调用失败: {error}\n\n以下为基于规则简单汇总的结果。",
            key_risks=list(set(all_risks))[:5],
            disagreements=["综合分析未能执行"],
        )

    @staticmethod
    def _extract_json(text: str) -> str:
        """从 LLM 返回中提取 JSON，并修复常见格式问题"""
        m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if m:
            json_str = m.group(1).strip()
        else:
            s = text.find("{")
            e = text.rfind("}")
            if s != -1 and e != -1 and e > s:
                json_str = text[s:e+1]
            else:
                return text

        # 修复 LLM 常见 JSON 格式错误
        # 1. 移除数字前的 + 号（如 +1.5 → 1.5）
        json_str = re.sub(r':\s*\+(\d+\.?\d*)', r': \1', json_str)
        # 2. 移除尾部逗号（如 "key": value, } → "key": value }）
        json_str = re.sub(r',\s*}', '}', json_str)
        json_str = re.sub(r',\s*]', ']', json_str)

        return json_str

    # ================================================================
    # 🆕 Round1: Agent 质量评分
    # ================================================================

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
