"""
设备管理端点
"""
from fastapi import APIRouter, HTTPException

from app.schemas.device import (
    DeviceConnectRequest,
    DevicePairRequest,
)
from app.services.device_manager import device_manager
from app.services.screen_capture import screen_capture
from app.services.task_engine import task_engine
from app.services.auto_task_settings import auto_task_settings
from app.services.auto_task_runner import run_auto_tasks, is_triggered

router = APIRouter(prefix="/devices", tags=["devices"])


@router.get("")
async def list_devices():
    """获取所有已连接的 ADB 设备，新设备自动触发自动任务"""
    devices = await device_manager.get_devices()
    print(f"[list_devices] 发现 {len(devices)} 台设备: {[d['serial'] for d in devices]}")
    # 对轮询发现的新设备触发自动任务（去重由 auto_task_runner 保证）
    if auto_task_settings.has_auto_tasks():
        for d in devices:
            serial = d["serial"]
            if not is_triggered(serial):
                print(f"[list_devices] 新设备 {serial}，触发自动任务")
                await run_auto_tasks(serial)
            else:
                print(f"[list_devices] 设备 {serial} 已触发过，跳过")
    else:
        print("[list_devices] 未配置自动任务")
    return devices


@router.post("/connect")
async def connect_device(req: DeviceConnectRequest):
    """远程连接 ADB 设备 (IP:Port)，连接后自动执行已配置的自动任务"""
    result = await device_manager.connect_device(req.address)
    # 连接成功后自动触发自动任务
    if result.get("success") and auto_task_settings.has_auto_tasks():
        await run_auto_tasks(req.address)
    return result


@router.post("/pair")
async def pair_device(req: DevicePairRequest):
    """ADB 配对 (Android 11+ 无线调试配对)"""
    stdout, stderr, rc = await device_manager._run_adb("pair", req.address, req.code, timeout=30)
    msg = stdout.strip() or stderr.strip()
    success = "successfully paired" in msg.lower() or "配对成功" in msg
    return {
        "success": success,
        "message": msg,
    }


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
