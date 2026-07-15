from src.core.prediction_target import (
    PredictionTargetSpec,
    direction_correct,
    direction_from_return,
    target_spec_for_volatility,
)


def test_v3_fixed_horizon_label_is_mutually_exclusive():
    spec = PredictionTargetSpec(
        evaluation_mode="fixed_horizon",
        up_threshold_pct=1.5,
        down_threshold_pct=-1.5,
        neutral_band_pct=1.0,
    )

    assert direction_from_return(-2.0, spec) == "bearish"
    assert direction_correct("bearish", -2.0, 4.0, -4.0, spec)
    assert not direction_correct("bullish", -2.0, 4.0, -4.0, spec)


def test_old_barrier_target_remains_compatible():
    spec = PredictionTargetSpec(
        target_version="v2",
        evaluation_mode="fixed_horizon_and_barrier",
        label_mode="legacy_barrier",
    )

    assert direction_correct("bullish", -2.0, 3.0, -4.0, spec)


def test_volatility_scaled_threshold_uses_horizon_and_bounds():
    spec = PredictionTargetSpec(horizon_trading_days=5, volatility_multiplier=0.75)
    adapted = target_spec_for_volatility(spec, 2.0)

    assert adapted.up_threshold_pct == 3.35
    assert adapted.down_threshold_pct == -3.35
    assert adapted.neutral_band_pct == 2.24


def test_v31_declares_beta_residual_and_quantile_targets():
    spec = PredictionTargetSpec()

    assert spec.target_version == "v3.1"
    assert spec.target_type == "residual_return"
    assert spec.residualization_mode == "rolling_beta_market"
    assert spec.industry_neutral is False
    assert spec.price_basis == "as_of_close_to_horizon_close"
    assert spec.quantile_levels == (0.1, 0.5, 0.9)
