"""
设备管理服务
负责 ADB 通信、设备发现、设备信息获取
使用 asyncio.to_thread + subprocess.run 避免事件循环阻塞
（Windows Python 3.14+ 兼容方案：create_subprocess_exec 可能触发 NotImplementedError）
"""

import asyncio
import re
import subprocess
from typing import Optional

from app.core.config import get_settings


class DeviceManager:
    """设备管理服务 — ADB 通信封装"""

    def __init__(self):
        self.settings = get_settings()
        self._adb_path = self.settings.ADB_PATH

    async def _run_adb(self, *args, timeout: int = 10) -> tuple[str, str, int]:
        """执行 ADB 命令，返回 (stdout, stderr, returncode)"""
        cmd = [self._adb_path] + list(args)

        def _run():
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    timeout=timeout,
                    text=True,
                )
                return (
                    result.stdout.strip(),
                    result.stderr.strip(),
                    result.returncode,
                )
            except subprocess.TimeoutExpired:
                return "", "timeout", -1

        return await asyncio.to_thread(_run)

    async def get_devices(self) -> list[dict]:
        """
        获取已连接的 ADB 设备列表
        解析 adb devices -l 输出
        """
        stdout, _, _ = await self._run_adb("devices", "-l")
        devices = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line or "List of devices" in line or "attached" in line:
                continue
            # 格式: serial device model:xxx ...
            match = re.match(r'^(\S+)\s+device\s+(.*)', line)
            if match:
                serial = match.group(1)
                detail = match.group(2)
                # 过滤 ADB TLS 握手伪设备：没有 model/device 信息的才是假的
                # 真设备即使 serial 含 adb- 前缀，也会有 model:xxx 信息
                has_model = "model:" in detail or "product:" in detail or "device:" in detail
                if ("_adb-tls-connect" in serial or serial.startswith("adb-")) and not has_model:
                    continue
                model_match = re.search(r'model:(\S+)', detail)
                model_match = re.search(r'model:(\S+)', detail)
                model = model_match.group(1) if model_match else "Unknown"
                # 通过 serial 判断连接类型: 包含 :port 则为 wifi
                conn_type = "wifi" if ":" in serial and "." in serial else "usb"
                devices.append({
                    "serial": serial,
                    "model": model,
                    "status": "online",
                    "connection_type": conn_type,
                    "android_version": "Unknown",
                })
        return devices

    async def connect_device(self, address: str) -> dict:
        """
        远程连接 ADB 设备
        执行 adb connect <address> (IP:Port)
        """
        stdout, stderr, rc = await self._run_adb("connect", address, timeout=15)
        msg = stdout.strip() or stderr.strip()
        success = (
            "connected" in msg.lower()
            or "already connected" in msg.lower()
        )
        return {
            "success": success,
            "message": msg,
            "address": address,
        }

    async def disconnect_device(self, serial: str) -> dict:
        """
        断开设备连接
        执行 adb disconnect <serial>
        """
        stdout, stderr, _ = await self._run_adb("disconnect", serial, timeout=10)
        msg = stdout.strip() or stderr.strip() or "已断开"
        return {
            "success": True,
            "message": msg,
        }

    async def get_device_info(self, serial: str) -> Optional[dict]:
        """
        获取单台设备的详细信息
        通过 adb -s <serial> shell getprop 获取型号和 Android 版本
        """
        # 先检查设备是否在线
        devices = await self.get_devices()
        device_map = {d["serial"]: d for d in devices}
        if serial not in device_map:
            return None

        # 获取型号
        stdout_model, _, _ = await self._run_adb(
            "-s", serial, "shell", "getprop", "ro.product.model", timeout=5
        )
        model = stdout_model.strip() or device_map[serial]["model"]

        # 获取 Android 版本
        stdout_ver, _, _ = await self._run_adb(
            "-s", serial, "shell", "getprop", "ro.build.version.release", timeout=5
        )
        android_version = stdout_ver.strip() or "Unknown"

        # 获取连接方式
        conn_type = "wifi" if (":" in serial and "." in serial) else "usb"

        return {
            "serial": serial,
            "model": model,
            "status": "online",
            "connection_type": conn_type,
            "android_version": android_version,
        }

    async def is_adb_available(self) -> bool:
        """检查 ADB 是否可用"""
        try:
            stdout, _, _ = await self._run_adb("version", timeout=5)
            return "Android Debug Bridge" in stdout
        except (FileNotFoundError, Exception):
            return False


# 全局单例
device_manager = DeviceManager()
