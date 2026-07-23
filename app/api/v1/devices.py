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
from app.services.websocket_manager import ws_manager

router = APIRouter(prefix="/devices", tags=["devices"])


@router.get("")
async def list_devices():
    """获取所有已连接的 ADB 设备，新设备自动触发自动任务"""
    devices = await device_manager.get_devices()
    # 对轮询发现的新设备触发自动任务
    if auto_task_settings.has_auto_tasks():
        for d in devices:
            serial = d["serial"]
            if serial not in _auto_task_triggered:
                await _run_auto_tasks(serial)
    return devices


@router.post("/connect")
async def connect_device(req: DeviceConnectRequest):
    """远程连接 ADB 设备 (IP:Port)，连接后自动执行已配置的自动任务"""
    result = await device_manager.connect_device(req.address)
    # 连接成功后自动触发自动任务
    if result.get("success") and auto_task_settings.has_auto_tasks():
        await _run_auto_tasks(req.address)
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


# ---------- 自动任务触发器 ----------

# 记录已触发自动任务的设备，避免重复触发
_auto_task_triggered: set[str] = set()


async def _run_auto_tasks(device_id: str):
    """设备连接后自动入队并启动已配置的任务"""
    if device_id in _auto_task_triggered:
        return
    _auto_task_triggered.add(device_id)

    tasks = auto_task_settings.get_auto_tasks()
    print(f"[AutoTask] 设备 {device_id} 已连接，检查自动任务: {tasks}")
    if not tasks:
        print("[AutoTask] 无自动任务配置，跳过")
        return
    enqueued = 0
    for script_name in tasks:
        try:
            await task_engine.enqueue(device_id, script_name)
            enqueued += 1
            print(f"[AutoTask] ✅ 自动入队 {script_name} → {device_id}")
        except ValueError as e:
            print(f"[AutoTask] ❌ 入队失败 {script_name} → {device_id}: {e}")

    if enqueued == 0:
        print("[AutoTask] 没有成功入队的任务，跳过启动队列")
        return

    # 启动队列
    try:
        if device_id not in screen_capture.active_streams:
            await screen_capture.start_stream(
                device_id,
                callback=lambda img: ws_manager.send_screenshot(device_id, img),
                fps=2.0,
            )

        async def log_cb(did: str, tid: str, text: str):
            await ws_manager.send_log(did, text, task_id=tid)

        async def status_cb(did: str, tid: str, s: str):
            await ws_manager.send_status(did, tid, s)

        await task_engine.start_queue(device_id, log_callback=log_cb, status_callback=status_cb)
        print(f"[AutoTask] ✅ 队列已启动 → {device_id}")
    except Exception as e:
        print(f"[AutoTask] ❌ 启动队列失败 → {device_id}: {e}")
