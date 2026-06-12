"""摘要引擎统一接口（可插拔）。

每个引擎只负责"调用机制 + 能力声明"，长文本的 map-reduce 编排与 JSON 解析
统一放在 summarizer.py，避免每个适配器重复实现。

能力声明：
- max_input_chars: 单次调用可吃下的最大字符数（超过则由 summarizer 走 map-reduce）。
- supports_vision: 是否支持原生读图（图文走视觉而非 OCR→文本）。

调用接口：
- available() -> bool                 是否配置了可用 key。
- complete(...) -> ChatResult         一次调用，返回原始文本 + token 用量。
  - images 非空且 supports_vision 时，由各适配器构造 provider 专属的多模态内容。
  - json_mode=True 时尽量启用结构化 JSON 输出（部分 provider 靠提示词约束）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class ChatResult:
    text: str
    input_tokens: int = -1
    output_tokens: int = -1


class Engine:
    """引擎适配器基类。子类覆盖 name / 能力 / _complete。"""

    name: str = "base"
    # 单次可吃下的最大字符数；超过则 summarizer 走 map-reduce。
    max_input_chars: int = 100_000
    # 是否支持原生视觉读图。
    supports_vision: bool = False
    # 单次输出 token 上限的默认值。
    default_max_tokens: int = 4096

    # ---- 子类需实现 ----

    def available(self) -> bool:
        """是否具备调用条件（已配置 key 等）。"""
        raise NotImplementedError

    def complete(
        self,
        system: str,
        text: str,
        images: Optional[list[str]] = None,
        max_tokens: Optional[int] = None,
        json_mode: bool = True,
    ) -> ChatResult:
        """单次调用：返回模型原始文本输出与 token 用量。"""
        raise NotImplementedError

    # ---- 公共工具 ----

    @staticmethod
    def _env(*names: str) -> str:
        """按顺序取第一个非空环境变量（去空白）。"""
        for n in names:
            v = os.environ.get(n)
            if v and v.strip():
                return v.strip()
        return ""

    @staticmethod
    def _image_media_type(path: str) -> str:
        ext = os.path.splitext(path)[1].lower()
        return {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }.get(ext, "image/png")


class EngineUnavailableError(RuntimeError):
    """引擎缺少可用 key 或配置时抛出。"""
