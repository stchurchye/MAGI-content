"""下载策略调度：yt-dlp / yutto / gallery-dl。"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

import yt_dlp

from app.services.fingerprint import pick_user_agent, referer_for
from app.services.platform_detector import PlatformRule
from app.services.throttle import throttle


def _duration_match_filter(max_sec: int):
    """yt-dlp match_filter:时长超 max_sec 即跳过;时长缺失(直播等)放行(返回 None)。"""
    def _f(info, *, incomplete=False):
        dur = info.get("duration")
        if dur is not None and dur > max_sec:
            return f"视频时长 {int(dur)}s 超过上限 {max_sec}s,跳过"
        return None

    return _f


def _build_ydl_opts(
    output_template: str,
    progress_hook: Callable,
    platform_key: str = "",
    cookies_file: str = "",
    cookies_from_browser: str = "",
    proxy: str = "",
) -> dict:
    from app.config import get_config
    _cfg = get_config()
    opts = {
        "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
        "merge_output_format": "mp4",
        "outtmpl": output_template,
        "progress_hooks": [progress_hook],
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "retries": 3,
        # 滥用防护:单文件大小上限(超限中止下载);时长上限(元数据缺失如直播放行)。
        "max_filesize": _cfg.max_download_mb * 1024 * 1024,
        "match_filter": _duration_match_filter(_cfg.max_duration_sec),
        # 顺带拉取官方字幕（YouTube 等）：命中即被流水线复用为 transcript，免去本地
        # 重转写。自动生成字幕默认不取（机翻/ASR 质量常不及 whisper，且可能串语种），
        # 由 SUBTITLE_USE_AUTOCAPTION 控制。无字幕的平台不受影响（不报错、不阻断下载）。
        "writesubtitles": True,
        "writeautomaticsub": get_config().subtitle_use_autocaption,
        "subtitleslangs": ["zh-Hans", "zh-Hant", "zh", "zh-CN", "en", "en-US"],
        # 只取可解析格式；去掉 /best 兜底，避免下到 find_subtitle_file 不认的 json3/ttml/srv
        "subtitlesformat": "srt/vtt",
        "http_headers": {
            # UA 从池中轮换取，避免所有 job 共用单一指纹
            "User-Agent": pick_user_agent(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    }

    # 按平台设置正确的 Referer；未知平台不设（修复此前固定 B站 Referer 的 bug）
    referer = referer_for(platform_key)
    if referer:
        opts["http_headers"]["Referer"] = referer

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
        # Referer 已由 referer_for 统一设置，此处只配置 extractor_args
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

        from app.services.subtitles import find_subtitle_file
        subtitle_path = find_subtitle_file(output_dir)
        if subtitle_path:
            logger.info("yt-dlp subtitle found | path=%s", subtitle_path)

        return {
            "video_path": video_path,
            "title": title,
            "duration_sec": duration,
            "subtitle_path": subtitle_path,
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
    sessdata: str = "",
) -> dict:
    """使用 yutto 下载 B站视频（含弹幕/AI字幕）。

    sessdata 非空时以登录态下载：可取高清/大会员/付费内容，并能拉到 B站 AI 字幕。
    """
    from app.services.subtitles import find_subtitle_file

    merged = _merge_bilibili_m4s(output_dir, logger) or _find_merged_video(output_dir)
    if merged:
        logger.info("yutto skip: using existing video | path=%s", merged)
        progress_cb(100, "使用已下载的视频")
        return {
            "video_path": merged,
            "title": os.path.splitext(os.path.basename(merged))[0],
            "duration_sec": None,
            "subtitle_path": find_subtitle_file(output_dir),
        }

    logger.info("yutto starting | url=%s sessdata=%s", url, "set" if sessdata else "anonymous")

    yutto_bin = _find_tool("yutto")

    cmd = [
        yutto_bin,
        url,
        "-d", output_dir,
        "--no-color",
    ]
    # 登录态：让 yutto 拿到高清/会员清晰度与 AI 字幕（仅在配置了 SESSDATA 时）
    if sessdata:
        cmd.extend(["-c", sessdata])

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

    subtitle_path = find_subtitle_file(output_dir)
    if subtitle_path:
        logger.info("yutto subtitle found | path=%s", subtitle_path)
    progress_cb(100, "yutto 完成")
    return {
        "video_path": video_path,
        "title": os.path.splitext(os.path.basename(video_path))[0],
        "duration_sec": None,
        "subtitle_path": subtitle_path,
    }


# ---------- gallery-dl (小红书) ----------

def download_gallerydl(
    url: str,
    output_dir: str,
    job_id: str,
    logger: logging.Logger,
    progress_cb: Callable[[int, str], None],
    cancel_event: Optional[threading.Event] = None,
    cookies_file: str = "",
    cookies_from_browser: str = "",
    proxy: str = "",
) -> dict:
    """使用 gallery-dl 下载图集（微博/快手/IG/X 等降级路径）。返回 {images_dir, title}。

    透传 cookie/proxy，使降级路径与 yt-dlp 主路具备同等反爬能力（此前降级即裸奔）。
    """
    logger.info("gallery-dl starting | url=%s cookie=%s proxy=%s",
                url, "set" if (cookies_file or cookies_from_browser) else "none",
                "set" if proxy else "none")

    gdl_bin = _find_tool("gallery-dl")

    images_dir = os.path.join(output_dir, "images")
    os.makedirs(images_dir, exist_ok=True)

    cmd = [gdl_bin, "--dest", images_dir]
    if cookies_file and os.path.exists(cookies_file):
        cmd.extend(["--cookies", cookies_file])
    elif cookies_from_browser:
        cmd.extend(["--cookies-from-browser", cookies_from_browser])
    if proxy:
        cmd.extend(["--proxy", proxy])
    cmd.append(url)

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


# ---------- 下载器插件注册表 ----------


@dataclass
class DownloadContext:
    """一次下载所需的全部上下文，传给下载器插件（统一签名，便于扩展）。"""
    url: str
    rule: PlatformRule
    output_dir: str
    job_id: str
    logger: logging.Logger
    progress_cb: Callable[[int, str], None]
    cancel_event: Optional[threading.Event] = None
    cookies_file: str = ""
    cookies_from_browser: str = ""
    xhs_cookie: str = ""
    proxy: str = ""


def _plugin_ytdlp(ctx: DownloadContext) -> dict:
    return download_ytdlp(
        ctx.url, ctx.output_dir, ctx.job_id, ctx.logger, ctx.progress_cb,
        ctx.rule.key, ctx.cookies_file, ctx.cookies_from_browser, ctx.proxy,
    )


def _plugin_yutto(ctx: DownloadContext) -> dict:
    from app.config import get_config
    cfg = get_config()
    return download_yutto(
        ctx.url, ctx.output_dir, ctx.job_id, ctx.logger, ctx.progress_cb,
        ctx.cancel_event, timeout_sec=cfg.yutto_timeout_sec,
        sessdata=cfg.bilibili_sessdata,
    )


def _plugin_gallerydl(ctx: DownloadContext) -> dict:
    return download_gallerydl(
        ctx.url, ctx.output_dir, ctx.job_id, ctx.logger, ctx.progress_cb, ctx.cancel_event,
        cookies_file=ctx.cookies_file, cookies_from_browser=ctx.cookies_from_browser,
        proxy=ctx.proxy,
    )


def _plugin_wechat(ctx: DownloadContext) -> dict:
    from app.services.wechat_downloader import download_wechat
    return download_wechat(
        ctx.url, ctx.output_dir, logger=ctx.logger,
        progress_cb=ctx.progress_cb, proxy=ctx.proxy,
    )


def _plugin_xhs(ctx: DownloadContext) -> dict:
    from app.services.xhs_downloader import download_xhs
    return download_xhs(
        ctx.url, ctx.output_dir, logger=ctx.logger,
        progress_cb=ctx.progress_cb, cookie=ctx.xhs_cookie,
    )


def _plugin_local(ctx: DownloadContext) -> dict:
    """本地文件：跳过下载，按类型返回 video_path（视频/音频）或 images_dir（图片）。"""
    from app.config import get_config
    from app.services.ocr import IMAGE_EXTS  # 单一真源：避免图片扩展名集合漂移

    path = ctx.url
    if path.startswith("file://"):
        path = path[len("file://"):]
    if not os.path.isfile(path):
        raise RuntimeError(f"本地文件不存在: {path}")

    # 安全：仅允许 storage_dir 内的文件，挡任意本地文件读取(LFI)；realpath 解析符号链接逃逸
    real = os.path.realpath(path)
    root = os.path.realpath(get_config().storage_dir)
    if not (real == root or real.startswith(root + os.sep)):
        raise RuntimeError("本地文件必须位于上传目录内")

    ext = os.path.splitext(path)[1].lower()
    title = os.path.splitext(os.path.basename(path))[0]
    ctx.logger.info("local | path=%s ext=%s", path, ext)
    ctx.progress_cb(100, "使用本地文件")

    if ext in IMAGE_EXTS:
        images_dir = os.path.join(ctx.output_dir, "images")
        os.makedirs(images_dir, exist_ok=True)
        dst = os.path.join(images_dir, os.path.basename(path))
        if os.path.abspath(dst) != os.path.abspath(path):
            shutil.copy2(path, dst)
        return {"images_dir": images_dir, "title": title, "duration_sec": None}

    # 视频/音频：直接作为 video_path，交给 extract_audio（ffmpeg 对纯音频同样适用）
    return {"video_path": path, "title": title, "duration_sec": None}


# 下载器名 → 插件（统一接受 DownloadContext）。新增下载器登记一行即可。
DOWNLOAD_PLUGINS: dict[str, Callable[["DownloadContext"], dict]] = {
    "ytdlp": _plugin_ytdlp,
    "yutto": _plugin_yutto,
    "gallerydl": _plugin_gallerydl,
    "xhs": _plugin_xhs,
    "local": _plugin_local,
    "wechat": _plugin_wechat,
}


def register_downloader(name: str, fn: Callable[["DownloadContext"], dict]) -> None:
    """注册自定义下载器插件（第三方扩展点）。"""
    DOWNLOAD_PLUGINS[name] = fn


def _dispatch(downloader: str, ctx: DownloadContext) -> dict:
    """按名查表调用下载器插件（不含节流/降级逻辑）。"""
    fn = DOWNLOAD_PLUGINS.get(downloader)
    if fn is None:
        raise ValueError(f"Unknown downloader: {downloader}")
    return fn(ctx)


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

    - 发起下载前按平台 rate_limit 节流（整个 download 仅一次，不随降级重复）。
    - 若主下载器抛异常且 rule.alt_downloader 存在且与主不同，则自动用备用下载器
      重试一次（降级仅一层，避免无限递归）。用户主动取消不触发降级。
      最终报错同时保留主/备两个下载器的原始异常信息。
    """
    if downloader not in DOWNLOAD_PLUGINS:
        raise ValueError(f"Unknown downloader: {downloader}")

    # 按平台频率限制：多任务并发打同一平台时拉开相邻请求间隔，降低被风控/限流误杀的概率。
    # 节流发生在真正发起下载之前，按 rule.key 各自计时，不同平台互不阻塞；仅节流一次，
    # 降级重试不再二次等待。
    waited = throttle(rule.key, rule.rate_limit, cancel_event=cancel_event)
    if waited > 0:
        logger.info(
            "throttle | platform=%s rate_limit=%s waited=%.2fs",
            rule.key, rule.rate_limit, waited,
        )

    # 节流等待期间用户可能已取消（throttle 在取消时静默提前返回）。在发起真正的下载前
    # 复查一次：ytdlp 路径一旦启动便不可中断，若不在此拦截，取消信号会在节流窗口后失效。
    if cancel_event and cancel_event.is_set():
        raise RuntimeError("User cancelled")

    ctx = DownloadContext(
        url=url, rule=rule, output_dir=output_dir, job_id=job_id,
        logger=logger, progress_cb=progress_cb, cancel_event=cancel_event,
        cookies_file=cookies_file, cookies_from_browser=cookies_from_browser,
        xhs_cookie=xhs_cookie, proxy=proxy,
    )

    def _do(name: str) -> dict:
        return _dispatch(name, ctx)

    try:
        return _do(downloader)
    except Exception as primary_exc:  # 仅捕获业务异常；KeyboardInterrupt/SystemExit 直接上抛
        alt = rule.alt_downloader
        # 用户主动取消、备用为空、备用与主相同、或备用不在已知下载器集合 → 不降级，原样抛出
        cancelled = bool(cancel_event and cancel_event.is_set())
        if (
            cancelled
            or not alt
            or alt == downloader
            or alt not in DOWNLOAD_PLUGINS
        ):
            raise

        logger.warning(
            "主下载器失败，自动降级到备用下载器 | primary=%s alt=%s error=%s",
            downloader, alt, primary_exc,
        )
        try:
            return _do(alt)
        except Exception as alt_exc:  # 同上：系统级中断不在此吞掉
            # 取消优先：备用路径中用户取消不再包装为降级失败
            if cancel_event and cancel_event.is_set():
                raise
            logger.error(
                "备用下载器也失败 | primary=%s alt=%s primary_error=%s alt_error=%s",
                downloader, alt, primary_exc, alt_exc,
            )
            raise RuntimeError(
                f"下载失败：主下载器 {downloader} 报错 [{primary_exc}]；"
                f"备用下载器 {alt} 报错 [{alt_exc}]"
            ) from alt_exc
