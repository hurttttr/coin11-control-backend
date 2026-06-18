from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.core.constants import DeviceStatus, TaskStatus, ConnectionType


@dataclass
class Device:
    """设备数据模型"""
    serial: str
    model: str = ""
    status: DeviceStatus = DeviceStatus.OFFLINE
    connection_type: ConnectionType = ConnectionType.USB
    android_version: str = ""


@dataclass
class Task:
    """任务数据模型"""
    id: str
    device_id: str
    script_name: str
    script_path: str = ""
    status: TaskStatus = TaskStatus.PENDING
    position: int = 0
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    log: str = ""


@dataclass
class ScriptInfo:
    """原项目中的可用脚本信息"""
    name: str
    path: str
    description: str = ""


@dataclass
class UpdateInfo:
    """版本更新信息"""
    has_update: bool = False
    current_commit: str = ""
    latest_commit: str = ""
    commits_behind: int = 0
    commit_messages: list[str] = field(default_factory=list)
    checked_at: datetime = field(default_factory=datetime.now)
