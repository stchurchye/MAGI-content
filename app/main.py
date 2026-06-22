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
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                os.path.join(config.logs_dir, "app.log"), encoding="utf-8"
            ),
        ],
    )
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
                presented = auth_header[7:] if auth_header.startswith("Bearer ") else ""
                cookie_token = request.cookies.get("auth_token", "")
                ok = (
                    (presented and hmac.compare_digest(presented, token))
                    or (cookie_token and hmac.compare_digest(cookie_token, token))
                )
                if ok:
                    return await call_next(request)

                return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

        app.add_middleware(TokenAuthMiddleware)
        logger.info("Token auth enabled")

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
