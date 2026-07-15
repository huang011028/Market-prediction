import json
import sqlite3

from src.data.prediction_store import PredictionStore


def test_legacy_prediction_is_not_relabelled_as_v31(tmp_path):
    db_path = tmp_path / "predictions.db"
    PredictionStore(db_path)
    report = {
        "prediction_target": {
            "target_type": "excess_return",
            "price_basis": "close",
            "benchmark_symbol": "510300",
        }
    }
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO predictions (
                   id, target, timeframe, direction, confidence,
                   predicted_at, valid_until, agents_used, report_json
               ) VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                "legacy-1", "000001", "短期(1周)", "neutral", 0.5,
                "2026-07-01T10:00:00", "2026-07-08T10:00:00", "[]",
                json.dumps(report),
            ),
        )
        conn.commit()

    PredictionStore(db_path)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT target_version, target_type, residualization_mode FROM predictions"
        ).fetchone()
    assert row == ("legacy-v2", "excess_return", "market_difference_legacy")
