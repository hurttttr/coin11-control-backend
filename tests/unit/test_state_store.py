"""
state_store 单元测试。

要点：不 import app.main；只 import app.services.state_store（不 import task_engine）。
使用假引擎（duck-typing）验证状态保存/恢复；状态文件与临时目录建在
工作区 .test-tmp/（按 os.getpid() 唯一）下，结束 best-effort 清理。
"""
import asyncio
import json
import os
import shutil
import sys


# ---------------------------------------------------------------------------
# 工作区临时目录（不用 pytest tmp_path）。因为 state_store.STATE_FILE 在 import
# 时根据环境变量 COIN11_STATE_FILE 计算，必须在 import 之前设置好。
# ---------------------------------------------------------------------------
_TMP_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    ".test-tmp",
    str(os.getpid()),
)
_STATE_FILE = os.path.join(_TMP_ROOT, "task_state.json")
os.environ["COIN11_STATE_FILE"] = _STATE_FILE

import pytest

import app.services.state_store as state_store


# ---------------------------------------------------------------------------
# 假引擎：只提供 snapshot()/restore()，不依赖真实 task_engine。
# ---------------------------------------------------------------------------
class FakeEngine:
    def __init__(self, snapshot=None):
        self._snapshot = snapshot if snapshot is not None else {}
        self.restore_calls = []

    def snapshot(self) -> dict:
        return self._snapshot

    def restore(self, snap) -> None:
        self.restore_calls.append(snap)


@pytest.fixture
def temp_dir():
    os.makedirs(_TMP_ROOT, exist_ok=True)
    yield _TMP_ROOT
    shutil.rmtree(_TMP_ROOT, ignore_errors=True)


@pytest.fixture(autouse=True)
def reset_state(temp_dir):
    # 每个测试前清空模块内部状态，避免用例间泄漏
    state_store._engine = None
    if os.path.exists(_STATE_FILE):
        os.remove(_STATE_FILE)
    if os.path.exists(_STATE_FILE + ".tmp"):
        os.remove(_STATE_FILE + ".tmp")
    yield
    # 清理并重置引擎，释放对假引擎的引用
    state_store._engine = None


def _read_state_file() -> dict:
    with open(_STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.asyncio
async def test_on_mutation_silent_without_engine(temp_dir):
    """未 init() 时 on_mutation 直接返回，不写文件、不抛异常。"""
    await state_store.on_mutation()
    assert os.path.exists(_STATE_FILE) is False


@pytest.mark.asyncio
async def test_on_mutation_writes_file_with_correct_format(temp_dir):
    """init 后 on_mutation 写出文件，且格式为 {version, saved_at, queues}。"""
    engine = FakeEngine({"dev1": {"task1": "PENDING"}})
    state_store.init(engine)
    await state_store.on_mutation()
    assert os.path.exists(_STATE_FILE)
    data = _read_state_file()
    assert data["version"] == 1
    assert "saved_at" in data
    assert data["queues"] == {"dev1": {"task1": "PENDING"}}


@pytest.mark.asyncio
async def test_on_mutation_skips_when_snapshot_empty(temp_dir):
    """snapshot 为空 dict 时不写文件。"""
    engine = FakeEngine({})
    state_store.init(engine)
    await state_store.on_mutation()
    assert os.path.exists(_STATE_FILE) is False


@pytest.mark.asyncio
async def test_restore_state_calls_engine_restore(temp_dir):
    """restore_state 读到文件后调用 engine.restore 并传入 queues 数据。"""
    engine = FakeEngine({"some": "old-snapshot"})
    state_store.init(engine)
    # 先保存一份
    await state_store.save_now()
    # 重置引擎，模拟新实例
    restored_engine = FakeEngine({})
    state_store.init(restored_engine)
    ok = await state_store.restore_state()
    assert ok is True
    assert restored_engine.restore_calls == [{"some": "old-snapshot"}]


@pytest.mark.asyncio
async def test_restore_state_missing_file_returns_false(temp_dir):
    """文件不存在时 restore_state 返回 False。"""
    ok = await state_store.restore_state()
    assert ok is False


@pytest.mark.asyncio
async def test_cleanup_orphans_no_exception(temp_dir):
    """cleanup_orphans 不抛异常（Windows 下会真的跑 powershell 查询）。"""
    await state_store.cleanup_orphans()
    # 到达这里即未抛异常
    assert True


@pytest.mark.asyncio
async def test_save_now_writes_file(temp_dir):
    """save_now 强制保存。"""
    engine = FakeEngine({"devA": []})
    state_store.init(engine)
    await state_store.save_now()
    assert os.path.exists(_STATE_FILE)
    assert _read_state_file()["queues"] == {"devA": []}
