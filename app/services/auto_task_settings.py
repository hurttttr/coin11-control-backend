"""
自动任务设置服务
保存/读取自动运行任务配置（JSON 文件）
"""

import json
import os

SETTINGS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "auto_task_settings.json")


class AutoTaskSettings:
    """自动任务设置管理"""

    def __init__(self):
        self._cache: list[str] | None = None

    def _load(self) -> list[str]:
        """从 JSON 文件加载自动任务列表"""
        if self._cache is not None:
            return self._cache
        if os.path.isfile(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._cache = data.get("auto_tasks", [])
                    return self._cache
            except (json.JSONDecodeError, OSError):
                pass
        self._cache = []
        return self._cache

    def get_auto_tasks(self) -> list[str]:
        """获取自动运行任务脚本名列表"""
        return self._load()

    def set_auto_tasks(self, tasks: list[str]) -> None:
        """设置自动运行任务脚本名列表"""
        self._cache = tasks
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump({"auto_tasks": tasks}, f, ensure_ascii=False, indent=2)

    def has_auto_tasks(self) -> bool:
        """是否有配置自动任务"""
        return len(self._load()) > 0


# 全局单例
auto_task_settings = AutoTaskSettings()
