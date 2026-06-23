"""
任务执行引擎
负责 subprocess 管理、任务队列调度、日志捕获
支持异步回调集成（日志推送、状态通知）
"""

import asyncio
import os
import subprocess
import sys
import uuid
from collections import defaultdict
from datetime import datetime
from typing import AsyncIterator, Callable, Coroutine, Optional

from app.core.config import get_settings


class Task:
    """内部任务数据模型"""

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
        self.log_lines: list[str] = []

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
            "log": "\n".join(self.log_lines[-200:]),  # 保留最近 200 行
        }


class TaskEngine:
    """任务执行引擎 — subprocess + 队列调度"""

    def __init__(self):
        self.settings = get_settings()
        self._queues: dict[str, list[Task]] = defaultdict(list)
        self._running: dict[str, Optional[asyncio.Task]] = {}
        self._current_task: dict[str, Optional[Task]] = {}

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
        return [t.to_dict() for t in self._queues[device_id]]

    # ---------- 队列执行 ----------

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
            # 复制队列（取快照），防止并发修改导致迭代异常
            queue = list(self._queues[device_id])
            for task in queue:
                if task.status not in ("pending", "failed"):
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
                    task.status = "failed"
                    task.finished_at = datetime.now().isoformat()
                    if status_callback:
                        try:
                            await status_callback(device_id, task.id, "failed")
                        except Exception:
                            pass
                    continue

                # 执行脚本
                try:
                    def _run_script():
                        """在子线程中运行脚本并逐行捕获输出"""
                        process = subprocess.Popen(
                            [sys.executable, task.script_path],
                            cwd=self.settings.coin11_tb_path_resolved,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            env={**os.environ, "PYTHONUNBUFFERED": "1", "COIN11_TB_DEVICE_SERIAL": device_id},
                            text=True,
                            bufsize=1,  # 行缓冲
                        )
                        return process

                    process = await asyncio.to_thread(_run_script)

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
                                        pass

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
                    task.log_lines.append("[系统] 任务被手动取消")
                    task.status = "failed"
                except Exception as e:
                    task.log_lines.append(f"[错误] {e}")
                    task.status = "failed"

                task.finished_at = datetime.now().isoformat()
                if status_callback:
                    try:
                        await status_callback(device_id, task.id, task.status)
                    except Exception:
                        pass

            # 所有任务执行完毕
            self._current_task[device_id] = None
            self._running[device_id] = None

        self._running[device_id] = asyncio.create_task(runner())
        return True

    async def stop_queue(self, device_id: str) -> bool:
        """停止队列执行"""
        running_task = self._running.get(device_id)
        if running_task is not None:
            running_task.cancel()
            self._running[device_id] = None

        # 标记当前运行中的任务为 failed
        current = self._current_task.get(device_id)
        if current and current.status == "running":
            current.status = "failed"
            current.finished_at = datetime.now().isoformat()
            current.log_lines.append("[系统] 任务已手动停止")

        return True


# 全局单例
task_engine = TaskEngine()
