"""引擎注册表：按名字取摘要引擎（纯可插拔）。

新增引擎只需在此登记一行，无需改动 summarizer / pipeline。
运行时引擎由 config.summary_engine（环境变量 SUMMARY_ENGINE）选择。
"""
from __future__ import annotations

from app.services.engines.base import ChatResult, Engine, EngineUnavailableError
from app.services.engines.claude import ClaudeEngine
from app.services.engines.openai_compat import (
    DeepSeekEngine,
    MiniMaxEngine,
    QwenVLEngine,
)

# 名字 → 引擎类。新增引擎在此登记即可。
ENGINES: dict[str, type[Engine]] = {
    "deepseek": DeepSeekEngine,
    "claude": ClaudeEngine,
    "qwen": QwenVLEngine,
    "qwen-vl": QwenVLEngine,
    "minimax": MiniMaxEngine,
}


def available_engine_names() -> list[str]:
    return sorted(set(ENGINES.keys()))


def get_engine(name: str) -> Engine:
    """按名字实例化引擎。未知名字抛 ValueError，并列出可选项。"""
    key = (name or "").strip().lower()
    cls = ENGINES.get(key)
    if cls is None:
        raise ValueError(
            f"未知引擎: {name!r}。可选: {', '.join(available_engine_names())}"
        )
    return cls()


__all__ = [
    "ChatResult",
    "Engine",
    "EngineUnavailableError",
    "ENGINES",
    "get_engine",
    "available_engine_names",
]
