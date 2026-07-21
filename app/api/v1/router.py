"""
API v1 路由聚合
"""
from fastapi import APIRouter

from app.api.v1.devices import router as devices_router
from app.api.v1.tasks import router as tasks_router
from app.api.v1.update import router as update_router
from app.services.task_engine import task_engine
from app.services.device_manager import device_manager
from app.schemas.device import BatchTaskCreateRequest

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
