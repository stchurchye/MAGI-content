"""请求级日志上下文(ContextVar)。

中间件在请求入口把 request_id 存进来,深层任意模块的 logging 都能经 RequestContextFilter
自动带上,无需逐层透传。request_id 由 agent 经 X-Request-Id 头传入,是跨服务关联主键。
"""
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="")
