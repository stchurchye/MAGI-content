"""结构化摘要：可插拔引擎 + 长文本 map-reduce + 多模态视觉。

- 引擎由 config.summary_engine 选择（SUMMARY_ENGINE），见 app/services/engines。
- 长文本：内容超过引擎 max_input_chars 时自动走 map-reduce（分段摘要→归并），
  不再像旧版那样在 30 万字符处硬截断丢尾。
- 多模态：图文且引擎 supports_vision 时直接读图（OCR 文本作参考），否则用 OCR 文本。
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Callable, Optional

from app.services.engines import Engine, EngineUnavailableError, get_engine
from app.services.summary_format import (
    IMAGE_JSON_PROMPT,
    MAP_PROMPT,
    REDUCE_PREFIX,
    VIDEO_JSON_PROMPT,
    build_display_text,
    build_markdown_file,
    parse_summary_json,
)
from app.ui_copy import ProgressMsg


# 主流视觉接口（Claude / OpenAI 兼容）都支持的图片格式；heic/avif/tiff/bmp 不在内，
# 传入会被谎报为 image/png 导致接口拒收，故视觉路径只保留这些。
_VISION_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


@dataclass
class SummarizeResult:
    summary_path: str
    summary_display: str


def _chunk(text: str, size: int) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)]


def _map_reduce(
    engine: Engine,
    reduce_system: str,
    text: str,
    logger: logging.Logger,
    progress_cb: Callable[[int, str], None],
    chunk_chars: int,
) -> str:
    """长文本 map-reduce：逐段摘要（不截断）→ 归并为结构化 JSON。返回 reduce 阶段原始文本。"""
    chunks = _chunk(text, chunk_chars)
    logger.info("map-reduce | chunks=%d chunk_chars=%d total_chars=%d",
                len(chunks), chunk_chars, len(text))
    partials: list[str] = []
    for i, ch in enumerate(chunks):
        progress_cb(20 + int(i / (len(chunks) + 1) * 60), f"分段摘要 {i + 1}/{len(chunks)}")
        r = engine.complete(
            MAP_PROMPT.format(idx=i + 1, total=len(chunks)),
            ch, None, max_tokens=1024, json_mode=False,
        )
        partials.append((r.text or "").strip())

    progress_cb(85, "归并各段摘要")
    merged = REDUCE_PREFIX + "\n\n".join(
        f"【第{i + 1}段】\n{p}" for i, p in enumerate(partials)
    )
    r = engine.complete(reduce_system, merged, None, max_tokens=4096, json_mode=True)
    return r.text


def summarize(
    transcript_text: str,
    title: str,
    output_dir: str,
    job_id: str,
    logger: logging.Logger,
    progress_cb: Callable[[int, str], None],
    media_type: str = "video",
    image_count: int = 0,
    images: Optional[list[str]] = None,
    engine: Optional[Engine] = None,
    chunk_chars: Optional[int] = None,
) -> SummarizeResult:
    """用可插拔引擎生成结构化摘要，返回展示用纯文本与 .md 文件路径。"""
    progress_cb(0, ProgressMsg.SUMMARIZE_START)

    if engine is None:
        from app.config import get_config
        cfg = get_config()
        engine = get_engine(cfg.summary_engine)
        if chunk_chars is None:
            chunk_chars = cfg.summary_chunk_chars
    chunk_chars = chunk_chars or 80_000
    # 分块不得超过引擎单次容量；再留 2000 字符余量给 MAP 提示词与格式，避免块+提示词略超窗
    chunk_chars = max(1000, min(chunk_chars, engine.max_input_chars - 2000))

    if not engine.available():
        raise RuntimeError(
            f"摘要引擎 {engine.name} 不可用：缺少 API key。"
            f"请在 .env 配置对应 key，或用 SUMMARY_ENGINE 切换引擎。"
        )

    # 视觉路径只保留接口支持的图片格式（过滤 heic/avif/tiff/bmp，否则会被谎报 png 致拒收）；
    # 过滤后无可用图片则退回 OCR 文本路径。
    vision_images: list[str] = []
    if media_type == "image_text" and engine.supports_vision and images:
        vision_images = [p for p in images
                         if os.path.splitext(p)[1].lower() in _VISION_EXTS]
    use_vision = bool(vision_images)

    if media_type == "image_text":
        system = IMAGE_JSON_PROMPT
        if use_vision:
            user = "请阅读上面图片中的全部内容（含视觉信息），整理为结构化 JSON。"
            if transcript_text:
                user += "\n\n（参考 OCR 文本）：\n" + transcript_text
        else:
            user = (
                f"标题：{title}\n\n"
                f"以下文字由 {image_count} 张图片 OCR 提取，顺序可能错乱。"
                f"请去重、排序并整理：\n\n{transcript_text}"
            )
    else:
        system = VIDEO_JSON_PROMPT
        user = f"标题：{title}\n\n文稿：\n{transcript_text}"

    logger.info(
        "summarize | engine=%s media=%s vision=%s text_len=%d max_input=%d",
        engine.name, media_type, use_vision, len(user), engine.max_input_chars,
    )
    progress_cb(20, ProgressMsg.SUMMARIZE_ANALYZING)

    try:
        if not use_vision and len(user) > engine.max_input_chars:
            logger.info("长文本超过引擎容量 (%d > %d)，走 map-reduce 不截断",
                        len(user), engine.max_input_chars)
            raw = _map_reduce(engine, system, user, logger, progress_cb, chunk_chars)
        else:
            r = engine.complete(
                system, user,
                vision_images if use_vision else None,
                max_tokens=4096, json_mode=True,
            )
            raw = r.text
    except EngineUnavailableError as exc:
        raise RuntimeError(str(exc)) from exc

    raw = (raw or "").strip()
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

    logger.info("summarize done | engine=%s display_len=%d path=%s",
                engine.name, len(summary_display), summary_path)
    progress_cb(100, f"{ProgressMsg.SUMMARIZE_DONE} ({len(summary_display)} 字)")

    return SummarizeResult(summary_path=summary_path, summary_display=summary_display)
