from fastapi.testclient import TestClient

import api_server
from config.settings import Settings
from src.core.model_registry import LLMModelRegistry


def test_model_registry_api_adds_and_activates_model(tmp_path, monkeypatch):
    settings = Settings(
        LLM_API_KEY="sk-default-secret",
        LLM_BASE_URL="https://api.default.test/v1",
        LLM_MODEL="default-model",
    )
    registry = LLMModelRegistry(path=tmp_path / "models.json", settings=settings)
    monkeypatch.setattr(api_server, "model_registry", registry)
    monkeypatch.setattr(api_server, "llm", registry.create_client())

    client = TestClient(api_server.app)

    listed = client.get("/api/models")
    assert listed.status_code == 200
    assert listed.json()["active_model"]["model"] == "default-model"

    created = client.post(
        "/api/models",
        json={
            "name": "Candidate Model",
            "provider": "custom",
            "base_url": "https://api.candidate.test/v1",
            "model": "candidate-model",
            "api_key": "sk-candidate-secret",
            "set_active": True,
        },
    )
    assert created.status_code == 200
    payload = created.json()
    assert payload["active_model"]["model"] == "candidate-model"
    assert payload["active_model"]["api_key_hint"] == "sk-****cret"
    assert "api_key" not in payload["active_model"]
    assert api_server.llm.model == "candidate-model"

    updated = client.patch(
        f"/api/models/{payload['active_model_id']}",
        json={"verify_ssl": False},
    )
    assert updated.status_code == 200
    assert updated.json()["active_model"]["verify_ssl"] is False
    assert api_server.llm.verify_ssl is False

    fallback = client.post("/api/models/active", json={"model_id": "env-default"})
    assert fallback.status_code == 200
    assert fallback.json()["active_model"]["model"] == "default-model"
