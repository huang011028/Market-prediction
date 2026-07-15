import json
from dataclasses import dataclass

from fastapi.testclient import TestClient

import api_server


@dataclass
class _FakeSandboxReport:
    def to_dict(self):
        return {
            "candidate_id": "fake-candidate",
            "summary": {"artifacts": 1, "validated_passed": 0},
            "artifacts": [],
            "decisions": [],
            "candidate_root": "config/agent_improvement/candidates/fake-candidate",
        }


class _FakeCandidateSandbox:
    def __init__(self, *args, **kwargs):
        pass

    async def run(self, evaluation_report, config, source_report_path=None):
        assert evaluation_report["total_samples"] == 1
        assert config.min_samples == 5
        assert config.holdout_targets == ["600900"]
        assert source_report_path.endswith("evaluation.json")
        return _FakeSandboxReport()


class _FakeBootstrapReport:
    def summary(self):
        return "fake summary"

    def to_dict(self):
        return {
            "agent_name": "近期股价分析师",
            "success_samples": 24,
            "total_candidates": 24,
            "direction_accuracy": 0.25,
            "improvement_signals": [
                {
                    "agent_name": "近期股价分析师",
                    "area": "skill",
                    "bucket_group": "technical_scenario_buckets",
                    "bucket": "bear|near_resistance|high_volume",
                    "sample_size": 24,
                    "accuracy": 0.25,
                    "issue": "fake",
                    "recommendation": "fake",
                    "priority": "P1",
                }
            ],
        }


class _FakeTechnicalBootstrapper:
    async def run(self, config):
        assert config.targets == ["000001"]
        return _FakeBootstrapReport()


class _FakePromptLoopSandbox(_FakeCandidateSandbox):
    async def run(self, evaluation_report, config, source_report_path=None):
        assert evaluation_report["total_samples"] == 24
        assert any(signal["area"] == "prompt" for signal in evaluation_report["wrong_strategy_signals"])
        assert config.run_technical_prompt_replay is True
        assert config.use_llm_candidates is True
        assert source_report_path.endswith("technical_training_evaluation.json")
        return _FakeSandboxReport()


def test_candidate_sandbox_api_runs_with_existing_report(tmp_path, monkeypatch):
    report_path = tmp_path / "evaluation.json"
    report_path.write_text(
        json.dumps({"total_samples": 1, "wrong_strategy_signals": []}),
        encoding="utf-8",
    )
    monkeypatch.setattr(api_server, "_safe_project_path", lambda value: report_path)
    monkeypatch.setattr(api_server, "CandidateValidationSandbox", _FakeCandidateSandbox)

    client = TestClient(api_server.app)
    resp = client.post(
        "/api/improvement/candidate-sandbox",
        json={
            "report_path": str(report_path),
            "min_samples": 5,
            "min_unique_cases": 3,
            "validate_technical": True,
            "holdout_targets": "600900",
        },
    )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["success"] is True
    assert payload["report"]["candidate_id"] == "fake-candidate"
    assert payload["candidate_root"].endswith("config/agent_improvement/candidates/" + payload["report_paths"]["json"].split("/")[-2])


def test_technical_prompt_loop_api_runs_training_then_sandbox(monkeypatch):
    monkeypatch.setattr(api_server, "llm", object())
    monkeypatch.setattr(api_server, "TechnicalCalibrationBootstrapper", _FakeTechnicalBootstrapper)
    monkeypatch.setattr(api_server, "CandidateValidationSandbox", _FakePromptLoopSandbox)

    client = TestClient(api_server.app)
    resp = client.post(
        "/api/improvement/technical-prompt-loop",
        json={
            "targets": "000001",
            "start_date": "2025-01-01",
            "end_date": "2025-01-15",
            "holdout_targets": "600900",
            "prompt_replay_max_samples": 2,
            "prompt_replay_min_samples": 1,
        },
    )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["success"] is True
    assert payload["training_report"]["success_samples"] == 24
    assert payload["report"]["candidate_id"] == "fake-candidate"
