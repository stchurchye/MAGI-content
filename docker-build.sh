#!/bin/bash
# 本地构建 MAGI-CONTENT 镜像（代理参数与 magi-system 一致）
set -e
cd "$(dirname "$0")"

IMAGE="${IMAGE:-magi-content:latest}"
PROXY="${DOCKER_PROXY:-http://host.docker.internal:7890}"

echo "> docker build -t ${IMAGE}"
echo "  HTTP_PROXY=${PROXY}"

docker build -t "${IMAGE}" \
  --build-arg "HTTP_PROXY=${PROXY}" \
  --build-arg "HTTPS_PROXY=${PROXY}" \
  "$@" \
  .

echo "> 完成。启动: docker compose up -d"
