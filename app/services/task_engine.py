"""
任务执行引擎
负责 subprocess 管理、任务队列调度、日志捕获
支持异步回调集成（日志推送、状态通知）

设计要点（P0 改造）：
- P0-1 停止队列必须终止子进程：runner 为每个设备登记当前 subprocess.Popen，
  stop_queue 先 kill 子进程再取消 runner。
- P0-2 运行中入队的新任务自动执行：runner 用 while 循环 + asyncio.Condition
  持续消费队列，enqueue 后 notify。
- P0-3 日志内存上限：Task.log_lines 为 collections.deque(maxlen=200)。
"""

import asyncio
import logging
import os
import subprocess
import sys
import uuid
from collections import defaultdict, deque
from datetime import datetime
from typing import Callable, Optional

from app.core.config import get_settings
from app.services.device_selector_patch import create_launcher_script, cleanup_launcher

logger = logging.getLogger(__name__)


class Task:
    """内部任务数据模型"""

    def __init__(
        self,
        device_id: str,
        script_name: str,
        script_path: str,
        task_id: Optional[str] = None,
    ):
        self.id = task_id if task_id else str(uuid.uuid4())[:8]
        self.device_id = device_id
        self.script_name = script_name
        self.script_path = script_path
        self.status = "pending"  # pending / running / completed / failed
        self.position = 0
        self.created_at = datetime.now().isoformat()
        self.started_at: Optional[str] = None
        self.finished_at: Optional[str] = None
        # P0-3: 日志内存上限 200 行
        self.log_lines: deque[str] = deque(maxlen=200)

    def to_dict(self) -> dict:
        """转换为可序列化的字典（与 TaskInfo schema 兼容）"""
        return {
            "id": self.id,
            "device_id": self.device_id,
            "script_name": self.script_name,
            "script_path": self.script_path,
            "status": self.status,
            "position": self.position,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "log": "\n".join(self.log_lines),
        }


class TaskEngine:
    """任务执行引擎 — subprocess + 队列调度"""

    def __init__(self):
        self.settings = get_settings()
        self._queues: dict[str, list[Task]] = defaultdict(list)
        self._running: dict[str, Optional[asyncio.Task]] = {}
        self._current_task: dict[str, Optional[Task]] = {}

        # P0-1: 每个设备当前运行的 subprocess（未运行时为 None）
        self._processes: dict[str, Optional[subprocess.Popen]] = {}
        # P0-2: 每个设备的队列消费条件
        self._conditions: dict[str, asyncio.Condition] = {}

    # ---------- 脚本扫描 ----------

    EXCLUDED_SCRIPTS = {"utils.py", "chromedriver.py", "识别图片测试.py"}

    async def get_available_scripts(self) -> list[dict]:
        """
        扫描原项目目录下的 .py 脚本文件
        排除 utils.py, chromedriver.py, 识别图片测试.py
        """
        path = self.settings.coin11_tb_path_resolved
        scripts = []
        if os.path.isdir(path):
            for f in sorted(os.listdir(path)):
                if f.endswith(".py") and f not in self.EXCLUDED_SCRIPTS:
                    name = f.replace(".py", "")
                    scripts.append({
                        "name": f,
                        "path": f,
                        "description": f"自动化脚本: {name}",
                    })
        return scripts

    # ---------- 队列管理 ----------

    async def enqueue(self, device_id: str, script_name: str) -> Task:
        """添加任务到设备队列（含白名单校验，防路径穿越）"""
        # 1. 安全校验：script_name 必须在白名单中
        allowed_scripts = await self.get_available_scripts()
        allowed_names = {s["name"] for s in allowed_scripts}
        if script_name not in allowed_names:
            raise ValueError(f"脚本 '{script_name}' 不在可用列表中")

        # 2. 使用 basename 防止路径穿越
        safe_name = os.path.basename(script_name)
        script_path = os.path.join(self.settings.coin11_tb_path_resolved, safe_name)
        task = Task(device_id, safe_name, script_path)
        task.position = len(self._queues[device_id])
        self._queues[device_id].append(task)

        # P0-2: 唤醒等待中的 runner（如有）取走新任务
        cond = self._conditions.get(device_id)
        if cond is not None:
            async with cond:
                cond.notify_all()

        await self._save_state()
        return task

    async def dequeue(self, device_id: str, task_id: str) -> bool:
        """从队列中移除指定任务（禁止删除正在运行的任务）"""
        current = self._current_task.get(device_id)
        if current and current.id == task_id and current.status == "running":
            raise ValueError("不能删除正在运行的任务，请先停止队列")

        original_len = len(self._queues[device_id])
        self._queues[device_id] = [
            t for t in self._queues[device_id] if t.id != task_id
        ]
        # 重新计算 position
        for i, t in enumerate(self._queues[device_id]):
            t.position = i

        await self._save_state()
        return len(self._queues[device_id]) < original_len

    async def reorder(self, device_id: str, order: list[str]) -> list[Task]:
        """按 task_id 列表重排队列"""
        task_map = {t.id: t for t in self._queues[device_id]}
        reordered = []
        for tid in order:
            if tid in task_map:
                reordered.append(task_map[tid])
        # 补回不在 order 中的任务
        for t in self._queues[device_id]:
            if t.id not in order:
                reordered.append(t)
        self._queues[device_id] = reordered
        for i, t in enumerate(self._queues[device_id]):
            t.position = i

        await self._save_state()
        return self._queues[device_id]

    async def get_queue(self, device_id: str) -> list[dict]:
        """获取设备队列状态"""
        q = self._queues.get(device_id)
        if q is None:
            logger.debug(f"[DEBUG get_queue] device_id={device_id!r} -> key not in _queues (defaultdict would create empty)")
            return []
        result = [t.to_dict() for t in q]
        logger.debug(f"[DEBUG get_queue] device_id={device_id!r} -> {len(q)} tasks, returning {len(result)} items")
        return result

    # ---------- 快照 / 恢复（契约） ----------

    def snapshot(self) -> dict[str, list[dict]]:
        """所有设备队列的序列化（每任务 to_dict()）"""
        result = {}
        for device_id, q in self._queues.items():
            result[device_id] = [t.to_dict() for t in q]
        return result

    async def restore(self, snapshot: dict[str, list[dict]]) -> None:
        """
        恢复队列。
        - status=="running" 的任务改为 "failed" 并追加日志行 "[系统] 服务重启，任务中断"
        - 保留原 task id
        - 恢复 position/created_at/started_at/finished_at/log_lines
        - 不自动启动；不校验白名单（脚本可能已删除）
        """
        self._queues.clear()
        for device_id, tasks in snapshot.items():
            queue = []
            for td in tasks:
                t = Task(
                    td.get("device_id", device_id),
                    td.get("script_name", ""),
                    td.get("script_path", ""),
                    task_id=td.get("id"),
                )
                t.status = td.get("status", "pending")
                t.position = td.get("position", 0)
                t.created_at = td.get("created_at", datetime.now().isoformat())
                t.started_at = td.get("started_at")
                t.finished_at = td.get("finished_at")
                log = td.get("log") or ""
                for line in log.split("\n"):
                    if line:
                        t.log_lines.append(line)
                if t.status == "running":
                    t.status = "failed"
                    t.log_lines.append("[系统] 服务重启，任务中断")
                queue.append(t)
            self._queues[device_id] = queue
        # 不自动启动

    # ---------- 队列执行 ----------

    def _condition(self, device_id: str) -> asyncio.Condition:
        """获取（惰性创建）设备的队列消费条件。"""
        cond = self._conditions.get(device_id)
        if cond is None:
            cond = asyncio.Condition()
            self._conditions[device_id] = cond
        return cond

    def _queue_has_pending(self, device_id: str) -> bool:
        """队列中是否存在可执行的任务（pending）"""
        q = self._queues.get(device_id)
        if not q:
            return False
        return any(t.status == "pending" for t in q)

    def _pick_next_pending(self, device_id: str) -> Optional[Task]:
        """按队列顺序选取第一个可执行任务（reorder 影响其顺序）。"""
        q = self._queues.get(device_id)
        if not q:
            return None
        for t in q:
            if t.status == "pending":
                return t
        return None

    async def start_queue(
        self,
        device_id: str,
        log_callback: Optional[Callable] = None,
        status_callback: Optional[Callable] = None,
    ) -> bool:
        """
        按 FIFO 顺序持续执行队列中的任务（P0-2：运行中入队的新任务会自动执行）。
        异步后台执行，不阻塞调用方
        - log_callback: 日志行回调 async (device_id, task_id, text)
        - status_callback: 状态变更回调 async (device_id, task_id, status)

        start_queue 语义：已在运行时返回 True（幂等成功，避免前端重复点击
        "开始"报错——自动任务可能已由 watcher 启动）；停止（stop_queue）后
        可重新启动。runner 退出条件：被取消（stop_queue / stop_all）。
        """
        if self._running.get(device_id) is not None:
            return True  # 已在运行：幂等成功

        async def runner():
            """后台执行器 — while 循环 + Condition 持续消费队列"""
            try:
                while True:
                    # 等待可执行任务
                    cond = self._condition(device_id)
                    async with cond:
                        while not self._queue_has_pending(device_id):
                            await cond.wait()

                    task = self._pick_next_pending(device_id)
                    if task is None:
                        continue

                    # 标记运行中（pick 与标记间无 await，事件循环内原子）
                    task.status = "running"
                    task.started_at = datetime.now().isoformat()
                    self._current_task[device_id] = task

                    await self._run_task(
                        device_id, task, log_callback, status_callback
                    )
                    # 任务结束后清空当前任务，继续循环消费后续/新入队任务
                    self._current_task[device_id] = None
            except asyncio.CancelledError:
                pass  # 停止/关闭时预期取消 runner 循环，正常退出
            finally:
                # 兜底清理：残留进程 / 当前任务 / 运行标志 / 条件
                proc = self._processes.pop(device_id, None)
                if proc is not None:
                    try:
                        proc.kill()
                    except OSError:
                        # 子进程可能在 runner 取消前已退出，kill 报 OSError 属正常清理路径
                        pass
                self._current_task[device_id] = None
                self._running[device_id] = None
                self._conditions.pop(device_id, None)

        self._conditions.pop(device_id, None)
        self._condition(device_id)  # 预创建，保证 runner 直接可用
        self._running[device_id] = asyncio.create_task(runner())
        return True

    async def _run_task(
        self,
        device_id: str,
        task: Task,
        log_callback: Optional[Callable] = None,
        status_callback: Optional[Callable] = None,
    ) -> None:
        """执行单个任务：登记子进程、捕获输出、更新状态。"""
        ok = False
        try:
            if status_callback:
                try:
                    await status_callback(device_id, task.id, "running")
                except Exception:
                    logger.debug("状态回调失败 (running)", exc_info=True)

            # 检查脚本是否存在
            if not os.path.isfile(task.script_path):
                logger.debug(f"[DEBUG runner] 任务 {task.id} 脚本不存在: {task.script_path}")
                task.log_lines.append(f"[错误] 脚本文件不存在: {task.script_path}")
                task.status = "failed"
                task.finished_at = datetime.now().isoformat()
                if status_callback:
                    try:
                        await status_callback(device_id, task.id, "failed")
                    except Exception:
                        logger.debug("状态回调失败 (failed)", exc_info=True)
                ok = True
                return

            # 执行脚本
            launcher_path = None
            try:
                def _run_script():
                    """在子线程中运行脚本并逐行捕获输出"""
                    nonlocal launcher_path
                    # ---- debug: 记录子进程启动参数 ----
                    debug_info = (
                        f"[debug] sys.executable={sys.executable!r}\n"
                        f"[debug] coin11_tb_dir={self.settings.coin11_tb_path_resolved!r}\n"
                        f"[debug] target_script={task.script_path!r}\n"
                        f"[debug] device_serial={device_id!r}"
                    )
                    task.log_lines.append(debug_info)
                    # ---------------------------------
                    launcher_path = create_launcher_script(
                        coin11_tb_dir=self.settings.coin11_tb_path_resolved,
                        target_script=task.script_path,
                    )
                    process = subprocess.Popen(
                        [sys.executable, launcher_path],
                        cwd=self.settings.coin11_tb_path_resolved,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        stdin=subprocess.DEVNULL,
                        # 强制子进程与父进程都用 UTF-8，规避 Windows 下 GBK/UTF-8 混编导致
                        # readline 抛 UnicodeDecodeError 的问题
                        env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1", "COIN11_TB_DEVICE_SERIAL": device_id},
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        bufsize=1,  # 行缓冲
                    )
                    return process

                process = await asyncio.to_thread(_run_script)

                # P0-1: 登记当前子进程（stop_queue 据此 kill）
                self._processes[device_id] = process

                # 确保 process.stdout/err 已正确创建
                if process.stdout is None or process.stderr is None:
                    raise RuntimeError(
                        f"子进程 stdout={process.stdout is not None}, stderr={process.stderr is not None} — PIPE 创建失败"
                    )

                async def read_stream(
                    stream,  # IO[str] from subprocess.PIPE
                    prefix: str = "",
                ) -> None:
                    loop = asyncio.get_running_loop()
                    while True:
                        line = await loop.run_in_executor(
                            None, stream.readline
                        )
                        if not line:
                            break
                        text = line.rstrip("\r\n")
                        if text:
                            task.log_lines.append(text)
                            if log_callback:
                                try:
                                    await log_callback(device_id, task.id, text)
                                except Exception:
                                    logger.debug("日志回调失败", exc_info=True)

                await asyncio.gather(
                    read_stream(process.stdout),
                    read_stream(process.stderr),
                )
                returncode = await asyncio.to_thread(process.wait)
                task.status = "completed" if returncode == 0 else "failed"

                if returncode != 0:
                    task.log_lines.append(
                        f"[系统] 进程退出码: {returncode}"
                    )

            except asyncio.CancelledError:
                # stop_queue 先杀子进程再取消；这里只标记失败并向上传播，
                # 具体日志由 stop_queue 追加 "[系统] 任务已手动停止"
                task.status = "failed"
                raise
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                task.log_lines.append(f"[错误] {e}")
                task.log_lines.append(f"[错误] 详情: {tb}")
                task.status = "failed"
            finally:
                if launcher_path:
                    cleanup_launcher(launcher_path)
                # 任务结束后清除子进程登记
                self._processes[device_id] = None

            task.finished_at = datetime.now().isoformat()
            if status_callback:
                try:
                    await status_callback(device_id, task.id, task.status)
                except Exception:
                    logger.debug("状态回调失败 (终态)", exc_info=True)
            ok = True
        finally:
            if ok:
                # 每个任务进入 completed/failed 终态后持久化
                await self._save_state()

    async def stop_queue(self, device_id: str) -> bool:
        """停止队列执行。

        P0-1: 先 kill 子进程（Windows 即 TerminateProcess），再等待/取消 runner；
        runner 结束后当前任务标记 failed 并追加日志行 "[系统] 任务已手动停止"；
        结束后 _running[device] 必须为 None，之后可再次 start_queue 重启。
        覆盖两条路径：
        - 任务在 _run_task 内被取消（其 CancelledError 分支只置 status 不写 finished_at）
        - 任务已标记 running 但尚未进入 _run_task 就被取消
        因 runner 的 finally 会清空 _current_task[device]，必须在 cancel/await 之前
        捕获 current 引用；await 之后再按 finished_at 是否为空决定是否标记"手动停止"，
        避免与正常的 completed/failed 终态（runner 已写 finished_at）重复覆盖。
        """
        # 1. 先终止当前子进程，避免脚本继续在手机上执行
        process = self._processes.get(device_id)
        if process is not None:
            try:
                process.kill()
            except OSError:
                # 子进程可能已自然退出，kill 报 OSError 属预期，忽略即可
                pass

        # 2. 在 runner 清理前捕获当前任务引用
        current = self._current_task.get(device_id)

        # 3. 取消并等待 runner
        running_task = self._running.get(device_id)
        if running_task is not None:
            running_task.cancel()
            try:
                await running_task
            except (asyncio.CancelledError, Exception):
                # 取消/等待 runner 期间的预期中断或异常，均在此容错
                pass
            self._running[device_id] = None

        # 4. 若任务未进入任何终态（finished_at 仍为空），标记为手动停止
        if current is not None and current.finished_at is None:
            current.status = "failed"
            current.finished_at = datetime.now().isoformat()
            current.log_lines.append("[系统] 任务已手动停止")
        self._current_task[device_id] = None

        await self._save_state()
        return True

    async def stop_all(self) -> None:
        """终止所有设备的 runner + 子进程，清理队列执行状态（供关闭时调用）。"""
        for device_id in list(self._running.keys()):
            try:
                await self.stop_queue(device_id)
            except Exception:
                # 逐个停止设备，单个失败不影响其余设备停止
                logger.debug("stop_all: stop_queue 失败", exc_info=True)

        # 兜底清理任何残留进程
        for device_id in list(self._processes.keys()):
            process = self._processes.pop(device_id, None)
            if process is not None:
                try:
                    process.kill()
                except OSError:
                    # 子进程可能已退出，kill 报 OSError 属预期，忽略即可
                    pass
        self._running.clear()
        self._current_task.clear()
        self._processes.clear()
        self._conditions.clear()

    # ---------- 高级启动（契约） ----------

    async def start_queue_full(self, device_id: str) -> bool:
        """
        集成的队列启动：
        若未在跑截图流则 start_stream(fps=2.0, callback 推送 screenshot) +
        构造 log/status 回调 + start_queue。返回 start_queue 的 bool。
        """
        from app.services.screen_capture import screen_capture
        from app.services.websocket_manager import ws_manager

        # 确保截图流正在运行（防止被 stop_queue 或意外中断后未恢复）
        if device_id not in screen_capture.active_streams:
            await screen_capture.start_stream(
                device_id,
                callback=lambda img: ws_manager.send_screenshot(device_id, img),
                fps=2.0,
            )

        # 构建回调闭包
        async def log_callback(did: str, tid: str, text: str):
            await ws_manager.send_log(did, text, task_id=tid)

        async def status_callback(did: str, tid: str, status: str):
            await ws_manager.send_status(did, tid, status)

        return await self.start_queue(
            device_id,
            log_callback=log_callback,
            status_callback=status_callback,
        )

    # ---------- 状态持久化钩子（契约） ----------

    async def _save_state(self) -> None:
        """触发 state_store.on_mutation 保存（方法内懒导入，避免循环依赖）。

        state_store 可能尚未注册（_engine is None），必须静默。
        """
        try:
            from app.services import state_store

            await state_store.on_mutation()
        except Exception:
            # state_store 可能尚未注册（_engine is None），按契约静默；此处本应不触发
            logger.debug("state_store.on_mutation 失败", exc_info=True)


# 全局单例
task_engine = TaskEngine()
