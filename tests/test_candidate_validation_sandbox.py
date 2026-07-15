import pytest

from src.core.candidate_validation_sandbox import (
    CandidateSandboxConfig,
    CandidateValidationSandbox,
)
from src.core.technical_improvement_validation import (
    TechnicalImprovementRule,
    TechnicalImprovementValidationReport,
    TechnicalValidationDecision,
)
from src.core.technical_prompt_replay import (
    PromptReplayDecision,
    TechnicalPromptReplayReport,
)
from src.prompts.dynamic_overrides import (
    build_prompt_with_overrides,
    candidate_override_context,
)


def _news_evaluation_report():
    signals = [
        {
            "agent_name": "最新新闻分析师",
            "area": "prompt",
            "priority": "P1",
            "bucket_group": "event_buckets",
            "bucket": "rumor_driven",
            "sample_size": 12,
            "unique_cases": 6,
            "accuracy": 0.25,
            "avg_confidence": 0.68,
            "issue": "传闻主导场景低命中且过度自信",
            "recommendation": "加入传闻反例自检",
            "signal_type": "wrong_strategy",
        },
        {
            "agent_name": "最新新闻分析师",
            "area": "skill",
            "priority": "P1",
            "bucket_group": "sentiment_buckets",
            "bucket": "divergent",
            "sample_size": 10,
            "unique_cases": 5,
            "accuracy": 0.30,
            "avg_confidence": 0.62,
            "issue": "情绪分化场景误读",
            "recommendation": "增加事件确定性规则",
            "signal_type": "wrong_strategy",
        },
        {
            "agent_name": "最新新闻分析师",
            "area": "data_source",
            "priority": "P0",
            "bucket_group": "source_buckets",
            "bucket": "single_source",
            "sample_size": 18,
            "unique_cases": 7,
            "accuracy": 0.22,
            "avg_confidence": 0.70,
            "issue": "单来源新闻策略错误",
            "recommendation": "补官方公告来源",
            "signal_type": "wrong_strategy",
        },
    ]
    return {
        "total_samples": 40,
        "verified_predictions": 20,
        "improvement_signals": signals,
        "wrong_strategy_signals": signals,
        "strength_signals": [],
    }


def _technical_evaluation_report():
    signal = {
        "agent_name": "近期股价分析师",
        "area": "skill",
        "priority": "P1",
        "bucket_group": "technical_scenario_buckets",
        "bucket": "bear|near_resistance|high_volume",
        "sample_size": 24,
        "unique_cases": 8,
        "accuracy": 0.25,
        "avg_confidence": 0.66,
        "dominant_actual_direction": "bearish",
        "dominant_actual_rate": 0.65,
        "issue": "熊市阻力位放量场景误判",
        "recommendation": "强制复查阻力位放量假突破",
        "signal_type": "wrong_strategy",
    }
    return {
        "total_samples": 40,
        "verified_predictions": 20,
        "improvement_signals": [signal],
        "wrong_strategy_signals": [signal],
        "strength_signals": [],
    }


def _technical_prompt_evaluation_report():
    signal = {
        "agent_name": "近期股价分析师",
        "area": "prompt",
        "priority": "P1",
        "bucket_group": "technical_scenario_buckets",
        "bucket": "bear|near_resistance|high_volume",
        "sample_size": 24,
        "unique_cases": 8,
        "accuracy": 0.25,
        "avg_confidence": 0.66,
        "dominant_actual_direction": "bearish",
        "dominant_actual_rate": 0.65,
        "issue": "熊市阻力位放量场景 prompt 误判",
        "recommendation": "加强阻力位放量反例检查",
        "signal_type": "wrong_strategy",
    }
    return {
        "total_samples": 40,
        "verified_predictions": 20,
        "improvement_signals": [signal],
        "wrong_strategy_signals": [signal],
        "strength_signals": [],
    }


def _technical_prompt_batch_evaluation_report():
    batches = []
    all_signals = []
    for idx, bucket in enumerate([
        "bear|near_resistance|high_volume",
        "bull|near_support|low_volume",
        "sideways|mid_range|high_volume",
    ], start=1):
        signal = {
            "agent_name": "近期股价分析师",
            "area": "prompt",
            "priority": "P1",
            "bucket_group": "technical_scenario_buckets",
            "bucket": bucket,
            "sample_size": 24,
            "unique_cases": 8,
            "accuracy": 0.18,
            "avg_confidence": 0.66,
            "dominant_actual_direction": "bearish",
            "dominant_actual_rate": 0.65,
            "issue": f"batch {idx} 技术面 prompt 误判",
            "recommendation": f"加强 batch {idx} 反例检查",
            "signal_type": "wrong_strategy",
        }
        all_signals.append(signal)
        batches.append({
            "batch_id": f"train_batch_{idx:02d}",
            "total_samples": 80,
            "verified_predictions": 24,
            "improvement_signals": [signal],
            "wrong_strategy_signals": [signal],
            "strength_signals": [],
        })
    return {
        "total_samples": 240,
        "verified_predictions": 72,
        "improvement_signals": all_signals,
        "wrong_strategy_signals": all_signals,
        "strength_signals": [],
        "candidate_batches": batches,
    }


class EmptyDirectionValidator:
    def build_rules(self, evaluation_report, min_samples=10, min_unique_cases=5, **kwargs):
        return []

    def write_registry_skills(self, report, registry_path=None, holdout_report_path=None):
        return []


class PassingDirectionValidator:
    def build_rules(self, evaluation_report, min_samples=10, min_unique_cases=5, **kwargs):
        return [
            TechnicalImprovementRule(
                bucket_group="technical_scenario_buckets",
                bucket="bear|near_resistance|high_volume",
                area="skill",
                sample_size=24,
                unique_cases=8,
                accuracy=0.25,
                avg_confidence=0.66,
                action="force_bearish",
                dominant_actual_direction="bearish",
                dominant_actual_rate=0.65,
                conditions={
                    "market_regime_bucket": "bear",
                    "sr_zone_bucket": "near_resistance",
                    "volume_bucket": "high_volume",
                },
            )
        ]

    async def run(self, evaluation_report, training_report_path, holdout_config, output_dir, **kwargs):
        rules = self.build_rules(evaluation_report)
        decision = TechnicalValidationDecision(
            should_apply=True,
            reason="candidate 在 holdout 上达到更高命中率",
            baseline_accuracy=0.35,
            candidate_accuracy=0.45,
            accuracy_delta=0.10,
            holdout_samples=20,
            changed_predictions=4,
        )
        return TechnicalImprovementValidationReport(
            generated_at="2026-07-07T00:00:00",
            training_report_path=training_report_path,
            holdout_config=holdout_config.__dict__,
            rules=rules,
            decision=decision,
        )

    def write_registry_skills(self, report, registry_path=None, holdout_report_path=None):
        return ["technical_direction_policy_test"]


class EmptyConfidenceValidator:
    def build_confidence_rules(self, evaluation_report, min_samples=8, min_unique_cases=5, **kwargs):
        return []

    def write_confidence_registry_skills(self, report, registry_path=None, holdout_report_path=None):
        return []


class PassingPromptReplayHarness:
    async def run(self, config, output_dir):
        output_dir.mkdir(parents=True, exist_ok=True)
        decision = PromptReplayDecision(
            should_apply=True,
            reason="candidate prompt 在 LLM holdout replay 上提升命中率且风险指标未变差",
            baseline_accuracy=0.30,
            candidate_accuracy=0.45,
            accuracy_delta=0.15,
            baseline_brier=0.42,
            candidate_brier=0.33,
            brier_delta=0.09,
            baseline_overconfidence_rate=0.25,
            candidate_overconfidence_rate=0.15,
            overconfidence_delta=-0.10,
            holdout_samples=8,
            changed_predictions=3,
        )
        return TechnicalPromptReplayReport(
            generated_at="2026-07-07T00:00:00",
            candidate_root=str(config.candidate_root),
            config={},
            decision=decision,
        )


@pytest.mark.asyncio
async def test_candidate_sandbox_writes_isolated_candidates(tmp_path):
    sandbox = CandidateValidationSandbox()
    report = await sandbox.run(
        _news_evaluation_report(),
        config=CandidateSandboxConfig(
            project_root=tmp_path,
            output_dir=tmp_path / "output",
            candidate_id="cand001",
            min_samples=5,
            min_unique_cases=5,
            validate_technical=False,
        ),
        source_report_path="output/evaluation.json",
    )

    candidate_root = tmp_path / "config" / "agent_improvement" / "candidates" / "cand001"
    formal_root = tmp_path / "config" / "agent_improvement"

    assert report.summary["artifacts"] == 2
    assert (candidate_root / "prompt_guardrails" / "news.md").exists()
    assert list((candidate_root / "skills").glob("news_*.md"))
    assert not (formal_root / "prompt_guardrails" / "news.md").exists()
    assert not (formal_root / "skills").exists()
    assert report.protected_recommendations


@pytest.mark.asyncio
async def test_candidate_sandbox_writes_batch_isolated_technical_candidates(tmp_path):
    sandbox = CandidateValidationSandbox()
    report = await sandbox.run(
        _technical_prompt_batch_evaluation_report(),
        config=CandidateSandboxConfig(
            project_root=tmp_path,
            output_dir=tmp_path / "output",
            candidate_id="cand_batch",
            min_samples=20,
            min_unique_cases=5,
            validate_technical=False,
            candidate_batch_count=3,
        ),
        source_report_path="output/evaluation.json",
    )

    candidate_root = tmp_path / "config" / "agent_improvement" / "candidates" / "cand_batch"

    assert report.summary["artifacts"] == 3
    assert all("train_batch_" in artifact.title for artifact in report.artifacts)
    assert all(len(artifact.source_signals) == 1 for artifact in report.artifacts)
    assert len(list((candidate_root / "artifacts").glob("*.json"))) == 3
    assert (candidate_root / "prompt_guardrails" / "technical.md").exists()


def test_candidate_prompt_loader_requires_explicit_candidate_context(tmp_path):
    candidate_root = tmp_path / "config" / "agent_improvement" / "candidates" / "cand002"
    (candidate_root / "prompt_guardrails").mkdir(parents=True)
    (candidate_root / "skills").mkdir(parents=True)
    (candidate_root / "prompt_guardrails" / "news.md").write_text(
        "候选 prompt 内容",
        encoding="utf-8",
    )
    (candidate_root / "skills" / "news_candidate.md").write_text(
        "候选 skill 内容",
        encoding="utf-8",
    )

    baseline = build_prompt_with_overrides("BASE", "最新新闻分析师", project_root=tmp_path)
    explicit = build_prompt_with_overrides(
        "BASE",
        "最新新闻分析师",
        project_root=tmp_path,
        candidate_root=candidate_root,
    )
    with candidate_override_context(candidate_root):
        contextual = build_prompt_with_overrides(
            "BASE",
            "最新新闻分析师",
            project_root=tmp_path,
        )

    assert "候选 prompt 内容" not in baseline
    assert "候选 skill 内容" not in baseline
    assert "候选 Prompt Guardrail" in explicit
    assert "候选 prompt 内容" in explicit
    assert "候选 skill 内容" in contextual


@pytest.mark.asyncio
async def test_candidate_sandbox_promotes_only_after_holdout_pass(tmp_path):
    sandbox = CandidateValidationSandbox(
        direction_validator=PassingDirectionValidator(),
        confidence_validator=EmptyConfidenceValidator(),
    )
    report = await sandbox.run(
        _technical_evaluation_report(),
        config=CandidateSandboxConfig(
            project_root=tmp_path,
            output_dir=tmp_path / "output",
            candidate_id="cand003",
            min_samples=20,
            min_unique_cases=5,
            apply_if_passed=True,
            allow_prompt_promotion=True,
            allow_skill_promotion=True,
            validate_technical=True,
            holdout_targets=["600900"],
        ),
        source_report_path="output/evaluation.json",
    )

    formal_skills = tmp_path / "config" / "agent_improvement" / "skills"

    assert report.status == "applied"
    assert report.registry_skill_ids == ["technical_direction_policy_test"]
    assert report.promoted_paths
    assert list(formal_skills.glob("technical_*.md"))
    assert any(artifact.status == "applied" for artifact in report.artifacts)


@pytest.mark.asyncio
async def test_candidate_sandbox_promotes_technical_prompt_after_llm_replay_pass(tmp_path):
    sandbox = CandidateValidationSandbox(
        llm=object(),
        direction_validator=EmptyDirectionValidator(),
        confidence_validator=EmptyConfidenceValidator(),
        prompt_replay_harness=PassingPromptReplayHarness(),
    )
    report = await sandbox.run(
        _technical_prompt_evaluation_report(),
        config=CandidateSandboxConfig(
            project_root=tmp_path,
            output_dir=tmp_path / "output",
            candidate_id="cand004",
            min_samples=20,
            min_unique_cases=5,
            apply_if_passed=True,
            allow_prompt_promotion=True,
            allow_skill_promotion=True,
            validate_technical=True,
            run_technical_prompt_replay=True,
            holdout_targets=["600900"],
        ),
        source_report_path="output/evaluation.json",
    )

    formal_prompt = (
        tmp_path
        / "config"
        / "agent_improvement"
        / "prompt_guardrails"
        / "technical.md"
    )

    assert report.status == "applied"
    assert formal_prompt.exists()
    assert any(
        decision["name"] == "technical_prompt_replay" and decision["should_apply"]
        for decision in report.decisions
    )
    assert any(artifact.area == "prompt" and artifact.status == "applied" for artifact in report.artifacts)
