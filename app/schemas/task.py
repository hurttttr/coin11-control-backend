"""任务相关 Schema（与 device.py 共享，此文件作为占位扩展用）"""

from app.schemas.device import TaskInfo, TaskCreateRequest, QueueReorderRequest

__all__ = ["TaskInfo", "TaskCreateRequest", "QueueReorderRequest"]
