"""Job 数据模型与状态机。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

from app.services.summary_format import summary_for_display
from app.ui_copy import (
    downloader_label,
    format_duration,
    stage_label,
    stage_label_from_display,
    status_label,
)


class JobStatus(str, Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    EXTRACTING = "extracting"
    TRANSCRIBING = "transcribing"
    SUMMARIZING = "summarizing"
    COMPLETED = "completed"
    FAILED = "failed"

    @classmethod
    def active_set(cls) -> set[str]:
        """仍在处理中的状态集合。"""
        return {"pending", "downloading", "extracting", "transcribing", "summarizing"}


class Stage(str, Enum):
    """流水线阶段标识。"""
    PLATFORM_DETECT = "detecting"
    DOWNLOAD = "downloading"
    EXTRACT = "extracting"
    TRANSCRIBE = "transcribing"
    OCR = "ocr"
    SUMMARIZE = "summarizing"

    @property
    def label_cn(self) -> str:
        return stage_label(self.value)

    @property
    def status(self) -> JobStatus:
        _map = {
            "detecting": JobStatus.DOWNLOADING,
            "downloading": JobStatus.DOWNLOADING,
            "extracting": JobStatus.EXTRACTING,
            "transcribing": JobStatus.TRANSCRIBING,
            "ocr": JobStatus.TRANSCRIBING,
            "summarizing": JobStatus.SUMMARIZING,
        }
        return _map.get(self.value, JobStatus.PENDING)


@dataclass
class Job:
    id: str
    url: str
    platform: Optional[str] = None
    downloader: Optional[str] = None      # ytdlp / yutto / gallerydl
    title: Optional[str] = None
    media_type: str = "video"             # video / image_text
    duration_sec: Optional[float] = None
    status: str = JobStatus.PENDING.value
    current_stage: Optional[str] = None
    storage_dir: Optional[str] = None
    video_path: Optional[str] = None
    audio_path: Optional[str] = None
    images_dir: Optional[str] = None
    danmaku_path: Optional[str] = None
    transcript_path: Optional[str] = None
    summary_path: Optional[str] = None
    transcript_text: Optional[str] = None
    summary_text: Optional[str] = None
    error_message: Optional[str] = None
    error_stage: Optional[str] = None
    retry_count: int = 0
    progress_pct: int = 0
    is_archived: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @classmethod
    def from_row(cls, row: dict) -> "Job":
        return cls(**{k: row[k] for k in row.keys() if k in cls.__dataclass_fields__})

    def to_dict(self) -> dict:
        d: dict[str, Any] = {}
        for k in self.__dataclass_fields__:
            v = getattr(self, k)
            if isinstance(v, Enum):
                v = v.value
            d[k] = v
        d["status_label"] = self.status_label
        d["stage_label"] = self.stage_label
        d["downloader_label"] = self.downloader_label
        d["platform_label"] = self.platform_label
        return d

    @property
    def status_label(self) -> str:
        return status_label(self.status)

    @property
    def stage_label(self) -> str:
        return stage_label_from_display(self.current_stage)

    @property
    def downloader_label(self) -> str:
        return downloader_label(self.downloader)

    @property
    def duration_display(self) -> str:
        return format_duration(self.duration_sec)

    @property
    def platform_label(self) -> str:
        from app.services.platform_detector import PLATFORM_RULES
        key = self.platform or "generic"
        rule = PLATFORM_RULES.get(key) or PLATFORM_RULES["generic"]
        return rule.name

    @property
    def is_active(self) -> bool:
        return self.status in JobStatus.active_set()

    @property
    def is_completed(self) -> bool:
        return self.status == JobStatus.COMPLETED.value

    @property
    def is_failed(self) -> bool:
        return self.status == JobStatus.FAILED.value

    @property
    def summary_display(self) -> str:
        return summary_for_display(self.summary_text)

    @property
    def display_title(self) -> str:
        t = (self.title or "").strip()
        if t and t.lower() != "unknown":
            return t
        if self.url:
            return self.url if len(self.url) <= 88 else self.url[:85] + "…"
        return "未命名任务"

    def is_stuck(self, stale_minutes: int = 20) -> bool:
        if not self.is_active or not self.updated_at:
            return False
        try:
            ts = datetime.strptime(self.updated_at, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            return False
        return datetime.now(timezone.utc) - ts > timedelta(minutes=stale_minutes)
