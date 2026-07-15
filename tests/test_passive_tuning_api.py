import json
from datetime import datetime, timedelta

import pandas as pd
from fastapi.testclient import TestClient

import api_server
from src.data.prediction_store import PredictionRecord, PredictionStore
from src.core.result import Direction, FinalReport, Magnitude


def _insert_prediction(store: PredictionStore, pid: str, *, verified: bool) -> None:
    now = datetime.now()
    with store._conn() as conn:
        conn.execute(
            """INSERT INTO predictions
               (id, target, target_name, timeframe, direction, min_pct, max_pct,
                confidence, predicted_at, valid_until, actual_direction,
                actual_change_pct, direction_correct, verified_at, agents_used,
                elapsed_seconds, llm_model, summary, report_json, report_md)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                pid,
                "000001",
                "平安银行",
                "短期(1周)",
                "bullish",
                1.0,
                5.0,
                0.65,
                (now - timedelta(days=10)).isoformat(),
                (now - timedelta(days=3)).isoformat(),
                "bullish" if verified else None,
                2.4 if verified else None,
                1 if verified else None,
                now.isoformat() if verified else None,
                json.dumps(["近期股价分析师"], ensure_ascii=False),
                1.0,
                "test",
                "summary",
                "{}",
                "",
            ),
        )
        conn.commit()


def test_passive_samples_api_lists_verified_pool(tmp_path, monkeypatch):
    store = PredictionStore(db_path=tmp_path / "predictions.db")
    _insert_prediction(store, "verified-1", verified=True)
    _insert_prediction(store, "watch-1", verified=False)
    with store._conn() as conn:
        conn.execute(
            """UPDATE predictions SET
               expected_excess_return_pct=2.0, prob_up=0.55, prob_down=0.15,
               prob_no_edge=0.30, edge_score=0.42, decision='long_bias',
               actual_effective_return_pct=2.4, brier_score=0.12, edge_hit=1
               WHERE id='verified-1'"""
        )
        conn.commit()
    monkeypatch.setattr(api_server, "PredictionStore", lambda: store)

    client = TestClient(api_server.app)
    resp = client.get("/api/improvement/passive-samples?verified=verified&limit=20")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["summary"]["total"] == 2
    assert payload["summary"]["verified"] == 1
    assert len(payload["samples"]) == 1
    assert payload["samples"][0]["id"] == "verified-1"
    assert payload["samples"][0]["eligible_for_tuning"] is True
    assert payload["samples"][0]["expected_excess_return_pct"] == 2.0
    assert payload["samples"][0]["edge_score"] == 0.42
    assert payload["samples"][0]["brier_score"] == 0.12
    assert payload["samples"][0]["edge_hit"] == 1
    assert payload["parameter_help"]


def test_prediction_store_persists_prediction_target_v2_fields(tmp_path):
    store = PredictionStore(db_path=tmp_path / "predictions.db")
    report = FinalReport(
        target="000001",
        timeframe="短期(1周)",
        direction=Direction.BULLISH,
        magnitude=Magnitude(min_pct=1.0, max_pct=3.0),
        confidence=0.72,
        expected_excess_return_pct=2.1,
        prob_up=0.58,
        prob_down=0.17,
        prob_no_edge=0.25,
        edge_score=0.44,
        decision="long_bias",
        summary="test",
    )

    pid = store.save_prediction(
        target="000001",
        timeframe="短期(1周)",
        report=report,
        agent_results=[],
        agents_used=["近期股价分析师"],
        agents_failed=[],
        elapsed_seconds=1.2,
        llm_model="unit-test",
        target_name="平安银行",
    )

    with store._conn() as conn:
        row = conn.execute(
            """SELECT expected_excess_return_pct, prob_up, prob_no_edge,
                      edge_score, decision, target_type, horizon
               FROM predictions WHERE id=?""",
            (pid,),
        ).fetchone()

    assert row["expected_excess_return_pct"] == 2.1
    assert row["prob_up"] == 0.58
    assert row["prob_no_edge"] == 0.25
    assert row["edge_score"] == 0.44
    assert row["decision"] == "long_bias"
    assert row["target_type"] in {"residual_return", "excess_return", "absolute_return"}
    assert row["horizon"] == "5d"

    record = store.get_prediction(pid)
    assert record.expected_excess_return_pct == 2.1
    assert record.decision == "long_bias"


def test_prediction_tracking_helper_builds_return_curve(monkeypatch):
    class FakePriceFetcher:
        async def fetch_close_near(self, symbol, target_date, prefer="on_or_before", tolerance_days=10):
            return 100.0

        async def fetch_close_window(self, symbol, start_date, end_date):
            return pd.Series(
                [100.0, 103.0, 105.0],
                index=pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-05"]),
            )

        async def fetch(self, symbol, period="1mo"):
            class PriceData:
                intraday_trend = [
                    {
                        "time": "2026-01-01 09:35",
                        "date": "2026-01-01",
                        "open": 99.0,
                        "high": 100.0,
                        "low": 98.5,
                        "close": 99.5,
                        "volume": 1200,
                    },
                    {
                        "time": "2026-01-01 10:00",
                        "date": "2026-01-01",
                        "open": 100.0,
                        "high": 102.0,
                        "low": 99.8,
                        "close": 101.0,
                        "volume": 1800,
                    },
                    {
                        "time": "2026-01-01 10:05",
                        "date": "2026-01-01",
                        "open": 101.0,
                        "high": 104.5,
                        "low": 100.8,
                        "close": 104.0,
                        "volume": 2400,
                    },
                ]
                intraday_meta = {
                    "available": True,
                    "source": "fake",
                    "interval": "5m",
                }

            return PriceData()

    import src.data.price_fetcher as price_fetcher_module

    monkeypatch.setattr(price_fetcher_module, "PriceFetcher", FakePriceFetcher)
    record = PredictionRecord(
        id="pred-1",
        target="AAPL",
        target_name="Apple",
        timeframe="短期(1周)",
        direction="bullish",
        min_pct=1.0,
        max_pct=6.0,
        confidence=0.7,
        expected_excess_return_pct=3.0,
        prob_up=0.6,
        prob_down=0.2,
        prob_no_edge=0.2,
        edge_score=0.5,
        decision="long_bias",
        predicted_at="2026-01-01T10:00:00",
        valid_until="2026-01-08T10:00:00",
        verified_at=None,
        report_json=json.dumps({
            "prediction_target": {
                "target_type": "absolute_return",
                "benchmark_symbol": None,
                "up_threshold_pct": 1.5,
                "down_threshold_pct": -1.5,
                "neutral_band_pct": 1.0,
            }
        }),
    )

    import asyncio

    payload = asyncio.run(api_server._build_prediction_tracking(record))

    assert payload["prediction"]["id"] == "pred-1"
    assert payload["summary"]["latest_effective_return_pct"] == 5.0
    assert payload["summary"]["correct_so_far"] is True
    assert payload["summary"]["edge_hit_so_far"] is True
    assert payload["summary"]["brier_score_so_far"] == 0.08
    assert payload["summary"]["expected_excess_return_pct"] == 3.0
    assert payload["summary"]["range_hit_now"] is True
    assert payload["prediction"]["edge_score"] == 0.5
    assert payload["prediction"]["decision"] == "long_bias"
    assert len(payload["points"]) == 3
    assert payload["intraday_meta"]["available"] is True
    assert payload["intraday_meta"]["points"] == 2
    assert payload["intraday_points"][0]["time"] == "2026-01-01 10:00"
    assert payload["intraday_points"][-1]["effective_return_pct"] == 4.0
    assert payload["intraday_points"][-1]["high"] == 104.5
