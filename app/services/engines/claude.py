"""Claude 引擎适配器（anthropic SDK）。

Claude Sonnet 4.6：1M 上下文（长文稿全量塞入不截断）+ 原生视觉。
长输入/大输出用流式 + get_final_message() 避免 HTTP 超时。
结构化 JSON 靠提示词约束（不依赖 response_format）。
"""
from __future__ import annotations

import base64
import os
from typing import Optional

from app.services.engines.base import ChatResult, Engine, EngineUnavailableError


class ClaudeEngine(Engine):
    name = "claude"
    supports_vision = True
    # 1M token 上下文，按中文余量设为 200 万字符，实际几乎不会触发 map-reduce。
    max_input_chars = 2_000_000
    default_max_tokens = 8000

    def __init__(self, model: Optional[str] = None):
        self.model = model or os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6").strip()
        self.api_key = self._env("ANTHROPIC_API_KEY")

    def available(self) -> bool:
        return bool(self.api_key)

    def _user_content(self, text: str, images: Optional[list[str]]):
        if images and self.supports_vision:
            content = []
            for p in images:
                with open(p, "rb") as f:
                    b64 = base64.standard_b64encode(f.read()).decode("utf-8")
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": self._image_media_type(p),
                        "data": b64,
                    },
                })
            content.append({"type": "text", "text": text})
            return content
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
            raise EngineUnavailableError(f"{self.name}: 缺少 ANTHROPIC_API_KEY")
        import anthropic

        client = anthropic.Anthropic(api_key=self.api_key)
        user_content = self._user_content(text, images)

        with client.messages.stream(
            model=self.model,
            max_tokens=max_tokens or self.default_max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_content}],
        ) as stream:
            msg = stream.get_final_message()

        raw = next((b.text for b in msg.content if getattr(b, "type", None) == "text"), "")
        usage = getattr(msg, "usage", None)
        return ChatResult(
            text=(raw or "").strip(),
            input_tokens=getattr(usage, "input_tokens", -1) if usage else -1,
            output_tokens=getattr(usage, "output_tokens", -1) if usage else -1,
        )
