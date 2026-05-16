"""小红书下载封装，基于 XHS-Downloader。"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Callable

from app.services.ocr import list_image_files
from app.ui_copy import ProgressMsg

# 将 xhs-downloader 目录加入 sys.path
_xhs_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "xhs-downloader",
)
if _xhs_path not in sys.path:
    sys.path.insert(0, _xhs_path)

_VIDEO_EXTS = {".mp4", ".mov", ".m4a", ".webm", ".mkv"}


def _collect_videos(base_dir: str) -> list[str]:
    found: list[str] = []
    if not os.path.isdir(base_dir):
        return found
    for root, _, filenames in os.walk(base_dir):
        for fn in filenames:
            if fn.startswith("."):
                continue
            path = os.path.join(root, fn)
            if os.path.splitext(fn)[1].lower() in _VIDEO_EXTS:
                found.append(path)
    found.sort(key=lambda p: os.path.getsize(p) if os.path.isfile(p) else 0, reverse=True)
    return found


def download_xhs(
    url: str,
    output_dir: str,
    cookie: str = "",
    logger: logging.Logger | None = None,
    progress_cb: Callable[[int, str], None] | None = None,
) -> dict:
    """下载小红书图文/视频。返回 {images_dir?, video_path?, title}。"""
    from source.application import XHS

    log = logger or logging.getLogger(__name__)

    async def _run():
        images_dir = os.path.join(output_dir, "images")
        os.makedirs(images_dir, exist_ok=True)

        kwargs = dict(
            work_path=output_dir,
            folder_name="images",
            image_download=True,
            video_download=True,
            live_download=False,
            record_data=False,
            download_record=False,
            timeout=30,
            max_retry=3,
        )
        if cookie:
            kwargs["cookie"] = cookie
        elif log:
            log.warning("小红书 Cookie 为空，下载可能失败")

        async with XHS(**kwargs) as xhs:
            if progress_cb:
                progress_cb(20, ProgressMsg.XHS_FETCH)

            try:
                result = await xhs.extract(url, download=True)
            except Exception as e:
                log.error("XHS extract failed: %s", e)
                raise RuntimeError(f"小红书下载失败: {e}") from e

            if progress_cb:
                progress_cb(100, ProgressMsg.XHS_DONE)

            title = ""
            if isinstance(result, dict):
                title = result.get("title", "") or result.get("note_title", "") or ""
            elif isinstance(result, list) and result:
                title = result[0].get("title", "") or result[0].get("note_title", "") or ""

            # 在整棵任务目录下扫描（文件可能在 images/标题名/ 子目录）
            image_files = list_image_files(output_dir)
            video_files = _collect_videos(output_dir)

            log.info(
                "XHS done | title=%s images=%d videos=%d",
                title, len(image_files), len(video_files),
            )

            if not image_files and not video_files:
                if cookie:
                    raise RuntimeError(
                        "小红书未下载到任何媒体文件。xhs_cookie.txt 已存在但可能已过期或未包含登录态，"
                        "请在首页「粘贴 Cookie」重新保存，或在 Mac 执行 ./scripts/sync-xhs-cookie.sh "
                        "（需浏览器已登录 xiaohongshu.com）"
                    )
                raise RuntimeError(
                    "小红书未下载到任何媒体文件。请先在页面「粘贴 Cookie」或执行 "
                    "./scripts/sync-xhs-cookie.sh，并确认浏览器已登录 xiaohongshu.com"
                )

            out: dict = {
                "title": title or "Unknown",
                "duration_sec": None,
            }
            if image_files:
                out["images_dir"] = images_dir
            if video_files:
                out["video_path"] = video_files[0]
            return out

    return asyncio.run(_run())
