"""
WebSocket 连接池管理
管理设备维度的 WebSocket 连接，支持广播推送（日志、状态、截图）
"""

import asyncio
import base64
import json
from typing import Optional

from fastapi import WebSocket


class ConnectionManager:
    """WebSocket 连接池管理"""

    def __init__(self):
        # device_id -> set[WebSocket]
        self._connections: dict[str, set[WebSocket]] = {}

    async def connect(self, device_id: str, websocket: WebSocket) -> None:
        """接受并注册一个新的 WebSocket 连接"""
        await websocket.accept()
        if device_id not in self._connections:
            self._connections[device_id] = set()
        self._connections[device_id].add(websocket)

    async def disconnect(self, device_id: str, websocket: WebSocket) -> None:
        """断开并注销一个 WebSocket 连接"""
        if device_id in self._connections:
            self._connections[device_id].discard(websocket)
            if not self._connections[device_id]:
                del self._connections[device_id]

    def has_connections(self, device_id: str) -> bool:
        """检查某设备是否有活动的 WebSocket 连接"""
        return device_id in self._connections and bool(self._connections[device_id])

    async def broadcast(self, device_id: str, message_type: str, data: str) -> None:
        """向订阅某设备的全部客户端推送 JSON 消息（快照迭代防并发修改）"""
        if device_id not in self._connections:
            return
        payload = json.dumps({
            "type": message_type,
            "device_id": device_id,
            "data": data,
        })
        # 快照迭代，防止并发修改 set 导致 RuntimeError
        dead: set[WebSocket] = set()
        for ws in list(self._connections[device_id]):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.add(ws)
        for ws in dead:
            await self.disconnect(device_id, ws)

    async def send_screenshot(self, device_id: str, image_bytes: bytes) -> None:
        """推送 base64 编码的截图帧"""
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        await self.broadcast(device_id, "screenshot", b64)

    async def send_log(self, device_id: str, text: str, task_id: str = "") -> None:
        """推送日志行（纯文本，避免双重 JSON 编码）"""
        await self.broadcast(
            device_id,
            "log",
            text,
        )

    async def send_status(
        self, device_id: str, task_id: str, status: str
    ) -> None:
        """推送任务状态变更"""
        await self.broadcast(
            device_id,
            "status",
            json.dumps({"task_id": task_id, "status": status}),
        )


# 全局单例
ws_manager = ConnectionManager()
