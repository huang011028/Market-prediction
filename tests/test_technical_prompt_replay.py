import json
from datetime import datetime

import pandas as pd
import pytest

from src.core.prediction_target import default_target_spec
from src.core.result import AnalysisResult, Direction, Magnitude
from src.core.technical_prompt_replay import (
    TechnicalPromptReplayConfig,
    TechnicalPromptReplayHarness,
)


class FakePriceData:
    trading_days = 60
    price_current = 100.0

    def to_agent_dict(self):
        return {
            "trading_days": 60,
            "data_period": "fake",
            "price_summary": {"latest_close": 100.0},
            "technical_snapshot": {"ok": True},
            "indicators": {},
            "patterns": {},
        }


class FakePriceFetcher:
    async def fetch_as_of(self, target, as_of, lookback_days=180):
        return FakePriceData()

    async def fetch_close_window(self, target, start, end):
        return pd.Series([95.0], index=[pd.Timestamp(end)])


class FakeTechnicalAnalyst:
    name = "近期股价分析师"

    def __init__(self, llm):
        self.calls = 0

    def build_context(self, target, timeframe):
        spec = default_target_spec(timeframe, target=None).to_dict()
        spec["target_type"] = "absolute_return"
        spec["benchmark_symbol"] = None
        return {
            "target": target,
            "timeframe": timeframe,
            "agent_name": self.name,
            "prediction_target": spec,
        }

    async def analyze(self, data, context):
        self.calls += 1
        if self.calls == 1:
            return AnalysisResult(
                agent_name=self.name,
                target=context["target"],
                timeframe=context["timeframe"],
                direction=Direction.BULLISH,
                magnitude=Magnitude(min_pct=1.5, max_pct=3.0),
                confidence=0.80,
                prediction_target=context["prediction_target"],
                reasoning="baseline bullish",
                key_factors=["baseline"],
            )
        return AnalysisResult(
            agent_name=self.name,
            target=context["target"],
            timeframe=context["timeframe"],
            direction=Direction.BEARISH,
            magnitude=Magnitude(min_pct=-3.0, max_pct=-1.5),
            confidence=0.70,
            prediction_target=context["prediction_target"],
            reasoning="candidate bearish",
            key_factors=["candidate"],
        )


class SameDirectionTechnicalAnalyst(FakeTechnicalAnalyst):
    async def analyze(self, data, context):
        self.calls += 1
        return AnalysisResult(
            agent_name=self.name,
            target=context["target"],
            timeframe=context["timeframe"],
            direction=Direction.BULLISH,
            magnitude=Magnitude(min_pct=1.5, max_pct=3.0),
            confidence=0.80,
            prediction_target=context["prediction_target"],
            reasoning="same bullish",
            key_factors=["same"],
        )


@pytest.mark.asyncio
async def test_technical_prompt_replay_detects_candidate_improvement(tmp_path):
    harness = TechnicalPromptReplayHarness(
        llm=object(),
        price_fetcher=FakePriceFetcher(),
        analyst_factory=lambda llm: FakeTechnicalAnalyst(llm),
    )

    report = await harness.run(
        TechnicalPromptReplayConfig(
            targets=["FAKE"],
            start_date="2025-01-01",
            end_date="2025-01-15",
            interval_days=7,
            candidate_root=tmp_path / "candidate",
            max_samples=2,
            min_samples=2,
            min_accuracy_delta=0.01,
            min_brier_delta=0.0,
        ),
        output_dir=tmp_path / "replay",
    )

    assert report.decision.should_apply is True
    assert report.decision.baseline_accuracy == 0.0
    assert report.decision.candidate_accuracy == 1.0
    assert report.decision.changed_predictions == 2
    assert (tmp_path / "replay" / "technical_prompt_replay.json").exists()


@pytest.mark.asyncio
async def test_technical_prompt_replay_applies_candidate_runtime_guardrail(tmp_path, monkeypatch):
    candidate_root = tmp_path / "candidate"
    content_path = candidate_root / "prompt_guardrails" / "technical.md"
    artifact_path = candidate_root / "artifacts" / "technical_prompt_guardrail.json"
    content_path.parent.mkdir(parents=True)
    artifact_path.parent.mkdir(parents=True)
    content_path.write_text("候选技术面 guardrail", encoding="utf-8")
    artifact_path.write_text(
        json.dumps({
            "artifact_id": "technical_prompt_guardrail",
            "agent_name": "近期股价分析师",
            "area": "prompt",
            "title": "低命中场景置信度上限",
            "content_path": str(content_path),
            "source_signals": [
                {
                    "bucket_group": "technical_scenario_buckets",
                    "bucket": "bear|near_resistance|high_volume",
                    "sample_size": 40,
                    "unique_cases": 12,
                    "accuracy": 0.10,
                }
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        TechnicalPromptReplayHarness,
        "_technical_buckets",
        staticmethod(lambda analyst, data, context: {
            "market_regime_bucket": "bear",
            "sr_zone_bucket": "near_resistance",
            "volume_bucket": "high_volume",
            "technical_scenario_bucket": "bear|near_resistance|high_volume",
        }),
    )
    harness = TechnicalPromptReplayHarness(
        llm=object(),
        price_fetcher=FakePriceFetcher(),
        analyst_factory=lambda llm: SameDirectionTechnicalAnalyst(llm),
    )

    report = await harness.run(
        TechnicalPromptReplayConfig(
            targets=["FAKE"],
            start_date="2025-01-01",
            end_date="2025-01-15",
            interval_days=7,
            candidate_root=candidate_root,
            max_samples=2,
            min_samples=2,
            min_accuracy_delta=0.0,
            min_brier_delta=0.0,
        ),
        output_dir=tmp_path / "replay_guardrail",
    )

    assert report.decision.should_apply is True
    assert report.decision.changed_directions == 0
    assert report.decision.changed_confidences == 2
    assert report.decision.candidate_guardrail_hits == 2
    assert report.decision.brier_delta > 0
    assert report.candidate_manifest["rules"]
    assert report.samples[0].candidate.confidence == 0.35
    assert report.samples[0].candidate.applied_overrides
