"""摘要/多模态引擎对比基准台 —— 统一引擎适配器。

独立于 app.config，3.9 兼容（不使用 `X | None` 等 3.10 语法）。
每个引擎缺对应 API key 时返回 {error, skipped: True}，不崩。

统一接口::

    engine.summarize(text, images, media_type) -> dict

返回值约定（dict）::

    {
        # 内容字段（视引擎/媒体类型而定）
        "headline": str,
        "key_points": [str, ...],   # video
        "summary": str,             # video
        "quotes": [str, ...],       # video
        "body": str,                # image_text
        "key_facts": [str, ...],    # image_text
        # 元数据
        "_meta": {
            "engine": str,
            "latency_sec": float,
            "input_tokens": int,    # -1 表示未知
            "output_tokens": int,
            "truncated": bool,
            "skipped": bool,
            "error": str | None,
        },
    }
"""
from __future__ import annotations

import base64
import json
import os
import time

# ---------------------------------------------------------------------------
# 提示词（复用 app/services/summary_format.py 的风格，此处内联以保持独立）
# ---------------------------------------------------------------------------

VIDEO_JSON_PROMPT = """你是一位专业的内容摘要助手。根据提供的视频或内容文稿，输出结构化中文摘要。

要求：
1. 使用简体中文
2. 只输出一个 JSON 对象，不要 Markdown、不要代码块标记、不要客套开场白
3. JSON 字段（均为字符串或字符串数组）：
   - headline: 一句话概括
   - key_points: 3-5 条核心观点（数组）
   - summary: 一段完整详细摘要
   - quotes: 2-3 条值得注意的原文引用（数组，无则 []）
4. 简洁完整，保留重要专有名词
"""

IMAGE_JSON_PROMPT = """你是一位专业的内容整理助手。你将收到从小红书等图文帖子提取的图片内容，需忠实整理。

要求：
1. 使用简体中文
2. 只输出一个 JSON 对象，不要 Markdown、不要代码块标记、不要客套开场白
3. 图片顺序可能错乱——根据序号、步骤、时间线等线索排成正确顺序
4. 去除重复内容
5. 忠实原文，不添加、不发挥
6. JSON 字段：
   - headline: 帖子主题
   - body: 按正确顺序整理的正文（字符串）
   - key_facts: 事实、数字、地点、价格、日期等（数组，无则 []）
"""

# map-reduce 分块阶段提示词
MAP_PROMPT = """你是一位专业的内容摘要助手。下面是一段长文稿的【第 {idx}/{total} 段】。
请用简体中文输出这一段的要点摘要（150-300 字），保留这一段中的关键事实、数字、专有名词和任何特殊标记句。
只输出摘要正文，不要客套、不要 JSON、不要 Markdown 标记。"""

REDUCE_PROMPT = """你是一位专业的内容摘要助手。下面是同一篇长文稿按顺序切分后、逐段生成的多段摘要。
请将它们归并为一份针对【全文】的结构化中文摘要。

要求：
1. 使用简体中文
2. 只输出一个 JSON 对象，不要 Markdown、不要代码块标记、不要客套开场白
3. JSON 字段：
   - headline: 一句话概括
   - key_points: 3-5 条核心观点（数组）
   - summary: 一段完整详细摘要
   - quotes: 2-3 条值得注意的原文引用（数组，无则 []）
4. 必须覆盖全文（含首段与尾段），保留所有特殊标记句与关键专有名词
"""

MAX_CHARS = 300_000  # 复现 summarizer.py 的硬截断阈值


def _new_meta(engine):
    return {
        "engine": engine,
        "latency_sec": 0.0,
        "input_tokens": -1,
        "output_tokens": -1,
        "truncated": False,
        "skipped": False,
        "error": None,
    }


def _parse_json(raw):
    """容错解析 JSON（去除可能的代码块围栏）。"""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)
        text = text[1] if len(text) > 1 else (raw or "")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip().rstrip("`").strip()
    return json.loads(text)


# ---------------------------------------------------------------------------
# A. DeepSeek 单次调用（复现现状：300k 硬截断）
# ---------------------------------------------------------------------------

class DeepSeekEngine:
    name = "deepseek-single"
    # DeepSeek 定价（deepseek-chat，cache-miss）：约 $0.27/1M 输入、$1.10/1M 输出（美元）。
    PRICE_IN = 0.27 / 1_000_000
    PRICE_OUT = 1.10 / 1_000_000

    def __init__(self, model="deepseek-chat"):
        self.model = model
        self.api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()

    def available(self):
        return bool(self.api_key)

    def summarize(self, text, images=None, media_type="video"):
        meta = _new_meta(self.name)
        if not self.available():
            meta["skipped"] = True
            meta["error"] = "missing key: DEEPSEEK_API_KEY"
            return {"_meta": meta}

        from openai import OpenAI

        client = OpenAI(api_key=self.api_key, base_url="https://api.deepseek.com")

        if media_type == "image_text":
            system = IMAGE_JSON_PROMPT
            user = "以下文字由图片 OCR 提取，顺序可能错乱。请去重、排序并整理：\n\n" + text
        else:
            system = VIDEO_JSON_PROMPT
            user = "文稿：\n" + text

        if len(user) > MAX_CHARS:
            user = user[:MAX_CHARS] + "\n\n[文稿因过长已截断]"
            meta["truncated"] = True

        t0 = time.time()
        try:
            resp = client.chat.completions.create(
                model=self.model,
                max_tokens=4096,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
            )
        except Exception as exc:  # noqa: BLE001
            meta["latency_sec"] = round(time.time() - t0, 2)
            meta["error"] = "{}: {}".format(type(exc).__name__, exc)
            return {"_meta": meta}
        meta["latency_sec"] = round(time.time() - t0, 2)

        usage = getattr(resp, "usage", None)
        if usage:
            meta["input_tokens"] = usage.prompt_tokens
            meta["output_tokens"] = usage.completion_tokens

        raw = (resp.choices[0].message.content or "").strip()
        try:
            data = _parse_json(raw)
        except Exception as exc:  # noqa: BLE001
            meta["error"] = "json parse: {}".format(exc)
            data = {"summary": raw[:500]}
        data["_meta"] = meta
        return data


# ---------------------------------------------------------------------------
# B. DeepSeek map-reduce 分块（解决截断）
# ---------------------------------------------------------------------------

class DeepSeekMapReduceEngine:
    name = "deepseek-mapreduce"
    PRICE_IN = 0.27 / 1_000_000
    PRICE_OUT = 1.10 / 1_000_000

    def __init__(self, model="deepseek-chat", chunk_chars=80_000):
        self.model = model
        self.chunk_chars = chunk_chars
        self.api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()

    def available(self):
        return bool(self.api_key)

    def _chunks(self, text):
        return [text[i:i + self.chunk_chars]
                for i in range(0, len(text), self.chunk_chars)]

    def summarize(self, text, images=None, media_type="video"):
        meta = _new_meta(self.name)
        if not self.available():
            meta["skipped"] = True
            meta["error"] = "missing key: DEEPSEEK_API_KEY"
            return {"_meta": meta}

        from openai import OpenAI

        client = OpenAI(api_key=self.api_key, base_url="https://api.deepseek.com")

        if media_type == "image_text":
            # 图文非长文，退化为单次（map-reduce 主要针对长视频转录）
            return DeepSeekEngine(self.model).summarize(text, images, media_type)

        chunks = self._chunks(text)
        meta["chunks"] = len(chunks)
        in_tok = 0
        out_tok = 0
        partials = []
        t0 = time.time()
        try:
            # MAP：逐段摘要（不截断，每段独立调用）
            for i, ch in enumerate(chunks):
                resp = client.chat.completions.create(
                    model=self.model,
                    max_tokens=1024,
                    messages=[
                        {"role": "system",
                         "content": MAP_PROMPT.format(idx=i + 1, total=len(chunks))},
                        {"role": "user", "content": ch},
                    ],
                )
                if resp.usage:
                    in_tok += resp.usage.prompt_tokens
                    out_tok += resp.usage.completion_tokens
                partials.append((resp.choices[0].message.content or "").strip())

            # REDUCE：归并为结构化 JSON
            merged = "\n\n".join(
                "【第{}段摘要】\n{}".format(i + 1, p) for i, p in enumerate(partials)
            )
            resp = client.chat.completions.create(
                model=self.model,
                max_tokens=4096,
                messages=[
                    {"role": "system", "content": REDUCE_PROMPT},
                    {"role": "user", "content": merged},
                ],
                response_format={"type": "json_object"},
            )
        except Exception as exc:  # noqa: BLE001
            meta["latency_sec"] = round(time.time() - t0, 2)
            meta["input_tokens"] = in_tok
            meta["output_tokens"] = out_tok
            meta["error"] = "{}: {}".format(type(exc).__name__, exc)
            return {"_meta": meta}

        meta["latency_sec"] = round(time.time() - t0, 2)
        if resp.usage:
            in_tok += resp.usage.prompt_tokens
            out_tok += resp.usage.completion_tokens
        meta["input_tokens"] = in_tok
        meta["output_tokens"] = out_tok
        meta["truncated"] = False  # 关键：全量处理，不丢尾

        raw = (resp.choices[0].message.content or "").strip()
        try:
            data = _parse_json(raw)
        except Exception as exc:  # noqa: BLE001
            meta["error"] = "json parse: {}".format(exc)
            data = {"summary": raw[:500]}
        data["_meta"] = meta
        return data


# ---------------------------------------------------------------------------
# C. Claude Sonnet 4.6（1M 上下文 + 原生视觉，全量不截断）
# ---------------------------------------------------------------------------

class ClaudeEngine:
    name = "claude-sonnet-4-6"
    # Claude Sonnet 4.6 定价：$3/1M 输入、$15/1M 输出
    PRICE_IN = 3.0 / 1_000_000
    PRICE_OUT = 15.0 / 1_000_000

    def __init__(self, model="claude-sonnet-4-6", max_tokens=8000):
        self.model = model
        self.max_tokens = max_tokens
        self.api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()

    def available(self):
        return bool(self.api_key)

    @staticmethod
    def _media_type_for(path):
        ext = os.path.splitext(path)[1].lower()
        return {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }.get(ext, "image/png")

    def summarize(self, text, images=None, media_type="video"):
        meta = _new_meta(self.name)
        if not self.available():
            meta["skipped"] = True
            meta["error"] = "missing key: ANTHROPIC_API_KEY"
            return {"_meta": meta}

        import anthropic

        client = anthropic.Anthropic(api_key=self.api_key)

        if media_type == "image_text":
            system = IMAGE_JSON_PROMPT
            content = []
            for img in (images or []):
                with open(img, "rb") as f:
                    b64 = base64.standard_b64encode(f.read()).decode("utf-8")
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": self._media_type_for(img),
                        "data": b64,
                    },
                })
            # 同时把 OCR 文本（若有）一并给，但主路径是原生视觉
            instr = "请阅读上面图片中的全部内容（含视觉信息），整理为结构化 JSON。"
            if text:
                instr += "\n\n（参考 OCR 文本）：\n" + text
            content.append({"type": "text", "text": instr})
            user_content = content
        else:
            system = VIDEO_JSON_PROMPT
            # 1M 上下文：长文稿全量塞入，不截断
            user_content = "文稿：\n" + text

        t0 = time.time()
        try:
            # 大输出/长输入用流式避免超时
            with client.messages.stream(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": user_content}],
            ) as stream:
                msg = stream.get_final_message()
        except Exception as exc:  # noqa: BLE001
            meta["latency_sec"] = round(time.time() - t0, 2)
            meta["error"] = "{}: {}".format(type(exc).__name__, exc)
            return {"_meta": meta}
        meta["latency_sec"] = round(time.time() - t0, 2)

        if msg.usage:
            meta["input_tokens"] = msg.usage.input_tokens
            meta["output_tokens"] = msg.usage.output_tokens

        raw = next((b.text for b in msg.content if b.type == "text"), "")
        try:
            data = _parse_json(raw)
        except Exception as exc:  # noqa: BLE001
            meta["error"] = "json parse: {}".format(exc)
            data = {"summary": raw[:500]}
        data["_meta"] = meta
        return data


# ---------------------------------------------------------------------------
# D. 通义千问 Qwen-VL（dashscope openai 兼容端点）
# ---------------------------------------------------------------------------

class QwenVLEngine:
    name = "qwen-vl-max"
    # Qwen-VL-Max 定价随时间变动，此处用占位估算：约 $1.5/1M 输入、$4.5/1M 输出
    PRICE_IN = 1.5 / 1_000_000
    PRICE_OUT = 4.5 / 1_000_000
    BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def __init__(self, model="qwen-vl-max"):
        self.model = model
        # 兼容多种常见 env 名
        self.api_key = (
            os.environ.get("DASHSCOPE_API_KEY")
            or os.environ.get("QWEN_API_KEY")
            or ""
        ).strip()

    def available(self):
        return bool(self.api_key)

    @staticmethod
    def _data_uri(path):
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        mt = "jpeg" if ext in ("jpg", "jpeg") else (ext or "png")
        with open(path, "rb") as f:
            b64 = base64.standard_b64encode(f.read()).decode("utf-8")
        return "data:image/{};base64,{}".format(mt, b64)

    def summarize(self, text, images=None, media_type="video"):
        meta = _new_meta(self.name)
        if not self.available():
            meta["skipped"] = True
            meta["error"] = "missing key: DASHSCOPE_API_KEY"
            return {"_meta": meta}

        from openai import OpenAI

        client = OpenAI(api_key=self.api_key, base_url=self.BASE_URL)

        if media_type == "image_text":
            system = IMAGE_JSON_PROMPT
            parts = []
            for img in (images or []):
                parts.append({"type": "image_url",
                              "image_url": {"url": self._data_uri(img)}})
            instr = "请阅读图片中的全部内容（含视觉信息），整理为结构化 JSON。"
            if text:
                instr += "\n\n（参考 OCR 文本）：\n" + text
            parts.append({"type": "text", "text": instr})
            user_content = parts
        else:
            system = VIDEO_JSON_PROMPT
            user_content = "文稿：\n" + text

        t0 = time.time()
        try:
            resp = client.chat.completions.create(
                model=self.model,
                max_tokens=4096,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_content},
                ],
            )
        except Exception as exc:  # noqa: BLE001
            meta["latency_sec"] = round(time.time() - t0, 2)
            meta["error"] = "{}: {}".format(type(exc).__name__, exc)
            return {"_meta": meta}
        meta["latency_sec"] = round(time.time() - t0, 2)

        usage = getattr(resp, "usage", None)
        if usage:
            meta["input_tokens"] = usage.prompt_tokens
            meta["output_tokens"] = usage.completion_tokens

        raw = (resp.choices[0].message.content or "").strip()
        try:
            data = _parse_json(raw)
        except Exception as exc:  # noqa: BLE001
            meta["error"] = "json parse: {}".format(exc)
            data = {"summary": raw[:500]}
        data["_meta"] = meta
        return data


ALL_ENGINES = [
    DeepSeekEngine,
    DeepSeekMapReduceEngine,
    ClaudeEngine,
    QwenVLEngine,
]
