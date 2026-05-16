"""Cookie 状态查询与同步（按平台独立）。"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from app.config import Config
from app.services.xhs_cookie import (
    _default_browser,
    _in_docker,
    _read_cookie_file,
    _read_from_browser,
)

_log = logging.getLogger("app.cookie_sync")


def write_xhs_cookie_file(path: str, cookie: str) -> None:
    cookie = cookie.strip()
    if not cookie:
        raise ValueError("Cookie 为空")
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(cookie)
    os.chmod(path, 0o600)


def _file_mtime_iso(path: str) -> str | None:
    if not os.path.isfile(path):
        return None
    ts = os.path.getmtime(path)
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def xhs_cookie_status(cfg: Config) -> dict:
    if cfg.xhs_cookie:
        return {
            "id": "xiaohongshu",
            "name": "小红书",
            "configured": True,
            "source": "env",
            "source_label": "环境变量 XHS_COOKIE",
            "updated_at": None,
            "can_sync_browser": False,
            "hint": "已通过环境变量配置；与 B站/YouTube 等平台 Cookie 不共用",
        }
    content = _read_cookie_file(cfg.xhs_cookie_file)
    if content:
        return {
            "id": "xiaohongshu",
            "name": "小红书",
            "configured": True,
            "source": "file",
            "source_label": os.path.basename(cfg.xhs_cookie_file),
            "updated_at": _file_mtime_iso(cfg.xhs_cookie_file),
            "can_sync_browser": not _in_docker(),
            "hint": "与 yt-dlp 平台 Cookie 不共用；失效后重新同步",
        }
    return {
        "id": "xiaohongshu",
        "name": "小红书",
        "configured": False,
        "source": "none",
        "source_label": "未配置",
        "updated_at": None,
        "can_sync_browser": not _in_docker(),
        "hint": (
            "请同步 Cookie。Docker 下可在宿主机运行 ./scripts/sync-xhs-cookie.sh，"
            "或在本页粘贴 Cookie"
            if _in_docker()
            else "请先在浏览器登录 xiaohongshu.com，再点击同步"
        ),
    }


def ytdlp_cookie_status(cfg: Config) -> dict:
    if cfg.cookies_file and os.path.isfile(cfg.cookies_file):
        return {
            "id": "ytdlp",
            "name": "B站 / YouTube / 抖音等",
            "configured": True,
            "source": "file",
            "source_label": os.path.basename(cfg.cookies_file),
            "updated_at": _file_mtime_iso(cfg.cookies_file),
            "can_sync_browser": False,
            "hint": "使用 Netscape cookies.txt；与小红书 Cookie 不共用",
        }
    if cfg.cookies_from_browser:
        return {
            "id": "ytdlp",
            "name": "B站 / YouTube / 抖音等",
            "configured": True,
            "source": "browser",
            "source_label": f"浏览器 · {cfg.cookies_from_browser}",
            "updated_at": None,
            "can_sync_browser": not _in_docker(),
            "hint": "下载时从浏览器读取；与小红书 Cookie 不共用",
        }
    return {
        "id": "ytdlp",
        "name": "B站 / YouTube / 抖音等",
        "configured": False,
        "source": "none",
        "source_label": "未配置（多数站点可不配）",
        "updated_at": None,
        "can_sync_browser": False,
        "hint": "可选：设置 COOKIES_FROM_BROWSER 或 COOKIES_FILE；与小红书不共用",
    }


def get_cookies_status(cfg: Config) -> dict:
    return {
        "shared": False,
        "message": "各平台 Cookie 相互独立，不共用",
        "in_docker": _in_docker(),
        "platforms": [xhs_cookie_status(cfg), ytdlp_cookie_status(cfg)],
    }


def sync_xhs_cookie(
    cfg: Config,
    *,
    browser: str | None = None,
    manual_cookie: str | None = None,
) -> dict:
    """同步小红书 Cookie 到本地文件（供 Docker 挂载使用）。"""
    if manual_cookie and manual_cookie.strip():
        write_xhs_cookie_file(cfg.xhs_cookie_file, manual_cookie)
        _log.info("小红书 Cookie 已写入（手动粘贴）| path=%s", cfg.xhs_cookie_file)
        return {
            "ok": True,
            "source": "paste",
            "message": "小红书 Cookie 已保存",
            "updated_at": _file_mtime_iso(cfg.xhs_cookie_file),
            "length": len(manual_cookie.strip()),
        }

    if _in_docker():
        raise ValueError(
            "容器内无法读取本机浏览器。请在 Mac 终端执行 ./scripts/sync-xhs-cookie.sh，"
            "或点击「粘贴 Cookie」"
        )

    name = browser or cfg.xhs_cookie_from_browser or _default_browser()
    cookie = _read_from_browser(name)
    if not cookie:
        raise ValueError(
            f"未能从 {name} 读取小红书 Cookie。请先在浏览器登录 xiaohongshu.com"
        )

    write_xhs_cookie_file(cfg.xhs_cookie_file, cookie)
    _log.info("小红书 Cookie 已从浏览器同步 | browser=%s", name)
    return {
        "ok": True,
        "source": "browser",
        "browser": name,
        "message": f"已从 {name} 同步小红书 Cookie",
        "updated_at": _file_mtime_iso(cfg.xhs_cookie_file),
        "length": len(cookie),
    }
