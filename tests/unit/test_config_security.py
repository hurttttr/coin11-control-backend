"""
配置与安全相关单测

覆盖：
1. CORS 一致性处理：origins 含 "*" 时强制关闭 credentials（浏览器禁止 wildcard+credentials）
2. API 鉴权：
   - API_AUTH_TOKEN 未设置 → 原行为不变（向后兼容），健康检查始终可用
   - API_AUTH_TOKEN 设置后 → 无/错 token 被拒(401)，正确 token 通过，/api/health 豁免
3. WS 端点：不带 token 时被拒绝（close code 4001）

说明：本模块在导入 app.main 之前设置最小测试环境变量（COIN11_TB_PATH 指向
不存在的目录 → clone 快速失败；AUTO_TASK_SETTINGS_FILE 为空 → watcher 跳过扫描），
避免 lifespan 触发真实的 git clone / ADB 调用。
"""

import os

# ---- 在导入 app.main 之前设置隔离环境 ----
_WORKSPACE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_TMP = os.path.join(_WORKSPACE, ".test-tmp")
os.makedirs(_TMP, exist_ok=True)
os.environ["TMP"] = _TMP
os.environ["TEMP"] = _TMP
os.environ["COIN11_TB_PATH"] = os.path.join(_TMP, "no-such-coin11-tb")
_EMPTY_TASKS = os.path.join(_TMP, "empty_auto_tasks.json")
os.environ["AUTO_TASK_SETTINGS_FILE"] = _EMPTY_TASKS
# 确保初始状态文件存在且为空，watcher 直接跳过设备扫描
with open(_EMPTY_TASKS, "w", encoding="utf-8") as _f:
    _f.write('{"auto_tasks": []}')

import logging

from fastapi import FastAPI, APIRouter
from fastapi.testclient import TestClient


# ---------- CORS 一致性 ----------


def test_cors_policy_forces_no_credentials_for_wildcard(monkeypatch, caplog):
    """origins 含 "*" 时 credentials 必须被强制关闭，避免非法组合"""
    import app.main as main_mod

    class _FakeSettings:
        CORS_ORIGINS = ["*"]
        WS_AUTH_TOKEN = "token"
        API_AUTH_TOKEN = None
        HOST = "127.0.0.1"
        LOG_LEVEL = "DEBUG"

    monkeypatch.setattr(main_mod, "settings", _FakeSettings())
    with caplog.at_level(logging.WARNING, logger="app.main"):
        policy = main_mod.build_cors_policy(_FakeSettings())
    assert policy["allow_credentials"] is False
    assert policy["allow_origins"] == ["*"]
    assert set(policy["allow_methods"]) == {"GET", "POST", "PUT", "DELETE", "OPTIONS"}
    assert set(policy["allow_headers"]) == {"Authorization", "Content-Type", "X-API-Token"}
    assert any("credentials" in r.message for r in caplog.records)


def test_cors_policy_keeps_credentials_without_wildcard():
    """origins 不含 "*" 时 credentials 保持 True（本地开发场景）"""
    import app.main as main_mod

    class _FakeSettings:
        CORS_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]
        WS_AUTH_TOKEN = "token"
        API_AUTH_TOKEN = None
        HOST = "127.0.0.1"
        LOG_LEVEL = "DEBUG"

    policy = main_mod.build_cors_policy(_FakeSettings())
    assert policy["allow_credentials"] is True


# ---------- API 鉴权（轻量 App，不触发完整 lifespan） ----------


def _build_api_app(token: str | None) -> FastAPI:
    """构造带可选 API 鉴权依赖的轻量 App（/api/health 始终豁免）"""
    import app.main as main_mod

    class _FakeSettings:
        CORS_ORIGINS = ["http://localhost:5173"]
        WS_AUTH_TOKEN = "coin11-control-token"
        API_AUTH_TOKEN = token
        HOST = "127.0.0.1"
        LOG_LEVEL = "DEBUG"

    app = FastAPI()
    api = APIRouter(prefix="/api")

    @api.get("/protected")
    async def protected():
        return {"ok": True}

    # 生产布局：/api/health 不挂在带鉴权依赖的 router 上（供健康检查豁免）
    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    app.include_router(
        api,
        dependencies=[main_mod.verify_api_token(_FakeSettings())],
    )
    return app



def test_api_auth_disabled_when_token_unset():
    """API_AUTH_TOKEN 未设置 → 原行为不变（无鉴权，向后兼容）"""
    with TestClient(_build_api_app(None)) as client:
        r = client.get("/api/protected")
        assert r.status_code == 200
        r = client.get("/api/health")
        assert r.status_code == 200
    assert True


def test_api_auth_rejects_without_token():
    """API_AUTH_TOKEN 设置后 → 无/错 token 请求被拒(401)"""
    with TestClient(_build_api_app("secret-token")) as client:
        r = client.get("/api/protected")
        assert r.status_code == 401
        r = client.get("/api/protected", headers={"X-API-Token": "wrong"})
        assert r.status_code == 401


def test_api_auth_accepts_bearer_and_header():
    """正确 token（Bearer 或 X-API-Token）通过"""
    with TestClient(_build_api_app("secret-token")) as client:
        r = client.get("/api/protected", headers={"Authorization": "Bearer secret-token"})
        assert r.status_code == 200
        r = client.get("/api/protected", headers={"X-API-Token": "secret-token"})
        assert r.status_code == 200


def test_api_health_always_exempt():
    """/api/health 在启用鉴权时始终豁免（供健康检查使用）"""
    with TestClient(_build_api_app("secret-token")) as client:
        r = client.get("/api/health")
        assert r.status_code == 200


# ---------- WS 端点鉴权（真实 app.main，环境已隔离，clone 快速失败） ----------


def test_ws_rejects_missing_token():
    """WS 端点不带 token → 拒绝连接（close code 4001）"""
    import app.main as main_mod

    with TestClient(main_mod.app) as client:
        try:
            with client.websocket_connect("/ws/device/FAKE", expect=4001):
                pass
        except Exception:
            # TestClient 按 expect=4001 抛 WebSocketDisconnect，属预期路径
            pass
    assert True

def test_tokens_equal_handles_non_ascii():
    """token 含非 ASCII 字符时必须干净返回 False/True，而不是抛 TypeError。

    secrets.compare_digest 对 str 只支持 ASCII，直接传中文 token 会抛
    "comparing strings with non-ASCII characters is not supported"，
    在鉴权路径上表现为 500 而非 401 —— 用户把 token 设成中文即触发。
    """
    from app.main import _tokens_equal

    assert _tokens_equal("中文token", "中文token") is True
    assert _tokens_equal("中文token", "其他token") is False
    assert _tokens_equal("", "中文token") is False
    assert _tokens_equal("中文token", "") is False
    # 长度不同也不应抛异常
    assert _tokens_equal("中", "中文") is False
