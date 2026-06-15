"""小红书 Cookie 解析：环境变量 → 本地文件 → 浏览器自动读取。"""
from __future__ import annotations

import logging
import os
import sys

from app.config import Config

_log = logging.getLogger("app.xhs_cookie")

_XHS_DOMAINS = ("xiaohongshu.com", "www.xiaohongshu.com", ".xiaohongshu.com")

# 小红书登录态相关的 Cookie 关键字段：
# - web_session: 登录会话令牌，是否处于登录态的核心标志
# - a1 / webId: 设备指纹字段，登录后通常成对出现
_XHS_SESSION_KEY = "web_session"
_XHS_DEVICE_KEYS = ("a1", "webId")
# 经验阈值：有效的小红书 Cookie（含登录态）通常远超此长度，过短基本可判定无效
_XHS_COOKIE_MIN_LEN = 64


def _cookie_field_names(cookie: str) -> set[str]:
    """从 Cookie 字符串中提取出现过的字段名集合（仅解析，不发网络请求）。

    兼容两种常见格式：
    - HTTP 请求头风格: "a1=xxx; web_session=yyy; webId=zzz"
    - 仅以分号或换行分隔的键值对
    """
    names: set[str] = set()
    # 同时按分号与换行切分，逐段取 "=" 左侧作为字段名
    for chunk in cookie.replace("\n", ";").split(";"):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        name = chunk.split("=", 1)[0].strip()
        if name:
            names.add(name)
    return names


def validate_xhs_cookie(cookie: str) -> tuple[bool, str]:
    """对小红书 Cookie 做轻量结构性校验（纯函数，不发任何网络请求）。

    返回 (ok, reason)：ok 为 True 时 reason 为 "ok"；
    ok 为 False 时 reason 给出可读的失效原因，供调用方记录预警日志。

    注意：本函数只能判定 "结构上不像有效登录态"，无法保证服务端仍认可该 Cookie；
    真正的过期仍可能在下载时才暴露。其价值在于尽早拦截空 / 残缺 / 未登录的 Cookie。
    """
    if not cookie or not cookie.strip():
        return False, "Cookie 为空"

    cookie = cookie.strip()
    if len(cookie) < _XHS_COOKIE_MIN_LEN:
        return False, f"Cookie 长度过短（{len(cookie)} < {_XHS_COOKIE_MIN_LEN}），疑似不完整"

    names = _cookie_field_names(cookie)
    if not names:
        return False, "无法解析出任何 Cookie 字段（格式异常）"

    if _XHS_SESSION_KEY not in names:
        return False, f"缺少登录态字段 {_XHS_SESSION_KEY}，可能未登录或已退出"

    if not any(k in names for k in _XHS_DEVICE_KEYS):
        return False, f"缺少设备指纹字段 {'/'.join(_XHS_DEVICE_KEYS)}，疑似 Cookie 不完整"

    return True, "ok"


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


def _warn_if_invalid(cookie: str, *, source: str) -> bool:
    """对已解析到的 Cookie 做结构性校验，失效时输出明确的预警日志。

    返回 ok（是否通过校验），便于调用方按需进一步处理；当前调用方仅用于预警，
    不阻断流程。
    """
    ok, reason = validate_xhs_cookie(cookie)
    if not ok:
        _log.warning("Cookie 可能已失效: %s | 来源=%s", reason, source)
    return ok


def resolve_xhs_cookie(cfg: Config) -> str:
    """
    解析小红书 Cookie，优先级：
    1. XHS_COOKIE 环境变量
    2. XHS_COOKIE_FILE / data/xhs_cookie.txt
    3. 本机从浏览器读取——仅当显式设置 XHS_COOKIE_FROM_BROWSER / COOKIES_FROM_BROWSER 时
       才进行（隐私默认：不自动读取本机浏览器登录态，避免“没配 Cookie 却带上你的小红书账号”）。

    注意：手动同步脚本 sync_xhs_cookie() 是另一条显式路径，仍按 _default_browser() 默认 chrome。
    """
    if cfg.xhs_cookie:
        cookie = cfg.xhs_cookie.strip()
        _warn_if_invalid(cookie, source="XHS_COOKIE 环境变量")
        return cookie

    from_file = _read_cookie_file(cfg.xhs_cookie_file)
    if from_file:
        _warn_if_invalid(from_file, source=f"文件 {cfg.xhs_cookie_file}")
        return from_file

    # 浏览器读取改为显式 opt-in：cfg.xhs_cookie_from_browser 为空（未设
    # XHS_COOKIE_FROM_BROWSER / COOKIES_FROM_BROWSER）时直接跳过，不再隐式默认读 chrome。
    browser = cfg.xhs_cookie_from_browser
    if not browser:
        _log.info(
            "未配置 XHS_COOKIE / Cookie 文件，且未显式开启浏览器读取，按隐私默认跳过自动读取"
            "（如需用本机浏览器登录态，设 XHS_COOKIE_FROM_BROWSER=chrome，或跑 ./scripts/sync-xhs-cookie.sh）"
        )
        return ""

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
    else:
        _warn_if_invalid(cookie, source=f"浏览器 {browser}")
    return cookie
