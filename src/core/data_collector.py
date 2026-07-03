"""
数据采集基类

为所有数据获取器提供统一接口，预留缓存机制。
Phase 0 仅定义接口，Phase 1+ 由各 Agent 实现具体数据获取。
"""

import hashlib
import json
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


class BaseDataCollector(ABC):
    """数据采集抽象基类

    所有数据获取器（股价、新闻、财报等）继承此类，
    实现 fetch() 方法即可。

    内置缓存机制（Phase 3 实现）：
    - 基于 target + kwargs 生成缓存键
    - 支持 TTL 过期
    - JSON 文件存储
    """

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        cache_ttl_hours: int = 24,
    ):
        """
        Args:
            cache_dir: 缓存目录，None 时禁用缓存
            cache_ttl_hours: 缓存有效期（小时）
        """
        self.cache_dir = cache_dir
        self.cache_ttl_hours = cache_ttl_hours

    @abstractmethod
    async def fetch(self, target: str, **kwargs) -> dict:
        """获取原始数据

        Args:
            target: 标的代码，如 "0700.HK"
            **kwargs: 额外参数（时间范围等）

        Returns:
            原始数据字典，格式由各子类自行定义
        """
        ...

    # ================================================================
    # 缓存接口（Phase 3 实现，Phase 0 仅定义接口）
    # ================================================================

    def _cache_key(self, target: str, **kwargs) -> str:
        """基于 target 和参数生成 MD5 缓存键"""
        raw = f"{self.__class__.__name__}_{target}_{sorted(kwargs.items())}"
        return hashlib.md5(raw.encode()).hexdigest()

    def _read_cache(self, cache_key: str) -> Optional[dict]:
        """从缓存读取数据

        Returns:
            缓存数据，不存在或过期返回 None
        """
        if self.cache_dir is None:
            return None

        cache_file = self.cache_dir / f"{cache_key}.json"
        if not cache_file.exists():
            return None

        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached = json.load(f)

            # 检查 TTL
            cached_at = cached.get("_cached_at")
            if cached_at:
                cached_time = datetime.fromisoformat(cached_at)
                if datetime.now() - cached_time > timedelta(hours=self.cache_ttl_hours):
                    # 过期，删除缓存文件
                    cache_file.unlink(missing_ok=True)
                    return None

            return cached.get("data")

        except (json.JSONDecodeError, KeyError, OSError):
            return None

    def _write_cache(self, cache_key: str, data: dict) -> None:
        """将数据写入缓存"""
        if self.cache_dir is None:
            return

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = self.cache_dir / f"{cache_key}.json"

        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "_cached_at": datetime.now().isoformat(),
                        "_cache_key": cache_key,
                        "data": data,
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
        except OSError:
            pass  # 缓存写入失败不应影响主流程
