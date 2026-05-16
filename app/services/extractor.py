"""ffmpeg 音频提取。"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
from typing import Callable, Optional


def _find_ffmpeg() -> str:
    venv_bin = os.path.join(os.path.dirname(sys.executable), "ffmpeg")
    if os.path.exists(venv_bin):
        return venv_bin
    tool = shutil.which("ffmpeg")
    if tool:
        return tool
    raise RuntimeError("ffmpeg 未安装，请运行: brew install ffmpeg")


def extract_audio(
    video_path: str,
    output_dir: str,
    job_id: str,
    logger: logging.Logger,
    progress_cb: Callable[[int, str], None],
    cancel_event: Optional[threading.Event] = None,
) -> str:
    """
    从视频提取 16kHz mono WAV 音频。

    Returns:
        audio_path: 提取的音频文件路径
    """
    audio_path = os.path.join(output_dir, f"{job_id}.wav")

    logger.info("ffmpeg extracting audio | input=%s output=%s", video_path, audio_path)
    from app.ui_copy import ProgressMsg

    progress_cb(0, ProgressMsg.FFMPEG_START)

    ffmpeg_bin = _find_ffmpeg()

    cmd = [
        ffmpeg_bin,
        "-i", video_path,
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        "-y",
        "-progress", "pipe:1",
        "-nostats",
        audio_path,
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        if proc.stdout:
            for line in proc.stdout:
                if cancel_event and cancel_event.is_set():
                    proc.kill()
                    proc.wait()
                    raise RuntimeError("User cancelled")

        proc.wait(timeout=600)
    except BaseException:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        raise

    if proc.returncode != 0:
        stderr = proc.stderr.read()[-500:] if proc.stderr else ""
        raise RuntimeError(f"ffmpeg failed (code={proc.returncode}): {stderr}")

    if not os.path.exists(audio_path) or os.path.getsize(audio_path) == 0:
        raise RuntimeError("ffmpeg produced empty audio file")

    size_mb = os.path.getsize(audio_path) / (1024 * 1024)
    logger.info("ffmpeg done | path=%s size=%.1fMB", audio_path, size_mb)
    progress_cb(100, f"{ProgressMsg.FFMPEG_DONE} ({size_mb:.1f} MB)")

    return audio_path
