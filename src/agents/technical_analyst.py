"""
近期股价分析师（Round2 升级版）

改进：
- Round1: 结构化信号摘要 + 6步框架 + 新指标
- Round2: 置信度/幅度校准 + 标的个性化参数
"""

import logging
import json
from src.core.base_agent import BaseAgent
from src.core.llm_client import LLMClient
from src.core.result import AnalysisResult, Direction, Magnitude
from src.core.confidence_calibrator import ConfidenceCalibrator
from src.data.price_fetcher import PriceFetcher
from src.data.stock_profiles import get_stock_profile, build_profile_context
from src.prompts.dynamic_overrides import build_prompt_with_overrides
from src.prompts.technical_prompts import TECHNICAL_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class TechnicalAnalyst(BaseAgent):
    """近期股价分析师（Round2）

    基于 6 步分析框架 + 置信度校准 + 标的个性化
    """

    def __init__(self, llm: LLMClient):
        super().__init__(
            name="近期股价分析师",
            description="基于K线形态、均线系统、MACD、RSI等技术指标分析短期走势",
            llm=llm,
        )
        self.price_fetcher = PriceFetcher()
        self.calibrator = ConfidenceCalibrator()

    async def gather_data(self, target: str, timeframe: str) -> dict:
        period = self._timeframe_to_period(timeframe)
        try:
            price_data = await self.price_fetcher.fetch(target, period)
            return price_data.to_agent_dict()
        except Exception as e:
            logger.error(f"股价数据获取失败: {e}")
            raise

    def _timeframe_to_period(self, timeframe: str) -> str:
        t = timeframe.lower()
        if "长期" in t or "季度" in t: return "1y"
        elif "中期" in t or "月" in t: return "6mo"
        return "3mo"

    def _get_system_prompt(self) -> str:
        return build_prompt_with_overrides(TECHNICAL_SYSTEM_PROMPT, self.name)

    # ================================================================
    # 🆕 Round1: 结构化信号摘要 + 更好的数据呈现
    # ================================================================

    def _build_user_prompt(self, data: dict, context: dict) -> str:
        """构建结构化分析请求，包含信号摘要和 ATR 引导"""
        parts = []

        # 标的信息
        parts.append(f"## 分析任务")
        parts.append(f"- 标的: {context.get('target', 'N/A')}")
        parts.append(f"- 周期: {context.get('timeframe', 'N/A')}")
        parts.append(f"- 数据范围: {data.get('data_period', 'N/A')} ({data.get('trading_days', '?')}个交易日)")
        if context.get("prediction_target"):
            parts.append("- 预测目标规格:")
            parts.append("```json")
            parts.append(json.dumps(context.get("prediction_target"), ensure_ascii=False, indent=2))
            parts.append("```")
        parts.append("")

        # 价格概览
        ps = data.get("price_summary", {})
        parts.append("## 价格概览")
        parts.append(f"- 最新价: {ps.get('latest_close', '?')}")
        parts.append(f"- 5日涨跌: {ps.get('change_5d_pct', '?')}%")
        parts.append(f"- 20日涨跌: {ps.get('change_20d_pct', '?')}%")
        parts.append(f"- 20日最高: {ps.get('period_20d_high', '?')}")
        parts.append(f"- 20日最低: {ps.get('period_20d_low', '?')}")
        parts.append(f"- 最近10日收盘: {data.get('recent_closes', [])}")
        parts.append("")

        snapshot = data.get("technical_snapshot", {}) or {}
        if snapshot:
            evidence_payload = self._build_evidence_packet(
                data,
                context.get("timeframe", ""),
            )
            parts.append("## 技术证据包（代码计算，不依赖 LLM）")
            parts.append("```json")
            parts.append(json.dumps(evidence_payload, ensure_ascii=False, indent=2))
            parts.append("```")
            parts.append("")

        # 技术指标表格
        ind = data.get("indicators", {})
        parts.append("## 技术指标一览")
        parts.append("")
        parts.append("| 类别 | 指标 | 数值 | 信号解读 |")
        parts.append("|------|------|------|---------|")
        self._add_indicator_row(parts, "趋势", "MA5/MA10/MA20/MA60",
            f"{ind.get('MA5','?')}/{ind.get('MA10','?')}/{ind.get('MA20','?')}/{ind.get('MA60','?')}",
            self._ma_signal(ind))
        self._add_indicator_row(parts, "趋势强度", "ADX(14)",
            str(ind.get('ADX', '?')),
            self._adx_signal(ind))
        self._add_indicator_row(parts, "动能", "MACD",
            f"DIF={ind.get('MACD_DIF','?')} DEA={ind.get('MACD_DEA','?')}",
            self._macd_signal(ind))
        self._add_indicator_row(parts, "动能", "RSI(14)",
            str(ind.get('RSI', '?')),
            self._rsi_signal(ind))
        self._add_indicator_row(parts, "动能", "KDJ(9,3,3)",
            f"K={ind.get('KDJ_K','?')} D={ind.get('KDJ_D','?')} J={ind.get('KDJ_J','?')}",
            self._kdj_signal(ind))
        self._add_indicator_row(parts, "波动", "ATR(14)",
            f"{ind.get('ATR','?')} ({ind.get('ATR_pct','?')}%)",
            f"趋势: {ind.get('ATR_trend','?')}")
        self._add_indicator_row(parts, "成交量", "VOL_ratio",
            str(ind.get('VOL_ratio', '?')),
            ">1放量,<1缩量")
        self._add_indicator_row(parts, "成交量", "OBV背离",
            ind.get('OBV_divergence', '?'),
            self._obv_signal(ind))
        self._add_indicator_row(parts, "布林带", "BOLL(20,2)",
            f"上{ind.get('BOLL_upper','?')}/中{ind.get('BOLL_mid','?')}/下{ind.get('BOLL_lower','?')}",
            "")
        parts.append("")

        # K线形态
        patterns = data.get("patterns", {})
        candle = patterns.get("candlestick", [])
        if candle:
            parts.append(f"## K线形态: {', '.join(candle)}")
        parts.append(f"- 均线排列: {patterns.get('ma_arrangement', '?')}")
        parts.append(f"- 价格vs均线: {patterns.get('price_vs_ma', '?')}")
        parts.append(f"- RSI区间: {patterns.get('rsi_zone', '?')}")
        parts.append(f"- 布林带位置: {patterns.get('boll_position', '?')}")
        macd_event = patterns.get('macd_event', '')
        if macd_event:
            parts.append(f"- {macd_event}")
        parts.append("")

        # 周线背景
        weekly = patterns.get("weekly_context")
        if weekly:
            parts.append("## 周线背景（中期趋势参考）")
            parts.append(f"- 最近6周收盘: {weekly.get('weekly_closes', [])}")
            parts.append(f"- 周线MA5: {weekly.get('weekly_ma5','?')} | MA10: {weekly.get('weekly_ma10','?')}")
            parts.append(f"- 周线趋势: **{weekly.get('weekly_trend','?')}**")
            parts.append(f"- 本周涨跌: {weekly.get('weekly_change_pct','?')}%")
            parts.append(f"- 价格相对周线MA5: {weekly.get('price_vs_weekly_ma5','?')}")
            parts.append("")

        # ATR 引导
        atr_pct = ind.get("ATR_pct", 1.0)
        if isinstance(atr_pct, (int, float)) and atr_pct > 0:
            weekly_atr = round(atr_pct * 2.24, 1)  # √5 ≈ 2.236
            parts.append(f"## 💡 ATR 波动参考")
            parts.append(f"日波动参考: ±{atr_pct}% | 周波动参考: ±{weekly_atr}%")
            parts.append(f"(ADX={ind.get('ADX','?')}，趋势={'强' if float(ind.get('ADX',0) or 0)>25 else '弱'})")
            parts.append("")

        # 分析指引
        parts.append("## 你的分析任务")
        parts.append("请严格按 6 步框架分析，输出 JSON。")
        parts.append("Step1 趋势定级 → Step2 动能确认 → Step3 量价验证 → Step4 多周期确认 → Step5 关键价位 → Step6 综合")
        parts.append("")
        parts.append("硬性约束:")
        parts.append("- 不得编造未提供的价格、成交量、指标或新闻。")
        parts.append("- 若趋势、动量、量能互相冲突，必须降低置信度，必要时输出 neutral。")
        parts.append("- reasoning 第一段必须说明主导技术证据。")
        parts.append("- risks 必须至少包含一个技术判断失效条件。")
        parts.append("- 如果代码计算的 confidence_model 存在 hard_caps，不得给出高于 hard_caps 约束的强判断。")
        parts.append("- 建议输出 prediction_target.expected_return_pct 与 P(涨/跌/中性)，方向需由收益目标派生。")

        # 🆕 Round2: 标的信息
        target = context.get("target", "")
        profile_text = build_profile_context(target)
        parts.append(profile_text)

        return "\n".join(parts)

    # ================================================================
    # 信号解读辅助
    # ================================================================

    def _add_indicator_row(self, parts, category, name, value, signal):
        parts.append(f"| {category} | {name} | {value} | {signal} |")

    def _ma_signal(self, ind):
        ma5 = ind.get("MA5"); ma10 = ind.get("MA10")
        ma20 = ind.get("MA20"); ma60 = ind.get("MA60")
        if all(v is not None for v in [ma5, ma10, ma20, ma60]):
            if ma5 > ma10 > ma20 > ma60: return "📈 多头排列"
            if ma5 < ma10 < ma20 < ma60: return "📉 空头排列"
            return "缠绕/混合"
        return "数据不足"

    def _adx_signal(self, ind):
        adx = ind.get("ADX"); direction = ind.get("ADX_direction"); trend = ind.get("ADX_trend")
        if adx is None: return "N/A"
        dir_emoji = "📈" if direction == "bullish" else "📉"
        return f"{dir_emoji} ADX={adx}, {trend}"

    def _macd_signal(self, ind):
        sig = ind.get("MACD_signal")
        if sig == "golden_cross": return "🟢 金叉(看涨)"
        if sig == "death_cross": return "🔴 死叉(看跌)"
        if sig == "bullish_holding": return "🟢 多头持仓"
        if sig == "bearish_holding": return "🔴 空头持仓"
        return str(sig)

    def _rsi_signal(self, ind):
        rsi = ind.get("RSI")
        if rsi is None: return "N/A"
        rsi = float(rsi) if not isinstance(rsi, (int, float)) else rsi
        if rsi > 80: return f"🔴 极端超买({rsi})"
        if rsi > 70: return f"🟡 超买({rsi})"
        if rsi > 50: return f"🟢 偏强({rsi})"
        if rsi > 30: return f"🟡 偏弱({rsi})"
        if rsi > 20: return f"🟢 超卖({rsi})"
        return f"🟢 极端超卖({rsi})"

    def _kdj_signal(self, ind):
        sig = ind.get("KDJ_signal"); zone = ind.get("KDJ_zone")
        parts = []
        if sig == "golden_cross": parts.append("🟢 金叉")
        elif sig == "death_cross": parts.append("🔴 死叉")
        if zone: parts.append(zone)
        return " ".join(parts) if parts else "—"

    def _obv_signal(self, ind):
        div = ind.get("OBV_divergence", "none")
        if div == "bullish_divergence": return "🟢 底背离(看涨)"
        if div == "bearish_divergence": return "🔴 顶背离(看跌)"
        return "无背离"

    # ================================================================
    # 🆕 Round2: 覆盖 analyze 加入校准
    # ================================================================

    async def analyze(self, data: dict, context: dict) -> AnalysisResult:
        """分析 + 校准"""
        technical_buckets = None
        dq = data.get("data_quality", {}) or {}
        if dq.get("status") == "failed" or float(dq.get("score", 1.0) or 0) < 0.4:
            reason = "技术面数据不足，不能形成有效技术判断。"
            if dq.get("issues"):
                reason += " 数据问题: " + "；".join(dq.get("issues", []))
            return AnalysisResult(
                agent_name=self.name,
                target=context.get("target", ""),
                timeframe=context.get("timeframe", ""),
                direction=Direction.NEUTRAL,
                magnitude=Magnitude(min_pct=-2.0, max_pct=2.0),
                confidence=0.15,
                reasoning=reason,
                key_factors=["K线样本或字段不足"],
                risks=["行情数据不足时技术面结论不可靠"],
                data_summary=self._build_data_summary(data),
                status="failed",
                error_message=reason,
                data_quality_score=float(dq.get("score", 0.0) or 0.0),
            )

        # 先用默认 LLM 分析
        result = await super().analyze(data, context)
        result.data_summary = self._build_data_summary(data)
        result.data_quality_score = float(dq.get("score", 1.0) or 1.0)
        if dq.get("status") == "degraded":
            result.status = "degraded"
            result.confidence = min(result.confidence, 0.35)
            if dq.get("issues"):
                result.risks.append("技术面数据质量降级: " + "；".join(dq.get("issues", [])))

        # 校准置信度
        try:
            ind = data.get("indicators", {})
            # 判断信号是否有矛盾
            contradiction = self._has_signal_contradiction(ind)
            # 判断数据质量
            data_quality = "normal"
            if dq.get("status") == "degraded" or data.get("trading_days", 0) < 60:
                data_quality = "partial"

            cal = self.calibrator.calibrate_confidence(
                raw_confidence=result.confidence,
                agent_name=self.name,
                timeframe=context.get("timeframe", "短期"),
                signal_contradiction=contradiction,
                data_quality=data_quality,
            )
            if cal["calibrated"] != result.confidence:
                old_conf = result.confidence
                result.confidence = cal["calibrated"]
                result.reasoning += (
                    f"\n\n[系统校准: 原始置信度{old_conf:.0%}→校准后{cal['calibrated']:.0%}"
                    f"{' (' + '; '.join(cal['adjustments']) + ')' if cal['adjustments'] else ''}]"
                )
        except Exception as e:
            logger.debug(f"置信度校准失败: {e}")

        # 用技术面专用历史桶再校准一次，让回测冷启动样本能直接影响实时预测。
        try:
            from src.utils.technical_calibrator import TechnicalConfidenceCalibrator

            evidence_packet = self._build_evidence_packet(
                data,
                context.get("timeframe", "短期"),
            )
            buckets = TechnicalConfidenceCalibrator.extract_buckets_from_evidence(
                evidence_packet,
                context.get("timeframe", "短期"),
            )
            technical_buckets = buckets
            dedicated_calibrator = TechnicalConfidenceCalibrator()
            dedicated_conf = dedicated_calibrator.calibrate(
                raw_confidence=result.confidence,
                **buckets,
            )
            if abs(dedicated_conf - result.confidence) >= 0.02:
                old_conf = result.confidence
                result.confidence = dedicated_conf
                result.reasoning += (
                    f"\n\n[技术面历史桶校准: {old_conf:.0%}→{dedicated_conf:.0%}]"
                )
        except Exception as e:
            logger.debug(f"技术面专用校准失败: {e}")

        # 校准幅度
        try:
            atr_pct = float(data.get("indicators", {}).get("ATR_pct", 2.0))
            adx = float(data.get("indicators", {}).get("ADX", 0) or 0)
            if result.magnitude:
                cal_mag = self.calibrator.calibrate_magnitude(
                    result.magnitude, atr_pct, adx, context.get("timeframe", "短期"),
                )
                if cal_mag["warning"]:
                    result.risks.append(f"[幅度校准] {cal_mag['warning']}")
        except Exception as e:
            logger.debug(f"幅度校准失败: {e}")

        technical_issues = self._apply_snapshot_constraints(result, data)
        if result.data_summary is None:
            result.data_summary = {}
        result.data_summary["consistency_issues"] = technical_issues
        self._apply_holdout_direction_policy(
            result,
            data,
            context,
            technical_buckets,
        )
        self._apply_holdout_confidence_policy(
            result,
            data,
            context,
            technical_buckets,
        )

        return result

    def _apply_holdout_direction_policy(
        self,
        result: AnalysisResult,
        data: dict,
        context: dict,
        technical_buckets: dict | None,
    ) -> None:
        """应用通过 holdout 验证的声明式技术方向规则。"""
        try:
            from src.utils.technical_calibrator import TechnicalConfidenceCalibrator

            buckets = technical_buckets
            if buckets is None:
                evidence_packet = self._build_evidence_packet(
                    data,
                    context.get("timeframe", "短期"),
                )
                buckets = TechnicalConfidenceCalibrator.extract_buckets_from_evidence(
                    evidence_packet,
                    context.get("timeframe", "短期"),
                )
            current_direction = getattr(result.direction, "value", result.direction)
            policy_direction, matched_rules = TechnicalConfidenceCalibrator.apply_direction_policy(
                current_direction,
                buckets,
            )
            if not matched_rules:
                return
            if policy_direction == current_direction:
                result.data_summary = result.data_summary or {}
                result.data_summary["technical_direction_policy"] = {
                    "matched_rules": matched_rules,
                    "applied": False,
                    "direction": current_direction,
                }
                return

            old_direction = current_direction
            result.direction = Direction(policy_direction)
            self._ensure_policy_magnitude(result, data, policy_direction)
            old_confidence = result.confidence
            result.confidence = min(0.60, max(float(result.confidence or 0.0), 0.50))
            matched_labels = [
                f"{rule.get('bucket_group')}/{rule.get('bucket')}->{rule.get('action')}"
                for rule in matched_rules
            ]
            result.reasoning += (
                "\n\n[技术面方向规则: "
                f"{old_direction}->{policy_direction}; "
                f"confidence {old_confidence:.0%}->{result.confidence:.0%}; "
                + "; ".join(matched_labels)
                + "]"
            )
            result.key_factors.append("命中 holdout 通过的技术场景方向规则")
            result.risks.append("历史场景规则只代表样本多数方向，仍需结合新信息复核。")
            result.data_summary = result.data_summary or {}
            result.data_summary["technical_direction_policy"] = {
                "matched_rules": matched_rules,
                "applied": True,
                "old_direction": old_direction,
                "new_direction": policy_direction,
                "old_confidence": round(old_confidence, 3),
                "new_confidence": round(result.confidence, 3),
            }
        except Exception as e:
            logger.debug(f"技术面 holdout 方向规则应用失败: {e}")

    def _apply_holdout_confidence_policy(
        self,
        result: AnalysisResult,
        data: dict,
        context: dict,
        technical_buckets: dict | None,
    ) -> None:
        """应用通过 holdout 验证的声明式技术置信度规则。"""
        try:
            from src.utils.technical_calibrator import TechnicalConfidenceCalibrator

            buckets = technical_buckets
            if buckets is None:
                evidence_packet = self._build_evidence_packet(
                    data,
                    context.get("timeframe", "短期"),
                )
                buckets = TechnicalConfidenceCalibrator.extract_buckets_from_evidence(
                    evidence_packet,
                    context.get("timeframe", "短期"),
                )
            old_confidence = float(result.confidence or 0.0)
            policy_confidence, matched_rules = (
                TechnicalConfidenceCalibrator.apply_confidence_policy(
                    old_confidence,
                    buckets,
                )
            )
            if not matched_rules:
                return
            result.data_summary = result.data_summary or {}
            if policy_confidence >= old_confidence:
                result.data_summary["technical_confidence_policy"] = {
                    "matched_rules": matched_rules,
                    "applied": False,
                    "confidence": round(old_confidence, 3),
                }
                return

            result.confidence = policy_confidence
            matched_labels = [
                f"{rule.get('bucket_group')}/{rule.get('bucket')}<= {rule.get('confidence_cap')}"
                for rule in matched_rules
            ]
            result.reasoning += (
                "\n\n[技术面置信度规则: "
                f"confidence {old_confidence:.0%}->{result.confidence:.0%}; "
                + "; ".join(matched_labels)
                + "]"
            )
            result.key_factors.append("命中 holdout 通过的技术置信度封顶规则")
            result.risks.append("历史低命中场景触发置信度封顶，方向判断需等待更多确认。")
            result.data_summary["technical_confidence_policy"] = {
                "matched_rules": matched_rules,
                "applied": True,
                "old_confidence": round(old_confidence, 3),
                "new_confidence": round(result.confidence, 3),
            }
        except Exception as e:
            logger.debug(f"技术面 holdout 置信度规则应用失败: {e}")

    def _ensure_policy_magnitude(
        self,
        result: AnalysisResult,
        data: dict,
        direction: str,
    ) -> None:
        atr_pct = self._safe_float(data.get("indicators", {}).get("ATR_pct"), 2.0)
        max_move = round(max(1.0, min(6.0, atr_pct * 2.0)), 2)
        min_move = round(max(0.5, min(max_move, atr_pct * 0.5)), 2)
        if direction == "bullish":
            if result.magnitude is None or result.magnitude.max_pct <= 0:
                result.magnitude = Magnitude(min_pct=min_move, max_pct=max_move)
        elif direction == "bearish":
            if result.magnitude is None or result.magnitude.min_pct >= 0:
                result.magnitude = Magnitude(min_pct=-max_move, max_pct=-min_move)

    def _build_data_summary(self, data: dict) -> dict:
        snapshot = data.get("technical_snapshot", {}) or {}
        evidence_packet = self._build_evidence_packet(data)
        return {
            "symbol": data.get("symbol"),
            "name": data.get("name"),
            "market": data.get("market"),
            "data_period": data.get("data_period"),
            "trading_days": data.get("trading_days", 0),
            "latest_date": data.get("latest_date"),
            "source": "PriceFetcher",
            "freshness": (data.get("freshness") or {}).get("note") or data.get("latest_date") or "未提供",
            "quality": data.get("data_quality", {}).get("status", "unknown"),
            "data_quality": data.get("data_quality", {}),
            "freshness_detail": data.get("freshness", {}),
            "intraday_trend": data.get("intraday_trend", []),
            "intraday_meta": data.get("intraday_meta", {}),
            "intraday_signals": data.get("intraday_signals", {}),
            "price_summary": data.get("price_summary", {}),
            "recent_closes": data.get("recent_closes", []),
            "recent_trend": data.get("recent_trend", []),
            "indicators": data.get("indicators", {}),
            "technical_snapshot": snapshot,
            "trend_regime": snapshot.get("trend_regime", {}),
            "volume_signals": snapshot.get("volume_signals", {}),
            "support_resistance": snapshot.get("support_resistance", {}),
            "risk_levels": snapshot.get("risk_levels", {}),
            "confidence_model": snapshot.get("confidence_model", {}),
            "legacy_evidence": snapshot.get("evidence", {}),
            "evidence": evidence_packet,
        }

    @staticmethod
    def _safe_float(value, default: float = 0.0) -> float:
        try:
            if value in (None, "", "N/A"):
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    def _build_evidence_packet(self, data: dict, timeframe: str = "") -> dict:
        """把 technical_snapshot 包装为统一结构化技术证据包。"""
        snapshot = data.get("technical_snapshot", {}) or {}
        confidence_model = snapshot.get("confidence_model", {}) or {}
        decision_matrix = self._derive_technical_matrix(data, timeframe)
        max_confidence = self._safe_float(
            confidence_model.get("technical_confidence"), 0.0,
        )
        return {
            "identity": {
                "symbol": data.get("symbol"),
                "name": data.get("name"),
                "market": data.get("market"),
                "data_source": "PriceFetcher",
            },
            "price_window": {
                "data_period": data.get("data_period"),
                "latest_date": data.get("latest_date") or snapshot.get("latest_date"),
                "trading_days": data.get("trading_days", snapshot.get("trading_days", 0)),
                "timeframe": timeframe,
            },
            "price_summary": data.get("price_summary", {}),
            "close_series_summary": snapshot.get("close_series_summary", {}),
            "data_quality": data.get("data_quality") or snapshot.get("data_quality", {}),
            "trend_regime": snapshot.get("trend_regime", {}),
            "momentum_signals": snapshot.get("momentum_signals", {}),
            "volume_signals": snapshot.get("volume_signals", {}),
            "volatility_signals": snapshot.get("volatility_signals", {}),
            "support_resistance": snapshot.get("support_resistance", {}),
            "risk_levels": snapshot.get("risk_levels", {}),
            "intraday_signals": data.get("intraday_signals", {}),
            "recent_trend": data.get("recent_trend", snapshot.get("recent_trend", [])),
            "decision_matrix": decision_matrix,
            "confidence_constraints": {
                "max_confidence": round(max_confidence, 3),
                "technical_confidence": round(max_confidence, 3),
                "signal_consistency_score": confidence_model.get("signal_consistency_score"),
                "risk_reward_score": confidence_model.get("risk_reward_score"),
                "backtest_prior_score": confidence_model.get("backtest_prior_score"),
                "hard_caps": confidence_model.get("hard_caps", []) or [],
            },
            "evidence": snapshot.get("evidence", {}),
        }

    def _derive_technical_matrix(self, data: dict, timeframe: str = "") -> dict:
        """生成技术趋势-动量-量能-风险收益矩阵，供 Aggregator 消费。"""
        snapshot = data.get("technical_snapshot", {}) or {}
        confidence_model = snapshot.get("confidence_model", {}) or {}
        trend = snapshot.get("trend_regime", {}) or {}
        momentum = snapshot.get("momentum_signals", {}) or {}
        volume = snapshot.get("volume_signals", {}) or {}
        risk = snapshot.get("risk_levels", {}) or {}
        support_resistance = snapshot.get("support_resistance", {}) or {}
        intraday = data.get("intraday_signals", {}) or {}

        short_trend = trend.get("short_term", "unknown")
        medium_trend = trend.get("medium_term", "unknown")
        momentum_state = momentum.get("state", "mixed")
        volume_state = "neutral"
        if volume.get("price_up_volume_up"):
            volume_state = "confirm_up"
        elif volume.get("price_down_volume_up"):
            volume_state = "confirm_down"
        elif volume.get("volume_trend") == "shrinking":
            volume_state = "shrinking"

        risk_reward = self._safe_float(risk.get("risk_reward_ratio"), None)
        if risk_reward is None:
            risk_bucket = "unknown"
            risk_label = "风险收益未知"
        elif risk_reward >= 1.5:
            risk_bucket = "favorable"
            risk_label = "风险收益占优"
        elif risk_reward >= 1.0:
            risk_bucket = "balanced"
            risk_label = "风险收益均衡"
        elif risk_reward >= 0.7:
            risk_bucket = "weak"
            risk_label = "风险收益偏弱"
        else:
            risk_bucket = "unfavorable"
            risk_label = "风险收益不利"

        intraday_state = intraday.get("state") if intraday.get("available") else "unavailable"
        suggested_direction = confidence_model.get("suggested_direction", "neutral")
        matrix_reason = "技术证据方向不明确，默认中性。"
        if suggested_direction == "bullish":
            matrix_reason = "趋势、动量或量能证据偏多。"
        elif suggested_direction == "bearish":
            matrix_reason = "趋势、动量或量能证据偏空。"

        if intraday_state == "selloff" and suggested_direction == "bullish":
            suggested_direction = "neutral"
            matrix_reason = "日线偏多但盘中明显走弱，短线追多需降级。"
        elif intraday_state == "strong_up" and suggested_direction == "bearish":
            suggested_direction = "neutral"
            matrix_reason = "日线偏空但盘中明显走强，短线追空需降级。"

        if (
            support_resistance.get("resistance_distance_pct") is not None
            and 0 <= self._safe_float(support_resistance.get("resistance_distance_pct"), 99) <= 2
            and suggested_direction == "bullish"
            and not volume.get("price_up_volume_up")
        ):
            suggested_direction = "neutral"
            matrix_reason = "接近压力位且未放量突破，看涨结论需要等待确认。"

        if (
            support_resistance.get("support_distance_pct") is not None
            and -2 <= self._safe_float(support_resistance.get("support_distance_pct"), -99) <= 0
            and suggested_direction == "bearish"
        ):
            suggested_direction = "neutral"
            matrix_reason = "接近支撑位，看跌结论需要等待跌破确认。"

        return {
            "trend_bucket": short_trend,
            "medium_trend_bucket": medium_trend,
            "momentum_bucket": momentum_state,
            "volume_bucket": volume_state,
            "risk_reward_bucket": risk_bucket,
            "risk_reward_label": risk_label,
            "intraday_bucket": intraday_state,
            "matrix_position": (
                f"短线{short_trend}+动量{momentum_state}+量能{volume_state}+{risk_label}"
            ),
            "suggested_direction": suggested_direction,
            "reason": matrix_reason,
        }

    def _has_signal_contradiction(self, ind: dict) -> bool:
        """检测信号是否有明显矛盾"""
        contradictions = 0
        # 均线空头 vs KDJ金叉
        ma5 = ind.get("MA5"); ma10 = ind.get("MA10")
        kdj_sig = ind.get("KDJ_signal")
        if ma5 and ma10 and ma5 < ma10 and kdj_sig == "golden_cross":
            contradictions += 1
        # ADX强趋势方向 vs RSI超买超卖
        adx_dir = ind.get("ADX_direction"); rsi = ind.get("RSI")
        if adx_dir == "bearish" and rsi and float(rsi) < 30:
            contradictions += 1
        if adx_dir == "bullish" and rsi and float(rsi) > 70:
            contradictions += 1
        # OBV背离
        if ind.get("OBV_divergence") not in ("none", None):
            contradictions += 1
        return contradictions >= 2

    def _apply_snapshot_constraints(self, result: AnalysisResult, data: dict) -> list[str]:
        """用代码证据包约束 LLM 输出，避免高置信度自由发挥。"""
        snapshot = data.get("technical_snapshot", {}) or {}
        if not snapshot:
            return []

        confidence_model = snapshot.get("confidence_model", {}) or {}
        evidence = snapshot.get("evidence", {}) or {}
        support_resistance = snapshot.get("support_resistance", {}) or {}
        risk_levels = snapshot.get("risk_levels", {}) or {}
        volume = snapshot.get("volume_signals", {}) or {}
        intraday_signals = data.get("intraday_signals", {}) or {}

        caps = list(confidence_model.get("hard_caps", []) or [])
        model_confidence = confidence_model.get("technical_confidence")
        if model_confidence is not None:
            try:
                model_confidence = float(model_confidence)
                if result.confidence > model_confidence:
                    old_conf = result.confidence
                    result.confidence = max(0.05, min(result.confidence, model_confidence))
                    result.reasoning += (
                        f"\n\n[技术证据约束: LLM 置信度{old_conf:.0%}→"
                        f"证据模型上限{result.confidence:.0%}]"
                    )
            except (TypeError, ValueError):
                pass

        suggested = confidence_model.get("suggested_direction")
        if suggested in {"bullish", "bearish"} and result.direction.value != suggested:
            opposite = (
                result.direction.value == "bullish" and suggested == "bearish"
            ) or (
                result.direction.value == "bearish" and suggested == "bullish"
            )
            if opposite:
                result.confidence = min(result.confidence, 0.4)
                caps.append(f"LLM 方向与技术证据包建议方向({suggested})相反，置信度不超过 0.40")

        if intraday_signals.get("available"):
            intraday_state = intraday_signals.get("state")
            intraday_evidence = intraday_signals.get("evidence", {}) or {}
            if result.direction == Direction.BULLISH and intraday_state == "selloff":
                result.confidence = min(result.confidence, 0.45)
                caps.append("分钟线处于盘中弱势，日线看涨结论需要降级，置信度不超过 0.45")
            elif result.direction == Direction.BEARISH and intraday_state == "strong_up":
                result.confidence = min(result.confidence, 0.45)
                caps.append("分钟线处于盘中强势，日线看跌结论需要降级，置信度不超过 0.45")
            elif intraday_state in {"mixed", "range_bound"} and result.confidence > 0.65:
                result.confidence = min(result.confidence, 0.65)
                caps.append("分钟线未形成明确单边方向，技术面高置信结论需要保守处理")

            intraday_source = {
                Direction.BULLISH: intraday_evidence.get("bullish", []),
                Direction.BEARISH: intraday_evidence.get("bearish", []),
                Direction.NEUTRAL: intraday_evidence.get("neutral", []),
            }.get(result.direction, [])
            for factor in intraday_source[:2]:
                factor = f"盘中信号: {factor}"
                if factor not in result.key_factors:
                    result.key_factors.append(factor)

        if result.direction == Direction.BULLISH:
            resistance_distance = support_resistance.get("resistance_distance_pct")
            if (
                resistance_distance is not None
                and 0 <= float(resistance_distance) <= 2
                and not volume.get("price_up_volume_up")
                and not volume.get("abnormal_volume")
            ):
                result.confidence = min(result.confidence, 0.45)
                caps.append("接近上方压力且未放量突破，看涨置信度不超过 0.45")
        elif result.direction == Direction.BEARISH:
            support_distance = support_resistance.get("support_distance_pct")
            if support_distance is not None and -2 <= float(support_distance) <= 0:
                result.confidence = min(result.confidence, 0.55)
                caps.append("接近下方支撑，看跌置信度需要保守")

        factor_source = {
            Direction.BULLISH: evidence.get("bullish", []),
            Direction.BEARISH: evidence.get("bearish", []),
            Direction.NEUTRAL: evidence.get("neutral", []),
        }.get(result.direction, [])
        for factor in factor_source[:3]:
            if factor and factor not in result.key_factors:
                result.key_factors.append(factor)

        if risk_levels.get("stop_loss_reference") is not None:
            risk = f"技术失效参考: 跌破 {risk_levels['stop_loss_reference']}"
            if risk not in result.risks:
                result.risks.append(risk)
        if risk_levels.get("breakout_reference") is not None:
            risk = f"突破确认参考: 放量站上 {risk_levels['breakout_reference']}"
            if risk not in result.risks:
                result.risks.append(risk)

        if caps:
            if getattr(result, "status", "ok") == "ok" and result.confidence < 0.5:
                result.status = "degraded"
                result.error_message = "技术证据存在硬约束，已降低置信度"
            for cap in caps[:3]:
                if cap not in result.risks:
                    result.risks.append(cap)
        return caps
