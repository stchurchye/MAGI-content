"""启动期防呆检查:配了代理但代理不通时,在启动日志里打一条醒目 warning,
避免等到转写/下载阶段才以 LocalEntryNotFoundError 这类隐晦错误失败。

直连模式(未配代理)零开销、不探测。探测 best-effort:任何异常都吞掉,绝不阻塞启动。
"""
import logging

import requests

logger = logging.getLogger("app")


def check_outbound(proxy: str, *, getter=None, url: str = "https://huggingface.co", timeout: int = 3) -> bool:
    """经代理探一次外网。可达 → True;不可达/异常 → False(不抛)。getter 可注入便于测试。"""
    get = getter or requests.get
    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        get(url, proxies=proxies, timeout=timeout)
        return True
    except Exception:
        return False


def assert_auth_config(config) -> None:
    """生产 fail-closed:ENVIRONMENT=production 时必须配置 AUTH_TOKEN。

    本服务是 server-to-server 后端(下载/转写),默认不应暴露公网;即便仅内网,
    也要求 token 防内网横移。开发环境(默认)不强制,便于本地零配置自测。
    """
    env = (getattr(config, "environment", "") or "").lower()
    if env == "production" and not config.auth_token:
        raise RuntimeError(
            "ENVIRONMENT=production 必须设置 AUTH_TOKEN(server-to-server 鉴权):"
            "请生成强随机 token 并与 agent 侧 MAGI_CONTENT_TOKEN 保持一致。"
        )


def warn_if_outbound_blocked(config, *, getter=None) -> None:
    """配了代理就探一下;不通则打醒目 warning(指明 whisper 模型下载会受影响 + 怎么修)。"""
    proxy = config.http_proxy or config.https_proxy
    if not proxy:
        return  # 直连模式:不探测
    if check_outbound(proxy, getter=getter):
        logger.info("外网代理 %s 可达。", proxy)
    else:
        logger.warning(
            "⚠️ 代理 %s 不可达 —— whisper 模型下载 / YouTube 等外网下载很可能失败"
            "(典型报错 LocalEntryNotFoundError)。请确认代理已启动,或在 .env 置 HTTP_PROXY= 走直连。",
            proxy,
        )
