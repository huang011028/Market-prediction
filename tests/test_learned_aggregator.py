from datetime import date, timedelta

from src.core.learned_aggregator import LearnedAggregatorTrainer
from src.data.quant_feature_store import QuantFeatureRow, QuantFeatureStore


def test_learned_aggregator_uses_purged_multifold_gate(tmp_path):
    store = QuantFeatureStore(db_path=tmp_path / "features.db")
    start = date(2025, 1, 1)
    for day in range(100):
        as_of = start + timedelta(days=day)
        label = "bullish" if day % 2 == 0 else "bearish"
        for symbol_index in range(3):
            correct_up = 0.85 if label == "bullish" else 0.05
            correct_down = 0.85 if label == "bearish" else 0.05
            store.save(QuantFeatureRow(
                market="A",
                symbol=f"{symbol_index + 1:06d}",
                as_of=as_of.isoformat(),
                timeframe="短期(1周)",
                horizon="5d",
                target_version="v3.1",
                source_kind="historical_replay",
                valid_date=(as_of + timedelta(days=7)).isoformat(),
                label_direction=label,
                label_return_pct=2.0 if label == "bullish" else -2.0,
                label_threshold_pct=1.0,
                features={
                    "agent__technical__prob_up": correct_up,
                    "agent__technical__prob_down": correct_down,
                    "agent__technical__prob_no_edge": 0.10,
                    "agent__news__prob_up": 0.20,
                    "agent__news__prob_down": 0.20,
                    "agent__news__prob_no_edge": 0.60,
                },
                lineage={
                    "point_in_time_verified": True,
                    "source_timestamps": [as_of.isoformat()],
                    "prediction_origin": "live",
                },
            ))

    artifact = LearnedAggregatorTrainer(
        store=store,
        artifact_root=tmp_path / "models",
    ).run(
        market="A",
        horizon="5d",
        min_samples=90,
        min_unique_dates=30,
        purge_days=3,
        lockbox_days=10,
        min_folds=3,
    )

    assert len(artifact.validation["folds"]) == 3
    assert artifact.validation["brier_delta_ci_low"] > 0
    assert artifact.validation["passed"] is True
    assert artifact.weights["technical"] > artifact.weights["news"]
    assert artifact.enabled is False
