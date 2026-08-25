"""
API 鉴权中间件。

默认关闭（auth_token 为空时全部放行），不影响现有前端。
只依赖 starlette，不 import 任何 app 模块，因此单独 import 本文件无副作用。
"""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


# /api/health 豁免：即使启用鉴权，健康检查也无需 token（供负载均衡/探活使用）
_AUTH_EXEMPT_PATHS = {"/api/health"}


class AuthMiddleware(BaseHTTPMiddleware):
    """基于固定 token 的简单 API 鉴权中间件。

    - auth_token 为空：全部放行（默认关闭）。
    - auth_token 非空：对以 /api/ 开头的路径校验请求头 ``X-Auth-Token``；
      同时豁免 ``/api/health``；其余路径全部放行。
    """

    def __init__(self, app, auth_token: str = ""):
        super().__init__(app)
        self.auth_token = auth_token

    async def dispatch(self, request: Request, call_next):
        token = (self.auth_token or "").strip()
        if token and request.url.path.startswith("/api/"):
            if request.url.path in _AUTH_EXEMPT_PATHS:
                return await call_next(request)
            if request.headers.get("X-Auth-Token", "") != token:
                return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
        return await call_next(request)
