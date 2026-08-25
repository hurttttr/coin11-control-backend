"""
任务引擎核心回归测试

覆盖：
1. 停止队列后不再启动下一个任务（最严重缺陷回归）
2. 停止后子进程被真正杀死（进程树终止）
3. 任务超时：标记失败并继续执行下一个任务
4. log_lines 内存上限生效
5. 入队白名单校验（防回归）

环境约定：不依赖真实 ADB / coin11-tb 仓库。
- 使用工作区内的临时目录（.test-tmp/unit/...），避免沙箱禁止写系统 TEMP
- 通过环境变量 COIN11_TB_PATH + get_settings.cache_clear() 重定向脚本目录
"""

import asyncio
import os
import shutil
import sys
import time

import pytest

# 必须在导入 app 之前执行：把临时目录重定向到工作区内，
# 保证 launcher 临时脚本与假 coin11_tb 目录可写（沙箱可能禁止写系统 TEMP）。
_WORKSPACE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_TEST_ROOT = os.path.join(_WORKSPACE, ".test-tmp", "unit")
os.makedirs(_TEST_ROOT, exist_ok=True)
os.environ["TMP"] = _TEST_ROOT
os.environ["TEMP"] = _TEST_ROOT

# 哑脚本内容（在伪 coin11_tb 目录中由 launcher 执行）
SLEEP_FOREVER = '\n'.join([
    "import time",
    "print('TASK1_STARTED', flush=True)",
    "time.sleep(3600)  # 长时间挂起，用于测试停止/超时",
    "",
])
QUICK = '\n'.join([
    "print('QUICK_OK', flush=True)",
    "",
])
UTILS_STUB = '\n'.join([
    "def set_terminal_title(*args, **kwargs):",
    "    pass",
    "def select_device(*args, **kwargs):",
    "    return 'FAKE'",
    "",
])
FAKE_SCRIPTS = {
    "sleep_forever.py": SLEEP_FOREVER,
    "quick.py": QUICK,
    "utils.py": UTILS_STUB,
}


@pytest.fixture()
def engine(monkeypatch):
    """构造独立 TaskEngine：伪 coin11_tb 目录 + 独立配置 + 小超时"""
    test_dir = os.path.join(_TEST_ROOT, f"t_{os.getpid()}_{time.monotonic_ns()}")
    os.makedirs(test_dir, exist_ok=True)
    tb_dir = os.path.join(test_dir, "coin11_tb")
    os.makedirs(tb_dir, exist_ok=True)
    for name, content in FAKE_SCRIPTS.items():
        with open(os.path.join(tb_dir, name), "w", encoding="utf-8") as f:
            f.write(content)

    settings_file = os.path.join(test_dir, "auto_task_settings.json")
    with open(settings_file, "w", encoding="utf-8") as f:
        f.write('{"auto_tasks": []}')

    monkeypatch.setenv("COIN11_TB_PATH", tb_dir)
    monkeypatch.setenv("AUTO_TASK_SETTINGS_FILE", settings_file)

    # 清除 settings 缓存，让新的 TaskEngine 读到 COIN11_TB_PATH
    from app.core.config import get_settings

    get_settings.cache_clear()

    from app.services.task_engine import TaskEngine

    eng = TaskEngine(task_timeout=None)  # 默认不限时；超时测试内单独设置
    yield eng

    # 兜底：清理可能残留的临时 launcher/目录
    eng._queues.clear()
    eng._running.clear()
    eng._current_task.clear()
    shutil.rmtree(test_dir, ignore_errors=True)


async def _wait_until(cond, timeout=10.0, interval=0.05):
    """轮询等待条件成立，超时则断言失败"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return
        await asyncio.sleep(interval)
    raise AssertionError(f"等待条件超时（{timeout}s）: {cond}")


async def test_stop_queue_does_not_start_next_task(engine):
    """核心回归：停止队列后绝不启动下一个任务"""
    from app.services.task_engine import TaskEngine

    assert isinstance(engine, TaskEngine)
    DEV = "test-device-stop"
    t1 = await engine.enqueue(DEV, "sleep_forever.py")
    t2 = await engine.enqueue(DEV, "quick.py")

    started = await engine.start_queue(DEV)
    assert started is True
    # 等待任务1进入 running（子进程已启动）
    await _wait_until(
        lambda: engine._current_task.get(DEV) is t1 and t1.status == "running",
        timeout=10.0,
    )

    # 关键：停止队列，等待 runner 完全退出
    await engine.stop_queue(DEV)

    # 1) 任务1被标记失败（手动停止）
    assert t1.status == "failed"
    # 2) 任务2必须仍是 pending，且从未被启动
    assert t2.status == "pending", f"停止队列后任务2不应被启动，实际 status={t2.status}"
    assert not t2.log_lines, "任务2不应产生任何日志"

    # 3) run 状态已清理，可再次启动
    assert engine._running.get(DEV) is None
    assert engine._current_task.get(DEV) is None


async def test_stop_queue_kills_subprocess(engine):
    """回归：停止队列后子进程被真正杀死"""
    DEV = "test-device-kill"
    t1 = await engine.enqueue(DEV, "sleep_forever.py")
    await engine.start_queue(DEV)
    await _wait_until(
        lambda: engine._current_task.get(DEV) is t1 and t1.status == "running",
        timeout=10.0,
    )
    # 记录子进程 PID
    assert t1._process is not None, "子进程句柄应已被记录"
    proc = t1._process

    await engine.stop_queue(DEV)
    # 等一小会儿让 taskkill 生效（stop_queue 内部已等待终止任务完成）
    await _wait_until(lambda: proc.poll() is not None, timeout=10.0)
    assert proc.poll() is not None, "停止队列后子进程必须已被杀死"
    assert t1.status == "failed"


async def test_task_timeout_fails_and_continues(engine):
    """回归：任务超时 → 标记 failed + 超时日志，且继续执行下一个任务"""
    DEV = "test-device-timeout"
    t1 = await engine.enqueue(DEV, "sleep_forever.py")
    t2 = await engine.enqueue(DEV, "quick.py")

    # 仅此测试启用短超时（0.8s），且只作用于任务1
    engine.task_timeout = 0.8
    await engine.start_queue(DEV)
    # 任务1先 running，随后因 0.8s 超时
    await _wait_until(
        lambda: engine._current_task.get(DEV) is t1 and t1.status == "running",
        timeout=10.0,
    )
    # 关键：任务1尚未超时（其 0.8s 超时已提交），此时把引擎超时关掉，
    # runner 启动任务2时会快照到 None —— 避免任务2被误杀
    engine.task_timeout = None
    await _wait_until(lambda: t1.status != "running", timeout=10.0)
    assert t1.status == "failed", f"超时后任务1应为 failed, 实际 {t1.status}"

    log1 = "\n".join(t1.log_lines)
    assert "[系统] 任务执行超时" in log1, f"任务1日志应含超时提示:\n{log1}"

    # 队列继续执行任务2（quick.py 立即打印 QUICK_OK 后正常退出）
    await _wait_until(
        lambda: engine._current_task.get(DEV) is t2 and t2.status == "running",
        timeout=10.0,
    )
    await _wait_until(lambda: t2.status in ("completed", "failed"), timeout=10.0)
    assert t2.status == "completed", f"任务2应正常完成, 实际 {t2.status}"
    assert any("QUICK_OK" in line for line in t2.log_lines), "任务2日志应含 QUICK_OK 标记"


async def test_log_lines_capped(engine):
    """回归：log_lines 使用 deque 有上限，to_dict/get_replay_logs 切片仍正确"""
    from app.services.task_engine import Task

    DEV = "test-device-capped"
    t1 = await engine.enqueue(DEV, "quick.py")

    # 直接灌入超过上限的日志（模拟长跑任务疯狂输出）
    for i in range(Task.MAX_LOG_LINES + 3000):
        t1.log_lines.append(f"line {i}")

    assert len(t1.log_lines) == Task.MAX_LOG_LINES, "log_lines 长度应被限制在上限"

    d = t1.to_dict()
    lines = d["log"].split("\n")
    assert len(lines) == 200, "to_dict 应只暴露最近 200 行"
    # 总共追加了 MAX_LOG_LINES + 3000 行，deque 只保留最后 MAX_LOG_LINES 行：
    # 保留行号区间 [3000, MAX_LOG_LINES + 2999]；其中最后 200 行是 [4800, 4999]
    assert lines[0] == "line 4800", f"第一个保留行应为 4800, 实际 {lines[0]}"
    assert lines[-1] == f"line {Task.MAX_LOG_LINES + 2999}"  # 4999 = 最后追加的行号

    replay = await engine.get_replay_logs(DEV)
    assert len(replay) == 1
    assert replay[0]["lines"][0] == "line 4800"
    assert replay[0]["lines"][-1] == f"line {Task.MAX_LOG_LINES + 2999}"


async def test_enqueue_rejects_unknown_script(engine):
    """回归：白名单校验拒绝不存在/未收录的脚本名"""
    from app.services.task_engine import TaskEngine

    assert isinstance(engine, TaskEngine)
    DEV = "test-device-whitelist"
    with pytest.raises(ValueError):
        await engine.enqueue(DEV, "not_on_disk.py")
    # 队列保持为空
    assert await engine.get_queue(DEV) == []
