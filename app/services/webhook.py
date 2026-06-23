"""完成回调 webhook：作业进入终态(completed/failed)时,POST 通知外部接收端。

注册为 PipelineManager 的全局 SSE 订阅者(subscribe_to_all)。轻量「ping」语义:
只发 {job_id, status},接收端凭 job_id 回拉详情(GET /api/jobs/{id})。best-effort —
任何异常都吞掉并记日志,绝不影响流水线主流程。
"""
import logging

import requests

logger = logging.getLogger("app")

# 仅终态才回调(进行中的进度事件不发,避免噪音)。
# 'cancelled' 也是终态:cancel() 内部把 DB 置 failed 但广播 event='cancelled'
# (data.status='failed')。纳入这里,否则取消的作业永不回调、接收端会一直卡 pending。
_TERMINAL_EVENTS = {"completed", "failed", "cancelled"}


def make_webhook_subscriber(webhook_url: str, webhook_token: str = "", *, poster=None, timeout: int = 10):
    """构造一个 (job_id, event, data) 回调:终态时 POST {job_id, status} 到 webhook_url。

    poster 可注入(默认 requests.post)便于测试。
    """
    post = poster or requests.post

    def _on_event(job_id: str, event: str, data: dict) -> None:
        if event not in _TERMINAL_EVENTS:
            return
        # 发送权威终态:cancelled 的 data.status='failed',归一化为 failed,
        # 接收端只需识别 completed/failed 两态(契约不变)。
        status = (data or {}).get("status") or event
        headers = {"Content-Type": "application/json"}
        if webhook_token:
            headers["Authorization"] = f"Bearer {webhook_token}"
        try:
            post(
                webhook_url,
                json={"job_id": job_id, "status": status},
                headers=headers,
                timeout=timeout,
            )
            logger.info(
                "webhook 已发送",
                extra={"event": "webhook_sent", "job_id": job_id, "status": status, "target": webhook_url},
            )
        except Exception:
            # best-effort:回调失败不回滚作业,只记日志(接收端也可改用轮询兜底)。
            logger.warning(
                "webhook 发送失败",
                extra={"event": "webhook_failed", "job_id": job_id, "status": status, "target": webhook_url},
                exc_info=True,
            )

    return _on_event
