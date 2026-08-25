"""
统一日志配置
提供 setup_logging(level)：从环境变量读取日志级别，配置控制台 handler 与格式化。
Windows 下强制 stdout 使用 UTF-8 编码，避免中文日志在 GBK 控制台输出乱码。
"""

import logging
import os
import sys
from typing import Optional

# 控制台/日志格式：时间 | 级别 | 模块 | 消息
_DEFAULT_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 全局标记：防止重复配置 handler（重复调用会叠加处理器）
_configured = False


def setup_logging(level: Optional[str] = None) -> None:
    """初始化全局日志配置（幂等）

    Args:
        level: 日志级别字符串（INFO/DEBUG/WARNING/ERROR...）。
               为 None 时使用环境变量 LOG_LEVEL，默认 INFO。
    """
    global _configured

    # Windows CMD GBK 兼容：强制 stdout 使用 UTF-8
    # （原有 sys.stdout.reconfigure 逻辑整合进日志基建，与模块导入时机无关）
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8")
            except (AttributeError, OSError):
                # 某些环境（如重定向管道）可能不支持 reconfigure，忽略即可
                pass

    if level is None:
        level = os.environ.get("LOG_LEVEL", "INFO")

    root = logging.getLogger()
    root.setLevel(level.upper())

    if not _configured:
        handler = logging.StreamHandler(stream=sys.stdout)
        handler.setFormatter(
            logging.Formatter(
                _DEFAULT_FORMAT,
                datefmt=_DEFAULT_DATE_FORMAT,
            )
        )
        root.addHandler(handler)
        _configured = True
