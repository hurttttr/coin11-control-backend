from enum import Enum


class DeviceStatus(str, Enum):
    """设备连接状态"""
    ONLINE = "online"
    OFFLINE = "offline"
    BUSY = "busy"


class TaskStatus(str, Enum):
    """任务执行状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ConnectionType(str, Enum):
    """设备连接方式"""
    USB = "usb"
    WIFI = "wifi"
