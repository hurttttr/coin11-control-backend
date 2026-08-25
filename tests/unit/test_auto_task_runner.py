"""AutoTaskWatcher / run_auto_tasks 单元测试

覆盖:
1. 设备消失后再上线能重新触发（核心回归点）
2. 已在线设备不会重复触发
3. 未配置自动任务时 watcher 不调用 ADB

全部通过 monkeypatch 替换 device_manager / task_engine / screen_capture /
auto_task_settings，不接触真实 ADB。

注意：auto_task_runner 在模块导入时已用 from app.services.auto_task_settings
import auto_task_settings 绑定单例引用，因此测试必须 patch 该单例实例上的
方法（而非替换 app.services.auto_task_settings 模块属性），否则 runner 看到的
仍是真实单例。
"""

import asyncio

import pytest

from app.services.auto_task_runner import (
    MISS_THRESHOLD,
    AutoTaskWatcher,
    _absence_counter,
    _auto_task_triggered,
    forget_device,
    is_triggered,
    retain_devices,
    run_auto_tasks,
)
from app.services.screen_capture import screen_capture as sc_capture
from app.services.auto_task_settings import auto_task_settings as real_settings

SERIAL = "FAKE-SERIAL-01"
AUTO_TASKS = ["test.py", "淘宝芭芭农场.py"]


@pytest.fixture(autouse=True)
def _reset_state():
    """每个用例前重置模块级去重集合、缺席计数与截图流状态"""
    _auto_task_triggered.clear()
    _absence_counter.clear()
    sc_capture._active_serials.clear()
    yield
    _auto_task_triggered.clear()
    _absence_counter.clear()


def test_device_reappears_after_disconnect_retriggers(monkeypatch):
    """设备掉线再上线：从去重集合移除后可重新触发（核心回归点）"""
    calls = []

    async def fake_enqueue(device_id, script_name):
        calls.append(("enqueue", device_id, script_name))
        return None

    async def fake_start_queue(device_id, **kwargs):
        calls.append(("start_queue", device_id))
        return True

    async def fake_start_stream(serial, callback, fps=2.0):
        calls.append(("start_stream", serial))

    monkeypatch.setattr("app.services.task_engine.task_engine.enqueue", fake_enqueue)
    monkeypatch.setattr("app.services.task_engine.task_engine.start_queue", fake_start_queue)
    monkeypatch.setattr("app.services.task_engine.task_engine.dequeue", lambda *a, **k: True)
    monkeypatch.setattr("app.services.task_engine.task_engine.reorder", lambda *a, **k: [])
    monkeypatch.setattr("app.services.task_engine.task_engine.get_queue", lambda *a, **k: [])
    monkeypatch.setattr("app.services.screen_capture.screen_capture.start_stream", fake_start_stream)
    monkeypatch.setattr(real_settings, "get_auto_tasks", lambda: AUTO_TASKS)
    monkeypatch.setattr(real_settings, "has_auto_tasks", lambda: True)

    # 第一次上线：触发
    asyncio.run(run_auto_tasks(SERIAL))
    assert is_triggered(SERIAL)
    enqueued = [c for c in calls if c[0] == "enqueue"]
    assert len(enqueued) == len(AUTO_TASKS)
    starts = [c for c in calls if c[0] == "start_queue"]
    assert len(starts) == 1
    assert calls.count(("start_stream", SERIAL)) == 1

    # 模拟 watcher 扫描：设备在线 → 已触发状态保留 → 不会重复触发
    retain_devices({SERIAL})
    asyncio.run(run_auto_tasks(SERIAL))
    assert len([c for c in calls if c[0] == "enqueue"]) == len(AUTO_TASKS)

    # 设备掉线：需连续 MISS_THRESHOLD 轮缺席才遗忘（防 ADB 抖动误重触发）
    for _ in range(MISS_THRESHOLD - 1):
        retain_devices(set())
        assert is_triggered(SERIAL), "缺席未达阈值时不应遗忘"
    retain_devices(set())
    assert not is_triggered(SERIAL)
    asyncio.run(run_auto_tasks(SERIAL))
    enqueued = [c for c in calls if c[0] == "enqueue"]
    assert len(enqueued) == 2 * len(AUTO_TASKS)
    assert calls.count(("start_stream", SERIAL)) == 2


def test_forget_device_clears_trigger_state():
    """forget_device 应把设备从已触发集合中移除"""
    _auto_task_triggered.add("dev-a")
    _auto_task_triggered.add("dev-b")
    forget_device("dev-a")
    assert not is_triggered("dev-a")
    assert is_triggered("dev-b")


def test_watcher_does_not_call_adb_without_auto_tasks(monkeypatch):
    """未配置自动任务时，watcher 不应调用 ADB"""
    adb_calls = []

    async def fake_get_devices():
        adb_calls.append("get_devices")
        return []

    monkeypatch.setattr("app.services.device_manager.device_manager.get_devices", fake_get_devices)
    monkeypatch.setattr(real_settings, "get_auto_tasks", lambda: [])
    monkeypatch.setattr(real_settings, "has_auto_tasks", lambda: False)

    watcher = AutoTaskWatcher(interval=0.01)
    asyncio.run(watcher._loop(loop_count=2))

    assert adb_calls == [], "未配置自动任务时不应调用 ADB"


def test_watcher_no_duplicate_for_still_online_device(monkeypatch):
    """已在线设备（已触发过）不会被重复触发"""
    enqueue_calls = []

    async def fake_get_devices():
        return [{"serial": "recent-serial"}]

    async def fake_enqueue(device_id, script_name):
        enqueue_calls.append((device_id, script_name))
        return None

    async def fake_start_queue(device_id, **kwargs):
        return True

    async def fake_start_stream(serial, callback, fps=2.0):
        pass

    _auto_task_triggered.update({"recent-serial"})  # 模拟该设备已被"上一次"触发

    monkeypatch.setattr("app.services.device_manager.device_manager.get_devices", fake_get_devices)
    monkeypatch.setattr(real_settings, "get_auto_tasks", lambda: AUTO_TASKS)
    monkeypatch.setattr(real_settings, "has_auto_tasks", lambda: True)
    monkeypatch.setattr("app.services.task_engine.task_engine.enqueue", fake_enqueue)
    monkeypatch.setattr("app.services.task_engine.task_engine.start_queue", fake_start_queue)
    monkeypatch.setattr("app.services.screen_capture.screen_capture.start_stream", fake_start_stream)

    watcher = AutoTaskWatcher(interval=0.01)
    asyncio.run(watcher._loop(loop_count=2))

    # 设备一直在线且已触发 → watcher 两轮都不应重复入队
    assert enqueue_calls == []
    assert is_triggered("recent-serial")


def test_watcher_forgets_disappeared_device_then_retriggers(monkeypatch):
    """watcher 扫描时清理消失设备；该设备再次上线时重新触发"""
    enqueue_calls = []
    device_list: list[dict] = []

    async def fake_get_devices():
        return list(device_list)

    async def fake_enqueue(device_id, script_name):
        enqueue_calls.append((device_id, script_name))
        return None

    async def fake_start_queue(device_id, **kwargs):
        return True

    async def fake_start_stream(serial, callback, fps=2.0):
        pass

    _auto_task_triggered.add("was-online")  # 模拟上一轮触发过

    monkeypatch.setattr("app.services.device_manager.device_manager.get_devices", fake_get_devices)
    monkeypatch.setattr(real_settings, "get_auto_tasks", lambda: AUTO_TASKS)
    monkeypatch.setattr(real_settings, "has_auto_tasks", lambda: True)
    monkeypatch.setattr("app.services.task_engine.task_engine.enqueue", fake_enqueue)
    monkeypatch.setattr("app.services.task_engine.task_engine.start_queue", fake_start_queue)
    monkeypatch.setattr("app.services.screen_capture.screen_capture.start_stream", fake_start_stream)

    watcher = AutoTaskWatcher(interval=0.01)

    # 前 MISS_THRESHOLD 轮：设备 "was-online" 不在线 → 达到阈值后才移除
    device_list.append({"serial": "other-device"})
    asyncio.run(watcher._loop(loop_count=MISS_THRESHOLD))
    assert not is_triggered("was-online")
    assert is_triggered("other-device")

    # 第 2 轮：设备 "was-online" 重新上线 → 重新触发
    device_list.append({"serial": "was-online"})
    asyncio.run(watcher._loop(loop_count=1))
    assert is_triggered("was-online")
    assert len(enqueue_calls) >= len(AUTO_TASKS)

def test_single_round_flap_does_not_retrigger(monkeypatch):
    """ADB 抖动：设备单轮消失又立刻回来，不应重复入队自动任务"""
    enqueue_calls = []
    device_list: list[dict] = [{"serial": "flappy"}]

    async def fake_get_devices():
        return list(device_list)

    async def fake_enqueue(device_id, script_name):
        enqueue_calls.append((device_id, script_name))
        return None

    async def fake_start_queue(device_id, **kwargs):
        return True

    async def fake_start_stream(serial, callback, fps=2.0):
        pass

    monkeypatch.setattr("app.services.device_manager.device_manager.get_devices", fake_get_devices)
    monkeypatch.setattr(real_settings, "get_auto_tasks", lambda: AUTO_TASKS)
    monkeypatch.setattr(real_settings, "has_auto_tasks", lambda: True)
    monkeypatch.setattr("app.services.task_engine.task_engine.enqueue", fake_enqueue)
    monkeypatch.setattr("app.services.task_engine.task_engine.start_queue", fake_start_queue)
    monkeypatch.setattr("app.services.screen_capture.screen_capture.start_stream", fake_start_stream)

    watcher = AutoTaskWatcher(interval=0.01)

    # 第 1 轮：首次上线，正常触发一次
    asyncio.run(watcher._loop(loop_count=1))
    first_count = len(enqueue_calls)
    assert first_count == len(AUTO_TASKS)

    # 第 2 轮：设备短暂消失（未达遗忘阈值）
    device_list.clear()
    asyncio.run(watcher._loop(loop_count=1))

    # 第 3 轮：设备回来 —— 不应重复入队
    device_list.append({"serial": "flappy"})
    asyncio.run(watcher._loop(loop_count=1))
    assert len(enqueue_calls) == first_count, "单轮抖动不应导致重复触发自动任务"
