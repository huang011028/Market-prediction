"""
全局配置系统

从 .env 文件和环境变量加载配置，提供类型安全的配置访问。
所有路径基于 PROJECT_ROOT 动态定位，不依赖本机绝对路径。
"""

from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv
import os


@dataclass
class Settings:
    """全局配置单例，从 .env 文件和环境变量加载"""

    # === 路径 ===
    PROJECT_ROOT: Path = field(
        default_factory=lambda: Path(__file__).resolve().parent.parent
    )

    # === LLM 配置（DeepSeek） ===
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = "https://api.deepseek.com/v1"
    LLM_MODEL: str = "deepseek-v4-pro"
    LLM_TEMPERATURE: float = 0.3
    LLM_MAX_TOKENS: int = 4096
    LLM_VERIFY_SSL: bool = True

    # === 超时 ===
    AGENT_TIMEOUT: int = 120  # 单个 Agent 最大执行秒数

    # === 日志 ===
    LOG_LEVEL: str = "INFO"

    # ================================================================
    # 工厂方法
    # ================================================================

    @classmethod
    def from_env(cls) -> "Settings":
        """从 .env 文件和环境变量加载配置，创建 Settings 实例"""
        env_path = cls._find_env_file()
        if env_path.exists():
            load_dotenv(env_path)

        return cls(
            LLM_API_KEY=os.getenv("LLM_API_KEY", ""),
            LLM_BASE_URL=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"),
            LLM_MODEL=os.getenv("LLM_MODEL", "deepseek-v4-pro"),
            LLM_TEMPERATURE=float(os.getenv("LLM_TEMPERATURE", "0.3")),
            LLM_MAX_TOKENS=int(os.getenv("LLM_MAX_TOKENS", "4096")),
            LLM_VERIFY_SSL=os.getenv("LLM_VERIFY_SSL", "true").lower() in ("true", "1", "yes"),
            AGENT_TIMEOUT=int(os.getenv("AGENT_TIMEOUT", "120")),
            LOG_LEVEL=os.getenv("LOG_LEVEL", "INFO"),
        )

    @staticmethod
    def _find_env_file() -> Path:
        """查找 .env 文件：项目根目录"""
        return Path(__file__).resolve().parent.parent / ".env"

    # ================================================================
    # 路径属性
    # ================================================================

    @property
    def data_dir(self) -> Path:
        """原始数据缓存目录"""
        return self.PROJECT_ROOT / "data"

    @property
    def output_dir(self) -> Path:
        """分析报告输出目录"""
        return self.PROJECT_ROOT / "output"

    @property
    def logs_dir(self) -> Path:
        """日志目录"""
        return self.PROJECT_ROOT / "logs"


# ================================================================
# 全局单例（懒加载）
# ================================================================

_settings: Settings | None = None


def get_settings() -> Settings:
    """获取全局配置单例（懒加载，首次调用时从 .env 加载）"""
    global _settings
    if _settings is None:
        _settings = Settings.from_env()
    return _settings


def reload_settings() -> Settings:
    """强制重新加载配置（用于测试或运行时更新）"""
    global _settings
    _settings = Settings.from_env()
    return _settings
