"""
回归测试: 后端无头启动（不开网页、无前端轮询）时，自动任务应自动触发

修复前 (bug): 自动任务触发逻辑只存在于 HTTP 端点中 —— GET /api/devices（前端 5s 轮询）
和 POST /api/devices/connect。用 PowerShell 启动后端但不打开网页时，没有人调用这些
端点，设备永远不会被扫描，自动任务永远不执行。

修复后: app/services/auto_task_runner.AutoTaskWatcher 在 lifespan 中启动后台循环，
周期扫描 ADB 设备，新设备上线自动入队并启动任务，不依赖任何 HTTP 请求。

本测试刻意不调用 GET /api/devices（那是旧的轮询触发路径），只通过
GET /api/devices/{serial}/queue 观察结果。

注意: 不使用 pytest 的 tmp_path fixture —— 沙箱/CI 环境禁止跨进程删除目录，
临时目录由本测试进程自建自删（按 pid 隔离）。
"""
import json
import os
import shutil
import sys
import time

import pytest

# 必须在导入 app 模块之前执行: 把临时目录重定向到工作区内，
# 保证 fake adb 与 launcher 临时脚本可写（沙箱可能禁止写系统 TEMP）。
_WORKSPACE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_TMP = os.path.join(_WORKSPACE, ".test-tmp")
os.makedirs(_TMP, exist_ok=True)
os.environ["TMP"] = _TMP
os.environ["TEMP"] = _TMP

FAKE_SERIAL = "FAKE-TEST-DEVICE"
AUTO_TASKS = ["test.py", "淘宝芭芭农场.py"]

FAKE_ADB_CMD = """@echo off
if "%1"=="devices" (
  echo List of devices attached
  echo FAKE-TEST-DEVICE device product:fakephone model:FakePhone device:fakephone transport_id:1
  exit /b 0
)
if "%1"=="version" (
  echo Android Debug Bridge version 1.0.41
  exit /b 0
)
exit /b 0
"""


def _setup_environment(tmp: str) -> dict:
    """创建假 ADB 可执行文件 + 哑脚本 coin11-tb 目录 + 独立自动任务配置"""
    fake_adb = os.path.join(tmp, "fake_adb.cmd")
    with open(fake_adb, "w", encoding="ascii") as f:
        f.write(FAKE_ADB_CMD)

    tb_dir = os.path.join(tmp, "coin11_tb")
    os.makedirs(tb_dir, exist_ok=True)
    for name in (*AUTO_TASKS, "utils.py"):
        with open(os.path.join(tb_dir, name), "w", encoding="utf-8") as f:
            f.write('def set_terminal_title(*a): pass\nprint("dummy")\n')

    settings_file = os.path.join(tmp, "auto_task_settings.json")
    with open(settings_file, "w", encoding="utf-8") as f:
        json.dump({"auto_tasks": AUTO_TASKS}, f, ensure_ascii=False)

    return {
        "ADB_PATH": fake_adb,
        "COIN11_TB_PATH": tb_dir,
        "AUTO_TASK_SETTINGS_FILE": settings_file,
        "COIN11_STATE_FILE": os.path.join(tmp, "task_state.json"),
    }


@pytest.mark.skipif(sys.platform != "win32", reason="假 ADB 使用 .cmd 可执行文件，仅 Windows")
def test_headless_auto_task_trigger():
    # 本进程私有临时目录（同进程创建/删除，避免跨进程删除被沙箱拒绝）
    test_dir = os.path.join(_TMP, f"headless_{os.getpid()}")
    shutil.rmtree(test_dir, ignore_errors=True)
    os.makedirs(test_dir, exist_ok=True)

    env = _setup_environment(test_dir)
    for key, value in env.items():
        os.environ[key] = value

    try:
        # 首次导入 app（环境变量已就绪）；若本进程已被其他用例导入过，则重置设置缓存
        from app.core.config import get_settings

        get_settings.cache_clear()

        from app.main import app
        from app.services.auto_task_runner import _auto_task_triggered
        from app.services.task_engine import task_engine

        # 重置单例状态，保证用例可重复运行
        _auto_task_triggered.clear()
        for serial in list(task_engine._queues):
            del task_engine._queues[serial]
        task_engine._current_task.clear()
        task_engine._running.clear()

        from fastapi.testclient import TestClient

        with TestClient(app) as client:  # 进入 lifespan → watcher 启动
            # 只观察队列，绝不调用 GET /api/devices（模拟没有打开网页）
            deadline = time.time() + 20
            queue = []
            while time.time() < deadline:
                resp = client.get(f"/api/devices/{FAKE_SERIAL}/queue")
                assert resp.status_code == 200
                queue = resp.json()
                if queue:
                    break
                time.sleep(0.5)

            assert queue, (
                "无头启动时自动任务未触发: 队列始终为空。"
                "修复前自动任务只在 GET /api/devices 前端轮询中触发，不开网页则不执行。"
            )
            names = [t["script_name"] for t in queue]
            assert names == AUTO_TASKS, f"自动任务脚本不符: {names}"
    finally:
        # 释放生命周期资源后清理（best-effort）
        shutil.rmtree(test_dir, ignore_errors=True)
