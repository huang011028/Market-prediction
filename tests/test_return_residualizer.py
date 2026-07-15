import pandas as pd
import pytest

from src.core.return_residualizer import estimate_market_beta, residual_return_pct


def test_beta_estimation_uses_aligned_pre_prediction_returns():
    benchmark = pd.Series([100, 101, 99, 102, 103, 101], index=pd.date_range("2025-01-01", periods=6))
    benchmark_returns = benchmark.pct_change().fillna(0)
    asset = 50 * (1 + 1.5 * benchmark_returns).cumprod()

    beta = estimate_market_beta(asset, benchmark, min_observations=4)

    assert beta == pytest.approx(1.5, abs=0.08)


def test_residual_return_removes_beta_scaled_market_move():
    assert residual_return_pct(8.0, 4.0, 1.5) == pytest.approx(2.0)
