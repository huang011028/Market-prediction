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
from src.core.result import AnalysisResult
from src.core.confidence_calibrator import ConfidenceCalibrator
from src.data.price_fetcher import PriceFetcher
from src.data.stock_profiles import get_stock_profile, build_profile_context
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
        return TECHNICAL_SYSTEM_PROMPT

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
        # 先用默认 LLM 分析
        result = await super().analyze(data, context)

        # 校准置信度
        try:
            ind = data.get("indicators", {})
            # 判断信号是否有矛盾
            contradiction = self._has_signal_contradiction(ind)
            # 判断数据质量
            data_quality = "normal"
            if data.get("trading_days", 0) < 30:
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

        return result

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
