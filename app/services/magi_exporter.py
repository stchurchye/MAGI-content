"""MAGI 标准格式导出器 — 将完成的 job 输出为 magi_export.zip。

格式版本 1.1：zip 包含 manifest.json + 媒体文件（音频/图片）。
MAGI 侧通过文件导入接入，两系统完全独立。
"""
from __future__ import annotations

import json
import os
import zipfile
from datetime import datetime, timedelta
from typing import Optional


FORMAT_VERSION = "1.1"

_PLATFORM_DECAY_DAYS = {
    "youtube": 365,
    "bilibili": 365,
    "xiaohongshu": 180,
    "douyin": 180,
    "niconico": 365,
}

_AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus"}
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


def _infer_content_type(media_type: str) -> str:
    if media_type == "image_text":
        return "image_text"
    if media_type == "video":
        return "video_transcript"
    return "article"


def _collect_media_files(job: dict, job_storage: str) -> list[dict]:
    """收集 job 关联的媒体文件（音频 + 图片），不含原始视频。"""
    files: list[dict] = []

    audio_path = job.get("audio_path") or ""
    if audio_path and os.path.isfile(audio_path):
        files.append({
            "type": "audio",
            "path": audio_path,
            "filename": os.path.basename(audio_path),
        })

    images_dir = job.get("images_dir") or ""
    if images_dir and os.path.isdir(images_dir):
        for fname in sorted(os.listdir(images_dir)):
            ext = os.path.splitext(fname)[1].lower()
            if ext in _IMAGE_EXTENSIONS:
                files.append({
                    "type": "image",
                    "path": os.path.join(images_dir, fname),
                    "filename": f"images/{fname}",
                })

    return files


def export_for_magi(
    job: dict,
    output_dir: str,
    pipeline_version: str = "0.2.0",
) -> Optional[str]:
    """将完成的 job 导出为 MAGI 标准格式 zip 包。

    zip 结构：
        manifest.json          — 元数据 + 文本内容
        audio.m4a (或其他)     — 音频文件（如有）
        images/1.jpg ...       — 图片文件（如有）

    Args:
        job: 数据库 job 行（dict）
        output_dir: 输出目录（通常是 storage/{job_id}/）
        pipeline_version: 当前 pipeline 版本号

    Returns:
        导出 zip 文件路径，如果无有效内容则返回 None
    """
    transcript_text = job.get("transcript_text") or ""
    if not transcript_text.strip():
        return None

    platform = job.get("platform") or "unknown"
    media_type = job.get("media_type") or "video"
    title = job.get("title") or "Untitled"
    created_at = job.get("created_at") or datetime.now().isoformat()

    publish_date = created_at[:10] if len(created_at) >= 10 else datetime.now().strftime("%Y-%m-%d")

    decay_days = _PLATFORM_DECAY_DAYS.get(platform, 365)
    try:
        valid_from = datetime.strptime(publish_date, "%Y-%m-%d")
    except ValueError:
        valid_from = datetime.now()
    valid_until = valid_from + timedelta(days=decay_days)

    media_files = _collect_media_files(job, output_dir)

    manifest = {
        "format_version": FORMAT_VERSION,
        "source": {
            "title": title,
            "url": job.get("url") or "",
            "platform": platform,
            "content_type": _infer_content_type(media_type),
            "creator": "",
            "publish_date": publish_date,
            "duration_sec": job.get("duration_sec"),
            "language": "zh",
        },
        "content": {
            "transcript_text": transcript_text,
            "summary_text": job.get("summary_text") or "",
        },
        "time_sensitivity": {
            "is_time_sensitive": True,
            "valid_from": valid_from.strftime("%Y-%m-%d"),
            "valid_until": valid_until.strftime("%Y-%m-%d"),
        },
        "media_files": [
            {"type": f["type"], "filename": f["filename"]}
            for f in media_files
        ],
        "metadata": {
            "processed_at": datetime.now().isoformat(),
            "pipeline_version": pipeline_version,
            "job_id": job.get("id") or "",
        },
    }

    zip_path = os.path.join(output_dir, "magi_export.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for f in media_files:
            zf.write(f["path"], f["filename"])

    return zip_path
