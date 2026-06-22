"""下载滥用防护:时长 match_filter 的纯函数测试。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.downloader import _duration_match_filter


def test_duration_filter_passes_under_limit():
    f = _duration_match_filter(100)
    assert f({"duration": 50}) is None  # None = 通过


def test_duration_filter_rejects_over_limit():
    f = _duration_match_filter(100)
    reason = f({"duration": 200})
    assert reason is not None and "超过上限" in reason


def test_duration_filter_allows_missing_duration():
    # 直播等元数据缺失 → 放行(不拦)
    f = _duration_match_filter(100)
    assert f({}) is None
    assert f({"duration": None}) is None
