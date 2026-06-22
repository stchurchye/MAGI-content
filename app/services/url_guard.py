"""SSRF 守卫:校验用户提交的下载 URL,挡内网/保留地址与云元数据端点。

在下载入口(create_job)对所有 downloader 统一调用——不论是否走代理都校验:
代理只改变出口,但用户提交 IP 字面量(如 http://169.254.169.254/)或解析到内网的域名时,
本地即可拦下。这是 P0 stopgap;按平台域名白名单的纵深防御见 P1。

已知残留(按可接受风险处理,真正修法是 P1 平台域名 allowlist):
- DNS 重绑定 TOCTOU:解析期校验与实际连接之间的窗口。
- HTTP 重定向:yt-dlp/yutto/gallery-dl 默认在本校验之后跟随 3xx,过检的公网主机可
  302 到内网/云元数据。本守卫只校验"提交的 URL",不拦下游重定向。
本服务为自托管、邀请制内测,对上述高级攻击暴露面低(与 wechat_downloader 一致)。
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

# 云厂商元数据端点(IAM 凭据窃取高危),显式硬封(域名形式也挡,getaddrinfo 之外多一层)。
_METADATA_HOSTS = frozenset(
    {
        "169.254.169.254",  # AWS / GCP / Azure / 阿里云 IMDS
        "metadata.google.internal",  # GCP
        "100.100.100.200",  # 阿里云
    }
)


def assert_safe_download_url(url: str, *, resolver=None) -> None:
    """校验下载 URL 安全。不安全则抛 ValueError。resolver 可注入便于测试。

    规则:仅 http/https;拒绝云元数据端点;解析所有 IP,任一为非全局可路由
    (私网/loopback/link-local/CGNAT 等)即拒。
    """
    getaddrinfo = resolver or socket.getaddrinfo
    p = urlparse(url)
    # 无 scheme 的 URL(如 youtube.com/watch...)pipeline 的 normalize_url 会补 https://;
    # 守卫按同样规则校验,否则既有可用输入会被误拒。本地文件路径(/app/...)补 https 后
    # host 为空,仍会被下面的 no-host 检查拦下,不构成放行。
    if not p.scheme:
        p = urlparse("https://" + url)
    if p.scheme not in ("http", "https"):
        raise ValueError(f"仅支持 http/https 链接,拒绝 {p.scheme or '空'} 协议")
    host = p.hostname
    if not host:
        raise ValueError("无效 URL:缺少主机名")
    if host.lower() in _METADATA_HOSTS:
        raise ValueError("拒绝访问云元数据端点(SSRF 防护)")

    port = p.port or (443 if p.scheme == "https" else 80)
    try:
        infos = getaddrinfo(host, port)
    except socket.gaierror as e:
        raise ValueError(f"域名解析失败:{e}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise ValueError("拒绝访问内网/保留地址(SSRF 防护)")
        # NAT64(64:ff9b::/96)把 IPv4 嵌进 IPv6,is_global 可能放行,但其嵌入地址可指内网。
        if ip.version == 6 and ip in _NAT64_PREFIX:
            embedded = ipaddress.ip_address(int(ip) & 0xFFFFFFFF)
            if not embedded.is_global:
                raise ValueError("拒绝 NAT64 映射到内网/保留地址(SSRF 防护)")


_NAT64_PREFIX = ipaddress.ip_network("64:ff9b::/96")


def assert_download_url_allowed(url: str, *, allow_generic: bool, resolver=None) -> None:
    """下载入口统一守卫:IP 安全(总是)+ 平台白名单(纵深,allow_generic=False 时)。

    allow_generic=False(生产默认)时,仅放行已知平台域名 —— 把"任意 URL → yt-dlp
    generic"这条最易被滥用、且会跟随重定向的路径关掉,显著缩小 SSRF 面。
    """
    assert_safe_download_url(url, resolver=resolver)
    if not allow_generic:
        from app.services.platform_detector import is_supported_platform_url

        if not is_supported_platform_url(url):
            raise ValueError(
                "非白名单平台:仅允许已知平台域名;如需任意来源,设 MAGI_CONTENT_ALLOW_GENERIC=1"
            )
