import json
from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """应用配置，从 .env 文件加载"""

    # coin11-tb 原项目路径
    COIN11_TB_PATH: str = "D:\\lenovo\\Documents\\Code\\coin11-tb"

    # ADB 可执行文件路径
    ADB_PATH: str = "adb"

    # 服务监听地址
    HOST: str = "127.0.0.1"

    # 服务监听端口
    PORT: int = 8000

    # CORS 允许的来源
    CORS_ORIGINS: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    # DeepSeek API Key（可选，用于调试）
    DEEPSEEK_API_KEY: str | None = None

    # WebSocket 鉴权 Token（本地单用户场景用简单 token）
    WS_AUTH_TOKEN: str = "coin11-control-token"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        # 允许 CORS_ORIGINS 从 JSON 字符串解析
        "extra": "ignore",
    }

    @classmethod
    def parse_cors_origins(cls, v: str) -> list[str]:
        """支持从环境变量读取 JSON 数组字符串"""
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (json.JSONDecodeError, TypeError):
                return [v]
        return v


@lru_cache
def get_settings() -> Settings:
    """获取全局单例配置"""
    return Settings()
