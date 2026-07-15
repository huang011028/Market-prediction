import json

from src.core.improvement_patch_planner import AgentImprovementPatchPlanner


def _signals():
    return [
        {
            "agent_name": "最新新闻分析师",
            "area": "prompt",
            "priority": "P1",
            "bucket_group": "event_buckets",
            "bucket": "rumor_driven",
            "sample_size": 8,
            "accuracy": 0.25,
            "issue": "传闻主导场景命中率低",
            "recommendation": "调整新闻事件归因 prompt",
        },
        {
            "agent_name": "最新新闻分析师",
            "area": "data_source",
            "priority": "P0",
            "bucket_group": "source_buckets",
            "bucket": "single_source",
            "sample_size": 12,
            "accuracy": 0.30,
            "issue": "单来源场景命中率低",
            "recommendation": "扩展新闻/公告数据源",
        },
        {
            "agent_name": "最新新闻分析师",
            "area": "skill",
            "priority": "P1",
            "bucket_group": "sentiment_buckets",
            "bucket": "divergent",
            "sample_size": 6,
            "accuracy": 0.33,
            "issue": "情绪分化识别不足",
            "recommendation": "补充事件确定性特征",
        },
    ]


def test_patch_planner_builds_prompt_data_source_skill_drafts():
    planner = AgentImprovementPatchPlanner()
    plan = planner.build_plan("最新新闻分析师", _signals())

    areas = {draft.area for draft in plan.drafts}
    assert areas == {"prompt", "data_source", "skill"}
    md = plan.to_markdown()
    assert "src/prompts/news_prompts.py" in md
    assert "config/news_sources.yaml" in md
    assert "src/data/news_preprocessor.py" in md
    assert "rumor_driven" in md


def test_patch_planner_writes_draft_files(tmp_path):
    planner = AgentImprovementPatchPlanner()
    plan = planner.build_plan("最新新闻分析师", _signals())
    written = planner.write_plan(plan, tmp_path)

    assert (tmp_path / "improvement_patch_plan.md").exists()
    assert (tmp_path / "improvement_patch_plan.json").exists()
    assert len(written["drafts"]) == 3

    payload = json.loads((tmp_path / "improvement_patch_plan.json").read_text(encoding="utf-8"))
    assert payload["agent_name"] == "最新新闻分析师"
    assert len(payload["drafts"]) == 3


def test_patch_planner_can_derive_signals_from_calibration_stats():
    report = {
        "agent_name": "最新新闻分析师",
        "calibration_stats": {
            "source_buckets": {
                "single_source": {"total": 6, "accuracy": 0.2}
            }
        },
    }

    plan = AgentImprovementPatchPlanner().build_plan_from_report(report, min_samples=5)

    assert len(plan.drafts) == 1
    assert plan.drafts[0].area == "data_source"
