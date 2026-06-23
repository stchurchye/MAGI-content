"""FastAPI 应用入口。"""
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# 确保项目根目录在 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import get_config


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    from app.services.pipeline import get_pipeline_manager
    get_pipeline_manager().shutdown(wait=True)


def create_app() -> FastAPI:
    config = get_config()

    # ---- 日志 ----
    # 统一结构化 JSON(stdout + logs/app.log),便于集中采集(Loki/Grafana)与跨服务 request_id 串联。
    from app.logging_setup import setup_logging
    setup_logging(config.logs_dir)
    logger = logging.getLogger("app")
    logger.info("Starting MAGI-CONTENT...")

    # 生产 fail-closed:ENVIRONMENT=production 必须配 AUTH_TOKEN(server-to-server 鉴权)。
    # 缺失即拒绝启动,杜绝"默认无认证 + 公网可达"的高危默认态。
    from app.services.startup_checks import assert_auth_config, warn_if_outbound_blocked
    assert_auth_config(config)

    # 启动期防呆:配了代理但不通时,这里就警告(否则等转写阶段才隐晦失败)。
    warn_if_outbound_blocked(config)

    # ---- FastAPI ----
    app = FastAPI(title="MAGI-CONTENT", version="0.2.0", lifespan=lifespan)

    # 静态文件
    static_dir = os.path.join(config.base_dir, "static")
    os.makedirs(static_dir, exist_ok=True)
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    # 挂载 config
    app.state.config = config
    app.state.logger = logger

    # 全局异常兜底:未预期异常记完整堆栈便于排障,客户端只回脱敏 500;
    # HTTP/校验异常交回 FastAPI 默认处理(各自 4xx,不记栈不刷 error 告警)。
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse as _JSONResponse

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request, exc):
        if isinstance(exc, (StarletteHTTPException, RequestValidationError)):
            raise exc
        logger.error("未捕获异常", exc_info=exc, extra={"event": "unhandled_exception"})
        return _JSONResponse(status_code=500, content={"detail": "服务器内部错误，请稍后重试"})

    # 可选认证中间件
    if config.auth_token:
        from fastapi import Request
        from fastapi.responses import JSONResponse
        from starlette.middleware.base import BaseHTTPMiddleware

        import hmac

        class TokenAuthMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next):
                path = request.url.path
                if path in ("/health", "/static") or path.startswith("/static/"):
                    return await call_next(request)

                token = config.auth_token
                auth_header = request.headers.get("authorization", "")
                # 仅接受 Authorization: Bearer 头与 cookie;去掉 ?token= 查询参数
                # (会进访问日志/Referer 泄露)。比较用 hmac.compare_digest 防时序侧信道。
                # 在 bytes 上比较:compare_digest 对含非 ASCII 的 str 会抛 TypeError→全站 500,
                # 编码成 utf-8 bytes 永不抛(容忍任意 token 取值)。
                token_b = token.encode("utf-8")
                presented = auth_header[7:] if auth_header.startswith("Bearer ") else ""
                cookie_token = request.cookies.get("auth_token", "")
                ok = (
                    (presented and hmac.compare_digest(presented.encode("utf-8"), token_b))
                    or (cookie_token and hmac.compare_digest(cookie_token.encode("utf-8"), token_b))
                )
                if ok:
                    return await call_next(request)

                return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

        app.add_middleware(TokenAuthMiddleware)
        logger.info("Token auth enabled")

    # 请求日志中间件:无条件注册(与鉴权解耦),且在 TokenAuth 之后 add → 最外层,
    # 使被 Auth 拒绝的 401 也能记一条请求日志并带上 request_id。
    from app.request_log_middleware import RequestLogMiddleware
    app.add_middleware(RequestLogMiddleware)

    # 完成回调 webhook（可选）：作业终态时 POST 通知外部接收端（如 agent）。
    if config.webhook_url:
        from app.services.pipeline import get_pipeline_manager
        from app.services.webhook import make_webhook_subscriber

        get_pipeline_manager().subscribe_to_all(
            make_webhook_subscriber(config.webhook_url, config.webhook_token)
        )
        logger.info("完成回调 webhook 已启用 → %s", config.webhook_url)

    # 注册路由
    from app.routes.api import router as api_router
    app.include_router(api_router)

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    cfg = get_config()
    uvicorn.run("app.main:app", host=cfg.host, port=cfg.port, reload=True)
