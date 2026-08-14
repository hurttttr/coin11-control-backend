"""
任务状态持久化。

模块级无副作用：被 import 时不注册、不读文件。仅在 init() 注册引擎、
显式调用对应函数时才会执行实际 IO/子进程操作。

实现约定（来自并行 agent 的 task_engine 契约）：
- task_engine.snapshot() -> dict
- task_engine.restore(snapshot: dict) -> None（内部将 running->failed）
- task_engine.stop_all() -> None
"""
import asyncio
import json
import logging
import os
import subprocess
import sys
from datetime import datetime

logger = logging.getLogger(__name__)

# 状态文件路径，可用环境变量覆盖（测试使用）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATE_FILE = os.environ.get("COIN11_STATE_FILE", os.path.join(_PROJECT_ROOT, "task_state.json"))

_VERSION = 1

# 已注册的任务引擎（duck-typing：提供 snapshot()/restore()）
_engine = None


def init(engine):
    """注册引擎（应用启动时调用）。"""
    global _engine
    _engine = engine


def _format_snapshot(snapshot: dict) -> dict:
    """构造持久化文档。若 snapshot 为空返回 None。"""
    if not snapshot:
        return None
    return {
        "version": _VERSION,
        "saved_at": datetime.now().isoformat(),
        "queues": snapshot,
    }


def _atomic_write(data: dict) -> None:
    """原子写 JSON：先写 .tmp 再 os.replace。"""
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


async def on_mutation():
    """任务状态变更时调用（由 task_engine 内部触发），静默保存。

    未注册引擎时直接返回。
    """
    if _engine is None:
        return
    try:
        snapshot = _engine.snapshot()
        payload = _format_snapshot(snapshot)
        if payload is None:
            return
        await asyncio.to_thread(_atomic_write, payload)
    except Exception:
        logger.exception("state_store.on_mutation 保存失败")


async def save_now():
    """强制保存（关闭时调用）。未注册引擎时直接返回。"""
    if _engine is None:
        return
    try:
        snapshot = _engine.snapshot()
        payload = _format_snapshot(snapshot)
        if payload is None:
            return
        await asyncio.to_thread(_atomic_write, payload)
    except Exception:
        logger.exception("state_store.save_now 保存失败")


async def restore_state() -> bool:
    """从状态文件恢复。文件不存在返回 False；失败打印并返回 False。"""
    if not os.path.exists(STATE_FILE):
        return False
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if _engine is not None:
            _engine.restore(data.get("queues") or {})
        return True
    except Exception:
        logger.exception("state_store.restore_state 恢复失败")
        return False


async def cleanup_orphans():
    """清理上次崩溃遗留的 launcher 子进程（命令行含 _coin11_launcher.py）。

    best-effort，任何异常静默，不能拖慢启动。使用 asyncio.to_thread 避免阻塞事件循环。
    """
    try:
        if sys.platform == "win32":
            cmd = (
                "Get-CimInstance Win32_Process | "
                "Where-Object { $_.CommandLine -like '*_coin11_launcher.py*' } | "
                "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
            )
            await asyncio.to_thread(
                subprocess.run,
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                capture_output=True,
                timeout=5,
            )
        else:
            await asyncio.to_thread(
                subprocess.run,
                ["pkill", "-f", "_coin11_launcher.py"],
                capture_output=True,
                timeout=5,
            )
    except Exception:
        logger.exception("state_store.cleanup_orphans 失败")
