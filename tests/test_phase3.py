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
