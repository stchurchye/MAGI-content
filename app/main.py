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

        class TokenAuthMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next):
                path = request.url.path
                if path in ("/health", "/static") or path.startswith("/static/"):
                    return await call_next(request)

                token = config.auth_token
                auth_header = request.headers.get("authorization", "")
                query_token = request.query_params.get("token", "")
                cookie_token = request.cookies.get("auth_token", "")

                if (auth_header == f"Bearer {token}" or
                        query_token == token or
                        cookie_token == token):
                    return await call_next(request)

                if request.headers.get("accept", "").startswith("text/html"):
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "Unauthorized. Pass ?token=<AUTH_TOKEN> to authenticate."},
                    )
                return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

        app.add_middleware(TokenAuthMiddleware)
        logger.info("Token auth enabled")

    # 注册路由
    from app.routes.api import router as api_router
    app.include_router(api_router)

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    cfg = get_config()
    uvicorn.run("app.main:app", host=cfg.host, port=cfg.port, reload=True)
