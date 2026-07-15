from datetime import date, timedelta

import pytest

from src.core.quant_calibration import MulticlassProbabilityCalibrator
from src.core.quant_models import create_quant_model, dependency_status
from src.core.quant_models import QuantPrediction
from src.core.quant_stacking import ConstrainedIndustryStacker
from src.core.quant_walk_forward import QuantWalkForwardEvaluator, WalkForwardConfig
from src.data.quant_feature_store import QuantFeatureRow, QuantFeatureStore


def _rows(days=90, symbols=5):
    rows = []
    start = date(2024, 1, 1)
    for day in range(days):
        as_of = (start + timedelta(days=day)).isoformat()
        for symbol_index in range(symbols):
            momentum = ((day + symbol_index) % 9) - 4
            direction = "bullish" if momentum > 1 else "bearish" if momentum < -1 else "neutral"
            rows.append({
                "feature_id": f"{day}-{symbol_index}",
                "market": "A",
                "symbol": f"{symbol_index:06d}",
                "as_of": as_of,
                "horizon": "5d",
                "feature_version": "quant_features.v1",
                "features": {
                    "technical__return_5d_pct": momentum,
                    "technical__trend_score": momentum / 4,
                    "technical__regime": direction,
                },
                "label_direction": direction,
                "label_return_pct": float(momentum),
                "label_threshold_pct": 1.5,
            })
    return rows


@pytest.mark.parametrize("name", ["ridge", "logistic", "lightgbm"])
def test_quant_baselines_emit_normalized_distribution(name):
    dependency = "lightgbm" if name == "lightgbm" else "sklearn"
    if not dependency_status()[dependency]["available"]:
        pytest.skip(f"{dependency} not installed")
    rows = _rows(days=20, symbols=4)
    model = create_quant_model(name).fit(rows, market="A", horizon="5d")
    prediction = model.predict(rows[:2])[0]

    assert prediction.prob_down + prediction.prob_no_edge + prediction.prob_up == pytest.approx(1.0)
    assert prediction.direction in {"bearish", "neutral", "bullish"}


def test_walk_forward_keeps_lockbox_and_compares_benchmarks(tmp_path):
    if not dependency_status()["sklearn"]["available"]:
        pytest.skip("scikit-learn not installed")
    store = QuantFeatureStore(tmp_path / "features.db")
    for item in _rows(days=120, symbols=5):
        store.save(QuantFeatureRow(
            market="A",
            symbol=item["symbol"],
            as_of=item["as_of"],
            horizon="5d",
            features=item["features"],
            source_kind="historical_replay",
            label_direction=item["label_direction"],
            label_return_pct=item["label_return_pct"],
            label_threshold_pct=1.5,
            lineage={"point_in_time_verified": True, "source_timestamps": [item["as_of"]]},
        ))
    report = QuantWalkForwardEvaluator(store).run(
        WalkForwardConfig(
            market="A",
            horizon="5d",
            model_names=["ridge", "logistic", "lightgbm"],
            train_days=35,
            validation_days=15,
            test_days=15,
            purge_days=5,
            lockbox_days=15,
            min_train_samples=80,
            min_validation_samples=30,
            min_test_samples=30,
            min_unique_train_dates=15,
        ),
        tmp_path / "walk",
    )

    assert report.folds
    assert report.lockbox["status"] == "locked"
    assert "empirical_prior" in report.aggregate_metrics
    assert "ridge" in report.aggregate_metrics
    if dependency_status()["lightgbm"]["available"]:
        assert "lightgbm" in report.aggregate_metrics
        assert any(key.endswith("lightgbm_oof") for key in report.artifact_paths)


def test_walk_forward_reports_feature_family_ablation(tmp_path):
    if not dependency_status()["sklearn"]["available"]:
        pytest.skip("scikit-learn not installed")
    store = QuantFeatureStore(tmp_path / "features.db")
    for item in _rows(days=120, symbols=5):
        features = dict(item["features"])
        features["fundamental__revenue_yoy_pct"] = features["technical__return_5d_pct"] * 0.5
        store.save(QuantFeatureRow(
            market="A",
            symbol=item["symbol"],
            as_of=item["as_of"],
            horizon="5d",
            features=features,
            source_kind="historical_replay",
            label_direction=item["label_direction"],
            label_return_pct=item["label_return_pct"],
            label_threshold_pct=1.5,
            lineage={"point_in_time_verified": True, "source_timestamps": [item["as_of"]]},
        ))
    report = QuantWalkForwardEvaluator(store).run(
        WalkForwardConfig(
            model_names=["ridge"],
            feature_set_names=["technical", "technical_fundamental"],
            train_days=35,
            validation_days=15,
            test_days=15,
            purge_days=5,
            lockbox_days=15,
            min_train_samples=80,
            min_validation_samples=30,
            min_test_samples=30,
            min_unique_train_dates=15,
            save_models=True,
        ),
        tmp_path / "walk_ablation",
    )

    assert "ridge__technical" in report.aggregate_metrics
    assert "ridge__technical_fundamental" in report.aggregate_metrics
    assert "technical_fundamental" in report.feature_ablation["ridge"]["comparisons"]
    technical_path = report.artifact_paths["fold_1_ridge__technical"]["model"]
    fundamental_path = report.artifact_paths["fold_1_ridge__technical_fundamental"]["model"]
    assert technical_path != fundamental_path
    assert "/ridge__technical/" in technical_path
    assert "/ridge__technical_fundamental/" in fundamental_path


def test_probability_calibrator_is_validation_fitted_and_normalized():
    predictions = [
        QuantPrediction(1.0, 0.02, 0.03, 0.95, "bullish")
        for _ in range(60)
    ] + [
        QuantPrediction(-1.0, 0.95, 0.03, 0.02, "bearish")
        for _ in range(40)
    ]
    labels = ["bullish"] * 30 + ["neutral"] * 30 + ["bearish"] * 20 + ["neutral"] * 20

    calibrator = MulticlassProbabilityCalibrator(min_samples=50).fit(predictions, labels)
    calibrated = calibrator.transform(predictions)

    assert calibrator.artifact.validation_samples == 100
    assert calibrator.artifact.brier_delta >= 0
    assert calibrated[0].prob_down + calibrated[0].prob_no_edge + calibrated[0].prob_up == pytest.approx(1.0)
    assert calibrated[0].prob_up < predictions[0].prob_up


def test_constrained_industry_stacker_caps_incremental_weight():
    technical = [
        QuantPrediction(0.0, 0.30, 0.40, 0.30, "neutral")
        for _ in range(20)
    ]
    industry = [
        QuantPrediction(1.0, 0.05, 0.10, 0.85, "bullish")
        for _ in range(20)
    ]
    labels = ["bullish"] * 20

    stacker = ConstrainedIndustryStacker(max_industry_weight=0.30).fit(
        technical, industry, labels,
    )
    stacked = stacker.transform(technical, industry)

    assert 0 < stacker.artifact.industry_weight <= 0.30
    assert stacker.artifact.technical_weight + stacker.artifact.industry_weight == pytest.approx(1.0)
    assert stacked[0].prob_up > technical[0].prob_up


def test_walk_forward_reports_calibration_and_industry_stack(tmp_path):
    if not dependency_status()["sklearn"]["available"]:
        pytest.skip("scikit-learn not installed")
    store = QuantFeatureStore(tmp_path / "features.db")
    for item in _rows(days=120, symbols=5):
        features = dict(item["features"])
        features["industry__relative_return_5d_pct"] = item["label_return_pct"] * 0.5
        store.save(QuantFeatureRow(
            market="A",
            symbol=item["symbol"],
            as_of=item["as_of"],
            horizon="5d",
            features=features,
            source_kind="historical_replay",
            label_direction=item["label_direction"],
            label_return_pct=item["label_return_pct"],
            label_threshold_pct=1.5,
            lineage={"point_in_time_verified": True, "source_timestamps": [item["as_of"]]},
        ))
    report = QuantWalkForwardEvaluator(store).run(
        WalkForwardConfig(
            model_names=["ridge"],
            feature_set_names=["technical", "technical_industry"],
            train_days=35,
            validation_days=15,
            test_days=15,
            purge_days=5,
            lockbox_days=15,
            min_train_samples=80,
            min_validation_samples=30,
            min_test_samples=30,
            min_unique_train_dates=15,
            calibration_min_samples=30,
        ),
        tmp_path / "walk_calibrated",
    )

    assert "calibration_brier_delta" in report.aggregate_metrics["ridge__technical"]
    assert "ridge__technical_industry_stack" in report.aggregate_metrics
    fold_result = report.folds[0].models["ridge__technical"]
    assert fold_result["calibration"]["validation_samples"] >= 30
    stack_result = report.folds[0].models["ridge__technical_industry_stack"]
    assert stack_result["stacking"]["industry_weight"] <= 0.35
