from datetime import datetime
from typing import Optional

import re

from pydantic import BaseModel, Field, field_validator

from app.core.constants import DeviceStatus, ConnectionType, TaskStatus

_IPV4_PORT_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}:\d{2,5}$")


def _is_valid_ipv4_port(v: str) -> bool:
    """校验 IPv4:port 格式且各段 0-255、端口 1-65535。"""
    if not _IPV4_PORT_RE.match(v or ""):
        return False
    ip_part, port_part = v.rsplit(":", 1)
    for octet in ip_part.split("."):
        if not octet.isdigit() or not (0 <= int(octet) <= 255):
            return False
    if not port_part.isdigit() or not (1 <= int(port_part) <= 65535):
        return False
    return True


class DeviceInfo(BaseModel):
    """设备信息 Schema (响应)"""
    serial: str = Field(..., description="ADB 序列号")
    model: str = Field("", description="设备型号")
    status: DeviceStatus = Field(DeviceStatus.OFFLINE, description="连接状态")
    connection_type: ConnectionType = Field(ConnectionType.USB, description="连接方式")
    android_version: str = Field("", description="Android 版本")


class DeviceConnectRequest(BaseModel):
    """远程连接 ADB 请求"""
    address: str = Field(..., description="IP:Port 格式的远程地址", examples=["192.168.1.100:5555"])

    @field_validator("address")
    @classmethod
    def _validate_address(cls, v: str) -> str:
        if not _is_valid_ipv4_port(v):
            raise ValueError("地址格式必须为 IPv4:port")
        return v


class DevicePairRequest(BaseModel):
    """ADB 配对请求 (Android 11+ 无线配对)"""
    address: str = Field(..., description="配对地址 IP:Port", examples=["192.168.1.100:41339"])
    code: str = Field(..., description="六位配对码", examples=["123456"])

    @field_validator("address")
    @classmethod
    def _validate_address(cls, v: str) -> str:
        if not _is_valid_ipv4_port(v):
            raise ValueError("地址格式必须为 IPv4:port")
        return v

    @field_validator("code")
    @classmethod
    def _validate_code(cls, v: str) -> str:
        if not re.fullmatch(r"\d{6}", v):
            raise ValueError("配对码必须为 6 位数字")
        return v


class DeviceConnectResult(BaseModel):
    """设备连接结果"""
    success: bool
    device: Optional[DeviceInfo] = None
    message: str = ""


class TaskInfo(BaseModel):
    """任务信息 Schema (响应)"""
    id: str = Field(..., description="任务 ID")
    device_id: str = Field(..., description="所属设备序列号")
    script_name: str = Field(..., description="脚本名称")
    script_path: str = Field("", description="脚本完整路径")
    status: TaskStatus = Field(TaskStatus.PENDING, description="任务状态")
    position: int = Field(0, description="队列位置")
    created_at: datetime = Field(..., description="创建时间")
    started_at: Optional[datetime] = Field(None, description="开始时间")
    finished_at: Optional[datetime] = Field(None, description="完成时间")
    log: str = Field("", description="执行日志")


class TaskCreateRequest(BaseModel):
    """创建任务请求（同时兼容 script 和 script_name 字段名）"""
    script: str = Field(..., alias="script_name", description="脚本文件名", examples=["淘金币任务.py"])

    model_config = {"populate_by_name": True}


class QueueReorderRequest(BaseModel):
    """队列重排请求"""
    order: list[str] = Field(..., description="任务 ID 排序列表")


class BatchTaskCreateRequest(BaseModel):
    """批量分配任务请求"""
    script_name: str = Field(..., description="脚本文件名", examples=["淘金币任务.py"])
    device_ids: list[str] = Field(..., description="目标设备序列号列表")


class BatchDeviceRequest(BaseModel):
    """批量设备操作请求"""
    device_ids: list[str] = Field(..., description="目标设备序列号列表")


class HealthResponse(BaseModel):
    """健康检查响应"""
    status: str = "ok"
    adb_available: bool = False
    coin11_path_exists: bool = False


class ScriptInfo(BaseModel):
    """可用脚本信息"""
    name: str
    path: str
    description: str = ""
