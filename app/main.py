"""
Coin11-TB Control API — FastAPI 应用入口
"""
import asyncio
import logging
import os
import secrets
import sys
from contextlib import asynccontextmanager
from functools import partial
from importlib import metadata
from typing import Optional

logger = logging.getLogger(__name__)

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import Settings, get_settings
from app.core.logging_config import setup_logging
from app.api.v1.router import router as v1_router
from app.services.repo_manager import RepoManager, repo_manager as global_repo_manager
from app.services.websocket_manager import ws_manager
from app.services.screen_capture import screen_capture
from app.services.task_engine import task_engine
from app.services.auto_task_runner import auto_task_watcher
from app.services.queue_control import SCREENCAST_FPS

settings = get_settings()


# 版本号：优先从已安装的包元数据读取（与 pyproject.toml 保持一致），读不到时回退常量
_VERSION_FALLBACK = "0.3.0"


def _resolve_version() -> str:
    try:
        return metadata.version("coin11-control-backend")
    except metadata.PackageNotFoundError:
        return _VERSION_FALLBACK


# ---------- API 鉴权（可选，默认关闭以保持向后兼容） ----------


def _tokens_equal(provided: str, expected: str) -> bool:
    """常量时间比较两个 token。

    secrets.compare_digest 对含非 ASCII 字符的 str 会抛 TypeError
    （comparing strings with non-ASCII characters is not supported）——
    用户把 token 设成中文时会变成 500 而不是干净地拒绝。先编码为 bytes 再比较。
    """
    if not provided or not expected:
        return False
    return secrets.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def verify_api_token(cfg: Optional[Settings] = None):
    """
    构造 API 鉴权依赖（供 include_router(dependencies=[...]) 使用）。

    返回 Depends 实例（FastAPI 的 dependencies=[...] 列表要求已包装的 Depends，
    而非裸函数）。用法：app.include_router(router, dependencies=[verify_api_token()])

    - cfg.API_AUTH_TOKEN 未设置（None/空）→ 不校验，原行为不变
    - 设置后 → 校验 Authorization: Bearer <token> 或 X-API-Token 请求头
      （secrets.compare_digest 常量时间比较），失败返回 401
    - /api/health 由调用方负责豁免（该路径不挂在带依赖的 router 上）
    """
    if cfg is None:
        cfg = settings

    def _require_token(request: Request) -> None:
        token = cfg.API_AUTH_TOKEN
        if not token:
            return  # 未启用鉴权，放行

        provided = ""
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            provided = auth_header[len("Bearer "):].strip()
        if not provided:
            provided = request.headers.get("X-API-Token", "")

        if not _tokens_equal(provided, token):
            raise HTTPException(
                status_code=401,
                detail="Unauthorized: 缺少或错误的 API Token",
            )

    return Depends(_require_token)


# ---------- CORS 一致性处理 ----------


def build_cors_policy(cfg: Optional[Settings] = None) -> dict:
    """
    计算 CORS 中间件参数。

    浏览器禁止 wildcard origin + credentials 组合；若 CORS_ORIGINS 含 "*"，
    强制关闭 allow_credentials 并告警，同时把 methods/headers 收敛到实际使用的集合。
    """
    if cfg is None:
        cfg = settings

    origins = list(cfg.CORS_ORIGINS or [])
    allow_credentials = True
    if "*" in origins:
        allow_credentials = False
        logger.warning(
            "CORS_ORIGINS 包含 \"*\"，已强制关闭 allow_credentials "
            "（浏览器禁止 wildcard + credentials 组合）"
        )

    return {
        "allow_origins": origins,
        "allow_credentials": allow_credentials,
        "allow_methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Authorization", "Content-Type", "X-API-Token"],
    }


# ---------- SPA 静态托管（frontend-dist） ----------


class SPAStaticFiles(StaticFiles):
    """
    SPA history 路由兼容的静态文件托管。

    与原生 StaticFiles(html=True) 的区别：当请求路径找不到真实文件时，
    回退到 index.html（而非 404），保证前端 createWebHistory 的深链接
    （/tasks、/settings、/device/xxx）刷新可用。

    回退规则：
    - 真实存在的静态资源（assets/*）正常返回
    - 访问目录根路径时优先找 index.html
    - 未命中的路径：
      * 位于静态资源目录（assets/、static/、favicon/ 等）下时返回 404
        （缺失的构建产物不应被 index.html 掩盖，否则浏览器缓存/刷新会拿到错内容）
      * 其余路径视为 SPA history 路由，回退 index.html
      （目录穿越由 Starlette 内部路径规范化保证）
    """

    # 静态资源目录前缀：这些目录下未命中的请求一律 404，不做 SPA 回退
    _STATIC_PREFIXES = ("assets/", "static/", "favicon/", "icons/")
    # 后端命名空间：未命中的 API/WS 路径必须保持 404 JSON，绝不能回退成 index.html。
    # 否则未知端点会返回 200 + HTML，前端的 response.ok 检查永不触发，
    # 转而对 HTML 执行 JSON.parse，把"端点不存在"伪装成解析错误，极难排查。
    _BACKEND_PREFIXES = ("api/", "ws/")

    async def get_response(self, path: str, scope: dict):
        # 注意：StaticFiles 抛出的是 starlette.exceptions.HTTPException（与 fastapi 的
        # HTTPException 是不同类，后者继承前者但反之不成立），必须用 Starlette 版本捕获。
        try:
            return await super().get_response(path, scope)
        except (StarletteHTTPException, OSError) as e:
            # 只有 404 才回退到 index.html，其它错误（401/405/路径非法等）原样抛出
            if isinstance(e, StarletteHTTPException) and e.status_code != 404:
                raise
            normalized = path.replace(os.sep, "/").lstrip("/")
            if normalized.startswith(self._STATIC_PREFIXES):
                raise  # 静态资源目录下未命中 → 保持 404
            if normalized.startswith(self._BACKEND_PREFIXES):
                raise  # 未知 API/WS 路径 → 保持 404，不伪装成 SPA 页面
            index_path = os.path.normpath(os.path.join(self.directory, "index.html"))
            if os.path.isfile(index_path):
                from starlette.responses import FileResponse

                return FileResponse(index_path)
            raise


def safemount_frontend_dist(
    app: FastAPI,
    frontend_dist: Optional[str] = None,
) -> bool:
    """
    挂载前端静态目录（存在时）。

    Args:
        app: 目标 FastAPI 应用
        frontend_dist: 前端构建产物目录；None 时按项目根目录下 frontend-dist 推断

    Returns:
        是否成功挂载（false = 开发模式，目录不存在）
    """
    if frontend_dist is None:
        frontend_dist = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "frontend-dist",
        )
    if os.path.isdir(frontend_dist):
        # 注意 mount 的 name 不能为 None/空：Starlette 对无名 Mount 只返回 PARTIAL 匹配，
        # 路由不会落进来；起一个合法名字可强制 FULL 匹配（spa-fallback 属合法标识符）。
        app.mount("/", SPAStaticFiles(directory=frontend_dist, html=True), name="spa-fallback")
        logger.info("生产模式: 前端静态文件已挂载 (%s)", frontend_dist)
        return True
    logger.info("开发模式: 前端静态文件未找到 (%s)", frontend_dist)
    return False


# ---------- 生命周期 ----------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 统一日志配置（幂等；从 LOG_LEVEL 环境变量读取级别）
    setup_logging(settings.LOG_LEVEL)

    logger.info("=" * 50)
    logger.info("  Coin11-TB Control API 启动中...")
    logger.info("  内置路径: %s", settings.coin11_tb_path_resolved)
    logger.info("  ADB 路径: %s", settings.ADB_PATH)
    logger.info("  监听地址: %s:%s", settings.HOST, settings.PORT)
    logger.info("=" * 50)

    # 安全检查：监听非回环地址且未设置 API 鉴权 Token 时明确告警
    if not settings.API_AUTH_TOKEN:
        host = (settings.HOST or "").lower()
        if host not in ("127.0.0.1", "localhost", "::1"):
            logger.warning(
                "监听地址 %s 非回环地址且 API_AUTH_TOKEN 未设置："
                "局域网内任何人均可访问 /api/* 控制端点，生产环境必须设置 API_AUTH_TOKEN",
                settings.HOST,
            )
        else:
            logger.info("API 鉴权未启用：仅本机回环地址监听，风险可控")

    # 检查 ADB 是否可用
    def check_adb():
        import subprocess as sp
        try:
            r = sp.run([settings.ADB_PATH, "version"], capture_output=True, text=True, timeout=5)
            return "Android Debug Bridge" in r.stdout
        except Exception:
            return False
    adb_ok = await asyncio.to_thread(check_adb)
    if adb_ok:
        logger.info("  [OK] ADB 可用: %s", settings.ADB_PATH)
    else:
        logger.warning("  [WARN] ADB 未找到 — 设备管理功能将不可用")

    # 初始化 RepoManager 并自动 clone/更新 coin11-tb 仓库
    import app.services.repo_manager as rm

    coin11_tb_path = settings.coin11_tb_path_resolved
    repo_mgr = RepoManager(coin11_tb_path, settings.COIN11_TB_REPO_URL)
    rm.repo_manager = repo_mgr

    clone_ok = await repo_mgr.ensure_repo()
    if clone_ok:
        logger.info("  [OK] coin11-tb 仓库就绪: %s", coin11_tb_path)
    else:
        logger.warning("  [WARN] coin11-tb 仓库初始化失败: %s", repo_mgr.error_msg)

    # WebSocket 默认 Token 告警（前端 submodule 硬编码了默认值，故保留默认配置）
    if settings.WS_AUTH_TOKEN == "coin11-control-token":
        logger.warning(
            "WS_AUTH_TOKEN 仍为默认值，生产环境必须通过 .env 覆盖为强随机值 "
            "(前端 websocket.ts 当前硬编码此默认值，后续应改为构建期环境变量注入)"
        )

    logger.info("  [OK] Coin11-TB Control API 已启动")
    logger.info("=" * 50)

    # 将检查结果存入 app.state
    app.state.adb_available = adb_ok
    app.state.coin11_tb_ready = clone_ok

    # 启动后台设备监视：设备上线自动触发任务，不依赖前端轮询/网页打开
    await auto_task_watcher.start()

    yield

    # 关闭时: 停止后台监视 + 清理所有截图流
    await auto_task_watcher.stop()
    for serial in list(screen_capture.active_streams):
        await screen_capture.stop_stream(serial)
    logger.info("Coin11-TB Control API 正在关闭...")
    logger.info("资源已清理")


app = FastAPI(
    title="Coin11-TB Control API",
    description="Coin11-TB 网页控制平台后端 API",
    version=_resolve_version(),
    lifespan=lifespan,
)

# CORS 配置（一致性处理见 build_cors_policy）
app.add_middleware(
    CORSMiddleware,
    **build_cors_policy(settings),
)

# 注册路由（可选 API 鉴权依赖注入；/api/health 单独注册于下方，天然豁免）
app.include_router(v1_router, dependencies=[verify_api_token(settings)])


# ---------- WebSocket 端点 ----------
# 注意：必须先于下方 "/" 静态挂载注册，否则生产模式（frontend-dist 存在）下
# 根路径 Mount 会匹配并吞掉 /ws/* 升级请求。


@app.websocket("/ws/device/{device_id}")
async def device_websocket(websocket: WebSocket, device_id: str, token: Optional[str] = Query(default=None)):
    """
    设备实时 WebSocket 连接
    推送: screenshot (base64), log (文本), status (任务状态)
    鉴权: 必须在连接时附带 ?token= 参数，缺失或错误即拒绝（close code 4001）
    截图流在建立连接后自动启动，无需客户端发送 start_screencast
    """
    # 鉴权检查（常量时间比较；token 缺失(None)恒不通过）
    if token is None or not _tokens_equal(token, settings.WS_AUTH_TOKEN):
        await websocket.close(code=4001, reason="Unauthorized: invalid token")
        return

    await ws_manager.connect(device_id, websocket)

    # 回放该设备队列的历史日志（自动任务可能在前端连接前已产生日志）
    # 回放消息带 task_id 标记，前端据此识别历史日志并去重
    try:
        replay = await task_engine.get_replay_logs(device_id)
        if replay:
            await ws_manager.send_replay(device_id, replay)
    except Exception as e:
        logger.warning("回放 %s 历史日志失败: %s", device_id, e)

    # 自动启动截图流（首次连接时启动）
    if device_id not in screen_capture.active_streams:
        await screen_capture.start_stream(
            device_id,
            callback=partial(ws_manager.send_screenshot, device_id),
            fps=SCREENCAST_FPS,
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
                        callback=partial(ws_manager.send_screenshot, device_id),
                        fps=SCREENCAST_FPS,
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
        logger.debug("设备 %s WebSocket 连接断开", device_id)
    except Exception:
        logger.debug("设备 %s WebSocket 处理异常", device_id, exc_info=True)
    finally:
        await ws_manager.disconnect(device_id, websocket)
        # 如果没有其他客户端连接，停止截图流
        if not ws_manager.has_connections(device_id):
            await screen_capture.stop_stream(device_id)

        # 通知其他仍然连接的客户端（如果有的话）截图流已停止
        if ws_manager.has_connections(device_id):
            await ws_manager.broadcast(device_id, "screencast", "stopped")


# ---------- Health Check ----------
# 单独注册（不挂在 v1_router 上），API 鉴权启用时始终豁免（供健康检查使用）


@app.get("/api/health")
async def health_check():
    """健康检查端点"""
    return {
        "status": "ok",
        "adb_available": getattr(app.state, "adb_available", False),
        "coin11_tb_ready": getattr(app.state, "coin11_tb_ready", False),
    }


# ---------- 生产模式: 托管前端静态文件 ----------
# 必须最后挂载：根路径 Mount 会匹配一切未命中的路径；/api 与 /ws 已先注册优先命中
safemount_frontend_dist(app)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=True,
    )
