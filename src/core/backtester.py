"""
回测引擎

在历史区间内滚动执行分析，对比预测 vs 实际结果，
输出方向准确率、幅度命中率、置信度校准等统计。
"""

import logging
import time
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from src.core.base_agent import BaseAgent
from src.core.llm_client import LLMClient, create_llm_client
from src.core.orchestrator import Orchestrator
from src.core.prediction_target import (
    default_target_spec,
    direction_correct,
    direction_from_return,
)
from src.core.return_residualizer import estimate_market_beta_from_trends
from src.core.result import AnalysisResult
from src.agents.fundamental_analyst import FundamentalAnalyst
from src.agents.industry_analyst import IndustryAnalyst
from src.agents.macro_analyst import MacroAnalyst
from src.agents.news_analyst import NewsAnalyst
from src.agents.technical_analyst import TechnicalAnalyst
from src.agents.aggregator import Aggregator
from src.data.news_snapshot_archive import NewsSnapshotArchive
from src.data.point_in_time_snapshot_archive import PointInTimeSnapshotArchive
from src.data.price_fetcher import PriceFetcher
from src.data.symbol_resolver import resolve_symbol

logger = logging.getLogger(__name__)

AGENT_TECH = "近期股价分析师"
AGENT_FUNDAMENTAL = "公司前景分析师"
AGENT_INDUSTRY = "行业对比分析师"
AGENT_MACRO = "国际形势分析师"
AGENT_NEWS = "最新新闻分析师"
SNAPSHOT_AGENTS = {AGENT_FUNDAMENTAL, AGENT_INDUSTRY, AGENT_MACRO, AGENT_NEWS}


@dataclass
class BacktestConfig:
    """回测配置"""
    target: str
    start_date: str                    # "2025-01-01"
    end_date: str                      # "2026-06-30"
    timeframe: str = "短期(1周)"
    interval_days: int = 7             # 每隔几天做一次预测
    agents: list[str] = field(default_factory=lambda: [
        AGENT_TECH,
        AGENT_FUNDAMENTAL,
        AGENT_INDUSTRY,
        AGENT_MACRO,
        AGENT_NEWS,
    ])
    snapshot_replay_mode: str = "reanalyze"  # reanalyze / frozen_result
    max_snapshot_age_days: int = 120
    max_news_snapshot_age_days: int = 7


@dataclass
class BacktestResult:
    """单次回测结果"""
    date: str
    predicted_direction: str
    predicted_min: Optional[float]
    predicted_max: Optional[float]
    predicted_confidence: float
    actual_direction: str
    actual_change_pct: float
    direction_correct: bool
    magnitude_hit: Optional[bool]
    price_start: float
    price_end: float
    elapsed_seconds: float
    window_max_change_pct: Optional[float] = None
    window_min_change_pct: Optional[float] = None
    prediction_target: dict = field(default_factory=dict)
    expected_excess_return_pct: Optional[float] = None
    prob_up: Optional[float] = None
    prob_down: Optional[float] = None
    prob_no_edge: Optional[float] = None
    edge_score: Optional[float] = None
    decision: str = ""
    no_trade_reason: str = ""
    agent_snapshot_lineage: list[dict] = field(default_factory=list)


@dataclass
class BacktestReport:
    """完整回测报告"""
    config: BacktestConfig
    total_runs: int
    success_runs: int
    results: list[BacktestResult] = field(default_factory=list)

    @property
    def direction_accuracy(self) -> float:
        if not self.results:
            return 0.0
        correct = sum(1 for r in self.results if r.direction_correct)
        return correct / len(self.results)

    @property
    def magnitude_accuracy(self) -> float:
        valid = [r for r in self.results if r.magnitude_hit is not None]
        if not valid:
            return 0.0
        return sum(1 for r in valid if r.magnitude_hit) / len(valid)

    @property
    def avg_confidence(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.predicted_confidence for r in self.results) / len(self.results)

    @property
    def avg_error_pct(self) -> float:
        if not self.results:
            return 0.0
        errors = []
        for r in self.results:
            if r.predicted_min is not None and r.predicted_max is not None:
                mid = (r.predicted_min + r.predicted_max) / 2
                errors.append(abs(r.actual_change_pct - mid))
        return sum(errors) / len(errors) if errors else 0.0

    @property
    def high_confidence_non_neutral_coverage(self) -> float:
        if not self.results:
            return 0.0
        selected = [
            r for r in self.results
            if r.predicted_direction != "neutral" and r.predicted_confidence >= 0.60
        ]
        return len(selected) / len(self.results)

    @property
    def high_edge_avg_directional_return_pct(self) -> float:
        selected = [
            r for r in self.results
            if (r.edge_score or 0.0) >= 0.22 and r.predicted_direction in {"bullish", "bearish"}
        ]
        if not selected:
            return 0.0
        directional_returns = [
            r.actual_change_pct if r.predicted_direction == "bullish" else -r.actual_change_pct
            for r in selected
        ]
        return sum(directional_returns) / len(directional_returns)

    @property
    def brier_score(self) -> float:
        if not self.results:
            return 0.0
        return sum(_multiclass_brier(r) for r in self.results) / len(self.results)

    @property
    def probability_calibration_error(self) -> float:
        if not self.results:
            return 0.0
        errors = []
        for r in self.results:
            probs = _result_probabilities(r)
            predicted_class = max(probs, key=probs.get)
            confidence = probs[predicted_class]
            hit = 1.0 if predicted_class == r.actual_direction else 0.0
            errors.append(abs(confidence - hit))
        return sum(errors) / len(errors) if errors else 0.0

    def to_dict(self) -> dict:
        return {
            "config": {
                "target": self.config.target,
                "start_date": self.config.start_date,
                "end_date": self.config.end_date,
                "timeframe": self.config.timeframe,
                "interval_days": self.config.interval_days,
            },
            "total_runs": self.total_runs,
            "success_runs": self.success_runs,
            "direction_accuracy": round(self.direction_accuracy, 3),
            "magnitude_accuracy": round(self.magnitude_accuracy, 3),
            "avg_confidence": round(self.avg_confidence, 3),
            "avg_error_pct": round(self.avg_error_pct, 2),
            "high_confidence_non_neutral_coverage": round(self.high_confidence_non_neutral_coverage, 3),
            "high_edge_avg_directional_return_pct": round(self.high_edge_avg_directional_return_pct, 2),
            "brier_score": round(self.brier_score, 4),
            "probability_calibration_error": round(self.probability_calibration_error, 4),
            "results": [
                {
                    "date": r.date,
                    "predicted": f"{r.predicted_direction} {r.predicted_min}~{r.predicted_max}%",
                    "actual": f"{r.actual_direction} {r.actual_change_pct:+.2f}%",
                    "decision": r.decision,
                    "no_trade_reason": r.no_trade_reason,
                    "expected_excess_return_pct": r.expected_excess_return_pct,
                    "edge_score": r.edge_score,
                    "probabilities": {
                        "up": r.prob_up,
                        "down": r.prob_down,
                        "no_edge": r.prob_no_edge,
                    },
                    "window": {
                        "max_pct": r.window_max_change_pct,
                        "min_pct": r.window_min_change_pct,
                    },
                    "prediction_target": r.prediction_target,
                    "agent_snapshot_lineage": r.agent_snapshot_lineage,
                    "direction_correct": r.direction_correct,
                    "magnitude_hit": r.magnitude_hit,
                    "elapsed_s": round(r.elapsed_seconds, 1),
                }
                for r in self.results
            ],
        }

    def summary(self) -> str:
        lines = [
            "=" * 50,
            f"  回测报告: {self.config.target}",
            f"  区间: {self.config.start_date} → {self.config.end_date}",
            f"  周期: {self.config.timeframe} | 间隔: {self.config.interval_days}天",
            "=" * 50,
            f"  运行: {self.total_runs} | 成功: {self.success_runs}",
            f"  方向准确率: {self.direction_accuracy:.1%}",
            f"  幅度命中率: {self.magnitude_accuracy:.1%}",
            f"  平均置信度: {self.avg_confidence:.1%}",
            f"  平均误差:    {self.avg_error_pct:.2f}%",
            f"  高置信非中性覆盖率: {self.high_confidence_non_neutral_coverage:.1%}",
            f"  高边际方向收益: {self.high_edge_avg_directional_return_pct:+.2f}%",
            f"  Brier: {self.brier_score:.4f}",
            f"  概率校准误差: {self.probability_calibration_error:.4f}",
            "=" * 50,
        ]
        return "\n".join(lines)


def _result_probabilities(result: BacktestResult) -> dict[str, float]:
    up = _coerce_probability(result.prob_up)
    down = _coerce_probability(result.prob_down)
    neutral = _coerce_probability(result.prob_no_edge)
    if up is None or down is None or neutral is None:
        confidence = max(0.0, min(1.0, float(result.predicted_confidence or 0.0)))
        residual = 1.0 - confidence
        if result.predicted_direction == "bullish":
            up, down, neutral = confidence, residual * 0.35, residual * 0.65
        elif result.predicted_direction == "bearish":
            down, up, neutral = confidence, residual * 0.35, residual * 0.65
        else:
            neutral, up, down = confidence, residual * 0.5, residual * 0.5
    total = max(float(up or 0.0) + float(down or 0.0) + float(neutral or 0.0), 1e-9)
    return {
        "bullish": float(up or 0.0) / total,
        "bearish": float(down or 0.0) / total,
        "neutral": float(neutral or 0.0) / total,
    }


def _multiclass_brier(result: BacktestResult) -> float:
    probs = _result_probabilities(result)
    return sum(
        (probs[label] - (1.0 if result.actual_direction == label else 0.0)) ** 2
        for label in ("bullish", "bearish", "neutral")
    ) / 3.0


def _coerce_probability(value) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number > 1.0 and number <= 100.0:
        number = number / 100.0
    return max(0.0, min(1.0, number))


class HistoricalTechnicalAnalyst(TechnicalAnalyst):
    """回测专用技术面 Agent，使用已截断的历史 K 线快照。"""

    def __init__(self, llm: LLMClient, price_data, as_of: datetime):
        super().__init__(llm)
        self._price_data = price_data
        self._as_of = as_of

    async def gather_data(self, target: str, timeframe: str) -> dict:
        data = self._price_data.to_agent_dict()
        data["_backtest_as_of"] = self._as_of.strftime("%Y-%m-%d")
        return data


class _HistoricalSnapshotMixin:
    """Inject archived data while preserving each analyst's current analysis path."""

    def _set_historical_snapshot(self, data: dict, snapshot: dict) -> None:
        self._historical_data = deepcopy(data)
        self._historical_snapshot = deepcopy(snapshot)

    async def gather_data(self, target: str, timeframe: str) -> dict:
        data = deepcopy(self._historical_data)
        data["_point_in_time_replay"] = {
            "snapshot_id": self._historical_snapshot.get("snapshot_id"),
            "as_of": self._historical_snapshot.get("as_of"),
            "source_kind": self._historical_snapshot.get("source_kind"),
            "lineage": self._historical_snapshot.get("lineage") or {},
        }
        return data


class HistoricalFundamentalAnalyst(_HistoricalSnapshotMixin, FundamentalAnalyst):
    def __init__(self, llm: LLMClient, data: dict, snapshot: dict):
        super().__init__(llm)
        self._set_historical_snapshot(data, snapshot)


class HistoricalIndustryAnalyst(_HistoricalSnapshotMixin, IndustryAnalyst):
    def __init__(self, llm: LLMClient, data: dict, snapshot: dict):
        super().__init__(llm)
        self._set_historical_snapshot(data, snapshot)


class HistoricalMacroAnalyst(_HistoricalSnapshotMixin, MacroAnalyst):
    def __init__(self, llm: LLMClient, data: dict, snapshot: dict):
        super().__init__(llm)
        self._set_historical_snapshot(data, snapshot)


class HistoricalNewsAnalyst(_HistoricalSnapshotMixin, NewsAnalyst):
    def __init__(self, llm: LLMClient, data: dict, snapshot: dict):
        super().__init__(llm, archive_snapshots=False)
        self._set_historical_snapshot(data, snapshot)


class FrozenSnapshotResultAgent(BaseAgent):
    """Return the exact result archived at prediction time."""

    def __init__(self, result: dict):
        self._result = AnalysisResult.from_dict(result)
        super().__init__(
            name=self._result.agent_name,
            description="历史时点已归档预测结果",
            llm=None,
        )

    async def run(self, target: str, timeframe: str) -> AnalysisResult:
        return deepcopy(self._result)

    async def gather_data(self, target: str, timeframe: str) -> dict:
        return {}

    def _get_system_prompt(self) -> str:
        return ""


class Backtester:
    """回测引擎"""

    def __init__(
        self,
        llm: Optional[LLMClient] = None,
        point_in_time_archive: Optional[PointInTimeSnapshotArchive] = None,
        news_archive: Optional[NewsSnapshotArchive] = None,
    ):
        self.llm = llm or create_llm_client()
        self.point_in_time_archive = point_in_time_archive or PointInTimeSnapshotArchive()
        self.news_archive = news_archive or NewsSnapshotArchive()

    async def run(self, config: BacktestConfig) -> BacktestReport:
        """执行回测"""
        logger.info(f"开始回测: {config.target} | "
                     f"{config.start_date} → {config.end_date} | "
                     f"间隔={config.interval_days}天")

        start_dt = datetime.strptime(config.start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(config.end_date, "%Y-%m-%d")

        # 生成回测日期列表（只取交易日近似——实际会被数据获取自然跳过非交易日）
        dates = []
        current = start_dt
        while current <= end_dt:
            dates.append(current)
            current += timedelta(days=config.interval_days)

        logger.info(f"共 {len(dates)} 个回测日期")

        results = []
        success = 0

        for i, bt_date in enumerate(dates):
            date_str = bt_date.strftime("%Y-%m-%d")
            logger.info(f"[{i+1}/{len(dates)}] 回测 {date_str}...")

            try:
                result = await self._run_single(config, bt_date)
                results.append(result)
                success += 1
                logger.info(f"  → {result.predicted_direction} vs "
                             f"实际 {result.actual_direction} {result.actual_change_pct:+.2f}% | "
                             f"{'✓' if result.direction_correct else '✗'} "
                             f"({result.elapsed_seconds:.0f}s)")
            except Exception as e:
                logger.warning(f"  ✗ 回测 {date_str} 失败: {e}")

        return BacktestReport(
            config=config,
            total_runs=len(dates),
            success_runs=success,
            results=results,
        )

    async def _run_single(
        self, config: BacktestConfig, bt_date: datetime
    ) -> BacktestResult:
        """在指定历史日期执行一次分析"""
        start_t = time.monotonic()

        # 获取截至回测日期可见的技术面数据，避免使用未来 K 线。
        pf = PriceFetcher()
        price_data = await pf.fetch_as_of(config.target, bt_date, lookback_days=180)

        if price_data.trading_days < 20:
            raise ValueError(f"数据不足: {price_data.trading_days} 个交易日")

        # --- 执行 Agent 分析 ---
        orchestrator = Orchestrator()
        active = []
        snapshot_lineage: list[dict] = []

        if AGENT_TECH in config.agents:
            tech = HistoricalTechnicalAnalyst(self.llm, price_data, bt_date)
            orchestrator.register(tech)
            active.append(AGENT_TECH)

        for agent_name in config.agents:
            if agent_name not in SNAPSHOT_AGENTS:
                continue
            snapshot = self._find_snapshot(config.target, agent_name, bt_date, config)
            if not snapshot:
                logger.warning(
                    "历史快照缺失，回测中跳过: agent=%s target=%s as_of=%s",
                    agent_name,
                    config.target,
                    bt_date.date().isoformat(),
                )
                continue
            agent = self._build_snapshot_agent(agent_name, snapshot, config.snapshot_replay_mode)
            if agent is None:
                logger.warning(
                    "历史快照缺少可回放内容，跳过: agent=%s snapshot=%s mode=%s",
                    agent_name,
                    snapshot.get("snapshot_id"),
                    config.snapshot_replay_mode,
                )
                continue
            orchestrator.register(agent)
            active.append(agent_name)
            snapshot_lineage.append({
                "agent_name": agent_name,
                "snapshot_id": snapshot.get("snapshot_id"),
                "as_of": snapshot.get("as_of") or snapshot.get("date"),
                "source_kind": snapshot.get("source_kind"),
                "schema_version": snapshot.get("schema_version"),
                "replay_mode": config.snapshot_replay_mode,
            })

        if not active:
            raise ValueError("没有可历史回放的 Agent：技术面需要历史 K 线，其余 Agent 需要合规快照")

        agent_results = await orchestrator.run_selected(
            config.target, config.timeframe, agent_names=active,
        )

        # 汇总
        aggregator = Aggregator(self.llm)
        succeeded = {r.agent_name for r in agent_results}
        failed = [n for n in active if n not in succeeded]

        report = await aggregator.aggregate(
            config.target, config.timeframe,
            agent_results,
            failed_agents=failed if failed else None,
        )

        elapsed = time.monotonic() - start_t

        # --- 计算实际涨跌幅 ---
        # 起点是回测日当时可见的收盘价；终点收益用于幅度，窗口高低点用于方向障碍命中。
        price_start = price_data.price_current

        target_spec = getattr(report, "prediction_target", None) or default_target_spec(
            config.timeframe,
            target=config.target,
        )
        if target_spec.target_type == "residual_return" and target_spec.benchmark_symbol:
            try:
                benchmark_history = await pf.fetch_as_of(
                    target_spec.benchmark_symbol,
                    bt_date,
                    lookback_days=max(180, target_spec.beta_lookback_days),
                )
                target_spec.market_beta = estimate_market_beta_from_trends(
                    price_data.recent_trend,
                    benchmark_history.recent_trend,
                    min_observations=target_spec.beta_min_observations,
                )
            except Exception:
                target_spec.market_beta = None
        valid_date = bt_date + timedelta(days=int(target_spec.horizon_calendar_days))
        if target_spec.evaluation_mode == "fixed_horizon":
            future_closes = await pf.fetch_trading_horizon(
                config.target,
                bt_date,
                target_spec.horizon_trading_days,
                target_spec.horizon_calendar_days + 10,
            )
            closes = future_closes
            valid_date = closes.index[-1].to_pydatetime()
        else:
            closes = await pf.fetch_close_window(
                config.target,
                bt_date + timedelta(days=1),
                valid_date,
            )

        if closes is None or len(closes) == 0:
            raise ValueError("验证窗口内没有可用收盘价")

        price_end = float(closes.iloc[-1])
        target_changes = (closes / price_start - 1) * 100 if price_start > 0 else closes * 0
        effective_changes = target_changes
        if target_spec.target_type in {"excess_return", "residual_return"} and target_spec.benchmark_symbol:
            try:
                benchmark_start = await pf.fetch_close_near(
                    target_spec.benchmark_symbol,
                    bt_date,
                    prefer="on_or_before",
                    tolerance_days=10,
                )
                if target_spec.evaluation_mode == "fixed_horizon":
                    benchmark_future = await pf.fetch_trading_horizon(
                        target_spec.benchmark_symbol,
                        bt_date,
                        target_spec.horizon_trading_days,
                        target_spec.horizon_calendar_days + 10,
                    )
                    benchmark_closes = benchmark_future
                else:
                    benchmark_closes = await pf.fetch_close_window(
                        target_spec.benchmark_symbol,
                        bt_date + timedelta(days=1),
                        valid_date,
                    )
                benchmark_changes = (benchmark_closes / benchmark_start - 1) * 100
                benchmark_aligned = benchmark_changes.reindex(
                    target_changes.index,
                    method="ffill",
                ).bfill()
                if not benchmark_aligned.isna().any():
                    beta = (
                        float(target_spec.market_beta)
                        if target_spec.target_type == "residual_return"
                        and target_spec.market_beta is not None
                        else 1.0
                    )
                    effective_changes = target_changes - beta * benchmark_aligned
            except Exception as e:
                logger.debug(
                    "回测基准收益计算失败，回退绝对收益: target=%s benchmark=%s error=%s",
                    config.target,
                    target_spec.benchmark_symbol,
                    e,
                )

        actual_change = float(effective_changes.iloc[-1])
        window_max = float(effective_changes.max())
        window_min = float(effective_changes.min())

        # 判断方向：固定到期收益 + 窗口障碍。
        pred_dir = report.direction.value
        dir_correct = direction_correct(
            pred_dir,
            actual_change,
            window_max,
            window_min,
            target_spec,
        )

        if target_spec.evaluation_mode == "fixed_horizon":
            actual_dir = direction_from_return(actual_change, target_spec)
        else:
            upper_hits = effective_changes[effective_changes >= target_spec.up_threshold_pct]
            lower_hits = effective_changes[effective_changes <= target_spec.down_threshold_pct]
            if not upper_hits.empty and not lower_hits.empty:
                actual_dir = "bullish" if upper_hits.index[0] <= lower_hits.index[0] else "bearish"
            elif not upper_hits.empty:
                actual_dir = "bullish"
            elif not lower_hits.empty:
                actual_dir = "bearish"
            else:
                actual_dir = direction_from_return(actual_change, target_spec)

        # 幅度
        mag = report.magnitude
        if mag:
            mag_hit = mag.min_pct <= actual_change <= mag.max_pct
        else:
            mag_hit = None

        return BacktestResult(
            date=bt_date.strftime("%Y-%m-%d"),
            predicted_direction=pred_dir,
            predicted_min=mag.min_pct if mag else None,
            predicted_max=mag.max_pct if mag else None,
            predicted_confidence=report.confidence,
            actual_direction=actual_dir,
            actual_change_pct=round(actual_change, 2),
            direction_correct=dir_correct,
            magnitude_hit=mag_hit,
            price_start=price_start,
            price_end=price_end,
            elapsed_seconds=elapsed,
            window_max_change_pct=round(window_max, 2),
            window_min_change_pct=round(window_min, 2),
            prediction_target=target_spec.to_dict(),
            expected_excess_return_pct=getattr(report, "expected_excess_return_pct", None),
            prob_up=getattr(report, "prob_up", None),
            prob_down=getattr(report, "prob_down", None),
            prob_no_edge=getattr(report, "prob_no_edge", None),
            edge_score=getattr(report, "edge_score", None),
            decision=getattr(report, "decision", ""),
            no_trade_reason=getattr(report, "no_trade_reason", ""),
            agent_snapshot_lineage=snapshot_lineage,
        )

    def _find_snapshot(
        self,
        target: str,
        agent_name: str,
        bt_date: datetime,
        config: BacktestConfig,
    ) -> Optional[dict]:
        """Return the freshest eligible snapshot that was visible at bt_date."""
        try:
            identifiers = {str(target).upper(), str(resolve_symbol(target).symbol).upper()}
        except Exception:
            identifiers = {str(target).upper()}

        end_date = bt_date.date().isoformat()
        if agent_name == AGENT_NEWS:
            candidates = self.news_archive.load_snapshots(end_date=end_date)
            max_age = config.max_news_snapshot_age_days
        else:
            candidates = self.point_in_time_archive.load_snapshots(
                agent_name=agent_name,
                end_date=end_date,
            )
            max_age = config.max_snapshot_age_days

        eligible: list[dict] = []
        for snapshot in candidates:
            snapshot_ids = {
                str(snapshot.get("target") or "").upper(),
                str(snapshot.get("symbol") or "").upper(),
                str((snapshot.get("data") or {}).get("_resolved_symbol") or "").upper(),
                str((snapshot.get("news_data") or {}).get("_resolved_symbol") or "").upper(),
            }
            if not identifiers.intersection(snapshot_ids):
                continue
            raw_date = str(snapshot.get("as_of") or snapshot.get("date") or "")[:10]
            try:
                snapshot_date = datetime.strptime(raw_date, "%Y-%m-%d")
            except ValueError:
                continue
            age_days = (bt_date.date() - snapshot_date.date()).days
            if 0 <= age_days <= max_age:
                eligible.append(snapshot)
        if not eligible:
            return None
        return max(eligible, key=lambda item: str(item.get("as_of") or item.get("date") or ""))

    def _build_snapshot_agent(
        self,
        agent_name: str,
        snapshot: dict,
        replay_mode: str,
    ) -> Optional[BaseAgent]:
        if replay_mode not in {"reanalyze", "frozen_result"}:
            raise ValueError("snapshot_replay_mode 必须是 reanalyze 或 frozen_result")
        if replay_mode == "frozen_result":
            result = snapshot.get("analysis_result") or {}
            return FrozenSnapshotResultAgent(result) if result else None

        data = snapshot.get("news_data") if agent_name == AGENT_NEWS else snapshot.get("data")
        if not isinstance(data, dict) or not data:
            return None
        factories = {
            AGENT_FUNDAMENTAL: HistoricalFundamentalAnalyst,
            AGENT_INDUSTRY: HistoricalIndustryAnalyst,
            AGENT_MACRO: HistoricalMacroAnalyst,
            AGENT_NEWS: HistoricalNewsAnalyst,
        }
        factory = factories.get(agent_name)
        return factory(self.llm, data, snapshot) if factory else None

    @staticmethod
    def _horizon_days(timeframe: str) -> int:
        """把预测周期映射为自然日 horizon。"""
        return default_target_spec(timeframe).horizon_calendar_days
