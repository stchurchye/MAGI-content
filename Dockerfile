# MAGI-CONTENT — 多平台内容流水线
# 构建方式与 magi-system/backend 对齐（代理 + 腾讯云镜像）
FROM python:3.12-slim

WORKDIR /app

ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY

# 先装系统依赖（走 Docker Desktop 注入的 host.docker.internal 代理）
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# 走代理用官方 PyPI（腾讯云镜像偶发 yt-dlp 包校验失败）
RUN pip install --no-cache-dir -r requirements.txt --timeout 300

# 小红书下载器运行时依赖
RUN pip install --no-cache-dir --timeout 300 \
    aiofiles \
    aiosqlite \
    "httpx[http2,socks]" \
    lxml \
    pyyaml \
    emoji \
    click \
    rich \
    pyperclip \
    websockets \
    rookiepy

COPY app ./app
COPY static ./static
COPY xhs-downloader ./xhs-downloader

RUN mkdir -p storage data logs

ENV HOST=0.0.0.0 \
    PORT=8080 \
    PYTHONUNBUFFERED=1

EXPOSE 8080

VOLUME ["/app/storage", "/app/data", "/app/logs"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
