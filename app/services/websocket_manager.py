"""
WebSocket 连接池管理
管理设备维度的 WebSocket 连接，支持广播推送（日志、状态、截图）
"""

import asyncio
import base64
import json

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
        """向订阅某设备的全部客户端推送 JSON 消息。

        并发发送：使用 asyncio.gather 同时给所有客户端发送，
        慢客户端不再阻塞其他客户端的推送（2 FPS 截图场景尤为重要）。
        发送失败的连接在结束后统一清理；快照迭代避免并发修改 set。
        """
        if device_id not in self._connections:
            return
        payload = json.dumps({
            "type": message_type,
            "device_id": device_id,
            "data": data,
        })
        # 快照连接集合，防止并发修改 set 导致 RuntimeError
        conns = list(self._connections[device_id])
        results = await asyncio.gather(
            *(ws.send_text(payload) for ws in conns),
            return_exceptions=True,
        )
        dead: set[WebSocket] = set()
        for ws, res in zip(conns, results):
            if isinstance(res, Exception):
                dead.add(ws)
        for ws in dead:
            await self.disconnect(device_id, ws)

    async def send_screenshot(self, device_id: str, image_bytes: bytes) -> None:
        """推送 base64 编码的截图帧"""
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        await self.broadcast(device_id, "screenshot", b64)

    async def send_log(self, device_id: str, text: str, task_id: str = "") -> None:
        """推送实时日志行（纯文本，避免双重 JSON 编码）。

        ⚠️ 消息格式注意（历史遗留，勿随意改动）：
        实时日志的 data 是**裸文本**（text 原样）；
        而 send_replay() 回放的日志 data 是 JSON 字符串
        {"task_id": ..., "text": ...} —— 两种来源的 data 格式**不一致**，
        前端按"data 是否携带 task_id 标记"区分回放消息并去重。

        统一建议：让实时日志也走 {"task_id": task_id, "text": text} JSON，
        并让 task_engine 的 log_callback 传入 task_id —— 需前端配合调整
        解析逻辑（详见报告）。

        task_id 参数当前**未使用**，仅保留以兼容历史调用点，是个已知陷阱。
        """
        await self.broadcast(
            device_id,
            "log",
            text,
        )

    async def send_replay(self, device_id: str, replay: list[dict]) -> None:
        """
        回放该设备队列的历史日志（WS 连接建立时调用）。

        每条日志的 data 为 JSON 字符串 {"task_id":..., "text":...}，
        供前端按 task_id 识别历史日志并去重（避免与 fetchQueue 注入重复）。
        """
        if not replay:
            return
        for task in replay:
            task_id = task.get("task_id", "")
            for line in task.get("lines", []):
                data = json.dumps({"task_id": task_id, "text": line})
                await self.broadcast(device_id, "log", data)

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
