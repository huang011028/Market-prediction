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
    assert report.significance["method"] == "moving_block_bootstrap"
    assert "market" in report.regime_analysis
    assert report.promotion_gate["should_promote"] is False


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


def test_portfolio_requires_pre_registered_policy_and_reports_sparsity(tmp_path):
    path = tmp_path / "oof.jsonl"
    rows = []
    for index in range(40):
        day = f"2025-{1 + index // 28:02d}-{1 + index % 28:02d}"
        rows.append({
            "market": "A", "model": "ridge", "as_of": day, "symbol": f"{index:06d}",
            "expected_return_pct": 2.0, "prob_up": 0.8, "prob_down": 0.05,
            "prob_no_edge": 0.15, "direction": "bullish",
            "actual_return_pct": 1.0, "actual_absolute_return_pct": 1.2,
            "actual_benchmark_return_pct": 0.1, "daily_volatility_pct": 1.0,
            "avg_traded_value_20d": 100_000_000,
        })
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    report = PortfolioBacktester().run(PortfolioBacktestConfig(
        prediction_paths=[str(path)], market="A", model_name="ridge",
        horizon_trading_days=1, top_k=1, pre_registered=True,
        policy_role="production_candidate", policy_id="a-5d-v1",
        selection_source="frozen_validation_policy", min_independent_dates=10,
        min_invested_dates=10, min_position_selections=10,
        max_top5_profit_concentration=0.5, min_regime_periods=3,
    ), tmp_path / "policy_report")

    assert report.policy["promotion_eligible"] is True
    assert report.significance["independent_dates"] == len(report.periods)
    assert "positive_block_bootstrap_ci" in report.promotion_gate["checks"]
    assert "profit_not_over_concentrated" in report.promotion_gate["checks"]
