"""
Phase 3 测试 — PredictionStore, Backtester, CaseRetriever
"""

import json
import pytest
from pathlib import Path
from src.data.prediction_store import PredictionStore, PredictionRecord
from src.core.result import FinalReport, Direction, Magnitude, AnalysisResult


class TestPredictionStore:
    """预测存储测试"""

    @pytest.fixture
    def store(self, tmp_path):
        db_path = tmp_path / "test_predictions.db"
        return PredictionStore(db_path=db_path)

    def test_init_creates_db(self, store):
        assert Path(store.db_path).exists()

    def test_save_and_retrieve(self, store):
        report = FinalReport(
            target="000001", timeframe="短期(1周)",
            direction=Direction.BEARISH,
            magnitude=Magnitude(-3.0, 1.0),
            confidence=0.55, summary="测试",
        )
        agent_results = [
            AnalysisResult(
                agent_name="技术面分析师", target="000001", timeframe="短期",
                direction=Direction.BEARISH, magnitude=Magnitude(-3.0, 1.0),
                confidence=0.65, reasoning="测试推理",
            )
        ]

        pid = store.save_prediction(
            target="000001", timeframe="短期(1周)",
            report=report, agent_results=agent_results,
            agents_used=["技术面分析师"], agents_failed=[],
            elapsed_seconds=30.0, llm_model="deepseek",
        )

        assert pid
        assert len(pid) == 8

        # 检索
        rec = store.get_prediction(pid)
        assert rec is not None
        assert rec.target == "000001"
        assert rec.direction == "bearish"
        assert rec.confidence == 0.55

    def test_unverified_count(self, store):
        report = FinalReport(
            target="000001", timeframe="短期(1周)",
            direction=Direction.NEUTRAL, confidence=0.5,
            summary="测试",
        )
        store.save_prediction(
            target="000001", timeframe="短期(1周)",
            report=report, agent_results=[],
            agents_used=["技术面分析师"], agents_failed=[],
            elapsed_seconds=10.0, llm_model="deepseek",
        )

        assert store.get_unverified_count() == 1

    def test_get_predictions_filter(self, store):
        report = FinalReport(
            target="000001", timeframe="短期(1周)",
            direction=Direction.BULLISH, confidence=0.5,
            summary="测试",
        )

        store.save_prediction(
            target="000001", timeframe="短期(1周)",
            report=report, agent_results=[],
            agents_used=["技术面分析师"], agents_failed=[],
            elapsed_seconds=10.0, llm_model="deepseek",
        )

        preds = store.get_predictions(target="000001")
        assert len(preds) == 1

        preds = store.get_predictions(target="NONEXIST")
        assert len(preds) == 0

    def test_get_prediction_summaries_uses_lightweight_fields(self, store):
        report = FinalReport(
            target="000001", timeframe="短期(1周)",
            direction=Direction.BULLISH, confidence=0.5,
            summary="测试",
        )
        store.save_prediction(
            target="000001", target_name="平安银行", timeframe="短期(1周)",
            report=report, agent_results=[],
            agents_used=["技术面分析师"], agents_failed=[],
            elapsed_seconds=10.0, llm_model="deepseek",
        )

        summaries = store.get_prediction_summaries(target="000001")

        assert len(summaries) == 1
        assert summaries[0]["target"] == "000001"
        assert summaries[0]["target_name"] == "平安银行"
        assert summaries[0]["verified_at"] is None
        assert "report_json" not in summaries[0]
        assert "report_md" not in summaries[0]

    def test_stats_empty(self, store):
        stats = store.get_accuracy_stats()
        assert stats["total"] == 0

    def test_multiple_predictions(self, store):
        for i in range(3):
            report = FinalReport(
                target=f"00000{i}", timeframe="短期(1周)",
                direction=Direction.BULLISH if i % 2 == 0 else Direction.BEARISH,
                confidence=0.5 + i * 0.1, summary="测试",
            )
            store.save_prediction(
                target=f"00000{i}", timeframe="短期(1周)",
                report=report, agent_results=[], agents_used=["技术面分析师"],
                agents_failed=[], elapsed_seconds=10.0, llm_model="deepseek",
            )

        assert store.get_unverified_count() == 3
        preds = store.get_predictions()
        assert len(preds) == 3

    def test_save_with_failed_agents(self, store):
        report = FinalReport(
            target="000001", timeframe="短期(1周)",
            direction=Direction.NEUTRAL, confidence=0.3,
            summary="部分Agent失败",
        )
        pid = store.save_prediction(
            target="000001", timeframe="短期(1周)",
            report=report, agent_results=[],
            agents_used=["技术面分析师"],
            agents_failed=["新闻分析师", "宏观分析师"],
            elapsed_seconds=15.0, llm_model="deepseek",
        )

        rec = store.get_prediction(pid)
        assert rec.agents_failed == ["新闻分析师", "宏观分析师"]

    def test_fundamental_validation_updates_calibrator(self, store, monkeypatch):
        """验证完成后，公司前景分析师样本应回写专用校准器。"""
        import src.utils.fundamental_calibrator as calibrator_module

        class DummyCalibrator:
            calls = []
            save_calls = 0

            def update_from_validation(self, **kwargs):
                self.__class__.calls.append(kwargs)

            def save(self):
                self.__class__.save_calls += 1

        monkeypatch.setattr(
            calibrator_module,
            "FundamentalConfidenceCalibrator",
            DummyCalibrator,
        )

        data_summary = {
            "data_quality": {"overall_quality": 0.76},
            "quality_scorecard": {"rating": "good"},
            "valuation_analysis": {"pe_percentile_3yr": 0.35},
        }
        with store._conn() as conn:
            conn.execute(
                """INSERT INTO agent_results
                   (prediction_id, agent_name, direction, confidence, data_summary)
                   VALUES (?,?,?,?,?)""",
                (
                    "pred-1",
                    "公司前景分析师",
                    "bullish",
                    0.68,
                    json.dumps(data_summary, ensure_ascii=False),
                ),
            )
            conn.commit()

        store._update_agent_calibration_from_verification("pred-1", "bullish", 4.2)

        assert DummyCalibrator.calls
        call = DummyCalibrator.calls[0]
        assert call["predicted_conf"] == 0.68
        assert call["was_correct"] is True
        assert call["data_quality_bucket"] == "high"
        assert call["scorecard_rating"] == "good"
        assert call["pe_percentile"] == 0.35
        assert call["actual_return_pct"] == 4.2
        assert DummyCalibrator.save_calls == 1

    def test_industry_validation_updates_calibrator(self, store, monkeypatch):
        """验证完成后，行业对比分析师样本应回写专用校准器。"""
        import src.utils.industry_calibrator as calibrator_module

        class DummyCalibrator:
            calls = []
            save_calls = 0

            def update_from_validation(self, **kwargs):
                self.__class__.calls.append(kwargs)

            def save(self):
                self.__class__.save_calls += 1

        monkeypatch.setattr(
            calibrator_module,
            "IndustryConfidenceCalibrator",
            DummyCalibrator,
        )

        data_summary = {
            "industry": "通信",
            "data_quality": {
                "overall": 0.82,
                "has_constituents": True,
                "has_trend": True,
            },
        }
        with store._conn() as conn:
            conn.execute(
                """INSERT INTO agent_results
                   (prediction_id, agent_name, direction, confidence, data_summary)
                   VALUES (?,?,?,?,?)""",
                (
                    "pred-2",
                    "行业对比分析师",
                    "bearish",
                    0.62,
                    json.dumps(data_summary, ensure_ascii=False),
                ),
            )
            conn.commit()

        store._update_agent_calibration_from_verification("pred-2", "bearish", -3.1)

        assert DummyCalibrator.calls
        call = DummyCalibrator.calls[0]
        assert call["predicted_conf"] == 0.62
        assert call["was_correct"] is True
        assert call["industry"] == "通信"
        assert call["data_quality_level"] == "constituents+trend"
        assert DummyCalibrator.save_calls == 1

    def test_macro_validation_updates_calibrator(self, store, monkeypatch):
        """验证完成后，国际形势分析师样本应回写专用校准器。"""
        import src.utils.macro_calibrator as calibrator_module

        class DummyCalibrator:
            calls = []
            save_calls = 0

            def update_from_validation(self, **kwargs):
                self.__class__.calls.append(kwargs)

            def save(self):
                self.__class__.save_calls += 1

        monkeypatch.setattr(
            calibrator_module,
            "MacroConfidenceCalibrator",
            DummyCalibrator,
        )

        data_summary = {
            "market": "HK",
            "sector": "互联网平台",
            "data_quality": {
                "overall_freshness": "82%",
                "reference_count": 0,
                "realtime_count": 9,
            },
        }
        with store._conn() as conn:
            conn.execute(
                """INSERT INTO agent_results
                   (prediction_id, agent_name, direction, confidence, data_summary)
                   VALUES (?,?,?,?,?)""",
                (
                    "pred-3",
                    "国际形势分析师",
                    "bearish",
                    0.64,
                    json.dumps(data_summary, ensure_ascii=False),
                ),
            )
            conn.commit()

        store._update_agent_calibration_from_verification("pred-3", "bearish", -2.8)

        assert DummyCalibrator.calls
        call = DummyCalibrator.calls[0]
        assert call["predicted_conf"] == 0.64
        assert call["was_correct"] is True
        assert call["market"] == "HK"
        assert call["sector"] == "互联网平台"
        assert call["data_quality_level"] == "fresh"
        assert DummyCalibrator.save_calls == 1

    def test_technical_validation_updates_calibrator(self, store, monkeypatch):
        """验证完成后，近期股价分析师样本应回写专用校准器。"""
        import src.utils.technical_calibrator as calibrator_module

        RealCalibrator = calibrator_module.TechnicalConfidenceCalibrator

        class DummyCalibrator(RealCalibrator):
            calls = []
            save_calls = 0

            def __init__(self, *args, **kwargs):
                pass

            def update_from_validation(self, **kwargs):
                self.__class__.calls.append(kwargs)

            def save(self):
                self.__class__.save_calls += 1

        monkeypatch.setattr(
            calibrator_module,
            "TechnicalConfidenceCalibrator",
            DummyCalibrator,
        )

        data_summary = {
            "evidence": {
                "decision_matrix": {
                    "trend_bucket": "up",
                    "momentum_bucket": "bullish",
                    "volume_bucket": "confirm_up",
                    "intraday_bucket": "unavailable",
                },
                "support_resistance": {
                    "resistance_distance_pct": 5.0,
                    "support_distance_pct": -8.0,
                },
            }
        }
        with store._conn() as conn:
            conn.execute(
                """INSERT INTO predictions
                   (id, target, timeframe, direction, confidence,
                    predicted_at, valid_until, agents_used)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    "pred-4",
                    "000001",
                    "短期(1周)",
                    "bullish",
                    0.60,
                    "2025-01-01T00:00:00",
                    "2025-01-08T00:00:00",
                    "[]",
                ),
            )
            conn.execute(
                """INSERT INTO agent_results
                   (prediction_id, agent_name, direction, confidence, data_summary)
                   VALUES (?,?,?,?,?)""",
                (
                    "pred-4",
                    "近期股价分析师",
                    "bullish",
                    0.72,
                    json.dumps(data_summary, ensure_ascii=False),
                ),
            )
            conn.commit()

        store._update_agent_calibration_from_verification("pred-4", "bullish", 3.2)

        assert DummyCalibrator.calls
        call = DummyCalibrator.calls[0]
        assert call["predicted_conf"] == 0.72
        assert call["was_correct"] is True
        assert call["trend_bucket"] == "up"
        assert call["volume_bucket"] == "confirm_up"
        assert call["position_bucket"] == "middle_range"
        assert call["timeframe_bucket"] == "短期"
        assert DummyCalibrator.save_calls == 1

    def test_news_validation_updates_calibrator(self, store, monkeypatch):
        """验证完成后，最新新闻分析师样本应回写专用校准器。"""
        import src.utils.news_calibrator as calibrator_module

        RealCalibrator = calibrator_module.NewsConfidenceCalibrator

        class DummyCalibrator(RealCalibrator):
            calls = []
            save_calls = 0

            def __init__(self, *args, **kwargs):
                pass

            def update_from_validation(self, **kwargs):
                self.__class__.calls.append(kwargs)

            def save(self):
                self.__class__.save_calls += 1

        monkeypatch.setattr(
            calibrator_module,
            "NewsConfidenceCalibrator",
            DummyCalibrator,
        )

        data_summary = {
            "evidence": {
                "news_window": {"news_count": 8, "sources_used": ["eastmoney", "sina"]},
                "source_quality": {"source_count": 2},
                "decision_matrix": {
                    "volume_bucket": "adequate",
                    "sentiment_bucket": "negative",
                    "event_bucket": "negative_catalyst",
                },
                "event_impact_matrix": {"average_time_weight": 0.8},
            }
        }
        with store._conn() as conn:
            conn.execute(
                """INSERT INTO agent_results
                   (prediction_id, agent_name, direction, confidence, data_summary)
                   VALUES (?,?,?,?,?)""",
                (
                    "pred-5",
                    "最新新闻分析师",
                    "bearish",
                    0.66,
                    json.dumps(data_summary, ensure_ascii=False),
                ),
            )
            conn.commit()

        store._update_agent_calibration_from_verification("pred-5", "bearish", -2.4)

        assert DummyCalibrator.calls
        call = DummyCalibrator.calls[0]
        assert call["predicted_conf"] == 0.66
        assert call["was_correct"] is True
        assert call["news_count_bucket"] == "adequate"
        assert call["source_bucket"] == "multi_source"
        assert call["event_bucket"] == "negative_catalyst"
        assert call["freshness_bucket"] == "fresh"
        assert DummyCalibrator.save_calls == 1


class TestBacktestConfig:
    """回测配置测试"""

    def test_default_config(self):
        from src.core.backtester import BacktestConfig
        cfg = BacktestConfig(
            target="000001",
            start_date="2025-01-01",
            end_date="2025-06-30",
        )
        assert cfg.target == "000001"
        assert cfg.interval_days == 7
        assert "近期股价分析师" in cfg.agents

    def test_custom_interval(self):
        from src.core.backtester import BacktestConfig
        cfg = BacktestConfig(
            target="000001", start_date="2025-01-01", end_date="2025-06-30",
            interval_days=30,
        )
        assert cfg.interval_days == 30

    def test_horizon_days(self):
        from src.core.backtester import Backtester

        assert Backtester._horizon_days("短期(1周)") == 7
        assert Backtester._horizon_days("中期(1月)") == 30
        assert Backtester._horizon_days("长期(1季)") == 90

    def test_historical_technical_agent_uses_snapshot(self):
        import asyncio
        from src.core.backtester import HistoricalTechnicalAnalyst

        class DummyLLM:
            pass

        class DummyPriceData:
            def to_agent_dict(self):
                return {"price_summary": {"latest_close": 10.0}}

        agent = HistoricalTechnicalAnalyst(DummyLLM(), DummyPriceData(), __import__("datetime").datetime(2025, 1, 2))
        data = asyncio.run(agent.gather_data("000001", "短期(1周)"))

        assert data["price_summary"]["latest_close"] == 10.0
        assert data["_backtest_as_of"] == "2025-01-02"


class TestBacktestReport:
    """回测报告测试"""

    def test_empty_report(self):
        from src.core.backtester import BacktestConfig, BacktestReport, BacktestResult
        cfg = BacktestConfig(
            target="000001", start_date="2025-01-01", end_date="2025-06-30",
        )
        report = BacktestReport(config=cfg, total_runs=10, success_runs=8, results=[])
        assert report.direction_accuracy == 0.0
        assert report.magnitude_accuracy == 0.0

    def test_report_with_results(self):
        from src.core.backtester import BacktestConfig, BacktestReport, BacktestResult
        cfg = BacktestConfig(
            target="000001", start_date="2025-01-01", end_date="2025-06-30",
        )
        results = [
            BacktestResult(
                date="2025-01-01", predicted_direction="bullish",
                predicted_min=1.0, predicted_max=5.0, predicted_confidence=0.7,
                actual_direction="bullish", actual_change_pct=3.0,
                direction_correct=True, magnitude_hit=True,
                price_start=10.0, price_end=10.3, elapsed_seconds=30,
            ),
            BacktestResult(
                date="2025-01-08", predicted_direction="bearish",
                predicted_min=-5.0, predicted_max=-1.0, predicted_confidence=0.6,
                actual_direction="bullish", actual_change_pct=2.0,
                direction_correct=False, magnitude_hit=False,
                price_start=10.3, price_end=10.5, elapsed_seconds=28,
            ),
        ]
        report = BacktestReport(config=cfg, total_runs=2, success_runs=2, results=results)
        assert report.direction_accuracy == 0.5
        assert report.magnitude_accuracy == 0.5
        assert report.avg_confidence == pytest.approx(0.65)

    def test_to_dict(self):
        from src.core.backtester import BacktestConfig, BacktestReport, BacktestResult
        cfg = BacktestConfig(
            target="000001", start_date="2025-01-01", end_date="2025-03-31",
        )
        results = [
            BacktestResult(
                date="2025-01-01", predicted_direction="bullish",
                predicted_min=1.0, predicted_max=5.0, predicted_confidence=0.7,
                actual_direction="bullish", actual_change_pct=3.0,
                direction_correct=True, magnitude_hit=True,
                price_start=10.0, price_end=10.3, elapsed_seconds=30,
            ),
        ]
        report = BacktestReport(config=cfg, total_runs=1, success_runs=1, results=results)
        d = report.to_dict()
        assert d["total_runs"] == 1
        assert d["direction_accuracy"] == 1.0
        assert len(d["results"]) == 1


class TestCaseRetriever:
    """RAG 检索器测试"""

    def test_features_to_text(self):
        from src.core.case_retriever import CaseRetriever
        retriever = CaseRetriever()
        text = retriever._features_to_text(
            "000001", "短期", {"ma_arrangement": "空头排列", "rsi": 42.0}
        )
        assert "000001" in text
        assert "空头排列" in text
        assert "rsi" in text

    def test_build_case_context_empty(self):
        from src.core.case_retriever import CaseRetriever
        retriever = CaseRetriever()
        ctx = retriever.build_case_context([])
        assert ctx == ""

    def test_build_case_context_with_cases(self):
        from src.core.case_retriever import CaseRetriever
        retriever = CaseRetriever()
        cases = [
            {
                "prediction_id": "abc", "target": "000001",
                "timeframe": "短期", "predicted_direction": "bearish",
                "actual_direction": "bearish", "actual_change_pct": -2.1,
                "direction_correct": 1, "similarity": 0.87,
            }
        ]
        ctx = retriever.build_case_context(cases)
        assert "📚" in ctx
        assert "000001" in ctx
        assert "bearish" in ctx
        assert "-2.1" in ctx
