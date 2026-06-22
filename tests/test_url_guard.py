"""SSRF 守卫的行为测试(注入假 resolver,纯 Python,不发真 DNS/网络)。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.url_guard import assert_safe_download_url


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
