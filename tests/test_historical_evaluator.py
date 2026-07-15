import json
from datetime import datetime, timedelta

from src.core.historical_evaluator import (
    AGGREGATOR_AGENT_NAME,
    HistoricalAgentEvaluator,
    HistoricalAgentSample,
)
from src.data.prediction_store import PredictionStore


def test_historical_evaluator_flags_wrong_strategy_and_contribution():
    samples = []
    for idx in range(6):
        samples.append(
            HistoricalAgentSample(
                agent_name="最新新闻分析师",
                target=f"TEST{idx}",
                timeframe="短期(1周)",
                as_of="2025-01-01",
                valid_date="2025-01-08",
                predicted_direction="bullish",
                predicted_confidence=0.78,
                actual_direction="bearish",
                actual_change_pct=-3.2,
                was_correct=False,
                buckets={
                    "source_bucket": "single_source",
                    "event_bucket": "rumor_driven",
                },
                prediction_id=f"news-{idx}",
                final_direction="bullish",
                final_confidence=0.72,
                final_was_correct=False,
            )
        )

    for idx in range(10):
        samples.append(
            HistoricalAgentSample(
                agent_name="近期股价分析师",
                target=f"TECH{idx}",
                timeframe="短期(1周)",
                as_of="2025-01-01",
                valid_date="2025-01-08",
                predicted_direction="bullish",
                predicted_confidence=0.66,
                actual_direction="bullish",
                actual_change_pct=2.4,
                was_correct=True,
                buckets={"trend_bucket": "up"},
                prediction_id=f"tech-{idx}",
                final_direction="bullish",
                final_confidence=0.68,
                final_was_correct=True,
            )
        )

    report = HistoricalAgentEvaluator().evaluate(samples, min_samples=5)

    assert report.agents["最新新闻分析师"]["accuracy"] == 0
    assert report.agents["最新新闻分析师"]["unique_cases"] == 6
    assert report.agent_contributions["最新新闻分析师"]["reinforced_final_error"] == 6
    assert any(
        signal["agent_name"] == "最新新闻分析师"
        and signal["area"] == "data_source"
        and signal["bucket_group"] == "source_buckets"
        and signal["unique_cases"] == 6
        for signal in report.wrong_strategy_signals
    )
    assert any(
        signal["agent_name"] == AGGREGATOR_AGENT_NAME
        and signal["bucket"] == "reinforced_final_errors"
        for signal in report.wrong_strategy_signals
    )
    assert any(
        signal["agent_name"] == "近期股价分析师"
        and signal["signal_type"] == "strength"
        and signal["bucket_group"] == "trend_buckets"
        and signal["dominant_actual_direction"] == "bullish"
        for signal in report.strength_signals
    )


def test_historical_evaluator_loads_bootstrap_report():
    payload = {
        "reports": [
            {
                "agent_name": "近期股价分析师",
                "samples": [
                    {
                        "target": "000001",
                        "as_of": "2025-03-01",
                        "valid_date": "2025-03-08",
                        "timeframe": "短期(1周)",
                        "predicted_direction": "bearish",
                        "predicted_confidence": 0.61,
                        "actual_direction": "bearish",
                        "actual_change_pct": -2.1,
                        "was_correct": True,
                        "buckets": {"momentum_bucket": "bearish"},
                    }
                ],
            }
        ]
    }

    evaluator = HistoricalAgentEvaluator()
    samples = evaluator.samples_from_bootstrap_report(payload)
    report = evaluator.evaluate(samples, min_samples=1)

    assert len(samples) == 1
    assert report.bucket_stats["近期股价分析师"]["momentum_buckets"]["bearish"]["accuracy"] == 1
    assert report.bucket_stats["近期股价分析师"]["momentum_buckets"]["bearish"]["unique_cases"] == 1


def test_historical_evaluator_maps_technical_regime_buckets_to_skill_and_prompt():
    samples = []
    for idx in range(6):
        samples.append(
            HistoricalAgentSample(
                agent_name="近期股价分析师",
                target=f"REGIME{idx}",
                timeframe="短期(1周)",
                as_of="2025-01-01",
                valid_date="2025-01-08",
                predicted_direction="bullish",
                predicted_confidence=0.70,
                actual_direction="bearish",
                actual_change_pct=-2.0,
                was_correct=False,
                buckets={
                    "market_regime_bucket": "bull_trend",
                    "sr_zone_bucket": "near_resistance",
                },
            )
        )

    report = HistoricalAgentEvaluator().evaluate(samples, min_samples=5)

    assert any(
        signal["area"] == "skill"
        and signal["bucket_group"] == "market_regime_buckets"
        for signal in report.wrong_strategy_signals
    )
    assert any(
        signal["area"] == "prompt"
        and signal["bucket_group"] == "sr_zone_buckets"
        for signal in report.wrong_strategy_signals
    )


def test_historical_evaluator_reads_verified_prediction_store(tmp_path):
    store = PredictionStore(db_path=tmp_path / "predictions.db")
    predicted_at = datetime.now() - timedelta(days=10)
    valid_until = datetime.now() - timedelta(days=3)
    with store._conn() as conn:
        conn.execute(
            """INSERT INTO predictions
               (id, target, timeframe, direction, confidence, predicted_at, valid_until,
                actual_direction, actual_change_pct, direction_correct, verified_at,
                expected_excess_return_pct, prob_up, prob_down, prob_no_edge,
                edge_score, decision, actual_effective_return_pct,
                actual_absolute_return_pct, actual_benchmark_return_pct,
                target_type_used, brier_score, edge_hit,
                agents_used, elapsed_seconds, llm_model, summary, report_json, report_md)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "pred-1",
                "000001",
                "短期(1周)",
                "bearish",
                0.62,
                predicted_at.isoformat(),
                valid_until.isoformat(),
                "bearish",
                -2.6,
                1,
                datetime.now().isoformat(),
                -1.8,
                0.14,
                0.64,
                0.22,
                0.72,
                "short_bias",
                -2.2,
                -2.6,
                -0.4,
                "excess_return",
                0.09,
                1,
                json.dumps(["行业对比分析师"], ensure_ascii=False),
                1.0,
                "test",
                "summary",
                json.dumps(
                    {
                        "prediction_target": {
                            "target_type": "excess_return",
                            "horizon": "short_1w",
                            "benchmark_symbol": "000300.SH",
                        }
                    },
                    ensure_ascii=False,
                ),
                "",
            ),
        )
        conn.execute(
            """INSERT INTO agent_results
               (prediction_id, agent_name, direction, confidence, data_summary)
               VALUES (?,?,?,?,?)""",
            (
                "pred-1",
                "行业对比分析师",
                "bearish",
                0.58,
                json.dumps(
                    {
                        "industry": "银行",
                        "data_quality": {
                            "overall": 0.22,
                            "has_constituents": False,
                            "has_trend": False,
                        },
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        conn.commit()

    samples = HistoricalAgentEvaluator().samples_from_prediction_store(store)

    assert len(samples) == 1
    assert samples[0].agent_name == "行业对比分析师"
    assert samples[0].buckets["data_quality_level"] == "reference_only"
    assert samples[0].prediction_target["target_type"] == "excess_return"
    assert samples[0].expected_excess_return_pct == -1.8
    assert samples[0].prob_down == 0.64
    assert samples[0].edge_score == 0.72
    assert samples[0].decision == "short_bias"
    assert samples[0].actual_effective_return_pct == -2.2
    assert samples[0].brier_score == 0.09
    assert samples[0].edge_hit is True

    report = HistoricalAgentEvaluator().evaluate(samples, min_samples=1)
    stats = report.agents["行业对比分析师"]
    assert stats["avg_brier_score"] == 0.09
    assert stats["edge_hit_rate"] == 1.0
    assert stats["avg_edge_score"] == 0.72
    assert stats["actionable_coverage"] == 1.0
    assert stats["avg_actual_effective_return_pct"] == -2.2
    assert stats["avg_expected_excess_return_pct"] == -1.8
    assert report.bucket_stats["行业对比分析师"]["decision_buckets"]["short_bias"]["total"] == 1
    assert report.bucket_stats["行业对比分析师"]["edge_buckets"]["moderate_edge"]["total"] == 1
    assert report.bucket_stats["行业对比分析师"]["brier_buckets"]["good_calibration"]["total"] == 1


def test_historical_evaluator_filters_prediction_ids(tmp_path):
    store = PredictionStore(db_path=tmp_path / "predictions.db")
    now = datetime.now()
    with store._conn() as conn:
        for pid, target in [("pred-1", "000001"), ("pred-2", "600519")]:
            conn.execute(
                """INSERT INTO predictions
                   (id, target, timeframe, direction, confidence, predicted_at, valid_until,
                    actual_direction, actual_change_pct, direction_correct, verified_at,
                    agents_used, elapsed_seconds, llm_model, summary, report_json, report_md)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    pid,
                    target,
                    "短期(1周)",
                    "bullish",
                    0.6,
                    now.isoformat(),
                    now.isoformat(),
                    "bullish",
                    2.0,
                    1,
                    now.isoformat(),
                    json.dumps(["近期股价分析师"], ensure_ascii=False),
                    1.0,
                    "test",
                    "summary",
                    "{}",
                    "",
                ),
            )
            conn.execute(
                """INSERT INTO agent_results
                   (prediction_id, agent_name, direction, confidence, data_summary)
                   VALUES (?,?,?,?,?)""",
                (
                    pid,
                    "近期股价分析师",
                    "bullish",
                    0.6,
                    json.dumps({"trend_bucket": "up"}, ensure_ascii=False),
                ),
            )
        conn.commit()

    samples = HistoricalAgentEvaluator().samples_from_prediction_store(
        store,
        prediction_ids=["pred-2"],
    )

    assert len(samples) == 1
    assert samples[0].prediction_id == "pred-2"
    assert samples[0].target == "600519"
