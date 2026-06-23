"""HTTP 请求日志中间件(最外层)。

职责:读入站 X-Request-Id(无则生成)→ 存 ContextVar + request.state → 回写响应头;
每个请求记一条结构化日志(method/path/status/耗时/client_ip);call_next 直接抛出时补一条带堆栈的 error。

无条件注册(与 TokenAuth 解耦),且在 TokenAuth 之后 add_middleware → 最外层,使 401 也能记日志、带 request_id。
"""
import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.logging_context import request_id_var

logger = logging.getLogger("request")


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


class RequestLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = rid
        token = request_id_var.set(rid)
        start = time.monotonic()
        try:
            response = await call_next(request)
            response.headers["X-Request-Id"] = rid
            logger.info(
                "请求",
                extra={
                    "event": "http_request",
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": int((time.monotonic() - start) * 1000),
                    "client_ip": _client_ip(request),
                },
            )
            return response
        except Exception:
            logger.exception(
                "请求处理未捕获异常",
                extra={
                    "event": "http_request_error",
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": int((time.monotonic() - start) * 1000),
                    "client_ip": _client_ip(request),
                },
            )
            raise
        finally:
            request_id_var.reset(token)
