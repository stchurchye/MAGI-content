"""启动期代理探活的行为测试(注入假 getter,纯 Python,不发真网络)。"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from app.services.startup_checks import (
    assert_auth_config,
    check_outbound,
    warn_if_outbound_blocked,
)


class _Cfg:
    def __init__(self, http="", https=""):
        self.http_proxy = http
        self.https_proxy = https


class _AuthCfg:
    def __init__(self, environment="development", auth_token=""):
        self.environment = environment
        self.auth_token = auth_token


def test_check_outbound_true_when_getter_succeeds():
    assert check_outbound("http://p:7890", getter=lambda *a, **k: None) is True


def test_check_outbound_false_when_getter_raises():
    def boom(*a, **k):
        raise RuntimeError("connect refused")

    assert check_outbound("http://p:7890", getter=boom) is False


def test_no_proxy_skips_probe(caplog):
    called = {"n": 0}

    def getter(*a, **k):
        called["n"] += 1

    with caplog.at_level(logging.WARNING):
        warn_if_outbound_blocked(_Cfg(http="", https=""), getter=getter)
    assert called["n"] == 0  # 直连模式不探测
    assert not caplog.records


def test_warns_when_proxy_unreachable(caplog):
    def boom(*a, **k):
        raise RuntimeError("connect refused")

    with caplog.at_level(logging.WARNING):
        warn_if_outbound_blocked(_Cfg(http="http://host.docker.internal:7890"), getter=boom)
    assert any("不可达" in r.message for r in caplog.records)


def test_no_warning_when_proxy_reachable(caplog):
    with caplog.at_level(logging.WARNING):
        warn_if_outbound_blocked(_Cfg(http="http://host.docker.internal:7890"), getter=lambda *a, **k: None)
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)


# ---- 生产 fail-closed 鉴权校验 ----

def test_auth_config_production_without_token_raises():
    with pytest.raises(RuntimeError, match="AUTH_TOKEN"):
        assert_auth_config(_AuthCfg(environment="production", auth_token=""))


def test_auth_config_production_with_token_ok():
    assert_auth_config(_AuthCfg(environment="production", auth_token="strong-token"))  # 不抛


def test_auth_config_development_without_token_ok():
    assert_auth_config(_AuthCfg(environment="development", auth_token=""))  # 本地零配置不强制


def test_auth_config_case_insensitive_env():
    with pytest.raises(RuntimeError):
        assert_auth_config(_AuthCfg(environment="PRODUCTION", auth_token=""))
