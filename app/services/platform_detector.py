"""平台检测 + 平台规则配置 + URL 规范化。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


@dataclass
class PlatformRule:
    key: str
    name: str
    domains: list[str]
    default_downloader: str                           # ytdlp / yutto / gallerydl
    alt_downloader: Optional[str] = None              # 备用下载器
    media_type: str = "video"                         # video / image_text
    strictness: str = "medium"                        # low / medium / high
    needs_cookie: str = "optional"                    # none / optional / required
    needs_proxy: str = "none"                         # none / optional / required
    rate_limit: str = "none"                          # none / moderate / aggressive
    note: str = ""


PLATFORM_RULES: dict[str, PlatformRule] = {
    "bilibili": PlatformRule(
        key="bilibili",
        name="B站",
        domains=["bilibili.com", "b23.tv", "www.bilibili.com"],
        default_downloader="yutto",
        alt_downloader="ytdlp",
        strictness="high",
        needs_cookie="required",
        rate_limit="moderate",
        note="yt-dlp 已无法直接下载 B站，默认使用 yutto；如需高清格式可手动切 yt-dlp + cookie",
    ),
    "youtube": PlatformRule(
        key="youtube",
        name="YouTube",
        domains=["youtube.com", "youtu.be", "www.youtube.com", "m.youtube.com"],
        default_downloader="ytdlp",
        strictness="high",
        needs_cookie="optional",
        needs_proxy="optional",
        note="中国大陆可能需要代理；地域限制/年龄限制视频需要 cookie",
    ),
    "xiaohongshu": PlatformRule(
        key="xiaohongshu",
        name="小红书",
        domains=["xiaohongshu.com", "xhslink.com", "www.xiaohongshu.com"],
        default_downloader="xhs",
        media_type="image_text",
        strictness="high",
        needs_cookie="required",
        note="使用 XHS-Downloader，支持图文和视频",
    ),
    "douyin": PlatformRule(
        key="douyin",
        name="抖音",
        domains=["douyin.com", "www.douyin.com", "v.douyin.com"],
        default_downloader="ytdlp",
        strictness="high",
        needs_cookie="optional",
        rate_limit="aggressive",
        note="反爬严格，可能需要频繁更新 cookie",
    ),
    "nicovideo": PlatformRule(
        key="nicovideo",
        name="N站",
        domains=["nicovideo.jp", "www.nicovideo.jp", "nico.ms"],
        default_downloader="ytdlp",
        strictness="medium",
        needs_cookie="optional",
        note="部分视频需要日本 IP + 登录",
    ),
    "weibo": PlatformRule(
        key="weibo",
        name="微博",
        domains=["weibo.com", "m.weibo.cn", "weibo.cn", "video.weibo.com", "t.cn"],
        default_downloader="ytdlp",
        alt_downloader="gallerydl",
        strictness="medium",
        needs_cookie="optional",
        rate_limit="moderate",
        note="视频走 yt-dlp，图文/九宫格自动降级 gallery-dl（其 weibo 支持较好）；t.cn 短链交下载器跟随重定向",
    ),
    "kuaishou": PlatformRule(
        key="kuaishou",
        name="快手",
        domains=["kuaishou.com", "www.kuaishou.com", "v.kuaishou.com"],
        default_downloader="ytdlp",
        alt_downloader="gallerydl",
        strictness="high",
        needs_cookie="optional",
        rate_limit="moderate",
        note="反爬较严，公开视频多数可下；失败自动降级 gallery-dl，稳定下载可能需 cookie",
    ),
    "instagram": PlatformRule(
        key="instagram",
        name="Instagram",
        domains=["instagram.com", "www.instagram.com", "instagr.am"],
        default_downloader="ytdlp",
        alt_downloader="gallerydl",
        media_type="video",
        strictness="high",
        needs_cookie="optional",
        needs_proxy="optional",
        note="多数内容需登录 cookie；图片帖可降级 gallery-dl；大陆需代理",
    ),
    "tiktok": PlatformRule(
        key="tiktok",
        name="TikTok",
        domains=["tiktok.com", "www.tiktok.com", "vm.tiktok.com", "vt.tiktok.com"],
        default_downloader="ytdlp",
        strictness="medium",
        needs_proxy="optional",
        note="yt-dlp 原生支持；大陆需代理，部分内容受地区限制",
    ),
    "twitter": PlatformRule(
        key="twitter",
        name="X (Twitter)",
        domains=["twitter.com", "x.com", "mobile.twitter.com", "fxtwitter.com"],
        default_downloader="ytdlp",
        alt_downloader="gallerydl",
        strictness="medium",
        needs_cookie="optional",
        needs_proxy="optional",
        note="视频推文走 yt-dlp；纯图片/图集推文自动降级 gallery-dl；年龄/受限内容需 cookie，地区限制可能需代理",
    ),
    "wechat": PlatformRule(
        key="wechat",
        name="微信公众号",
        domains=["mp.weixin.qq.com"],
        default_downloader="wechat",
        media_type="text",
        strictness="low",
        needs_cookie="none",
        note="公众号图文文章：抓取 HTML 正文文本直接进摘要（不下载/不转写/不 OCR）",
    ),
    "local": PlatformRule(
        key="local",
        name="本地文件",
        domains=[],
        default_downloader="local",
        media_type="video",  # 实际 video/image_text 由 local 下载器按文件类型决定
        strictness="low",
        needs_cookie="none",
        note="本地上传/本地路径的视频/音频/图片，跳过下载直接进入处理流水线",
    ),
    "generic": PlatformRule(
        key="generic",
        name="通用",
        domains=[],
        default_downloader="ytdlp",
        strictness="low",
        note="兜底规则，尝试 yt-dlp 通用提取",
    ),
}


def _local_root() -> str:
    """允许的本地文件根目录（仅上传存储目录），realpath 以解析符号链接。"""
    from app.config import get_config
    return os.path.realpath(get_config().storage_dir)


def _is_local_input(s: str) -> bool:
    """是否为受信的本地文件：必须是真实文件，且解析后位于 storage_dir 之内。

    安全要点：只接受上传目录内的文件，拒绝 /etc/passwd、~/.ssh 等任意绝对路径，
    避免公开 url 字段被用于任意本地文件读取(LFI)。realpath 同时挡住符号链接逃逸。
    """
    if s.startswith("file://"):
        s = s[len("file://"):]
    elif not s.startswith("/"):
        return False
    if not os.path.isfile(s):
        return False
    real = os.path.realpath(s)
    root = _local_root()
    return real == root or real.startswith(root + os.sep)


def normalize_url(url: str, rule: PlatformRule) -> str:
    """将分享/短链接转成下载器需要的标准格式。"""
    cleaned = url.strip()
    if rule.key == "local":
        return cleaned[len("file://"):] if cleaned.startswith("file://") else cleaned
    if cleaned and "://" not in cleaned:
        cleaned = "https://" + cleaned
    parsed = urlparse(cleaned)

    if rule.key == "xiaohongshu":
        # /discovery/item/xxx → /explore/xxx，保留 xsec_token
        path = parsed.path
        if "/discovery/item/" in path:
            note_id = path.rsplit("/", 1)[-1]
            path = f"/explore/{note_id}"
        keep = {k: v[0] for k, v in parse_qs(parsed.query).items()
                if k in ("xsec_token", "xsec_source")}
        qs = urlencode(keep) if keep else ""
        return urlunparse((parsed.scheme, parsed.netloc, path, "", qs, ""))

    if rule.key == "douyin":
        # 保留必要参数（如 modal_id），去追踪参数
        keep = {k: v[0] for k, v in parse_qs(parsed.query).items()
                if k in ("modal_id", "video_id", "item_id")}
        qs = urlencode(keep) if keep else ""
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", qs, ""))

    if rule.key == "bilibili":
        # b23.tv 短链不处理
        if "b23.tv" in parsed.netloc.lower():
            return cleaned
        # 去除全部追踪参数，只保留路径中的 BV 号
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))

    if rule.key == "twitter":
        # 去掉 ?s=20 等追踪参数，只保留 /user/status/id 路径
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))

    return cleaned


def detect_platform(url: str) -> tuple[PlatformRule, str]:
    """
    根据 URL 检测平台，返回 (规则, 匹配方式)。

    匹配方式: 'domain' | 'generic'
    """
    cleaned = url.strip()
    if _is_local_input(cleaned):
        return PLATFORM_RULES["local"], "local"
    if cleaned and "://" not in cleaned:
        cleaned = "https://" + cleaned

    try:
        host = urlparse(cleaned).hostname or ""
    except Exception:
        return PLATFORM_RULES["generic"], "generic"

    host_lower = host.lower().removeprefix("www.")

    for rule in PLATFORM_RULES.values():
        if rule.key == "generic":
            continue
        for domain in rule.domains:
            clean = domain.lower().removeprefix("www.")
            if host_lower == clean or host_lower.endswith("." + clean):
                return rule, "domain"

    return PLATFORM_RULES["generic"], "generic"


def get_platform_note(url: str) -> str:
    """获取平台备注信息（用于前端提示）。"""
    rule, _ = detect_platform(url)
    parts = [f"平台: {rule.name}"]
    if rule.needs_cookie in ("required", "optional"):
        parts.append("可能需要 Cookie")
    if rule.needs_proxy in ("required", "optional"):
        parts.append("可能需要代理")
    if rule.rate_limit != "none":
        parts.append(f"有速率限制({rule.rate_limit})")
    parts.append(rule.note)
    return " | ".join(parts)
