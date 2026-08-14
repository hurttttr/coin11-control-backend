"""
自动任务触发服务 — 设备上线自动入队并启动已配置的任务

触发来源:
1. 后台监视循环 (AutoTaskWatcher): 周期扫描 ADB 设备，新设备自动触发
   —— 不依赖前端轮询/网页打开，后端启动后即自动工作
2. HTTP 端点 (GET /api/devices 轮询、POST /api/devices/connect): 保持即时触发

两处共用 run_auto_tasks() 和 _auto_task_triggered 去重集合，避免重复触发。
watcher 扫描基于 device_manager 的设备缓存（refresh() 驱动），并维护本实例的
已见集合 _seen；设备断开后通过 forget_device() 从去重集合中剔除，使断开重连后
能重新触发自动任务（P1-7）。
"""

import asyncio

from app.services.auto_task_settings import auto_task_settings
from app.services.device_manager import device_manager
from app.services.task_engine import task_engine

# 记录已触发自动任务的设备，避免重复触发（进程生命周期内有效）
_auto_task_triggered: set[str] = set()


def is_triggered(device_id: str) -> bool:
    """该设备是否已触发过自动任务"""
    return device_id in _auto_task_triggered


def forget_device(device_id: str) -> None:
    """设备断开后从已触发集合中移除，使断开重连后可重新触发自动任务"""
    _auto_task_triggered.discard(device_id)


async def run_auto_tasks(device_id: str):
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
    # 截图流 + log/status 回调已由 task_engine.start_queue_full 内部构造，此处不再
    # 直接使用 screen_capture / ws_manager。
    try:
        ok = await task_engine.start_queue_full(device_id)
        if ok:
            print(f"[AutoTask] ✅ 队列已启动 → {device_id}")
        else:
            print(f"[AutoTask] ⚠️ 队列已在运行中 → {device_id}")
    except Exception as e:
        print(f"[AutoTask] ❌ 启动队列失败 → {device_id}: {e}")


class AutoTaskWatcher:
    """后台设备监视循环

    周期刷新 ADB 设备缓存并对每个新出现的设备触发 run_auto_tasks。
    后端启动后即自动运行，不依赖前端打开网页 / 轮询 HTTP 接口。
    """

    def __init__(self, interval: float = 5.0):
        self.interval = interval
        self._task: asyncio.Task | None = None
        # 本实例已见过的设备集合，用于区分新增/消失设备
        self._seen: set[str] = set()

    async def start(self) -> None:
        """启动后台监视循环（幂等）"""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(), name="auto-task-watcher")
        print(f"[AutoTaskWatcher] 后台设备监视已启动 (interval={self.interval}s)")

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
        print("[AutoTaskWatcher] 后台设备监视已停止")

    async def _scan_once(self) -> None:
        """
        执行一次设备扫描 tick：刷新缓存、触发新设备、剔除消失设备。
        独立成方法便于单元测试直接驱动单次迭代。
        """
        # 未配置自动任务时跳过扫描，避免无意义地调用 ADB
        if not auto_task_settings.has_auto_tasks():
            return
        await device_manager.refresh()
        devices = await device_manager.get_devices()  # 缓存快照
        current = {d["serial"] for d in devices}

        # 触发新出现的设备
        for d in devices:
            serial = d["serial"]
            if serial not in self._seen:
                await run_auto_tasks(serial)
                self._seen.add(serial)

        # 消失的设备从已见集合与去重集合中剔除，断开重连后可重新触发
        for gone in list(self._seen - current):
            self._seen.discard(gone)
            forget_device(gone)

    async def _loop(self) -> None:
        while True:
            try:
                await self._scan_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"[AutoTaskWatcher] 扫描失败: {e}")
            await asyncio.sleep(self.interval)


# 全局单例
auto_task_watcher = AutoTaskWatcher()
