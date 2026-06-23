import json
import os
from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """应用配置，从 .env 文件加载"""

    # coin11-tb 内置路径（空字符串表示使用默认内置路径）
    COIN11_TB_PATH: str = ""
    # coin11-tb 远程仓库地址
    COIN11_TB_REPO_URL: str = "https://github.com/czl0325/coin11-tb.git"

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

    @property
    def coin11_tb_path_resolved(self) -> str:
        """获取 coin11-tb 绝对路径

        如果 COIN11_TB_PATH 非空，直接使用该值；
        否则使用后端项目根目录下的 coin11_tb/ 内置路径
        """
        if self.COIN11_TB_PATH:
            return self.COIN11_TB_PATH
        # 默认：后端项目根目录下的 coin11_tb/
        backend_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return os.path.join(backend_root, "coin11_tb")

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
