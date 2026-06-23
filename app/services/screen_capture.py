"""
设备屏幕截图服务
支持单帧截图和持续截图流推送（通过 WebSocket）
使用 asyncio.to_thread + subprocess.run 避免事件循环阻塞
（Windows Python 3.14+ 兼容方案）
"""

import asyncio
import subprocess
from typing import Callable

from app.core.config import get_settings


class ScreenCapture:
    """设备屏幕截图服务"""

    def __init__(self):
        self.settings = get_settings()
        self._stream_tasks: dict[str, asyncio.Task] = {}
        self._active_serials: set[str] = set()

    @property
    def active_streams(self) -> set[str]:
        """返回当前活跃截图流的设备列表"""
        return self._active_serials.copy()

    async def capture_single(self, serial: str) -> bytes:
        """
        单帧截图
        执行 adb -s <serial> exec-out screencap -p
        直接返回原始 PNG 二进制字节（绕过字符串编解码）
        """
        cmd = [self.settings.ADB_PATH, "-s", serial, "exec-out", "screencap", "-p"]

        def _run():
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=10,
            )
            if result.returncode != 0 and not result.stdout:
                err_msg = result.stderr.decode("utf-8", errors="replace") if result.stderr else "unknown error"
                raise RuntimeError(f"ADB screencap 失败 (rc={result.returncode}): {err_msg}")
            return result.stdout  # 直接返回 bytes，不做编解码转换！

        try:
            return await asyncio.to_thread(_run)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"ADB screencap 超时: {serial}")

    async def start_stream(
        self,
        serial: str,
        callback: Callable,
        fps: float = 2.0,
    ) -> None:
        """
        启动截图流，按 fps 频率回调推送
        callback: async (image_bytes) -> None
        """
        if serial in self._stream_tasks:
            return  # 已在流采集

        self._active_serials.add(serial)

        async def loop():
            interval = 1.0 / fps
            while True:
                try:
                    frame = await self.capture_single(serial)
                    await callback(frame)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    print(f"[ScreenCapture] 截图失败 {serial}: {e}")
                await asyncio.sleep(interval)

        self._stream_tasks[serial] = asyncio.create_task(loop())

    async def stop_stream(self, serial: str) -> None:
        """停止截图流"""
        self._active_serials.discard(serial)
        task = self._stream_tasks.pop(serial, None)
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


# 全局单例
screen_capture = ScreenCapture()
