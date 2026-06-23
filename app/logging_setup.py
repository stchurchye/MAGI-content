"""统一结构化 JSON 日志(与 agent、magi-system 同形契约)。

输出:
  {ts, level, service, logger, message, request_id?, event?, method?, status?, ...}
便于集中日志(Loki/Grafana)按 service/level 过滤、按 request_id 串联全链路。

约定:机器字段(键与 level/service/event 等值)一律 ASCII,只有 message 与面向人的文本用中文
(ensure_ascii=False)。LOG_LEVEL 环境变量驱动级别。保留写入 logs/app.log 的习惯(同 JSON 格式)。

注意:本文件与 MAGI-System 的 backend/logging_setup.py 是**有意复制**(两仓独立、无共享包)。
改格式/字段/uvicorn 处理时**两处须同步**,否则集中查询会在某个服务上静默断链。
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone

from app.logging_context import request_id_var

SERVICE_NAME = "magi-content"

# 标准 LogRecord 属性 + 已显式处理的字段。其余经 logging 的 extra= 传入的业务字段一律
# 自动收录(见 JsonFormatter),避免「加了 extra 字段却忘维护白名单 → 被静默丢弃」。
_RESERVED = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName", "message", "asctime",
    "request_id", "trace_id",
}


class JsonFormatter(logging.Formatter):
    """把一条 LogRecord 渲染成单行 JSON。exc_info 存在时落 stack(完整堆栈)。"""

    def format(self, record: logging.LogRecord) -> str:
        ts = (
            datetime.fromtimestamp(record.created, timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        entry = {
            "ts": ts,
            "level": record.levelname.lower(),
            "service": SERVICE_NAME,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", "") or request_id_var.get("")
        if request_id:
            entry["request_id"] = request_id
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_") or value is None:
                continue
            entry[key] = value
        if record.exc_info:
            entry["stack"] = self.formatException(record.exc_info)
        # default=str 兜底:万一某 extra 值不可 JSON 序列化也不让整条日志崩。
        return json.dumps(entry, ensure_ascii=False, default=str)


class RequestContextFilter(logging.Filter):
    """把 ContextVar 里的 request_id 注入每条日志,使深层模块日志自动带上。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "request_id", ""):
            record.request_id = request_id_var.get("")
        return True


def setup_logging(logs_dir: str) -> None:
    """配置 root logger 输出统一 JSON 到 stdout + logs/app.log。幂等(可重复调用)。

    LOG_LEVEL(默认 INFO)控制级别。沿用原 app.log 文件(便于宿主机直接 tail),但改为同形 JSON。
    """
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    formatter = JsonFormatter()
    context_filter = RequestContextFilter()

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(context_filter)

    handlers = [stream_handler]
    try:
        os.makedirs(logs_dir, exist_ok=True)
        file_handler = logging.FileHandler(os.path.join(logs_dir, "app.log"), encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler.addFilter(context_filter)
        handlers.append(file_handler)
    except OSError:
        # 日志文件不可写(只读挂载等)不应拖垮启动:退化为仅 stdout。
        pass

    root = logging.getLogger()
    root.handlers[:] = handlers
    root.setLevel(level)

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers[:] = []
        lg.propagate = True
    # 每个请求已由 RequestLogMiddleware 输出一条结构化访问日志,uvicorn.access 的重复行静音。
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
