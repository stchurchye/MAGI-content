"""DeepSeek API 结构化摘要。"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Callable

from openai import OpenAI

from app.services.summary_format import (
    IMAGE_JSON_PROMPT,
    VIDEO_JSON_PROMPT,
    build_display_text,
    build_markdown_file,
    parse_summary_json,
)
from app.ui_copy import ProgressMsg


@dataclass
class SummarizeResult:
    summary_path: str
    summary_display: str


def summarize(
    transcript_text: str,
    title: str,
    output_dir: str,
    job_id: str,
    api_key: str,
    model: str,
    max_tokens: int,
    logger: logging.Logger,
    progress_cb: Callable[[int, str], None],
    media_type: str = "video",
    image_count: int = 0,
) -> SummarizeResult:
    """
    使用 DeepSeek API 摘要文本，返回展示用纯文本与 .md 文件路径。
    """
    progress_cb(0, ProgressMsg.SUMMARIZE_START)
    logger.info(
        "DeepSeek summarizing | title=%s text_len=%d model=%s media_type=%s",
        title,
        len(transcript_text),
        model,
        media_type,
    )

    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")

    if media_type == "image_text":
        system_prompt = IMAGE_JSON_PROMPT
        user_content = (
            f"标题：{title}\n\n"
            f"以下文字由 {image_count} 张图片 OCR 提取，顺序可能错乱。"
            f"请去重、排序并整理：\n\n{transcript_text}"
        )
    else:
        system_prompt = VIDEO_JSON_PROMPT
        user_content = f"标题：{title}\n\n文稿：\n{transcript_text}"

    MAX_CHARS = 300_000
    if len(user_content) > MAX_CHARS:
        user_content = user_content[:MAX_CHARS] + "\n\n[文稿因过长已截断]"
        logger.warning(
            "Transcript truncated from %d to %d chars",
            len(transcript_text),
            MAX_CHARS,
        )

    progress_cb(30, ProgressMsg.SUMMARIZE_ANALYZING)

    response = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        response_format={"type": "json_object"},
    )

    raw = (response.choices[0].message.content or "").strip()
    try:
        data = parse_summary_json(raw)
    except json.JSONDecodeError as exc:
        logger.error("Invalid summary JSON: %s | raw=%s", exc, raw[:500])
        raise RuntimeError("摘要模型返回格式无效，请重试") from exc

    summary_display = build_display_text(data, media_type=media_type)
    if not summary_display:
        raise RuntimeError("摘要模型未返回有效内容")

    md_content = build_markdown_file(data, title=title, media_type=media_type)
    summary_path = os.path.join(output_dir, f"{job_id}.md")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    logger.info(
        "DeepSeek done | display_len=%d path=%s",
        len(summary_display),
        summary_path,
    )
    progress_cb(100, f"{ProgressMsg.SUMMARIZE_DONE} ({len(summary_display)} 字)")

    return SummarizeResult(summary_path=summary_path, summary_display=summary_display)
