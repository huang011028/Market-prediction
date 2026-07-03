"""
LLM 调用客户端

封装对 DeepSeek API（兼容 OpenAI 接口格式）的调用。
支持同步和异步两种方式，内置重试和错误处理。
"""

import asyncio
import time
import json
from dataclasses import dataclass, field
from typing import Optional

import aiohttp
import requests

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
        max_retries: int = 3,
        timeout_seconds: int = 120,
        verify_ssl: bool = True,
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
        self.max_retries = max_retries
        self.timeout = timeout_seconds
        self.verify_ssl = verify_ssl

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

        last_error: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                response = requests.post(
                    url,
                    headers=self._headers,
                    json=payload,
                    timeout=(30, self.timeout),
                    verify=self.verify_ssl,
                )

                # 处理 HTTP 错误
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 2**attempt))
                    time.sleep(retry_after)
                    continue

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

        last_error: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                connector = aiohttp.TCPConnector(ssl=self.verify_ssl)
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
                            retry_after = int(
                                response.headers.get("Retry-After", 2**attempt)
                            )
                            await asyncio.sleep(retry_after)
                            continue

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
        )

    # ================================================================
    # 内部方法
    # ================================================================

    def _build_payload(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: Optional[float] = None,
    ) -> dict:
        """构建 API 请求 payload"""
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
