#!/bin/bash
# 从本机浏览器读取小红书 Cookie，写入 data/xhs_cookie.txt（Docker 通过卷挂载使用）
set -e
cd "$(dirname "$0")/.."

BROWSER="${XHS_COOKIE_FROM_BROWSER:-${COOKIES_FROM_BROWSER:-chrome}}"
OUT="${XHS_COOKIE_FILE:-data/xhs_cookie.txt}"

PYTHON="python3"
if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
fi

echo "> 从浏览器读取小红书 Cookie (browser=${BROWSER})"
SYNC_BROWSER="$BROWSER" SYNC_OUT="$OUT" "$PYTHON" - <<'PY'
import os
import sys

root = os.path.abspath(".")
sys.path.insert(0, os.path.join(root, "xhs-downloader"))

browser = os.environ.get("SYNC_BROWSER", "chrome")
out = os.environ.get("SYNC_OUT", "data/xhs_cookie.txt")

from source.expansion.browser import BrowserCookie

cookie = BrowserCookie.get(
    browser,
    ["xiaohongshu.com", "www.xiaohongshu.com", ".xiaohongshu.com"],
)
if not cookie:
    print(
        "未读到 Cookie。请先在浏览器登录 https://www.xiaohongshu.com ，"
        "或指定浏览器，例如: XHS_COOKIE_FROM_BROWSER=safari ./scripts/sync-xhs-cookie.sh",
        file=sys.stderr,
    )
    sys.exit(1)

os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
with open(out, "w", encoding="utf-8") as f:
    f.write(cookie)
print(f"已写入 {out} ({len(cookie)} 字符)")
PY
