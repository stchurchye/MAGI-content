#!/bin/bash
set -e

cd "$(dirname "$0")"

echo "> MAGI-CONTENT starting..."

# 检查 ffmpeg
if ! command -v ffmpeg &> /dev/null; then
    echo "[ERROR] ffmpeg 未安装，请运行: brew install ffmpeg"
    exit 1
fi

# 检查 .env
if [ ! -f .env ]; then
    echo "[WARN] .env 文件不存在，从 .env.example 复制..."
    cp .env.example .env
    echo "请编辑 .env 填入 API Key 后重新运行"
    exit 1
fi

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] python3 未找到"
    exit 1
fi

# 安装依赖（如果需要）
if [ ! -d ".venv" ]; then
    echo "> 创建虚拟环境..."
    python3 -m venv .venv
fi

source .venv/bin/activate

if ! python3 -c "import fastapi" 2>/dev/null; then
    echo "> 安装依赖..."
    pip install -r requirements.txt
fi

echo "> 启动服务: http://127.0.0.1:8080"
uvicorn app.main:app --host 127.0.0.1 --port 8080 --reload
