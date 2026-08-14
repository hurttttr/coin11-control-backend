"""
单元测试：app.services.task_engine

覆盖（P0）：
1. enqueue 白名单校验 / 路径穿越拒绝
2. dequeue / reorder 基本行为
3. 运行中入队的新任务自动执行（P0-2）
4. stop_queue 终止子进程（P0-1）
5. snapshot / restore（契约）
6. start_queue_full（契约）

关键约定：
- 不 import app.main；不使用模块级 task_engine 单例。
- 每个测试新建 TaskEngine() 实例，并把实例 .settings 覆盖为轻量对象。
- 临时目录建在工作区 <项目根>\\.test-tmp\\<按 pid 的唯一名>，
  结束 best-effort 清理（已 .gitignore）。
- 测试不实用 pytest tmp_path（基目录在系统 TEMP，沙箱拒绝）。
"""

import asyncio
import os
import shutil
import sys
import types

import pytest

if sys.platform != "win32":
    pytest.skip("任务引擎子进程/launcher 假定 Windows（TerminateProcess 语义）", allow_module_level=True)

# ---- 必须在导入 app 模块之前设置环境 ----
_WORKSPACE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_TMP_ROOT = os.path.join(_WORKSPACE, ".test-tmp")
os.makedirs(_TMP_ROOT, exist_ok=True)
# 防止状态文件写到项目根
os.environ["COIN11_STATE_FILE"] = os.path.join(_TMP_ROOT, f"state_{os.getpid()}.json")
# launcher 脚本（tempfile.mkstemp(dir=None)）写入系统 TEMP，重定向到工作区
os.environ["TMP"] = _TMP_ROOT
os.environ["TEMP"] = _TMP_ROOT

from app.services.task_engine import TaskEngine  # noqa: E402
from app.services.screen_capture import screen_capture as _sc_singleton  # noqa: E402


def _make_engine(base: str, tb_name: str = "coin11_tb"):
    """创建全新 TaskEngine 实例，settings 覆盖为轻量对象（指向工作区临时目录）。"""
    tb_dir = os.path.join(base, tb_name)
    os.makedirs(tb_dir, exist_ok=True)
    # launcher 模板会 `import utils` 并调用 set_terminal_title，必须提供桩模块
    _write_script(tb_dir, "utils.py", "def set_terminal_title(*a): pass\ndef select_device(*a): return 'dummy'\n")
    engine = TaskEngine()
    engine.settings = types.SimpleNamespace(coin11_tb_path_resolved=tb_dir)
    return engine, tb_dir


def _write_script(tb_dir: str, name: str, code: str):
    with open(os.path.join(tb_dir, name), "w", encoding="utf-8") as f:
        f.write(code)


async def _wait_for(pred, timeout: float = 10.0, interval: float = 0.05) -> bool:
    elapsed = 0.0
    while elapsed < timeout:
        if pred():
            return True
        await asyncio.sleep(interval)
        elapsed += interval
    return pred()


# ---------- 1. enqueue ----------

@pytest.mark.asyncio
async def test_enqueue_whitelist_and_path_traversal():
    base = os.path.join(_TMP_ROOT, f"test_enqueue_{os.getpid()}")
    shutil.rmtree(base, ignore_errors=True)
    os.makedirs(base, exist_ok=True)
    try:
        engine, tb_dir = _make_engine(base)
        _write_script(tb_dir, "a.py", "print('a')\n")

        # 白名单内脚本可入队
        task = await engine.enqueue("dev1", "a.py")
        assert task is not None
        assert task.script_name == "a.py"
        assert task.script_path == os.path.join(tb_dir, "a.py")

        # 不存在的脚本抛 ValueError
        with pytest.raises(ValueError):
            await engine.enqueue("dev1", "nonexistent.py")

        # 路径穿越 / 非白名单全名被拒
        with pytest.raises(ValueError):
            await engine.enqueue("dev1", "../a.py")
        with pytest.raises(ValueError):
            await engine.enqueue("dev1", "sub\\a.py")
        with pytest.raises(ValueError):
            await engine.enqueue("dev1", os.path.join(tb_dir, "a.py"))
    finally:
        shutil.rmtree(base, ignore_errors=True)


# ---------- 2. dequeue / reorder ----------

@pytest.mark.asyncio
async def test_dequeue_and_reorder():
    base = os.path.join(_TMP_ROOT, f"test_deq_{os.getpid()}")
    shutil.rmtree(base, ignore_errors=True)
    os.makedirs(base, exist_ok=True)
    try:
        engine, tb_dir = _make_engine(base)
        for n in ("a.py", "b.py", "c.py"):
            _write_script(tb_dir, n, "print('x')\n")

        ta = await engine.enqueue("dev1", "a.py")
        tb = await engine.enqueue("dev1", "b.py")
        tc = await engine.enqueue("dev1", "c.py")
        assert [t.position for t in engine._queues["dev1"]] == [0, 1, 2]

        # dequeue 中间任务，position 重算
        removed = await engine.dequeue("dev1", tb.id)
        assert removed is True
        ids = [t.id for t in engine._queues["dev1"]]
        assert ta.id in ids and tc.id in ids and tb.id not in ids
        assert [t.position for t in engine._queues["dev1"]] == [0, 1]

        # dequeue 不存在的任务返回 False
        assert await engine.dequeue("dev1", "does-not-exist") is False

        # reorder：翻转剩余顺序
        remaining = list(engine._queues["dev1"])
        order = [remaining[1].id, remaining[0].id]
        reordered = await engine.reorder("dev1", order)
        assert [t.id for t in reordered] == order

        # 禁止删除运行中的任务
        engine._current_task["dev1"] = ta
        ta.status = "running"
        with pytest.raises(ValueError):
            await engine.dequeue("dev1", ta.id)
    finally:
        shutil.rmtree(base, ignore_errors=True)


# ---------- 3. 运行中入队自动执行（P0-2） ----------

@pytest.mark.asyncio
async def test_running_enqueue_is_executed():
    base = os.path.join(_TMP_ROOT, f"test_running_{os.getpid()}")
    shutil.rmtree(base, ignore_errors=True)
    os.makedirs(base, exist_ok=True)
    engine = None
    try:
        engine, tb_dir = _make_engine(base)
        _write_script(
            tb_dir, "a.py",
            "import time\n"
            "time.sleep(0.2)\n"
            "open('marker_A', 'w', encoding='utf-8').write('A')\n",
        )
        _write_script(
            tb_dir, "b.py",
            "import time\n"
            "time.sleep(0.2)\n"
            "open('marker_B', 'w', encoding='utf-8').write('B')\n",
        )

        # 先启动队列（空队列 → runner 进入等待），再入队 a.py 触发自动执行
        await engine.start_queue("dev1")
        task_a = await engine.enqueue("dev1", "a.py")
        marker_a = os.path.join(tb_dir, "marker_A")
        ok_a = await _wait_for(lambda: os.path.exists(marker_a), timeout=10)
        if not ok_a:
            print("\n[dbg] status:", task_a.status)
            print("[dbg] log:", "\n| ".join(line for line in task_a.log_lines))
        assert ok_a is True, "A 标记未出现"

        # 同一 runner 继续：入队 b.py 应自动执行
        await engine.enqueue("dev1", "b.py")
        marker_b = os.path.join(tb_dir, "marker_B")
        assert await _wait_for(lambda: os.path.exists(marker_b), timeout=10) is True, "B 标记未出现，运行中入队未被自动执行"

        # runner 仍存活等待新任务
        assert engine._running["dev1"] is not None
    finally:
        if engine is not None:
            await engine.stop_all()
        shutil.rmtree(base, ignore_errors=True)


# ---------- 4. stop_queue 终止子进程（P0-1） ----------

@pytest.mark.asyncio
async def test_stop_queue_kills_subprocess():
    base = os.path.join(_TMP_ROOT, f"test_stop_{os.getpid()}")
    shutil.rmtree(base, ignore_errors=True)
    os.makedirs(base, exist_ok=True)
    engine = None
    try:
        engine, tb_dir = _make_engine(base)
        _write_script(
            tb_dir, "c.py",
            "import time\n"
            "time.sleep(10)\n"
            "open('marker_C', 'w', encoding='utf-8').write('done')\n",
        )

        task = await engine.enqueue("dev1", "c.py")
        started = await engine.start_queue("dev1")
        assert started is True

        # 等子进程真正启动
        await _wait_for(lambda: engine._current_task.get("dev1") is not None, timeout=5)
        await asyncio.sleep(0.5)

        await engine.stop_queue("dev1")

        # 当前任务 failed + 日志
        assert task.status == "failed"
        assert any("任务已手动停止" in line for line in task.log_lines)

        # 等待 2s 后完成标记不应出现（子进程已被杀）
        await asyncio.sleep(2.0)
        assert os.path.exists(os.path.join(tb_dir, "marker_C")) is False, "完成标记不应存在：子进程未被终止"

        # runner 已清空，可再次启动
        assert engine._running.get("dev1") is None
        restarted = await engine.start_queue("dev1")
        assert restarted is True
        # 清理重启动产生的 runner/子进程
        await engine.stop_queue("dev1")
    finally:
        if engine is not None:
            await engine.stop_all()
        shutil.rmtree(base, ignore_errors=True)


# ---------- 5. snapshot / restore（契约） ----------

@pytest.mark.asyncio
async def test_snapshot_restore():
    base = os.path.join(_TMP_ROOT, f"test_snap_{os.getpid()}")
    shutil.rmtree(base, ignore_errors=True)
    os.makedirs(base, exist_ok=True)
    try:
        engine, tb_dir = _make_engine(base)
        _write_script(tb_dir, "a.py", "print('a')\n")
        _write_script(tb_dir, "b.py", "print('b')\n")

        ta = await engine.enqueue("dev1", "a.py")
        tb = await engine.enqueue("dev1", "b.py")
        # 模拟一个 running 任务
        ta.status = "running"
        ta.started_at = "2024-01-01T00:00:00"
        ta.log_lines.append("hello")
        engine._current_task["dev1"] = ta
        # 构造一个带自定义 id 的任务（completed 态）
        from app.services.task_engine import Task
        _write_script(tb_dir, "c.py", "print('c')\n")
        tc = Task("dev1", "c.py", os.path.join(tb_dir, "c.py"), task_id="keep-me")
        tc.status = "completed"
        engine._queues["dev1"].append(tc)
        for i, t in enumerate(engine._queues["dev1"]):
            t.position = i

        snap = engine.snapshot()
        assert "dev1" in snap
        assert len(snap["dev1"]) == 3

        # 恢复进新引擎
        engine2, _ = _make_engine(base, tb_name="coin11_tb")
        await engine2.restore(snap)
        q = engine2._queues["dev1"]
        assert len(q) == 3

        a_rest, b_rest, c_rest = q
        # running -> failed，保留 id，日志含中断说明
        assert a_rest.status == "failed"
        assert a_rest.id == ta.id
        assert any("服务重启，任务中断" in line for line in a_rest.log_lines)
        # 原日志保留
        assert "hello" in a_rest.log_lines
        # pending 保持 pending，id 保留
        assert b_rest.status == "pending"
        assert b_rest.id == tb.id
        # 自定义 id 保留
        assert c_rest.id == "keep-me"
        # 不自动启动
        assert engine2._running.get("dev1") is None
    finally:
        shutil.rmtree(base, ignore_errors=True)


# ---------- 6. start_queue_full（契约） ----------

@pytest.mark.asyncio
async def test_start_queue_full(monkeypatch):
    base = os.path.join(_TMP_ROOT, f"test_full_{os.getpid()}")
    shutil.rmtree(base, ignore_errors=True)
    os.makedirs(base, exist_ok=True)
    engine = None
    try:
        engine, tb_dir = _make_engine(base)
        _write_script(tb_dir, "c.py", "import time; time.sleep(0.3)\n")

        called = {"start": 0}

        async def _noop(*args, **kwargs):
            called["start"] += 1

        monkeypatch.setattr(_sc_singleton, "start_stream", _noop)

        await engine.enqueue("dev1", "c.py")
        started = await engine.start_queue_full("dev1")
        assert started is True
        assert called["start"] == 1

        # 已在运行时幂等成功（不再返回 False/报 400）
        assert await engine.start_queue_full("dev1") is True

        await engine.stop_queue("dev1")
        # 停止后 _running 清空，可再次启动
        assert await engine.start_queue_full("dev1") is True
    finally:
        if engine is not None:
            await engine.stop_all()
        shutil.rmtree(base, ignore_errors=True)
