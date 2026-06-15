"""完成回调 webhook 订阅者的行为测试。

注入假的 poster 在 HTTP 边界处替换 requests.post,纯 Python 即可运行,无需真发网络。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.webhook import make_webhook_subscriber


def _recorder():
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})

    return calls, fake_post


def test_posts_on_completed_with_payload_and_auth():
    calls, fake_post = _recorder()
    cb = make_webhook_subscriber("http://agent/cb", "tok", poster=fake_post)
    cb("job-1", "completed", {"status": "completed", "progress_pct": 100})
    assert len(calls) == 1
    assert calls[0]["url"] == "http://agent/cb"
    assert calls[0]["json"] == {"job_id": "job-1", "status": "completed"}
    assert calls[0]["headers"]["Authorization"] == "Bearer tok"


def test_posts_on_failed():
    calls, fake_post = _recorder()
    cb = make_webhook_subscriber("http://agent/cb", "tok", poster=fake_post)
    cb("job-2", "failed", {"status": "failed"})
    assert len(calls) == 1
    assert calls[0]["json"] == {"job_id": "job-2", "status": "failed"}


def test_skips_non_terminal_events():
    calls, fake_post = _recorder()
    cb = make_webhook_subscriber("http://agent/cb", "tok", poster=fake_post)
    for ev in ("pending", "downloading", "transcribing", "summarizing"):
        cb("job-3", ev, {})
    assert calls == []


def test_no_auth_header_when_token_empty():
    calls, fake_post = _recorder()
    cb = make_webhook_subscriber("http://agent/cb", "", poster=fake_post)
    cb("job-4", "completed", {})
    assert "Authorization" not in calls[0]["headers"]


def test_swallows_poster_exception():
    def boom(*args, **kwargs):
        raise RuntimeError("network down")

    cb = make_webhook_subscriber("http://agent/cb", "tok", poster=boom)
    # 不得抛出 —— 回调失败绝不能影响流水线主流程。
    cb("job-5", "completed", {})
