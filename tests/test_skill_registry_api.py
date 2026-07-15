from fastapi.testclient import TestClient

import api_server
from src.core.agent_skill_registry import AgentSkillRegistry


def test_skill_registry_api_lists_and_toggles_skill(tmp_path, monkeypatch):
    registry_path = tmp_path / "agent_skill_registry.json"
    registry = AgentSkillRegistry(registry_path)
    registry.upsert_skill({
        "skill_id": "technical.confidence_policy.test",
        "agent_name": "近期股价分析师",
        "skill_type": "confidence_policy",
        "enabled": True,
        "trigger_conditions": {"volume_bucket": "shrinking"},
        "action": {"type": "cap_confidence", "confidence_cap": 0.35},
        "validation": {
            "bucket_group": "volume_buckets",
            "bucket": "shrinking",
            "training_samples": 12,
            "holdout": {
                "holdout_samples": 8,
                "brier_delta": 0.02,
                "reason": "passed",
            },
        },
        "source": {
            "generated_by": "Agent 改进工程师",
            "training_report_path": "output/train.json",
            "holdout_report_path": "output/holdout.json",
        },
    })
    registry.save()

    monkeypatch.setattr(
        api_server,
        "AgentSkillRegistry",
        lambda *args, **kwargs: AgentSkillRegistry(registry_path),
    )

    client = TestClient(api_server.app)
    listed = client.get("/api/skills/registry")

    assert listed.status_code == 200
    payload = listed.json()
    assert payload["summary"]["enabled"] == 1
    assert payload["skills"][0]["validation_summary"]["brier_delta"] == 0.02

    toggled = client.patch(
        "/api/skills/registry/technical.confidence_policy.test",
        json={"enabled": False},
    )

    assert toggled.status_code == 200
    assert toggled.json()["skill"]["enabled"] is False
    assert AgentSkillRegistry(registry_path).skills[0].enabled is False
