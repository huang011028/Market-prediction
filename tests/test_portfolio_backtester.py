import json

from src.core.portfolio_backtester import PortfolioBacktestConfig, PortfolioBacktester


def test_a_share_portfolio_applies_costs_and_disables_short(tmp_path):
    path = tmp_path / "oof.jsonl"
    rows = []
    for day in ("2025-01-01", "2025-01-08", "2025-01-15"):
        rows.extend([
            {
                "market": "A", "model": "ridge", "as_of": day, "symbol": "000001",
                "expected_return_pct": 2.0, "prob_up": 0.7, "prob_down": 0.1,
                "prob_no_edge": 0.2, "direction": "bullish",
                "actual_return_pct": 1.8, "actual_absolute_return_pct": 2.0,
                "actual_benchmark_return_pct": 0.2, "daily_volatility_pct": 1.5,
            },
            {
                "market": "A", "model": "ridge", "as_of": day, "symbol": "000002",
                "expected_return_pct": -2.0, "prob_up": 0.1, "prob_down": 0.7,
                "prob_no_edge": 0.2, "direction": "bearish",
                "actual_return_pct": -1.8, "actual_absolute_return_pct": -2.0,
                "actual_benchmark_return_pct": 0.2, "daily_volatility_pct": 2.0,
            },
        ])
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    report = PortfolioBacktester().run(
        PortfolioBacktestConfig(
            prediction_paths=[str(path)],
            market="A",
            model_name="ridge",
            top_k=1,
            bottom_k=1,
            allow_short=True,
        ),
        tmp_path / "report",
    )

    assert all(period.short_positions == 0 for period in report.periods)
    assert report.metrics["total_transaction_cost_pct"] > 0
    assert report.metrics["total_return_pct"] < sum(period.gross_return_pct for period in report.periods)


def test_portfolio_skips_overlapping_horizons_and_charges_capacity_impact(tmp_path):
    path = tmp_path / "oof.jsonl"
    rows = [
        {
            "market": "A", "model": "ridge", "as_of": day, "symbol": "000001",
            "expected_return_pct": 2.0, "prob_up": 0.7, "prob_down": 0.1,
            "prob_no_edge": 0.2, "direction": "bullish",
            "actual_return_pct": 1.0, "actual_absolute_return_pct": 1.2,
            "actual_benchmark_return_pct": 0.2, "daily_volatility_pct": 1.5,
            "avg_traded_value_20d": 1_000_000,
        }
        for day in ("2025-01-01", "2025-01-02", "2025-01-08")
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    report = PortfolioBacktester().run(PortfolioBacktestConfig(
        prediction_paths=[str(path)], market="A", model_name="ridge",
        top_k=1, max_participation_rate=0.05,
    ), tmp_path / "report")

    assert len(report.periods) == 2
    assert report.metrics["overlap_dates_skipped"] == 1
    assert report.metrics["max_participation_rate"] <= 0.05 + 1e-9
    assert report.metrics["capacity_clipped_orders"] > 0
    assert report.metrics["avg_gross_exposure"] < 1.0
