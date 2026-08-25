"""API 批量操作与设备列表端点单元测试（不进入 lifespan，无 watcher 副作用）

覆盖:
1. batch-start 的截图回调绑定到各自的 device_id（核心回归点，修复前必然失败）
2. GET /api/devices 不再触发自动任务
3. PUT /settings/auto-tasks 非法输入返回 422
"""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.router import router as v1_router
from app.services.screen_capture import screen_capture as sc_capture


@pytest.fixture()
def client():
    """轻量 FastAPI 实例：只挂载 v1 路由，避免 lifespan 启动 watcher/clone 仓库"""
    app = FastAPI()
    app.include_router(v1_router)
    with TestClient(app) as c:
        yield c


def test_batch_start_binds_screenshot_callback_to_each_device(monkeypatch, client):
    """核心回归：批量启动时，每个设备的截图回调必须绑定各自的 serial。

    修复前的实现是循环变量 lambda（late binding），所有回调都指向最后一个
    device_id；修复后为 functools.partial 绑定。
    """
    serials = ["dev-a", "dev-b"]

    recorded = []  # (serial, callback) 由 start_stream 收集
    sent_to: list[str] = []  # send_screenshot 收到的 device_id

    async def fake_start_queue(device_id, **kwargs):
        return True

    async def fake_start_stream(serial, callback, fps=2.0):
        recorded.append((serial, callback))

    async def fake_send_screenshot(device_id, image_bytes):
        sent_to.append(device_id)

    sc_capture._active_serials.clear()  # 等价于 active_streams 为空

    # 注意：send_screenshot 必须在发起请求前替换——start_device_queue 构造
    # partial 时绑定的是当前 ws_manager.send_screenshot 函数对象
    monkeypatch.setattr(
        "app.services.websocket_manager.ws_manager.send_screenshot", fake_send_screenshot
    )
    monkeypatch.setattr("app.services.task_engine.task_engine.start_queue", fake_start_queue)
    monkeypatch.setattr(
        "app.services.screen_capture.screen_capture.start_stream", fake_start_stream
    )

    resp = client.post("/api/tasks/batch-start", json={"device_ids": serials})
    assert resp.status_code == 200
    body = resp.json()
    assert body["succeeded"] == 2
    assert body["failed"] == 0

    # 每个设备都启动了截图流，且回调是各自独立的
    assert len(recorded) == 2
    by_serial = {serial: callback for serial, callback in recorded}
    assert set(by_serial.keys()) == set(serials)

    # 分别触发回调：修复前（lambda 晚绑定）两个回调都会推给最后一个 serial，
    # 修复后（partial 绑定）各推各的
    async def _invoke():
        await by_serial["dev-a"](b"frame-a")
        await by_serial["dev-b"](b"frame-b")

    asyncio.run(_invoke())
    assert sent_to == ["dev-a", "dev-b"], "截图回调存在闭包晚绑定，推给了错误的设备"


def test_get_devices_does_not_trigger_auto_tasks(monkeypatch, client):
    """GET /api/devices 不应再有触发自动任务的副作用"""
    devices = [
        {"serial": "dev-1", "model": "X", "status": "online", "connection_type": "usb", "android_version": "14"},
        {"serial": "dev-2", "model": "Y", "status": "online", "connection_type": "wifi", "android_version": "13"},
    ]
    triggered = []

    async def fake_get_devices():
        return devices

    async def fake_run_auto_tasks(device_id):
        triggered.append(device_id)

    monkeypatch.setattr("app.services.device_manager.device_manager.get_devices", fake_get_devices)
    monkeypatch.setattr("app.api.v1.devices.run_auto_tasks", fake_run_auto_tasks)

    resp = client.get("/api/devices")
    assert resp.status_code == 200
    assert resp.json() == devices
    assert triggered == [], "GET /api/devices 不应触发自动任务"


def test_settings_put_rejects_non_list(monkeypatch, client):
    """PUT /settings/auto-tasks 收到非数组时返回 422（Pydantic 校验）"""
    monkeypatch.setattr(
        "app.services.auto_task_settings.auto_task_settings.set_auto_tasks",
        lambda tasks: None,
    )
    resp = client.put("/api/settings/auto-tasks", json={"auto_tasks": "not-a-list"})
    assert resp.status_code == 422


def test_settings_put_accepts_valid_list(monkeypatch, client):
    """PUT /settings/auto-tasks 合法列表返回 200 且结构不变"""
    captured = []

    def fake_set(tasks):
        captured.append(tasks)

    monkeypatch.setattr("app.services.auto_task_settings.auto_task_settings.set_auto_tasks", fake_set)
    tasks = ["淘宝芭芭农场.py"]
    resp = client.put("/api/settings/auto-tasks", json={"auto_tasks": tasks})
    assert resp.status_code == 200
    assert resp.json() == {"success": True, "auto_tasks": tasks}
    assert captured == [tasks]