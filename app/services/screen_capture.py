"""
设备屏幕截图服务
支持单帧截图和持续截图流推送（通过 WebSocket）
使用 asyncio.to_thread + subprocess.run 避免事件循环阻塞
（Windows Python 3.14+ 兼容方案）
"""

import asyncio
import logging
import subprocess
from typing import Callable

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# 截图流连续失败阈值：超过后自动停止该设备的流，避免设备掉线时无限刷错误日志
MAX_CONSECUTIVE_FAILURES = 10


class ScreenCapture:
    """设备屏幕截图服务"""

    def __init__(self):
        self._stream_tasks: dict[str, asyncio.Task] = {}
        self._active_serials: set[str] = set()

    @property
    def settings(self):
        """每次访问都取当前配置单例（理由同 DeviceManager.settings）"""
        return get_settings()

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
            failures = 0
            try:
                while True:
                    try:
                        frame = await self.capture_single(serial)
                        await callback(frame)
                        failures = 0  # 恢复成功，清零连续失败计数
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        failures += 1
                        if failures >= MAX_CONSECUTIVE_FAILURES:
                            logger.error(
                                "截图流 [%s] 连续失败 %d 次，自动停止",
                                serial,
                                failures,
                            )
                            break
                        logger.warning(
                            "截图失败 [%s]: %s (连续 %d 次)",
                            serial,
                            e,
                            failures,
                        )
                    await asyncio.sleep(interval)
            finally:
                # 流结束（被 stop_stream 取消或连续失败退出）时清理自身状态，
                # 避免 _stream_tasks / _active_serials 残留导致 start_stream 误判
                self._active_serials.discard(serial)
                self._stream_tasks.pop(serial, None)

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
