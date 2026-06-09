"""本地 faster-whisper 转录，输出结构与 transcribe_tingwu 对齐。"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Callable, Optional


@lru_cache(maxsize=2)
def _load_model(model: str, device: str, compute_type: str):
    """加载并缓存 faster-whisper 模型，避免每个任务都重新加载。"""
    from faster_whisper import WhisperModel
    return WhisperModel(model, device=device, compute_type=compute_type)


def transcribe_whisper(
    audio_path: str,
    output_dir: str,
    job_id: str,
    logger: logging.Logger,
    progress_cb: Callable[[int, str], None],
    model: str = "small",
    device: str = "cpu",
    compute_type: str = "int8",
    language: Optional[str] = None,
    model_factory: Optional[Callable] = None,
) -> dict:
    progress_cb(2, "加载 Whisper 模型…")
    wm = (model_factory or _load_model)(model, device, compute_type)

    progress_cb(8, "开始本地转录…")
    segments_iter, info = wm.transcribe(audio_path, language=language, vad_filter=True)
    total = getattr(info, "duration", 0) or 0

    full_parts = []
    segments = []
    for seg in segments_iter:
        text = seg.text.strip()
        if not text:
            continue
        full_parts.append(text)
        segments.append({
            "begin_time": int(seg.start * 1000),
            "end_time": int(seg.end * 1000),
            "text": text,
        })
        if total:
            pct = min(8 + int(seg.end / total * 85), 95)
            progress_cb(pct, f"转录中… ({int(seg.end)}/{int(total)}s)")
    full_text = "\n".join(full_parts)

    transcript_path = os.path.join(output_dir, f"{job_id}.txt")
    with open(transcript_path, "w", encoding="utf-8") as f:
        f.write(full_text)

    detailed_path = os.path.join(output_dir, f"{job_id}_detailed.txt")
    with open(detailed_path, "w", encoding="utf-8") as f:
        for s in segments:
            begin = s["begin_time"] / 1000
            end = s["end_time"] / 1000
            f.write(f"[{begin:07.1f} - {end:07.1f}] {s['text']}\n")

    duration = segments[-1]["end_time"] / 1000 if segments else 0
    logger.info("Whisper done | text_len=%d segments=%d", len(full_text), len(segments))
    progress_cb(95, f"转录完成 ({len(full_text)} 字)")
    return {
        "transcript_path": transcript_path,
        "transcript_text": full_text,
        "language": info.language,
        "duration": duration,
        "segments": segments,
    }
