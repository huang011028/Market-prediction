"""
LLM 调用客户端

封装对 DeepSeek API（兼容 OpenAI 接口格式）的调用。
支持同步和异步两种方式，内置重试和错误处理。
"""

import asyncio
import time
import json
import ssl
import threading
from dataclasses import dataclass, field
from typing import Optional

import aiohttp
import requests

try:
    import certifi
except Exception:  # pragma: no cover - certifi is optional at runtime
    certifi = None

from config.settings import get_settings

# ================================================================
# 数据结构
# ================================================================


@dataclass
class LLMResponse:
    """LLM 返回的统一结构"""

    content: str
    model: str = ""
    usage: dict = field(default_factory=dict)  # {"prompt_tokens": N, "completion_tokens": M}

    @property
    def total_tokens(self) -> int:
        return self.usage.get("total_tokens", 0)


# ================================================================
# 异常定义
# ================================================================


class LLMError(Exception):
    """LLM 调用基础异常"""
    pass


class LLMRateLimitError(LLMError):
    """Rate Limit 异常"""
    pass


class LLMTimeoutError(LLMError):
    """超时异常"""
    pass


class LLMAuthenticationError(LLMError):
    """认证失败异常（API Key 无效）"""
    pass


# ================================================================
# LLM 客户端
# ================================================================


class LLMClient:
    """LLM API 调用客户端

    兼容 OpenAI 接口格式，默认使用 DeepSeek API。
    支持同步 (chat) 和异步 (achat) 两种调用方式。

    使用示例:
        client = LLMClient(api_key="sk-xxx")
        response = client.chat("你是分析师", "分析腾讯股价")
        print(response.content)
    """

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "https://api.deepseek.com/v1",
        model: str = "deepseek-v4-pro",
        temperature: float = 0.3,
        max_tokens: int = 4096,
        max_retries: Optional[int] = None,
        timeout_seconds: int = 120,
        verify_ssl: bool = True,
        max_concurrent_requests: Optional[int] = None,
        min_request_interval_seconds: Optional[float] = None,
    ):
        """
        Args:
            api_key: LLM API Key，留空则从 Settings 加载
            base_url: API 基础地址（兼容 OpenAI 格式）
            model: 模型名称
            temperature: 温度参数 (0~2)
            max_tokens: 最大输出 token 数
            max_retries: 最大重试次数
            timeout_seconds: 请求超时时间
            verify_ssl: 是否验证 SSL 证书。公司代理环境可能需要设为 False
        """
        if not api_key:
            settings = get_settings()
            api_key = settings.LLM_API_KEY

        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retries = int(max_retries if max_retries is not None else self._default_max_retries())
        self.timeout = timeout_seconds
        self.verify_ssl = verify_ssl
        self._ca_bundle_path = certifi.where() if (verify_ssl and certifi) else None
        self.max_concurrent_requests = max(
            1,
            int(max_concurrent_requests or self._default_max_concurrent_requests()),
        )
        self.max_prompt_chars = self._default_max_prompt_chars()
        if min_request_interval_seconds is None:
            min_request_interval_seconds = self._default_min_request_interval_seconds()
        self.min_request_interval_seconds = max(0.0, float(min_request_interval_seconds))
        self._sync_semaphore = threading.BoundedSemaphore(self.max_concurrent_requests)
        self._sync_rate_lock = threading.Lock()
        self._async_semaphore = asyncio.Semaphore(self.max_concurrent_requests)
        self._async_rate_lock = asyncio.Lock()
        self._last_request_started_at = 0.0
        self._rate_limited_until = 0.0

        # 请求头模板
        self._headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    # ================================================================
    # 同步调用
    # ================================================================

    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: Optional[float] = None,
    ) -> LLMResponse:
        """同步调用 LLM

        Args:
            system_prompt: 系统提示词
            user_prompt: 用户提示词
            temperature: 温度覆盖（None 使用默认值）

        Returns:
            LLMResponse

        Raises:
            LLMError: 调用失败
        """
        url = f"{self.base_url}/chat/completions"
        payload = self._build_payload(system_prompt, user_prompt, temperature)

        with self._sync_semaphore:
            self._raise_if_rate_limited()
            self._sync_wait_for_rate_slot()
            return self._chat_with_retries(url, payload)

    # ================================================================
    # 异步调用
    # ================================================================

    async def achat(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: Optional[float] = None,
    ) -> LLMResponse:
        """异步调用 LLM

        Args:
            system_prompt: 系统提示词
            user_prompt: 用户提示词
            temperature: 温度覆盖（None 使用默认值）

        Returns:
            LLMResponse

        Raises:
            LLMError: 调用失败
        """
        url = f"{self.base_url}/chat/completions"
        payload = self._build_payload(system_prompt, user_prompt, temperature)

        async with self._async_semaphore:
            self._raise_if_rate_limited()
            await self._async_wait_for_rate_slot()
            return await self._achat_with_retries(url, payload)

    def _chat_with_retries(self, url: str, payload: dict) -> LLMResponse:
        last_error: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                response = requests.post(
                    url,
                    headers=self._headers,
                    json=payload,
                    timeout=(30, self.timeout),
                    verify=self._requests_verify(),
                )

                if response.status_code == 429:
                    error_body = response.text[:500]
                    self._mark_rate_limited(response.headers, attempt)
                    last_error = LLMRateLimitError(
                        f"API 限流 (429): {error_body or 'Too Many Requests'}"
                    )
                    if attempt < self.max_retries:
                        time.sleep(self._retry_after_seconds(response.headers, attempt))
                        continue
                    raise last_error

                if response.status_code == 401:
                    raise LLMAuthenticationError(
                        "API Key 无效或未配置。请检查 .env 中的 LLM_API_KEY"
                    )

                if response.status_code != 200:
                    error_body = response.text[:500]
                    raise LLMError(
                        f"API 返回错误 ({response.status_code}): {error_body}"
                    )

                data = response.json()
                return self._parse_response(data)

            except (requests.Timeout, requests.ConnectionError) as e:
                last_error = e
                if attempt < self.max_retries:
                    wait = 2**attempt
                    time.sleep(wait)
                continue

            except LLMError:
                raise

            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    wait = 2**attempt
                    time.sleep(wait)

        raise LLMError(f"LLM 调用失败，已重试 {self.max_retries} 次: {last_error}")

    async def _achat_with_retries(self, url: str, payload: dict) -> LLMResponse:
        last_error: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                connector = aiohttp.TCPConnector(ssl=self._aiohttp_ssl_context())
                async with aiohttp.ClientSession(connector=connector) as session:
                    async with session.post(
                        url,
                        headers=self._headers,
                        json=payload,
                        timeout=aiohttp.ClientTimeout(
                            total=self.timeout + 30,
                            connect=30,
                        ),
                    ) as response:
                        if response.status == 429:
                            error_body = (await response.text())[:500]
                            self._mark_rate_limited(response.headers, attempt)
                            last_error = LLMRateLimitError(
                                f"API 限流 (429): {error_body or 'Too Many Requests'}"
                            )
                            if attempt < self.max_retries:
                                await asyncio.sleep(
                                    self._retry_after_seconds(response.headers, attempt)
                                )
                                continue
                            raise last_error

                        if response.status == 401:
                            raise LLMAuthenticationError(
                                "API Key 无效或未配置。请检查 .env 中的 LLM_API_KEY"
                            )

                        if response.status != 200:
                            error_body = await response.text()
                            error_body = error_body[:500]
                            raise LLMError(
                                f"API 返回错误 ({response.status}): {error_body}"
                            )

                        data = await response.json()
                        return self._parse_response(data)

            except (asyncio.TimeoutError, aiohttp.ClientError) as e:
                last_error = e
                if attempt < self.max_retries:
                    wait = 2**attempt
                    await asyncio.sleep(wait)
                continue

            except LLMError:
                raise

            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    wait = 2**attempt
                    await asyncio.sleep(wait)

        raise LLMError(f"LLM 调用失败，已重试 {self.max_retries} 次: {last_error}")

    # ================================================================
    # 工厂方法
    # ================================================================

    def with_model(self, model: str) -> "LLMClient":
        """返回使用指定模型的新客户端副本

        用于不同 Agent 使用不同模型但共享其他配置的场景。

        Args:
            model: 新的模型名称

        Returns:
            新的 LLMClient 实例
        """
        return LLMClient(
            api_key=self.api_key,
            base_url=self.base_url,
            model=model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            max_retries=self.max_retries,
            timeout_seconds=self.timeout,
            verify_ssl=self.verify_ssl,
            max_concurrent_requests=self.max_concurrent_requests,
            min_request_interval_seconds=self.min_request_interval_seconds,
        )

    def with_temperature(self, temperature: float) -> "LLMClient":
        """返回使用指定温度的新客户端副本"""
        return LLMClient(
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.model,
            temperature=temperature,
            max_tokens=self.max_tokens,
            max_retries=self.max_retries,
            timeout_seconds=self.timeout,
            verify_ssl=self.verify_ssl,
            max_concurrent_requests=self.max_concurrent_requests,
            min_request_interval_seconds=self.min_request_interval_seconds,
        )

    # ================================================================
    # 内部方法
    # ================================================================

    def _default_max_concurrent_requests(self) -> int:
        if self._is_rate_sensitive_model():
            return 1
        return 4

    def _default_max_retries(self) -> int:
        if self._is_rate_sensitive_model():
            return 1
        return 3

    def _is_rate_sensitive_model(self) -> bool:
        identity = f"{self.base_url} {self.model}".lower()
        return "bigmodel.cn" in identity or self.model.lower().startswith("glm")

    def _default_min_request_interval_seconds(self) -> float:
        if self._is_rate_sensitive_model():
            return 3.0
        return 0.0

    def _default_max_prompt_chars(self) -> int:
        if self._is_rate_sensitive_model():
            return 3500
        return 0

    def _raise_if_rate_limited(self) -> None:
        remaining = self._rate_limited_until - time.monotonic()
        if remaining > 0:
            raise LLMRateLimitError(
                f"API 仍处于限流冷却期，约 {remaining:.0f} 秒后可重试"
            )

    def _mark_rate_limited(self, headers, attempt: int) -> None:
        cooldown = self._retry_after_seconds(headers, attempt)
        if self._is_rate_sensitive_model():
            cooldown = max(cooldown, 90)
        self._rate_limited_until = max(
            self._rate_limited_until,
            time.monotonic() + cooldown,
        )

    def _sync_wait_for_rate_slot(self) -> None:
        if self.min_request_interval_seconds <= 0:
            return
        with self._sync_rate_lock:
            elapsed = time.monotonic() - self._last_request_started_at
            wait = self.min_request_interval_seconds - elapsed
            if wait > 0:
                time.sleep(wait)
            self._last_request_started_at = time.monotonic()

    async def _async_wait_for_rate_slot(self) -> None:
        if self.min_request_interval_seconds <= 0:
            return
        async with self._async_rate_lock:
            elapsed = time.monotonic() - self._last_request_started_at
            wait = self.min_request_interval_seconds - elapsed
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request_started_at = time.monotonic()

    @staticmethod
    def _retry_after_seconds(headers, attempt: int) -> int:
        raw = headers.get("Retry-After") if headers else None
        try:
            value = int(float(raw))
        except (TypeError, ValueError):
            value = 2**attempt
        return max(1, min(value, 30))

    def _requests_verify(self):
        if not self.verify_ssl:
            return False
        return self._ca_bundle_path or True

    def _aiohttp_ssl_context(self):
        if not self.verify_ssl:
            return False
        if self._ca_bundle_path:
            return ssl.create_default_context(cafile=self._ca_bundle_path)
        return True

    def _build_payload(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: Optional[float] = None,
    ) -> dict:
        """构建 API 请求 payload"""
        system_prompt = self._compact_system_prompt(system_prompt)
        user_prompt = self._compact_user_prompt(user_prompt)
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
        }

    def _compact_system_prompt(self, system_prompt: str) -> str:
        if self.max_prompt_chars <= 0:
            return system_prompt
        return (
            system_prompt
            + "\n\n[模型执行约束] 当前模型需走低延迟路径：只输出紧凑 JSON；"
            "reasoning 不超过 160 个汉字；key_factors 不超过 4 条；risks 不超过 3 条；不要输出 Markdown。"
        )

    def _compact_user_prompt(self, user_prompt: str) -> str:
        if self.max_prompt_chars <= 0 or len(user_prompt) <= self.max_prompt_chars:
            return user_prompt
        head_chars = int(self.max_prompt_chars * 0.68)
        tail_chars = self.max_prompt_chars - head_chars
        omitted = len(user_prompt) - self.max_prompt_chars
        return (
            user_prompt[:head_chars]
            + f"\n\n[系统提示: 当前模型上下文较慢，已折叠中间 {omitted} 个字符；请只基于保留数据和明确可见证据输出。]\n\n"
            + user_prompt[-tail_chars:]
        )

    def _parse_response(self, data: dict) -> LLMResponse:
        """解析 API 返回数据"""
        try:
            choice = data["choices"][0]
            return LLMResponse(
                content=choice["message"]["content"],
                model=data.get("model", self.model),
                usage=data.get("usage", {}),
            )
        except (KeyError, IndexError) as e:
            raise LLMError(f"无法解析 LLM 返回: {e}\n原始数据: {json.dumps(data, ensure_ascii=False)[:500]}")


# ================================================================
# 便捷工厂函数
# ================================================================


def create_llm_client() -> LLMClient:
    """使用全局配置创建 LLMClient 实例"""
    settings = get_settings()
    return LLMClient(
        api_key=settings.LLM_API_KEY,
        base_url=settings.LLM_BASE_URL,
        model=settings.LLM_MODEL,
        temperature=settings.LLM_TEMPERATURE,
        max_tokens=settings.LLM_MAX_TOKENS,
        timeout_seconds=settings.AGENT_TIMEOUT,
        verify_ssl=settings.LLM_VERIFY_SSL,
    )
