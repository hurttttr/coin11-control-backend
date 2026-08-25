"""
AutoTaskWatcher 单元测试 — 直接驱动 AutoTaskWatcher._scan_once()/模块函数，
不 import app.main，不依赖真实 ADB / 配置文件。

覆盖:
- 新设备触发且仅触发一次（watch 去重）
- 已触发设备不重复触发
- 设备消失后从 _auto_task_triggered 中 forget，断开重连可重新触发 (P1-7)
- refresh() 每 tick 被调用
- run_auto_tasks 的入队 ValueError 分支与 start_queue_full 调用契约

注意: _auto_task_triggered 是进程级模块状态，测试前/后必须清理。
"""

import pytest

from app.services import auto_task_runner as atr
from app.services.auto_task_runner import AutoTaskWatcher, _auto_task_triggered


class FakeTask:
    id = "fake-task"


class FakeTaskEngine:
    """记录 enqueue / start_queue_full 调用；enqueue 可选抛 ValueError"""

    def __init__(self):
        self.enqueued: list[tuple[str, str]] = []
        self.start_queue_full_calls: list[str] = []
        self.fail_enqueue_for: set[str] = set()

    async def enqueue(self, device_id, script_name):
        if script_name in self.fail_enqueue_for:
            raise ValueError(f"脚本 '{script_name}' 不在可用列表中")
        self.enqueued.append((device_id, script_name))
        return FakeTask()

    async def start_queue_full(self, device_id):
        self.start_queue_full_calls.append(device_id)
        return True


class FakeDeviceManager:
    """refresh() 记录调用；get_devices() 返回脚本控制的设备列表"""

    def __init__(self, initial: list[str] | None = None):
        self.refresh_count = 0
        self.serials: list[str] = initial or []

    async def refresh(self):
        self.refresh_count += 1

    async def get_devices(self):
        return [{"serial": s} for s in self.serials]

    def set_devices(self, serials: list[str]):
        self.serials = serials


class FakeAutoTaskSettings:
    auto_tasks: list[str] = ["test.py"]

    def has_auto_tasks(self) -> bool:
        return True

    def get_auto_tasks(self) -> list[str]:
        return self.auto_tasks


@pytest.fixture(autouse=True)
def _cleanup_module_state():
    """每个用例前清理模块级去重集合与单例状态"""
    _auto_task_triggered.clear()
    atr.auto_task_watcher._seen.clear()
    yield
    _auto_task_triggered.clear()
    atr.auto_task_watcher._seen.clear()


@pytest.fixture
def fakes(monkeypatch):
    dm = FakeDeviceManager()
    te = FakeTaskEngine()
    settings = FakeAutoTaskSettings()
    monkeypatch.setattr(atr, "device_manager", dm)
    monkeypatch.setattr(atr, "task_engine", te)
    monkeypatch.setattr(atr, "auto_task_settings", settings)
    return dm, te, settings


@pytest.mark.asyncio
async def test_watcher_new_device_trigger_only_once(fakes):
    dm, te, _ = fakes
    watcher = AutoTaskWatcher()

    # 首次扫描 [A] → A 被触发
    dm.set_devices(["A"])
    await watcher._scan_once()
    assert te.enqueued == [("A", "test.py")]
    assert te.start_queue_full_calls == ["A"]
    assert "A" in _auto_task_triggered
    assert dm.refresh_count == 1

    # 第二次相同列表 [A] → A 不重复触发
    await watcher._scan_once()
    assert te.enqueued == [("A", "test.py")]
    assert te.start_queue_full_calls == ["A"]
    assert dm.refresh_count == 2


@pytest.mark.asyncio
async def test_watcher_add_and_remove_device(fakes):
    dm, te, _ = fakes
    watcher = AutoTaskWatcher()

    # [A] → A 触发
    dm.set_devices(["A"])
    await watcher._scan_once()
    assert ("A", "test.py") in te.enqueued

    # [A, B] → 仅 B 新触发，A 不重复
    dm.set_devices(["A", "B"])
    await watcher._scan_once()
    assert te.enqueued.count(("A", "test.py")) == 1
    assert ("B", "test.py") in te.enqueued
    assert "B" in _auto_task_triggered

    # [A] → B 消失，从 _seen 与 _auto_task_triggered 中 forget
    dm.set_devices(["A"])
    await watcher._scan_once()
    assert "B" not in _auto_task_triggered
    assert "B" not in watcher._seen

    # [A, B] → B 记为全新设备，再次触发（第 2 次触发）
    dm.set_devices(["A", "B"])
    await watcher._scan_once()
    assert te.enqueued.count(("B", "test.py")) == 2  # scan2 + scan4 各一次
    # 且 refresh 在每次 tick 都被调用
    assert dm.refresh_count == 4


@pytest.mark.asyncio
async def test_watcher_reconnect_retriggers(fakes):
    dm, te, _ = fakes
    watcher = AutoTaskWatcher()

    # [A] → A 触发
    dm.set_devices(["A"])
    await watcher._scan_once()
    assert te.enqueued.count(("A", "test.py")) == 1

    # [] → A 消失
    dm.set_devices([])
    await watcher._scan_once()
    assert "A" not in _auto_task_triggered

    # [A] → A 重连，重新触发 (P1-7)
    dm.set_devices(["A"])
    await watcher._scan_once()
    assert te.enqueued.count(("A", "test.py")) == 2


@pytest.mark.asyncio
async def test_run_auto_tasks_enqueue_valueerror_skips_start(fakes):
    dm, te, settings = fakes
    settings.auto_tasks = ["ok.py", "bad.py"]
    te.fail_enqueue_for = {"bad.py"}
    te.start_queue_full_calls.clear()

    await atr.run_auto_tasks("A")

    assert te.enqueued == [("A", "ok.py")]
    # 有一个成功入队 → 调用 start_queue_full
    assert te.start_queue_full_calls == ["A"]
    # bad.py 抛 ValueError 被捕获，A 仍标记为已触发（避免死循环重试）
    assert "A" in _auto_task_triggered


@pytest.mark.asyncio
async def test_run_auto_tasks_no_enqueued_skips_start(fakes):
    dm, te, settings = fakes
    settings.auto_tasks = ["only-bad.py"]
    te.fail_enqueue_for = {"only-bad.py"}

    await atr.run_auto_tasks("A")

    assert te.enqueued == []
    # 入队数为 0 → 不应调用 start_queue_full
    assert te.start_queue_full_calls == []


@pytest.mark.asyncio
async def test_watcher_skips_scan_when_no_auto_tasks(fakes):
    dm, te, settings = fakes
    settings.has_auto_tasks = lambda: False
    watcher = AutoTaskWatcher()

    dm.set_devices(["A"])
    await watcher._scan_once()

    # 未配置自动任务时不调用 refresh（保持现有优化）
    assert dm.refresh_count == 0
    assert te.enqueued == []
