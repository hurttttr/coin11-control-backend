"""设备队列启动共享助手

把"确保截图流运行 + 构造日志/状态回调 + 调用 task_engine.start_queue"这三件套
收敛到一个函数，供 tasks / router(批量) / auto_task_runner 三处调用，
避免同一逻辑被复制多份后各自漂移。

截图帧推送回调使用 functools.partial 绑定 device_id，避免循环变量晚绑定
导致截图广播到错误的设备。
"""

import logging
from functools import partial

from app.services.screen_capture import screen_capture
from app.services.task_engine import task_engine
from app.services.websocket_manager import ws_manager

logger = logging.getLogger(__name__)

# 截图流帧率（三处调用点共用同一魔数，收敛到此处统一维护）
SCREENCAST_FPS = 2.0


async def start_device_queue(device_id: str, *, ensure_screencast: bool = True) -> bool:
    """确保截图流运行并启动设备队列，返回 task_engine.start_queue 的结果。

    - ensure_screencast=True 时，若该设备尚未有截图流则自动启动（回调经
      functools.partial 绑定 device_id，杜绝批量循环中的闭包晚绑定）；
    - 日志/状态回调统一在此构造，推送到 WebSocket；
    - 并发启动去重由 task_engine.start_queue 自身的 _running 状态保证
      （不在此处缓存 asyncio.Lock——跨事件循环复用会报 "Event loop is closed"）。
    """
    if ensure_screencast and device_id not in screen_capture.active_streams:
        await screen_capture.start_stream(
            device_id,
            callback=partial(ws_manager.send_screenshot, device_id),
            fps=SCREENCAST_FPS,
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
    if not started:
        logger.info("队列已在运行中，跳过重复启动: %s", device_id)
    return started
