"""
设备管理端点
"""
from fastapi import APIRouter, HTTPException

from app.schemas.device import (
    DeviceConnectRequest,
)
from app.services.device_manager import device_manager
from app.services.screen_capture import screen_capture
from app.services.task_engine import task_engine

router = APIRouter(prefix="/devices", tags=["devices"])


@router.get("")
async def list_devices():
    """获取所有已连接的 ADB 设备"""
    devices = await device_manager.get_devices()
    return devices


@router.post("/connect")
async def connect_device(req: DeviceConnectRequest):
    """远程连接 ADB 设备 (IP:Port)"""
    result = await device_manager.connect_device(req.address)
    return result


@router.delete("/{serial}")
async def disconnect_device(serial: str):
    """断开设备连接"""
    result = await device_manager.disconnect_device(serial)
    return result


@router.get("/{serial}")
async def get_device(serial: str):
    """获取单台设备的详细信息"""
    device = await device_manager.get_device_info(serial)
    if not device:
        raise HTTPException(status_code=404, detail=f"设备 {serial} 未找到")
    return device


@router.get("/{serial}/screenshot")
async def get_screenshot(serial: str):
    """获取设备单帧截图 (HTTP 降级方案)"""
    try:
        image_bytes = await screen_capture.capture_single(serial)
        import base64
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        return {"device_id": serial, "screenshot": b64, "format": "png"}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"截图失败: {str(e)}",
        )


@router.get("/{serial}/queue")
async def get_device_queue(serial: str):
    """获取设备任务队列"""
    return await task_engine.get_queue(serial)
