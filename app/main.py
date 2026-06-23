"""
Coin11-TB Control API — FastAPI 应用入口
"""
import asyncio
import logging
import os
import subprocess as _subprocess
import sys
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

# Windows CMD GBK 兼容：强制 stdout 使用 UTF-8
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import router as v1_router
from app.core.config import get_settings
from app.services.repo_manager import RepoManager, repo_manager as global_repo_manager
from app.services.websocket_manager import ws_manager
from app.services.screen_capture import screen_capture
from app.services.task_engine import task_engine

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时
    print("=" * 50)
    print("  Coin11-TB Control API 启动中...")
    print(f"  内置路径: {settings.coin11_tb_path_resolved}")
    print(f"  ADB 路径: {settings.ADB_PATH}")
    print(f"  监听地址: {settings.HOST}:{settings.PORT}")
    print("=" * 50)

    # 检查 ADB 是否可用
    adb_available = await _check_adb()

    if not adb_available:
        print(f"  [WARN] ADB 未找到 — 设备管理功能将不可用")
    else:
        print(f"  [OK] ADB 可用")

    # 初始化 RepoManager 并自动 clone/更新 coin11-tb 仓库
    import app.services.repo_manager as rm

    coin11_tb_path = settings.coin11_tb_path_resolved
    repo_mgr = RepoManager(coin11_tb_path, settings.COIN11_TB_REPO_URL)
    rm.repo_manager = repo_mgr

    clone_ok = await repo_mgr.ensure_repo()
    if clone_ok:
        print(f"  [OK] coin11-tb 仓库就绪: {coin11_tb_path}")
    else:
        print(f"  [WARN] coin11-tb 仓库初始化失败: {repo_mgr.error_msg}")

    print(f"  [OK] Coin11-TB Control API 已启动")
    print("=" * 50)

    # 将检查结果存入 app.state
    app.state.adb_available = adb_available
    app.state.coin11_tb_ready = clone_ok

    yield

    # 关闭时: 清理所有截图流
    for serial in list(screen_capture.active_streams):
        await screen_capture.stop_stream(serial)
    print("Coin11-TB Control API 正在关闭...")
    print("资源已清理")


app = FastAPI(
    title="Coin11-TB Control API",
    description="Coin11-TB 网页控制平台后端 API",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(v1_router)


# ---------- WebSocket 端点 ----------


@app.websocket("/ws/device/{device_id}")
async def device_websocket(websocket: WebSocket, device_id: str, token: str = Query(default="coin11-control-token")):
    """
    设备实时 WebSocket 连接
    推送: screenshot (base64), log (文本), status (任务状态)
    鉴权: 需要在连接时附带 token 参数
    截图流在建立连接后自动启动，无需客户端发送 start_screencast
    """
    # 鉴权检查
    if token != settings.WS_AUTH_TOKEN:
        await websocket.close(code=4001, reason="Unauthorized: invalid token")
        return

    await ws_manager.connect(device_id, websocket)

    # 自动启动截图流（首次连接时启动）
    if device_id not in screen_capture.active_streams:
        await screen_capture.start_stream(
            device_id,
            callback=lambda img: ws_manager.send_screenshot(device_id, img),
            fps=2.0,
        )
    await websocket.send_text('{"type":"screencast","status":"started"}')

    try:
        # 保持连接，处理客户端控制指令
        while True:
            data = await websocket.receive_text()
            cmd = data.strip().lower()
            if cmd == "ping":
                await websocket.send_text('{"type":"pong"}')
            elif cmd == "start_screencast":
                if device_id not in screen_capture.active_streams:
                    await screen_capture.start_stream(
                        device_id,
                        callback=lambda img: ws_manager.send_screenshot(device_id, img),
                        fps=2.0,
                    )
                await websocket.send_text(
                    '{"type":"screencast","status":"started"}'
                )
            elif cmd == "stop_screencast":
                await screen_capture.stop_stream(device_id)
                await websocket.send_text(
                    '{"type":"screencast","status":"stopped"}'
                )
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await ws_manager.disconnect(device_id, websocket)
        # 如果没有其他客户端连接，停止截图流
        if not ws_manager.has_connections(device_id):
            await screen_capture.stop_stream(device_id)

        # 通知其他仍然连接的客户端（如果有的话）截图流已停止
        if ws_manager.has_connections(device_id):
            await ws_manager.broadcast(device_id, "screencast", "stopped")


# ---------- Health Check ----------


@app.get("/api/health")
async def health_check():
    """健康检查端点"""
    return {
        "status": "ok",
        "adb_available": getattr(app.state, "adb_available", False),
        "coin11_tb_ready": getattr(app.state, "coin11_tb_ready", False),
    }


async def _check_adb() -> bool:
    """检查 ADB 是否在 PATH 中可用"""
    try:
        result = await asyncio.to_thread(
            _subprocess.run,
            [settings.ADB_PATH, "version"],
            capture_output=True,
            timeout=5.0,
        )
        return b"Android Debug Bridge" in result.stdout
    except (FileNotFoundError, _subprocess.TimeoutExpired, Exception):
        return False


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=True,
    )
