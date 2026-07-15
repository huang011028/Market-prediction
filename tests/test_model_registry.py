from config.settings import Settings
from src.core.model_registry import DEFAULT_MODEL_ID, LLMModelRegistry


def _settings() -> Settings:
    return Settings(
        LLM_API_KEY="sk-test-secret",
        LLM_BASE_URL="https://api.example.com/v1",
        LLM_MODEL="default-model",
        LLM_TEMPERATURE=0.2,
        LLM_MAX_TOKENS=2048,
    )


def test_registry_exposes_env_default_without_file(tmp_path):
    registry = LLMModelRegistry(path=tmp_path / "models.json", settings=_settings())

    state = registry.public_state(llm_ready=True)

    assert state["active_model_id"] == DEFAULT_MODEL_ID
    assert state["active_model"]["model"] == "default-model"
    assert state["active_model"]["api_key_hint"] == "sk-****cret"
    assert state["active_model"]["has_api_key"] is True


def test_registry_adds_and_switches_user_model(tmp_path):
    registry = LLMModelRegistry(path=tmp_path / "models.json", settings=_settings())

    model = registry.add_model(
        {
            "name": "OpenAI Test",
            "provider": "openai",
            "base_url": "https://api.openai.com/v1/",
            "model": "gpt-test",
            "api_key": "sk-user-secret",
            "temperature": 0,
            "max_tokens": 8192,
        },
        activate=True,
    )

    active = registry.get_active_model()
    state = registry.public_state(llm_ready=True)

    assert active.id == model.id
    assert active.base_url == "https://api.openai.com/v1"
    assert active.temperature == 0
    assert state["active_model"]["api_key_hint"] == "sk-****cret"
    assert "api_key" not in state["active_model"]


def test_registry_delete_active_model_falls_back_to_default(tmp_path):
    registry = LLMModelRegistry(path=tmp_path / "models.json", settings=_settings())
    model = registry.add_model(
        {
            "name": "Qwen Test",
            "provider": "qwen",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "model": "qwen-test",
        },
        activate=True,
    )

    registry.delete_model(model.id)

    assert registry.active_model_id() == DEFAULT_MODEL_ID
    assert registry.get_active_model().model == "default-model"


def test_registry_updates_user_model_without_exposing_secret(tmp_path):
    registry = LLMModelRegistry(path=tmp_path / "models.json", settings=_settings())
    model = registry.add_model(
        {
            "name": "GLM Test",
            "provider": "zhipu",
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "model": "glm-4.7",
            "api_key": "sk-user-secret",
            "verify_ssl": True,
        },
        activate=True,
    )

    updated = registry.update_model(model.id, {"verify_ssl": False})
    state = registry.public_state(llm_ready=True)

    assert updated.verify_ssl is False
    assert registry.get_active_model().api_key == "sk-user-secret"
    assert state["active_model"]["verify_ssl"] is False
    assert "api_key" not in state["active_model"]
