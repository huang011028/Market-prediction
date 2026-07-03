"""
LLM 响应缓存

简单的 prompt→响应缓存，减少重复 API 调用。

策略：
- 基于 prompt 前 200 字符的 hash 做 key
- TTL 默认 30 分钟
- 仅缓存"通用知识类"调用（如信号提取、宏观评估），不缓存"新闻数据相关"调用
"""

import hashlib
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class LLMCache:
    """轻量级 LLM 响应缓存"""

    def __init__(self, ttl_seconds: int = 1800, max_entries: int = 200):
        """
        Args:
            ttl_seconds: 缓存有效期（秒），默认 30 分钟
            max_entries: 最大缓存条目
        """
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._cache: dict[str, tuple[float, str]] = {}  # key → (timestamp, response)

    def _make_key(self, system_prompt: str, user_prompt: str) -> str:
        """生成缓存 key（基于 prompt 内容的 hash）"""
        # 只取 prompt 前 300 字符做 hash（快速）
        content = (system_prompt[:300] + user_prompt[:300]).encode("utf-8")
        return hashlib.md5(content).hexdigest()[:12]

    def get(self, system_prompt: str, user_prompt: str) -> Optional[str]:
        """查找缓存"""
        key = self._make_key(system_prompt, user_prompt)
        if key in self._cache:
            ts, response = self._cache[key]
            if time.time() - ts < self.ttl:
                logger.debug(f"LLM 缓存命中 ({key})")
                return response
            else:
                del self._cache[key]
        return None

    def set(self, system_prompt: str, user_prompt: str, response: str):
        """存入缓存"""
        key = self._make_key(system_prompt, user_prompt)

        # 淘汰最旧的条目
        if len(self._cache) >= self.max_entries:
            oldest_key = min(self._cache, key=lambda k: self._cache[k][0])
            del self._cache[oldest_key]

        self._cache[key] = (time.time(), response)

    def clear(self):
        """清空缓存"""
        self._cache.clear()

    @property
    def size(self) -> int:
        return len(self._cache)


# 全局单例
_llm_cache: Optional[LLMCache] = None


def get_llm_cache() -> LLMCache:
    """获取全局 LLM 缓存实例"""
    global _llm_cache
    if _llm_cache is None:
        _llm_cache = LLMCache()
    return _llm_cache
