"""小红书 Cookie 解析：环境变量 → 本地文件 → 浏览器自动读取。"""
from __future__ import annotations

import logging
import os
import sys

from app.config import Config

_log = logging.getLogger("app.xhs_cookie")

_XHS_DOMAINS = ("xiaohongshu.com", "www.xiaohongshu.com", ".xiaohongshu.com")


def _in_docker() -> bool:
    return os.path.exists("/.dockerenv")


def _default_browser() -> str:
    if sys.platform == "darwin":
        return os.environ.get("XHS_COOKIE_FROM_BROWSER") or os.environ.get(
            "COOKIES_FROM_BROWSER", "chrome"
        )
    return os.environ.get("XHS_COOKIE_FROM_BROWSER") or os.environ.get(
        "COOKIES_FROM_BROWSER", "chrome"
    )


def _read_cookie_file(path: str) -> str:
    if not path or not os.path.isfile(path):
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError as e:
        _log.warning("读取小红书 Cookie 文件失败 | path=%s err=%s", path, e)
        return ""


def _read_from_browser(browser: str) -> str:
    if not browser:
        return ""
    xhs_root = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "xhs-downloader",
    )
    if xhs_root not in sys.path:
        sys.path.insert(0, xhs_root)
    try:
        from source.expansion.browser import BrowserCookie
    except ImportError as e:
        _log.warning("浏览器 Cookie 模块不可用（需安装 rookiepy）: %s", e)
        return ""

    cookie = BrowserCookie.get(browser, list(_XHS_DOMAINS))
    if cookie:
        _log.info("已从浏览器 %s 读取小红书 Cookie", browser)
    return cookie or ""


def resolve_xhs_cookie(cfg: Config) -> str:
    """
    解析小红书 Cookie，优先级：
    1. XHS_COOKIE 环境变量
    2. XHS_COOKIE_FILE / data/xhs_cookie.txt
    3. 本机从浏览器读取（Chrome/Safari 等，Docker 内通常不可用）
    """
    if cfg.xhs_cookie:
        return cfg.xhs_cookie.strip()

    from_file = _read_cookie_file(cfg.xhs_cookie_file)
    if from_file:
        return from_file

    browser = cfg.xhs_cookie_from_browser or _default_browser()
    if _in_docker():
        _log.warning(
            "Docker 内无法直接读取宿主机浏览器 Cookie；"
            "请在 Mac 上运行: ./scripts/sync-xhs-cookie.sh "
            "或设置 XHS_COOKIE / 挂载 data/xhs_cookie.txt"
        )
        return ""

    cookie = _read_from_browser(browser)
    if not cookie:
        _log.warning(
            "未获取到小红书 Cookie | browser=%s | "
            "请先在浏览器登录 xiaohongshu.com，或设置 XHS_COOKIE",
            browser,
        )
    return cookie
