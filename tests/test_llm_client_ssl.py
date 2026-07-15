import ssl
import asyncio

import pytest

from src.core.llm_client import LLMClient, LLMRateLimitError, LLMResponse


def test_llm_client_uses_ca_bundle_when_ssl_verification_enabled():
    client = LLMClient(
        api_key="sk-test",
        base_url="https://example.com/v1",
        model="test-model",
        verify_ssl=True,
    )

    assert client._requests_verify() is not False
    assert isinstance(client._aiohttp_ssl_context(), ssl.SSLContext) or client._aiohttp_ssl_context() is True


def test_llm_client_can_disable_ssl_verification():
    client = LLMClient(
        api_key="sk-test",
        base_url="https://example.com/v1",
        model="test-model",
        verify_ssl=False,
    )

    assert client._requests_verify() is False
    assert client._aiohttp_ssl_context() is False


def test_glm_client_defaults_to_serial_requests():
    client = LLMClient(
        api_key="sk-test",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        model="glm-4.7",
        min_request_interval_seconds=0,
    )

    assert client.max_concurrent_requests == 1


def test_glm_client_compacts_long_user_prompts():
    client = LLMClient(
        api_key="sk-test",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        model="glm-4.7",
        min_request_interval_seconds=0,
    )
    prompt = "A" * 7000 + "TAIL"

    payload = client._build_payload("system", prompt)
    compacted = payload["messages"][1]["content"]

    assert len(compacted) < len(prompt)
    assert "已折叠中间" in compacted
    assert compacted.startswith("A")
    assert compacted.endswith("TAIL")


def test_async_glm_requests_are_serialized():
    async def run_check():
        client = LLMClient(
            api_key="sk-test",
            base_url="https://open.bigmodel.cn/api/paas/v4",
            model="glm-4.7",
            min_request_interval_seconds=0,
        )
        active = 0
        max_active = 0

        async def fake_call(url, payload):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return LLMResponse(content="OK", model="glm-4.7")

        client._achat_with_retries = fake_call
        await asyncio.gather(*(client.achat("system", "user") for _ in range(5)))
        assert max_active == 1

    asyncio.run(run_check())


def test_rate_limit_error_preserves_response_body(monkeypatch):
    class FakeResponse:
        status_code = 429
        text = '{"error":"too many requests"}'
        headers = {}

    monkeypatch.setattr("src.core.llm_client.requests.post", lambda *args, **kwargs: FakeResponse())
    client = LLMClient(
        api_key="sk-test",
        base_url="https://example.com/v1",
        model="test-model",
        max_retries=1,
    )

    with pytest.raises(LLMRateLimitError, match="too many requests"):
        client.chat("system", "user")
