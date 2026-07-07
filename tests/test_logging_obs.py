"""可观测性:统一 JSON 日志 + 请求中间件 + 跨服务关联 ID。

纯 Python 即可运行(不依赖真服务):JsonFormatter/Filter 字段契约,以及 RequestLogMiddleware
经极简 Starlette app 验 X-Request-Id 生成/回显与 ContextVar 传到深层路由(BaseHTTPMiddleware 坑点)。
"""
import io
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.logging_context import request_id_var
from app.logging_setup import JsonFormatter, RequestContextFilter
from app.request_log_middleware import RequestLogMiddleware


def _make_logger():
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RequestContextFilter())
    lg = logging.getLogger(f"obs_test_{id(buf)}")
    lg.handlers[:] = [handler]
    lg.setLevel(logging.DEBUG)
    lg.propagate = False
    return lg, buf


def test_json_formatter_contract_and_service_name():
    lg, buf = _make_logger()
    lg.info("启动完成")
    entry = json.loads(buf.getvalue())
    assert entry["service"] == "magi-content"
    assert entry["level"] == "info"
    assert entry["message"] == "启动完成"
    assert entry["ts"].endswith("Z")


def test_request_id_and_business_fields():
    lg, buf = _make_logger()
    token = request_id_var.set("rid-mc")
    try:
        lg.warning("URL 被拒", extra={"event": "ssrf_blocked", "status": 400, "host": "10.0.0.1"})
    finally:
        request_id_var.reset(token)
    entry = json.loads(buf.getvalue())
    assert entry["request_id"] == "rid-mc"
    assert entry["event"] == "ssrf_blocked"
    assert entry["status"] == 400
    assert entry["host"] == "10.0.0.1"


def test_chinese_not_escaped_and_stack_on_exc():
    lg, buf = _make_logger()
    try:
        raise RuntimeError("下载器崩了")
    except RuntimeError as exc:
        lg.error("未捕获异常", exc_info=exc, extra={"event": "unhandled_exception"})
    out = buf.getvalue()
    assert "下载器崩了" in out  # ensure_ascii=False
    assert "RuntimeError: 下载器崩了" in json.loads(out)["stack"]


def _build_app():
    async def ok(request):
        return JSONResponse({"rid": request_id_var.get()})

    app = Starlette(routes=[Route("/ok", ok)])
    app.add_middleware(RequestLogMiddleware)
    return app


def test_middleware_generates_and_echoes_request_id():
    client = TestClient(_build_app())
    assert client.get("/ok").headers.get("X-Request-Id")
    r = client.get("/ok", headers={"X-Request-Id": "abc-mc"})
    assert r.headers["X-Request-Id"] == "abc-mc"


def test_request_id_propagates_to_route_via_contextvar():
    r = TestClient(_build_app()).get("/ok", headers={"X-Request-Id": "abc-mc"})
    assert r.json()["rid"] == "abc-mc"
