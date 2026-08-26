"""
设备管理端点
"""
import logging

from fastapi import APIRouter, HTTPException

from app.schemas.device import (
    DeviceConnectRequest,
    DevicePairRequest,
)
from app.services.auto_task_runner import run_auto_tasks
from app.services.auto_task_settings import auto_task_settings
from app.services.device_manager import device_manager
from app.services.network_info import get_network_info
from app.services.screen_capture import screen_capture
from app.services.task_engine import task_engine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/devices", tags=["devices"])


@router.get("")
async def list_devices():
    """获取所有已连接的 ADB 设备列表

    注意：自动任务触发由后台 AutoTaskWatcher 负责（v0.2.1 起），
    本端点保持 GET 幂等，不再对设备执行入队/启动等副作用操作。
    """
    devices = await device_manager.get_devices()
    logger.info("发现 %s 台设备: %s", len(devices), [d["serial"] for d in devices])
    return devices


@router.post("/connect")
async def connect_device(req: DeviceConnectRequest):
    """远程连接 ADB 设备 (IP:Port)，连接后自动执行已配置的自动任务"""
    result = await device_manager.connect_device(req.address)
    # 连接成功后立即触发自动任务（watcher 最长要等一个扫描周期，这里即时触发）
    if result.get("success") and auto_task_settings.has_auto_tasks():
        await run_auto_tasks(req.address)
    return result


@router.post("/pair")
async def pair_device(req: DevicePairRequest):
    """ADB 配对 (Android 11+ 无线调试配对)"""
    return await device_manager.pair_device(req.address, req.code)


@router.get("/network-info")
async def network_info():
    """获取局域网网段与本机 IP，供前端自动预填配对/连接地址

    自动探测失败或探测到非局域网网段时回退 LAN_SUBNET_OVERRIDE。
    返回 {"subnet": "192.168.1", "host_ip": "192.168.1.10"}
    """
    return get_network_info()


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
        ) from None


@router.get("/{serial}/queue")
async def get_device_queue(serial: str):
    """获取设备任务队列"""
    return await task_engine.get_queue(serial)