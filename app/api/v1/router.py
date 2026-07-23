"""
API v1 路由聚合
"""
from fastapi import APIRouter

from app.api.v1.devices import router as devices_router
from app.api.v1.tasks import router as tasks_router
from app.api.v1.update import router as update_router
from app.services.task_engine import task_engine
from app.services.device_manager import device_manager
from app.services.websocket_manager import ws_manager
from app.services.screen_capture import screen_capture
from app.schemas.device import BatchTaskCreateRequest, BatchDeviceRequest

router = APIRouter(prefix="/api")

# 注册子路由
router.include_router(devices_router)
router.include_router(tasks_router)
router.include_router(update_router)


@router.get("/scripts")
async def list_available_scripts():
    """获取原项目中的可用脚本列表（顶层路径）"""
    return await task_engine.get_available_scripts()


@router.get("/tasks")
async def list_all_tasks():
    """获取所有设备的任务队列汇总"""
    result = []
    devices = await device_manager.get_devices()
    for device in devices:
        serial = device["serial"]
        tasks = await task_engine.get_queue(serial)
        result.append({"device_id": serial, "tasks": tasks})
    return result


@router.post("/tasks/batch-enqueue", status_code=201)
async def batch_enqueue_task(req: BatchTaskCreateRequest):
    """将同一个脚本批量分配到多台设备"""
    results = []
    errors = []
    for device_id in req.device_ids:
        try:
            task = await task_engine.enqueue(device_id, req.script_name)
            results.append({
                "device_id": device_id,
                "task_id": task.id,
                "status": "enqueued",
            })
        except ValueError as e:
            errors.append({
                "device_id": device_id,
                "error": str(e),
            })
    return {
        "success": len(errors) == 0,
        "results": results,
        "errors": errors,
        "total": len(req.device_ids),
        "succeeded": len(results),
        "failed": len(errors),
    }


@router.post("/tasks/batch-start")
async def batch_start_queues(req: BatchDeviceRequest):
    """批量启动设备队列"""
    results = []
    errors = []
    for device_id in req.device_ids:
        try:
            # 确保截图流正在运行
            if device_id not in screen_capture.active_streams:
                await screen_capture.start_stream(
                    device_id,
                    callback=lambda img: ws_manager.send_screenshot(device_id, img),
                    fps=2.0,
                )

            async def log_callback(did: str, tid: str, text: str):
                await ws_manager.send_log(did, text, task_id=tid)

            async def status_callback(did: str, tid: str, status: str):
                await ws_manager.send_status(did, tid, status)

            started = await task_engine.start_queue(
                device_id,
                log_callback=log_callback,
                status_callback=status_callback,
            )
            if started:
                results.append({"device_id": device_id, "status": "started"})
            else:
                errors.append({"device_id": device_id, "error": "队列已在运行中"})
        except Exception as e:
            errors.append({"device_id": device_id, "error": str(e)})
    return {
        "results": results,
        "errors": errors,
        "total": len(req.device_ids),
        "succeeded": len(results),
        "failed": len(errors),
    }


@router.post("/tasks/batch-stop")
async def batch_stop_queues(req: BatchDeviceRequest):
    """批量停止设备队列"""
    results = []
    errors = []
    for device_id in req.device_ids:
        try:
            await task_engine.stop_queue(device_id)
            results.append({"device_id": device_id, "status": "stopped"})
        except Exception as e:
            errors.append({"device_id": device_id, "error": str(e)})
    return {
        "results": results,
        "errors": errors,
        "total": len(req.device_ids),
        "succeeded": len(results),
        "failed": len(errors),
    }
