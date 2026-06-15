"""微信公众号等 HTML 图文文章的正文抽取下载器。

公众号文章本质是 HTML 富文本（文字为主），既非视频也非可下载媒体，yt-dlp 的 generic
提取器对其 0 产物。此下载器用 trafilatura 抽取正文文本，交给流水线的「文本分支」
直接走摘要（跳过下载/转写/OCR）。

返回 {"article_text": 正文, "title": 标题, "duration_sec": None}。
"""
from __future__ import annotations

import ipaddress
import logging
import socket
import urllib.request
from typing import Callable, Optional
from urllib.parse import urlparse

from app.services.fingerprint import pick_user_agent

_MIN_ARTICLE_CHARS = 50  # 少于此视为抽取失败（验证码页/空壳）
_MAX_HTML_BYTES = 10 * 1024 * 1024  # 正文页 10MB 足够；超出视为异常，防止 OOM/占满 worker


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """禁止跟随重定向：防止公开 URL 被重定向到内网地址绕过下面的 SSRF 校验。"""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _assert_allowed_scheme(url: str):
    """只放行 http/https（始终生效）。挡 file://、ftp:// 等本地文件读取面。"""
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise RuntimeError(f"仅支持 http/https 链接，拒绝 {p.scheme or '空'} 协议")
    if not p.hostname:
        raise RuntimeError("无效 URL：缺少主机名")
    return p


def _assert_public_ip(p) -> None:
    """直连出口时校验目标解析到的 IP 必须是全局可路由地址（SSRF 防护）。

    仅在「无 http 代理直连」时调用：用代理时实际出口是代理、本地解析不代表真实目标，
    再做本地 IP 校验既无意义又会在代理/NAT 环境误伤合法公网域名。

    用 `not is_global` 统一判定，而非枚举 is_private/is_loopback/… ——后者会漏掉
    RFC6598 CGNAT 共享地址段 100.64.0.0/10（云/容器内网常见）等范围。
    已知残留：这是解析期校验，与实际连接之间存在 TOCTOU（DNS 重绑定）窗口；本工具为
    单用户自托管、对该高级攻击的暴露面低，未做连接级 IP 钉定，按可接受残留风险处理。
    """
    port = p.port or (443 if p.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(p.hostname, port)
    except socket.gaierror as e:
        raise RuntimeError(f"域名解析失败：{e}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise RuntimeError("拒绝访问内网/保留地址（SSRF 防护）")


def _fetch_html(url: str, proxy: str = "", timeout: int = 30) -> str:
    """抓取公开文章 HTML：仅 http/https、（直连时）挡内网、禁重定向、限大小。"""
    p = _assert_allowed_scheme(url)
    # 无 scheme 的代理值（如 127.0.0.1:7890）视为 http 代理，避免被当成"无代理"而直连泄漏真实 IP
    if proxy and "://" not in proxy:
        proxy = "http://" + proxy
    use_proxy = bool(proxy and urlparse(proxy).scheme in ("http", "https"))
    if not use_proxy:
        _assert_public_ip(p)  # 直连才做本地 IP 校验；走代理则交给代理出口

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": pick_user_agent(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://mp.weixin.qq.com/",
        },
    )
    # 手动构造 OpenerDirector，只挂 http/https 处理器——刻意不加 FileHandler/FTPHandler，
    # 杜绝 file://、ftp:// 读取。urllib 不支持 socks 代理，仅 http/https 代理才挂上
    # （公众号多为境内直连，socks 代理跳过即可，避免 socks5:// 透传必失败）。
    opener = urllib.request.OpenerDirector()
    if use_proxy:
        opener.add_handler(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener.add_handler(urllib.request.HTTPHandler())
    opener.add_handler(urllib.request.HTTPSHandler())
    opener.add_handler(urllib.request.HTTPErrorProcessor())
    opener.add_handler(_NoRedirectHandler())

    with opener.open(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        raw = resp.read(_MAX_HTML_BYTES + 1)
    if len(raw) > _MAX_HTML_BYTES:
        raise RuntimeError(f"页面过大（>{_MAX_HTML_BYTES // (1024 * 1024)}MB），已中止")
    return raw.decode(charset, errors="ignore")


def download_wechat(
    url: str,
    output_dir: str,
    logger: Optional[logging.Logger] = None,
    progress_cb: Optional[Callable[[int, str], None]] = None,
    proxy: str = "",
) -> dict:
    """抓取并抽取公众号/HTML 文章正文。返回 {article_text, title, duration_sec}。"""
    import os

    import trafilatura

    log = logger or logging.getLogger(__name__)
    if progress_cb:
        progress_cb(20, "正在抓取文章…")

    try:
        html = _fetch_html(url, proxy=proxy)
    except Exception as e:
        raise RuntimeError(f"文章抓取失败（网络/反爬）：{e}") from e

    if progress_cb:
        progress_cb(60, "正在抽取正文…")

    text = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=True,
        favor_recall=True,
    ) or ""
    text = text.strip()

    title = ""
    try:
        meta = trafilatura.extract_metadata(html)
        if meta and meta.title:
            title = meta.title.strip()
    except Exception:
        pass

    if len(text) < _MIN_ARTICLE_CHARS:
        raise RuntimeError(
            "未能从该链接抽取到正文。可能是：需要登录/验证的文章、纯图片推文，"
            "或非文章页面。公众号请确认是可公开访问的 /s/ 文章链接。"
        )

    # 落盘一份原始正文，便于排查与留档
    raw_path = os.path.join(output_dir, "article.txt")
    try:
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        pass

    log.info("wechat/article extracted | title=%s chars=%d", title or "(无标题)", len(text))
    if progress_cb:
        progress_cb(100, "正文抽取完成")

    return {"article_text": text, "title": title or "Unknown", "duration_sec": None}
