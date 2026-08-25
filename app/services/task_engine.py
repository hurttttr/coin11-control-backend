
"""
任务执行引擎
负责 subprocess 管理、任务队列调度、日志捕获
支持异步回调集成（日志推送、状态通知）
"""

import asyncio
import logging
import os
import subprocess
import sys
import time
import traceback
import uuid
from collections import defaultdict, deque
from datetime import datetime
from typing import Callable, Optional

from app.core.config import get_settings
from app.services.device_selector_patch import create_launcher_script, cleanup_launcher

logger = logging.getLogger(__name__)

# 进程终止宽限期（秒）：terminate() 后等待进程自行退出，超时则强制 kill()
TERMINATE_GRACE_SECONDS = 3.0
# 单任务执行超时（秒），None 表示不限制
DEFAULT_TASK_TIMEOUT = 1800.0
# 任务执行超时提示（写入任务日志）
TASK_TIMEOUT_MSG = "[系统] 任务执行超时"


class Task:
    """内部任务数据模型"""

    # 日志行内存上限：远超展示用的 200 行，防止长跑任务日志无限增长
    MAX_LOG_LINES = 2000

    def __init__(self, device_id: str, script_name: str, script_path: str):
        self.id = str(uuid.uuid4())[:8]
        self.device_id = device_id
        self.script_name = script_name
        self.script_path = script_path
        self.status = "pending"  # pending / running / completed / failed
        self.position = 0
        self.created_at = datetime.now().isoformat()
        self.started_at: Optional[str] = None
        self.finished_at: Optional[str] = None
        self.log_lines: deque[str] = deque(maxlen=self.MAX_LOG_LINES)
        # 当前任务对应的子进程句柄（由 _execute_task 填充，用于终止进程树）
        self._process: Optional[subprocess.Popen] = None
        # 子进程是否以独立会话启动（POSIX 上决定能否用 killpg 整组终止）。
        # Popen 不保留 start_new_session 参数，必须由启动方自己记录。
        self._new_session: bool = False
        # 后台终止任务句柄（stop_queue 需等待它落地）
        self._termination_task: Optional[asyncio.Task] = None

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
            "log": "\n".join(list(self.log_lines)[-200:]),  # 保留最近 200 行（deque 不支持切片，先转 list）
        }


class TaskEngine:
    """任务执行引擎 — subprocess + 队列调度"""

    def __init__(self, task_timeout: Optional[float] = DEFAULT_TASK_TIMEOUT):
        self.task_timeout = task_timeout  # 单任务执行超时（秒），None 表示不限制
        self._queues: dict[str, list[Task]] = defaultdict(list)
        self._running: dict[str, Optional[asyncio.Task]] = {}
        self._current_task: dict[str, Optional[Task]] = {}

    @property
    def settings(self):
        """每次访问都取当前配置单例。

        不在 __init__ 里捕获 —— 本类是模块级单例，导入时机早于
        get_settings.cache_clear()（测试用 env 覆盖 COIN11_TB_PATH 时依赖它）。
        固化配置会导致脚本白名单仍指向旧路径。
        """
        return get_settings()

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
        return self._queues[device_id]

    async def get_queue(self, device_id: str) -> list[dict]:
        """获取设备队列状态"""
        q = self._queues.get(device_id)
        if q is None:
            logger.debug("get_queue: device_id=%r 不在 _queues 中，返回空队列", device_id)
            return []
        result = [t.to_dict() for t in q]
        logger.debug("get_queue: device_id=%r 共 %d 个任务", device_id, len(q))
        return result

    async def get_replay_logs(self, device_id: str) -> list[dict]:
        """
        导出设备队列的历史日志（用于 WS 连接建立时回放）。

        自动任务由后台 watcher 触发，可能在前端连接前就已产生日志；
        这些日志通过本方法按任务导出，配合前端按 task_id 去重注入日志通道。

        返回按队列顺序排列的 [{task_id, script_name, lines:[text,...]}, ...]，
        每任务仅导出 log_lines 的最近 200 行，与 to_dict().log 保持一致。
        """
        q = self._queues.get(device_id)
        if not q:
            return []
        result = []
        for t in q:
            if not t.log_lines:
                continue
            result.append({
                "task_id": t.id,
                "script_name": t.script_name,
                "lines": list(t.log_lines)[-200:],
            })
        return result

    # ---------- 队列执行 ----------

    def _mark_task_finished(
        self,
        task: Task,
        status: str,
        status_callback: Optional[Callable],
        device_id: str,
    ) -> None:
        """标记任务结束：写入状态/时间并通知回调（幂等，回调走事件循环调度）"""
        if task.status == "running":
            task.status = status
            task.finished_at = datetime.now().isoformat()
            if status_callback:
                try:
                    loop = asyncio.get_event_loop()
                    if not loop.is_closed():
                        loop.create_task(status_callback(device_id, task.id, status))
                except Exception:
                    pass

    async def _terminate_process_tree(
        self, process: subprocess.Popen, new_session: bool = False
    ) -> None:
        """
        终止子进程及其整个进程树（幂等、不抛异常）。

        launcher 会再派生 python 子进程，所以必须按进程树终止，
        单纯 terminate() 只会杀掉直接子进程，留下孙进程继续操作设备。

        - Windows：taskkill /F /T 终止整个进程树
        - POSIX：以独立会话启动时用 os.killpg 终止整个进程组，
          否则只能退化为 kill() 直接子进程
        - new_session 必须由启动方传入：Popen 不保留 start_new_session 参数
        """
        if process is None:
            return
        if process.poll() is not None:
            return  # 进程已退出

        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                    capture_output=True,
                    timeout=TERMINATE_GRACE_SECONDS + 2,
                )
                # taskkill /F 直接强杀，等待句柄确认退出
                deadline = time.monotonic() + TERMINATE_GRACE_SECONDS
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        break
                    time.sleep(0.1)
                if process.poll() is None:
                    process.kill()
            else:
                import signal

                if not new_session:
                    # 未独立成组，无法整组终止，只能杀直接子进程
                    logger.warning(
                        "子进程 pid=%s 未以独立会话启动，无法终止整个进程树", process.pid
                    )
                    process.kill()
                    return
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                except ProcessLookupError:
                    pass
                deadline = time.monotonic() + TERMINATE_GRACE_SECONDS
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        break
                    time.sleep(0.1)
                if process.poll() is None:
                    try:
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                    except ProcessLookupError:
                        pass
        except Exception:
            try:
                if process.poll() is None:
                    process.kill()
            except Exception:
                pass
        # 不在此处关闭 stdout/stderr：读线程可能正阻塞在 readline() 上，
        # 从另一个线程关闭它是未定义行为。进程死亡会让管道产生 EOF，
        # readline() 自然返回空串退出（原实现调用的 stream.destroy()
        # 在 TextIOWrapper 上并不存在，异常被吞掉后等于没做任何事）。

    def _background_terminate(self, task: Task) -> None:
        """
        在独立任务中终止子进程树（不在当前任务内 await）。

        取消路径上（except asyncio.CancelledError 内）再次 await 会被
        asyncio 立刻重新抛出 CancelledError，导致终止逻辑永远执行不到；
        改为派发到独立任务执行，本方法立即返回。
        """
        if task._termination_task is not None and not task._termination_task.done():
            return  # 已在终止中，避免重复
        process = task._process
        if process is None or process.poll() is not None:
            return
        task._termination_task = asyncio.create_task(
            self._terminate_process_tree(process, new_session=task._new_session)
        )

    async def _execute_task(
        self,
        device_id: str,
        task: Task,
        log_callback: Optional[Callable] = None,
        timeout: Optional[float] = None,
    ) -> str:
        """
        执行单个任务脚本（含超时控制）。

        返回 "ok" 表示正常结束（含非零退出码），"timeout" 表示超时；
        被外部取消（stop_queue）时抛出 asyncio.CancelledError。
        - timeout: 本任务执行超时（秒），None 表示不限制。
          由 runner 在启动每个任务前显式快照传入，避免任务间串读共享状态。
        """
        launcher_path: Optional[str] = None
        daemon: Optional[asyncio.Task] = None
        # POSIX 上以独立会话启动，才能用 killpg 终止 launcher 派生的整棵进程树
        _new_session = sys.platform != "win32"

        def _run_script():
            """创建 launcher 临时脚本并启动子进程（在子线程中执行）"""
            nonlocal launcher_path
            logger.info(
                "启动脚本: 解释器=%r, coin11_tb=%r, 脚本=%r, 设备=%r",
                sys.executable,
                self.settings.coin11_tb_path_resolved,
                task.script_path,
                device_id,
            )
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
                env={
                    **os.environ,
                    "PYTHONUNBUFFERED": "1",
                    "COIN11_TB_DEVICE_SERIAL": device_id,
                },
                text=True,
                bufsize=1,  # 行缓冲
                start_new_session=_new_session,  # POSIX 上便于整组终止
            )
            task._new_session = _new_session
            return process

        async def read_stream(stream, prefix: str = "") -> None:
            """逐行读取子进程输出并写入任务日志（deque 自动裁剪上限）"""
            loop = asyncio.get_running_loop()
            while True:
                line = await loop.run_in_executor(None, stream.readline)
                if not line:
                    break
                text = line.rstrip("\r\n")
                if text:
                    task.log_lines.append(text)
                    if log_callback:
                        try:
                            await log_callback(device_id, task.id, text)
                        except Exception:
                            pass

        async def daemon_coro():
            """守护协程：维持子进程存活并收集输出，直到进程退出"""
            process = await asyncio.to_thread(_run_script)
            task._process = process

            if process.stdout is None or process.stderr is None:
                raise RuntimeError(
                    f"子进程 stdout={process.stdout is not None}, "
                    f"stderr={process.stderr is not None} — PIPE 创建失败"
                )

            await asyncio.gather(
                read_stream(process.stdout),
                read_stream(process.stderr),
            )
            returncode = await asyncio.to_thread(process.wait)
            if returncode == 0:
                task.status = "completed"
            else:
                task.status = "failed"
                task.log_lines.append(f"[系统] 进程退出码: {returncode}")

        try:
            try:
                daemon = asyncio.create_task(daemon_coro())
                if timeout is not None:
                    await asyncio.wait_for(
                        asyncio.shield(daemon), timeout=timeout
                    )
                else:
                    await asyncio.shield(daemon)
                return "ok"
            except asyncio.TimeoutError:
                logger.warning(
                    "任务执行超时: device=%r task=%s 超时=%ss",
                    device_id, task.id, timeout,
                )
                # 取消 wait_for 内部残留的屏蔽引用，清除取消计数后正常终止进程树
                try:
                    if daemon is not None:
                        daemon.cancel()
                        await asyncio.shield(daemon)
                except asyncio.CancelledError:
                    asyncio.current_task().uncancel()
                self._background_terminate(task)
                return "timeout"
            except asyncio.CancelledError:
                # 停止队列：派发后台终止进程树（守护任务被屏蔽，
                # 不会中断收集；进程死后管道 EOF，读线程自然退出），
                # 随后重新抛出，runner 立即退出，不会启动下一个任务
                self._background_terminate(task)
                raise
        finally:
            if launcher_path:
                cleanup_launcher(launcher_path)

    async def start_queue(
        self,
        device_id: str,
        log_callback: Optional[Callable] = None,
        status_callback: Optional[Callable] = None,
    ) -> bool:
        """
        按 FIFO 顺序执行队列中的任务
        异步后台执行，不阻塞调用方
        - log_callback: 日志行回调 async (device_id, task_id, text)
        - status_callback: 状态变更回调 async (device_id, task_id, status)
        """
        if self._running.get(device_id) is not None:
            return False  # 已在运行

        async def runner():
            """后台执行器 — 遍历队列快照，逐个执行任务"""
            # 复制队列（取快照），防止并发修改导致迭代异常
            queue = list(self._queues.get(device_id, []))
            logger.info("队列执行开始: device=%r, 任务数=%d", device_id, len(queue))
            try:
                for task in queue:
                    if task.status not in ("pending", "failed"):
                        logger.debug("跳过任务 %s status=%s", task.id, task.status)
                        continue

                    # 标记运行中
                    task.status = "running"
                    task.started_at = datetime.now().isoformat()
                    self._current_task[device_id] = task
                    if status_callback:
                        try:
                            await status_callback(device_id, task.id, "running")
                        except Exception:
                            pass

                    # 检查脚本是否存在
                    if not os.path.isfile(task.script_path):
                        task.log_lines.append(f"[错误] 脚本文件不存在: {task.script_path}")
                        self._mark_task_finished(task, "failed", status_callback, device_id)
                        continue

                    try:
                        # 在每个任务启动前快照超时配置，避免任务间串读共享状态
                        result = await self._execute_task(
                            device_id,
                            task,
                            log_callback=log_callback,
                            timeout=self.task_timeout,
                        )
                    except asyncio.CancelledError:
                        # 停止队列关键点：清理后重新抛出，runner 立即退出，
                        # 绝不会继续启动队列中的下一个任务
                        if task.status == "running":
                            task.log_lines.append("[系统] 任务被手动取消")
                            self._mark_task_finished(task, "failed", status_callback, device_id)
                        raise
                    except Exception:
                        logger.exception("任务执行发生异常: device=%r task=%s", device_id, task.id)
                        task.log_lines.append(f"[错误] {traceback.format_exc()}")
                        self._mark_task_finished(task, "failed", status_callback, device_id)
                        continue

                    if result == "timeout":
                        # 超时不等同于用户取消：标记失败后继续执行队列中的下一个任务
                        task.log_lines.append(TASK_TIMEOUT_MSG)
                        self._mark_task_finished(task, "failed", status_callback, device_id)
                        continue

                    self._mark_task_finished(task, task.status, status_callback, device_id)
            finally:
                # 无论正常结束、异常还是被取消，都必须清理运行状态
                self._current_task[device_id] = None
                self._running[device_id] = None

        self._running[device_id] = asyncio.create_task(runner())
        return True

    async def stop_queue(self, device_id: str) -> bool:
        """停止队列执行：取消 runner 并终止当前子进程树，等待清理完成"""
        running_task = self._running.get(device_id)
        if running_task is not None:
            running_task.cancel()
            try:
                await asyncio.gather(running_task, return_exceptions=True)
            except Exception:
                pass
            self._running[device_id] = None

        # 标记当前运行中的任务为 failed
        current = self._current_task.get(device_id)
        if current and current.status == "running":
            current.status = "failed"
            current.finished_at = datetime.now().isoformat()
            current.log_lines.append("[系统] 任务已手动停止")

        # 等待后台终止任务完成，确保子进程树确实被杀死（幂等）
        term_task = current._termination_task if current else None
        if term_task is not None:
            try:
                await asyncio.gather(term_task, return_exceptions=True)
            except Exception:
                pass

        return True


# 全局单例
task_engine = TaskEngine()
