"""自动任务触发服务 — 设备上线自动入队并启动已配置的任务

触发来源:
1. 后台监视循环 (AutoTaskWatcher): 周期扫描 ADB 设备，新设备自动触发
   —— 不依赖前端轮询/网页打开，后端启动后即自动工作
2. HTTP 端点 (POST /api/devices/connect): 用户主动连接时立即触发

两处共用 run_auto_tasks() 和 _auto_task_triggered 去重集合，避免重复触发。

设备消失 (retain_devices / forget_device) 时从去重集合中移除该 serial，
使其下次上线时能够重新触发，无需重启后端。
"""

import asyncio
import logging

from app.services.auto_task_settings import auto_task_settings
from app.services.device_manager import device_manager
from app.services.queue_control import start_device_queue
from app.services.task_engine import task_engine

logger = logging.getLogger(__name__)

# 记录已触发自动任务的设备，避免重复触发（进程生命周期内有效）
_auto_task_triggered: set[str] = set()

# 连续缺席计数：serial -> 连续多少轮扫描没看到它
# ADB 偶发抖动（USB 重枚举、WiFi 丢包）会让设备在单轮扫描里短暂消失，
# 若一消失就立刻遗忘，设备"回来"时会重复入队同一批自动任务。
# 因此要求连续缺席 MISS_THRESHOLD 轮才真正遗忘。
_absence_counter: dict[str, int] = {}
MISS_THRESHOLD = 2


def is_triggered(device_id: str) -> bool:
    """该设备是否已触发过自动任务"""
    return device_id in _auto_task_triggered


def forget_device(device_id: str) -> None:
    """忘记某设备的已触发状态，使其下次上线时重新触发（立即生效，不走缺席计数）"""
    _auto_task_triggered.discard(device_id)
    _absence_counter.pop(device_id, None)


def retain_devices(online: set[str]) -> None:
    """根据当前在线设备集合，清理已消失设备的已触发状态。

    设备掉线再上线时应重新触发自动任务，但 ADB 扫描存在偶发抖动
    （设备在单轮里短暂消失又出现）。若一消失就遗忘，设备回来时会
    重复入队同一批任务。因此这里用连续缺席计数：只有连续
    MISS_THRESHOLD 轮都没看到该设备，才真正从去重集合中移除。

    由 AutoTaskWatcher 每轮扫描调用；HTTP 触发路径无需调用。
    """
    # 在线设备的缺席计数清零
    for serial in online:
        _absence_counter.pop(serial, None)

    # 已触发但本轮不在线的设备：累加缺席计数，达到阈值才遗忘
    for serial in list(_auto_task_triggered - online):
        misses = _absence_counter.get(serial, 0) + 1
        if misses >= MISS_THRESHOLD:
            _auto_task_triggered.discard(serial)
            _absence_counter.pop(serial, None)
            logger.info("设备 %s 连续 %s 轮离线，重置其自动任务触发状态", serial, misses)
        else:
            _absence_counter[serial] = misses


async def run_auto_tasks(device_id: str):
    """设备连接后自动入队并启动已配置的任务"""
    if device_id in _auto_task_triggered:
        return
    _auto_task_triggered.add(device_id)

    tasks = auto_task_settings.get_auto_tasks()
    logger.info("设备 %s 已连接，检查自动任务: %s", device_id, tasks)
    if not tasks:
        logger.info("无自动任务配置，跳过")
        return
    enqueued = 0
    for script_name in tasks:
        try:
            await task_engine.enqueue(device_id, script_name)
            enqueued += 1
            logger.info("自动入队 %s → %s", script_name, device_id)
        except ValueError as e:
            logger.warning("入队失败 %s → %s: %s", script_name, device_id, e)

    if enqueued == 0:
        logger.info("没有成功入队的任务，跳过启动队列")
        return

    # 启动队列（截图流 + 日志/状态回调统一由 queue_control 负责）
    try:
        await start_device_queue(device_id)
        logger.info("队列已启动 → %s", device_id)
    except Exception as e:
        logger.warning("启动队列失败 → %s: %s", device_id, e)


class AutoTaskWatcher:
    """后台设备监视循环

    周期扫描 ADB 设备列表，对每个新出现的设备触发 run_auto_tasks；
    同时清理已消失设备的已触发状态，支持设备重连后重新触发。
    后端启动后即自动运行，不依赖前端打开网页 / 轮询 HTTP 接口。
    """

    def __init__(self, interval: float = 5.0):
        self.interval = interval
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """启动后台监视循环（幂等）"""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(), name="auto-task-watcher")
        logger.info("后台设备监视已启动 (interval=%ss)", self.interval)

    async def stop(self) -> None:
        """停止后台监视循环（幂等）"""
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        logger.info("后台设备监视已停止")

    async def _loop(self, loop_count: int | None = None) -> None:
        """监视主循环。

        loop_count 仅供测试使用：传入正整数时仅执行该轮次后返回，
        None 表示无限循环（正常生产路径）。
        """
        iterations = 0
        while loop_count is None or iterations < loop_count:
            iterations += 1
            try:
                # 未配置自动任务时跳过扫描，避免无意义地调用 ADB
                if auto_task_settings.has_auto_tasks():
                    devices = await device_manager.get_devices()
                    online = {d["serial"] for d in devices}
                    # 清理已消失设备的已触发状态，支持重连后重新触发
                    retain_devices(online)
                    for d in devices:
                        await run_auto_tasks(d["serial"])
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("扫描失败: %s", e)
            await asyncio.sleep(self.interval)


# 全局单例
auto_task_watcher = AutoTaskWatcher()
