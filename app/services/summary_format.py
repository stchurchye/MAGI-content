"""摘要 JSON 解析、展示文本与 Markdown 文件生成。"""
from __future__ import annotations

import json
import re
from typing import Any

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

IMAGE_JSON_PROMPT = """你是一位专业的内容整理助手。你将收到从小红书等图文帖子 OCR 提取的多张图片文字，需忠实整理。

要求：
1. 使用简体中文
2. 只输出一个 JSON 对象，不要 Markdown、不要代码块标记、不要客套开场白
3. 图片顺序可能错乱——根据序号、步骤、时间线等线索排成正确顺序
4. 去除重复内容（含 OCR 轻微差异的重复句）
5. 忠实原文，不添加、不发挥
6. JSON 字段：
   - headline: 帖子主题
   - body: 按正确顺序整理的正文（字符串）
   - key_facts: 事实、数字、地点、价格、日期等（数组，无则 []）
7. 若图片顺序只能推测，在 body 末尾加一行：[注：图片顺序为推测]
"""

_LEGACY_SECTION_RE = re.compile(
    r"^#{1,3}\s*(?:\*\*)?(标题|核心观点|详细摘要|关键引用|正文|关键信息)(?:\*\*)?\s*$",
    re.MULTILINE,
)


def _strip_json_fence(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```\s*$", "", text)
    return text.strip()


def parse_summary_json(raw: str) -> dict[str, Any]:
    text = _strip_json_fence(raw)
    return json.loads(text)


def _lines(items: list[str] | None, bullet: str = "•") -> list[str]:
    if not items:
        return []
    out: list[str] = []
    for item in items:
        s = str(item).strip()
        if s:
            out.append(f"{bullet} {s}")
    return out


def build_display_text(data: dict[str, Any], media_type: str = "video") -> str:
    """生成用于列表/详情展示的纯文本（无 Markdown 标记）。"""
    parts: list[str] = []

    if media_type == "image_text":
        body = (data.get("body") or "").strip()
        if body:
            parts.append(body)
        facts = _lines(data.get("key_facts"))
        if facts:
            if parts:
                parts.append("")
            parts.extend(facts)
    else:
        points = _lines(data.get("key_points"))
        if points:
            parts.extend(points)
        summary = (data.get("summary") or "").strip()
        if summary:
            if parts:
                parts.append("")
            parts.append(summary)
        quotes = _lines(data.get("quotes"))
        if quotes:
            if parts:
                parts.append("")
            parts.extend(quotes)

    return "\n".join(parts).strip()


def build_markdown_file(data: dict[str, Any], title: str, media_type: str = "video") -> str:
    """生成可下载的 .md 文件内容。"""
    headline = (data.get("headline") or title or "").strip()
    lines = [f"# {title}", ""]

    if media_type == "image_text":
        if headline and headline != title:
            lines.extend([f"**主题**：{headline}", ""])
        body = (data.get("body") or "").strip()
        if body:
            lines.extend(["## 正文", "", body, ""])
        facts = data.get("key_facts") or []
        if facts:
            lines.extend(["## 关键信息", ""])
            lines.extend(f"- {f}" for f in facts if str(f).strip())
            lines.append("")
    else:
        if headline:
            lines.extend([f"**标题**：{headline}", ""])
        points = data.get("key_points") or []
        if points:
            lines.extend(["## 核心观点", ""])
            lines.extend(f"- {p}" for p in points if str(p).strip())
            lines.append("")
        summary = (data.get("summary") or "").strip()
        if summary:
            lines.extend(["## 详细摘要", "", summary, ""])
        quotes = data.get("quotes") or []
        if quotes:
            lines.extend(["## 关键引用", ""])
            lines.extend(f"> {q}" for q in quotes if str(q).strip())
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _is_legacy_markdown_summary(text: str) -> bool:
    t = text.strip()
    if not t:
        return False
    if t.startswith("# "):
        return True
    if "### " in t or "## " in t:
        return True
    if _LEGACY_SECTION_RE.search(t):
        return True
    return False


def _extract_legacy_sections(text: str) -> dict[str, str]:
    """从旧版 Markdown 摘要中提取各节正文。"""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    buf: list[str] = []

    def flush() -> None:
        nonlocal buf, current
        if current and buf:
            body = "\n".join(buf).strip()
            body = re.sub(r"^\*\*([^*]+)\*\*\s*$", r"\1", body, flags=re.MULTILINE)
            body = re.sub(r"^[-*]\s+", "", body, flags=re.MULTILINE)
            if body:
                sections.setdefault(current, []).append(body)
        buf = []

    for line in text.splitlines():
        m = _LEGACY_SECTION_RE.match(line.strip())
        if m:
            flush()
            current = m.group(1)
            continue
        if line.strip().startswith("# ") and not current:
            continue
        if current is not None:
            buf.append(line)
    flush()

    order = ("核心观点", "详细摘要", "正文", "关键信息", "关键引用", "标题")
    out: dict[str, str] = {}
    for key in order:
        if key in sections:
            out[key] = "\n\n".join(sections[key])
    return out


def summary_for_display(summary_text: str | None) -> str:
    """将 DB 中的 summary_text 转为界面展示用纯文本。"""
    if not summary_text or not summary_text.strip():
        return ""

    text = summary_text.strip()
    if not _is_legacy_markdown_summary(text):
        return text

    sections = _extract_legacy_sections(text)
    parts: list[str] = []

    for key in ("核心观点", "正文", "详细摘要", "关键信息", "关键引用"):
        body = sections.get(key, "").strip()
        if not body:
            continue
        if key in ("核心观点", "关键信息", "关键引用"):
            lines = []
            for line in body.splitlines():
                line = line.strip()
                if not line:
                    continue
                line = re.sub(r"^[-*•]\s*", "", line)
                line = re.sub(r"^\d+\.\s*", "", line)
                line = re.sub(r"^>\s*", "", line)
                if line:
                    lines.append(f"• {line}" if key != "关键引用" else f"「{line}」")
            if lines:
                if parts:
                    parts.append("")
                parts.extend(lines)
        else:
            if parts:
                parts.append("")
            parts.append(body)

    if parts:
        return "\n".join(parts).strip()

    stripped = re.sub(r"^#+\s+.*$", "", text, flags=re.MULTILINE)
    stripped = re.sub(r"^#{1,3}\s+", "", stripped, flags=re.MULTILINE)
    stripped = re.sub(r"\*\*([^*]+)\*\*", r"\1", stripped)
    return stripped.strip() or text
