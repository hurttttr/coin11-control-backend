"""
设备管理服务
负责 ADB 通信、设备发现、设备信息获取
使用 asyncio.to_thread + subprocess.run 避免事件循环阻塞
（Windows Python 3.14+ 兼容方案：create_subprocess_exec 可能触发 NotImplementedError）
"""

import asyncio
import logging
import re
import subprocess
import time
from typing import Optional

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# get_devices() 结果的 TTL 缓存秒数（前端 5s 轮询 + watcher 5s 扫描会重复打 ADB，
# 加短 TTL 避免每次请求都起子进程）
DEVICES_CACHE_TTL = 2.0


class DeviceManager:
    """设备管理服务 — ADB 通信封装"""

    def __init__(self):
        # (cached_at, devices) 缓存；None 表示无缓存
        self._devices_cache: Optional[tuple[float, list[dict]]] = None

    @property
    def settings(self):
        """每次访问都取当前配置单例。

        不在 __init__ 里捕获 —— 本类是模块级单例，导入时机早于
        get_settings.cache_clear()（测试用 env 覆盖配置时依赖它），
        一旦在构造时固化配置，后续 cache_clear 对本单例就完全无效。
        """
        return get_settings()

    @property
    def _adb_path(self) -> str:
        """ADB 可执行文件路径（延迟读取，随配置变化生效）"""
        return self.settings.ADB_PATH

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

    @staticmethod
    def _parse_devices_output(stdout: str) -> list[dict]:
        """解析 `adb devices -l` 输出为设备列表（纯函数，便于单元测试）"""
        devices = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line or "List of devices" in line or "attached" in line:
                continue
            # 格式: serial device model:xxx ...
            # 明细可省略（形如 "SERIAL device"），此时 detail 视为空串
            match = re.match(r'^(\S+)\s+device(?:\s+(.*))?$', line)
            if match:
                serial = match.group(1)
                detail = match.group(2) or ""
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
        return devices

    async def get_devices(self) -> list[dict]:
        """
        获取已连接的 ADB 设备列表（短 TTL 缓存，避免重复打 ADB）
        解析 adb devices -l 输出
        """
        now = time.monotonic()
        if self._devices_cache is not None:
            cached_at, cached = self._devices_cache
            if now - cached_at < DEVICES_CACHE_TTL:
                # 返回副本，防止调用方修改污染缓存
                return [dict(d) for d in cached]

        stdout, _, _ = await self._run_adb("devices", "-l")
        devices = self._parse_devices_output(stdout)
        # 缓存保存副本，避免调用方修改返回列表污染缓存
        self._devices_cache = (now, [dict(d) for d in devices])
        return devices

    def clear_devices_cache(self) -> None:
        """清空设备列表缓存（测试隔离与设备状态敏感场景使用）"""
        self._devices_cache = None

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
        # 设备拓扑已变化，作废缓存 —— 否则前端连接成功后立刻 fetchDevices
        # 会在 TTL 内拿到不含新设备的陈旧列表
        self.clear_devices_cache()
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
        # 设备拓扑已变化，作废缓存
        self.clear_devices_cache()
        return {
            "success": True,
            "message": msg,
        }

    async def pair_device(self, address: str, code: str) -> dict:
        """
        ADB 无线配对 (Android 11+)，返回 {"success": bool, "message": str}
        执行 adb pair <address> <code>
        """
        stdout, stderr, _ = await self._run_adb("pair", address, code, timeout=30)
        msg = stdout.strip() or stderr.strip()
        success = "successfully paired" in msg.lower() or "配对成功" in msg
        return {"success": success, "message": msg}

    async def get_device_info(self, serial: str) -> Optional[dict]:
        """
        获取单台设备的详细信息
        通过 adb -s <serial> shell getprop 获取型号和 Android 版本
        """
        # 先检查设备是否在线（走 get_devices 缓存，避免额外一次 adb 调用）
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
        except Exception:
            return False


# 全局单例
device_manager = DeviceManager()
