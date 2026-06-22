"""SSRF 守卫的行为测试(注入假 resolver,纯 Python,不发真 DNS/网络)。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.url_guard import assert_download_url_allowed, assert_safe_download_url
from app.services.platform_detector import is_supported_platform_url


def _resolver_to(ip):
    """返回一个把任意 host 解析到固定 ip 的假 getaddrinfo。"""
    def fake(host, port):
        return [(2, 1, 6, "", (ip, port))]
    return fake


def test_public_url_ok():
    assert_safe_download_url("https://example.com/video", resolver=_resolver_to("93.184.216.34"))


def test_reject_non_http_scheme():
    with pytest.raises(ValueError, match="http"):
        assert_safe_download_url("file:///etc/passwd", resolver=_resolver_to("1.1.1.1"))


def test_reject_cloud_metadata_ip_literal():
    # 169.254.0.0/16 是 link-local,即便不在显式名单也应被 is_global 拦下。
    with pytest.raises(ValueError):
        assert_safe_download_url("http://169.254.169.254/latest/meta-data/", resolver=_resolver_to("169.254.169.254"))


def test_reject_metadata_hostname():
    with pytest.raises(ValueError, match="元数据"):
        assert_safe_download_url("http://metadata.google.internal/", resolver=_resolver_to("8.8.8.8"))


def test_reject_private_resolved_host():
    # 内网服务名(magi-backend / postgres)解析到私网 → 拒。
    with pytest.raises(ValueError, match="内网|保留"):
        assert_safe_download_url("http://magi-backend:8000/", resolver=_resolver_to("172.18.0.5"))


def test_reject_loopback():
    with pytest.raises(ValueError):
        assert_safe_download_url("http://127.0.0.1:3922/", resolver=_resolver_to("127.0.0.1"))


def test_reject_cgnat():
    # RFC6598 100.64.0.0/10(云/容器常见),is_global=False。
    with pytest.raises(ValueError):
        assert_safe_download_url("http://100.64.0.1/", resolver=_resolver_to("100.64.0.1"))


def test_missing_host():
    with pytest.raises(ValueError, match="主机名"):
        assert_safe_download_url("http://", resolver=_resolver_to("1.1.1.1"))


def test_schemeless_known_platform_allowed():
    # 无 scheme 的平台 URL(pipeline 会补 https)应通过,不被误拒(回归 #3)
    assert_download_url_allowed(
        "youtube.com/watch?v=x", allow_generic=False, resolver=_resolver_to("93.184.216.34")
    )


def test_schemeless_still_ip_guarded():
    # 无 scheme 但解析到内网 → 补 https 后 IP 守卫照常拦
    with pytest.raises(ValueError, match="内网|保留"):
        assert_safe_download_url("youtube.com/x", resolver=_resolver_to("10.0.0.1"))


def test_local_path_as_url_rejected():
    # 本地文件路径当 URL 提交 → 补 https 后 host 为空 → 拒(防 LFI 误放行)
    with pytest.raises(ValueError):
        assert_safe_download_url("/app/storage/x.mp4", resolver=_resolver_to("1.1.1.1"))


def test_reject_nat64_to_private():
    # 64:ff9b::0a00:0001 = NAT64 映射 10.0.0.1(内网),应拒
    with pytest.raises(ValueError, match="NAT64|内网|保留"):
        assert_safe_download_url("https://h/", resolver=_resolver_to("64:ff9b::a00:1"))


# ---- 平台白名单(纵深) ----

def test_is_supported_platform_url():
    assert is_supported_platform_url("https://www.youtube.com/watch?v=x")
    assert is_supported_platform_url("https://b23.tv/abc")
    assert not is_supported_platform_url("https://example.com/video.mp4")


def test_allow_generic_true_permits_unknown_public():
    # allow_generic=True:未知公网平台仅过 IP 守卫即可
    assert_download_url_allowed(
        "https://example.com/v.mp4", allow_generic=True, resolver=_resolver_to("93.184.216.34")
    )


def test_allow_generic_false_rejects_unknown_platform():
    with pytest.raises(ValueError, match="白名单"):
        assert_download_url_allowed(
            "https://example.com/v.mp4", allow_generic=False, resolver=_resolver_to("93.184.216.34")
        )


def test_allow_generic_false_permits_known_platform():
    assert_download_url_allowed(
        "https://www.youtube.com/watch?v=x", allow_generic=False, resolver=_resolver_to("93.184.216.34")
    )


def test_ip_guard_runs_before_platform_check():
    # 已知平台但解析到内网(DNS 投毒)仍被 IP 守卫先拦下
    with pytest.raises(ValueError, match="内网|保留"):
        assert_download_url_allowed(
            "https://www.youtube.com/x", allow_generic=False, resolver=_resolver_to("10.0.0.1")
        )
