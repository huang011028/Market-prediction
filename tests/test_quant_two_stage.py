from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from src.core.experiment_ledger import ExperimentLedger
from src.core.quant_two_stage import (
    BinaryProbabilityCalibrator,
    TwoStageConfig,
    TwoStageQuantEvaluator,
    _fit_rank,
    _feature_incremental_gate,
    _neutralize_predictions,
    _ranking_relevance,
)
from src.data.quant_feature_store import FEATURE_SCHEMA_VERSION, QuantFeatureRow, QuantFeatureStore


def _rows(days=130, symbols=8):
    start = date(2024, 1, 1)
    for day in range(days):
        as_of = (start + timedelta(days=day)).isoformat()
        market_cycle = ((day % 11) - 5) * 0.15
        for index in range(symbols):
            signal = ((day * 3 + index * 2) % 13) - 6
            value = signal * 0.7 + market_cycle
            direction = "bullish" if value > 1.5 else "bearish" if value < -1.5 else "neutral"
            yield {
                "feature_id": f"{day}-{index}",
                "market": "A",
                "symbol": f"{index:06d}",
                "as_of": as_of,
                "horizon": "5d",
                "feature_version": "quant_features.v1",
                "features": {
                    "technical__return_5d_pct": signal,
                    "technical__trend_score": signal / 6,
                    "fundamental__quality_score": (index % 4) / 4,
                    "meta__industry": "bank" if index < 4 else "tech",
                    "meta__avg_traded_value_20d": 20_000_000 + index * 1_000_000,
                    "meta__daily_volatility_pct": 1.0 + index * 0.1,
                },
                "label_direction": direction,
                "label_return_pct": float(value),
                "label_threshold_pct": 1.5,
            }


def _store(tmp_path):
    store = QuantFeatureStore(tmp_path / "features.db")
    for item in _rows():
        store.save(QuantFeatureRow(
            market=item["market"], symbol=item["symbol"], as_of=item["as_of"],
            horizon=item["horizon"], features=item["features"],
            source_kind="historical_replay",
            label_direction=item["label_direction"],
            label_return_pct=item["label_return_pct"],
            label_threshold_pct=item["label_threshold_pct"],
            lineage={"point_in_time_verified": True, "source_timestamps": [item["as_of"]]},
        ))
    return store


def test_binary_calibrator_uses_validation_only_and_normalizes():
    raw = np.asarray([0.05] * 20 + [0.95] * 80)
    actual = np.asarray([0] * 20 + [1] * 40 + [0] * 40)
    calibrator = BinaryProbabilityCalibrator(
        ["identity", "sigmoid", "isotonic"],
    ).fit(raw, actual)
    calibrated = calibrator.transform(raw)

    assert calibrator.artifact.validation_samples == 100
    assert calibrator.artifact.calibrated_brier <= calibrator.artifact.raw_brier
    assert np.all((calibrated >= 0) & (calibrated <= 1))


def test_exposure_neutralization_removes_date_industry_mean():
    rows = list(_rows(days=1, symbols=8))
    predicted = np.asarray([3.0] * 4 + [-2.0] * 4)
    controlled = _neutralize_predictions(rows, predicted, industry=True, risk=True)

    assert abs(float(controlled[:4].mean())) < 1e-8
    assert abs(float(controlled[4:].mean())) < 1e-8


def test_lightgbm_ranking_labels_are_bounded_and_trainable():
    pytest.importorskip("lightgbm")
    rows = list(_rows(days=4, symbols=8))
    frame = pd.DataFrame({
        "date": [row["as_of"] for row in rows],
        "return": [row["label_return_pct"] for row in rows],
    })
    relevance = _ranking_relevance(frame)
    x = np.asarray([
        [row["features"]["technical__return_5d_pct"]]
        for row in rows
    ])

    assert int(relevance.min()) >= 0
    assert int(relevance.max()) <= 4
    model = _fit_rank("lightgbm_ranker", x, rows)
    assert len(model.predict(x)) == len(rows)


def test_phase2_incremental_gate_compares_same_model_technical_baseline():
    baseline = {
        "brier_score": 0.18, "rank_ic": 0.02, "top_k_mean_return_pct": 0.10,
    }
    candidate = {
        "brier_score": 0.17, "rank_ic": 0.03, "top_k_mean_return_pct": 0.12,
    }
    name = "gate_logistic__rank_ridge__phase2_full"
    result = _feature_incremental_gate(
        name,
        candidate,
        {
            "gate_logistic__rank_ridge__technical": baseline,
            name: candidate,
        },
        TwoStageConfig(min_feature_rank_ic_delta=0.005),
    )

    assert result["passed"] is True
    assert result["brier_delta"] == 0.01
    assert result["rank_ic_delta"] == 0.01


def test_two_stage_walk_forward_emits_oof_ablation_and_shadow_gate(tmp_path):
    store = _store(tmp_path)
    ledger = ExperimentLedger(tmp_path / "ledger.db")
    config = TwoStageConfig(
        feature_version=FEATURE_SCHEMA_VERSION,
        gate_models=["logistic"],
        rank_models=["ridge"],
        feature_sets={"technical": ["technical"]},
        train_days=45,
        validation_days=18,
        test_days=18,
        purge_days=3,
        lockbox_days=15,
        min_train_samples=200,
        min_validation_samples=80,
        min_test_samples=80,
        min_unique_train_dates=20,
        min_actionable_train_samples=40,
        gate_threshold_candidates=[0.4, 0.5, 0.6],
        bootstrap_iterations=100,
        save_models=False,
    )

    report = TwoStageQuantEvaluator(store, ledger=ledger).run(
        config, tmp_path / "two_stage",
    )

    assert report.folds
    candidates = {
        key: value for key, value in report.aggregate_metrics.items()
        if key != "empirical_prior"
    }
    assert candidates
    metrics = next(iter(candidates.values()))
    assert "gate_brier" in metrics
    assert "top_k_mean_return_pct" in metrics
    assert "one_stage" in metrics["ablation"]
    assert report.risk_controls["size_exposure_coverage"] == 0
    assert report.promotion_gate["checks"]["size_exposure_available"] is False
    assert report.promotion_gate["should_promote"] is False
    assert report.lockbox["status"] == "locked"
    assert list((tmp_path / "two_stage" / "oof").glob("*.jsonl"))
    assert ledger.status()["total_trials"] == 1
