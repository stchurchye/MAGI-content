"""OpenAI 兼容协议的引擎适配器：DeepSeek / 通义千问 Qwen-VL / MiniMax。

三者都走 openai SDK 的 chat.completions，仅 base_url / model / key 名 / 能力不同，
故共用一个基类 _OpenAICompatEngine。
"""
from __future__ import annotations

import base64
import os
from typing import Optional

from app.services.engines.base import ChatResult, Engine, EngineUnavailableError


class _OpenAICompatEngine(Engine):
    """OpenAI 兼容端点通用实现。子类设定 base_url / model / key 名 / 能力。"""

    base_url: str = ""
    model: str = ""
    key_envs: tuple[str, ...] = ()
    # 该端点是否支持 response_format={"type":"json_object"}（不支持则靠提示词约束 JSON）。
    supports_json_response_format: bool = True

    def __init__(self, model: Optional[str] = None):
        if model:
            self.model = model
        self.api_key = self._env(*self.key_envs)

    def available(self) -> bool:
        return bool(self.api_key)

    def _data_uri(self, path: str) -> str:
        mt = self._image_media_type(path)
        with open(path, "rb") as f:
            b64 = base64.standard_b64encode(f.read()).decode("utf-8")
        return f"data:{mt};base64,{b64}"

    def _user_content(self, text: str, images: Optional[list[str]]):
        """构造 user 内容：有图且支持视觉 → 多模态 parts，否则纯文本字符串。"""
        if images and self.supports_vision:
            parts = [{"type": "image_url", "image_url": {"url": self._data_uri(p)}}
                     for p in images]
            parts.append({"type": "text", "text": text})
            return parts
        return text

    def complete(
        self,
        system: str,
        text: str,
        images: Optional[list[str]] = None,
        max_tokens: Optional[int] = None,
        json_mode: bool = True,
    ) -> ChatResult:
        if not self.available():
            raise EngineUnavailableError(
                f"{self.name}: 缺少 API key（环境变量 {' / '.join(self.key_envs)}）"
            )
        from openai import OpenAI

        client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        user_content = self._user_content(text, images)
        has_image = isinstance(user_content, list)

        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens or self.default_max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
        }
        # 视觉调用避免叠加 response_format（部分多模态端点不兼容），靠提示词约束 JSON。
        if json_mode and self.supports_json_response_format and not has_image:
            kwargs["response_format"] = {"type": "json_object"}

        resp = client.chat.completions.create(**kwargs)
        raw = (resp.choices[0].message.content or "").strip()
        usage = getattr(resp, "usage", None)
        return ChatResult(
            text=raw,
            input_tokens=getattr(usage, "prompt_tokens", -1) if usage else -1,
            output_tokens=getattr(usage, "completion_tokens", -1) if usage else -1,
        )


class DeepSeekEngine(_OpenAICompatEngine):
    name = "deepseek"
    base_url = "https://api.deepseek.com"
    model = "deepseek-chat"
    key_envs = ("DEEPSEEK_API_KEY",)
    supports_vision = False
    # deepseek-chat 上下文约 64K tokens；中文约 1.5 字/token，留余量按 ~90k 字符触发 map-reduce。
    max_input_chars = 90_000
    supports_json_response_format = True


class QwenVLEngine(_OpenAICompatEngine):
    name = "qwen-vl"
    base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model = "qwen-vl-max"
    key_envs = ("DASHSCOPE_API_KEY", "QWEN_API_KEY")
    supports_vision = True
    max_input_chars = 100_000
    # qwen-vl 端点对 response_format 支持不稳，统一靠提示词约束。
    supports_json_response_format = False


class MiniMaxEngine(_OpenAICompatEngine):
    """MiniMax（如 M3）。OpenAI 兼容，端点/模型/视觉能力由环境变量驱动，便于"后面加"。

    环境变量：
    - MINIMAX_API_KEY        必填
    - MINIMAX_BASE_URL       默认 https://api.minimaxi.com/v1（按你实际端点改）
    - MINIMAX_MODEL          默认 MiniMax-M3（请按官方确切模型串覆盖）
    - MINIMAX_VISION=1       声明该模型支持视觉（决定图文走视觉还是 OCR）
    - MINIMAX_MAX_INPUT_CHARS 单次容量，默认 100000
    """

    name = "minimax"
    key_envs = ("MINIMAX_API_KEY",)
    supports_json_response_format = False  # 未确认，保守靠提示词

    def __init__(self, model: Optional[str] = None):
        self.base_url = os.environ.get("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1").strip()
        self.model = model or os.environ.get("MINIMAX_MODEL", "MiniMax-M3").strip()
        self.supports_vision = os.environ.get("MINIMAX_VISION", "").strip() in ("1", "true", "True")
        try:
            v = int(os.environ.get("MINIMAX_MAX_INPUT_CHARS", "100000"))
            self.max_input_chars = v if v > 0 else 100_000
        except (TypeError, ValueError):
            self.max_input_chars = 100_000
        self.api_key = self._env(*self.key_envs)
