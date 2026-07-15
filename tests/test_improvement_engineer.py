import pytest

from src.agents.improvement_engineer import (
    AgentImprovementEngineer,
    ImprovementEngineerConfig,
)
from src.prompts.dynamic_overrides import build_prompt_with_overrides


def _evaluation_report():
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


@pytest.mark.asyncio
async def test_improvement_engineer_applies_only_prompt_and_declarative_skill(tmp_path):
    engineer = AgentImprovementEngineer()
    report = await engineer.run(
        _evaluation_report(),
        config=ImprovementEngineerConfig(
            project_root=tmp_path,
            output_dir=tmp_path / "output",
            min_samples_for_auto_apply=5,
            dry_run=False,
        ),
        source_report_path="output/evaluation.json",
    )

    prompt_path = tmp_path / "config" / "agent_improvement" / "prompt_guardrails" / "news.md"
    skill_dir = tmp_path / "config" / "agent_improvement" / "skills"

    assert prompt_path.exists()
    assert "rumor_driven" in prompt_path.read_text(encoding="utf-8")
    assert list(skill_dir.glob("news_*.md"))
    assert any(action.area == "data_source" and action.status == "protected" for action in report.actions)
    assert report.protected_recommendations


@pytest.mark.asyncio
async def test_improvement_engineer_respects_sample_threshold(tmp_path):
    engineer = AgentImprovementEngineer()
    report = await engineer.run(
        _evaluation_report(),
        config=ImprovementEngineerConfig(
            project_root=tmp_path,
            output_dir=tmp_path / "output",
            min_samples_for_auto_apply=100,
            dry_run=False,
        ),
    )

    assert all(action.status == "skipped" for action in report.actions)
    assert not (tmp_path / "config" / "agent_improvement").exists()


@pytest.mark.asyncio
async def test_improvement_engineer_respects_unique_case_threshold(tmp_path):
    engineer = AgentImprovementEngineer()
    report_payload = _evaluation_report()
    for signal in report_payload["improvement_signals"]:
        signal["unique_cases"] = 2

    report = await engineer.run(
        report_payload,
        config=ImprovementEngineerConfig(
            project_root=tmp_path,
            output_dir=tmp_path / "output",
            min_samples_for_auto_apply=5,
            min_unique_cases_for_auto_apply=5,
            dry_run=False,
        ),
    )

    assert all(action.status == "skipped" for action in report.actions)
    assert all(action.reason == "独立历史案例数未达到自动修改阈值" for action in report.actions)
    assert not (tmp_path / "config" / "agent_improvement").exists()


@pytest.mark.asyncio
async def test_improvement_engineer_ignores_strength_signals_for_auto_changes(tmp_path):
    engineer = AgentImprovementEngineer()
    strength_signal = {
        "agent_name": "近期股价分析师",
        "area": "skill",
        "priority": "P2",
        "bucket_group": "trend_buckets",
        "bucket": "up",
        "sample_size": 20,
        "unique_cases": 10,
        "accuracy": 0.80,
        "avg_confidence": 0.68,
        "issue": "高命中策略",
        "recommendation": "保留",
        "signal_type": "strength",
    }

    report = await engineer.run(
        {
            "total_samples": 20,
            "verified_predictions": 0,
            "improvement_signals": [strength_signal],
            "wrong_strategy_signals": [],
            "strength_signals": [strength_signal],
        },
        config=ImprovementEngineerConfig(
            project_root=tmp_path,
            output_dir=tmp_path / "output",
            min_samples_for_auto_apply=5,
            min_unique_cases_for_auto_apply=5,
        ),
    )

    assert report.actions == []
    assert not (tmp_path / "config" / "agent_improvement").exists()


@pytest.mark.asyncio
async def test_dynamic_prompt_loader_reads_engineer_outputs(tmp_path):
    engineer = AgentImprovementEngineer()
    await engineer.run(
        _evaluation_report(),
        config=ImprovementEngineerConfig(
            project_root=tmp_path,
            output_dir=tmp_path / "output",
            min_samples_for_auto_apply=5,
        ),
    )

    prompt = build_prompt_with_overrides(
        "BASE",
        "最新新闻分析师",
        project_root=tmp_path,
    )

    assert "BASE" in prompt
    assert "历史评估调优规则" in prompt
    assert "声明式 Skill 规则" in prompt
