"""
回测引擎

在历史区间内滚动执行分析，对比预测 vs 实际结果，
输出方向准确率、幅度命中率、置信度校准等统计。
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from src.core.llm_client import LLMClient, create_llm_client
from src.core.orchestrator import Orchestrator
from src.agents.technical_analyst import TechnicalAnalyst
from src.agents.aggregator import Aggregator
from src.data.price_fetcher import PriceFetcher

logger = logging.getLogger(__name__)

AGENT_TECH = "近期股价分析师"


@dataclass
class BacktestConfig:
    """回测配置"""
    target: str
    start_date: str                    # "2025-01-01"
    end_date: str                      # "2026-06-30"
    timeframe: str = "短期(1周)"
    interval_days: int = 7             # 每隔几天做一次预测
    agents: list[str] = field(default_factory=lambda: [
        "近期股价分析师", "公司前景分析师"
    ])


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
            "results": [
                {
                    "date": r.date,
                    "predicted": f"{r.predicted_direction} {r.predicted_min}~{r.predicted_max}%",
                    "actual": f"{r.actual_direction} {r.actual_change_pct:+.2f}%",
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
            "=" * 50,
        ]
        return "\n".join(lines)


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


class Backtester:
    """回测引擎"""

    def __init__(self, llm: Optional[LLMClient] = None):
        self.llm = llm or create_llm_client()

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

        if AGENT_TECH in config.agents:
            tech = HistoricalTechnicalAnalyst(self.llm, price_data, bt_date)
            orchestrator.register(tech)
            active.append(AGENT_TECH)

        unsupported = [name for name in config.agents if name != AGENT_TECH]
        if unsupported:
            logger.warning(
                "以下 Agent 暂无历史快照回放能力，回测中跳过: %s",
                ", ".join(unsupported),
            )

        if not active:
            raise ValueError("没有可历史回放的 Agent。当前回测仅支持近期股价分析师")

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
        # 起点是回测日当时可见的收盘价；终点是预测周期结束后附近交易日收盘价。
        price_start = price_data.price_current

        valid_date = bt_date + timedelta(days=self._horizon_days(config.timeframe))
        price_end = await pf.fetch_close_near(
            config.target,
            valid_date,
            prefer="on_or_after",
            tolerance_days=10,
        )

        actual_change = (price_end / price_start - 1) * 100 if price_start > 0 else 0

        # 判断方向
        pred_dir = report.direction.value
        if pred_dir == "bullish":
            dir_correct = actual_change > 0.5
        elif pred_dir == "bearish":
            dir_correct = actual_change < -0.5
        else:
            dir_correct = abs(actual_change) <= 1.0

        actual_dir = "bullish" if actual_change > 0.5 else ("bearish" if actual_change < -0.5 else "neutral")

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
        )

    @staticmethod
    def _horizon_days(timeframe: str) -> int:
        """把预测周期映射为自然日 horizon。"""
        if "周" in timeframe or "短期" in timeframe:
            return 7
        if "月" in timeframe or "中期" in timeframe:
            return 30
        if "季" in timeframe or "季度" in timeframe or "长期" in timeframe:
            return 90
        return 7
