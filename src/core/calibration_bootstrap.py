"""
历史回测初始校准系统

用历史价格截面/新闻快照主动生成校准样本，解决校准器冷启动问题。
技术面可以直接用历史 K 线回放；新闻面依赖已归档的新闻快照。
"""

import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, Optional

from src.agents.fundamental_analyst import FundamentalAnalyst
from src.agents.industry_analyst import IndustryAnalyst
from src.agents.macro_analyst import MacroAnalyst
from src.agents.news_analyst import NewsAnalyst
from src.agents.technical_analyst import TechnicalAnalyst
from src.core.agent_improvement import AgentImprovementAdvisor
from src.core.prediction_target import (
    PredictionTargetSpec,
    default_target_spec,
    direction_correct as target_direction_correct,
    direction_from_return,
    resolve_prediction_target,
    target_spec_for_volatility,
)
from src.data.price_fetcher import PriceFetcher
from src.utils.fundamental_calibrator import FundamentalConfidenceCalibrator
from src.utils.industry_calibrator import IndustryConfidenceCalibrator
from src.utils.macro_calibrator import MacroConfidenceCalibrator
from src.utils.news_calibrator import NewsConfidenceCalibrator
from src.utils.technical_calibrator import TechnicalConfidenceCalibrator

logger = logging.getLogger(__name__)


@dataclass
class CalibrationBootstrapConfig:
    """历史校准启动配置。"""

    targets: list[str]
    start_date: str
    end_date: str
    timeframe: str = "短期(1周)"
    interval_days: int = 7
    lookback_days: int = 180
    tolerance_days: int = 10


@dataclass
class CalibrationSample:
    """单个历史校准样本。"""

    agent_name: str
    target: str
    as_of: str
    valid_date: str
    predicted_direction: str
    predicted_confidence: float
    actual_direction: str
    actual_change_pct: float
    was_correct: bool
    price_start: float
    price_end: float
    buckets: dict
    evidence_reason: str = ""
    validation_mode: str = "single_date"
    validation_window: str = ""
    window_max_change_pct: Optional[float] = None
    window_min_change_pct: Optional[float] = None
    prediction_target: dict = field(default_factory=dict)
    fixed_horizon_return_pct: Optional[float] = None
    effective_fixed_return_pct: Optional[float] = None
    target_type_used: str = "absolute_return"
    benchmark_return_pct: Optional[float] = None
    excess_return_pct: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)


def _outcome_sample_fields(outcome: dict, target_spec: PredictionTargetSpec) -> dict:
    return {
        "prediction_target": target_spec.to_dict(),
        "fixed_horizon_return_pct": round(outcome["fixed_horizon_return_pct"], 2),
        "effective_fixed_return_pct": round(outcome["effective_fixed_return_pct"], 2),
        "target_type_used": outcome["target_type_used"],
        "benchmark_return_pct": (
            round(outcome["benchmark_return_pct"], 2)
            if outcome.get("benchmark_return_pct") is not None else None
        ),
        "excess_return_pct": (
            round(outcome["excess_return_pct"], 2)
            if outcome.get("excess_return_pct") is not None else None
        ),
    }


@dataclass
class CalibrationBootstrapReport:
    """历史校准启动报告。"""

    agent_name: str
    total_candidates: int
    success_samples: int
    samples: list[CalibrationSample] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    calibration_stats: dict = field(default_factory=dict)
    improvement_signals: list[dict] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    @property
    def direction_accuracy(self) -> float:
        if not self.samples:
            return 0.0
        return sum(1 for sample in self.samples if sample.was_correct) / len(self.samples)

    def to_dict(self) -> dict:
        return {
            "agent_name": self.agent_name,
            "total_candidates": self.total_candidates,
            "success_samples": self.success_samples,
            "direction_accuracy": round(self.direction_accuracy, 3),
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "samples": [sample.to_dict() for sample in self.samples],
            "skipped": self.skipped,
            "calibration_stats": self.calibration_stats,
            "improvement_signals": self.improvement_signals,
        }

    def summary(self) -> str:
        return (
            f"{self.agent_name} 历史校准: "
            f"{self.success_samples}/{self.total_candidates} 个样本, "
            f"方向命中率 {self.direction_accuracy:.1%}, "
            f"跳过 {len(self.skipped)} 个。"
        )


class TechnicalCalibrationBootstrapper:
    """生成技术面历史回测初始校准样本。"""

    AGENT_NAME = "近期股价分析师"

    def __init__(
        self,
        price_fetcher: Optional[PriceFetcher] = None,
        calibrator: Optional[TechnicalConfidenceCalibrator] = None,
        advisor: Optional[AgentImprovementAdvisor] = None,
    ):
        self.price_fetcher = price_fetcher or PriceFetcher()
        self.calibrator = calibrator or TechnicalConfidenceCalibrator()
        self.advisor = advisor or AgentImprovementAdvisor()
        self._analyst = TechnicalAnalyst.__new__(TechnicalAnalyst)

    async def run(self, config: CalibrationBootstrapConfig) -> CalibrationBootstrapReport:
        started = time.monotonic()
        dates = self._build_dates(config.start_date, config.end_date, config.interval_days)
        total_candidates = len(config.targets) * len(dates)
        samples: list[CalibrationSample] = []
        skipped: list[dict] = []

        for target in config.targets:
            for as_of in dates:
                try:
                    sample = await self._generate_sample(target, as_of, config)
                except Exception as e:
                    skipped.append({
                        "target": target,
                        "as_of": as_of.strftime("%Y-%m-%d"),
                        "reason": str(e),
                    })
                    logger.debug("技术面历史校准样本跳过: %s %s %s", target, as_of, e)
                    continue

                samples.append(sample)
                self.calibrator.update_from_validation(
                    predicted_conf=sample.predicted_confidence,
                    was_correct=sample.was_correct,
                    **sample.buckets,
                )

        self.calibrator.save()
        stats = self.calibrator.get_calibration_stats()
        signals = [
            signal.to_dict()
            for signal in self.advisor.recommend(self.AGENT_NAME, stats)
        ]
        return CalibrationBootstrapReport(
            agent_name=self.AGENT_NAME,
            total_candidates=total_candidates,
            success_samples=len(samples),
            samples=samples,
            skipped=skipped,
            calibration_stats=stats,
            improvement_signals=signals,
            elapsed_seconds=time.monotonic() - started,
        )

    async def _generate_sample(
        self,
        target: str,
        as_of: datetime,
        config: CalibrationBootstrapConfig,
    ) -> CalibrationSample:
        price_data = await self.price_fetcher.fetch_as_of(
            target,
            as_of,
            lookback_days=config.lookback_days,
        )
        if getattr(price_data, "trading_days", 0) < 20:
            raise ValueError(f"数据不足: {getattr(price_data, 'trading_days', 0)} 个交易日")

        data = price_data.to_agent_dict()
        evidence = self._analyst._build_evidence_packet(data, config.timeframe)
        matrix = evidence.get("decision_matrix") or {}
        constraints = evidence.get("confidence_constraints") or {}

        predicted_direction = matrix.get("suggested_direction") or "neutral"
        predicted_confidence = self._safe_float(
            constraints.get("max_confidence") or constraints.get("technical_confidence"),
            0.50,
        )
        buckets = TechnicalConfidenceCalibrator.extract_buckets_from_evidence(
            evidence,
            config.timeframe,
        )

        price_start = float(getattr(price_data, "price_current", 0.0) or 0.0)
        if price_start <= 0:
            raise ValueError("起始价格不可用")

        target_spec = resolve_prediction_target(
            config.timeframe,
            predicted_direction,
            None,
            predicted_confidence,
            target=target,
        )
        daily_volatility = (
            (data.get("technical_snapshot") or {})
            .get("volatility_signals", {})
            .get("daily_volatility_20d_pct")
        )
        target_spec = target_spec_for_volatility(target_spec, daily_volatility)
        valid_dt = as_of + timedelta(days=target_spec.horizon_calendar_days)
        outcome = await self._horizon_window_outcome(
            self.price_fetcher,
            target,
            price_start,
            as_of,
            valid_dt,
            predicted_direction,
            target_spec,
        )

        return CalibrationSample(
            agent_name=self.AGENT_NAME,
            target=target,
            as_of=as_of.strftime("%Y-%m-%d"),
            valid_date=valid_dt.strftime("%Y-%m-%d"),
            predicted_direction=predicted_direction,
            predicted_confidence=round(predicted_confidence, 3),
            actual_direction=outcome["actual_direction"],
            actual_change_pct=round(outcome["actual_change_pct"], 2),
            was_correct=self._direction_correct(
                predicted_direction,
                outcome["effective_fixed_return_pct"],
                outcome["window_max_change_pct"],
                outcome["window_min_change_pct"],
                target_spec,
            ),
            price_start=round(float(outcome.get("price_entry", price_start)), 4),
            price_end=round(float(outcome["price_end"]), 4),
            buckets=buckets,
            evidence_reason=matrix.get("reason", ""),
            validation_mode=outcome["validation_mode"],
            validation_window=outcome["validation_window"],
            window_max_change_pct=round(outcome["window_max_change_pct"], 2),
            window_min_change_pct=round(outcome["window_min_change_pct"], 2),
            prediction_target=target_spec.to_dict(),
            fixed_horizon_return_pct=round(outcome["fixed_horizon_return_pct"], 2),
            effective_fixed_return_pct=round(outcome["effective_fixed_return_pct"], 2),
            target_type_used=outcome["target_type_used"],
            benchmark_return_pct=(
                round(outcome["benchmark_return_pct"], 2)
                if outcome.get("benchmark_return_pct") is not None else None
            ),
            excess_return_pct=(
                round(outcome["excess_return_pct"], 2)
                if outcome.get("excess_return_pct") is not None else None
            ),
        )

    @staticmethod
    def _build_dates(start_date: str, end_date: str, interval_days: int) -> list[datetime]:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        dates = []
        current = start_dt
        while current <= end_dt:
            dates.append(current)
            current += timedelta(days=max(1, interval_days))
        return dates

    @staticmethod
    def _horizon_days(timeframe: str) -> int:
        if "周" in timeframe or "短期" in timeframe:
            return 7
        if "月" in timeframe or "中期" in timeframe:
            return 30
        if "季" in timeframe or "季度" in timeframe or "长期" in timeframe:
            return 90
        return 7

    @staticmethod
    def _actual_direction(change_pct: float) -> str:
        if change_pct > 0.5:
            return "bullish"
        if change_pct < -0.5:
            return "bearish"
        return "neutral"

    @staticmethod
    def _actual_direction_from_window(max_change_pct: float, min_change_pct: float) -> str:
        if max_change_pct > 0.5 and abs(max_change_pct) >= abs(min_change_pct):
            return "bullish"
        if min_change_pct < -0.5:
            return "bearish"
        return "neutral"

    @staticmethod
    def _direction_correct(
        predicted_direction: str,
        actual_change_pct: float,
        window_max_change_pct: Optional[float] = None,
        window_min_change_pct: Optional[float] = None,
        target_spec: PredictionTargetSpec | dict | None = None,
    ) -> bool:
        return target_direction_correct(
            predicted_direction,
            actual_change_pct,
            window_max_change_pct,
            window_min_change_pct,
            target_spec,
        )

    @classmethod
    async def _horizon_window_outcome(
        cls,
        price_fetcher: PriceFetcher,
        target: str,
        price_start: float,
        as_of: datetime,
        valid_dt: datetime,
        predicted_direction: str,
        target_spec: PredictionTargetSpec | dict | None = None,
    ) -> dict:
        spec = PredictionTargetSpec.from_dict(target_spec)
        window_start = as_of + timedelta(days=1)
        if not hasattr(price_fetcher, "fetch_close_window"):
            price_end = await price_fetcher.fetch_close_near(
                target,
                valid_dt,
                prefer="on_or_after",
            )
            actual_change = (float(price_end) / price_start - 1) * 100
            return {
                "validation_mode": "single_date_fallback",
                "validation_window": (
                    f"{window_start.date().isoformat()}~{valid_dt.date().isoformat()}"
                ),
                "actual_direction": direction_from_return(actual_change, spec),
                "actual_change_pct": float(actual_change),
                "window_max_change_pct": float(actual_change),
                "window_min_change_pct": float(actual_change),
                "price_end": float(price_end),
                "fixed_horizon_return_pct": float(actual_change),
                "effective_fixed_return_pct": float(actual_change),
                "target_type_used": "absolute_return",
                "benchmark_return_pct": None,
                "excess_return_pct": None,
            }

        if spec.evaluation_mode == "fixed_horizon" and hasattr(price_fetcher, "fetch_trading_horizon"):
            future_closes = await price_fetcher.fetch_trading_horizon(
                target,
                as_of,
                spec.horizon_trading_days,
                spec.horizon_calendar_days + 10,
            )
            closes = future_closes
            valid_dt = closes.index[-1].to_pydatetime()
        else:
            closes = await price_fetcher.fetch_close_window(target, window_start, valid_dt)
        target_changes = (closes / price_start - 1) * 100
        fixed_horizon_return = float(target_changes.iloc[-1])
        effective_changes = target_changes
        target_type_used = "absolute_return"
        benchmark_return = None
        excess_return = None

        if spec.target_type in {"excess_return", "residual_return"} and spec.benchmark_symbol:
            try:
                if spec.evaluation_mode == "fixed_horizon" and hasattr(price_fetcher, "fetch_trading_horizon"):
                    benchmark_start = await price_fetcher.fetch_close_near(
                        spec.benchmark_symbol,
                        as_of,
                        prefer="on_or_before",
                        tolerance_days=10,
                    )
                    benchmark_future = await price_fetcher.fetch_trading_horizon(
                        spec.benchmark_symbol,
                        as_of,
                        spec.horizon_trading_days,
                        spec.horizon_calendar_days + 10,
                    )
                    benchmark_closes = benchmark_future
                else:
                    benchmark_start = await price_fetcher.fetch_close_near(
                        spec.benchmark_symbol,
                        as_of,
                        prefer="on_or_before",
                        tolerance_days=10,
                    )
                    benchmark_closes = await price_fetcher.fetch_close_window(
                        spec.benchmark_symbol,
                        window_start,
                        valid_dt,
                    )
                benchmark_changes = (benchmark_closes / benchmark_start - 1) * 100
                benchmark_aligned = benchmark_changes.reindex(
                    target_changes.index,
                    method="ffill",
                ).bfill()
                if benchmark_aligned.isna().any():
                    raise ValueError("基准窗口无法与标的交易日对齐")
                benchmark_return = float(benchmark_aligned.iloc[-1])
                beta = float(spec.market_beta) if spec.market_beta is not None else 1.0
                excess_series = target_changes - benchmark_aligned
                residual_series = target_changes - beta * benchmark_aligned
                excess_return = float(excess_series.iloc[-1])
                effective_changes = (
                    residual_series if spec.target_type == "residual_return" else excess_series
                )
                target_type_used = spec.target_type
            except Exception as e:
                logger.debug(
                    "基准收益计算失败，回退绝对收益: target=%s benchmark=%s error=%s",
                    target,
                    spec.benchmark_symbol,
                    e,
                )

        max_change = float(effective_changes.max())
        min_change = float(effective_changes.min())
        effective_fixed_return = (
            float(effective_changes.iloc[-1])
            if target_type_used in {"excess_return", "residual_return"}
            else fixed_horizon_return
        )
        barrier_direction = cls._barrier_direction(effective_changes, spec)
        if spec.evaluation_mode == "fixed_horizon":
            actual_direction = direction_from_return(effective_fixed_return, spec)
            selected_date = effective_changes.index[-1]
            actual_change = effective_fixed_return
        else:
            actual_direction = barrier_direction
            if actual_direction == "bullish":
                selected_date = effective_changes.idxmax()
                actual_change = max_change
            elif actual_direction == "bearish":
                selected_date = effective_changes.idxmin()
                actual_change = min_change
            else:
                selected_date = effective_changes.abs().idxmax()
                actual_change = float(effective_changes.loc[selected_date])

        return {
            "validation_mode": "horizon_window",
            "validation_window": (
                f"{window_start.date().isoformat()}~{valid_dt.date().isoformat()}"
            ),
            "actual_direction": actual_direction,
            "barrier_direction": barrier_direction,
            "actual_change_pct": float(actual_change),
            "window_max_change_pct": max_change,
            "window_min_change_pct": min_change,
            "price_end": float(closes.loc[selected_date]),
            "price_entry": float(price_start),
            "fixed_horizon_return_pct": fixed_horizon_return,
            "effective_fixed_return_pct": effective_fixed_return,
            "target_type_used": target_type_used,
            "benchmark_return_pct": benchmark_return,
            "excess_return_pct": excess_return,
            "market_beta": spec.market_beta,
            "market_residual_return_pct": (
                float(effective_changes.iloc[-1])
                if target_type_used == "residual_return" else None
            ),
        }

    @staticmethod
    def _barrier_direction(changes, spec: PredictionTargetSpec) -> str:
        upper_hits = changes[changes >= spec.up_threshold_pct]
        lower_hits = changes[changes <= spec.down_threshold_pct]
        if not upper_hits.empty and not lower_hits.empty:
            return "bullish" if upper_hits.index[0] <= lower_hits.index[0] else "bearish"
        if not upper_hits.empty:
            return "bullish"
        if not lower_hits.empty:
            return "bearish"
        return direction_from_return(float(changes.iloc[-1]), spec)

    @staticmethod
    def _safe_float(value, default: float = 0.0) -> float:
        try:
            if value in (None, "", "N/A"):
                return default
            return float(value)
        except (TypeError, ValueError):
            return default


class NewsSnapshotCalibrationBootstrapper:
    """用已归档新闻快照生成新闻面初始校准样本。"""

    AGENT_NAME = "最新新闻分析师"

    def __init__(
        self,
        price_fetcher: Optional[PriceFetcher] = None,
        calibrator: Optional[NewsConfidenceCalibrator] = None,
        advisor: Optional[AgentImprovementAdvisor] = None,
    ):
        self.price_fetcher = price_fetcher or PriceFetcher()
        self.calibrator = calibrator or NewsConfidenceCalibrator()
        self.advisor = advisor or AgentImprovementAdvisor()
        self._analyst = NewsAnalyst.__new__(NewsAnalyst)

    async def run_from_snapshots(
        self,
        snapshots: Iterable[dict],
        timeframe: str = "短期(1周)",
        tolerance_days: int = 10,
    ) -> CalibrationBootstrapReport:
        started = time.monotonic()
        snapshot_list = list(snapshots)
        samples: list[CalibrationSample] = []
        skipped: list[dict] = []

        for idx, snapshot in enumerate(snapshot_list):
            try:
                sample = await self._generate_sample_from_snapshot(
                    snapshot,
                    default_timeframe=timeframe,
                    tolerance_days=tolerance_days,
                )
            except Exception as e:
                skipped.append({
                    "index": idx,
                    "target": snapshot.get("target"),
                    "as_of": snapshot.get("as_of") or snapshot.get("date"),
                    "reason": str(e),
                })
                logger.debug("新闻历史校准样本跳过: %s", e)
                continue

            samples.append(sample)
            self.calibrator.update_from_validation(
                predicted_conf=sample.predicted_confidence,
                was_correct=sample.was_correct,
                **sample.buckets,
            )

        self.calibrator.save()
        stats = self.calibrator.get_calibration_stats()
        signals = [
            signal.to_dict()
            for signal in self.advisor.recommend(self.AGENT_NAME, stats)
        ]
        return CalibrationBootstrapReport(
            agent_name=self.AGENT_NAME,
            total_candidates=len(snapshot_list),
            success_samples=len(samples),
            samples=samples,
            skipped=skipped,
            calibration_stats=stats,
            improvement_signals=signals,
            elapsed_seconds=time.monotonic() - started,
        )

    async def _generate_sample_from_snapshot(
        self,
        snapshot: dict,
        default_timeframe: str,
        tolerance_days: int,
    ) -> CalibrationSample:
        news_data = snapshot.get("news_data") or snapshot.get("data") or snapshot
        target = (
            snapshot.get("target")
            or snapshot.get("symbol")
            or news_data.get("_resolved_symbol")
            or news_data.get("symbol")
        )
        if not target:
            raise ValueError("新闻快照缺少 target/symbol")

        timeframe = snapshot.get("timeframe") or default_timeframe
        as_of_text = snapshot.get("as_of") or snapshot.get("date")
        if not as_of_text:
            raise ValueError("新闻快照缺少 as_of/date")
        as_of = datetime.fromisoformat(str(as_of_text)[:10])

        evidence = self._analyst._build_evidence_packet(news_data, timeframe)
        matrix = evidence.get("decision_matrix") or {}
        constraints = evidence.get("confidence_constraints") or {}
        analysis_result = snapshot.get("analysis_result") or {}
        predicted_direction = (
            snapshot.get("predicted_direction")
            or analysis_result.get("direction")
            or matrix.get("suggested_direction")
            or "neutral"
        )
        predicted_confidence = TechnicalCalibrationBootstrapper._safe_float(
            snapshot.get("predicted_confidence")
            or analysis_result.get("confidence")
            or constraints.get("max_confidence"),
            0.50,
        )
        target_spec = resolve_prediction_target(
            timeframe,
            predicted_direction,
            None,
            predicted_confidence,
            target=target,
        )
        buckets = NewsConfidenceCalibrator.extract_buckets_from_evidence(evidence)

        valid_dt = self._resolve_valid_date(snapshot, as_of, timeframe)
        price_start = await self._resolve_price(
            snapshot, "price_start", target, as_of, "on_or_before",
        )
        if price_start <= 0:
            raise ValueError("起始价格不可用")
        outcome = await TechnicalCalibrationBootstrapper._horizon_window_outcome(
            self.price_fetcher,
            target,
            price_start,
            as_of,
            valid_dt,
            predicted_direction,
            target_spec,
        )

        return CalibrationSample(
            agent_name=self.AGENT_NAME,
            target=target,
            as_of=as_of.strftime("%Y-%m-%d"),
            valid_date=valid_dt.strftime("%Y-%m-%d"),
            predicted_direction=predicted_direction,
            predicted_confidence=round(predicted_confidence, 3),
            actual_direction=outcome["actual_direction"],
            actual_change_pct=round(outcome["actual_change_pct"], 2),
            was_correct=TechnicalCalibrationBootstrapper._direction_correct(
                predicted_direction,
                outcome["effective_fixed_return_pct"],
                outcome["window_max_change_pct"],
                outcome["window_min_change_pct"],
                target_spec,
            ),
            price_start=round(float(outcome.get("price_entry", price_start)), 4),
            price_end=round(float(outcome["price_end"]), 4),
            buckets=buckets,
            evidence_reason=matrix.get("reason", ""),
            validation_mode=outcome["validation_mode"],
            validation_window=outcome["validation_window"],
            window_max_change_pct=round(outcome["window_max_change_pct"], 2),
            window_min_change_pct=round(outcome["window_min_change_pct"], 2),
            prediction_target=target_spec.to_dict(),
            fixed_horizon_return_pct=round(outcome["fixed_horizon_return_pct"], 2),
            effective_fixed_return_pct=round(outcome["effective_fixed_return_pct"], 2),
            target_type_used=outcome["target_type_used"],
            benchmark_return_pct=(
                round(outcome["benchmark_return_pct"], 2)
                if outcome.get("benchmark_return_pct") is not None else None
            ),
            excess_return_pct=(
                round(outcome["excess_return_pct"], 2)
                if outcome.get("excess_return_pct") is not None else None
            ),
        )

    async def _resolve_price(
        self,
        snapshot: dict,
        key: str,
        target: str,
        dt: datetime,
        prefer: str,
        tolerance_days: int = 10,
    ) -> float:
        if snapshot.get(key) is not None:
            return float(snapshot[key])
        return float(
            await self.price_fetcher.fetch_close_near(
                target,
                dt,
                prefer=prefer,
                tolerance_days=tolerance_days,
            )
        )

    @staticmethod
    def _resolve_valid_date(snapshot: dict, as_of: datetime, timeframe: str) -> datetime:
        valid_text = snapshot.get("valid_date") or snapshot.get("valid_until")
        if valid_text:
            return datetime.fromisoformat(str(valid_text)[:10])
        return as_of + timedelta(days=default_target_spec(timeframe).horizon_calendar_days)


class _PointInTimeSnapshotBootstrapperBase:
    """基本面/行业/宏观 point-in-time 快照回放基类。"""

    AGENT_NAME = "unknown"

    def __init__(
        self,
        price_fetcher: Optional[PriceFetcher] = None,
        advisor: Optional[AgentImprovementAdvisor] = None,
    ):
        self.price_fetcher = price_fetcher or PriceFetcher()
        self.advisor = advisor or AgentImprovementAdvisor()

    async def run_from_snapshots(
        self,
        snapshots: Iterable[dict],
        timeframe: str = "短期(1周)",
        tolerance_days: int = 10,
    ) -> CalibrationBootstrapReport:
        started = time.monotonic()
        snapshot_list = list(snapshots)
        samples: list[CalibrationSample] = []
        skipped: list[dict] = []

        for idx, snapshot in enumerate(snapshot_list):
            try:
                sample = await self._generate_sample_from_snapshot(
                    snapshot,
                    default_timeframe=timeframe,
                    tolerance_days=tolerance_days,
                )
            except Exception as e:
                skipped.append({
                    "index": idx,
                    "target": snapshot.get("target") or snapshot.get("symbol"),
                    "as_of": snapshot.get("as_of") or snapshot.get("date"),
                    "reason": str(e),
                })
                logger.debug("%s point-in-time 样本跳过: %s", self.AGENT_NAME, e)
                continue

            samples.append(sample)
            self._update_calibrator(sample)

        self._save_calibrator()
        stats = self._calibration_stats()
        signals = [
            signal.to_dict()
            for signal in self.advisor.recommend(self.AGENT_NAME, stats)
        ]
        return CalibrationBootstrapReport(
            agent_name=self.AGENT_NAME,
            total_candidates=len(snapshot_list),
            success_samples=len(samples),
            samples=samples,
            skipped=skipped,
            calibration_stats=stats,
            improvement_signals=signals,
            elapsed_seconds=time.monotonic() - started,
        )

    async def _generate_sample_from_snapshot(
        self,
        snapshot: dict,
        default_timeframe: str,
        tolerance_days: int,
    ) -> CalibrationSample:
        raise NotImplementedError

    def _update_calibrator(self, sample: CalibrationSample) -> None:
        return None

    def _save_calibrator(self) -> None:
        calibrator = getattr(self, "calibrator", None)
        if calibrator and hasattr(calibrator, "save"):
            calibrator.save()

    def _calibration_stats(self) -> dict:
        calibrator = getattr(self, "calibrator", None)
        if calibrator and hasattr(calibrator, "get_calibration_stats"):
            return calibrator.get_calibration_stats()
        return {}

    async def _price_pair(
        self,
        snapshot: dict,
        target: str,
        as_of: datetime,
        valid_dt: datetime,
        tolerance_days: int,
    ) -> tuple[float, float]:
        if snapshot.get("price_start") is not None:
            price_start = float(snapshot["price_start"])
        else:
            price_start = float(
                await self.price_fetcher.fetch_close_near(
                    target,
                    as_of,
                    prefer="on_or_before",
                    tolerance_days=tolerance_days,
                )
            )
        if snapshot.get("price_end") is not None:
            price_end = float(snapshot["price_end"])
        else:
            price_end = float(
                await self.price_fetcher.fetch_close_near(
                    target,
                    valid_dt,
                    prefer="on_or_after",
                    tolerance_days=tolerance_days,
                )
            )
        if price_start <= 0:
            raise ValueError("起始价格不可用")
        return price_start, price_end

    async def _price_start(
        self,
        snapshot: dict,
        target: str,
        as_of: datetime,
        tolerance_days: int,
    ) -> float:
        if snapshot.get("price_start") is not None:
            return float(snapshot["price_start"])
        return float(
            await self.price_fetcher.fetch_close_near(
                target,
                as_of,
                prefer="on_or_before",
                tolerance_days=tolerance_days,
            )
        )

    async def _price_window_outcome(
        self,
        snapshot: dict,
        target: str,
        as_of: datetime,
        valid_dt: datetime,
        predicted_direction: str,
        tolerance_days: int,
        target_spec: PredictionTargetSpec | dict | None = None,
    ) -> tuple[float, dict]:
        spec = PredictionTargetSpec.from_dict(target_spec)
        price_start = await self._price_start(snapshot, target, as_of, tolerance_days)
        if price_start <= 0:
            raise ValueError("起始价格不可用")
        outcome = await TechnicalCalibrationBootstrapper._horizon_window_outcome(
            self.price_fetcher,
            target,
            price_start,
            as_of,
            valid_dt,
            predicted_direction,
            spec,
        )
        return price_start, outcome

    @staticmethod
    def _snapshot_data(snapshot: dict) -> dict:
        data = snapshot.get("data")
        if isinstance(data, dict):
            return data
        for key in ("fundamental_data", "industry_data", "macro_data"):
            if isinstance(snapshot.get(key), dict):
                return snapshot[key]
        return snapshot

    @staticmethod
    def _target(snapshot: dict, data: dict) -> str:
        target = (
            snapshot.get("target")
            or snapshot.get("symbol")
            or data.get("_resolved_symbol")
            or data.get("symbol")
        )
        if not target:
            raise ValueError("快照缺少 target/symbol")
        return str(target)

    @staticmethod
    def _as_of(snapshot: dict) -> datetime:
        text = snapshot.get("as_of") or snapshot.get("date")
        if not text:
            raise ValueError("快照缺少 as_of/date")
        return datetime.fromisoformat(str(text)[:10])

    @staticmethod
    def _valid_date(snapshot: dict, as_of: datetime, timeframe: str) -> datetime:
        text = snapshot.get("valid_date") or snapshot.get("valid_until")
        if text:
            return datetime.fromisoformat(str(text)[:10])
        return as_of + timedelta(days=default_target_spec(timeframe).horizon_calendar_days)

    @staticmethod
    def _predicted_direction(snapshot: dict, matrix: dict) -> str:
        analysis_result = snapshot.get("analysis_result") or {}
        return (
            snapshot.get("predicted_direction")
            or analysis_result.get("direction")
            or matrix.get("suggested_direction")
            or "neutral"
        )

    @staticmethod
    def _predicted_confidence(snapshot: dict, constraints: dict, default: float = 0.50) -> float:
        analysis_result = snapshot.get("analysis_result") or {}
        return TechnicalCalibrationBootstrapper._safe_float(
            snapshot.get("predicted_confidence")
            or analysis_result.get("confidence")
            or constraints.get("max_confidence")
            or constraints.get("ceiling"),
            default,
        )

    @staticmethod
    def _actual_change(price_start: float, price_end: float) -> float:
        return (price_end / price_start - 1) * 100


class FundamentalSnapshotCalibrationBootstrapper(_PointInTimeSnapshotBootstrapperBase):
    """用公司前景 point-in-time 快照生成历史样本。"""

    AGENT_NAME = "公司前景分析师"

    def __init__(
        self,
        price_fetcher: Optional[PriceFetcher] = None,
        calibrator: Optional[FundamentalConfidenceCalibrator] = None,
        advisor: Optional[AgentImprovementAdvisor] = None,
    ):
        super().__init__(price_fetcher=price_fetcher, advisor=advisor)
        self.calibrator = calibrator or FundamentalConfidenceCalibrator()
        self._analyst = FundamentalAnalyst.__new__(FundamentalAnalyst)

    async def _generate_sample_from_snapshot(
        self,
        snapshot: dict,
        default_timeframe: str,
        tolerance_days: int,
    ) -> CalibrationSample:
        data = self._snapshot_data(snapshot)
        timeframe = snapshot.get("timeframe") or default_timeframe
        target = self._target(snapshot, data)
        as_of = self._as_of(snapshot)
        valid_dt = self._valid_date(snapshot, as_of, timeframe)

        signals = self._analyst._derive_fundamental_signals(data, timeframe)
        matrix = signals["decision_matrix"]
        constraints = signals["confidence_model"]
        predicted_direction = self._predicted_direction(snapshot, matrix)
        predicted_confidence = self._predicted_confidence(snapshot, constraints, 0.50)
        target_spec = resolve_prediction_target(
            timeframe,
            predicted_direction,
            None,
            predicted_confidence,
            target=target,
        )
        price_start, outcome = await self._price_window_outcome(
            snapshot, target, as_of, valid_dt, predicted_direction, tolerance_days,
            target_spec,
        )
        buckets = self._buckets(data, signals)

        return CalibrationSample(
            agent_name=self.AGENT_NAME,
            target=target,
            as_of=as_of.strftime("%Y-%m-%d"),
            valid_date=valid_dt.strftime("%Y-%m-%d"),
            predicted_direction=predicted_direction,
            predicted_confidence=round(predicted_confidence, 3),
            actual_direction=outcome["actual_direction"],
            actual_change_pct=round(outcome["actual_change_pct"], 2),
            was_correct=TechnicalCalibrationBootstrapper._direction_correct(
                predicted_direction,
                outcome["effective_fixed_return_pct"],
                outcome["window_max_change_pct"],
                outcome["window_min_change_pct"],
                target_spec,
            ),
            price_start=round(price_start, 4),
            price_end=round(outcome["price_end"], 4),
            buckets=buckets,
            evidence_reason=matrix.get("reason", ""),
            validation_mode=outcome["validation_mode"],
            validation_window=outcome["validation_window"],
            window_max_change_pct=round(outcome["window_max_change_pct"], 2),
            window_min_change_pct=round(outcome["window_min_change_pct"], 2),
            **_outcome_sample_fields(outcome, target_spec),
        )

    def _update_calibrator(self, sample: CalibrationSample) -> None:
        self.calibrator.update_from_validation(
            predicted_conf=sample.predicted_confidence,
            was_correct=sample.was_correct,
            data_quality_bucket=sample.buckets.get("data_quality_bucket", "medium"),
            scorecard_rating=sample.buckets.get("scorecard_rating_bucket"),
            pe_percentile=sample.buckets.get("_pe_percentile"),
            actual_return_pct=sample.actual_change_pct,
        )

    @staticmethod
    def _buckets(data: dict, signals: dict) -> dict:
        matrix = signals["decision_matrix"]
        constraints = signals["confidence_model"]
        pe_pct = FundamentalSnapshotCalibrationBootstrapper._safe_float(
            (data.get("valuation_analysis") or {}).get("pe_percentile_3yr"),
            None,
        )
        return {
            "data_quality_bucket": constraints.get("quality_bucket", "medium"),
            "scorecard_rating_bucket": (
                (data.get("quality_scorecard") or {}).get("rating") or "unknown"
            ),
            "quality_bucket": matrix.get("quality_bucket", "unknown"),
            "valuation_bucket": matrix.get("valuation_bucket", "unknown"),
            "pe_percentile_bucket": FundamentalSnapshotCalibrationBootstrapper._pe_bucket(pe_pct),
            "market_bucket": data.get("market") or data.get("_market") or "unknown",
            "_pe_percentile": pe_pct,
        }

    @staticmethod
    def _pe_bucket(value: Optional[float]) -> str:
        if value is None:
            return "unknown"
        if value < 0.10:
            return "<0.1"
        if value < 0.30:
            return "0.1-0.3"
        if value <= 0.70:
            return "0.3-0.7"
        if value <= 0.90:
            return "0.7-0.9"
        return ">0.9"

    @staticmethod
    def _safe_float(value, default=None):
        try:
            if value in (None, "", "N/A"):
                return default
            return float(value)
        except (TypeError, ValueError):
            return default


class IndustrySnapshotCalibrationBootstrapper(_PointInTimeSnapshotBootstrapperBase):
    """用行业对比 point-in-time 快照生成历史样本。"""

    AGENT_NAME = "行业对比分析师"

    def __init__(
        self,
        price_fetcher: Optional[PriceFetcher] = None,
        calibrator: Optional[IndustryConfidenceCalibrator] = None,
        advisor: Optional[AgentImprovementAdvisor] = None,
    ):
        super().__init__(price_fetcher=price_fetcher, advisor=advisor)
        self.calibrator = calibrator or IndustryConfidenceCalibrator()
        self._analyst = IndustryAnalyst.__new__(IndustryAnalyst)

    async def _generate_sample_from_snapshot(
        self,
        snapshot: dict,
        default_timeframe: str,
        tolerance_days: int,
    ) -> CalibrationSample:
        data = self._snapshot_data(snapshot)
        timeframe = snapshot.get("timeframe") or default_timeframe
        target = self._target(snapshot, data)
        as_of = self._as_of(snapshot)
        valid_dt = self._valid_date(snapshot, as_of, timeframe)

        signals = self._analyst._derive_industry_signals(data, timeframe)
        matrix = signals["decision_matrix"]
        constraints = signals["confidence_model"]
        predicted_direction = self._predicted_direction(snapshot, matrix)
        predicted_confidence = self._predicted_confidence(snapshot, constraints, 0.45)
        target_spec = resolve_prediction_target(
            timeframe,
            predicted_direction,
            None,
            predicted_confidence,
            target=target,
        )
        price_start, outcome = await self._price_window_outcome(
            snapshot, target, as_of, valid_dt, predicted_direction, tolerance_days,
            target_spec,
        )
        buckets = self._buckets(data, signals)

        return CalibrationSample(
            agent_name=self.AGENT_NAME,
            target=target,
            as_of=as_of.strftime("%Y-%m-%d"),
            valid_date=valid_dt.strftime("%Y-%m-%d"),
            predicted_direction=predicted_direction,
            predicted_confidence=round(predicted_confidence, 3),
            actual_direction=outcome["actual_direction"],
            actual_change_pct=round(outcome["actual_change_pct"], 2),
            was_correct=TechnicalCalibrationBootstrapper._direction_correct(
                predicted_direction,
                outcome["effective_fixed_return_pct"],
                outcome["window_max_change_pct"],
                outcome["window_min_change_pct"],
                target_spec,
            ),
            price_start=round(price_start, 4),
            price_end=round(outcome["price_end"], 4),
            buckets=buckets,
            evidence_reason=matrix.get("reason", ""),
            validation_mode=outcome["validation_mode"],
            validation_window=outcome["validation_window"],
            window_max_change_pct=round(outcome["window_max_change_pct"], 2),
            window_min_change_pct=round(outcome["window_min_change_pct"], 2),
            **_outcome_sample_fields(outcome, target_spec),
        )

    def _update_calibrator(self, sample: CalibrationSample) -> None:
        self.calibrator.update_from_validation(
            predicted_conf=sample.predicted_confidence,
            was_correct=sample.was_correct,
            industry=sample.buckets.get("industry"),
            data_quality_level=sample.buckets.get("data_quality_level", "reference_only"),
        )

    @staticmethod
    def _buckets(data: dict, signals: dict) -> dict:
        matrix = signals["decision_matrix"]
        constraints = signals["confidence_model"]
        return {
            "industry": data.get("industry_name") or data.get("industry") or "unknown",
            "market_bucket": data.get("market") or data.get("_market") or "unknown",
            "data_quality_level": constraints.get("quality_level", "reference_only"),
            "relative_value_bucket": matrix.get("relative_value_bucket", "unknown"),
            "industry_trend_bucket": matrix.get("industry_trend_bucket", "neutral"),
            "source_type_bucket": data.get("data_source") or "unknown",
        }


class MacroSnapshotCalibrationBootstrapper(_PointInTimeSnapshotBootstrapperBase):
    """用国际形势 point-in-time 快照生成历史样本。"""

    AGENT_NAME = "国际形势分析师"

    def __init__(
        self,
        price_fetcher: Optional[PriceFetcher] = None,
        calibrator: Optional[MacroConfidenceCalibrator] = None,
        advisor: Optional[AgentImprovementAdvisor] = None,
    ):
        super().__init__(price_fetcher=price_fetcher, advisor=advisor)
        self.calibrator = calibrator or MacroConfidenceCalibrator()
        self._analyst = MacroAnalyst.__new__(MacroAnalyst)

    async def _generate_sample_from_snapshot(
        self,
        snapshot: dict,
        default_timeframe: str,
        tolerance_days: int,
    ) -> CalibrationSample:
        data = self._snapshot_data(snapshot)
        timeframe = snapshot.get("timeframe") or default_timeframe
        target = self._target(snapshot, data)
        as_of = self._as_of(snapshot)
        valid_dt = self._valid_date(snapshot, as_of, timeframe)
        market = snapshot.get("market") or data.get("_market") or data.get("market") or ""
        stock_ctx = (
            snapshot.get("stock_context")
            or data.get("_stock_context")
            or data.get("stock_context")
            or {}
        )

        signals = self._analyst._derive_macro_signals(data, stock_ctx, market, timeframe)
        matrix = signals["decision_matrix"]
        constraints = signals["confidence_model"]
        predicted_direction = self._predicted_direction(snapshot, matrix)
        predicted_confidence = self._predicted_confidence(snapshot, constraints, 0.50)
        target_spec = resolve_prediction_target(
            timeframe,
            predicted_direction,
            None,
            predicted_confidence,
            target=target,
        )
        price_start, outcome = await self._price_window_outcome(
            snapshot, target, as_of, valid_dt, predicted_direction, tolerance_days,
            target_spec,
        )
        buckets = self._buckets(data, stock_ctx, market, signals)

        return CalibrationSample(
            agent_name=self.AGENT_NAME,
            target=target,
            as_of=as_of.strftime("%Y-%m-%d"),
            valid_date=valid_dt.strftime("%Y-%m-%d"),
            predicted_direction=predicted_direction,
            predicted_confidence=round(predicted_confidence, 3),
            actual_direction=outcome["actual_direction"],
            actual_change_pct=round(outcome["actual_change_pct"], 2),
            was_correct=TechnicalCalibrationBootstrapper._direction_correct(
                predicted_direction,
                outcome["effective_fixed_return_pct"],
                outcome["window_max_change_pct"],
                outcome["window_min_change_pct"],
                target_spec,
            ),
            price_start=round(price_start, 4),
            price_end=round(outcome["price_end"], 4),
            buckets=buckets,
            evidence_reason=matrix.get("reason", ""),
            validation_mode=outcome["validation_mode"],
            validation_window=outcome["validation_window"],
            window_max_change_pct=round(outcome["window_max_change_pct"], 2),
            window_min_change_pct=round(outcome["window_min_change_pct"], 2),
            **_outcome_sample_fields(outcome, target_spec),
        )

    def _update_calibrator(self, sample: CalibrationSample) -> None:
        self.calibrator.update_from_validation(
            predicted_conf=sample.predicted_confidence,
            was_correct=sample.was_correct,
            market=sample.buckets.get("market_bucket", ""),
            sector=sample.buckets.get("sector", ""),
            data_quality_level=sample.buckets.get("data_quality_level", "mixed"),
        )

    @staticmethod
    def _buckets(data: dict, stock_ctx: dict, market: str, signals: dict) -> dict:
        matrix = signals["decision_matrix"]
        constraints = signals["confidence_model"]
        return {
            "market_bucket": market or "unknown",
            "sector": stock_ctx.get("inferred_sector") or matrix.get("sector") or "unknown",
            "data_quality_level": constraints.get("data_quality_level", "mixed"),
            "macro_regime_bucket": matrix.get("macro_regime", "mixed"),
            "source_type_bucket": data.get("data_source") or "unknown",
        }
