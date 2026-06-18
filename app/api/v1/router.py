"""
API v1 路由聚合
"""
from fastapi import APIRouter

from app.api.v1.devices import router as devices_router
from app.api.v1.tasks import router as tasks_router
from app.api.v1.update import router as update_router
from app.services.task_engine import task_engine
from app.services.device_manager import device_manager

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
