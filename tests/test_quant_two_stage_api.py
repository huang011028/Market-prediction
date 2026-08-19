import json

from fastapi.testclient import TestClient

import api_server
import scripts.run_quant_two_stage as runner


def test_two_stage_api_runs_project_local_frozen_config(monkeypatch):
    captured = {}

    def fake_run(config_path, experiment_id=None):
        captured["config_path"] = config_path
        captured["experiment_id"] = experiment_id
        return {
            "version": "quant_two_stage.v1",
            "report_path": "/tmp/report.json",
            "two_stage": {"promotion_gate": {"should_promote": False}},
            "portfolio": {},
        }

    monkeypatch.setattr(runner, "run", fake_run)
    response = TestClient(api_server.app).post(
        "/api/quant/two-stage",
        json={
            "config_path": "config/quant/two_stage_v1.json",
            "experiment_id": "api_contract_test",
        },
    )

    assert response.status_code == 200
    assert captured["config_path"].name == "two_stage_v1.json"
    assert captured["experiment_id"] == "api_contract_test"
    assert response.json()["report"]["version"] == "quant_two_stage.v1"


def test_two_stage_api_rejects_paths_outside_project():
    response = TestClient(api_server.app).post(
        "/api/quant/two-stage",
        json={"config_path": "/tmp/two_stage.json"},
    )

    assert response.status_code == 400


def test_latest_two_stage_status_returns_lightweight_summary(tmp_path, monkeypatch):
    path = tmp_path / "quant_two_stage_pipeline_report.json"
    path.write_text(json.dumps({
        "experiment_id": "formal-1",
        "version": "quant_two_stage.v1",
        "two_stage": {
            "folds": [{"fold": 1}],
            "promotion_gate": {"best_model": "candidate", "should_promote": False},
            "aggregate_metrics": {
                "candidate": {
                    "brier_score": 0.17,
                    "gate_brier_delta": 0.001,
                    "rank_ic": 0.03,
                    "actionable_coverage": 0.09,
                    "top_k_mean_return_pct": -0.2,
                },
            },
        },
        "portfolio": {
            "candidate": {"promotion_gate": {"should_promote": False}},
        },
    }), encoding="utf-8")
    monkeypatch.setattr(api_server, "_latest_files", lambda pattern, limit: [{
        "path": str(path), "name": path.name, "updated_at": "2026-07-16T00:00:00",
    }])

    item = api_server._latest_two_stage_reports(limit=1)[0]

    assert item["summary"]["experiment_id"] == "formal-1"
    assert item["summary"]["rank_ic"] == 0.03
    assert item["summary"]["portfolio_candidates"] == 1
    assert "two_stage" not in item
