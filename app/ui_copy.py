"""全站中文 UI 文案（单一来源）。"""
from __future__ import annotations

from typing import Sequence

# ---- 任务状态 ----
STATUS_LABELS: dict[str, str] = {
    "pending": "等待中",
    "downloading": "下载中",
    "extracting": "提取音频",
    "transcribing": "转写中",
    "summarizing": "生成摘要",
    "completed": "已完成",
    "failed": "失败",
}

# ---- 流水线阶段（展示名）----
STAGE_LABELS: dict[str, str] = {
    "detecting": "平台检测",
    "downloading": "下载中",
    "extracting": "提取音频",
    "transcribing": "语音转文字",
    "ocr": "文字识别",
    "summarizing": "生成摘要",
}

# 时间线：(stage_id, label, progress_pct)；video / image_text 各一套
STAGE_TIMELINE_VIDEO: list[tuple[str, str, int]] = [
    ("detecting", "平台检测", 5),
    ("downloading", "下载中", 25),
    ("extracting", "提取音频", 30),
    ("transcribing", "语音转文字", 80),
    ("summarizing", "生成摘要", 100),
]

STAGE_TIMELINE_IMAGE: list[tuple[str, str, int]] = [
    ("detecting", "平台检测", 5),
    ("downloading", "下载中", 25),
    ("ocr", "文字识别", 80),
    ("summarizing", "生成摘要", 100),
]

# ---- 下载器选项（value 不变，仅展示）----
DOWNLOADER_OPTIONS: list[tuple[str, str]] = [
    ("auto", "自动选择"),
    ("ytdlp", "yt-dlp"),
    ("yutto", "B站（yutto · 弹幕/AI 字幕）"),
    ("xhs", "小红书（XHS-Downloader）"),
    ("gallerydl", "gallery-dl"),
]

DOWNLOADER_LABELS: dict[str, str] = dict(DOWNLOADER_OPTIONS)

# 历史记录平台筛选（value 对应 jobs.platform）
PLATFORM_FILTER_OPTIONS: list[tuple[str, str]] = [
    ("", "全部"),
    ("bilibili", "B站"),
    ("youtube", "YouTube"),
    ("xiaohongshu", "小红书"),
    ("douyin", "抖音"),
    ("nicovideo", "N站"),
    ("generic", "通用"),
]

# ---- 流水线进度文案 ----
class ProgressMsg:
    PLATFORM_DETECT = "正在识别平台…"
    DOWNLOAD_START = "正在下载…"
    DOWNLOAD_DONE = "下载完成"
    EXTRACT_START = "正在提取音频…"
    EXTRACT_DONE = "音频提取完成"
    TRANSCRIBE_START = "正在转写…"
    TRANSCRIBE_DONE = "转写完成"
    OCR_START = "正在识别图片文字…"
    OCR_DONE = "文字识别完成"
    SUMMARIZE_START = "正在生成摘要…"
    SUMMARIZE_ANALYZING = "正在分析内容…"
    SUMMARIZE_DONE = "摘要完成"
    SUMMARIZE_SKIP_EMPTY = "转录无结果，已跳过摘要"
    OCR_SKIP_EMPTY = "未识别到文字，已跳过摘要"
    SAVE_DONE = "内容已保存"

    UPLOAD_OSS = "正在上传音频…"
    OSS_DONE = "音频上传完成"
    TRANSCRIBE_TASK = "正在创建转写任务…"
    TRANSCRIBE_POLL = "转写进行中…"
    TRANSCRIBE_FETCH = "正在获取转写结果…"

    OCR_FOUND = "发现图片"
    OCR_IMAGE = "正在识别图片"

    XHS_FETCH = "正在获取作品信息…"
    XHS_DONE = "小红书下载完成"

    FFMPEG_START = "正在提取音频…"
    FFMPEG_DONE = "音频提取完成"

    YUTTO_START = "正在启动 yutto…"
    YUTTO_DONE = "yutto 处理完成"

    GALLERY_START = "gallery-dl 下载中…"
    GALLERY_DONE = "gallery-dl 完成"

# ---- 对话框 / 按钮 ----
CONFIRM_DELETE = "确定删除该任务及全部文件？"
CONFIRM_CANCEL = "确定取消该任务？"
BTN_SUBMIT = "开始处理"
BTN_SUBMITTING = "提交中…"
BTN_CANCEL = "取消"
BTN_DELETE = "删除"
BTN_RETRY = "重试"
BTN_ARCHIVE = "归档"
BTN_UNARCHIVE = "取消归档"
BTN_VIEW = "查看"
BTN_SEARCH = "搜索"
BTN_CLEAR = "清除"
ERROR_UNKNOWN = "未知错误"
ERROR_RETRY_FAILED = "重试失败"
ERROR_RETRY_REQUEST = "重试请求失败"
TOAST_CANCELLED = "任务已取消"


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status)


def stage_label(stage_id: str | None) -> str:
    if not stage_id:
        return "等待中"
    if stage_id in STAGE_LABELS:
        return STAGE_LABELS[stage_id]
    return stage_id


def stage_label_from_display(display: str | None) -> str:
    """current_stage 字段可能已是中文展示名。"""
    if not display:
        return "等待中"
    return display


def downloader_label(downloader: str | None) -> str:
    if not downloader:
        return DOWNLOADER_LABELS.get("auto", "自动选择")
    return DOWNLOADER_LABELS.get(downloader, downloader)


def timeline_for_media_type(media_type: str) -> Sequence[tuple[str, str, int]]:
    if media_type == "image_text":
        return STAGE_TIMELINE_IMAGE
    return STAGE_TIMELINE_VIDEO


def format_duration(sec: float | None) -> str:
    if not sec:
        return ""
    minutes = int(sec // 60)
    seconds = int(sec % 60)
    return f"{minutes} 分 {seconds} 秒"
