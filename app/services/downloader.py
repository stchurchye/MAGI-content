"""下载策略调度：yt-dlp / yutto / gallery-dl。"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from typing import Callable, Optional

import yt_dlp

from app.services.platform_detector import PlatformRule


def _build_ydl_opts(
    output_template: str,
    progress_hook: Callable,
    platform_key: str = "",
    cookies_file: str = "",
    cookies_from_browser: str = "",
    proxy: str = "",
) -> dict:
    opts = {
        "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
        "merge_output_format": "mp4",
        "outtmpl": output_template,
        "progress_hooks": [progress_hook],
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "retries": 3,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Referer": "https://www.bilibili.com/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    }

    if proxy:
        opts["proxy"] = proxy

    # Cookies（反爬）
    if cookies_file and os.path.exists(cookies_file):
        opts["cookiefile"] = cookies_file
    elif cookies_from_browser:
        opts["cookiesfrombrowser"] = (cookies_from_browser,)

    # B站专用反爬策略
    if platform_key == "bilibili":
        opts["extractor_args"] = {
            "bilibili": {"prefer_multi_flv": True}
        }
    elif platform_key == "youtube":
        opts["http_headers"]["Referer"] = "https://www.youtube.com/"
        opts.setdefault("extractor_args", {})["youtube"] = {
            "player_client": ["android", "web"],
        }

    return opts


def _find_tool(name: str) -> str:
    """查找命令行工具，优先 venv 内，再查系统 PATH。"""
    import sys
    venv_bin = os.path.join(os.path.dirname(sys.executable), name)
    if os.path.exists(venv_bin):
        return venv_bin
    tool = shutil.which(name)
    if tool:
        return tool
    raise RuntimeError(f"{name} 未安装，请运行: pip install {name}")


def _progress_wrapper(logger: logging.Logger, progress_cb: Callable[[int, str], None]):
    """返回 yt-dlp 进度钩子函数。"""
    def hook(d):
        if d["status"] == "downloading":
            pct_str = d.get("_percent_str", "0%").strip("%").strip()
            try:
                pct = min(int(float(pct_str)), 100)
            except (ValueError, TypeError):
                pct = 0
            speed = d.get("_speed_str", "?")
            progress_cb(pct, f"{pct}% · {speed}")
        elif d["status"] == "finished":
            progress_cb(100, "下载完成，处理中...")
    return hook


# ---------- yt-dlp ----------

def download_ytdlp(
    url: str,
    output_dir: str,
    job_id: str,
    logger: logging.Logger,
    progress_cb: Callable[[int, str], None],
    platform_key: str = "",
    cookies_file: str = "",
    cookies_from_browser: str = "",
    proxy: str = "",
) -> dict:
    """使用 yt-dlp 下载视频。返回 {video_path, title, duration_sec}。"""
    output_template = os.path.join(output_dir, f"{job_id}.%(ext)s")
    ydl_opts = _build_ydl_opts(
        output_template,
        _progress_wrapper(logger, progress_cb),
        platform_key,
        cookies_file,
        cookies_from_browser,
        proxy,
    )

    logger.info("yt-dlp starting | url=%s", url)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        # 获取实际输出路径
        video_path = ydl.prepare_filename(info)
        actual_ext = info.get("ext", "mp4")
        expected = os.path.join(output_dir, f"{job_id}.{actual_ext}")
        if os.path.exists(expected):
            video_path = expected
        # 也检查可能的 mp4 合并结果
        mp4_path = os.path.join(output_dir, f"{job_id}.mp4")
        if os.path.exists(mp4_path):
            video_path = mp4_path

        title = info.get("title", "Unknown")
        duration = info.get("duration")
        logger.info("yt-dlp done | title=%s duration=%s path=%s", title, duration, video_path)

        return {
            "video_path": video_path,
            "title": title,
            "duration_sec": duration,
        }


# ---------- yutto (B站专用) ----------

def _find_merged_video(output_dir: str) -> str | None:
    """查找已完成的视频文件（不含 .m4s 分段）。"""
    candidates: list[str] = []
    for f in os.listdir(output_dir):
        if f.startswith("."):
            continue
        lower = f.lower()
        if lower.endswith((".mp4", ".flv", ".mkv", ".webm")) and not lower.endswith(".m4s"):
            candidates.append(os.path.join(output_dir, f))
    if not candidates:
        return None
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return candidates[0]


def _merge_bilibili_m4s(output_dir: str, logger: logging.Logger) -> str | None:
    """将 yutto 下载的 *_video.m4s + *_audio.m4s 合并为 mp4。"""
    video_m4s = audio_m4s = None
    base_name = ""
    for f in os.listdir(output_dir):
        if f.endswith("_video.m4s"):
            video_m4s = os.path.join(output_dir, f)
            base_name = f[: -len("_video.m4s")]
        elif f.endswith("_audio.m4s"):
            audio_m4s = os.path.join(output_dir, f)
    if not video_m4s:
        return None

    out_mp4 = os.path.join(output_dir, f"{base_name}.mp4")
    if os.path.isfile(out_mp4) and os.path.getsize(out_mp4) > 0:
        return out_mp4

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        logger.warning("ffmpeg 未找到，无法合并 B站 m4s 分段")
        return None

    cmd = [ffmpeg, "-y", "-i", video_m4s]
    if audio_m4s:
        cmd.extend(["-i", audio_m4s, "-c", "copy", "-shortest"])
    else:
        cmd.extend(["-c", "copy"])
    cmd.extend(["-movflags", "+faststart", out_mp4])
    logger.info("ffmpeg merge m4s | cmd=%s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if proc.returncode != 0:
        logger.error("ffmpeg merge failed: %s", (proc.stderr or "")[-800:])
        return None
    if os.path.isfile(out_mp4) and os.path.getsize(out_mp4) > 0:
        logger.info("ffmpeg merge done | path=%s", out_mp4)
        return out_mp4
    return None


def download_yutto(
    url: str,
    output_dir: str,
    job_id: str,
    logger: logging.Logger,
    progress_cb: Callable[[int, str], None],
    cancel_event: Optional[threading.Event] = None,
    timeout_sec: int = 7200,
) -> dict:
    """使用 yutto 下载 B站视频（含弹幕/AI字幕）。"""
    merged = _merge_bilibili_m4s(output_dir, logger) or _find_merged_video(output_dir)
    if merged:
        logger.info("yutto skip: using existing video | path=%s", merged)
        progress_cb(100, "使用已下载的视频")
        return {
            "video_path": merged,
            "title": os.path.splitext(os.path.basename(merged))[0],
            "duration_sec": None,
        }

    logger.info("yutto starting | url=%s", url)

    yutto_bin = _find_tool("yutto")

    cmd = [
        yutto_bin,
        url,
        "-d", output_dir,
        "--no-color",
    ]

    logger.info("yutto cmd: %s", " ".join(cmd))
    progress_cb(10, "yutto 启动...")

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.time() + max(timeout_sec, 60)
    try:
        while proc.poll() is None:
            if cancel_event and cancel_event.is_set():
                proc.kill()
                proc.wait()
                raise RuntimeError("User cancelled")
            if time.time() > deadline:
                proc.kill()
                proc.wait()
                raise RuntimeError(
                    f"yutto 下载超时（>{timeout_sec // 60} 分钟）。可重试；若已有 m4s 分段将自动合并"
                )
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
    except BaseException:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        raise

    stdout_tail = proc.stdout.read()[-1000:] if proc.stdout else ""
    stderr_tail = proc.stderr.read()[-500:] if proc.stderr else ""
    if proc.returncode != 0:
        merged = _merge_bilibili_m4s(output_dir, logger)
        if merged:
            logger.warning(
                "yutto exit code=%s but m4s merge succeeded | stderr=%s",
                proc.returncode,
                stderr_tail,
            )
        else:
            raise RuntimeError(f"yutto failed (code={proc.returncode}): {stderr_tail}")

    logger.info("yutto stdout: %s", stdout_tail)

    video_path = _merge_bilibili_m4s(output_dir, logger) or _find_merged_video(output_dir)
    if not video_path:
        raise RuntimeError(
            "yutto 已完成但未找到可播放视频（可能仅有 m4s 分段且合并失败）。请重试"
        )

    progress_cb(100, "yutto 完成")
    return {
        "video_path": video_path,
        "title": os.path.splitext(os.path.basename(video_path))[0],
        "duration_sec": None,
    }


# ---------- gallery-dl (小红书) ----------

def download_gallerydl(
    url: str,
    output_dir: str,
    job_id: str,
    logger: logging.Logger,
    progress_cb: Callable[[int, str], None],
    cancel_event: Optional[threading.Event] = None,
) -> dict:
    """使用 gallery-dl 下载小红书图文。返回 {images_dir, title}。"""
    logger.info("gallery-dl starting | url=%s", url)

    gdl_bin = _find_tool("gallery-dl")

    images_dir = os.path.join(output_dir, "images")
    os.makedirs(images_dir, exist_ok=True)

    cmd = [
        gdl_bin,
        "--dest", images_dir,
        url,
    ]

    logger.info("gallery-dl cmd: %s", " ".join(cmd))
    progress_cb(20, "gallery-dl 下载中...")

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        while proc.poll() is None:
            if cancel_event and cancel_event.is_set():
                proc.kill()
                proc.wait()
                raise RuntimeError("User cancelled")
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
    except BaseException:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        raise

    if proc.returncode != 0:
        stderr = proc.stderr.read()[-500:] if proc.stderr else ""
        raise RuntimeError(f"gallery-dl failed (code={proc.returncode}): {stderr}")

    logger.info("gallery-dl done | dir=%s", images_dir)
    progress_cb(100, "gallery-dl 完成")

    return {
        "images_dir": images_dir,
        "title": None,
        "duration_sec": None,
    }


# ---------- 调度 ----------

DOWNLOADERS = {
    "ytdlp": download_ytdlp,
    "yutto": download_yutto,
    "gallerydl": download_gallerydl,
    "xhs": "xhs",  # 由调度函数特殊处理
}


def download(
    url: str,
    rule: PlatformRule,
    downloader: str,
    output_dir: str,
    job_id: str,
    logger: logging.Logger,
    progress_cb: Callable[[int, str], None],
    cancel_event: Optional[threading.Event] = None,
    cookies_file: str = "",
    cookies_from_browser: str = "",
    xhs_cookie: str = "",
    proxy: str = "",
) -> dict:
    """
    统一的下载入口。
    """
    fn = DOWNLOADERS.get(downloader)
    if fn is None:
        raise ValueError(f"Unknown downloader: {downloader}")

    if downloader == "xhs":
        from app.services.xhs_downloader import download_xhs
        return download_xhs(url, output_dir, logger=logger, progress_cb=progress_cb, cookie=xhs_cookie)

    if downloader == "ytdlp":
        return fn(
            url, output_dir, job_id, logger, progress_cb,
            rule.key, cookies_file, cookies_from_browser, proxy,
        )
    if downloader == "yutto":
        from app.config import get_config
        return fn(
            url, output_dir, job_id, logger, progress_cb, cancel_event,
            timeout_sec=get_config().yutto_timeout_sec,
        )
    return fn(url, output_dir, job_id, logger, progress_cb, cancel_event)
