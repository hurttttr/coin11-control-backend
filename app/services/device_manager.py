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
        # 最近一次 refresh() 的设备快照缓存。get_devices() 只返回此缓存，
        # 不直接执行 adb —— 全量扫描统一由 refresh() 驱动（watcher/显式调用），
        # 避免 watcher 与前端轮询各自触发一遍 adb devices。
        self._devices_cache: list[dict] = []

    async def refresh(self) -> list[dict]:
        """
        执行真实的 adb devices -l 全量扫描并解析，更新 _devices_cache。
        返回本次扫描后的设备快照。
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
                # 过滤 ADB TLS 服务发现名称（如 adb-xxx._adb-tls-connect._tcp），
                # 但保留已有 model/product/device 信息的已连接设备
                has_model = "model:" in detail or "product:" in detail or "device:" in detail
                if ("_adb-tls-connect" in serial or serial.startswith("adb-")) and not has_model:
                    continue
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
        self._devices_cache = devices
        return devices

    async def get_devices(self) -> list[dict]:
        """
        获取已连接的 ADB 设备列表 —— 返回最近一次 refresh() 的缓存快照。
        不直接执行 adb（避免与 watcher 双重全量扫描）。
        """
        return self._devices_cache

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
        先 refresh() 刷新设备缓存判断设备在线，再通过一次 adb 调用
        （adb -s <serial> shell "getprop ...; getprop ..."）获取型号和 Android 版本。
        """
        # 先刷新缓存并检查设备是否在线
        devices = await self.refresh()
        device_map = {d["serial"]: d for d in devices}
        if serial not in device_map:
            return None

        # 一次 adb 调用获取型号 + Android 版本（两条 getprop，按行解析）
        stdout_props, _, _ = await self._run_adb(
            "-s", serial, "shell",
            "getprop ro.product.model; getprop ro.build.version.release",
            timeout=5,
        )
        lines = [ln.strip() for ln in stdout_props.splitlines() if ln.strip()]
        model = lines[0] if lines else device_map[serial]["model"]
        android_version = lines[1] if len(lines) > 1 else "Unknown"

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
