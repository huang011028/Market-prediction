"""Portfolio-level evaluation with market-specific execution costs."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np

from src.core.experiment_manifest import detect_experiment_source, write_experiment_manifest


@dataclass(frozen=True)
class MarketExecutionRules:
    market: str
    commission_bps: float
    slippage_bps: float
    spread_bps: float
    sell_tax_bps: float
    can_short: bool
    t_plus_one: bool
    price_limit_note: str


MARKET_RULES = {
    "A": MarketExecutionRules("A", 3.0, 8.0, 4.0, 5.0, False, True, "涨跌停时可能无法按计划成交"),
    "HK": MarketExecutionRules("HK", 8.0, 10.0, 8.0, 13.0, False, False, "含印花税近似，未建模整手约束"),
    "US": MarketExecutionRules("US", 1.0, 5.0, 3.0, 0.0, True, False, "做空需额外借券成本和可借性"),
}


@dataclass
class PortfolioBacktestConfig:
    prediction_paths: list[str]
    market: str = "A"
    model_name: Optional[str] = None
    horizon_trading_days: int = 5
    top_k: int = 10
    bottom_k: int = 0
    allow_short: bool = False
    min_edge_score: float = 0.10
    max_position_weight: float = 0.20
    volatility_weighted: bool = True
    initial_capital: float = 1_000_000.0
    extra_borrow_cost_bps: float = 0.0
    allow_overlapping_horizons: bool = False
    min_avg_traded_value: float = 0.0
    max_participation_rate: float = 0.05
    impact_coefficient_bps: float = 15.0


@dataclass
class PortfolioPeriod:
    as_of: str
    gross_return_pct: float
    net_return_pct: float
    benchmark_return_pct: float
    transaction_cost_pct: float
    turnover: float
    positions: int
    long_positions: int
    short_positions: int
    gross_exposure: float
    equity: float
    drawdown_pct: float
    holdings: list[dict] = field(default_factory=list)


@dataclass
class PortfolioBacktestReport:
    generated_at: str
    config: dict
    execution_rules: dict
    metrics: dict
    benchmark_metrics: dict
    excess_metrics: dict
    periods: list[PortfolioPeriod]
    warnings: list[str]
    input_rows: int
    skipped_rows: int
    output_path: str = ""
    manifest_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["periods"] = [asdict(period) for period in self.periods]
        return payload


class PortfolioBacktester:
    def run(
        self,
        config: PortfolioBacktestConfig,
        output_dir: Optional[str | Path] = None,
    ) -> PortfolioBacktestReport:
        rules = MARKET_RULES.get(config.market.upper())
        if rules is None:
            raise ValueError(f"不支持的市场: {config.market}")
        allow_short = bool(config.allow_short and rules.can_short)
        rows, skipped = self._load_predictions(config)
        if not rows:
            raise ValueError("没有可用于组合回测的 OOF 预测")

        grouped: dict[str, list[dict]] = {}
        for row in rows:
            grouped.setdefault(str(row["as_of"]), []).append(row)
        previous_weights: dict[str, float] = {}
        equity = float(config.initial_capital)
        peak = equity
        periods: list[PortfolioPeriod] = []

        selected_dates, overlap_skipped = self._non_overlapping_dates(
            sorted(grouped),
            config.horizon_trading_days,
            config.allow_overlapping_horizons,
        )
        max_participation = 0.0
        capacity_clipped_orders = 0
        for as_of in selected_dates:
            group = grouped[as_of]
            weights, selected = self._positions(group, config, allow_short)
            weights, clipped = self._apply_capacity_limits(
                weights,
                selected,
                equity,
                config.max_participation_rate,
            )
            capacity_clipped_orders += clipped
            selected = [row for row in selected if str(row["symbol"]) in weights]
            turnover = sum(
                abs(weights.get(symbol, 0.0) - previous_weights.get(symbol, 0.0))
                for symbol in set(weights) | set(previous_weights)
            )
            trading_bps = rules.commission_bps + rules.slippage_bps + rules.spread_bps
            cost_pct = turnover * trading_bps / 100.0
            sells = sum(
                max(0.0, previous_weights.get(symbol, 0.0) - weights.get(symbol, 0.0))
                for symbol in set(weights) | set(previous_weights)
            )
            cost_pct += sells * rules.sell_tax_bps / 100.0
            selected_by_symbol = {str(row["symbol"]): row for row in selected}
            for symbol in set(weights) | set(previous_weights):
                delta = abs(weights.get(symbol, 0.0) - previous_weights.get(symbol, 0.0))
                if delta <= 0:
                    continue
                avg_value = float(
                    (selected_by_symbol.get(symbol) or {}).get("avg_traded_value_20d") or 0.0
                )
                if avg_value <= 0:
                    continue
                order_value = delta * equity
                participation = order_value / avg_value
                max_participation = max(max_participation, participation)
                normalized = participation / max(float(config.max_participation_rate), 1e-6)
                impact_bps = float(config.impact_coefficient_bps) * math.sqrt(max(0.0, normalized))
                cost_pct += delta * impact_bps / 100.0
            if allow_short:
                short_exposure = sum(abs(weight) for weight in weights.values() if weight < 0)
                cost_pct += short_exposure * config.extra_borrow_cost_bps / 100.0

            gross_pct = 0.0
            benchmark_values = [
                float(row["actual_benchmark_return_pct"])
                for row in group
                if row.get("actual_benchmark_return_pct") is not None
            ]
            holdings = []
            for row in selected:
                symbol = str(row["symbol"])
                weight = weights.get(symbol, 0.0)
                actual = row.get("actual_absolute_return_pct")
                if actual is None:
                    actual = row.get("actual_return_pct", 0.0)
                actual = float(actual or 0.0)
                gross_pct += weight * actual
                holdings.append({
                    "symbol": symbol,
                    "weight": round(weight, 6),
                    "expected_return_pct": row.get("expected_return_pct"),
                    "actual_return_pct": actual,
                    "direction": row.get("direction"),
                })
            net_pct = gross_pct - cost_pct
            equity *= 1.0 + net_pct / 100.0
            peak = max(peak, equity)
            drawdown = (equity / peak - 1.0) * 100 if peak else 0.0
            periods.append(PortfolioPeriod(
                as_of=as_of,
                gross_return_pct=round(gross_pct, 6),
                net_return_pct=round(net_pct, 6),
                benchmark_return_pct=round(float(np.mean(benchmark_values)) if benchmark_values else 0.0, 6),
                transaction_cost_pct=round(cost_pct, 6),
                turnover=round(turnover, 6),
                positions=len(weights),
                long_positions=sum(weight > 0 for weight in weights.values()),
                short_positions=sum(weight < 0 for weight in weights.values()),
                gross_exposure=round(sum(abs(weight) for weight in weights.values()), 6),
                equity=round(equity, 2),
                drawdown_pct=round(drawdown, 6),
                holdings=holdings,
            ))
            previous_weights = weights

        net_returns = [period.net_return_pct / 100.0 for period in periods]
        benchmark_returns = [period.benchmark_return_pct / 100.0 for period in periods]
        excess_returns = [
            (period.net_return_pct - period.benchmark_return_pct) / 100.0
            for period in periods
        ]
        metrics = _portfolio_metrics(
            net_returns,
            periods,
            config.initial_capital,
            config.horizon_trading_days,
        )
        benchmark_metrics = _return_metrics(benchmark_returns, config.horizon_trading_days)
        excess_metrics = _return_metrics(excess_returns, config.horizon_trading_days)
        excess_metrics["information_ratio"] = excess_metrics.pop("sharpe")
        excess_metrics.pop("sortino", None)
        warnings = [rules.price_limit_note]
        if config.allow_short and not rules.can_short:
            warnings.append(f"{rules.market} 市场配置不允许做空，bottom_k 已忽略")
        if any(row.get("actual_absolute_return_pct") is None for row in rows):
            warnings.append("部分样本缺少绝对收益，已回退使用超额收益")
        warnings.append("成本为保守近似；真实成交仍受流动性、整手和停牌影响")
        if overlap_skipped:
            warnings.append(f"为避免重叠收益重复复利，已跳过 {overlap_skipped} 个重叠调仓日期")
        if max_participation > config.max_participation_rate + 1e-9:
            warnings.append(
                f"最大成交参与率 {max_participation:.1%} 超过配置 {config.max_participation_rate:.1%}"
            )
        if capacity_clipped_orders:
            warnings.append(
                f"已有 {capacity_clipped_orders} 个目标仓位按最大成交参与率硬性缩减，未使用资金保留为现金"
            )
        report = PortfolioBacktestReport(
            generated_at=datetime.now().isoformat(),
            config={**asdict(config), "allow_short_effective": allow_short},
            execution_rules=asdict(rules),
            metrics=metrics,
            benchmark_metrics=benchmark_metrics,
            excess_metrics=excess_metrics,
            periods=periods,
            warnings=warnings,
            input_rows=len(rows),
            skipped_rows=skipped,
        )
        if output_dir:
            root = Path(output_dir)
        else:
            root = Path("output") / "portfolio_backtest" / datetime.now().strftime("%Y%m%d_%H%M%S")
        root.mkdir(parents=True, exist_ok=True)
        path = root / "portfolio_backtest.json"
        report.output_path = str(path)
        report.metrics["overlap_dates_skipped"] = overlap_skipped
        report.metrics["max_participation_rate"] = round(max_participation, 6)
        report.metrics["capacity_clipped_orders"] = capacity_clipped_orders
        manifest = write_experiment_manifest(
            root,
            experiment_id=root.name,
            kind="portfolio_backtest",
            source_type=detect_experiment_source(),
            config=asdict(config),
            artifacts={"report": str(path)},
            metrics={
                "periods": len(periods),
                "net_return_pct": metrics.get("total_return_pct"),
                "excess_return_pct": excess_metrics.get("total_return_pct"),
            },
            project_root=Path(__file__).resolve().parents[2],
        )
        report.manifest_path = manifest
        path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    @staticmethod
    def _load_predictions(config: PortfolioBacktestConfig) -> tuple[list[dict], int]:
        values: dict[tuple[str, str], dict] = {}
        skipped = 0
        for raw_path in config.prediction_paths:
            path = Path(raw_path)
            if not path.exists():
                skipped += 1
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except Exception:
                    skipped += 1
                    continue
                if config.model_name and row.get("model") != config.model_name:
                    continue
                if str(row.get("market") or "").upper() != config.market.upper():
                    continue
                if row.get("actual_return_pct") is None:
                    skipped += 1
                    continue
                values[(str(row.get("as_of")), str(row.get("symbol")))] = row
        return list(values.values()), skipped

    @staticmethod
    def _positions(group: list[dict], config: PortfolioBacktestConfig, allow_short: bool):
        scored = []
        for row in group:
            avg_traded_value = float(row.get("avg_traded_value_20d") or 0.0)
            if config.min_avg_traded_value and avg_traded_value < config.min_avg_traded_value:
                continue
            up = float(row.get("prob_up", 0.0) or 0.0)
            down = float(row.get("prob_down", 0.0) or 0.0)
            no_edge = float(row.get("prob_no_edge", 0.0) or 0.0)
            expected = float(row.get("expected_return_pct", 0.0) or 0.0)
            edge = abs(up - down) * max(0.0, 1.0 - no_edge)
            if edge < config.min_edge_score:
                continue
            item = dict(row)
            item["_score"] = expected
            item["_edge"] = edge
            scored.append(item)
        longs = sorted((row for row in scored if row["_score"] > 0), key=lambda row: row["_score"], reverse=True)[:config.top_k]
        shorts = []
        if allow_short and config.bottom_k > 0:
            shorts = sorted((row for row in scored if row["_score"] < 0), key=lambda row: row["_score"])[:config.bottom_k]
        selected = longs + shorts
        if not selected:
            return {}, []

        long_budget = 0.5 if shorts else 1.0
        short_budget = 0.5 if shorts else 0.0
        weights = {}
        weights.update(_side_weights(longs, long_budget, config, sign=1.0))
        weights.update(_side_weights(shorts, short_budget, config, sign=-1.0))
        return weights, selected

    @staticmethod
    def _non_overlapping_dates(
        dates: list[str],
        horizon_trading_days: int,
        allow_overlapping: bool,
    ) -> tuple[list[str], int]:
        if allow_overlapping or len(dates) <= 1:
            return dates, 0
        selected: list[str] = []
        skipped = 0
        last = None
        for value in dates:
            current = np.datetime64(value[:10], "D")
            if last is not None and int(np.busday_count(last, current)) < max(1, horizon_trading_days):
                skipped += 1
                continue
            selected.append(value)
            last = current
        return selected, skipped

    @staticmethod
    def _apply_capacity_limits(
        weights: dict[str, float],
        selected: list[dict],
        equity: float,
        max_participation_rate: float,
    ) -> tuple[dict[str, float], int]:
        """Hard-cap target holdings to a conservative fraction of 20-day traded value."""
        selected_by_symbol = {str(row["symbol"]): row for row in selected}
        constrained: dict[str, float] = {}
        clipped = 0
        for symbol, weight in weights.items():
            avg_value = float(
                (selected_by_symbol.get(symbol) or {}).get("avg_traded_value_20d") or 0.0
            )
            if avg_value <= 0 or equity <= 0:
                constrained[symbol] = weight
                continue
            capacity_weight = avg_value * max(float(max_participation_rate), 0.0) / equity
            allowed = min(abs(weight), max(0.0, capacity_weight))
            if allowed + 1e-12 < abs(weight):
                clipped += 1
            if allowed > 0:
                constrained[symbol] = math.copysign(allowed, weight)
        return constrained, clipped


def _side_weights(rows: list[dict], budget: float, config: PortfolioBacktestConfig, sign: float) -> dict[str, float]:
    if not rows or budget <= 0:
        return {}
    raw = []
    for row in rows:
        volatility = float(row.get("daily_volatility_pct") or 0.0)
        score = 1.0 / max(volatility, 0.5) if config.volatility_weighted else 1.0
        raw.append(score)
    total = sum(raw) or 1.0
    values = {}
    for row, score in zip(rows, raw):
        weight = min(config.max_position_weight, budget * score / total)
        values[str(row["symbol"])] = sign * weight
    used = sum(abs(value) for value in values.values())
    if used > 0 and used < budget:
        scale = min(budget / used, 1.0 / max(abs(value) for value in values.values()) * config.max_position_weight)
        values = {key: value * scale for key, value in values.items()}
    return values


def _portfolio_metrics(returns: list[float], periods: list[PortfolioPeriod], initial: float, horizon: int) -> dict[str, float]:
    metrics = _return_metrics(returns, horizon)
    metrics.update({
        "initial_capital": round(initial, 2),
        "final_equity": periods[-1].equity if periods else round(initial, 2),
        "max_drawdown_pct": round(min((period.drawdown_pct for period in periods), default=0.0), 6),
        "avg_turnover": round(float(np.mean([period.turnover for period in periods])) if periods else 0.0, 6),
        "total_transaction_cost_pct": round(sum(period.transaction_cost_pct for period in periods), 6),
        "avg_positions": round(float(np.mean([period.positions for period in periods])) if periods else 0.0, 3),
        "avg_gross_exposure": round(
            float(np.mean([period.gross_exposure for period in periods])) if periods else 0.0,
            6,
        ),
    })
    return metrics


def _return_metrics(returns: list[float], horizon: int) -> dict[str, float]:
    if not returns:
        return {"periods": 0, "total_return_pct": 0.0, "annualized_return_pct": 0.0, "sharpe": 0.0, "sortino": 0.0}
    values = np.asarray(returns, dtype=float)
    periods_per_year = 252 / max(1, horizon)
    total = float(np.prod(1.0 + values) - 1.0)
    annualized = (1.0 + total) ** (periods_per_year / len(values)) - 1.0 if total > -1 else -1.0
    std = float(values.std(ddof=1)) if len(values) > 1 else 0.0
    downside = values[values < 0]
    downside_std = float(downside.std(ddof=1)) if len(downside) > 1 else 0.0
    sharpe = float(values.mean() / std * math.sqrt(periods_per_year)) if std > 0 else 0.0
    sortino = float(values.mean() / downside_std * math.sqrt(periods_per_year)) if downside_std > 0 else 0.0
    return {
        "periods": len(values),
        "total_return_pct": round(total * 100, 6),
        "annualized_return_pct": round(annualized * 100, 6),
        "sharpe": round(sharpe, 6),
        "sortino": round(sortino, 6),
        "win_rate": round(float(np.mean(values > 0)), 6),
        "volatility_pct": round(std * math.sqrt(periods_per_year) * 100, 6),
    }
