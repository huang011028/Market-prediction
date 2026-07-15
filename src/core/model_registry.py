"""
Runtime LLM model registry.

The registry keeps user-added model profiles in a local JSON file so the API can
switch the shared agent LLM client without rewriting .env or restarting the app.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config.settings import Settings
from src.core.llm_client import LLMClient


DEFAULT_MODEL_ID = "env-default"
DEFAULT_MODELS_PATH = Path(__file__).resolve().parents[2] / "config" / "llm_models.local.json"


@dataclass
class LLMModelConfig:
    id: str
    name: str
    provider: str
    base_url: str
    model: str
    api_key: str = ""
    temperature: float = 0.3
    max_tokens: int = 4096
    verify_ssl: bool = True
    source: str = "user"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LLMModelConfig":
        model_id = str(data.get("id") or "").strip()
        name = str(data.get("name") or "").strip()
        provider = str(data.get("provider") or "custom").strip() or "custom"
        base_url = str(data.get("base_url") or "").strip()
        model = str(data.get("model") or "").strip()
        if not model_id:
            raise ValueError("模型 ID 不能为空")
        if not name:
            raise ValueError("模型显示名称不能为空")
        if not base_url:
            raise ValueError("API Base URL 不能为空")
        if not model:
            raise ValueError("模型名称不能为空")
        return cls(
            id=model_id,
            name=name,
            provider=provider,
            base_url=base_url.rstrip("/"),
            model=model,
            api_key=str(data.get("api_key") or ""),
            temperature=float(data.get("temperature", 0.3)),
            max_tokens=int(data.get("max_tokens", 4096)),
            verify_ssl=bool(data.get("verify_ssl", True)),
            source=str(data.get("source") or "user"),
        )

    def to_dict(self, include_secret: bool = True) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "name": self.name,
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "verify_ssl": self.verify_ssl,
            "source": self.source,
        }
        if include_secret:
            payload["api_key"] = self.api_key
        return payload

    def to_public_dict(self, active: bool = False) -> dict[str, Any]:
        return {
            **self.to_dict(include_secret=False),
            "active": active,
            "has_api_key": bool(self.api_key),
            "api_key_hint": mask_api_key(self.api_key),
        }

    def to_client(self, settings: Settings) -> LLMClient:
        return LLMClient(
            api_key=self.api_key or settings.LLM_API_KEY,
            base_url=self.base_url or settings.LLM_BASE_URL,
            model=self.model or settings.LLM_MODEL,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            timeout_seconds=settings.AGENT_TIMEOUT,
            verify_ssl=self.verify_ssl,
        )


class LLMModelRegistry:
    def __init__(self, path: Path | None = None, settings: Settings | None = None):
        self.path = path or DEFAULT_MODELS_PATH
        self.settings = settings

    def list_models(self) -> list[LLMModelConfig]:
        data = self._load()
        models = [self._default_model()]
        models.extend(LLMModelConfig.from_dict(item) for item in data.get("models", []))
        seen = set()
        unique = []
        for model in models:
            if model.id in seen:
                continue
            seen.add(model.id)
            unique.append(model)
        return unique

    def active_model_id(self) -> str:
        data = self._load()
        active_id = str(data.get("active_model_id") or DEFAULT_MODEL_ID)
        if any(model.id == active_id for model in self.list_models()):
            return active_id
        return DEFAULT_MODEL_ID

    def get_active_model(self) -> LLMModelConfig:
        active_id = self.active_model_id()
        return self.get_model(active_id)

    def get_model(self, model_id: str) -> LLMModelConfig:
        model_id = str(model_id or "").strip()
        for model in self.list_models():
            if model.id == model_id:
                return model
        raise KeyError(f"模型不存在: {model_id}")

    def add_model(self, payload: dict[str, Any], activate: bool = False) -> LLMModelConfig:
        data = self._load()
        model_id = str(payload.get("id") or "").strip() or self._new_model_id(
            payload.get("provider"),
            payload.get("model"),
            payload.get("name"),
        )
        existing_ids = {model.id for model in self.list_models()}
        if model_id in existing_ids:
            raise ValueError(f"模型 ID 已存在: {model_id}")

        model = LLMModelConfig.from_dict({
            **payload,
            "id": model_id,
            "source": "user",
        })
        data.setdefault("models", []).append(model.to_dict(include_secret=True))
        if activate:
            data["active_model_id"] = model.id
        self._save(data)
        return model

    def set_active_model(self, model_id: str) -> LLMModelConfig:
        model = self.get_model(model_id)
        data = self._load()
        data["active_model_id"] = model.id
        data.setdefault("models", [])
        self._save(data)
        return model

    def update_model(self, model_id: str, updates: dict[str, Any]) -> LLMModelConfig:
        if model_id == DEFAULT_MODEL_ID:
            raise ValueError("默认 .env 模型不能在前端修改")
        data = self._load()
        for index, item in enumerate(data.get("models", [])):
            if item.get("id") != model_id:
                continue
            next_item = {**item}
            for key in (
                "name",
                "provider",
                "base_url",
                "model",
                "api_key",
                "temperature",
                "max_tokens",
                "verify_ssl",
            ):
                if key in updates and updates[key] is not None:
                    next_item[key] = updates[key]
            next_item["id"] = model_id
            next_item["source"] = "user"
            model = LLMModelConfig.from_dict(next_item)
            data["models"][index] = model.to_dict(include_secret=True)
            self._save(data)
            return model
        raise KeyError(f"模型不存在: {model_id}")

    def delete_model(self, model_id: str) -> None:
        if model_id == DEFAULT_MODEL_ID:
            raise ValueError("默认 .env 模型不能删除")
        data = self._load()
        before = len(data.get("models", []))
        data["models"] = [item for item in data.get("models", []) if item.get("id") != model_id]
        if len(data["models"]) == before:
            raise KeyError(f"模型不存在: {model_id}")
        if data.get("active_model_id") == model_id:
            data["active_model_id"] = DEFAULT_MODEL_ID
        self._save(data)

    def create_client(self, model_id: str | None = None) -> LLMClient:
        model = self.get_model(model_id) if model_id else self.get_active_model()
        return model.to_client(self._settings())

    def public_state(self, llm_ready: bool) -> dict[str, Any]:
        active_id = self.active_model_id()
        models = self.list_models()
        active_model = self.get_model(active_id)
        return {
            "active_model_id": active_id,
            "active_model": active_model.to_public_dict(active=True),
            "models": [model.to_public_dict(active=model.id == active_id) for model in models],
            "llm_ready": llm_ready,
            "registry_path": str(self.path),
        }

    def _default_model(self) -> LLMModelConfig:
        settings = self._settings()
        return LLMModelConfig(
            id=DEFAULT_MODEL_ID,
            name=".env 默认模型",
            provider="env",
            base_url=settings.LLM_BASE_URL,
            model=settings.LLM_MODEL,
            api_key=settings.LLM_API_KEY,
            temperature=settings.LLM_TEMPERATURE,
            max_tokens=settings.LLM_MAX_TOKENS,
            verify_ssl=settings.LLM_VERIFY_SSL,
            source="env",
        )

    def _settings(self) -> Settings:
        if self.settings is not None:
            return self.settings
        from config.settings import get_settings

        return get_settings()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"active_model_id": DEFAULT_MODEL_ID, "models": []}
        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"模型配置文件 JSON 无法解析: {e}") from e
        if not isinstance(data, dict):
            raise ValueError("模型配置文件格式错误")
        data.setdefault("active_model_id", DEFAULT_MODEL_ID)
        data.setdefault("models", [])
        return data

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")

    def _new_model_id(self, provider: Any, model: Any, name: Any) -> str:
        raw = "-".join(str(part or "") for part in (provider, model, name)).strip("-")
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", raw).strip("-").lower()
        slug = slug[:48].strip("-") or "model"
        existing = {model.id for model in self.list_models()}
        candidate = slug
        while candidate in existing:
            candidate = f"{slug}-{uuid.uuid4().hex[:6]}"
        return candidate


def mask_api_key(api_key: str) -> str:
    if not api_key:
        return ""
    if len(api_key) <= 8:
        return "****"
    return f"{api_key[:3]}****{api_key[-4:]}"
