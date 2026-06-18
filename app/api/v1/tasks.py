"""
任务队列管理端点
"""
from fastapi import APIRouter, HTTPException, Query

from app.schemas.device import TaskCreateRequest, QueueReorderRequest
from app.services.task_engine import task_engine
from app.services.websocket_manager import ws_manager
from app.services.screen_capture import screen_capture

router = APIRouter(prefix="/devices/{device_id}/queue", tags=["tasks"])


@router.get("")
async def get_queue(device_id: str):
    """获取设备的任务队列"""
    return await task_engine.get_queue(device_id)


@router.post("", status_code=201)
async def enqueue_task(device_id: str, req: TaskCreateRequest):
    """添加任务到设备队列"""
    try:
        task = await task_engine.enqueue(device_id, req.script)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return task.to_dict()


@router.delete("/{task_id}")
async def dequeue_task(device_id: str, task_id: str):
    """从队列中移除任务"""
    try:
        success = await task_engine.dequeue(device_id, task_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not success:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 未找到")
    return {"success": True}


@router.put("/reorder")
async def reorder_queue(device_id: str, req: QueueReorderRequest):
    """重新排序队列"""
    tasks = await task_engine.reorder(device_id, req.order)
    return [t.to_dict() for t in tasks]


@router.post("/start")
async def start_queue(device_id: str):
    """
    启动队列执行
    自动集成 WebSocket 日志推送、状态推送和截图流
    """
    # 构建回调闭包
    async def log_callback(did: str, tid: str, text: str):
        await ws_manager.send_log(did, text, task_id=tid)

    async def status_callback(did: str, tid: str, status: str):
        await ws_manager.send_status(did, tid, status)

    started = await task_engine.start_queue(
        device_id,
        log_callback=log_callback,
        status_callback=status_callback,
    )
    if not started:
        raise HTTPException(status_code=400, detail="队列已在运行中")

    return {"success": True, "message": "队列已启动"}


@router.post("/stop")
async def stop_queue(device_id: str):
    """停止队列执行"""
    await task_engine.stop_queue(device_id)
    # 同时停止截图流
    await screen_capture.stop_stream(device_id)
    return {"success": True, "message": "队列已停止"}


@router.get("/scripts")
async def get_available_scripts():
    """获取原项目中的可用脚本列表"""
    scripts = await task_engine.get_available_scripts()
    return scripts
