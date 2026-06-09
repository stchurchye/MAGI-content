"""浏览器指纹：User-Agent 池 + 各平台 Referer 映射。

用途：内容归档采集公开内容时，避免对所有平台都使用同一个写死的 UA / Referer
被目标站点的风控规则误判为爬虫。

注意：本项目运行在受限脚本环境，禁用 random / Date 等不确定性来源，
因此 UA 轮换采用 itertools.cycle + threading.Lock 做确定性轮转，
而非随机抽样。
"""
from __future__ import annotations

import itertools
import threading

# ---------- User-Agent 池 ----------
# 选取近期真实、主流的桌面浏览器 UA（Chrome / Edge / Firefox / Safari），
# 跨 macOS / Windows 平台，降低单一指纹被识别的概率。
USER_AGENTS: list[str] = [
    # Chrome 131 - macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    # Chrome 131 - Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    # Edge 131 - Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
    # Firefox 133 - Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) "
    "Gecko/20100101 Firefox/133.0",
    # Firefox 133 - macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:133.0) "
    "Gecko/20100101 Firefox/133.0",
    # Safari 18 - macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.1 Safari/605.1.15",
    # Chrome 130 - macOS（多保留一档旧版本，分散指纹）
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
]

# 线程安全的确定性轮转器：每次取下一个 UA，绕一圈后从头开始。
_ua_cycle = itertools.cycle(USER_AGENTS)
_ua_lock = threading.Lock()


def pick_user_agent() -> str:
    """线程安全地取下一个 User-Agent（轮换，非随机）。

    若配置了 USER_AGENT 覆盖（config.user_agent_override 非空），则始终返回该值，
    便于用户固定一个已知可用的指纹。否则在 USER_AGENTS 池中轮换。

    ThreadPoolExecutor 下多 job 并发调用安全：itertools.cycle 的 __next__
    在 CPython 中不保证原子性，故用锁保护。
    """
    from app.config import get_config

    override = get_config().user_agent_override
    if override:
        return override
    with _ua_lock:
        return next(_ua_cycle)


# ---------- 各平台 Referer 映射 ----------
# platform_key 来自 platform_detector.PlatformRule.key。
# 用错 Referer（如对 YouTube 发 B站 Referer）反而更易被识别为异常流量。
_REFERER_MAP: dict[str, str] = {
    "bilibili": "https://www.bilibili.com/",
    "youtube": "https://www.youtube.com/",
    "douyin": "https://www.douyin.com/",
    "xiaohongshu": "https://www.xiaohongshu.com/",
    "nicovideo": "https://www.nicovideo.jp/",
    "twitter": "https://x.com/",
    "weibo": "https://weibo.com/",
    "kuaishou": "https://www.kuaishou.com/",
    "instagram": "https://www.instagram.com/",
    "tiktok": "https://www.tiktok.com/",
}


def referer_for(platform_key: str) -> str:
    """返回该平台应使用的 Referer。

    已知平台返回其站点首页；未知 / generic 平台返回空串，
    表示不设置 Referer（交给 yt-dlp 默认行为，避免误用其他站点 Referer）。
    """
    return _REFERER_MAP.get(platform_key, "")
