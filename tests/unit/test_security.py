"""
API 鉴权中间件单元测试。

只 import app.core.auth；用 starlette 迷你 FastAPI + add_middleware + TestClient 验证。
不 import app.main，避免副作用。
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.auth import AuthMiddleware
from app.schemas.device import DeviceConnectRequest, DevicePairRequest


def _make_app(auth_token: str = ""):
    app = FastAPI()

    @app.get("/api/echo")
    async def echo():
        return {"ok": True}

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.get("/static/ping")
    async def static_ping():
        return {"ok": True}

    app.add_middleware(AuthMiddleware, auth_token=auth_token)
    return app


def test_empty_token_allows_everything():
    """token 为空时全放行（默认关闭）。"""
    app = _make_app("")
    client = TestClient(app)
    r = client.get("/api/echo")
    assert r.status_code == 200
    # 也验证带错误 X-Auth-Token 仍放行（因为未启用）
    r2 = client.get("/api/echo", headers={"X-Auth-Token": "whatever"})
    assert r2.status_code == 200


def test_auth_required_when_token_set():
    """token 非空时 /api/ 路径需正确头。"""
    app = _make_app("secret-token")
    client = TestClient(app)

    # 无头 -> 401
    r = client.get("/api/echo")
    assert r.status_code == 401

    # 错误头 -> 401
    r = client.get("/api/echo", headers={"X-Auth-Token": "wrong"})
    assert r.status_code == 401

    # 正确头 -> 200
    r = client.get("/api/echo", headers={"X-Auth-Token": "secret-token"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_health_exempt_when_token_set():
    """启用鉴权时 /api/health 豁免，无需 token。"""
    app = _make_app("secret-token")
    client = TestClient(app)
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_non_api_path_allowed_when_token_set():
    """启用鉴权时非 /api/ 路径放行。"""
    app = _make_app("secret-token")
    client = TestClient(app)
    r = client.get("/static/ping")
    assert r.status_code == 200


def test_401_body_is_json_detail():
    app = _make_app("secret-token")
    client = TestClient(app)
    r = client.get("/api/echo")
    assert r.status_code == 401
    assert r.json() == {"detail": "Unauthorized"}


# ---------------------------------------------------------------------------
# 输入校验（schemas/device.py）
# ---------------------------------------------------------------------------
def test_device_connect_address_valid():
    m = DeviceConnectRequest(address="192.168.1.100:5555")
    assert m.address == "192.168.1.100:5555"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "192.168.1.100",        # 缺端口
        "192.168.1.100:70000",  # 端口越界 65535
        "192.168.1.100:0",      # 端口为 0
        "256.1.1.1:5555",       # 段 > 255
        "300.1.1.1:5555",       # 段 > 255
        "a.b.c.d:5555",         # 非数字
        "192.168.1.100:abc",    # 端口非数字
        "192.168.1.100:5",      # 端口仅 1 位（正则要求 \d{2,5}）
        "192.168.1.100:555555", # 超过 5 位
    ],
)
def test_device_connect_address_invalid(bad):
    with pytest.raises(ValidationError) as exc:
        DeviceConnectRequest(address=bad)
    # 校验失败信息含"地址格式必须为 IPv4:port"
    assert "IPv4" in str(exc.value)


def test_device_pair_address_valid_port_ok():
    m = DevicePairRequest(address="192.168.1.100:41339", code="123456")
    assert m.code == "123456"


@pytest.mark.parametrize(
    "code",
    ["12345", "1234567", "abcdef", "12 456", "123456a", "00000"],
)
def test_device_pair_code_invalid(code):
    with pytest.raises(ValidationError) as exc:
        DevicePairRequest(address="192.168.1.100:41339", code=code)
    assert "6" in str(exc.value)


def test_device_pair_code_valid():
    m = DevicePairRequest(address="192.168.1.100:41339", code="000000")
    assert m.code == "000000"
