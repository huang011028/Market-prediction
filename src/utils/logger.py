"""
日志系统

统一的日志配置，支持控制台和文件双输出。
通过 LOG_LEVEL 环境变量控制级别。
"""

import logging
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional

# 全局标记：是否已初始化
_logging_initialized: bool = False


def setup_logging(
    log_level: str = "INFO",
    log_dir: Optional[Path] = None,
    log_to_file: bool = True,
) -> None:
    """初始化全局日志配置

    应在程序入口处调用一次。重复调用不会重复添加 handler。

    Args:
        log_level: 日志级别 (DEBUG/INFO/WARNING/ERROR)
        log_dir: 日志文件目录，默认 PROJECT_ROOT/logs
        log_to_file: 是否同时写入文件
    """
    global _logging_initialized
    if _logging_initialized:
        return

    level = getattr(logging, log_level.upper(), logging.INFO)

    # 根 logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # 清除已有的 handler（避免重复）
    root_logger.handlers.clear()

    # 日志格式
    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(name)-20s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台 handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(fmt)
    root_logger.addHandler(console_handler)

    # 文件 handler
    if log_to_file:
        if log_dir is None:
            # 自动定位到项目根目录下的 logs/
            log_dir = Path(__file__).resolve().parent.parent.parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        today_str = datetime.now().strftime("%Y-%m-%d")
        log_file = log_dir / f"market-prediction-{today_str}.log"

        file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(fmt)
        root_logger.addHandler(file_handler)

    _logging_initialized = True
    logging.debug("日志系统初始化完成")


def get_logger(name: str) -> logging.Logger:
    """获取指定名称的 logger

    Args:
        name: logger 名称，通常使用 __name__

    Returns:
        配置好的 logger 实例
    """
    return logging.getLogger(name)
