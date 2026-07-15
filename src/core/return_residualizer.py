"""Point-in-time market residual return utilities."""

from __future__ import annotations

import math
from typing import Any, Iterable, Optional

import numpy as np
import pandas as pd


def estimate_market_beta(
    asset_closes: pd.Series,
    benchmark_closes: pd.Series,
    *,
    min_observations: int = 20,
    clip: tuple[float, float] = (-1.0, 3.0),
) -> Optional[float]:
    """Estimate beta using only aligned closes available at prediction time."""
    frame = pd.concat(
        [asset_closes.astype(float).rename("asset"), benchmark_closes.astype(float).rename("benchmark")],
        axis=1,
        join="inner",
    ).dropna()
    returns = frame.pct_change(fill_method=None).dropna()
    if len(returns) < max(5, int(min_observations)):
        return None
    benchmark_variance = float(returns["benchmark"].var(ddof=1))
    if not math.isfinite(benchmark_variance) or benchmark_variance <= 1e-12:
        return None
    covariance = float(returns[["asset", "benchmark"]].cov().iloc[0, 1])
    beta = covariance / benchmark_variance
    if not math.isfinite(beta):
        return None
    return round(float(np.clip(beta, clip[0], clip[1])), 6)


def estimate_market_beta_from_trends(
    asset_trend: Iterable[dict[str, Any]],
    benchmark_trend: Iterable[dict[str, Any]],
    *,
    min_observations: int = 20,
) -> Optional[float]:
    return estimate_market_beta(
        _trend_closes(asset_trend),
        _trend_closes(benchmark_trend),
        min_observations=min_observations,
    )


def residual_return_pct(
    asset_return_pct: float,
    benchmark_return_pct: Optional[float],
    market_beta: Optional[float],
) -> float:
    if benchmark_return_pct is None:
        return float(asset_return_pct)
    beta = 1.0 if market_beta is None else float(market_beta)
    return float(asset_return_pct) - beta * float(benchmark_return_pct)


def normal_quantiles(mean: float, sigma: float) -> tuple[float, float, float]:
    width = 1.2815515655446004 * max(0.0, float(sigma))
    return mean - width, mean, mean + width


def _trend_closes(trend: Iterable[dict[str, Any]]) -> pd.Series:
    values = {}
    for item in trend or []:
        date_value = str(item.get("date") or "")[:10]
        close = item.get("close")
        if date_value and close not in (None, ""):
            values[pd.Timestamp(date_value)] = float(close)
    return pd.Series(values, dtype=float).sort_index()
