"""
SPA 静态托管回退单测

验证 frontend-dist 挂载逻辑（SPAStaticFiles）：
1. 访问 /tasks（不存在的真实文件）→ 200，返回 index.html 内容（前端 history 路由刷新）
2. /api/health → JSON，不被 fallback 吞掉
3. /assets/nope.js（不存在的静态资源）→ 404（静态资源不能被 fallback 掩盖）

不依赖 app.main 的完整 lifespan：构造轻量 FastAPI 实例直接调用
safemount_frontend_dist()，避免 clone 仓库 / 启动 watcher 等重副作用。
"""

import os

_WORKSPACE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_TMP = os.path.join(_WORKSPACE, ".test-tmp")
os.makedirs(_TMP, exist_ok=True)


def _build_dist(dist: str):
    """在临时目录构造一个假的 frontend-dist：index.html + assets/app.js"""
    os.makedirs(os.path.join(dist, "assets"), exist_ok=True)
    with open(os.path.join(dist, "index.html"), "w", encoding="utf-8") as f:
        f.write("<html><body>SPA-INDEX</body></html>")
    with open(os.path.join(dist, "assets", "app.js"), "w", encoding="utf-8") as f:
        f.write("console.log('app');")


def test_spa_fallback_serves_index_for_deep_link():
    """不存在的路径（如 /tasks）→ 200 且内容是 index.html"""
    import shutil

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import app.main as main_mod

    dist = os.path.join(_TMP, f"spa_dist_{os.getpid()}")
    shutil.rmtree(dist, ignore_errors=True)
    _build_dist(dist)

    app = FastAPI()

    @app.get("/api/health")
    async def _health():
        return {"status": "ok"}

    try:
        mounted = main_mod.safemount_frontend_dist(app, dist)
        assert mounted is True
        with TestClient(app) as client:
            r = client.get("/tasks")
            assert r.status_code == 200
            assert r.text == "<html><body>SPA-INDEX</body></html>"
    finally:
        shutil.rmtree(dist, ignore_errors=True)


def test_spa_fallback_does_not_swallow_api():
    """/api/health 返回 JSON，不被 fallback 吞掉"""
    import shutil

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import app.main as main_mod

    dist = os.path.join(_TMP, f"spa_dist_{os.getpid()}")
    shutil.rmtree(dist, ignore_errors=True)
    _build_dist(dist)

    app = FastAPI()

    @app.get("/api/health")
    async def _health():
        return {"status": "ok"}

    # v1_router 的依赖注入与真实 app 实现在 main.py 内核对；这里等价模拟
    try:
        main_mod.safemount_frontend_dist(app, dist)
        with TestClient(app) as client:
            r = client.get("/api/health")
            assert r.status_code == 200
            assert r.json() == {"status": "ok"}
            assert "SPA-INDEX" not in r.text
    finally:
        shutil.rmtree(dist, ignore_errors=True)


def test_spa_fallback_404_for_missing_asset():
    """不存在的静态资源 /assets/nope.js → 404（不能回退到 index.html）"""
    import shutil

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import app.main as main_mod

    dist = os.path.join(_TMP, f"spa_dist_{os.getpid()}")
    shutil.rmtree(dist, ignore_errors=True)
    _build_dist(dist)

    app = FastAPI()
    try:
        main_mod.safemount_frontend_dist(app, dist)
        with TestClient(app) as client:
            r = client.get("/assets/app.js")
            assert r.status_code == 200
            assert r.text == "console.log('app');"
            r = client.get("/assets/nope.js")
            assert r.status_code == 404
    finally:
        shutil.rmtree(dist, ignore_errors=True)


def test_dev_mode_returns_false_without_dist():
    """frontend-dist 不存在时 safemount 返回 False（开发模式，不挂载）"""
    import shutil

    from fastapi import FastAPI

    import app.main as main_mod

    dist = os.path.join(_TMP, f"spa_dist_missing_{os.getpid()}")
    shutil.rmtree(dist, ignore_errors=True)

    app = FastAPI()
    mounted = main_mod.safemount_frontend_dist(app, dist)
    assert mounted is False

def test_spa_fallback_does_not_swallow_unknown_api_path():
    """未注册的 /api/* 路径必须保持 404 JSON，不能回退成 index.html。

    这是比"已注册端点不被吞掉"更关键的回归点：已注册路由天然先于 Mount 命中，
    真正的风险是【未命中】的 /api 路径落到根 Mount 上被回退成 200 + HTML。
    那会让前端的 response.ok 检查永不触发，转而对 HTML 执行 JSON.parse，
    把"端点不存在/拼错"伪装成 JSON 解析错误。
    """
    import shutil

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import app.main as main_mod

    dist = os.path.join(_TMP, f"spa_dist_unknown_{os.getpid()}")
    shutil.rmtree(dist, ignore_errors=True)
    _build_dist(dist)

    app = FastAPI()

    @app.get("/api/health")
    async def _health():
        return {"status": "ok"}

    try:
        main_mod.safemount_frontend_dist(app, dist)
        with TestClient(app) as client:
            for path in ("/api/does-not-exist", "/api/devices/X/queue/bogus"):
                r = client.get(path)
                assert r.status_code == 404, f"{path} 应 404，实际 {r.status_code}"
                assert "SPA-INDEX" not in r.text, f"{path} 被 SPA fallback 吞掉了"
                assert "json" in r.headers.get("content-type", "")
    finally:
        shutil.rmtree(dist, ignore_errors=True)


def test_spa_fallback_does_not_swallow_unknown_ws_path():
    """未注册的 /ws/* 路径同样保持 404，不回退成 index.html"""
    import shutil

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import app.main as main_mod

    dist = os.path.join(_TMP, f"spa_dist_ws_{os.getpid()}")
    shutil.rmtree(dist, ignore_errors=True)
    _build_dist(dist)

    app = FastAPI()
    try:
        main_mod.safemount_frontend_dist(app, dist)
        with TestClient(app) as client:
            r = client.get("/ws/bogus")
            assert r.status_code == 404
            assert "SPA-INDEX" not in r.text
    finally:
        shutil.rmtree(dist, ignore_errors=True)
